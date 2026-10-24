"""Pure image-space geometry. No tracking or timing policy here."""
from dataclasses import dataclass
from typing import Literal
from shapely.geometry import Polygon, box
from foresmoke.shared.contracts import Candidate, Detection, Phase, Zone

# Tolerance only compensates for arithmetic at an exact configured boundary.
EPSILON = 1e-12

@dataclass(frozen=True)
class SpatialResult:
    classification: Literal["positive", "negative", "unknown"]
    candidates: list[Candidate]
    overlaps: list[float]
    max_person_occlusion: float

def classify(detections: list[Detection], zone: Zone, phase: Phase,
             polygon: Polygon | None = None) -> SpatialResult:
    polygon = Polygon(zone.polygon) if polygon is None else polygon
    threshold = zone.exit_overlap if phase in ("POSSIBLE", "CONFIRMED") else zone.enter_overlap
    candidates, overlaps = [], []
    person_ratio = 0.0
    for detection in detections:
        x1, y1, x2, y2 = detection.bbox_xyxy
        if detection.label == "person" and detection.confidence >= 0.35:
            ratio = polygon.intersection(box(x1, y1, x2, y2)).area / polygon.area
            person_ratio = max(person_ratio, min(1.0, max(0.0, ratio)))
        if detection.label not in zone.candidate_labels or detection.confidence < zone.min_confidence:
            continue
        strip = box(x1, y2 - zone.footprint_height_fraction * (y2 - y1), x2, y2)
        ratio = min(1.0, max(0.0, polygon.intersection(strip).area / strip.area))
        overlaps.append(ratio)
        if ratio + EPSILON >= threshold:
            candidates.append(Candidate(**detection.model_dump(), zone_overlap=ratio))
    candidates.sort(key=lambda c: (-c.zone_overlap, -c.confidence, c.label,
                                   c.track_id is None, c.track_id or "", *c.bbox_xyxy))
    classification = ("positive" if candidates else
                      "unknown" if person_ratio + EPSILON >= zone.person_occlusion_ratio else "negative")
    return SpatialResult(classification, candidates, overlaps, person_ratio)
