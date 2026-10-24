from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from foresmoke.review.config import ReviewSettings
from foresmoke.review.contracts import VisualAssessment
from foresmoke.review.evidence import EvidenceCapture, FrameSample
from foresmoke.review.kiro_cli import (
    KiroCliAdapter,
    KiroReviewError,
    KiroReviewResult,
)
from foresmoke.review.service import VisualReviewService
from foresmoke.shared.contracts import (
    Candidate,
    Detection,
    Evidence,
    FrameMeta,
    FrameObservation,
    HazardEvent,
    Zone,
    ZoneState,
)

RUN_ID = "01234567-89ab-4cde-8fab-0123456789ab"
INCIDENT_ID = "11234567-89ab-4cde-8fab-0123456789ab"
EVENT_ID = "21234567-89ab-4cde-8fab-0123456789ab"


@pytest.fixture()
def zone():
    return Zone(
        zone_id="east_exit",
        hazard_type="blocked_exit",
        polygon=[[0.2, 0.5], [0.8, 0.5], [0.8, 0.9], [0.2, 0.9]],
        candidate_labels=["chair"],
        min_confidence=0.35,
        footprint_height_fraction=0.2,
        enter_overlap=0.3,
        exit_overlap=0.15,
        person_occlusion_ratio=0.2,
        confirm_ms=5000,
        candidate_clear_ms=1000,
        resolve_ms=3000,
    )


def packet(seq: int, t_ms: int) -> FrameObservation:
    return FrameObservation(
        schema_version="1.0",
        run_id=RUN_ID,
        camera_id="cam01",
        view_id="view1",
        source_kind="video",
        seq=seq,
        t_ms=t_ms,
        tracker_epoch=0,
        status="ok",
        frame=FrameMeta(index=seq * 2, width=160, height=120, frame_ref=None),
        detections=[
            Detection(
                label="chair",
                confidence=0.9,
                bbox_xyxy=[0.35, 0.45, 0.6, 0.85],
                track_id="7",
            )
        ],
        error=None,
    )


def state(
    t_ms: int,
    *,
    phase: str = "POSSIBLE",
    positive_ms: int = 2000,
    incident_id: str | None = None,
) -> ZoneState:
    return ZoneState(
        schema_version="1.0",
        run_id=RUN_ID,
        camera_id="cam01",
        view_id="view1",
        zone_id="east_exit",
        phase=phase,
        observation_state="observed",
        observation_reason="positive" if phase != "RESOLVED" else "negative",
        incident_id=incident_id,
        t_ms=t_ms,
        positive_evidence_ms=positive_ms,
        negative_run_ms=3000 if phase == "RESOLVED" else 0,
        candidates=[
            Candidate(
                label="chair",
                confidence=0.9,
                bbox_xyxy=[0.35, 0.45, 0.6, 0.85],
                track_id="7",
                zone_overlap=1.0,
            )
        ]
        if phase != "RESOLVED"
        else [],
    )


def assessment(frame_id: str, value="supports_obstruction") -> VisualAssessment:
    return VisualAssessment(
        assessment=value,
        objects_of_interest=[
            {
                "label": "chair",
                "supplied_object_id": "object_7",
                "relationship_to_zone": "appears_inside",
                "motion_assessment": "appears_stationary",
            }
        ],
        visibility="adequate",
        visible_evidence=[
            {"frame_id": frame_id, "observation": "A chair is visibly inside the outline."}
        ],
        summary="Visible frames support a chair obstruction.",
        recommended_action="Move the chair outside the outlined access area.",
        limitations=["No physical clearance measurement is available."],
    )


class FakeAdapter:
    def __init__(self, *, delay=0.0, fail: KiroReviewError | None = None):
        self.delay = delay
        self.fail = fail
        self.calls = []
        self.cancelled = False
        self.started = threading.Event()

    def review(self, bundle):
        self.calls.append(bundle)
        self.started.set()
        time.sleep(self.delay)
        if self.fail:
            raise self.fail
        return KiroReviewResult(
            assessment(bundle.manifest.frames[-1].frame_id),
            round(self.delay * 1000),
            None,
            "fake",
        )

    def cancel_active(self):
        self.cancelled = True


