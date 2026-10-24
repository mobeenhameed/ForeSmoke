"""Seed a pre-analyzed sample run so the viewer works before any analysis is started.

The run replays the repository's existing SYNTHETIC fixture ``blocked_then_clear`` through
the real reasoning CLI (``python -m foresmoke.reasoning``). Nothing here decides incident
state; the engine produces states.jsonl and events.jsonl (CONFIRMED at 5000 ms, RESOLVED at
9200 ms). The fixture has no source video, so the viewer shows it without frames.

Usage: python -m foresmoke.webapp.sample [--force]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

from foresmoke.reasoning.fixtures import fixtures

from .store import Settings, now_iso, write_json

SAMPLE_RUN_ID = "exit-test-01"

# The synthetic calibration described in docs/CALIBRATION.md, matching the fixture packets
# (camera cam01, view exit-view-v1, 1280x720). It is not a real exit.
SYNTHETIC_ZONES = {
    "schema_version": "1.0",
    "camera_id": "cam01",
    "view_id": "exit-view-v1",
    "reference_frame": {"width": 1280, "height": 720},
    "max_observation_gap_ms": 1000,
    "live_stale_after_ms": 2000,
    "zones": [{
        "zone_id": "east_exit", "hazard_type": "blocked_exit",
        "polygon": [[0.30, 0.55], [0.80, 0.55], [0.80, 0.95], [0.30, 0.95]],
        "candidate_labels": ["chair", "backpack", "suitcase"], "min_confidence": 0.35,
        "footprint_height_fraction": 0.20, "enter_overlap": 0.30, "exit_overlap": 0.15,
        "person_occlusion_ratio": 0.20, "confirm_ms": 5000, "candidate_clear_ms": 1000,
        "resolve_ms": 3000,
    }],
}


def seed(settings: Settings | None = None, *, force: bool = False) -> bool:
    """Create runs/exit-test-01 if missing. Returns True when a run was written."""
    settings = settings or Settings()
    settings.ensure()
    run_dir = settings.runs_dir / SAMPLE_RUN_ID
    meta_dir = settings.run_meta_dir / SAMPLE_RUN_ID
    if (run_dir / "events.jsonl").exists() and (meta_dir / "meta.json").exists() and not force:
        return False
    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(meta_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    meta_dir.mkdir(parents=True)
    packets = fixtures()["blocked_then_clear"]
    observations = run_dir / "observations.jsonl"
    observations.write_text("".join(json.dumps(p, separators=(",", ":")) + "\n" for p in packets),
                            encoding="utf-8")
    zones = meta_dir / "zones.json"
    write_json(zones, SYNTHETIC_ZONES)
    command = [sys.executable, "-m", "foresmoke.reasoning", "--input", str(observations),
               "--zones", str(zones), "--events", str(run_dir / "events.jsonl"),
               "--snapshots", str(run_dir / "states.jsonl")]
    result = subprocess.run(command, cwd=settings.repo_root, capture_output=True, text=True)
    (meta_dir / "process.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    ok_packets = [p for p in packets if p["status"] == "ok"]
    write_json(meta_dir / "meta.json", {
        "run_id": SAMPLE_RUN_ID, "source": "sample",
        "state": "done" if result.returncode == 0 else "failed",
        "error": None if result.returncode == 0 else "Sample replay failed; see process.log",
        "returncode": result.returncode, "created_at": now_iso(), "finished_at": now_iso(),
        "video_id": None, "video_name": "Synthetic fixture (no video)",
        "duration_s": ok_packets[-1]["t_ms"] / 1000, "fps": None,
        "width": SYNTHETIC_ZONES["reference_frame"]["width"],
        "height": SYNTHETIC_ZONES["reference_frame"]["height"],
        "zone_config_id": None, "zone_name": "Synthetic east_exit", "target_hz": 5.0,
        "expected_observations": len(packets), "command": command,
    })
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="rebuild the sample run")
    args = parser.parse_args(argv)
    wrote = seed(force=args.force)
    print(f"Sample run {'written' if wrote else 'already present'}: runs/{SAMPLE_RUN_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
