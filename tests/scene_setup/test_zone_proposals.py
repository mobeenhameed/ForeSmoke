from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from foresmoke.review.kiro_cli import KiroReviewError
from foresmoke.scene_setup.config import SetupSettings
from foresmoke.scene_setup.contracts import validate_model_response
from foresmoke.scene_setup.evidence import SetupEvidenceCapture, image_token_count, submitted_size
from foresmoke.scene_setup.kiro import SetupProviderResult
from foresmoke.scene_setup.service import ProposalQueueFull, ZoneProposalService
from foresmoke.webapp.server import create_app
from foresmoke.webapp.store import Settings


def payload(*, zone_type="exit_access", polygon=None, box=None, status="proposals_available"):
    if polygon is None:
        polygon = [[20.0, 60.0], [80.0, 60.0], [90.0, 95.0], [10.0, 95.0]]
    if box is None:
        box = [20.0, 10.0, 80.0, 60.0]
    return {
        "scene_status": status,
        "scene_limitations": ["Estimated monitoring extent; no physical calibration."],
        "candidates": [] if status != "proposals_available" else [{
            "candidate_key": "candidate_1",
            "zone_type": zone_type,
            "label": "Visible access approach",
            "asset_bbox_px": box,
            "threshold_px": [[20.0, 60.0], [80.0, 60.0]] if zone_type == "exit_access" else None,
            "access_polygon_px": polygon,
            "visibility": "adequate",
            "evidence": "A visible asset and its floor approach are present.",
            "limitations": ["Front boundary is an estimate."],
        }],
    }


def make_clip(path: Path, *, size=(100, 100), frames=8, fps=10.0) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for index in range(frames):
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        frame[:, :] = (index * 20, 40, 180)
        writer.write(frame)
    writer.release()
    return path


def video_meta(path: Path, *, video_id="video-1", size=(100, 100), fps=10.0):
    return {
        "video_id": video_id,
        "path": str(path),
        "width": size[0],
        "height": size[1],
        "fps": fps,
        "frame_count": 8,
    }


class FakeProvider:
    def __init__(self, response=None, *, failure=None, block=False):
        self.response = response or payload()
        self.failure = failure
        self.block = block
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = False
        self.calls = []

    def propose(self, evidence):
        self.calls.append(evidence)
        self.started.set()
        if self.block:
            self.release.wait(5)
        if self.failure:
            raise self.failure
        result = validate_model_response(
            self.response,
            width=evidence.manifest["submitted_width"],
            height=evidence.manifest["submitted_height"],
        )
        return SetupProviderResult(result, 12, None, "fake")

    def cancel_active(self):
        self.cancelled = True
        self.release.set()


