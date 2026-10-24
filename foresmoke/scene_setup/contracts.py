"""Typed Kiro scene-setup output and deterministic geometry validation."""
from __future__ import annotations

from math import hypot
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from shapely.geometry import LineString, Point, Polygon

ShortText = Annotated[str, Field(min_length=1, max_length=300)]
PixelPoint = Annotated[list[float], Field(min_length=2, max_length=2)]
PixelBox = Annotated[list[float], Field(min_length=4, max_length=4)]


class SetupContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class ModelCandidate(SetupContract):
    candidate_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=80)]
    zone_type: Literal["exit_access", "extinguisher_access"]
    label: Annotated[str, Field(min_length=1, max_length=120)]
    asset_bbox_px: PixelBox | None
    threshold_px: Annotated[list[PixelPoint], Field(min_length=2, max_length=2)] | None
    access_polygon_px: Annotated[list[PixelPoint], Field(min_length=3, max_length=8)] | None
    visibility: Literal["adequate", "partially_occluded", "insufficient"]
    evidence: Annotated[str, Field(min_length=1, max_length=600)]
    limitations: Annotated[list[ShortText], Field(max_length=6)]

    @model_validator(mode="after")
    def structural_geometry(self):
        if self.asset_bbox_px is not None:
            x1, y1, x2, y2 = self.asset_bbox_px
            if not (x1 < x2 and y1 < y2):
                raise ValueError("asset_bbox_px must have positive width and height")
        if self.access_polygon_px is not None:
            points = [tuple(point) for point in self.access_polygon_px]
            if len(set(points)) != len(points):
                raise ValueError("access polygon vertices must be distinct")
        return self


class ModelSceneHeader(SetupContract):
    scene_status: Literal[
        "proposals_available", "no_candidate", "insufficient_visibility"
    ]
    scene_limitations: Annotated[list[ShortText], Field(max_length=8)]


class CandidateProposal(SetupContract):
    candidate_key: str
    zone_type: Literal["exit_access", "extinguisher_access"] | None
    label: str
    asset_bbox_px: list[float] | None
    asset_bbox: list[float] | None
    threshold_px: list[list[float]] | None
    threshold: list[list[float]] | None
    access_polygon_px: list[list[float]] | None
    access_polygon: list[list[float]] | None
    visibility: Literal["adequate", "partially_occluded", "insufficient"] | None
    evidence: str | None
    limitations: list[str]
    validation_warnings: list[str]
    validation_errors: list[str]
    valid: bool


class SceneProposalResult(SetupContract):
    scene_status: Literal[
        "proposals_available", "no_candidate", "insufficient_visibility"
    ]
    scene_limitations: list[str]
    candidates: list[CandidateProposal]


def _points_in_range(points: list[list[float]], width: int, height: int) -> bool:
    return all(0 <= point[0] <= width and 0 <= point[1] <= height for point in points)


def _normalized_points(points: list[list[float]], width: int, height: int) -> list[list[float]]:
    return [[point[0] / width, point[1] / height] for point in points]


def validate_model_response(
    payload: Any,
    *,
    width: int,
    height: int,
    min_area_ratio: float = 0.002,
    large_area_ratio: float = 0.70,
    threshold_tolerance_ratio: float = 0.04,
) -> SceneProposalResult:
    """Validate each candidate independently and convert submitted pixels to normalized geometry."""
    if not isinstance(payload, dict):
        raise ValueError("model response must be a JSON object")
    header = ModelSceneHeader.model_validate({
        "scene_status": payload.get("scene_status"),
        "scene_limitations": payload.get("scene_limitations"),
    })
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates must be a list")
    if len(raw_candidates) > 3:
        raise ValueError("at most three candidates are allowed")
    results: list[CandidateProposal] = []
    for index, raw in enumerate(raw_candidates):
        fallback_key = (
            raw.get("candidate_key")
            if isinstance(raw, dict) and isinstance(raw.get("candidate_key"), str)
            else f"candidate_{index + 1}"
        )
        try:
            candidate = ModelCandidate.model_validate(raw)
            results.append(_validate_candidate(
                candidate,
                width,
                height,
                min_area_ratio,
                large_area_ratio,
                threshold_tolerance_ratio,
            ))
        except (ValidationError, ValueError) as error:
            results.append(CandidateProposal(
                candidate_key=fallback_key[:80],
                zone_type=raw.get("zone_type") if isinstance(raw, dict) else None,
                label=(
                    str(raw.get("label", fallback_key))[:120]
                    if isinstance(raw, dict) else fallback_key
                ),
                asset_bbox_px=None,
                asset_bbox=None,
                threshold_px=None,
                threshold=None,
                access_polygon_px=None,
                access_polygon=None,
                visibility=None,
                evidence=None,
                limitations=[],
                validation_warnings=[],
                validation_errors=[str(error)[:1000]],
                valid=False,
            ))
    if header.scene_status == "proposals_available" and not results:
        raise ValueError("proposals_available requires at least one candidate")
    return SceneProposalResult(
        scene_status=header.scene_status,
        scene_limitations=header.scene_limitations,
        candidates=results,
    )


