import json
from pathlib import Path
import subprocess
import sys
import time
import pytest
from foresmoke.reasoning.fixtures import observation

ROOT = Path(__file__).resolve().parents[2]

def command(tmp_path, input_path):
    return [sys.executable, "-m", "foresmoke.reasoning", "--input", str(input_path),
            "--zones", str(ROOT / "tests/fixtures/zones.json"), "--events", str(tmp_path / "events.jsonl"),
            "--snapshots", str(tmp_path / "states.jsonl")]

def read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]

def test_cli_demo(tmp_path):
    process = subprocess.run(command(tmp_path, ROOT / "tests/fixtures/blocked_then_clear.jsonl"),
                             cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert process.stdout == ""
    assert [e["t_ms"] for e in read(tmp_path / "events.jsonl")] == [5000, 9200]
    assert read(tmp_path / "states.jsonl")[-1]["observation_reason"] == "source_ended"

@pytest.mark.parametrize("name", ["invalid_bbox", "unknown_schema", "timestamp_regression",
                                  "wrong_view", "wrong_aspect", "wrong_run"])
def test_cli_malformed_input_fails_with_line_number(tmp_path, name):
    process = subprocess.run(command(tmp_path, ROOT / f"tests/fixtures/{name}.jsonl"),
                             cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 2
    assert "line " in process.stderr
    assert read(tmp_path / "states.jsonl")[-1]["observation_reason"] == "invalid_input"

def test_parse_failure_after_confirmation_does_not_resolve(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text("".join(json.dumps(observation(t)) + "\n" for t in range(0, 5001, 200)) + "{broken\n")
    process = subprocess.run(command(tmp_path, source), cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 2 and "line 27" in process.stderr
    assert len(read(tmp_path / "events.jsonl")) == 1
    state = read(tmp_path / "states.jsonl")[-1]
    assert state["phase"] == "CONFIRMED" and state["observation_state"] == "unknown"

def test_outputs_cannot_overwrite_input(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps(observation(0)) + "\n")
    original = source.read_bytes()
    cmd = command(tmp_path, source)
    cmd[cmd.index("--events") + 1] = str(source)
    process = subprocess.run(cmd + ["--force"], cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 2 and source.read_bytes() == original

def test_existing_outputs_require_force(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("keep me")
    process = subprocess.run(command(tmp_path, ROOT / "tests/fixtures/blocked_then_clear.jsonl"),
                             cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 2 and path.read_text() == "keep me"

def test_stdin_stream_has_watchdog_and_retains_active_incident(tmp_path):
    process = subprocess.Popen(command(tmp_path, "-"), cwd=ROOT, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for t in range(0, 5001, 200):
            process.stdin.write(json.dumps(observation(t, source_kind="live")) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 12
        stale = False
        while time.monotonic() < deadline:
            if (tmp_path / "states.jsonl").exists():
                try:
                    stale = any(x["observation_reason"] == "stale_input" for x in read(tmp_path / "states.jsonl"))
                except json.JSONDecodeError:
                    pass
                if stale:
                    break
            time.sleep(0.05)
        assert stale, "live stdin stall did not trigger watchdog"
        process.stdin.close()
        process.stdin = None
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert stdout == ""
        events = read(tmp_path / "events.jsonl")
        assert len(events) == 1 and events[0]["event_type"] == "incident_confirmed"
        assert read(tmp_path / "states.jsonl")[-1]["phase"] == "CONFIRMED"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

def test_duplicate_eof_does_not_truncate_replay(tmp_path):
    packets = [observation(t) for t in range(0, 5001, 200)]
    packets.append(observation(5000, status="eof", frame=None, detections=[]))
    packets.extend(observation(t, detections=[]) for t in range(5200, 8201, 200))
    source = tmp_path / "input.jsonl"
    source.write_text("".join(json.dumps(p) + "\n" for p in packets))
    process = subprocess.run(command(tmp_path, source), cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert [e["t_ms"] for e in read(tmp_path / "events.jsonl")] == [5000, 8200]
    assert read(tmp_path / "states.jsonl")[-1]["observation_reason"] == "source_ended"

