"""Contract 1.0 bootstrap from the briefs; Person 1 should adopt this single module."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

def uuid_string(value: str) -> str:
    if str(UUID(value)) != value.lower():
        raise ValueError("UUID must use its canonical hyphenated form")
    return value

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
UUIDString = Annotated[str, AfterValidator(uuid_string), Field(json_schema_extra={"format": "uuid"})]
Text = Annotated[str, Field(min_length=1)]
Label = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")]
Nonnegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
Unit = Annotated[float, Field(ge=0, le=1)]
Fraction = Annotated[float, Field(gt=0, le=1)]
Box = Annotated[list[Unit], Field(min_length=4, max_length=4)]
Point = Annotated[list[Unit], Field(min_length=2, max_length=2)]
Phase = Literal["CLEAR", "POSSIBLE", "CONFIRMED", "RESOLVED"]
Reason = Literal["positive", "negative", "initializing", "source_unavailable",
                 "source_ended", "occluded", "stale_input", "observation_gap", "invalid_input"]

class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
                              revalidate_instances="always")

class Detection(ContractModel):
    label: Label
    confidence: Unit
    bbox_xyxy: Box
    track_id: Text | None

    @model_validator(mode="after")
    def valid_box(self):
        x1, y1, x2, y2 = self.bbox_xyxy
        if not (x1 < x2 and y1 < y2):
            raise ValueError("bbox_xyxy must have positive width and height")
        return self

class Candidate(Detection):
    zone_overlap: Unit

class FrameMeta(ContractModel):
    index: Nonnegative
    width: Positive
    height: Positive
    frame_ref: Text | None

class FrameObservation(ContractModel):
    schema_version: Literal["1.0"]
    run_id: UUIDString
    camera_id: Identifier
    view_id: Identifier
    source_kind: Literal["live", "video", "image"]
    seq: Nonnegative
    t_ms: Nonnegative
    tracker_epoch: Nonnegative
    status: Literal["ok", "unavailable", "eof"]
    frame: FrameMeta | None
    detections: list[Detection]
    error: Text | None

    @model_validator(mode="after")
    def valid_status(self):
        if self.status == "ok":
            if self.frame is None or self.error is not None:
                raise ValueError("ok requires frame metadata and error=null")
        else:
            if self.frame is not None or self.detections:
                raise ValueError("unavailable/eof requires frame=null and detections=[]")
            if (self.status == "unavailable") != (self.error is not None):
                raise ValueError("unavailable requires an error; eof requires error=null")
        ids = [d.track_id for d in self.detections if d.track_id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("non-null track IDs must be unique within a frame")
        return self

class Zone(ContractModel):
    zone_id: Identifier
    hazard_type: Literal["blocked_exit"]
    polygon: Annotated[list[Point], Field(min_length=3)]
    candidate_labels: Annotated[list[Label], Field(min_length=1)]
    min_confidence: Unit
    footprint_height_fraction: Fraction
    enter_overlap: Fraction
    exit_overlap: Fraction
    person_occlusion_ratio: Fraction
    confirm_ms: Positive
    candidate_clear_ms: Positive
    resolve_ms: Positive

    @model_validator(mode="after")
    def valid_zone(self):
        points = [tuple(p) for p in self.polygon]
        if len(set(points)) < 3 or len(set(points)) != len(points):
            raise ValueError("polygon vertices must be distinct; omit the closing vertex")
        if self.exit_overlap > self.enter_overlap:
            raise ValueError("exit_overlap must be <= enter_overlap")
        if len(set(self.candidate_labels)) != len(self.candidate_labels):
            raise ValueError("candidate_labels must be unique")
        if "person" in self.candidate_labels:
            raise ValueError("person is reserved for occlusion in contract 1.0")
        return self

class ReferenceFrame(ContractModel):
    width: Positive
    height: Positive

class ZoneConfig(ContractModel):
    schema_version: Literal["1.0"]
    camera_id: Identifier
    view_id: Identifier
    reference_frame: ReferenceFrame
    max_observation_gap_ms: Positive
    live_stale_after_ms: Positive
    zones: Annotated[list[Zone], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_zones(self):
        ids = [z.zone_id for z in self.zones]
        if len(ids) != len(set(ids)):
            raise ValueError("zone_id must be unique")
        return self

class Evidence(ContractModel):
    seq: Nonnegative
    t_ms: Nonnegative
    frame_index: Nonnegative
    frame_ref: Text | None
    tracker_epoch: Nonnegative
    objects: list[Candidate]

class HazardEvent(ContractModel):
    schema_version: Literal["1.0"]
    event_type: Literal["incident_confirmed", "incident_resolved"]
    event_id: UUIDString
    incident_id: UUIDString
    run_id: UUIDString
    camera_id: Identifier
    view_id: Identifier
    zone_id: Identifier
    hazard_type: Literal["blocked_exit"]
    phase: Literal["CONFIRMED", "RESOLVED"]
    t_ms: Nonnegative
    first_seen_ms: Nonnegative
    confirmed_at_ms: Nonnegative
    resolved_at_ms: Nonnegative | None
    confirmation_evidence_ms: Nonnegative
    clear_evidence_ms: Nonnegative
    reason_code: Literal["persistent_zone_occupancy", "observed_clear_interval"]
    last_positive_seq: Nonnegative
    evidence: Evidence

    @model_validator(mode="after")
    def valid_transition(self):
        if not self.first_seen_ms <= self.confirmed_at_ms <= self.t_ms:
            raise ValueError("event times must be ordered")
        if self.evidence.t_ms != self.t_ms or self.evidence.seq < self.last_positive_seq:
            raise ValueError("evidence must identify the triggering frame")
        if self.event_type == "incident_confirmed":
            if (self.phase != "CONFIRMED" or self.resolved_at_ms is not None
                or self.clear_evidence_ms != 0 or self.confirmed_at_ms != self.t_ms
                or self.reason_code != "persistent_zone_occupancy" or not self.evidence.objects):
                raise ValueError("inconsistent confirmation event")
        elif (self.phase != "RESOLVED" or self.resolved_at_ms != self.t_ms
              or self.reason_code != "observed_clear_interval" or self.evidence.objects):
            raise ValueError("inconsistent resolution event")
        return self

class ZoneState(ContractModel):
    schema_version: Literal["1.0"]
    run_id: UUIDString | None
    camera_id: Identifier
    view_id: Identifier
    zone_id: Identifier
    phase: Phase
    observation_state: Literal["observed", "unknown"]
    observation_reason: Reason
    incident_id: UUIDString | None
    t_ms: Nonnegative | None
    positive_evidence_ms: Nonnegative
    negative_run_ms: Nonnegative
    candidates: list[Candidate]

    @model_validator(mode="after")
    def consistent_state(self):
        if (self.phase in ("CONFIRMED", "RESOLVED")) != (self.incident_id is not None):
            raise ValueError("incident ID must match lifecycle phase")
        if self.observation_state == "unknown" and (
            self.candidates or self.positive_evidence_ms or self.negative_run_ms
        ):
            raise ValueError("unknown state cannot retain current candidates/evidence timers")
        return self

