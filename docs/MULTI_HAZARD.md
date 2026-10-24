# Exit and fire-extinguisher access monitoring

ForeSmoke uses one deterministic pipeline for two protected-area purposes:

- `exit_access` derives `blocked_exit`.
- `extinguisher_access` derives `blocked_extinguisher_access`.

Users configure `zone_type`; `hazard_type` is derived centrally and contradictory values are
rejected. A legacy zone with neither the new asset fields nor `zone_type` remains `exit_access`.
Both purposes retain the existing detector filters, footprint overlap, hysteresis, timers,
person-occlusion handling, and incident lifecycle.

## Configuration contract

`ZoneConfig.assets` is an optional list and defaults to empty. A registered asset contains:

- stable `asset_id`;
- `asset_type: fire_extinguisher`;
- human-readable `name`;
- the configuration's `camera_id` and `view_id`;
- exactly one normalized `bbox_xyxy` or `point`;
- optional `reference_image_id`.

Zones add `name`, `zone_type`, and nullable `protected_asset_id`. An
`extinguisher_access` zone must reference a same-camera/view `fire_extinguisher` asset. Its polygon
is the projected floor approach area, not the extinguisher marker. Overlapping zones are allowed
and maintain independent incidents.

See `configs/multi_hazard_demo.json` for a clearly labeled synthetic example.

## Synthetic demonstration

```powershell
.\.venv\Scripts\python.exe run_multi_hazard_demo.py
```

The synthetic chair occupies only the extinguisher approach zone. It confirms at source time
5000 ms and resolves at 9200 ms; the exit remains clear. Outputs are written to
`runs/multi-hazard-synthetic/`.

For real footage, first register and annotate the asset as documented in `docs/ANNOTATION.md`,
then use the normal runner:

```powershell
.\.venv\Scripts\python.exe -m foresmoke.demo `
  --source extinguisher_test.mp4 `
  --source-kind video `
  --source-fps 30 `
  --zones configs/zones.json `
  --run-dir runs\extinguisher-test
```

Recording guide:

1. Keep the camera fixed.
2. Capture a clear baseline showing the registered extinguisher and approach area.
3. Place a chair in the marked floor approach area.
4. Leave it longer than `confirm_ms`.
5. Remove it.
6. Keep the area clear longer than `resolve_ms`.

## Output additions

Newly emitted `HazardEvent` and `ZoneState` records use `schema_version: "1.1"`. Their 1.0 fields
retain their existing meanings, and the validators continue to accept legacy 1.0 records by deriving
the new context defaults. `HazardEvent` now includes `zone_name`, `zone_type`, `protected_asset_id`,
`protected_asset_name`, and deterministic `remediation`. `ZoneState` exposes the same context plus
`monitoring_objective`. Existing IDs, source timestamps, phases, and evidence fields are unchanged.

Example confirmed fields:

```json
{
  "schema_version": "1.1",
  "event_type": "incident_confirmed",
  "hazard_type": "blocked_extinguisher_access",
  "zone_id": "lobby_extinguisher_access",
  "zone_name": "Lobby extinguisher access",
  "zone_type": "extinguisher_access",
  "protected_asset_id": "extinguisher_lobby_01",
  "protected_asset_name": "Lobby fire extinguisher",
  "phase": "CONFIRMED",
  "t_ms": 5000,
  "remediation": "Remove the obstruction from the marked access area for Lobby fire extinguisher."
}
```

The resolution event retains the same hazard, zone, asset, and remediation fields, with
`event_type: incident_resolved`, `phase: RESOLVED`, and `resolved_at_ms` set.

## Visual-review/frontend handoff

Consumers do not need to infer purpose from names. Read these stable fields from snapshots/events:

- `zone_type` and derived `hazard_type`;
- protected asset ID/name;
- `remediation`;
- `monitoring_objective`;
- existing candidates, overlap, timing, phase, and observation quality.

Visual evidence manifests additionally include the complete registered asset marker and access-zone
polygon. The extinguisher objective explicitly says not to infer a missing extinguisher merely
because it is not visible.

This feature does not detect missing extinguishers, estimate physical clearance, determine code
compliance, discover assets automatically, or add detector classes. Validation is synthetic unless
the user supplies and calibrates real footage.
