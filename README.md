# ForeSmoke — merged perception and reasoning

Person 1 camera package plus Person 2 reasoning branch. Processing runs locally on your Mac.

## Install on macOS

Use Python 3.11 or newer. Open Terminal in this extracted folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[perception,reasoning,dev]'
python -m pytest -q
python run_reasoning_demo.py
```

The demo uses synthetic observations and confirms at 5000 ms, resolves at 9200 ms.
Model weights download on the first camera run if absent. No weights or virtual environment are bundled.

## Camera detection

```bash
python -m foresmoke.perception --source 1 --source-kind live --camera-id cam01 --view-id exit-view-v1 --output runs/camera-01/observations.jsonl --preview
```

Replace `1` with your working iPhone camera index. Camera selection is independent of Photo Booth.

## Annotate the exit-access zone

Use a clear frame from the fixed camera or recorded video. The annotator opens the selected
frame, lets you click the floor/access corners, and writes normalized coordinates into the
existing zone configuration while preserving the reasoning thresholds:

```bash
python -m foresmoke.annotate --source clear_then_chair.mp4 --source-kind video \
  --frame-index 0 --output configs/zones.json --camera-id cam01 --view-id exit-view-v2
```

Click at least three corners around the floor/access area that must remain clear. Press `U` to
undo the last point, `R` to reset, `Enter` to save, or `Esc` to cancel. Do not outline the exit
sign; outline the usable floor/access region. Keep the camera fixed after annotation. Use a new
view ID if the camera is moved, cropped, rotated, or mirrored.

For a single image, use `--source-kind image`. For a live camera, use `--source-kind live` and
the numeric camera index. The window scales large videos for display but saves coordinates in
the original frame's coordinate system.

The same annotator can append a registered fire extinguisher and its separate floor approach
polygon. Existing exit-only files remain valid. See
[docs/MULTI_HAZARD.md](docs/MULTI_HAZARD.md) and [docs/ANNOTATION.md](docs/ANNOTATION.md) for the
configuration contract, exact command, synthetic demo, and recording guide.

## Combined live demonstration

`configs/zones.json` is a saved calibration for the bundled chair footage (464×832, `exit-view-v2`). Calibrate a new zone for any other fixed camera view before interpreting results. The original synthetic test zone lives in `tests/fixtures/zones.json`. See `docs/CALIBRATION.md`. Moving, cropping or mirroring the camera requires a new calibration and view ID.

```bash
python -m foresmoke.demo --source 1 --source-kind live --zones configs/zones.json --run-dir runs/live-01 --preview
```

Press q in the preview or Ctrl+C to stop. Use a new run directory each time. The preview shows detections, zone boundaries and phase / observation status. A stale feed can display CONFIRMED / UNKNOWN; shutting down does not resolve an incident. Recordings, events and snapshots are separate JSONL files in the run directory.

To save synchronized video evidence, add `--record-video`:

```bash
python -m foresmoke.demo --source 1 --source-kind live --zones configs/zones.json --run-dir runs/live-01 --preview --record-video
```

The run directory then contains:

- `raw_footage.mp4`: clean, unannotated frames received from the iPhone camera, recorded at 15 fps by default.
- `combined_three_panel.mp4`: all three views synchronized horizontally.

The comparison video uses the processed observation cadence, five frames per second by default, so every frame aligns with one JSONL observation and reasoning result. Change processing and comparison cadence with `--target-hz`, for example `--target-hz 8`. Higher values increase model load. Change the clean recording cadence with `--raw-fps 20`. The MP4 files contain no audio. Here “raw” means the clean camera image before ForeSmoke overlays; Continuity Camera/OpenCV does not expose iPhone sensor RAW video.

Add `--record-panel-videos` if you also want separate `detections.mp4` and `final_results.mp4` files. This creates two extra encoders, so the default records only the clean source and combined comparison to reduce load on the Mac.

The three-panel video uses 640-pixel-wide panels by default, producing a compact 1920-pixel-wide comparison. Use `--panel-width 800` for larger panels if the Mac keeps up.

Video input also works: use `--source demo.mp4 --source-kind video --source-fps 30` with the video's actual constant FPS. Do not use a camera calibration for differently framed footage.

## Optional Kiro visual review

Add `--visual-review` to supplement the deterministic incident engine with bounded asynchronous
image review by the official `kiro-cli`. The feature is disabled by default and never changes
engine state. It saves immutable evidence under the run directory and appends validated status
history to `reviews.jsonl`. Kiro CLI setup, model and credit notes, security restrictions, all
review flags, and the frontend contract are documented in [docs/VISUAL_REVIEW.md](docs/VISUAL_REVIEW.md).

## Replay saved observations

```bash
python -m foresmoke.reasoning --input runs/camera-01/observations.jsonl --zones configs/zones.json --events runs/replay-01/events.jsonl --snapshots runs/replay-01/states.jsonl
```

Replay preserves source times and disables the wall-clock watchdog. Results using the synthetic example zone are only software checks, not findings about your exit.

## Integration notes

Person 2's strict shared contract is canonical: FrameMeta, string UUIDs, list boxes/detections, explicit schema_version, and Candidate.zone_overlap. Both components import the same module. Perception and reasoning extras are combined; all tests are discovered.

The integrated runner runs inference in a worker and keeps reasoning and OpenCV GUI on the main thread. It logs snapshots during watchdog ticks even when inference stalls. Live queue capacity is two; offline observations are not dropped. Invalid packets break continuity; producer failures return a nonzero exit status.

Raw detection and tracked inference use separate model instances so tracking callbacks cannot suppress unmatched current boxes. This retains a two-inference-pass cost. Measure speed and memory on your Mac. Real camera/GPU and GUI operation have not been validated in this Linux build environment.

Original Person 2 documentation and synthetic test fixtures are preserved. See docs/MERGE_REPORT.md for verification.
