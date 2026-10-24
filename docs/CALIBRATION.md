# Replace the synthetic zone before the real demo

configs/zones.json is copied from the brief: normalized x=0.30..0.80, y=0.55..0.95 on
a 1280x720 reference. It is **not calibrated to a real exit**.

1. Save an original, unmirrored frame from Person 1's fixed camera.
2. Identify the floor/access region that should remain clear; do not outline the exit sign.
3. Record at least three distinct polygon vertices in order around the region.
4. Normalize pixel vertices: x / original_width and y / original_height.
5. Update reference width/height and polygon; omit a repeated closing vertex.
6. Assign a new view_id and use it in both the config and perception packets.
7. Restart acquisition and reasoning. Configuration is not hot-reloaded.
8. Overlay the polygon and bottom 20% strip of a chair's box on the original frame.
9. Check a chair inside, outside, and at the edge. Inspect --debug overlap values.
10. Record the full lifecycle and a camera-failure case.

Invalid/self-crossing/zero-area polygons are rejected. Camera/view mismatches and aspect-ratio
differences above 1% are rejected. Different resolution is accepted for identical framing/aspect.

Camera motion/cropping/mirroring is not automatically detected. Recalibrate after a changed
view. Image overlap does not measure physical or legal passage width.