def wait_terminal(service: ZoneProposalService, job_id: str, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = service.status(job_id)
        if result["status"] not in {"pending", "running"}:
            return result
        time.sleep(0.02)
    raise AssertionError("proposal did not finish")


def test_valid_pixel_geometry_normalizes_for_landscape_and_portrait():
    landscape = validate_model_response(payload(), width=100, height=100)
    assert landscape.candidates[0].access_polygon == [
        [0.2, 0.6], [0.8, 0.6], [0.9, 0.95], [0.1, 0.95]
    ]
    portrait_payload = payload(
        polygon=[[20.0, 120.0], [80.0, 120.0], [90.0, 190.0], [10.0, 190.0]],
        box=[20.0, 20.0, 80.0, 120.0],
    )
    portrait_payload["candidates"][0]["threshold_px"] = [[20.0, 120.0], [80.0, 120.0]]
    portrait = validate_model_response(portrait_payload, width=100, height=200)
    assert portrait.candidates[0].access_polygon[0] == [0.2, 0.6]
    assert portrait.candidates[0].access_polygon[-1] == [0.1, 0.95]


@pytest.mark.parametrize("status", ["no_candidate", "insufficient_visibility"])
def test_empty_scene_results_are_honest(status):
    result = validate_model_response(payload(status=status), width=100, height=100)
    assert result.scene_status == status and result.candidates == []


@pytest.mark.parametrize("bad_polygon", [
    [[10.0, 60.0], [90.0, 95.0], [90.0, 60.0], [10.0, 95.0]],
    [[-2.0, 60.0], [80.0, 60.0], [80.0, 90.0]],
    [[10.0, 60.0], [10.1, 60.0], [10.0, 60.1]],
    [[10.0, 60.0], [float("inf"), 60.0], [80.0, 90.0]],
])
def test_invalid_geometry_is_rejected_per_candidate(bad_polygon):
    result = validate_model_response(payload(polygon=bad_polygon), width=100, height=100)
    assert not result.candidates[0].valid
    assert result.candidates[0].validation_errors


def test_extinguisher_requires_separate_asset_box():
    raw = payload(zone_type="extinguisher_access")
    raw["candidates"][0]["asset_bbox_px"] = None
    result = validate_model_response(raw, width=100, height=100)
    assert not result.candidates[0].valid
    assert "asset_bbox_px" in result.candidates[0].validation_errors[0]


def test_resize_obeys_edge_and_visual_token_limits_for_both_orientations():
    landscape = submitted_size(1920, 1080)
    portrait = submitted_size(1080, 1920)
    assert landscape == tuple(reversed(portrait))
    assert max(landscape) <= 1280
    assert image_token_count(*landscape) <= 1568


def test_capture_records_exact_frame_identity_and_immutable_hashes(tmp_path):
    clip = make_clip(tmp_path / "clip.mp4", frames=8, fps=10)
    settings = SetupSettings(root=tmp_path / "jobs", max_image_dimension=640)
    evidence = SetupEvidenceCapture(settings).create(
        job_id="proposal-1",
        video=video_meta(clip),
        requested_t_ms=249,
        requested_zone_type="auto",
    )
    assert evidence.manifest["source_frame_index"] == 2
    assert evidence.manifest["source_timestamp_ms"] == 200
    assert evidence.manifest["original_width"] == 100
    assert len(evidence.manifest["decoded_frame_sha256"]) == 64
    assert evidence.manifest["transform"]["crop"] is False
    with pytest.raises(ValueError, match="immutable"):
        SetupEvidenceCapture(settings).create(
            job_id="proposal-1",
            video=video_meta(clip),
            requested_t_ms=249,
            requested_zone_type="auto",
        )


def test_async_dedup_bounded_queue_and_cancellation(tmp_path):
    clip = make_clip(tmp_path / "clip.mp4")
    provider = FakeProvider(block=True)
    service = ZoneProposalService(
        SetupSettings(root=tmp_path / "jobs", queue_capacity=1), provider
    )
    first = service.submit(
        video=video_meta(clip), requested_t_ms=0, requested_zone_type="auto"
    )
    assert provider.started.wait(2)
    duplicate = service.submit(
        video=video_meta(clip), requested_t_ms=0, requested_zone_type="auto"
    )
    assert duplicate["job_id"] == first["job_id"]
    service.submit(
        video=video_meta(clip), requested_t_ms=100, requested_zone_type="exit_access"
    )
    with pytest.raises(ProposalQueueFull):
        service.submit(
            video=video_meta(clip), requested_t_ms=200, requested_zone_type="auto"
        )
    cancelled = service.cancel(first["job_id"])
    assert cancelled["status"] == "cancelled"
    assert provider.cancelled
    service.shutdown()


@pytest.mark.parametrize("code", ["timeout", "authentication_failed", "unsupported_model"])
def test_failures_remain_distinct(tmp_path, code):
    clip = make_clip(tmp_path / "clip.mp4")
    provider = FakeProvider(failure=KiroReviewError(code, f"{code} detail"))
    service = ZoneProposalService(SetupSettings(root=tmp_path / "jobs"), provider)
    job = service.submit(
        video=video_meta(clip), requested_t_ms=0, requested_zone_type="auto"
    )
    result = wait_terminal(service, job["job_id"])
    assert result["status"] == "failed"
    assert result["error"]["code"] == code
    service.shutdown()


def test_restart_reconciles_interrupted_job(tmp_path):
    root = tmp_path / "jobs"
    path = root / "proposal-old" / "job.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"job_id": "proposal-old", "status": "running"}))
    service = ZoneProposalService(SetupSettings(root=root), FakeProvider())
    assert service.status("proposal-old")["error"]["code"] == "interrupted"
    service.shutdown()


def test_api_requires_acceptance_and_saves_extinguisher_asset_with_provenance(tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    make_clip(samples / "clip.mp4")
    settings = Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "runs",
        sample_videos_dir=samples,
        bundled_zones=tmp_path / "missing-zones.json",
    )
    provider = FakeProvider(response=payload(zone_type="extinguisher_access"))
    with TestClient(
        create_app(settings, seed_sample=False, proposal_provider=provider)
    ) as client:
        video = client.get("/api/videos").json()[0]
        created = client.post("/api/zone-proposals", json={
            "video_id": video["video_id"],
            "frame_t_ms": 0,
            "requested_zone_type": "extinguisher_access",
        })
        assert created.status_code == 200
        job_id = created.json()["job_id"]
        deadline = time.time() + 5
        result = None
        while time.time() < deadline:
            result = client.get(f"/api/zone-proposals/{job_id}").json()
            if result["status"] == "completed":
                break
            time.sleep(0.02)
        assert result["status"] == "completed"
        assert client.get(f"/api/zone-proposals/{job_id}/frame").status_code == 200
        assert client.get("/api/zones").json() == []
        candidate = result["candidates"][0]
        accepted = client.post(f"/api/zone-proposals/{job_id}/accept", json={
            "candidate_key": candidate["candidate_key"],
            "name": "Lobby extinguisher access",
            "camera_name": "Lobby",
            "view_name": "north wall",
            "polygon": candidate["access_polygon"],
            "asset_name": "Lobby extinguisher",
        })
        assert accepted.status_code == 200, accepted.text
        saved = accepted.json()
        config = saved["config"]
        assert config["zones"][0]["zone_type"] == "extinguisher_access"
        assert config["zones"][0]["protected_asset_id"] == config["assets"][0]["asset_id"]
        assert config["assets"][0]["bbox_xyxy"] == candidate["asset_bbox"]
        assert saved["proposal_provenance"]["job_id"] == job_id
        assert client.get(f"/api/zone-proposals/{job_id}").json()["acceptances"]
