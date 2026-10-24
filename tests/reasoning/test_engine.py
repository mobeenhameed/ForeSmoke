from copy import deepcopy
import json
from pathlib import Path
import pytest
from foresmoke.reasoning import ReasoningEngine
from foresmoke.reasoning.fixtures import CHAIR, PERSON, observation, fixtures, EXPECTED

ROOT = Path(__file__).resolve().parents[2]

def run(config, packets, arrival_scale=1):
    engine = ReasoningEngine(config)
    events = []
    for i, packet in enumerate(packets):
        events.extend(engine.process(packet, i * arrival_scale))
    return engine, events

@pytest.mark.parametrize("name", list(EXPECTED))
def test_shared_fixture_transitions(config, name):
    engine, events = run(config, fixtures()[name])
    assert [(e.event_type, e.t_ms) for e in events] == EXPECTED[name]
    if name == "failure_after_confirmation":
        state = engine.snapshots()[0]
        assert state.phase == "CONFIRMED"
        assert state.observation_state == "unknown"
    if name == "new_occurrence":
        assert events[0].incident_id == events[1].incident_id != events[2].incident_id

def test_events_exactly_match_brief_example(config):
    _, events = run(config, fixtures()["blocked_then_clear"])
    expected = json.loads((ROOT / "tests/fixtures/expected_events.json").read_text())
    assert [e.model_dump(mode="json") for e in events] == expected

def test_initial_state_has_no_observed_safety(config):
    state = ReasoningEngine(config).snapshots()[0]
    assert state.phase == "CLEAR" and state.observation_state == "unknown"
    assert state.t_ms is None and state.run_id is None

def test_possible_cancels_at_3200(config):
    engine, _ = run(config, [p for p in fixtures()["brief_obstruction"] if p["t_ms"] <= 3200])
    assert engine.snapshots()[0].phase == "CLEAR"

def test_first_negative_contributes_zero_clearance(config):
    packets = [observation(t) for t in range(0, 5001, 200)] + [observation(5200, detections=[])]
    engine, events = run(config, packets)
    state = engine.snapshots()[0]
    assert len(events) == 1 and state.negative_run_ms == 0 and state.phase == "CONFIRMED"

def test_positive_interrupts_clearance(config):
    packets = [observation(t) for t in range(0, 5001, 200)]
    packets += [observation(t, detections=[]) for t in range(5200, 8001, 200)]
    packets += [observation(8200)]
    engine, events = run(config, packets)
    assert len(events) == 1
    assert engine.snapshots()[0].negative_run_ms == 0

def test_unknown_resets_possible_evidence(config):
    packets = [observation(t) for t in range(0, 4001, 200)]
    packets += [observation(4200, status="unavailable", frame=None, detections=[], error="capture_failed")]
    packets += [observation(t) for t in range(4400, 9401, 200)]
    _, events = run(config, packets)
    assert len(events) == 1 and events[0].t_ms == 9400 and events[0].first_seen_ms == 4400

def test_occlusion_never_resolves_confirmed_incident(config):
    packets = [observation(t) for t in range(0, 5001, 200)]
    packets += [observation(t, detections=[PERSON]) for t in range(5200, 10001, 200)]
    engine, events = run(config, packets)
    assert len(events) == 1
    assert engine.snapshots()[0].phase == "CONFIRMED"
    assert engine.snapshots()[0].observation_reason == "occluded"

def test_gap_cannot_clear_an_incident(config):
    packets = [observation(t) for t in range(0, 5001, 200)] + [observation(15000, detections=[])]
    engine, events = run(config, packets)
    assert len(events) == 1 and engine.snapshots()[0].negative_run_ms == 0

def test_watchdog_and_duplicate_cannot_keep_live_feed_fresh(config):
    engine = ReasoningEngine(config)
    packets = [observation(t, source_kind="live") for t in range(0, 5001, 200)]
    for packet in packets:
        engine.process(packet, packet["t_ms"])
    previous = engine.snapshots()
    assert engine.process(packets[-1], 6500) == []
    assert engine.snapshots() == previous
    engine.tick(7000)
    state = engine.snapshots()[0]
    assert state.phase == "CONFIRMED" and state.observation_reason == "stale_input"
    assert state.positive_evidence_ms == state.negative_run_ms == 0
    events = []
    for t in range(10000, 13001, 200):
        events.extend(engine.process(observation(t, source_kind="live", detections=[]), t))
    assert len(events) == 1 and events[0].t_ms == 13000

def test_late_recovery_resets_without_explicit_tick(config):
    engine = ReasoningEngine(config)
    for t in range(0, 4001, 200):
        engine.process(observation(t, source_kind="live"), t)
    assert engine.process(observation(4200, source_kind="live"), 9000) == []
    assert engine.snapshots()[0].positive_evidence_ms == 0

