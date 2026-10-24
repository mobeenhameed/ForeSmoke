"""Webapp orchestration tests. Incident logic itself is covered by the engine's own suite."""
from __future__ import annotations

import json
from pathlib import Path
import stat
import sys
import time

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from foresmoke.webapp import zones as zone_mod  # noqa: E402
from foresmoke.webapp.probe import ProbeError, probe_video  # noqa: E402
from foresmoke.webapp.runner import RunManager, build_command  # noqa: E402
from foresmoke.webapp.sample import SAMPLE_RUN_ID, seed  # noqa: E402
from foresmoke.webapp.server import create_app  # noqa: E402
from foresmoke.webapp.store import Settings  # noqa: E402

SQUARE = [[0.2, 0.5], [0.8, 0.5], [0.8, 0.9], [0.2, 0.9]]


def make_clip(path: Path, frames=20, size=(64, 48), fps=10.0) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(frames):
        writer.write(np.full((size[1], size[0], 3), (i * 10) % 255, dtype=np.uint8))
    writer.release()
    return path


@pytest.fixture()
def settings(tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    make_clip(samples / "clip.mp4")
    bundled = tmp_path / "zones.json"
    bundled.write_text(json.dumps(zone_mod.build_zone_config(
        camera_id="cam01", view_id="bundled-v1", width=64, height=48, zone_id="east_exit", polygon=SQUARE)))
    s = Settings(data_dir=tmp_path / "data", runs_dir=tmp_path / "runs", sample_videos_dir=samples,
                 bundled_zones=bundled)
    s.ensure()
    return s


# -- probing -------------------------------------------------------------------------------
def test_probe_reports_duration_dimensions_fps(tmp_path):
    info = probe_video(make_clip(tmp_path / "a.mp4", frames=20, size=(64, 48), fps=10.0))
    assert (info["width"], info["height"]) == (64, 48)
    assert info["fps"] == pytest.approx(10.0)
    assert info["duration_s"] == pytest.approx(2.0, abs=0.11)


def test_probe_rejects_non_video(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(ProbeError):
        probe_video(bad)


# -- zone configs: the engine's validator is the gate -------------------------------------
def test_valid_zone_matches_schema_and_reference_frame():
    config = zone_mod.validate_zone_config(zone_mod.build_zone_config(
        camera_id="cam01", view_id="v1", width=464, height=832, zone_id="east_exit", polygon=SQUARE))
    assert config["reference_frame"] == {"width": 464, "height": 832}
    zone = config["zones"][0]
    assert zone["candidate_labels"] == ["chair", "backpack", "suitcase"]
    assert (zone["confirm_ms"], zone["resolve_ms"], zone["min_confidence"]) == (5000, 3000, 0.35)
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).parents[2] / "schemas/foresmoke-v1.schema.json").read_text())
    jsonschema.validate(config, {**schema, "oneOf": [{"$ref": "#/$defs/ZoneConfig"}]})


@pytest.mark.parametrize("polygon", [
    [[0.2, 0.2], [0.8, 0.8], [0.8, 0.2], [0.2, 0.8]],     # bow-tie (self-intersecting)
    [[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]],                 # collinear: zero area
    [[0.1, 0.1], [1.2, 0.1], [0.5, 0.9]],                 # outside the frame
    [[0.1, 0.1], [0.9, 0.1]],                             # fewer than three vertices
])
def test_invalid_polygons_are_rejected(polygon):
    with pytest.raises(zone_mod.ZoneError):
        zone_mod.validate_zone_config(zone_mod.build_zone_config(
            camera_id="cam01", view_id="v1", width=464, height=832, zone_id="z", polygon=polygon))


def test_person_label_rejected():
    with pytest.raises(zone_mod.ZoneError):
        zone_mod.validate_zone_config(zone_mod.build_zone_config(
            camera_id="cam01", view_id="v1", width=10, height=10, zone_id="z", polygon=SQUARE,
            params={"candidate_labels": ["person"]}))


# -- runner ---------------------------------------------------------------------------------
def test_command_is_the_documented_cli():
    cmd = build_command(python="py", source=Path("v.mp4"), source_fps=60.0, zones=Path("z.json"),
                        run_dir=Path("runs/r1"), target_hz=5.0)
    assert cmd == ["py", "-m", "foresmoke.demo", "--source", "v.mp4", "--source-kind", "video",
                   "--source-fps", "60", "--zones", "z.json", "--run-dir", "runs/r1", "--target-hz", "5"]


