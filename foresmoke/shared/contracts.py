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
ZoneType = Literal["exit_access", "extinguisher_access"]
HazardType = Literal["blocked_exit", "blocked_extinguisher_access"]
AssetType = Literal["fire_extinguisher"]
HumanName = Annotated[str, Field(min_length=1, max_length=120)]
Reason = Literal["positive", "negative", "initializing", "source_unavailable",
                 "source_ended", "occluded", "stale_input", "observation_gap", "invalid_input"]

ZONE_TYPE_TO_HAZARD: dict[str, str] = {
    "exit_access": "blocked_exit",
    "extinguisher_access": "blocked_extinguisher_access",
}


def remediation_text(zone_type: str, asset_name: str | None = None) -> str:
    if zone_type == "extinguisher_access":
        return f"Remove the obstruction from the marked access area for {asset_name or 'the registered fire extinguisher'}."
    return "Remove the obstruction from the marked exit access area."


def monitoring_objective(zone_type: str, asset_name: str | None = None) -> str:
    if zone_type == "extinguisher_access":
        return (
            "Assess whether the supplied images support an obstruction of the marked "
            f"approach area for {asset_name or 'this registered fire extinguisher'}. "
            "Do not infer that the extinguisher is missing merely because it is not visible."
        )
    return "Assess whether the supplied images support an obstruction of the marked exit access area."


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

class ProtectedAsset(ContractModel):
    asset_id: Identifier
    asset_type: AssetType
    name: HumanName
    camera_id: Identifier
    view_id: Identifier
    bbox_xyxy: Box | None = None
    point: Point | None = None
    reference_image_id: Identifier | None = None

    @model_validator(mode="after")
    def valid_marker(self):
        if (self.bbox_xyxy is None) == (self.point is None):
            raise ValueError("asset requires exactly one of bbox_xyxy or point")
        if self.bbox_xyxy is not None:
            x1, y1, x2, y2 = self.bbox_xyxy
            if not (x1 < x2 and y1 < y2):
                raise ValueError("asset bbox_xyxy must have positive width and height")
        return self


class Zone(ContractModel):
    zone_id: Identifier
    name: HumanName | None = None
    zone_type: ZoneType = "exit_access"
    hazard_type: HazardType = "blocked_exit"
    protected_asset_id: Identifier | None = None
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

    @model_validator(mode="before")
    @classmethod
    def derive_purpose(cls, value):
        if isinstance(value, dict):
            data = dict(value)
            zone_type = data.get("zone_type", "exit_access")
            data.setdefault("hazard_type", ZONE_TYPE_TO_HAZARD.get(zone_type, "blocked_exit"))
            data.setdefault("name", data.get("zone_id"))
            data.setdefault("protected_asset_id", None)
            return data
        return value

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
        if "fire_extinguisher" in self.candidate_labels:
            raise ValueError("a protected fire extinguisher cannot be an obstruction candidate")
        expected = ZONE_TYPE_TO_HAZARD[self.zone_type]
        if self.hazard_type != expected:
            raise ValueError(
                f"hazard_type must be {expected!r} for zone_type {self.zone_type!r}"
            )
        if self.zone_type == "extinguisher_access" and self.protected_asset_id is None:
            raise ValueError("extinguisher_access requires protected_asset_id")
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
    assets: list[ProtectedAsset] = Field(default_factory=list)
    zones: Annotated[list[Zone], Field(min_length=1)]

    @model_validator(mode="after")
    def valid_associations(self):
        ids = [z.zone_id for z in self.zones]
        if len(ids) != len(set(ids)):
            raise ValueError("zone_id must be unique")
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset_id must be unique")
        assets = {asset.asset_id: asset for asset in self.assets}
        for asset in self.assets:
            if asset.camera_id != self.camera_id or asset.view_id != self.view_id:
                raise ValueError(
                    f"asset {asset.asset_id}: camera_id/view_id must match its configuration"
                )
        for zone in self.zones:
            if zone.protected_asset_id is None:
                continue
            asset = assets.get(zone.protected_asset_id)
            if asset is None:
                raise ValueError(
                    f"zone {zone.zone_id}: protected_asset_id does not reference an asset"
                )
            if zone.zone_type == "extinguisher_access" and asset.asset_type != "fire_extinguisher":
                raise ValueError(
                    f"zone {zone.zone_id}: extinguisher_access requires a fire_extinguisher asset"
                )
        return self

