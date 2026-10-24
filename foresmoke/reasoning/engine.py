"""Deterministic, single-consumer spatial/temporal incident engine."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any
from shapely.geometry import Polygon
from foresmoke.shared.contracts import (
    Candidate, Evidence, FrameObservation, HazardEvent, Phase, Zone, ZoneConfig, ZoneState,
    monitoring_objective, remediation_text,
)
from .config import validate_config
from .events import event_id, incident_id
from .geometry import classify

LOG = logging.getLogger(__name__)

@dataclass
class _State:
    phase: Phase = "CLEAR"
    observation_state: str = "unknown"
    reason: str = "initializing"
    positive_ms: int = 0
    negative_ms: int = 0
    first_seen: int | None = None
    confirmed_at: int | None = None
    incident: str | None = None
    ordinal: int = 0
    confirmation_ms: int = 0
    last_positive_seq: int | None = None
    previous_classification: str | None = None
    previous_t: int | None = None
    candidates: list[Candidate] = field(default_factory=list)

    def unknown(self, reason: str) -> None:
        self.observation_state, self.reason = "unknown", reason
        self.positive_ms = self.negative_ms = 0
        self.previous_classification = self.previous_t = None
        self.candidates = []
        if self.phase == "POSSIBLE":
            self.first_seen = None

class ReasoningEngine:
    def __init__(self, config: ZoneConfig, *, live_watchdog: bool = True):
        self.config = validate_config(config)
        self._watchdog_enabled = live_watchdog
        self._zones = {z.zone_id: _State() for z in self.config.zones}
        self._polygons = {z.zone_id: Polygon(z.polygon) for z in self.config.zones}
        self._assets = {asset.asset_id: asset for asset in self.config.assets}
        self._run_id: str | None = None
        self._kind: str | None = None
        self._last_seq = -1
        self._last_t: int | None = None
        self._last_ok_t: int | None = None
        self._epoch = 0
        self._received: int | None = None
        self._clock: int | None = None
        self._ended = False

    def _unknown(self, reason: str) -> None:
        for state in self._zones.values():
            state.unknown(reason)

    @staticmethod
    def _clock_value(value: int) -> None:
        if type(value) is not int or value < 0:
            raise ValueError("consumer clock must be nonnegative integer milliseconds")

    def invalidate_input(self) -> None:
        """CLI parse errors cannot be passed to process; break continuity explicitly."""
        self._unknown("invalid_input")

    def process(self, packet: FrameObservation | dict[str, Any], received_ms: int) -> list[HazardEvent]:
        try:
            packet = FrameObservation.model_validate(packet)
            self._clock_value(received_ms)
            if self._clock is not None and received_ms < self._clock:
                raise ValueError("consumer monotonic clock regressed")
            if packet.camera_id != self.config.camera_id or packet.view_id != self.config.view_id:
                raise ValueError("camera_id/view_id does not match calibrated configuration")
            if self._run_id is not None and packet.run_id != self._run_id:
                raise ValueError("run_id changed; restart engine for a new acquisition run")
            if self._kind is not None and packet.source_kind != self._kind:
                raise ValueError("source_kind changed within a run")
            if packet.seq <= self._last_seq:
                LOG.warning("Ignoring duplicate/out-of-order seq=%s (last=%s)", packet.seq, self._last_seq)
                return []
            if self._ended:
                raise ValueError("input already ended; create a new engine")
            if self._last_t is not None and packet.t_ms < self._last_t:
                raise ValueError("source timestamp regressed")
            if packet.tracker_epoch < self._epoch:
                raise ValueError("tracker_epoch decreased")
            if packet.status == "ok":
                if packet.source_kind == "image" and (packet.t_ms != 0 or self._last_ok_t is not None):
                    raise ValueError("image input permits exactly one ok observation at t_ms=0")
                if self._last_ok_t is not None and packet.t_ms <= self._last_ok_t:
                    raise ValueError("ok observation timestamps must strictly increase")
                assert packet.frame is not None
                ratio = packet.frame.width / packet.frame.height
                reference = self.config.reference_frame.width / self.config.reference_frame.height
                if abs(ratio / reference - 1) > 0.01 + 1e-12:
                    raise ValueError("frame aspect ratio differs from calibration by more than 1%")
        except (ValueError, TypeError):
            self._unknown("invalid_input")
            raise

        # Recovery after an unserviced watchdog still starts a fresh segment.
        if (self._watchdog_enabled and self._kind == "live" and self._received is not None
                and received_ms - self._received >= self.config.live_stale_after_ms):
            self._unknown("stale_input")
        self._run_id, self._kind = packet.run_id, packet.source_kind
        self._last_seq, self._last_t, self._epoch = packet.seq, packet.t_ms, packet.tracker_epoch
        self._received = self._clock = received_ms
        if packet.status != "ok":
            self._unknown("source_unavailable" if packet.status == "unavailable" else "source_ended")
            self._ended = packet.status == "eof"
            return []
        if self._last_ok_t is not None and packet.t_ms - self._last_ok_t > self.config.max_observation_gap_ms:
            self._unknown("observation_gap")
        self._last_ok_t = packet.t_ms
        events = []
        for zone in self.config.zones:
            state = self._zones[zone.zone_id]
            spatial = classify(packet.detections, zone, state.phase, self._polygons[zone.zone_id])
            if spatial.classification == "unknown":
                state.unknown("occluded")
                continue
            before = state.phase
            dt = 0 if state.previous_t is None else packet.t_ms - state.previous_t
            state.observation_state, state.reason = "observed", spatial.classification
            state.candidates = spatial.candidates
            if spatial.classification == "positive":
                state.negative_ms = 0
                state.last_positive_seq = packet.seq
                if state.phase in ("CLEAR", "RESOLVED"):
                    state.phase = "POSSIBLE"
                    state.positive_ms = state.confirmation_ms = 0
                    state.incident = state.confirmed_at = None
                    state.first_seen = packet.t_ms
                if state.first_seen is None:
                    state.first_seen = packet.t_ms
                if state.previous_classification == "positive":
                    state.positive_ms += dt
                if state.phase == "POSSIBLE" and state.positive_ms >= zone.confirm_ms:
                    state.ordinal += 1
                    state.incident = incident_id(packet.run_id, packet.camera_id, zone.zone_id, state.ordinal)
                    state.confirmed_at = packet.t_ms
                    state.confirmation_ms = state.positive_ms
                    state.phase = "CONFIRMED"
                    events.append(self._event(packet, zone, state, "incident_confirmed"))
            else:
                state.negative_ms = state.negative_ms + dt if state.previous_classification == "negative" else 0
                if state.phase == "POSSIBLE" and state.negative_ms >= zone.candidate_clear_ms:
                    state.phase = "CLEAR"
                    state.positive_ms = state.negative_ms = 0
                    state.first_seen = None
                    state.last_positive_seq = None
                elif state.phase == "CONFIRMED" and state.negative_ms >= zone.resolve_ms:
                    state.phase = "RESOLVED"
                    events.append(self._event(packet, zone, state, "incident_resolved"))
            state.previous_t, state.previous_classification = packet.t_ms, spatial.classification
            LOG.debug("zone=%s seq=%s t_ms=%s %s->%s class=%s dt=%s positive_ms=%s negative_ms=%s overlaps=%s",
                      zone.zone_id, packet.seq, packet.t_ms, before, state.phase, spatial.classification,
                      dt, state.positive_ms, state.negative_ms, spatial.overlaps)
        return events

    def _event(self, packet: FrameObservation, zone: Zone, state: _State, kind: str) -> HazardEvent:
        assert packet.frame is not None and state.incident is not None
        assert state.first_seen is not None and state.confirmed_at is not None
        assert state.last_positive_seq is not None and state.confirmation_ms >= zone.confirm_ms
        assert zone.name is not None
        asset = self._assets.get(zone.protected_asset_id or "")
        resolved = kind == "incident_resolved"
        assert not resolved or state.negative_ms >= zone.resolve_ms
        return HazardEvent(
            schema_version="1.1", event_type=kind, event_id=event_id(state.incident, kind),
            incident_id=state.incident, run_id=packet.run_id, camera_id=packet.camera_id,
            view_id=packet.view_id, zone_id=zone.zone_id, hazard_type=zone.hazard_type,
            zone_name=zone.name, zone_type=zone.zone_type,
            protected_asset_id=asset.asset_id if asset else None,
            protected_asset_name=asset.name if asset else None,
            remediation=remediation_text(zone.zone_type, asset.name if asset else None),
            phase=state.phase, t_ms=packet.t_ms, first_seen_ms=state.first_seen,
            confirmed_at_ms=state.confirmed_at, resolved_at_ms=packet.t_ms if resolved else None,
            confirmation_evidence_ms=state.confirmation_ms,
            clear_evidence_ms=state.negative_ms if resolved else 0,
            reason_code="observed_clear_interval" if resolved else "persistent_zone_occupancy",
            last_positive_seq=state.last_positive_seq,
            evidence=Evidence(seq=packet.seq, t_ms=packet.t_ms, frame_index=packet.frame.index,
                              frame_ref=packet.frame.frame_ref, tracker_epoch=packet.tracker_epoch,
                              objects=[c.model_copy(deep=True) for c in state.candidates]),
        )

    def tick(self, now_ms: int) -> None:
        self._clock_value(now_ms)
        if self._clock is not None and now_ms < self._clock:
            raise ValueError("consumer monotonic clock regressed")
        self._clock = now_ms
        if (self._watchdog_enabled and not self._ended and self._kind == "live" and self._received is not None
                and now_ms - self._received >= self.config.live_stale_after_ms):
            self._unknown("stale_input")

    @property
    def input_ended(self) -> bool:
        """True only after an accepted EOF or explicit close."""
        return self._ended

    def close_input(self) -> None:
        self._unknown("source_ended")
        self._ended = True

    def snapshots(self) -> list[ZoneState]:
        snapshots = []
        for zone in self.config.zones:
            s = self._zones[zone.zone_id]
            assert zone.name is not None
            asset = self._assets.get(zone.protected_asset_id or "")
            snapshots.append(
                ZoneState(schema_version="1.1", run_id=self._run_id,
                      camera_id=self.config.camera_id, view_id=self.config.view_id, zone_id=zone.zone_id,
                      zone_name=zone.name, zone_type=zone.zone_type, hazard_type=zone.hazard_type,
                      protected_asset_id=asset.asset_id if asset else None,
                      protected_asset_name=asset.name if asset else None,
                      remediation=remediation_text(zone.zone_type, asset.name if asset else None),
                      monitoring_objective=monitoring_objective(
                          zone.zone_type, asset.name if asset else None),
                      phase=s.phase, observation_state=s.observation_state, observation_reason=s.reason,
                      incident_id=s.incident, t_ms=self._last_t, positive_evidence_ms=s.positive_ms,
                      negative_run_ms=s.negative_ms, candidates=[c.model_copy(deep=True) for c in s.candidates])
            )
        return snapshots

