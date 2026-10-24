from types import SimpleNamespace

import cv2
import numpy as np

from foresmoke.reasoning import load_config
from foresmoke.shared.contracts import Detection
from foresmoke.visualization import (
    RawVideoRecorder,
    VideoBundle,
    detection_panel,
    result_panel,
)


def _frame(value=30):
    return np.full((180, 320, 3), value, dtype=np.uint8)


def _frame_count(path):
    capture = cv2.VideoCapture(str(path))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    capture.release()
    return count, width, height


def test_clean_recording_uses_source_timeline(tmp_path):
    recorder = RawVideoRecorder(tmp_path / "raw_footage.mp4", fps=10)
    recorder(_frame(10), 0, 0)
    recorder(_frame(20), 1, 100)
    recorder(_frame(30), 2, 200)
    recorder.close()
    assert recorder.error is None
    assert _frame_count(tmp_path / "raw_footage.mp4") == (3, 320, 180)


def test_three_panel_video_is_synchronized_and_horizontal(tmp_path):
    config = load_config("configs/zones.json")
    detection = Detection(label="chair", confidence=0.9,
                          bbox_xyxy=[0.38, 0.28, 0.56, 0.84], track_id="7")
    packet = SimpleNamespace(detections=[detection], t_ms=5000)
    state = SimpleNamespace(
        phase="CONFIRMED", observation_state="observed", observation_reason="positive",
        positive_evidence_ms=5000, negative_run_ms=0,
    )
    raw = _frame()
    detected = detection_panel(raw, packet)
    result = result_panel(raw, config.zones, [state], "EAST EXIT: INCIDENT CONFIRMED")
    bundle = VideoBundle(tmp_path, fps=5, panel_width=320)
    for t_ms in (5000, 5200, 5400):
        bundle.write(raw, detected, result, t_ms)
    bundle.close()
    count, width, height = _frame_count(tmp_path / "combined_three_panel.mp4")
    assert count == 3
    assert width == 960
    assert height > 180

