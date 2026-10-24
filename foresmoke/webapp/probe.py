"""Probe a recorded video with OpenCV, the same library the perception package reads it with."""
from __future__ import annotations

from pathlib import Path


class ProbeError(RuntimeError):
    pass


def probe_video(path: str | Path) -> dict:
    """Return duration_s, width, height, fps and frame_count for a video file.

    Width/height come from the first decoded frame, because that is the frame geometry the
    perception package will report in FrameMeta (it reflects any orientation OpenCV applies).
    """
    try:
        import cv2
    except ImportError as error:  # pragma: no cover - perception extra missing
        raise ProbeError("OpenCV is not installed; install the perception extra") from error
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ProbeError("This file could not be opened as a video.")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        if not fps or fps <= 0 or fps > 1000:
            raise ProbeError("The video does not report a valid frame rate.")
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ProbeError("No frames could be decoded from this video.")
        height, width = frame.shape[:2]
    finally:
        capture.release()
    if frame_count <= 0:
        raise ProbeError("The video does not report a frame count.")
    return {
        "duration_s": round(frame_count / fps, 3),
        "width": int(width),
        "height": int(height),
        "fps": round(fps, 6),
        "frame_count": frame_count,
    }


def read_frame_jpeg(path: str | Path, t_ms: int, max_width: int = 360) -> bytes:
    """Decode the source frame at source-video time t_ms and return it as JPEG bytes.

    The perception source assigns t_ms = round(index * 1000 / fps), so the inverse
    round(t_ms * fps / 1000) lands on the same frame index the engine reported.
    """
    import cv2
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ProbeError("video could not be opened")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if fps <= 0:
            raise ProbeError("video has no valid frame rate")
        index = max(0, min(round(max(0, t_ms) * fps / 1000), max(count - 1, 0)))
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ProbeError("frame could not be decoded")
    finally:
        capture.release()
    height, width = frame.shape[:2]
    if width > max_width:
        frame = cv2.resize(frame, (max_width, round(height * max_width / width)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ProbeError("frame could not be encoded")
    return encoded.tobytes()