def _validate_candidate(
    candidate: ModelCandidate,
    width: int,
    height: int,
    min_area_ratio: float,
    large_area_ratio: float,
    threshold_tolerance_ratio: float,
) -> CandidateProposal:
    errors: list[str] = []
    warnings: list[str] = []
    box = candidate.asset_bbox_px
    threshold = candidate.threshold_px
    polygon_points = candidate.access_polygon_px
    if box is not None and not _points_in_range(
        [[box[0], box[1]], [box[2], box[3]]], width, height
    ):
        errors.append("asset_bbox_px is outside the submitted image")
    if threshold is not None and not _points_in_range(threshold, width, height):
        errors.append("threshold_px is outside the submitted image")
    if polygon_points is not None and not _points_in_range(polygon_points, width, height):
        errors.append("access_polygon_px is outside the submitted image")

    polygon = None
    if polygon_points is not None and not errors:
        polygon = Polygon(polygon_points)
        if not polygon.is_valid:
            errors.append("access polygon is self-intersecting or otherwise invalid")
        elif polygon.area <= 0:
            errors.append("access polygon has zero area")
        else:
            area_ratio = polygon.area / (width * height)
            if area_ratio < min_area_ratio:
                errors.append(
                    f"access polygon is smaller than the configured {min_area_ratio:.3%} minimum"
                )
            if area_ratio > large_area_ratio:
                warnings.append(
                    f"Access polygon covers {area_ratio:.1%} of the frame; verify it carefully."
                )

    if candidate.zone_type == "exit_access" and polygon_points is not None:
        if threshold is None:
            errors.append("exit proposal with a polygon requires threshold_px")
        elif polygon is not None and polygon.is_valid:
            boundary = LineString(list(polygon.exterior.coords))
            tolerance = threshold_tolerance_ratio * hypot(width, height)
            if any(boundary.distance(Point(point)) > tolerance for point in threshold):
                errors.append(
                    "door threshold is not reasonably associated with the access polygon boundary"
                )
    if candidate.zone_type == "extinguisher_access" and box is None:
        errors.append("extinguisher proposal requires a separate visible asset_bbox_px")
    if candidate.visibility == "insufficient" and polygon_points is not None:
        errors.append("insufficient visibility cannot support an access polygon")
    if polygon_points is None:
        warnings.append("No floor access polygon was proposed; choose a clearer frame or draw manually.")

    normalized_box = (
        [box[0] / width, box[1] / height, box[2] / width, box[3] / height]
        if box is not None and _points_in_range([[box[0], box[1]], [box[2], box[3]]], width, height)
        else None
    )
    return CandidateProposal(
        candidate_key=candidate.candidate_key,
        zone_type=candidate.zone_type,
        label=candidate.label,
        asset_bbox_px=box,
        asset_bbox=normalized_box,
        threshold_px=threshold,
        threshold=(
            _normalized_points(threshold, width, height)
            if threshold is not None and _points_in_range(threshold, width, height)
            else None
        ),
        access_polygon_px=polygon_points,
        access_polygon=(
            _normalized_points(polygon_points, width, height)
            if polygon_points is not None and _points_in_range(polygon_points, width, height)
            else None
        ),
        visibility=candidate.visibility,
        evidence=candidate.evidence,
        limitations=candidate.limitations,
        validation_warnings=warnings,
        validation_errors=errors,
        valid=not errors and polygon_points is not None,
    )
