"""Run the clearly labeled synthetic Person 2 demo from any working directory."""
import json
from pathlib import Path
from foresmoke.reasoning.__main__ import main
from foresmoke.reasoning.fixtures import fixtures

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    output = root / "runs" / "fixture"
    output.mkdir(parents=True, exist_ok=True)
    source = output / "observations.jsonl"
    source.write_text("".join(json.dumps(p) + "\n" for p in fixtures()["blocked_then_clear"]), encoding="utf-8")
    print("SYNTHETIC ForeSmoke reasoning demo (no camera or detector).", flush=True)
    print("Expected: CONFIRMED at 5000 ms, RESOLVED at 9200 ms.", flush=True)
    status = main(["--input", str(source), "--zones", str(root / "tests/fixtures/zones.json"),
                   "--events", str(output / "events.jsonl"), "--snapshots", str(output / "states.jsonl"),
                   "--force"])
    if status == 0:
        print(f"Events and state snapshots saved in {output}", flush=True)
    raise SystemExit(status)