class Evidence(ContractModel):
    seq: Nonnegative
    t_ms: Nonnegative
    frame_index: Nonnegative
    frame_ref: Text | None
    tracker_epoch: Nonnegative
    objects: list[Candidate]

class HazardEvent(ContractModel):
    model_config = ConfigDict(json_schema_extra={
        "allOf": [
            {
                "if": {"properties": {"schema_version": {"const": "1.1"}}},
                "then": {"required": [
                    "zone_name", "zone_type", "protected_asset_id",
                    "protected_asset_name", "remediation",
                ]},
            },
            {
                "if": {"properties": {"schema_version": {"const": "1.0"}}},
                "then": {"properties": {
                    "hazard_type": {"const": "blocked_exit"},
                    "zone_type": {"const": "exit_access"},
                    "protected_asset_id": {"type": "null"},
                    "protected_asset_name": {"type": "null"},
                }},
            },
        ]
    })
    schema_version: Literal["1.0", "1.1"]
    event_type: Literal["incident_confirmed", "incident_resolved"]
    event_id: UUIDString
    incident_id: UUIDString
    run_id: UUIDString
    camera_id: Identifier
    view_id: Identifier
    zone_id: Identifier
    hazard_type: HazardType
    zone_name: HumanName | None = None
    zone_type: ZoneType = "exit_access"
    protected_asset_id: Identifier | None = None
    protected_asset_name: HumanName | None = None
    remediation: Text | None = None
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

    @model_validator(mode="before")
    @classmethod
    def derive_context(cls, value):
        if isinstance(value, dict):
            data = dict(value)
            version = data.get("schema_version")
            if version == "1.1":
                required = {
                    "zone_name", "zone_type", "protected_asset_id",
                    "protected_asset_name", "remediation",
                }
                missing = sorted(required.difference(data))
                if missing:
                    raise ValueError(f"1.1 event missing context fields: {missing}")
            elif version == "1.0" and (
                data.get("hazard_type", "blocked_exit") != "blocked_exit"
                or data.get("zone_type", "exit_access") != "exit_access"
                or data.get("protected_asset_id") is not None
                or data.get("protected_asset_name") is not None
            ):
                raise ValueError("1.0 events support exit access only")
            hazard = data.get("hazard_type", "blocked_exit")
            zone_type = (
                "extinguisher_access"
                if hazard == "blocked_extinguisher_access"
                else "exit_access"
            )
            data.setdefault("zone_type", zone_type)
            data.setdefault("zone_name", data.get("zone_id"))
            data.setdefault("protected_asset_id", None)
            data.setdefault("protected_asset_name", None)
            data.setdefault(
                "remediation",
                remediation_text(zone_type, data.get("protected_asset_name")),
            )
            return data
        return value

    @model_validator(mode="after")
    def valid_transition(self):
        if not self.first_seen_ms <= self.confirmed_at_ms <= self.t_ms:
            raise ValueError("event times must be ordered")
        if self.evidence.t_ms != self.t_ms or self.evidence.seq < self.last_positive_seq:
            raise ValueError("evidence must identify the triggering frame")
        if self.hazard_type != ZONE_TYPE_TO_HAZARD[self.zone_type]:
            raise ValueError("event hazard_type must match zone_type")
        if self.zone_type == "extinguisher_access" and self.protected_asset_id is None:
            raise ValueError("extinguisher event requires a protected asset")
        if (self.protected_asset_id is None) != (self.protected_asset_name is None):
            raise ValueError("event asset ID and name must be present together")
        if self.zone_name is None or self.remediation is None:
            raise ValueError("event requires derived zone name and remediation")
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
    model_config = ConfigDict(json_schema_extra={
        "allOf": [
            {
                "if": {"properties": {"schema_version": {"const": "1.1"}}},
                "then": {"required": [
                    "zone_name", "zone_type", "hazard_type", "protected_asset_id",
                    "protected_asset_name", "remediation", "monitoring_objective",
                ]},
            },
            {
                "if": {"properties": {"schema_version": {"const": "1.0"}}},
                "then": {"properties": {
                    "hazard_type": {"const": "blocked_exit"},
                    "zone_type": {"const": "exit_access"},
                    "protected_asset_id": {"type": "null"},
                    "protected_asset_name": {"type": "null"},
                }},
            },
        ]
    })
    schema_version: Literal["1.0", "1.1"]
    run_id: UUIDString | None
    camera_id: Identifier
    view_id: Identifier
    zone_id: Identifier
    zone_name: HumanName | None = None
    zone_type: ZoneType = "exit_access"
    hazard_type: HazardType = "blocked_exit"
    protected_asset_id: Identifier | None = None
    protected_asset_name: HumanName | None = None
    remediation: Text | None = None
    monitoring_objective: Text | None = None
    phase: Phase
    observation_state: Literal["observed", "unknown"]
    observation_reason: Reason
    incident_id: UUIDString | None
    t_ms: Nonnegative | None
    positive_evidence_ms: Nonnegative
    negative_run_ms: Nonnegative
    candidates: list[Candidate]

    @model_validator(mode="before")
    @classmethod
    def derive_context(cls, value):
        if isinstance(value, dict):
            data = dict(value)
            version = data.get("schema_version")
            if version == "1.1":
                required = {
                    "zone_name", "zone_type", "hazard_type", "protected_asset_id",
                    "protected_asset_name", "remediation", "monitoring_objective",
                }
                missing = sorted(required.difference(data))
                if missing:
                    raise ValueError(f"1.1 snapshot missing context fields: {missing}")
            elif version == "1.0" and (
                data.get("hazard_type", "blocked_exit") != "blocked_exit"
                or data.get("zone_type", "exit_access") != "exit_access"
                or data.get("protected_asset_id") is not None
                or data.get("protected_asset_name") is not None
            ):
                raise ValueError("1.0 snapshots support exit access only")
            hazard = data.get("hazard_type", "blocked_exit")
            zone_type = data.get(
                "zone_type",
                "extinguisher_access"
                if hazard == "blocked_extinguisher_access"
                else "exit_access",
            )
            data.setdefault("hazard_type", ZONE_TYPE_TO_HAZARD[zone_type])
            data.setdefault("zone_type", zone_type)
            data.setdefault("zone_name", data.get("zone_id"))
            data.setdefault("protected_asset_id", None)
            data.setdefault("protected_asset_name", None)
            data.setdefault(
                "remediation",
                remediation_text(zone_type, data.get("protected_asset_name")),
            )
            data.setdefault(
                "monitoring_objective",
                monitoring_objective(zone_type, data.get("protected_asset_name")),
            )
            return data
        return value

    @model_validator(mode="after")
    def consistent_state(self):
        if (self.phase in ("CONFIRMED", "RESOLVED")) != (self.incident_id is not None):
            raise ValueError("incident ID must match lifecycle phase")
        if self.observation_state == "unknown" and (
            self.candidates or self.positive_evidence_ms or self.negative_run_ms
        ):
            raise ValueError("unknown state cannot retain current candidates/evidence timers")
        if self.hazard_type != ZONE_TYPE_TO_HAZARD[self.zone_type]:
            raise ValueError("snapshot hazard_type must match zone_type")
        if self.zone_type == "extinguisher_access" and self.protected_asset_id is None:
            raise ValueError("extinguisher snapshot requires a protected asset")
        if (self.protected_asset_id is None) != (self.protected_asset_name is None):
            raise ValueError("snapshot asset ID and name must be present together")
        if self.zone_name is None or self.remediation is None or self.monitoring_objective is None:
            raise ValueError("snapshot requires derived zone context")
        return self

