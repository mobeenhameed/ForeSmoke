"""Versioned contracts for immutable visual-review evidence and results."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foresmoke.shared.contracts import (
    Box,
    HazardType,
    HumanName,
    Identifier,
    Nonnegative,
    Phase,
    Point,
    ProtectedAsset,
    Unit,
    UUIDString,
    ZoneType,
)

ShortText = Annotated[str, Field(min_length=1, max_length=500)]
SummaryText = Annotated[str, Field(min_length=1, max_length=1000)]
PathText = Annotated[str, Field(min_length=1)]
ReviewTrigger = Literal["candidate", "confirmed", "resolved"]
ReviewStatus = Literal["pending", "running", "completed", "failed", "skipped"]


class ReviewModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, revalidate_instances="always"
    )


class ObjectAssessment(ReviewModel):
    label: ShortText
    supplied_object_id: Identifier | None
    relationship_to_zone: Literal["appears_inside", "appears_outside", "ambiguous"]
    motion_assessment: Literal["appears_stationary", "appears_moving", "unknown"]


class VisibleEvidence(ReviewModel):
    frame_id: Identifier
    observation: ShortText


class VisualAssessment(ReviewModel):
    assessment: Literal[
        "supports_obstruction", "does_not_support_obstruction", "uncertain"
    ]
    objects_of_interest: Annotated[list[ObjectAssessment], Field(max_length=12)]
    visibility: Literal["adequate", "partially_occluded", "insufficient"]
    visible_evidence: Annotated[
        list[VisibleEvidence], Field(min_length=1, max_length=12)
    ]
    summary: SummaryText
    recommended_action: ShortText
    limitations: Annotated[list[ShortText], Field(max_length=10)]

    def validate_evidence_ids(
        self, supplied_frame_ids: set[str], supplied_object_ids: set[str]
    ) -> None:
        unknown = {item.frame_id for item in self.visible_evidence} - supplied_frame_ids
        if unknown:
            raise ValueError(
                f"visible_evidence references unknown frame IDs: {sorted(unknown)}"
            )
        unknown_objects = {
            item.supplied_object_id
            for item in self.objects_of_interest
            if item.supplied_object_id is not None
            and item.supplied_object_id not in supplied_object_ids
        }
        if unknown_objects:
            raise ValueError(
                f"objects_of_interest references unknown object IDs: {sorted(unknown_objects)}"
            )


class EvidenceDetection(ReviewModel):
    label: Identifier
    confidence: Unit
    bbox_xyxy: Box
    track_id: ShortText | None
    supplied_object_id: Identifier
    zone_overlap: Unit | None


class EvidenceFrame(ReviewModel):
    frame_id: Identifier
    seq: Nonnegative
    source_timestamp_ms: Nonnegative
    source_frame_index: Nonnegative
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    evidence_width: Annotated[int, Field(gt=0)]
    evidence_height: Annotated[int, Field(gt=0)]
    raw_image: PathText
    annotated_image: PathText
    detections: Annotated[list[EvidenceDetection], Field(max_length=100)]


class ClearReference(ReviewModel):
    raw_image: PathText
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    source_timestamp_ms: Nonnegative | None
    user_approved: Literal[True]


class EngineEvidence(ReviewModel):
    phase: Phase
    observation_state: Literal["observed", "unknown"]
    observation_reason: ShortText
    positive_evidence_ms: Nonnegative
    negative_run_ms: Nonnegative
    configured_confirm_ms: Annotated[int, Field(gt=0)]
    configured_resolve_ms: Annotated[int, Field(gt=0)]
    enter_overlap: Annotated[float, Field(gt=0, le=1)]
    exit_overlap: Annotated[float, Field(gt=0, le=1)]


class EvidenceManifest(ReviewModel):
    schema_version: Literal["1.0"]
    review_id: UUIDString
    camera_id: Identifier
    run_id: UUIDString
    view_id: Identifier
    zone_id: Identifier
    zone_name: HumanName
    zone_type: ZoneType
    hazard_type: HazardType
    protected_asset: ProtectedAsset | None
    monitoring_objective: SummaryText
    incident_id: UUIDString | None
    trigger: ReviewTrigger
    source_timestamp_ms: Nonnegative
    coordinate_convention: Literal[
        "normalized_top_left_origin_x_right_y_down"
    ] = "normalized_top_left_origin_x_right_y_down"
    zone_polygon: Annotated[list[Point], Field(min_length=3)]
    engine_evidence: EngineEvidence
    clear_reference: ClearReference | None
    frames: Annotated[list[EvidenceFrame], Field(min_length=1, max_length=3)]
    no_clear_reference_limitation: bool

    @model_validator(mode="after")
    def consistent_manifest(self):
        if self.no_clear_reference_limitation != (self.clear_reference is None):
            raise ValueError(
                "no_clear_reference_limitation must reflect clear_reference availability"
            )
        frame_ids = [frame.frame_id for frame in self.frames]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("evidence frame IDs must be unique")
        if any(frame.source_timestamp_ms > self.source_timestamp_ms for frame in self.frames):
            raise ValueError("evidence cannot contain future frames")
        return self


class ReviewRecord(ReviewModel):
    schema_version: Literal["1.0"]
    review_id: UUIDString
    camera_id: Identifier
    run_id: UUIDString
    view_id: Identifier
    zone_id: Identifier
    incident_id: UUIDString | None
    trigger: ReviewTrigger
    source_timestamp_ms: Nonnegative
    status: ReviewStatus
    provider: Literal["kiro_cli"] = "kiro_cli"
    requested_model: ShortText
    reported_model: ShortText | None
    latency_ms: Nonnegative | None
    stale_for_current_state: bool
    evidence_manifest: PathText
    assessment: VisualAssessment | None
    error: ShortText | None

    @model_validator(mode="after")
    def status_payload(self):
        if self.status == "completed":
            if self.assessment is None or self.error is not None:
                raise ValueError("completed review requires assessment and no error")
        elif self.assessment is not None:
            raise ValueError("non-completed review cannot contain an assessment")
        if self.status in ("failed", "skipped") and self.error is None:
            raise ValueError(f"{self.status} review requires an error")
        if self.status in ("pending", "running") and self.error is not None:
            raise ValueError(f"{self.status} review cannot contain an error")
        return self