def test_recorded_live_stream_can_disable_watchdog(config):
    engine = ReasoningEngine(config, live_watchdog=False)
    events = []
    for i, packet in enumerate(fixtures()["blocked_then_clear"]):
        packet["source_kind"] = "live"
        engine.tick(i * 10000)
        events += engine.process(packet, i * 10000)
    assert [e.t_ms for e in events] == [5000, 9200]

def test_file_replay_speed_does_not_change_events(config):
    _, fast = run(config, fixtures()["blocked_then_clear"], 1)
    _, slow = run(config, fixtures()["blocked_then_clear"], 10000)
    assert [e.model_dump_json() for e in fast] == [e.model_dump_json() for e in slow]

@pytest.mark.parametrize("change", [
    {"camera_id": "other"}, {"view_id": "other"},
    {"run_id": "11234567-89ab-4cde-8fab-0123456789ab"}, {"source_kind": "live"},
    {"t_ms": 0}, {"frame": {"index": 12, "width": 640, "height": 480, "frame_ref": None}},
    {"schema_version": "2.0"},
])
def test_invalid_input_resets_continuity_without_accepting_it(config, change):
    engine = ReasoningEngine(config)
    engine.process(observation(0), 0)
    engine.process(observation(200), 1)
    bad = observation(400)
    bad.update(change)
    with pytest.raises(ValueError):
        engine.process(bad, 2)
    assert engine.snapshots()[0].observation_reason == "invalid_input"
    assert engine.snapshots()[0].t_ms == 200
    engine.process(observation(600), 3)
    assert engine.snapshots()[0].positive_evidence_ms == 0

def test_epoch_reset_and_null_ids_keep_occupancy(config):
    packets = [observation(t, tracker_epoch=0 if t < 2600 else 1,
                           detections=[{**CHAIR, "track_id": None if t < 2600 else "7"}])
               for t in range(0, 5001, 200)]
    engine, events = run(config, packets)
    assert events[0].t_ms == 5000 and events[0].evidence.tracker_epoch == 1
    with pytest.raises(ValueError):
        engine.process(observation(5200, tracker_epoch=0), 100)

def test_close_input_preserves_active_incident(config):
    engine, events = run(config, [observation(t) for t in range(0, 5001, 200)])
    engine.close_input()
    state = engine.snapshots()[0]
    assert state.incident_id == events[0].incident_id and state.phase == "CONFIRMED"
    assert state.observation_reason == "source_ended"
    with pytest.raises(ValueError):
        engine.process(observation(5200), 99)

def test_snapshot_and_config_mutation_cannot_change_engine(config):
    engine = ReasoningEngine(config)
    config.zones[0].confirm_ms = 1
    engine.process(observation(0), 0)
    state = engine.snapshots()[0]
    state.candidates[0].confidence = 0
    assert engine.snapshots()[0].candidates[0].confidence == 0.91
    assert engine.process(observation(200), 1) == []

def test_two_zones_are_independent(config):
    data = config.model_dump()
    other = deepcopy(data["zones"][0])
    other["zone_id"] = "west_exit"
    other["polygon"] = [[0.01, 0.01], [0.2, 0.01], [0.2, 0.2], [0.01, 0.2]]
    data["zones"].append(other)
    from foresmoke.shared.contracts import ZoneConfig
    engine, events = run(ZoneConfig.model_validate(data), fixtures()["blocked_then_clear"])
    assert {e.zone_id for e in events} == {"east_exit"}
    assert engine.snapshots()[1].phase == "CLEAR"

def test_backward_consumer_clock_rejected(config):
    engine = ReasoningEngine(config)
    engine.process(observation(0), 100)
    with pytest.raises(ValueError):
        engine.tick(99)

def test_single_image_cannot_accumulate_time(config):
    engine = ReasoningEngine(config)
    assert engine.process(observation(0, source_kind="image"), 0) == []
    with pytest.raises(ValueError, match="image input"):
        engine.process(observation(200, source_kind="image"), 1)
    assert engine.snapshots()[0].phase == "POSSIBLE"
    assert engine.snapshots()[0].positive_evidence_ms == 0

def test_duplicate_eof_does_not_close_engine(config):
    engine = ReasoningEngine(config)
    engine.process(observation(0), 0)
    duplicate = observation(0, status="eof", frame=None, detections=[])
    assert engine.process(duplicate, 1) == []
    assert not engine.input_ended
    engine.process(observation(200), 2)
    assert engine.snapshots()[0].positive_evidence_ms == 200
