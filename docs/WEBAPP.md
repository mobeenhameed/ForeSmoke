# ForeSmoke review webapp

A browser tool for reviewing exit obstructions in recorded video: load a clip, draw the exit
zone, run analysis, and review incidents. No JSON editing or terminal work is needed after launch.

The app opens on **Overview**, where a building manager sees open findings, confirmed findings,
reviewed recordings, and available videos. These totals use the latest completed analysis per
recording and exclude the synthetic sample. **Videos** lists footage from the GitHub branch and
local uploads. **Reviews** holds saved analyses. **Workspace** shows one review's video, timeline,
status and evidence; a completed review opens at its first confirmed finding. **New review** opens
the video, zone and analysis controls in the workspace. The synthetic sample is still available
in Reviews, clearly labeled as synthetic.

Search Reviews by recording or zone and filter for runs with or without findings. While analysis
runs, the top bar shows progress even when the setup drawer is closed.

The Videos tab automatically fetches the configured Git branch once when the page loads and has a
**Sync GitHub videos** button for later pushes. It uses the checkout's existing Git remote and
credentials, checks all video files in that branch (including subdirectories), and caches new or
changed files under `webapp_data/videos/` without overwriting the checkout. Defaults are
`origin` and `webapp-mvp`; set `FORESMOKE_GITHUB_BRANCH` to choose another branch. Sync requires
network access and a Git checkout with permission to fetch. Existing local videos remain usable
when GitHub is unavailable.

The webapp only shows what the engine reports and collects a zone config. Detection, timing and
the CLEAR / POSSIBLE / CONFIRMED / RESOLVED decisions come from the existing
`python -m foresmoke.demo` CLI, which the server runs as a subprocess for each analysis.

## Launch

- **Finder:** double-click `scripts/ForeSmoke.command`.
- **Terminal:** `make webapp` (or `./scripts/run_webapp.sh`).

The first launch creates `.venv` and installs `.[perception,reasoning,webapp,dev]`. The first
analysis downloads the YOLO weights (`yolo26n.pt`, about 5 MB). The app opens at
http://localhost:8000. It listens on localhost only and has no authentication, so don't expose it
on a shared network.

## Review flow

1. **Video:** pick a bundled clip (`chair.mp4`, `chair_2.mp4`, …) or upload one. The sidebar
   shows its duration, dimensions and fps, all probed with OpenCV, the same library perception uses.
2. **Camera & view:** set the names you want. Each saved zone gets a fresh `view_id`.
3. **Exit zone:** scrub to a clear frame and click **Draw a new zone on this frame**. Click the
   floor corners. Drag a corner to move it. Right-click a corner (or select it and press Delete)
   to remove it. Use Undo / Ctrl-Z to step back. Edges that cross turn red, and Save stays
   disabled until the polygon is valid. The server then re-checks the zone with the engine's own
   validator. Detection parameters default to chair/backpack/suitcase, confirm 5.0 s,
   resolve 3.0 s and min confidence 0.35.
   Alternatively, pause on a clear frame and click **Suggest access areas**. The app sends that
   exact frame to the official Kiro CLI and shows returned floor polygons as purple dashed drafts.
   Select a candidate, copy it into the normal vertex editor, adjust it if needed, and explicitly
   save it. A suggestion never replaces a saved zone or starts analysis automatically. Manual
   drawing remains available when Kiro is unavailable. For extinguisher access, the saved zone
   remains associated with the separately identified extinguisher asset.
4. **Run analysis:** progress counts analysed frames against duration × 5 Hz. Processing time
   (wall clock, dashed amber) is shown separately from source-video time (the blue clock and timeline).
   You can cancel a run. Each run gets its own `runs/<run-id>/` folder.
5. **Review:** the right panel shows zone status at the playhead, for example
   "2.4 / 5.0 seconds of evidence", plus the active incident and incident cards with evidence
   thumbnails. Clicking a card, a "Jump to…" button or a timeline marker seeks to that source `t_ms`.
   If a confirmed incident loses its view, it stays open with an uncertainty badge.

Tip: `chair_2.mp4` with a floor-area zone gives one confirmed (≈13.2 s) and resolved (≈18.8 s)
incident. It takes about 10 s to analyse on an Apple-silicon Mac.

## Status mapping

| Engine | Shown as |
|---|---|
| CLEAR | ✓ Access clear (green) |
| POSSIBLE | ◔ Checking obstruction (amber) |
| CONFIRMED | ! Obstruction confirmed (red) |
| RESOLVED | ↺ Incident resolved (blue) |
| observation_state = unknown | ? View unavailable or obscured (gray), shown instead of the phase colour |

## Sample run

On startup the server seeds `runs/exit-test-01` (run `make webapp-sample` to rebuild it). The
engine replays the synthetic `blocked_then_clear` fixture, which confirms at 5.0 s and resolves at
9.2 s. It has no video, so the viewer shows only the engine's geometry. This means the review
screen works before any analysis has been run.

## Where things live

- `foresmoke/webapp/`: FastAPI server (`server.py`), probing, zone store/validation, run manager,
  sample seeding, and `static/` (plain HTML/CSS/JS modules, no build step).
- `webapp_data/` (gitignored): uploads, saved zone configs, per-run metadata, logs and thumbnails.
- `runs/<run-id>/`: unmodified `observations.jsonl`, `states.jsonl`, `events.jsonl` from the CLI.
- Tests: `make webapp-test`.

## Verified

Checked on 2026-10-24 in headless Chrome, driven by Playwright. Starting from `chair_2.mp4`, I
drew a 4-corner zone through the UI and saved it. The analysis finished in 8 s and found one
incident, confirmed at 13.2 s and resolved at 18.8 s. Clicking the card seeked the video to
13.214 s, and the resolution marker seeked it to 18.817 s. I also checked the uncertainty badge
during occlusion at 13.8 s, the "1.2 / 5.0 seconds of evidence" text, and cancelling a run.
Safari and Firefox have not been tested.

## AI-assisted access-area setup

Install and authenticate the official `kiro-cli`, then launch the webapp normally. Proposal
requests use a fresh read-only agent context that can read only the submitted frame and manifest.
The default model is `claude-sonnet-5.5`.

Optional environment variables:

- `FORESMOKE_PROPOSAL_MODEL` (default `claude-sonnet-5.5`)
- `FORESMOKE_PROPOSAL_TIMEOUT_S` (default `90`)
- `FORESMOKE_PROPOSAL_QUEUE_CAPACITY` (default `3`)
- `FORESMOKE_PROPOSAL_MAX_IMAGE_DIMENSION` (default `1280`)

Proposal evidence and reproducibility metadata are stored under
`webapp_data/zone_proposals/<job-id>/`. The submitted image is resized without cropping or
letterboxing, and returned pixel coordinates are normalized by the application. The CLI's image
reader may perform undocumented internal processing, so every polygon must still be checked
against the frozen submitted frame.

These polygons are model-generated drafts, not verified floor segmentation. Their front boundary
is an estimated monitoring extent in an uncalibrated image; they do not establish physical
clearance, depth, fire-door identity, or regulatory compliance. Temporary obstructions should be
reported as visibility limitations rather than used to shrink the protected area.
