"""Run a synthetic extinguisher-access incident while the exit zone stays clear."""
from __future__ import annotations

import json
from pathlib import Path

from foresmoke.reasoning.__main__ import main

RUN_ID = "71234567-89ab-4cde-8fab-0123456789ab"
CHAIR = {
    "label": "chair",
    "confidence": 0.91,
    "bbox_xyxy": [0.62, 0.25, 0.78, 0.85],
    "track_id": "chair-1",
}


def observations() -> list[dict]:
    records = []
    for seq, t_ms in enumerate(range(0, 10001, 200)):
        records.append({
            "schema_version": "1.0",
            "run_id": RUN_ID,
            "camera_id": "cam01",
            "view_id": "multi-hazard-synthetic-v1",
            "source_kind": "video",
            "seq": seq,
            "t_ms": t_ms,
            "tracker_epoch": 0,
            "status": "ok",
            "frame": {
                "index": t_ms * 30 // 1000,
                "width": 1280,
                "height": 720,
                "frame_ref": None,
            },
            "detections": [CHAIR] if t_ms <= 6000 else [],
            "error": None,
        })
    records.append({
        **records[-1],
        "seq": records[-1]["seq"] + 1,
        "status": "eof",
        "frame": None,
        "detections": [],
    })
    return records


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    output = root / "runs" / "multi-hazard-synthetic"
    output.mkdir(parents=True, exist_ok=True)
    source = output / "observations.jsonl"
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in observations()),
        encoding="utf-8",
    )
    print("SYNTHETIC multi-hazard demo (no camera, detector, or physical calibration).")
    print("Expected: extinguisher access CONFIRMED at 5000 ms and RESOLVED at 9200 ms.")
    print("Expected: east exit remains CLEAR and emits no event.")
    status = main([
        "--input", str(source),
        "--zones", str(root / "configs" / "multi_hazard_demo.json"),
        "--events", str(output / "events.jsonl"),
        "--snapshots", str(output / "states.jsonl"),
        "--force",
    ])
    raise SystemExit(status)
