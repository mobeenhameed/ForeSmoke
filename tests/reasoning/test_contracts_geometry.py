from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from foresmoke.shared.contracts import FrameObservation, Detection, ZoneConfig
from foresmoke.reasoning import ReasoningEngine
from foresmoke.reasoning.geometry import classify
from foresmoke.reasoning.fixtures import CHAIR, PERSON, observation, fixtures, EXPECTED

ROOT = Path(__file__).resolve().parents[2]

def test_populated_empty_unavailable_are_distinct():
    assert FrameObservation.model_validate(observation(0)).detections
    assert FrameObservation.model_validate(observation(0, detections=[])).status == "ok"
    packet = observation(0, status="unavailable", detections=[], frame=None, error="capture_failed")
    assert FrameObservation.model_validate(packet).frame is None
    with pytest.raises(ValidationError):
        FrameObservation.model_validate(observation(0, status="unavailable"))

@pytest.mark.parametrize("changes", [
    {"schema_version": "2.0"}, {"seq": "1"}, {"t_ms": True}, {"unexpected": 1},
    {"run_id": "bad"}, {"error": "failure"},
    {"detections": [{**CHAIR, "confidence": float("nan")}]},
    {"detections": [{**CHAIR, "confidence": float("inf")}]},
    {"detections": [{**CHAIR, "bbox_xyxy": [0.5, 0.2, 0.4, 0.8]}]},
    {"detections": [{**CHAIR, "bbox_xyxy": [-0.1, 0.2, 0.4, 0.8]}]},
    {"detections": [CHAIR, CHAIR]},
])
def test_reject_bad_observations(changes):
    with pytest.raises(ValidationError):
        FrameObservation.model_validate({**observation(0), **changes})

def test_untracked_objects_are_preserved():
    data = [{**CHAIR, "track_id": None}, {**CHAIR, "track_id": None}]
    assert len(FrameObservation.model_validate(observation(0, detections=data)).detections) == 2

@pytest.mark.parametrize("polygon", [
    [[0.3, 0.5], [0.8, 0.9], [0.8, 0.5], [0.3, 0.9]],
    [[0.3, 0.5], [0.4, 0.5], [0.5, 0.5]],
    [[0.3, 0.5], [0.8, 0.5], [0.8, 0.9], [0.3, 0.5]],
    [[-0.1, 0.5], [0.8, 0.5], [0.8, 0.9]],
])
def test_bad_polygon_is_rejected(config, polygon):
    data = config.model_dump()
    data["zones"][0]["polygon"] = polygon
    with pytest.raises(ValueError):
        ReasoningEngine(ZoneConfig.model_validate(data))

@pytest.mark.parametrize("changes", [
    {"enter_overlap": 0}, {"exit_overlap": 0.4}, {"confirm_ms": 0},
    {"candidate_labels": ["chair", "chair"]}, {"candidate_labels": ["person"]},
])
def test_bad_zone_rules(config, changes):
    data = config.model_dump()
    data["zones"][0].update(changes)
    with pytest.raises(ValueError):
        ZoneConfig.model_validate(data)

def test_duplicate_zone_id(config):
    data = config.model_dump()
    data["zones"].append(deepcopy(data["zones"][0]))
    with pytest.raises(ValueError):
        ZoneConfig.model_validate(data)

def result(config, detection, phase="CLEAR"):
    return classify([Detection.model_validate(detection)], config.zones[0], phase)

def test_inside_strip(config):
    actual = result(config, CHAIR)
    assert actual.classification == "positive"
    assert actual.candidates[0].zone_overlap == pytest.approx(1)

def test_upper_box_overlap_is_not_footprint_overlap(config):
    actual = result(config, {**CHAIR, "bbox_xyxy": [0.38, 0.8, 0.56, 1.0]})
    assert actual.classification == "negative"
    assert actual.overlaps == [0]

def test_exact_enter_threshold(config):
    actual = result(config, {**CHAIR, "bbox_xyxy": [0.16, 0.5, 0.36, 0.85]})
    assert actual.classification == "positive"
    assert actual.candidates[0].zone_overlap == pytest.approx(0.3)

def test_phase_hysteresis(config):
    det = {**CHAIR, "bbox_xyxy": [0.14, 0.5, 0.34, 0.85]}
    assert result(config, det).classification == "negative"
    assert result(config, det, "CONFIRMED").classification == "positive"
    assert result(config, det, "POSSIBLE").classification == "positive"
    assert result(config, det, "RESOLVED").classification == "negative"

def test_person_occlusion_and_visible_object_priority(config):
    assert result(config, PERSON).classification == "unknown"
    actual = classify([Detection.model_validate(d) for d in [PERSON, CHAIR]], config.zones[0], "CLEAR")
    assert actual.classification == "positive"
    assert [c.label for c in actual.candidates] == ["chair"]

def test_confidence_boundary_and_ignored_classes(config):
    assert result(config, {**CHAIR, "confidence": 0.34}).classification == "negative"
    assert result(config, {**CHAIR, "confidence": 0.35}).classification == "positive"
    assert result(config, {**CHAIR, "label": "dog"}).classification == "negative"

def test_boundary_contact_has_zero_area(config):
    assert result(config, {**CHAIR, "bbox_xyxy": [0.1, 0.5, 0.3, 0.85]}).classification == "negative"

def test_concave_polygon_and_reverse_winding(config):
    zone = config.zones[0].model_copy(deep=True)
    zone.polygon = [[0.3, 0.55], [0.8, 0.55], [0.8, 0.7], [0.5, 0.7], [0.5, 0.95], [0.3, 0.95]]
    det = Detection.model_validate({**CHAIR, "bbox_xyxy": [0.6, 0.6, 0.7, 0.9]})
    assert classify([det], zone, "CLEAR").classification == "negative"
    zone.polygon.reverse()
    assert classify([det], zone, "CLEAR").classification == "negative"

def test_candidate_order_is_deterministic(config):
    a = Detection.model_validate({**CHAIR, "track_id": "a"})
    b = Detection.model_validate({**CHAIR, "track_id": None})
    assert classify([b, a], config.zones[0], "CLEAR").candidates == classify([a, b], config.zones[0], "CLEAR").candidates

def test_all_outputs_validate_against_original_brief_schema(config):
    schema = json.loads((ROOT / "schemas/foresmoke-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(config.model_dump(mode="json"))
    for name in EXPECTED:
        engine = ReasoningEngine(config)
        for packet in fixtures()[name]:
            validator.validate(packet)
            for event in engine.process(packet, packet["seq"]):
                validator.validate(event.model_dump(mode="json"))
            for state in engine.snapshots():
                validator.validate(state.model_dump(mode="json"))

def test_reasoning_import_does_not_load_detector_dependencies():
    process = subprocess.run([sys.executable, "-c",
        "import sys; import foresmoke.reasoning; assert not any(x in sys.modules for x in ('torch','ultralytics','cv2'))"],
        cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr

def test_shared_import_does_not_load_geometry():
    process = subprocess.run([sys.executable, "-c",
        "import sys; import foresmoke.shared.contracts; assert 'shapely' not in sys.modules"],
        cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr

