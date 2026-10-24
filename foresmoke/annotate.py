"""Interactive exit-access zone annotator for a fixed camera or recorded video."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable


def fit_view(width: int, height: int, max_width: int, max_height: int) -> tuple[float, int, int, int, int]:
    """Return scale and letterbox placement for an image in the annotation window."""
    scale = min(1.0, max_width / width, max_height / height)
    view_w, view_h = round(width * scale), round(height * scale)
    left, top = (max_width - view_w) // 2, (max_height - view_h) // 2
    return scale, left, top, view_w, view_h


def display_to_normalized(x: int, y: int, scale: float, left: int, top: int,
                          width: int, height: int) -> list[float] | None:
    """Convert a window click into normalized original-frame coordinates."""
    if scale <= 0:
        raise ValueError("display scale must be positive")
    original_x = (x - left) / scale
    original_y = (y - top) / scale
    if not (0 <= original_x <= width and 0 <= original_y <= height):
        return None
    return [round(original_x / width, 6), round(original_y / height, 6)]


def normalized_to_display(point: Iterable[float], scale: float, left: int, top: int,
                          width: int, height: int) -> tuple[int, int]:
    x, y = point
    return round(left + x * width * scale), round(top + y * height * scale)


def update_zone_file(path: str | Path, polygon: list[list[float]], *, zone_id: str | None = None,
                    camera_id: str | None = None, view_id: str | None = None,
                    width: int, height: int) -> dict:
    """Update one zone polygon while preserving thresholds and other configuration."""
    target = Path(path)
    data = json.loads(target.read_text(encoding="utf-8-sig")) if target.exists() else {
        "schema_version": "1.0", "camera_id": camera_id or "cam01",
        "view_id": view_id or "exit-view-v1", "reference_frame": {"width": width, "height": height},
        "max_observation_gap_ms": 1000, "live_stale_after_ms": 2000, "zones": [],
    }
    data["schema_version"] = "1.0"
    data["camera_id"] = camera_id or data.get("camera_id", "cam01")
    data["view_id"] = view_id or data.get("view_id", "exit-view-v1")
    data["reference_frame"] = {"width": width, "height": height}
    zones = data.setdefault("zones", [])
    wanted = zone_id or (zones[0].get("zone_id") if zones else "east_exit")
    for zone in zones:
        if zone.get("zone_id") == wanted:
            zone["polygon"] = polygon
            break
    else:
        zones.append({
            "zone_id": wanted, "hazard_type": "blocked_exit", "polygon": polygon,
            "candidate_labels": ["chair", "backpack", "suitcase"], "min_confidence": 0.35,
            "footprint_height_fraction": 0.20, "enter_overlap": 0.30, "exit_overlap": 0.15,
            "person_occlusion_ratio": 0.20, "confirm_ms": 5000,
            "candidate_clear_ms": 1000, "resolve_ms": 3000,
        })
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def _read_frame(source: str, source_kind: str, frame_index: int):
    import cv2
    if source_kind == "image":
        frame = cv2.imread(source)
        if frame is None:
            raise RuntimeError(f"image_open_failed: {source}")
        return frame
    capture = cv2.VideoCapture(int(source) if source_kind == "live" else source)
    if not capture.isOpened():
        raise RuntimeError(f"capture_open_failed: {source}")
    try:
        if source_kind == "video":
            capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_index))
        ok, frame = capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"frame_read_failed: {source}")
        return frame
    finally:
        capture.release()


def annotate(source: str, source_kind: str, output: str, *, zone_id: str | None = None,
             camera_id: str | None = None, view_id: str | None = None,
             frame_index: int = 0, max_width: int = 1400, max_height: int = 800) -> int:
    import cv2
    import numpy as np

    frame = _read_frame(source, source_kind, frame_index)
    height, width = frame.shape[:2]
    scale, left, top, view_w, view_h = fit_view(width, height, max_width, max_height)
    points: list[list[float]] = []
    window = "ForeSmoke zone annotator"
    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    try:
        while True:
            canvas = np.zeros((max_height, max_width, 3), dtype=np.uint8)
            resized = cv2.resize(frame, (view_w, view_h))
            canvas[top:top + view_h, left:left + view_w] = resized
            display_points = [normalized_to_display(p, scale, left, top, width, height) for p in points]
            if len(display_points) >= 2:
                cv2.polylines(canvas, [np.array(display_points, dtype=np.int32)], False, (40, 220, 255), 3)
            if len(display_points) >= 3:
                cv2.fillPoly(canvas, [np.array(display_points, dtype=np.int32)], (30, 100, 70))
                cv2.polylines(canvas, [np.array(display_points, dtype=np.int32)], True, (40, 255, 120), 3)
            for index, point in enumerate(display_points, 1):
                cv2.circle(canvas, point, 7, (0, 200, 255), -1)
                cv2.putText(canvas, str(index), (point[0] + 8, point[1] - 8), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.rectangle(canvas, (0, 0), (max_width, 78), (18, 18, 18), -1)
            cv2.putText(canvas, "Click floor/access corners | Enter: save | R: reset | U: undo | Esc: cancel",
                        (18, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (245, 245, 245), 2, cv2.LINE_AA)
            cv2.putText(canvas, f"{len(points)} points  | frame {frame_index}  | {width}x{height}",
                        (18, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 210, 220), 1, cv2.LINE_AA)
            cv2.imshow(window, canvas)

            def click(event, x, y, flags, _):
                if event == cv2.EVENT_LBUTTONDOWN:
                    point = display_to_normalized(x, y, scale, left, top, width, height)
                    if point is not None:
                        points.append(point)
            cv2.setMouseCallback(window, click)
            key = cv2.waitKey(30) & 0xFF
            if key in (13, 10):
                if len(points) < 3:
                    print("Choose at least three points before saving.")
                    continue
                update_zone_file(output, points, zone_id=zone_id, camera_id=camera_id, view_id=view_id,
                                 width=width, height=height)
                print(f"Saved {len(points)}-point zone to {output} for view {view_id or 'existing'}.")
                return 0
            if key in (ord("r"), ord("R")):
                points.clear()
            elif key in (ord("u"), ord("U"), 8):
                if points:
                    points.pop()
            elif key == 27:
                return 1
    finally:
        cv2.destroyWindow(window)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="image, video path, or camera index")
    parser.add_argument("--source-kind", choices=["image", "video", "live"], default="video")
    parser.add_argument("--output", default="configs/zones.json")
    parser.add_argument("--zone-id", default=None)
    parser.add_argument("--camera-id", default=None)
    parser.add_argument("--view-id", default=None)
    parser.add_argument("--frame-index", type=int, default=0, help="video frame to annotate")
    parser.add_argument("--max-width", type=int, default=1400)
    parser.add_argument("--max-height", type=int, default=800)
    args = parser.parse_args(argv)
    if args.frame_index < 0 or args.max_width < 320 or args.max_height < 240:
        parser.error("frame-index must be nonnegative and annotation window must be at least 320x240")
    try:
        return annotate(args.source, args.source_kind, args.output, zone_id=args.zone_id,
                        camera_id=args.camera_id, view_id=args.view_id, frame_index=args.frame_index,
                        max_width=args.max_width, max_height=args.max_height)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
