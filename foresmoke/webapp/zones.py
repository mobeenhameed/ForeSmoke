"""Assemble, validate and persist ZoneConfig files.

The webapp never decides whether a polygon is acceptable on its own authority: every config
is checked by ``foresmoke.reasoning.config.validate_config`` (contract + Shapely geometry),
the same function the engine runs when the analysis CLI loads ``--zones``.
"""
from __future__ import annotations

import json
from pathlib import Path

from foresmoke.reasoning.config import validate_config
from foresmoke.shared.contracts import ZONE_TYPE_TO_HAZARD, ZoneConfig

from .store import Settings, check_id, new_id, now_iso, read_json, slug, write_json

# Defaults for schema-required fields that the MVP does not expose; identical to the values
# foresmoke.annotate writes for a new zone.
ZONE_DEFAULTS = {
    "hazard_type": "blocked_exit",
    "footprint_height_fraction": 0.20,
    "enter_overlap": 0.30,
    "exit_overlap": 0.15,
    "person_occlusion_ratio": 0.20,
    "candidate_clear_ms": 1000,
}
PARAM_DEFAULTS = {
    "candidate_labels": ["chair", "backpack", "suitcase"],
    "confirm_ms": 5000,
    "resolve_ms": 3000,
    "min_confidence": 0.35,
}
CONFIG_DEFAULTS = {"max_observation_gap_ms": 1000, "live_stale_after_ms": 2000}
BUNDLED_ID = "bundled-configs-zones"


class ZoneError(ValueError):
    pass


def build_zone_config(*, camera_id: str, view_id: str, width: int, height: int, zone_id: str,
                      polygon: list[list[float]], params: dict | None = None,
                      zone_type: str = "exit_access", asset_id: str | None = None,
                      asset_name: str | None = None,
                      asset_bbox: list[float] | None = None,
                      reference_image_id: str | None = None) -> dict:
    params = {**PARAM_DEFAULTS, **(params or {})}
    if zone_type not in ZONE_TYPE_TO_HAZARD:
        raise ZoneError(f"unsupported zone_type: {zone_type}")
    if zone_type == "extinguisher_access" and not (asset_id and asset_name and asset_bbox):
        raise ZoneError("extinguisher access requires an asset ID, name, and visible asset box")
    zone = {
        "zone_id": zone_id,
        "name": zone_id.replace("-", " ").replace("_", " ").title(),
        "zone_type": zone_type,
        "hazard_type": ZONE_TYPE_TO_HAZARD[zone_type],
        "protected_asset_id": asset_id if zone_type == "extinguisher_access" else None,
        "polygon": [[round(float(x), 6), round(float(y), 6)] for x, y in polygon],
        "candidate_labels": list(params["candidate_labels"]),
        "min_confidence": float(params["min_confidence"]),
        "footprint_height_fraction": ZONE_DEFAULTS["footprint_height_fraction"],
        "enter_overlap": ZONE_DEFAULTS["enter_overlap"],
        "exit_overlap": ZONE_DEFAULTS["exit_overlap"],
        "person_occlusion_ratio": ZONE_DEFAULTS["person_occlusion_ratio"],
        "confirm_ms": int(params["confirm_ms"]),
        "candidate_clear_ms": ZONE_DEFAULTS["candidate_clear_ms"],
        "resolve_ms": int(params["resolve_ms"]),
    }
    return {
        "schema_version": "1.0",
        "camera_id": camera_id,
        "view_id": view_id,
        "reference_frame": {"width": int(width), "height": int(height)},
        **CONFIG_DEFAULTS,
        "assets": ([{
            "asset_id": asset_id,
            "asset_type": "fire_extinguisher",
            "name": asset_name,
            "camera_id": camera_id,
            "view_id": view_id,
            "bbox_xyxy": [round(float(value), 6) for value in asset_bbox],
            "point": None,
            "reference_image_id": reference_image_id,
        }] if zone_type == "extinguisher_access" else []),
        "zones": [zone],
    }


