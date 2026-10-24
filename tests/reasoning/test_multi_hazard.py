from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from foresmoke.annotate import update_zone_file
from foresmoke.reasoning import ReasoningEngine, load_config
from foresmoke.review.config import ReviewSettings
from foresmoke.review.evidence import EvidenceCapture, FrameSample
from foresmoke.shared.contracts import (
    FrameObservation,
    HazardEvent,
    ZoneConfig,
    ZoneState,
    monitoring_objective,
)

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "81234567-89ab-4cde-8fab-0123456789ab"
CHAIR_EXT = {
    "label": "chair",
    "confidence": 0.91,
    "bbox_xyxy": [0.62, 0.25, 0.78, 0.85],
    "track_id": "chair-ext",
}
CHAIR_EXIT = {
    "label": "chair",
    "confidence": 0.91,
    "bbox_xyxy": [0.12, 0.25, 0.28, 0.85],
    "track_id": "chair-exit",
}
PERSON_EXT = {
    "label": "person",
    "confidence": 0.95,
    "bbox_xyxy": [0.58, 0.2, 0.86, 0.95],
    "track_id": "person-1",
}


@pytest.fixture()
def multi_config():
    return load_config(ROOT / "configs" / "multi_hazard_demo.json")


def observation(t_ms: int, detections=None, *, seq: int | None = None, status="ok"):
    return {
        "schema_version": "1.0",
        "run_id": RUN_ID,
        "camera_id": "cam01",
        "view_id": "multi-hazard-synthetic-v1",
        "source_kind": "video",
        "seq": t_ms // 200 if seq is None else seq,
        "t_ms": t_ms,
        "tracker_epoch": 0,
        "status": status,
        "frame": {
            "index": t_ms * 30 // 1000,
            "width": 1280,
            "height": 720,
            "frame_ref": None,
        }
        if status == "ok"
        else None,
        "detections": list(detections or []) if status == "ok" else [],
        "error": "capture_failed" if status == "unavailable" else None,
    }


def run(config, packets):
    engine = ReasoningEngine(config, live_watchdog=False)
    events = []
    for packet in packets:
        events.extend(engine.process(packet, packet["seq"]))
    return engine, events


def test_legacy_exit_config_defaults_without_changes_to_input_shape():
    legacy = {
        "schema_version": "1.0",
        "camera_id": "cam01",
        "view_id": "legacy-view",
        "reference_frame": {"width": 1280, "height": 720},
        "max_observation_gap_ms": 1000,
        "live_stale_after_ms": 2000,
        "zones": [{
            "zone_id": "east_exit",
            "hazard_type": "blocked_exit",
            "polygon": [[0.1, 0.5], [0.4, 0.5], [0.4, 0.9], [0.1, 0.9]],
            "candidate_labels": ["chair"],
            "min_confidence": 0.35,
            "footprint_height_fraction": 0.2,
            "enter_overlap": 0.3,
            "exit_overlap": 0.15,
            "person_occlusion_ratio": 0.2,
            "confirm_ms": 5000,
            "candidate_clear_ms": 1000,
            "resolve_ms": 3000,
        }],
    }
    config = ZoneConfig.model_validate(legacy)
    assert config.assets == []
    assert config.zones[0].zone_type == "exit_access"
    assert config.zones[0].hazard_type == "blocked_exit"
    assert config.zones[0].name == "east_exit"


def test_extinguisher_configuration_and_asset_context_load(multi_config):
    zone = multi_config.zones[1]
    asset = multi_config.assets[0]
    assert zone.zone_type == "extinguisher_access"
    assert zone.hazard_type == "blocked_extinguisher_access"
    assert zone.protected_asset_id == asset.asset_id
    assert asset.asset_type == "fire_extinguisher"


@pytest.mark.parametrize("mutation", ["missing", "unknown", "camera", "view", "duplicate"])
def test_invalid_asset_associations_are_rejected(multi_config, mutation):
    data = multi_config.model_dump(mode="json")
    if mutation == "missing":
        data["zones"][1]["protected_asset_id"] = None
    elif mutation == "unknown":
        data["zones"][1]["protected_asset_id"] = "does_not_exist"
    elif mutation == "camera":
        data["assets"][0]["camera_id"] = "other"
    elif mutation == "view":
        data["assets"][0]["view_id"] = "other"
    else:
        data["assets"].append(deepcopy(data["assets"][0]))
    with pytest.raises(ValidationError):
        ZoneConfig.model_validate(data)


def test_contradictory_hazard_type_is_rejected(multi_config):
    data = multi_config.model_dump(mode="json")
    data["zones"][1]["hazard_type"] = "blocked_exit"
    with pytest.raises(ValidationError, match="hazard_type"):
        ZoneConfig.model_validate(data)


