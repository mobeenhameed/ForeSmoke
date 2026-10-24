"""Load configuration and validate actual polygon geometry."""
from pathlib import Path
from shapely.geometry import Polygon
from shapely.validation import explain_validity
from foresmoke.shared.contracts import ZoneConfig

def validate_config(config: ZoneConfig) -> ZoneConfig:
    config = ZoneConfig.model_validate(config).model_copy(deep=True)
    for zone in config.zones:
        polygon = Polygon(zone.polygon)
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError(f"zone {zone.zone_id}: invalid polygon: {explain_validity(polygon)}")
    return config

def load_config(path: str | Path) -> ZoneConfig:
    return validate_config(ZoneConfig.model_validate_json(Path(path).read_text(encoding="utf-8-sig")))