def validate_zone_config(config: dict) -> dict:
    """Validate exactly as the engine does when it reads the file; return the canonical dict."""
    try:
        # Round-trip through JSON text so strict-mode parsing matches load_config on disk.
        model = validate_config(ZoneConfig.model_validate_json(json.dumps(config)))
    except ValueError as error:
        raise ZoneError(_friendly(error)) from error
    return json.loads(model.model_dump_json())


def _friendly(error: ValueError) -> str:
    errors = getattr(error, "errors", None)
    if callable(errors):
        parts = []
        for item in errors():
            where = ".".join(str(p) for p in item.get("loc", ()))
            parts.append(f"{where}: {item.get('msg')}" if where else str(item.get("msg")))
        return "; ".join(parts)
    return str(error)


class ZoneStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _paths(self, config_id: str) -> tuple[Path, Path]:
        check_id(config_id)
        return (self.settings.zones_dir / f"{config_id}.json",
                self.settings.zones_dir / f"{config_id}.meta.json")

    def _bundled(self) -> dict | None:
        path = self.settings.bundled_zones
        data = read_json(path)
        if not data:
            return None
        try:
            config = validate_zone_config(data)
        except ZoneError:
            return None
        return {"config_id": BUNDLED_ID, "name": f"Bundled calibration ({config['view_id']})",
                "source": "bundled", "video_id": None, "frame_t_ms": None,
                "created_at": None, "path": str(path), "config": config}

    def list(self) -> list[dict]:
        items = []
        for meta_path in sorted(self.settings.zones_dir.glob("*.meta.json")):
            meta = read_json(meta_path)
            config = read_json(meta_path.with_name(meta_path.name.replace(".meta.json", ".json")))
            if meta and config:
                items.append({**meta, "config": config})
        items.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        bundled = self._bundled()
        return items + ([bundled] if bundled else [])

    def get(self, config_id: str) -> dict:
        if config_id == BUNDLED_ID:
            bundled = self._bundled()
            if bundled:
                return bundled
            raise KeyError(config_id)
        config_path, meta_path = self._paths(config_id)
        meta, config = read_json(meta_path), read_json(config_path)
        if not meta or not config:
            raise KeyError(config_id)
        return {**meta, "config": config}

    def save(self, *, video: dict, name: str, camera_name: str, view_name: str,
             polygon: list[list[float]], params: dict | None, frame_t_ms: int | None,
             zone_type: str = "exit_access", asset_name: str | None = None,
             asset_bbox: list[float] | None = None,
             provenance: dict | None = None) -> dict:
        """Build a config for ``video``'s true frame size, validate it, and persist it."""
        config_id = new_id("zone")
        zone_id = slug(name, "exit_zone")
        camera_id = slug(camera_name, "cam01")
        # A fresh view_id per saved annotation: a config is bound to one framing.
        view_id = f"{slug(view_name, 'exit-view')}-{config_id.rsplit('-', 1)[-1]}"
        asset_id = (
            slug(f"{name}-extinguisher", "fire-extinguisher")
            if zone_type == "extinguisher_access"
            else None
        )
        config = validate_zone_config(build_zone_config(
            camera_id=camera_id, view_id=view_id, width=video["width"], height=video["height"],
            zone_id=zone_id, polygon=polygon, params=params, zone_type=zone_type,
            asset_id=asset_id, asset_name=asset_name, asset_bbox=asset_bbox,
            reference_image_id=(
                provenance.get("submitted_image_sha256")[:48]
                if provenance and provenance.get("submitted_image_sha256")
                else None
            )))
        config_path, meta_path = self._paths(config_id)
        meta = {"config_id": config_id, "name": (name or zone_id).strip()[:80] or zone_id,
                "source": "user", "video_id": video["video_id"], "frame_t_ms": frame_t_ms,
                "camera_name": camera_name, "view_name": view_name,
                "created_at": now_iso(), "path": str(config_path),
                "proposal_provenance": provenance}
        write_json(config_path, config)
        write_json(meta_path, meta)
        return {**meta, "config": config}


def public(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "path"}