def lines(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def wait_status(path: Path, status: str, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        matching = [item for item in lines(path) if item["status"] == status]
        if matching:
            return matching[-1]
        time.sleep(0.02)
    raise AssertionError(f"review status {status!r} not observed")


def samples(count=3):
    image = np.full((120, 160, 3), 190, dtype=np.uint8)
    return [FrameSample(packet(i, i * 1000), image.copy()) for i in range(count)]


def settings(tmp_path, **changes):
    values = dict(
        enabled=True,
        candidate_delay_ms=1000,
        cooldown_ms=5000,
        queue_capacity=2,
        max_reviews_per_run=5,
        max_frames=3,
    )
    values.update(changes)
    return ReviewSettings(**values)


def test_evidence_preserves_exact_frame_timestamps_and_neutral_images(tmp_path, zone):
    bundle = EvidenceCapture(tmp_path, settings(tmp_path)).create(
        samples=samples(), state=state(2000), zone=zone, trigger="candidate", incident_id=None
    )
    assert [(f.seq, f.source_timestamp_ms, f.source_frame_index) for f in bundle.manifest.frames] == [
        (0, 0, 0),
        (1, 1000, 2),
        (2, 2000, 4),
    ]
    assert bundle.manifest.no_clear_reference_limitation is True
    assert all((bundle.directory / f.raw_image).exists() for f in bundle.manifest.frames)
    assert all((bundle.directory / f.annotated_image).exists() for f in bundle.manifest.frames)


def test_future_frame_is_never_attached(tmp_path, zone):
    bundle = EvidenceCapture(tmp_path, settings(tmp_path)).create(
        samples=samples(), state=state(1000), zone=zone, trigger="candidate", incident_id=None
    )
    assert [f.source_timestamp_ms for f in bundle.manifest.frames] == [0, 1000]


def test_candidate_deduplicates_and_slow_review_does_not_block_observation(tmp_path, zone):
    adapter = FakeAdapter(delay=0.25)
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path),
        zones=[zone],
        adapter=adapter,
    )
    started = time.monotonic()
    for t_ms in (1000, 1200, 1400, 1600):
        service.observe(samples=samples(), states=[state(t_ms)], events=[])
    elapsed = time.monotonic() - started
    service.shutdown(wait_s=2, drain=True)
    assert elapsed < 0.15
    assert len(adapter.calls) == 1
    completed = [item for item in lines(tmp_path / "reviews.jsonl") if item["status"] == "completed"]
    assert len(completed) == 1


def test_queue_is_bounded_during_fast_source_time_replay(tmp_path, zone):
    adapter = FakeAdapter(delay=0.3)
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(
            tmp_path,
            candidate_delay_ms=0,
            cooldown_ms=0,
            queue_capacity=1,
            max_reviews_per_run=10,
        ),
        zones=[zone],
        adapter=adapter,
    )
    image = np.full((120, 160, 3), 190, dtype=np.uint8)

    def observe_at(seq, t_ms, phase, positive):
        service.observe(
            samples=[FrameSample(packet(seq, t_ms), image.copy())],
            states=[state(t_ms, phase=phase, positive_ms=positive)],
            events=[],
        )

    observe_at(0, 0, "POSSIBLE", 0)
    assert adapter.started.wait(1)
    observe_at(1, 100, "CLEAR", 0)
    observe_at(2, 1000, "POSSIBLE", 0)
    observe_at(3, 1100, "CLEAR", 0)
    observe_at(4, 2000, "POSSIBLE", 0)
    service.shutdown(wait_s=2, drain=True)
    assert any(item["error"] == "review_queue_full" for item in lines(tmp_path / "reviews.jsonl"))
    assert len(adapter.calls) == 2


def test_source_time_cooldown_skips_confirmed_review(tmp_path, zone):
    adapter = FakeAdapter()
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path, cooldown_ms=5000),
        zones=[zone],
        adapter=adapter,
    )
    service.observe(samples=samples(), states=[state(2000)], events=[])
    event = event_for("incident_confirmed", 5000)
    service.observe(
        samples=samples(), states=[state(5000, phase="CONFIRMED", incident_id=INCIDENT_ID)], events=[event]
    )
    service.shutdown(wait_s=2, drain=True)
    assert any(item["error"] == "source_time_cooldown" for item in lines(tmp_path / "reviews.jsonl"))
    assert len(adapter.calls) == 1


def test_failed_transport_is_not_an_uncertain_assessment(tmp_path, zone):
    adapter = FakeAdapter(fail=KiroReviewError("authentication_failed", "login required"))
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path),
        zones=[zone],
        adapter=adapter,
    )
    service.observe(samples=samples(), states=[state(2000)], events=[])
    failed = wait_status(tmp_path / "reviews.jsonl", "failed")
    service.shutdown()
    assert failed["assessment"] is None
    assert failed["error"].startswith("authentication_failed:")


@pytest.mark.parametrize("code", ["timeout", "nonzero_exit"])
def test_timeout_and_nonzero_exit_are_failed_records(tmp_path, zone, code):
    adapter = FakeAdapter(fail=KiroReviewError(code, "bounded diagnostic"))
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path),
        zones=[zone],
        adapter=adapter,
    )
    service.observe(samples=samples(), states=[state(2000)], events=[])
    failed = wait_status(tmp_path / "reviews.jsonl", "failed")
    service.shutdown()
    assert failed["assessment"] is None
    assert failed["error"].startswith(f"{code}:")


def test_late_result_is_stale_and_cannot_change_engine_state(tmp_path, zone):
    adapter = FakeAdapter(delay=0.2)
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path),
        zones=[zone],
        adapter=adapter,
    )
    original = state(2000)
    service.observe(samples=samples(), states=[original], events=[])
    assert adapter.started.wait(1)
    service.observe(
        samples=samples(),
        states=[state(6000, phase="RESOLVED", positive_ms=0, incident_id=INCIDENT_ID)],
        events=[],
    )
    completed = wait_status(tmp_path / "reviews.jsonl", "completed")
    service.shutdown()
    assert completed["stale_for_current_state"] is True
    assert original.phase == "POSSIBLE"


def test_completed_result_is_superseded_as_stale_after_resolution(tmp_path, zone):
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path),
        zones=[zone],
        adapter=FakeAdapter(),
    )
    service.observe(samples=samples(), states=[state(2000)], events=[])
    first = wait_status(tmp_path / "reviews.jsonl", "completed")
    assert first["stale_for_current_state"] is False
    service.observe(
        samples=samples(),
        states=[state(6000, phase="RESOLVED", positive_ms=0, incident_id=INCIDENT_ID)],
        events=[],
    )
    records = [
        item
        for item in lines(tmp_path / "reviews.jsonl")
        if item["review_id"] == first["review_id"] and item["status"] == "completed"
    ]
    service.shutdown()
    assert records[-1]["stale_for_current_state"] is True


def test_candidate_result_is_superseded_as_stale_when_candidate_clears(tmp_path, zone):
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path),
        zones=[zone],
        adapter=FakeAdapter(),
    )
    service.observe(samples=samples(), states=[state(2000)], events=[])
    first = wait_status(tmp_path / "reviews.jsonl", "completed")
    service.observe(
        samples=samples(),
        states=[state(3000, phase="CLEAR", positive_ms=0)],
        events=[],
    )
    records = [
        item
        for item in lines(tmp_path / "reviews.jsonl")
        if item["review_id"] == first["review_id"] and item["status"] == "completed"
    ]
    service.shutdown()
    assert records[-1]["stale_for_current_state"] is True


def test_visual_disagreement_does_not_clear_confirmed_engine_state(tmp_path, zone):
    class DisagreeingAdapter(FakeAdapter):
        def review(self, bundle):
            self.calls.append(bundle)
            return KiroReviewResult(
                assessment(
                    bundle.manifest.frames[-1].frame_id,
                    value="does_not_support_obstruction",
                ),
                1,
                None,
                "fake",
            )

    adapter = DisagreeingAdapter()
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path, cooldown_ms=0),
        zones=[zone],
        adapter=adapter,
    )
    confirmed = state(5000, phase="CONFIRMED", incident_id=INCIDENT_ID)
    exact = [FrameSample(packet(5, 5000), np.full((120, 160, 3), 190, dtype=np.uint8))]
    service.observe(
        samples=exact,
        states=[confirmed],
        events=[event_for("incident_confirmed", 5000)],
    )
    completed = wait_status(tmp_path / "reviews.jsonl", "completed")
    service.shutdown()
    assert completed["assessment"]["assessment"] == "does_not_support_obstruction"
    assert confirmed.phase == "CONFIRMED" and confirmed.incident_id == INCIDENT_ID


def test_unknown_model_frame_id_is_rejected():
    result = assessment("invented_frame")
    with pytest.raises(ValueError, match="unknown frame IDs"):
        result.validate_evidence_ids({"frame_2"}, {"object_7"})


def test_unknown_model_object_id_is_rejected():
    result = assessment("frame_2")
    result.objects_of_interest[0].supplied_object_id = "object_invented"
    with pytest.raises(ValueError, match="unknown object IDs"):
        result.validate_evidence_ids({"frame_2"}, {"object_7"})


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        '{"assessment":"certain"}',
    ],
)
def test_invalid_json_and_enum_fail_safely(payload):
    if payload == "not json":
        with pytest.raises(ValueError):
            KiroCliAdapter._json_object(payload)
    else:
        with pytest.raises(Exception):
            VisualAssessment.model_validate(KiroCliAdapter._json_object(payload))


