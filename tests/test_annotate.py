import json
import pytest
from foresmoke.annotate import display_to_normalized, fit_view, normalized_to_display, update_zone_file


def test_annotation_coordinate_round_trip():
    scale, left, top, _, _ = fit_view(1920, 1080, 1000, 700)
    point = [0.25, 0.75]
    x, y = normalized_to_display(point, scale, left, top, 1920, 1080)
    actual = display_to_normalized(x, y, scale, left, top, 1920, 1080)
    assert actual[0] == pytest.approx(0.25, abs=0.001)
    assert actual[1] == pytest.approx(0.75, abs=0.001)


def test_clicks_outside_letterboxed_frame_are_ignored():
    scale, left, top, _, _ = fit_view(1920, 1080, 1000, 700)
    assert display_to_normalized(left - 1, top, scale, left, top, 1920, 1080) is None


def test_annotation_preserves_zone_thresholds(tmp_path):
    path = tmp_path / "zones.json"
    path.write_text(json.dumps({
        "schema_version": "1.0", "camera_id": "old", "view_id": "old-view",
        "reference_frame": {"width": 10, "height": 10}, "max_observation_gap_ms": 1000,
        "live_stale_after_ms": 2000, "zones": [{
            "zone_id": "east_exit", "hazard_type": "blocked_exit", "polygon": [[0, 0], [1, 0], [1, 1]],
            "candidate_labels": ["chair"], "min_confidence": 0.35,
            "footprint_height_fraction": 0.2, "enter_overlap": 0.3, "exit_overlap": 0.15,
            "person_occlusion_ratio": 0.2, "confirm_ms": 5000, "candidate_clear_ms": 1000,
            "resolve_ms": 3000,
        }]
    }))
    data = update_zone_file(path, [[0.1, 0.2], [0.8, 0.2], [0.8, 0.9]],
                            camera_id="cam01", view_id="view-v2", width=1920, height=1080)
    assert data["zones"][0]["enter_overlap"] == 0.3
    assert data["zones"][0]["polygon"] == [[0.1, 0.2], [0.8, 0.2], [0.8, 0.9]]
    assert data["reference_frame"] == {"width": 1920, "height": 1080}
