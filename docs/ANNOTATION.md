# Exit-zone annotation workflow

The zone is a manually defined polygon in the original camera frame. This is the best MVP
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

The annotation tool saves normalized values, so a matching aspect ratio at another resolution is
accepted. It rejects no polygon topology at click time; the reasoning configuration loader checks
for self-intersection and zero-area polygons when the demo starts. Draw a simple polygon and do
not repeat the first point as the last point.
