"""Generate SYNTHETIC contract fixtures; no camera/model inference is represented."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path

RUN_ID = "01234567-89ab-4cde-8fab-0123456789ab"
CHAIR = {"label": "chair", "confidence": 0.91, "bbox_xyxy": [0.38, 0.28, 0.56, 0.84], "track_id": "7"}
PERSON = {"label": "person", "confidence": 0.95, "bbox_xyxy": [0.3, 0.25, 0.7, 0.95], "track_id": "person-1"}

def observation(t_ms: int, *, seq: int | None = None, detections=None, source_kind="video", **changes):
    packet = {
        "schema_version": "1.0", "run_id": RUN_ID, "camera_id": "cam01", "view_id": "exit-view-v1",
        "source_kind": source_kind, "seq": t_ms // 200 if seq is None else seq, "t_ms": t_ms,
        "tracker_epoch": 0, "status": "ok",
        "frame": {"index": t_ms * 30 // 1000, "width": 1280, "height": 720, "frame_ref": None},
        "detections": deepcopy([CHAIR] if detections is None else detections), "error": None,
    }
    packet.update(changes)
    return packet

def end(records):
    records = deepcopy(records)
    last = records[-1]
    records.append({**last, "seq": last["seq"] + 1, "status": "eof", "frame": None,
                    "detections": [], "error": None})
    return records

def fixtures() -> dict[str, list[dict]]:
    base = [observation(t, detections=[CHAIR] if t <= 6000 else []) for t in range(0, 10001, 200)]
    result = {"blocked_then_clear": end(base)}
    result["brief_obstruction"] = end([observation(t, detections=[CHAIR] if t <= 2000 else [])
                                        for t in range(0, 5001, 200)])
    result["walking_person"] = end([observation(t, detections=[PERSON]) for t in range(0, 7001, 200)])
    outside = {**CHAIR, "bbox_xyxy": [0.38, 0.8, 0.56, 1.0]}
    result["outside_zone"] = end([observation(t, detections=[outside])
                                  for t in range(0, 7001, 200)])
    result["id_switch"] = end([observation(t, detections=[{**CHAIR, "track_id": "7" if t < 2600 else "99"}])
                               for t in range(0, 6001, 200)])
    result["brief_detector_miss"] = end([observation(t, detections=[] if t == 2000 else [CHAIR])
                                         for t in range(0, 6001, 200)])
    failure = [observation(t) for t in range(0, 5001, 200)]
    failure.append(observation(5200, status="unavailable", frame=None, detections=[], error="capture_failed"))
    result["failure_after_confirmation"] = end(failure)
    result["recovery"] = end(failure + [observation(t, detections=[]) for t in range(10000, 13001, 200)])
    result["large_gap"] = end([observation(0), observation(10000)])
    second = {**CHAIR, "label": "backpack", "track_id": "8"}
    result["two_objects"] = end([observation(t, detections=[CHAIR, second] if t <= 6000 else [second])
                                 for t in range(0, 10001, 200)])
    result["new_occurrence"] = end(base + [observation(t) for t in range(10200, 16001, 200)])
    result["low_confidence"] = end([observation(t, detections=[{**CHAIR, "confidence": 0.34}])
                                    for t in range(0, 6001, 200)])
    result["duplicate_seq"] = end(base[:11] + [deepcopy(base[10])] + base[11:])
    # Deliberately malformed streams must fail with a line-number diagnostic.
    result["invalid_bbox"] = [observation(0, detections=[{**CHAIR, "bbox_xyxy": [0.5, 0.2, 0.4, 0.8]}])]
    result["unknown_schema"] = [observation(0, schema_version="2.0")]
    result["timestamp_regression"] = [observation(0), observation(400), observation(200, seq=3)]
    result["wrong_view"] = [observation(0, view_id="uncalibrated")]
    result["wrong_aspect"] = [observation(0, frame={"index": 0, "width": 640, "height": 480, "frame_ref": None})]
    result["wrong_run"] = [observation(0), observation(200, run_id="11234567-89ab-4cde-8fab-0123456789ab")]
    return result

EXPECTED = {
    "blocked_then_clear": [("incident_confirmed", 5000), ("incident_resolved", 9200)],
    "brief_obstruction": [], "walking_person": [], "outside_zone": [],
    "id_switch": [("incident_confirmed", 5000)],
    "brief_detector_miss": [("incident_confirmed", 5400)],
    "failure_after_confirmation": [("incident_confirmed", 5000)],
    "recovery": [("incident_confirmed", 5000), ("incident_resolved", 13000)],
    "large_gap": [], "two_objects": [("incident_confirmed", 5000)],
    "new_occurrence": [("incident_confirmed", 5000), ("incident_resolved", 9200),
                       ("incident_confirmed", 15200)],
    "low_confidence": [],
    "duplicate_seq": [("incident_confirmed", 5000), ("incident_resolved", 9200)],
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="tests/fixtures")
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    for name, packets in fixtures().items():
        (directory / f"{name}.jsonl").write_text(
            "".join(json.dumps(p, separators=(",", ":"), allow_nan=False) + "\n" for p in packets),
            encoding="utf-8")
    (directory / "expected_transitions.json").write_text(json.dumps(EXPECTED, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(fixtures())} synthetic fixtures to {directory}")

if __name__ == "__main__":
    main()

