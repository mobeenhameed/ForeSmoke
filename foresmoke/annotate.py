"""Interactive protected-access zone annotator for a fixed camera or recorded video."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from foresmoke.reasoning.config import validate_config
from foresmoke.shared.contracts import ZoneConfig, ZONE_TYPE_TO_HAZARD


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
                    width: int, height: int, zone_name: str | None = None,
                    zone_type: str | None = None, asset_id: str | None = None,
                    asset_name: str | None = None, asset_bbox: list[float] | None = None,
                    asset_point: list[float] | None = None,
                    reference_image_id: str | None = None) -> dict:
    """Update or append one zone while preserving unrelated assets, zones, and thresholds."""
    target = Path(path)
    data = json.loads(target.read_text(encoding="utf-8-sig")) if target.exists() else {
        "schema_version": "1.0", "camera_id": camera_id or "cam01",
        "view_id": view_id or "exit-view-v1", "reference_frame": {"width": width, "height": height},
        "max_observation_gap_ms": 1000, "live_stale_after_ms": 2000,
        "assets": [], "zones": [],
    }
    data["schema_version"] = "1.0"
    data["camera_id"] = camera_id or data.get("camera_id", "cam01")
    data["view_id"] = view_id or data.get("view_id", "exit-view-v1")
    data["reference_frame"] = {"width": width, "height": height}
    assets = data.setdefault("assets", [])
    zones = data.setdefault("zones", [])
    wanted = zone_id or (zones[0].get("zone_id") if zones else "east_exit")
    selected = None
    for zone in zones:
        if zone.get("zone_id") == wanted:
            zone["polygon"] = polygon
            selected = zone
            break
    else:
        selected = {
            "zone_id": wanted, "name": zone_name or wanted,
            "zone_type": zone_type or "exit_access", "polygon": polygon,
            "candidate_labels": ["chair", "backpack", "suitcase"], "min_confidence": 0.35,
            "footprint_height_fraction": 0.20, "enter_overlap": 0.30, "exit_overlap": 0.15,
            "person_occlusion_ratio": 0.20, "confirm_ms": 5000,
            "candidate_clear_ms": 1000, "resolve_ms": 3000,
        }
        zones.append(selected)
    assert selected is not None
    selected_type = zone_type or selected.get("zone_type", "exit_access")
    if selected_type not in ZONE_TYPE_TO_HAZARD:
        raise ValueError(f"unsupported zone_type: {selected_type}")
    selected["zone_type"] = selected_type
    selected["hazard_type"] = ZONE_TYPE_TO_HAZARD[selected_type]
    selected["name"] = zone_name or selected.get("name") or wanted

    if selected_type == "extinguisher_access":
        linked_id = asset_id or selected.get("protected_asset_id")
        if not linked_id:
            raise ValueError("extinguisher_access requires --asset-id")
        selected["protected_asset_id"] = linked_id
        asset = next((item for item in assets if item.get("asset_id") == linked_id), None)
        if asset is None:
            if not asset_name:
                raise ValueError("a new extinguisher asset requires --asset-name")
            if (asset_bbox is None) == (asset_point is None):
                raise ValueError("provide exactly one of --asset-bbox or --asset-point")
            asset = {
                "asset_id": linked_id,
                "asset_type": "fire_extinguisher",
                "name": asset_name,
                "camera_id": data["camera_id"],
                "view_id": data["view_id"],
                "bbox_xyxy": asset_bbox,
                "point": asset_point,
                "reference_image_id": reference_image_id,
            }
            assets.append(asset)
        else:
            if asset_name is not None:
                asset["name"] = asset_name
            if asset_bbox is not None:
                asset["bbox_xyxy"], asset["point"] = asset_bbox, None
            if asset_point is not None:
                asset["point"], asset["bbox_xyxy"] = asset_point, None
            if reference_image_id is not None:
                asset["reference_image_id"] = reference_image_id
            asset["camera_id"], asset["view_id"] = data["camera_id"], data["view_id"]
    elif asset_id is not None:
        selected["protected_asset_id"] = asset_id
    else:
        selected.setdefault("protected_asset_id", None)

    # Strict contract and polygon validation happen before replacing the saved file.
    validate_config(ZoneConfig.model_validate_json(json.dumps(data)))
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
             frame_index: int = 0, max_width: int = 1400, max_height: int = 800,
             zone_name: str | None = None, zone_type: str | None = None,
             asset_id: str | None = None, asset_name: str | None = None,
             asset_bbox: list[float] | None = None, asset_point: list[float] | None = None,
             reference_image_id: str | None = None) -> int:
    import cv2
    import numpy as np

    frame = _read_frame(source, source_kind, frame_index)
    height, width = frame.shape[:2]
    scale, left, top, view_w, view_h = fit_view(width, height, max_width, max_height)
    points: list[list[float]] = []
    asset_click_point = list(asset_point) if asset_point is not None else None
    needs_asset_click = (
        zone_type == "extinguisher_access"
        and asset_bbox is None
        and asset_click_point is None
    )
    window = "ForeSmoke zone annotator"
    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    try:
        while True:
            canvas = np.zeros((max_height, max_width, 3), dtype=np.uint8)
            resized = cv2.resize(frame, (view_w, view_h))
            canvas[top:top + view_h, left:left + view_w] = resized
            if asset_bbox is not None:
                x1, y1, x2, y2 = asset_bbox
                p1 = normalized_to_display([x1, y1], scale, left, top, width, height)
                p2 = normalized_to_display([x2, y2], scale, left, top, width, height)
                cv2.rectangle(canvas, p1, p2, (220, 80, 220), 3)
            elif asset_click_point is not None:
                marker = normalized_to_display(
                    asset_click_point, scale, left, top, width, height
                )
                cv2.drawMarker(
                    canvas, marker, (220, 80, 220), cv2.MARKER_CROSS, 24, 3, cv2.LINE_AA
                )
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
            instruction = (
                "First click extinguisher, then click floor/access corners"
                if needs_asset_click and asset_click_point is None
                else "Click floor/access corners"
            )
            cv2.putText(canvas, f"{instruction} | Enter: save | R: reset | U: undo | Esc: cancel",
                        (18, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (245, 245, 245), 2, cv2.LINE_AA)
            cv2.putText(canvas, f"{len(points)} points  | frame {frame_index}  | {width}x{height}",
                        (18, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 210, 220), 1, cv2.LINE_AA)
            cv2.imshow(window, canvas)

            def click(event, x, y, flags, _):
                nonlocal asset_click_point
                if event == cv2.EVENT_LBUTTONDOWN:
                    point = display_to_normalized(x, y, scale, left, top, width, height)
                    if point is not None:
                        if needs_asset_click and asset_click_point is None:
                            asset_click_point = point
                        else:
                            points.append(point)
            cv2.setMouseCallback(window, click)
            key = cv2.waitKey(30) & 0xFF
            if key in (13, 10):
                if len(points) < 3:
                    print("Choose at least three points before saving.")
                    continue
                update_zone_file(output, points, zone_id=zone_id, camera_id=camera_id, view_id=view_id,
                                 width=width, height=height, zone_name=zone_name,
                                 zone_type=zone_type, asset_id=asset_id, asset_name=asset_name,
                                 asset_bbox=asset_bbox, asset_point=asset_click_point,
                                 reference_image_id=reference_image_id)
                print(f"Saved {len(points)}-point zone to {output} for view {view_id or 'existing'}.")
                return 0
            if key in (ord("r"), ord("R")):
                points.clear()
                if needs_asset_click:
                    asset_click_point = None
            elif key in (ord("u"), ord("U"), 8):
                if points:
                    points.pop()
                elif needs_asset_click:
                    asset_click_point = None
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
    parser.add_argument("--zone-name", default=None)
    parser.add_argument("--zone-type", choices=["exit_access", "extinguisher_access"], default=None)
    parser.add_argument("--asset-id", default=None)
    parser.add_argument("--asset-name", default=None)
    parser.add_argument("--asset-bbox", type=_coordinates,
                        help="normalized x1,y1,x2,y2 marker for a registered extinguisher")
    parser.add_argument("--asset-point", type=_coordinates,
                        help="normalized x,y marker for a registered extinguisher")
    parser.add_argument("--reference-image-id", default=None)
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
                        max_width=args.max_width, max_height=args.max_height,
                        zone_name=args.zone_name, zone_type=args.zone_type,
                        asset_id=args.asset_id, asset_name=args.asset_name,
                        asset_bbox=args.asset_bbox, asset_point=args.asset_point,
                        reference_image_id=args.reference_image_id)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 2


def _coordinates(value: str) -> list[float]:
    try:
        return [float(part.strip()) for part in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("coordinates must be comma-separated numbers") from error


if __name__ == "__main__":
    raise SystemExit(main())