def test_optional_exit_asset_association_does_not_crash_outputs(multi_config):
    data = multi_config.model_dump(mode="json")
    data["zones"][0]["protected_asset_id"] = "extinguisher_lobby_01"
    config = ZoneConfig.model_validate(data)
    engine, _ = run(config, [observation(0, [])])
    exit_state = {state.zone_id: state for state in engine.snapshots()}["east_exit"]
    assert exit_state.protected_asset_id == "extinguisher_lobby_01"
    assert exit_state.hazard_type == "blocked_exit"


def test_chair_inside_extinguisher_zone_confirms_and_resolves_only_that_zone(multi_config):
    packets = [
        observation(t, [CHAIR_EXT] if t <= 6000 else [])
        for t in range(0, 10001, 200)
    ]
    engine, events = run(multi_config, packets)
    assert [(event.event_type, event.t_ms) for event in events] == [
        ("incident_confirmed", 5000),
        ("incident_resolved", 9200),
    ]
    assert {event.zone_id for event in events} == {"lobby_extinguisher_access"}
    assert all(event.hazard_type == "blocked_extinguisher_access" for event in events)
    assert all(event.protected_asset_id == "extinguisher_lobby_01" for event in events)
    assert all(event.protected_asset_name == "Lobby fire extinguisher" for event in events)
    assert events[0].incident_id == events[1].incident_id
    bad_event = events[0].model_dump(mode="json")
    bad_event["hazard_type"] = "blocked_exit"
    with pytest.raises(ValidationError, match="hazard_type"):
        HazardEvent.model_validate(bad_event)
    legacy_new_hazard = events[0].model_dump(mode="json")
    legacy_new_hazard["schema_version"] = "1.0"
    with pytest.raises(ValidationError, match="1.0 events"):
        HazardEvent.model_validate(legacy_new_hazard)
    states = {state.zone_id: state for state in engine.snapshots()}
    assert states["east_exit"].phase == "CLEAR"
    assert states["lobby_extinguisher_access"].phase == "RESOLVED"
    bad_state = states["lobby_extinguisher_access"].model_dump(mode="json")
    bad_state["hazard_type"] = "blocked_exit"
    with pytest.raises(ValidationError, match="hazard_type"):
        ZoneState.model_validate(bad_state)
    incomplete_state = states["lobby_extinguisher_access"].model_dump(mode="json")
    incomplete_state.pop("monitoring_objective")
    with pytest.raises(ValidationError, match="1.1 snapshot missing"):
        ZoneState.model_validate(incomplete_state)


def test_outside_and_brief_occupancy_do_not_confirm(multi_config):
    outside = {**CHAIR_EXT, "bbox_xyxy": [0.05, 0.1, 0.2, 0.4]}
    _, outside_events = run(
        multi_config, [observation(t, [outside]) for t in range(0, 7001, 200)]
    )
    _, brief_events = run(
        multi_config,
        [
            observation(t, [CHAIR_EXT] if t <= 2000 else [])
            for t in range(0, 5001, 200)
        ],
    )
    assert outside_events == []
    assert brief_events == []


def test_passerby_and_unknown_evidence_never_resolve_confirmed_incident(multi_config):
    packets = [observation(t, [CHAIR_EXT]) for t in range(0, 5001, 200)]
    packets += [observation(t, [PERSON_EXT]) for t in range(5200, 9001, 200)]
    engine, events = run(multi_config, packets)
    assert len(events) == 1 and events[0].event_type == "incident_confirmed"
    state = {item.zone_id: item for item in engine.snapshots()}["lobby_extinguisher_access"]
    assert state.phase == "CONFIRMED"
    assert state.observation_state == "unknown"


def test_missing_or_detected_extinguisher_never_creates_an_incident(multi_config):
    detected_asset = {
        "label": "fire_extinguisher",
        "confidence": 0.99,
        "bbox_xyxy": [0.62, 0.25, 0.78, 0.85],
        "track_id": "asset",
    }
    _, missing_events = run(
        multi_config, [observation(t, []) for t in range(0, 7001, 200)]
    )
    _, asset_events = run(
        multi_config, [observation(t, [detected_asset]) for t in range(0, 7001, 200)]
    )
    assert missing_events == []
    assert asset_events == []