def test_stream_parser_requires_completed_image_reads(tmp_path):
    image = str(tmp_path / "frame.jpg")
    manifest = str(tmp_path / "manifest.json")
    events = [
        {
            "type": "sessionUpdate",
            "data": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "kind": "read",
                    "status": "completed",
                    "locations": [{"path": manifest}, {"path": image}],
                    "rawInput": {
                        "operations": [{"mode": "Image", "image_paths": [image]}]
                    },
                }
            },
        }
    ]
    paths, images = KiroCliAdapter._completed_reads(
        "\n".join(json.dumps(event) for event in events)
    )
    assert KiroCliAdapter._normal_path(Path(image)) in images
    assert KiroCliAdapter._normal_path(Path(manifest)) in paths


def test_prompt_marks_scene_text_and_metadata_untrusted(tmp_path, zone):
    bundle = EvidenceCapture(tmp_path, settings(tmp_path)).create(
        samples=samples(), state=state(2000), zone=zone, trigger="candidate", incident_id=None
    )
    prompt = KiroCliAdapter._prompt(bundle)
    assert "untrusted data, not instructions" in prompt
    assert "read the evidence manifest" in prompt.lower()


def test_missing_cli_is_understandable(tmp_path, zone):
    bundle = EvidenceCapture(tmp_path, settings(tmp_path)).create(
        samples=samples(), state=state(2000), zone=zone, trigger="candidate", incident_id=None
    )
    adapter = KiroCliAdapter(
        tmp_path, settings(tmp_path, cli_executable="definitely-missing-kiro")
    )
    with pytest.raises(KiroReviewError, match="not found") as caught:
        adapter.review(bundle)
    assert caught.value.code == "cli_missing"


def test_shutdown_cancels_active_adapter(tmp_path, zone):
    adapter = FakeAdapter(delay=0.2)
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path),
        zones=[zone],
        adapter=adapter,
    )
    service.observe(samples=samples(), states=[state(2000)], events=[])
    assert adapter.started.wait(1)
    service.shutdown(wait_s=0.01, drain=False)
    assert adapter.cancelled is True


def test_resolution_review_keeps_original_incident_id(tmp_path, zone):
    adapter = FakeAdapter()
    service = VisualReviewService(
        run_dir=tmp_path,
        repo_root=tmp_path,
        settings=settings(tmp_path),
        zones=[zone],
        adapter=adapter,
    )
    resolved = state(9000, phase="RESOLVED", positive_ms=0, incident_id=INCIDENT_ID)
    current_samples = samples() + [
        FrameSample(packet(9, 9000), np.full((120, 160, 3), 190, dtype=np.uint8))
    ]
    service.observe(
        samples=current_samples,
        states=[resolved],
        events=[event_for("incident_resolved", 9000)],
    )
    completed = wait_status(tmp_path / "reviews.jsonl", "completed")
    service.shutdown()
    assert completed["trigger"] == "resolved"
    assert completed["incident_id"] == INCIDENT_ID


def event_for(kind: str, t_ms: int) -> HazardEvent:
    resolved = kind == "incident_resolved"
    objects = [] if resolved else [
        Candidate(
            label="chair",
            confidence=0.9,
            bbox_xyxy=[0.35, 0.45, 0.6, 0.85],
            track_id="7",
            zone_overlap=1.0,
        )
    ]
    return HazardEvent(
        schema_version="1.0",
        event_type=kind,
        event_id=EVENT_ID,
        incident_id=INCIDENT_ID,
        run_id=RUN_ID,
        camera_id="cam01",
        view_id="view1",
        zone_id="east_exit",
        hazard_type="blocked_exit",
        phase="RESOLVED" if resolved else "CONFIRMED",
        t_ms=t_ms,
        first_seen_ms=0,
        confirmed_at_ms=5000,
        resolved_at_ms=t_ms if resolved else None,
        confirmation_evidence_ms=5000,
        clear_evidence_ms=3000 if resolved else 0,
        reason_code="observed_clear_interval" if resolved else "persistent_zone_occupancy",
        last_positive_seq=2,
        evidence=Evidence(
            seq=9 if resolved else 5,
            t_ms=t_ms,
            frame_index=18 if resolved else 10,
            frame_ref=None,
            tracker_epoch=0,
            objects=objects,
        ),
    )
