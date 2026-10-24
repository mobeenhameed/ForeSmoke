# Protected-area annotation workflow

The access zone is a manually defined floor polygon in the original camera frame. This is the best MVP
method because the protected region is a human decision and the camera is assumed fixed.
Automatic discovery would not know which floor area must remain accessible.

1. Record a clear frame with the camera mounted in its final position. A video is useful because
   the same recording can later test clear → chair placed → chair held → chair removed → clear.
2. Run `python -m foresmoke.annotate --source <video> --source-kind video --frame-index <clear-frame>`.
3. Click corners around the floor/access area, including the part a chair must not occupy.
4. Press Enter. The tool updates `configs/zones.json` but keeps the configured thresholds.
5. Visually check the coordinates on the next integrated preview. The shaded polygon is only a
   guide; the reasoning engine tests the chair's bottom bounding-box strip against it.
6. Do not move, zoom, crop, mirror, or rotate the camera after saving. If that changes, annotate
   again and use a new `view_id`.

## Register a fire extinguisher

Use the same annotator. The first click marks the extinguisher itself in magenta; subsequent clicks
draw its floor approach area. The marker identifies the protected asset and is not used as the
obstruction polygon.

```powershell
python -m foresmoke.annotate `
  --source clear_then_chair.mp4 `
  --source-kind video `
  --frame-index 0 `
  --output configs/zones.json `
  --camera-id cam01 `
  --view-id lobby-view-v1 `
  --zone-id lobby_extinguisher_access `
  --zone-name "Lobby extinguisher access" `
  --zone-type extinguisher_access `
  --asset-id extinguisher_lobby_01 `
  --asset-name "Lobby fire extinguisher" `
  --reference-image-id clear-frame-0
```

For a known normalized marker, pass either `--asset-point x,y` or
`--asset-bbox x1,y1,x2,y2`; then every click is treated as an access-polygon corner. Saving appends
the asset and zone while preserving existing zones, assets, and thresholds. Reusing the same
`--zone-id` updates that polygon rather than creating a duplicate.

The extinguisher marker may be on a wall, but the access polygon must be the floor area needed to
approach it. Pixel geometry is not a physical or regulatory clearance measurement.

The annotation tool saves normalized values, so a matching aspect ratio at another resolution is
accepted. It rejects no polygon topology at click time; the reasoning configuration loader checks
for self-intersection and zero-area polygons when the demo starts. Draw a simple polygon and do
not repeat the first point as the last point.
