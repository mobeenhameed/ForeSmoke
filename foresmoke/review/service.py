"""Bounded asynchronous scheduling, correlation, and persistence for reviews."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time
from typing import Protocol
from uuid import uuid4

from foresmoke.shared.contracts import HazardEvent, ProtectedAsset, Zone, ZoneState

from .config import ReviewSettings
from .contracts import ReviewRecord, ReviewTrigger
from .evidence import EvidenceBundle, EvidenceCapture, FrameSample
from .kiro_cli import KiroCliAdapter, KiroReviewError, KiroReviewResult

LOG = logging.getLogger(__name__)


class ReviewAdapter(Protocol):
    def review(self, bundle: EvidenceBundle) -> KiroReviewResult: ...
    def cancel_active(self) -> None: ...


@dataclass(frozen=True)
class ReviewJob:
    bundle: EvidenceBundle
    occurrence_key: str
    generation: int


class ReviewLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: ReviewRecord) -> None:
        line = record.model_dump_json() + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()


class VisualReviewService:
    """Observe engine output synchronously; execute Kiro calls on one worker."""

    def __init__(
        self,
        *,
        run_dir: Path,
        repo_root: Path,
        settings: ReviewSettings,
        zones: list[Zone],
        assets: list[ProtectedAsset] | None = None,
        adapter: ReviewAdapter | None = None,
    ):
        self.settings = settings.validate()
        self.run_dir = Path(run_dir)
        self.capture = EvidenceCapture(self.run_dir, settings)
        self.adapter = adapter or KiroCliAdapter(repo_root, settings)
        self.log = ReviewLog(self.run_dir / "reviews.jsonl")
        self.zones = {zone.zone_id: zone for zone in zones}
        self.assets = {asset.asset_id: asset for asset in (assets or [])}
        self._condition = threading.Condition()
        self._pending: deque[ReviewJob] = deque()
        self._stopping = False
        self._abort = False
        self._active_job: ReviewJob | None = None
        self._completed_current: dict[str, tuple[ReviewJob, ReviewRecord]] = {}
        self._submitted = 0
        self._candidate_requested: dict[str, int] = {}
        self._generation: dict[str, int] = {zone.zone_id: 0 for zone in zones}
        self._previous_phase: dict[str, str] = {
            zone.zone_id: "CLEAR" for zone in zones
        }
        self._current: dict[str, tuple[int, str, str | None, str]] = {}
        self._last_requested_ms: dict[str, int] = {}
        self._worker = threading.Thread(
            target=self._work, daemon=True, name="foresmoke-visual-review"
        )
        self._worker.start()

    def observe(
        self,
        *,
        samples: list[FrameSample],
        states: list[ZoneState],
        events: list[HazardEvent],
    ) -> None:
        event_by_zone = {event.zone_id: event for event in events}
        for state in states:
            try:
                with self._condition:
                    previous = self._previous_phase.get(state.zone_id, "CLEAR")
                    if state.phase == "POSSIBLE" and previous in ("CLEAR", "RESOLVED"):
                        self._generation[state.zone_id] = self._generation.get(state.zone_id, 0) + 1
                    generation = self._generation.get(state.zone_id, 0)
                    self._current[state.zone_id] = (
                        generation,
                        state.phase,
                        state.incident_id,
                        state.observation_state,
                    )
                    self._mark_completed_stale_locked(state.zone_id)
                    candidate_due = (
                        state.phase == "POSSIBLE"
                        and state.observation_state == "observed"
                        and state.positive_evidence_ms >= self.settings.candidate_delay_ms
                        and self._candidate_requested.get(state.zone_id) != generation
                    )
                    if candidate_due:
                        self._candidate_requested[state.zone_id] = generation
                    self._previous_phase[state.zone_id] = state.phase
                zone = self.zones[state.zone_id]
                event = event_by_zone.get(state.zone_id)
                if event is not None and event.event_type == "incident_resolved":
                    self._request(
                        samples=samples, state=state, zone=zone, trigger="resolved",
                        incident_id=event.incident_id, generation=generation,
                        bypass_cooldown=True,
                    )
                elif event is not None and event.event_type == "incident_confirmed":
                    self._request(
                        samples=samples, state=state, zone=zone, trigger="confirmed",
                        incident_id=event.incident_id, generation=generation,
                    )
                elif candidate_due:
                    self._request(
                        samples=samples, state=state, zone=zone, trigger="candidate",
                        incident_id=None, generation=generation,
                    )
            except Exception:
                # Optional review failures must never interrupt deterministic analysis.
                LOG.exception("visual review scheduling failed for zone %s", state.zone_id)

    def _request(
        self,
        *,
        samples: list[FrameSample],
        state: ZoneState,
        zone: Zone,
        trigger: ReviewTrigger,
        incident_id: str | None,
        generation: int,
        bypass_cooldown: bool = False,
    ) -> None:
        assert state.t_ms is not None
        last = self._last_requested_ms.get(zone.zone_id)
        if (
            not bypass_cooldown
            and last is not None
            and state.t_ms - last < self.settings.cooldown_ms
        ):
            self._audit_skip(
                state,
                trigger,
                incident_id,
                "source_time_cooldown",
            )
            return
        if self._submitted >= self.settings.max_reviews_per_run:
            self._audit_skip(state, trigger, incident_id, "run_review_limit_reached")
            return
        try:
            bundle = self.capture.create(
                samples=samples,
                state=state,
                zone=zone,
                protected_asset=self.assets.get(zone.protected_asset_id or ""),
                trigger=trigger,
                incident_id=incident_id,
            )
        except (OSError, ValueError) as error:
            self._audit_skip(
                state,
                trigger,
                incident_id,
                f"evidence_unavailable: {error}",
            )
            return

        occurrence_key = f"{zone.zone_id}:{generation}"
        job = ReviewJob(bundle, occurrence_key, generation)
        with self._condition:
            replaced = next(
                (pending for pending in self._pending if pending.occurrence_key == occurrence_key),
                None,
            )
            if replaced is not None:
                self._pending.remove(replaced)
                self._safe_append(
                    self._record(
                        replaced.bundle,
                        "skipped",
                        stale=True,
                        error="replaced_by_newer_pending_evidence",
                    )
                )
            elif len(self._pending) >= self.settings.queue_capacity:
                self._safe_append(
                    self._record(
                        bundle,
                        "skipped",
                        stale=False,
                        error="review_queue_full",
                    )
                )
                return
            self._pending.append(job)
            self._submitted += 1
            self._last_requested_ms[zone.zone_id] = state.t_ms
            self._safe_append(self._record(bundle, "pending"))
            self._condition.notify()

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stopping:
                    self._condition.wait()
                if self._abort or (self._stopping and not self._pending):
                    return
                job = self._pending.popleft()
                self._active_job = job
            self._safe_append(self._record(job.bundle, "running"))
            started = time.monotonic()
            try:
                result = self.adapter.review(job.bundle)
                with self._condition:
                    aborted = self._abort
                    stale = self._is_stale_locked(job)
                    record = self._record(
                        job.bundle, "failed" if aborted else "completed",
                        latency_ms=result.latency_ms, stale=stale,
                        assessment=None if aborted else result.assessment,
                        reported_model=result.reported_model,
                        error="shutdown_cancelled_running_review" if aborted else None,
                    )
                    self._safe_append(record)
                    if not aborted and not stale:
                        self._completed_current[record.review_id] = (job, record)
            except KiroReviewError as error:
                self._safe_append(
                    self._record(
                        job.bundle,
                        "failed",
                        latency_ms=max(0, round((time.monotonic() - started) * 1000)),
                        stale=self._is_stale(job),
                        error=f"{error.code}: {error.diagnostic}"[:500],
                    )
                )
            except Exception as error:  # adapters must never kill the review worker
                self._safe_append(
                    self._record(
                        job.bundle,
                        "failed",
                        latency_ms=max(0, round((time.monotonic() - started) * 1000)),
                        stale=self._is_stale(job),
                        error=f"unexpected_review_error: {error}"[:500],
                    )
                )
            finally:
                with self._condition:
                    if self._active_job is job:
                        self._active_job = None
                    self._condition.notify_all()

    def _is_stale(self, job: ReviewJob) -> bool:
        with self._condition:
            return self._is_stale_locked(job)

    def _is_stale_locked(self, job: ReviewJob) -> bool:
        manifest = job.bundle.manifest
        current = self._current.get(manifest.zone_id)
        if current is None:
            return True
        generation, phase, incident_id, observation_state = current
        if generation != job.generation:
            return True
        if manifest.trigger != "resolved" and phase == "RESOLVED":
            return True
        if manifest.trigger == "candidate" and (
            phase == "CLEAR" or observation_state == "unknown"
        ):
            return True
        if manifest.incident_id is not None and incident_id != manifest.incident_id:
            return True
        return False

    def _mark_completed_stale_locked(self, zone_id: str) -> None:
        for review_id, (job, record) in list(self._completed_current.items()):
            if job.bundle.manifest.zone_id != zone_id or not self._is_stale_locked(job):
                continue
            stale_record = record.model_copy(update={"stale_for_current_state": True})
            self._safe_append(stale_record)
            self._completed_current.pop(review_id, None)

    def _audit_skip(
        self,
        state: ZoneState,
        trigger: ReviewTrigger,
        incident_id: str | None,
        error: str,
    ) -> None:
        self._safe_append(
            ReviewRecord(
                schema_version="1.0",
                review_id=str(uuid4()),
                camera_id=state.camera_id,
                run_id=state.run_id
                or "00000000-0000-0000-0000-000000000000",
                view_id=state.view_id,
                zone_id=state.zone_id,
                incident_id=incident_id,
                trigger=trigger,
                source_timestamp_ms=state.t_ms or 0,
                status="skipped",
                requested_model=self.settings.model,
                reported_model=None,
                latency_ms=None,
                stale_for_current_state=False,
                evidence_manifest="unavailable",
                assessment=None,
                error=error[:500],
            )
        )

    def _safe_append(self, record: ReviewRecord) -> None:
        try:
            self.log.append(record)
        except OSError:
            LOG.exception("could not persist visual review status %s", record.status)

    def _record(
        self,
        bundle: EvidenceBundle,
        status: str,
        *,
        latency_ms: int | None = None,
        stale: bool = False,
        assessment=None,
        reported_model: str | None = None,
        error: str | None = None,
    ) -> ReviewRecord:
        manifest = bundle.manifest
        return ReviewRecord(
            schema_version="1.0",
            review_id=manifest.review_id,
            camera_id=manifest.camera_id,
            run_id=manifest.run_id,
            view_id=manifest.view_id,
            zone_id=manifest.zone_id,
            incident_id=manifest.incident_id,
            trigger=manifest.trigger,
            source_timestamp_ms=manifest.source_timestamp_ms,
            status=status,
            requested_model=self.settings.model,
            reported_model=reported_model,
            latency_ms=latency_ms,
            stale_for_current_state=stale,
            evidence_manifest=bundle.relative_manifest,
            assessment=assessment,
            error=error,
        )

    def shutdown(self, *, wait_s: float = 5.0, drain: bool = False) -> None:
        with self._condition:
            self._stopping = True
            if not drain:
                self._abort = True
                self._skip_pending_locked("shutdown_before_review_started")
            self._condition.notify_all()
        if not drain:
            self.adapter.cancel_active()
        self._worker.join(timeout=wait_s)
        if self._worker.is_alive():
            with self._condition:
                self._abort = True
                self._skip_pending_locked("shutdown_deadline_reached")
                self._condition.notify_all()
            self.adapter.cancel_active()
            self._worker.join(timeout=2)
        if self._worker.is_alive():
            self.adapter.cancel_active()
            LOG.error("visual review worker did not stop after cancellation")

    def _skip_pending_locked(self, reason: str) -> None:
        while self._pending:
            job = self._pending.popleft()
            self._safe_append(
                self._record(job.bundle, "skipped", stale=True, error=reason)
            )
