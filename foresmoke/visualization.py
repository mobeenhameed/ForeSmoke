"""Synchronized video panels for the integrated ForeSmoke demonstration."""
from __future__ import annotations

from pathlib import Path
import threading


PHASE_COLORS = {
    "CLEAR": (70, 200, 70),
    "POSSIBLE": (0, 200, 255),
    "CONFIRMED": (40, 40, 230),
    "RESOLVED": (220, 170, 40),
}


def _fit(frame, width: int, height: int):
    import cv2
    import numpy as np

    source_h, source_w = frame.shape[:2]
    scale = min(width / source_w, height / source_h)
    resized = cv2.resize(frame, (round(source_w * scale), round(source_h * scale)))
    canvas = np.zeros((height, width, 3), dtype=frame.dtype)
    y = (height - resized.shape[0]) // 2
    x = (width - resized.shape[1]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return canvas


def detection_panel(raw_frame, packet):
    from .perception.preview import draw_preview

    panel = raw_frame.copy()
    return draw_preview(panel, packet.detections, packet.t_ms)


def result_panel(raw_frame, zones, states, latest_event: str | None = None):
    import cv2
    import numpy as np

    panel = raw_frame.copy()
    shade = np.zeros_like(panel)
    cv2.addWeighted(panel, 0.58, shade, 0.42, 0, panel)
    height, width = panel.shape[:2]
    for index, (zone, state) in enumerate(zip(zones, states)):
        color = (140, 140, 140) if state.observation_state == "unknown" else PHASE_COLORS[state.phase]
        points = np.array(
            [[round(x * (width - 1)), round(y * (height - 1))] for x, y in zone.polygon],
            dtype=np.int32,
        )
        cv2.polylines(panel, [points], True, color, max(3, round(width / 500)))
        cv2.putText(
            panel,
            f"{zone.zone_id}: {state.phase} / {state.observation_state.upper()}",
            (20, 42 + index * 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(0.65, width / 1900),
            color,
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            panel,
            f"Reason: {state.observation_reason} | positive {state.positive_evidence_ms / 1000:.1f}s | clear {state.negative_run_ms / 1000:.1f}s",
            (20, 68 + index * 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(0.48, width / 2500),
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
    if latest_event:
        cv2.rectangle(panel, (0, height - 54), (width, height), (25, 25, 25), -1)
        cv2.putText(panel, latest_event, (18, height - 18), cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.55, width / 2200), (255, 255, 255), 2, cv2.LINE_AA)
    return panel


def comparison_panel(raw, detections, result, t_ms: int, panel_width: int = 640):
    import cv2
    import numpy as np

    aspect = raw.shape[0] / raw.shape[1]
    panel_height = max(240, round(panel_width * aspect))
    header_height, footer_height = 44, 48
    body = np.hstack([
        _fit(raw, panel_width, panel_height),
        _fit(detections, panel_width, panel_height),
        _fit(result, panel_width, panel_height),
    ])
    canvas = np.zeros((header_height + panel_height + footer_height, panel_width * 3, 3), dtype=np.uint8)
    canvas[header_height:header_height + panel_height] = body
    for index, title in enumerate(("1  RAW CAMERA", "2  DETECTIONS", "3  FINAL RESULT")):
        cv2.putText(canvas, title, (index * panel_width + 18, 29), cv2.FONT_HERSHEY_SIMPLEX,
                    0.68, (245, 245, 245), 2, cv2.LINE_AA)
    cv2.putText(canvas, f"ForeSmoke synchronized evidence  |  source time {t_ms / 1000:.1f}s",
                (18, canvas.shape[0] - 17), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                (210, 210, 210), 1, cv2.LINE_AA)
    return canvas


class RawVideoRecorder:
    """Record clean source frames on a constant timeline before any overlays."""

    def __init__(self, path: Path, fps: float):
        self.path = Path(path)
        self.fps = fps
        self.writer = None
        self.first_t_ms = None
        self.written = 0
        self.last_frame = None
        self.error = None
        self.closed = False
        self._lock = threading.Lock()

    def __call__(self, frame, frame_index: int, t_ms: int):
        with self._lock:
            if self.closed:
                return
            self._write(frame, frame_index, t_ms)

    def _write(self, frame, frame_index: int, t_ms: int):
        import cv2

        if self.error is not None:
            return
        try:
            if self.writer is None:
                height, width = frame.shape[:2]
                self.writer = cv2.VideoWriter(
                    str(self.path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (width, height)
                )
                if not self.writer.isOpened():
                    raise RuntimeError(f"video_writer_failed: {self.path}")
                self.first_t_ms = t_ms
            target_index = max(0, round((t_ms - self.first_t_ms) * self.fps / 1000))
            if target_index < self.written:
                self.last_frame = frame.copy()
                return
            while self.written < target_index:
                self.writer.write(frame if self.last_frame is None else self.last_frame)
                self.written += 1
            self.writer.write(frame)
            self.written += 1
            self.last_frame = frame.copy()
        except Exception as exc:
            self.error = exc

    def close(self):
        with self._lock:
            self.closed = True
            if self.writer is not None:
                self.writer.release()
                self.writer = None


class VideoBundle:
    """Write the synchronized comparison, with optional separate processed panels."""

    def __init__(self, run_dir: Path, fps: float, panel_width: int = 640, individual=False):
        self.run_dir = Path(run_dir)
        self.fps = fps
        self.panel_width = panel_width
        self.individual = individual
        self._writers = {}

    def _writer(self, name: str, frame):
        import cv2

        writer = self._writers.get(name)
        if writer is None:
            height, width = frame.shape[:2]
            path = self.run_dir / name
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (width, height))
            if not writer.isOpened():
                raise RuntimeError(f"video_writer_failed: {path}")
            self._writers[name] = writer
        return writer

    def write(self, raw, detections, result, t_ms: int):
        combined = comparison_panel(raw, detections, result, t_ms, self.panel_width)
        outputs = [("combined_three_panel.mp4", combined)]
        if self.individual:
            outputs += [("detections.mp4", detections), ("final_results.mp4", result)]
        for name, frame in outputs:
            self._writer(name, frame).write(frame)
        return combined

    def close(self):
        for writer in self._writers.values():
            writer.release()
        self._writers.clear()