def test_overlapping_object_can_create_independent_typed_incidents(multi_config):
    spanning = {
        "label": "chair",
        "confidence": 0.92,
        "bbox_xyxy": [0.2, 0.25, 0.7, 0.85],
        "track_id": "wide-chair",
    }
    engine, events = run(
        multi_config, [observation(t, [spanning]) for t in range(0, 5001, 200)]
    )
    confirmed = {event.zone_id: event for event in events}
    assert set(confirmed) == {"east_exit", "lobby_extinguisher_access"}
    assert confirmed["east_exit"].hazard_type == "blocked_exit"
    assert confirmed["east_exit"].protected_asset_id is None
    assert confirmed["lobby_extinguisher_access"].hazard_type == "blocked_extinguisher_access"
    assert confirmed["lobby_extinguisher_access"].protected_asset_id == "extinguisher_lobby_01"
    states = {state.zone_id: state for state in engine.snapshots()}
    assert all(state.phase == "CONFIRMED" for state in states.values())


def test_removing_one_obstruction_does_not_resolve_other_zone(multi_config):
    packets = [
        observation(t, [CHAIR_EXIT, CHAIR_EXT])
        for t in range(0, 5001, 200)
    ]
    packets += [
        observation(t, [CHAIR_EXIT])
        for t in range(5200, 8201, 200)
    ]
    engine, events = run(multi_config, packets)
    resolved = [event for event in events if event.event_type == "incident_resolved"]
    assert [event.zone_id for event in resolved] == ["lobby_extinguisher_access"]
    states = {state.zone_id: state for state in engine.snapshots()}
    assert states["east_exit"].phase == "CONFIRMED"
    assert states["lobby_extinguisher_access"].phase == "RESOLVED"


def test_snapshot_and_visual_handoff_expose_asset_purpose(multi_config):
    engine, _ = run(multi_config, [observation(0, [CHAIR_EXT])])
    state = {item.zone_id: item for item in engine.snapshots()}["lobby_extinguisher_access"]
    assert state.zone_type == "extinguisher_access"
    assert state.hazard_type == "blocked_extinguisher_access"
    assert state.protected_asset_name == "Lobby fire extinguisher"
    assert "Do not infer that the extinguisher is missing" in state.monitoring_objective
    assert state.remediation == (
        "Remove the obstruction from the marked access area for Lobby fire extinguisher."
    )
    assert "marked approach area" in monitoring_objective(
        "extinguisher_access", "Lobby fire extinguisher"
    )


def test_visual_review_manifest_contains_extinguisher_context(tmp_path, multi_config):
    np = pytest.importorskip("numpy")
    packet = FrameObservation.model_validate(observation(0, [CHAIR_EXT]))
    engine, _ = run(multi_config, [packet.model_dump(mode="json")])
    state = {item.zone_id: item for item in engine.snapshots()}[
        "lobby_extinguisher_access"
    ]
    bundle = EvidenceCapture(tmp_path, ReviewSettings(enabled=True)).create(
        samples=[FrameSample(packet, np.zeros((720, 1280, 3), dtype=np.uint8))],
        state=state,
        zone=multi_config.zones[1],
        protected_asset=multi_config.assets[0],
        trigger="candidate",
        incident_id=None,
    )
    assert bundle.manifest.zone_type == "extinguisher_access"
    assert bundle.manifest.hazard_type == "blocked_extinguisher_access"
    assert bundle.manifest.protected_asset.asset_id == "extinguisher_lobby_01"
    assert "Do not infer that the extinguisher is missing" in (
        bundle.manifest.monitoring_objective
    )


def test_annotation_appends_asset_zone_and_preserves_existing_configuration(tmp_path):
    path = tmp_path / "zones.json"
    source = json.loads((ROOT / "configs" / "multi_hazard_demo.json").read_text())
    source["assets"] = []
    source["zones"] = [source["zones"][0]]
    path.write_text(json.dumps(source))
    old_exit = deepcopy(source["zones"][0])
    polygon = [[0.58, 0.58], [0.9, 0.58], [0.9, 0.94], [0.58, 0.94]]
    result = update_zone_file(
        path,
        polygon,
        zone_id="lobby_extinguisher_access",
        zone_name="Lobby extinguisher access",
        zone_type="extinguisher_access",
        asset_id="extinguisher_lobby_01",
        asset_name="Lobby fire extinguisher",
        asset_bbox=[0.82, 0.2, 0.88, 0.48],
        reference_image_id="clear-frame-0",
        width=1280,
        height=720,
    )
    assert result["zones"][0] == old_exit
    assert result["zones"][1]["polygon"] == polygon
    assert result["zones"][1]["protected_asset_id"] == "extinguisher_lobby_01"
    assert result["assets"][0]["bbox_xyxy"] == [0.82, 0.2, 0.88, 0.48]
    assert result["zones"][0]["confirm_ms"] == 5000
    assert ZoneConfig.model_validate_json(path.read_text())