def fake_cli(tmp_path: Path, *, lines: int, code: int, delay: float = 0.02) -> str:
    """A stand-in interpreter that behaves like `python -m foresmoke.demo` without a model."""
    script = tmp_path / "fake_python"
    script.write_text(f"""#!{sys.executable}
import sys, time, pathlib
args = sys.argv[1:]
run = pathlib.Path(args[args.index('--run-dir') + 1]); run.mkdir(parents=True, exist_ok=True)
for name in ('events.jsonl', 'states.jsonl'): (run / name).write_text('')
with open(run / 'observations.jsonl', 'w') as f:
    for i in range({lines}):
        f.write('{{}}\\n'); f.flush(); time.sleep({delay})
sys.exit({code})
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def wait_terminal(manager, run_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = manager.status(run_id)
        if status["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def _inputs(settings):
    from foresmoke.webapp.videos import VideoStore
    video = VideoStore(settings).list()[0]
    zone = zone_mod.ZoneStore(settings).list()[0]
    return video, zone


def test_run_done_on_zero_exit_with_progress(settings, tmp_path):
    manager = RunManager(settings, python=fake_cli(tmp_path, lines=10, code=0))
    video, zone = _inputs(settings)
    meta = manager.start(video=video, zone=zone)
    assert meta["expected_observations"] == 10            # 2.0 s x 5 Hz
    status = wait_terminal(manager, meta["run_id"])
    assert status["state"] == "done" and status["progress"] == 1.0
    assert status["processed_observations"] == 10


def test_run_failed_on_nonzero_exit(settings, tmp_path):
    manager = RunManager(settings, python=fake_cli(tmp_path, lines=3, code=2))
    video, zone = _inputs(settings)
    status = wait_terminal(manager, manager.start(video=video, zone=zone)["run_id"])
    assert status["state"] == "failed" and status["returncode"] == 2
    assert status["progress"] < 1.0


def test_cancel_terminates_and_each_run_gets_fresh_dir(settings, tmp_path):
    manager = RunManager(settings, python=fake_cli(tmp_path, lines=500, code=0, delay=0.05))
    video, zone = _inputs(settings)
    first = manager.start(video=video, zone=zone)
    second_dir_before = set(p.name for p in settings.runs_dir.iterdir()) if settings.runs_dir.exists() else set()
    time.sleep(0.3)
    status = manager.cancel(first["run_id"])
    assert status["state"] == "cancelled"
    assert wait_terminal(manager, first["run_id"])["state"] == "cancelled"
    second = manager.start(video=video, zone=zone)
    assert second["run_id"] != first["run_id"] and second["run_id"] not in second_dir_before
    manager.cancel(second["run_id"])


# -- HTTP API -------------------------------------------------------------------------------
def test_api_flow_upload_zone_and_serve_jsonl_unaltered(settings, tmp_path):
    with TestClient(create_app(settings, seed_sample=False)) as client:
        clip = make_clip(tmp_path / "up.mp4", frames=30, size=(80, 60), fps=15.0)
        with clip.open("rb") as handle:
            r = client.post("/api/videos", files={"file": ("my clip.mp4", handle, "video/mp4")})
        assert r.status_code == 200, r.text
        video = r.json()
        assert (video["width"], video["height"]) == (80, 60) and video["fps"] == pytest.approx(15.0)
        assert "path" not in video

        bad = client.post("/api/zones", json={"video_id": video["video_id"], "name": "east exit",
                                              "polygon": [[0.2, 0.2], [0.8, 0.8], [0.8, 0.2], [0.2, 0.8]]})
        assert bad.status_code == 422

        ok = client.post("/api/zones", json={"video_id": video["video_id"], "name": "east exit",
                                             "camera_name": "Lobby cam", "view_name": "north door",
                                             "polygon": SQUARE, "frame_t_ms": 1200})
        assert ok.status_code == 200, ok.text
        saved = ok.json()
        cfg = saved["config"]
        assert cfg["reference_frame"] == {"width": 80, "height": 60}
        assert cfg["camera_id"] == "Lobby-cam" and cfg["view_id"].startswith("north-door-")
        assert cfg["zones"][0]["zone_id"] == "east-exit"

        # The bundled 64x48 zone cannot be run against the 80x60 upload.
        mismatch = client.post("/api/runs", json={"video_id": video["video_id"],
                                                  "zone_config_id": zone_mod.BUNDLED_ID})
        assert mismatch.status_code == 409

        # Served JSONL is byte-identical to what the engine wrote.
        run_dir = settings.runs_dir / "external-1"
        run_dir.mkdir()
        raw = b'{"zone_id":"z","phase":"CLEAR","t_ms":0}\n{"zone_id":"z","phase":"POSSIBLE","t_ms":200}\n'
        (run_dir / "states.jsonl").write_bytes(raw)
        assert client.get("/api/runs/external-1/states").content == raw
        assert any(r["run_id"] == "external-1" for r in client.get("/api/runs").json())
        assert client.get("/api/runs/..%2Fetc/states").status_code in (400, 404)


def test_video_file_supports_range_requests(settings):
    with TestClient(create_app(settings, seed_sample=False)) as client:
        video_id = client.get("/api/videos").json()[0]["video_id"]
        r = client.get(f"/api/videos/{video_id}/file", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.content) == 100


def test_sample_run_has_confirmed_and_resolved_incident(settings):
    assert seed(settings)
    events = [json.loads(l) for l in (settings.runs_dir / SAMPLE_RUN_ID / "events.jsonl").read_text().splitlines()]
    assert [(e["event_type"], e["t_ms"]) for e in events] == [("incident_confirmed", 5000), ("incident_resolved", 9200)]
    assert events[0]["incident_id"] == events[1]["incident_id"]
    assert not seed(settings)                                   # idempotent
