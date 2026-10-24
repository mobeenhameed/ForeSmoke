# Design — ForeSmoke Webapp MVP

## Overview

A FastAPI backend serves a single-page frontend and orchestrates the existing ForeSmoke CLI.
The backend is a thin layer: it probes uploaded videos, persists zone configs that conform to
the `ZoneConfig` schema, spawns `python -m foresmoke.demo` as a subprocess per analysis run,
tracks that subprocess, and serves the resulting JSONL plus the source video. It never parses
or recomputes incident logic — it streams the engine's `states.jsonl`/`events.jsonl` to the
browser untouched.

The frontend renders the three-area workspace, draws/validates zone polygons, maps engine
states to plain-language status, and reviews incidents with seek-to-moment.

### Frontend technology choice: plain HTML/CSS/JS

Chosen over React. Justification:
- The app is a single page with a handful of views (sidebar, player+timeline, right panel).
  There is no routing, no large component tree, and no shared-state complexity that a
  framework would simplify.
- It avoids a Node/npm build toolchain in a Python repo. The backend can serve one static
  `index.html` + `app.js` + `styles.css` directly, keeping the launch story to a single
  Python command against the existing venv — directly serving Requirement 8.
- Canvas-based polygon drawing and `<video>` seeking are DOM-native; a framework adds
  indirection without benefit here.
- The JSONL data contract is read-only and small per run; vanilla fetch + render is
  sufficient. If the app grows (multi-building, accounts — explicitly deferred), React can be
  revisited.

The JS is organized into small ES modules (no bundler, loaded via `<script type="module">`):
`api.js`, `player.js`, `annotator.js`, `status.js`, `timeline.js`, `incidents.js`, `app.js`.

---

## Architecture

```
Browser (single page, vanilla JS modules)
  │  REST (JSON) + multipart upload + polling
  ▼
FastAPI app  (foresmoke/webapp/server.py)
  ├── /api/videos           upload, probe, list, serve bytes
  ├── /api/zones            validate + save ZoneConfig, load
  ├── /api/runs             start (spawn CLI), progress, cancel, list, serve JSONL
  ├── /api/runs/{id}/thumb  evidence thumbnail (seek video to t_ms)
  └── /  (static)           index.html, app.js, styles.css
  │
  ├── probe.py     OpenCV VideoCapture → duration/width/height/fps
  ├── zones.py     build ZoneConfig dict, validate via foresmoke.reasoning.load_config
  ├── runner.py    subprocess.Popen(python -m foresmoke.demo ...), progress, cancel
  └── store.py     filesystem layout: uploads/, runs/<id>/, run registry
  │
  ▼  spawns (never imports engine logic for decisions)
python -m foresmoke.demo  →  runs/<id>/{observations,states,events}.jsonl
```

### Why subprocess, not in-process

Requirement 4 of the integration rules mandates spawning the exact CLI. `foresmoke.demo`
drives perception (model inference) + reasoning and writes the three JSONL files and refuses a
non-empty run dir. Running it as a subprocess keeps the webapp fully decoupled from engine
internals and matches how a reviewer would otherwise run it. The backend reads the engine's
output files; it does not instantiate `ReasoningEngine` to make decisions.

The one sanctioned in-process use of the package is **validation**: `zones.py` calls
`foresmoke.reasoning.load_config` on the config we are about to save, so an invalid polygon is
caught server-side with the same Shapely check the engine uses. This validates; it does not
make incident decisions.

---

## Filesystem layout

```
foresmoke/webapp/
  __init__.py
  server.py          FastAPI app + route wiring
  probe.py           video probing (OpenCV)
  zones.py           ZoneConfig assembly + validation
  runner.py          analysis subprocess manager (RunManager)
  store.py           paths, ids, run registry, listing
  models.py          pydantic request/response models (webapp-local, not the contract)
  static/
    index.html
    styles.css
    app.js
    api.js
    player.js
    annotator.js
    status.js
    timeline.js
    incidents.js

webapp_data/          (gitignored; created at runtime)
  uploads/<video_id>/source.<ext> + meta.json
  zones/<zone_id>.json

runs/<run_id>/        existing convention; CLI writes JSONL here
scripts/run_webapp.sh launch script
Makefile              `make webapp` target
docs/WEBAPP.md
```

`runs/` is already gitignored. `webapp_data/` is added to `.gitignore`. Uploaded videos are
stored under `webapp_data/uploads/`; a run records which upload and zone config produced it.

---

## Backend components

### probe.py

```
probe_video(path) -> {duration_s, width, height, fps, frame_count}
```
Opens the file with `cv2.VideoCapture`, reads `CAP_PROP_FPS`, `CAP_PROP_FRAME_WIDTH/HEIGHT`,
`CAP_PROP_FRAME_COUNT`; `duration_s = frame_count / fps`. Raises a `ProbeError` with a clear
message if the file won't open or fps ≤ 0 (mirrors `sources.py` `invalid_video_fps`). OpenCV is
already a declared perception dependency, so no new runtime dependency is introduced.

### zones.py

```
build_zone_config(*, camera_id, view_id, width, height, zone_id, polygon, params) -> dict
validate_zone_config(config_dict) -> None   # raises on invalid
save_zone_config(config_dict) -> zone_id/path
```
`build_zone_config` assembles a dict matching `ZoneConfig`: `schema_version` "1.0",
`reference_frame` = probed width/height, `max_observation_gap_ms` 1000, `live_stale_after_ms`
2000, and one zone with the params-panel fields plus annotator defaults
(`footprint_height_fraction` 0.20, `enter_overlap` 0.30, `exit_overlap` 0.15,
`person_occlusion_ratio` 0.20, `candidate_clear_ms` 1000). Validation writes the dict to a temp
file and calls `foresmoke.reasoning.load_config`, which runs both the pydantic contract checks
and the Shapely geometry check. Any `ValueError` becomes an HTTP 422 with the message. This is
the authoritative server-side guard; the client pre-validates for fast feedback but the server
is the gate.

### runner.py — RunManager

Holds the active/known runs in memory keyed by `run_id`.

```
start(video_path, zones_path, source_fps, duration_s, target_hz=5) -> run_id
  - run_dir = runs/<run_id>  (uuid4); must be fresh/empty
  - cmd = [python, -m, foresmoke.demo,
           --source <video_path>, --source-kind video,
           --source-fps <source_fps>, --zones <zones_path>,
           --run-dir <run_dir>, --target-hz 5]
  - Popen(cmd, cwd=repo_root, stdout/stderr -> run_dir/stdout.log)
  - record expected_lines = ceil(duration_s * target_hz)

status(run_id) -> {state, processed_lines, expected_lines, progress, returncode, error}
  - state: running | done | failed | cancelled
  - processed_lines = line count of run_dir/observations.jsonl (cheap tail/count)
  - progress = min(processed_lines / expected_lines, 0.99) while running; 1.0 when done
  - when poll() is not None: done if returncode==0 else failed
    (nonzero exit = failure, per Requirement 3.4; demo.py returns 2 on failure)

cancel(run_id) -> terminate subprocess (SIGTERM, then kill), mark cancelled
```

The Python interpreter used is `sys.executable` (the active venv's python), guaranteeing the
subprocess uses the same environment the server runs in, satisfying "uses the repo's existing
venv and package".

Progress is intentionally an estimate (line count vs `duration_s × target_hz`) exactly as the
task specifies. It is capped below 100% until the process actually exits so completion is driven
by the real exit code, not the estimate.

### store.py

Allocates ids (uuid4), resolves repo root, lays out `webapp_data/`, lists runs by scanning
`runs/` for directories containing the three JSONL files and any recorded run metadata.

### server.py — REST surface

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/videos` | multipart upload → store + probe → `{video_id, duration_s, width, height, fps}` |
| GET | `/api/videos` | list uploaded videos |
| GET | `/api/videos/{id}/file` | stream video bytes (range requests for scrub/seek) |
| POST | `/api/zones/validate` | validate a candidate config → 200 or 422 w/ message |
| POST | `/api/zones` | validate + save → `{zone_id}` |
| GET | `/api/zones/{id}` | load a saved config |
| POST | `/api/runs` | `{video_id, zone_id}` → start subprocess → `{run_id}` |
| GET | `/api/runs` | list runs with metadata/state |
| GET | `/api/runs/{id}` | run status/progress |
| POST | `/api/runs/{id}/cancel` | cancel |
| GET | `/api/runs/{id}/observations` | serve observations.jsonl (text) |
| GET | `/api/runs/{id}/states` | serve states.jsonl (text) |
| GET | `/api/runs/{id}/events` | serve events.jsonl (text) |
| GET | `/api/runs/{id}/thumb?t_ms=` | JPEG frame seeked to t_ms |
| GET | `/` + static | SPA assets |

"Progress channel": polling `GET /api/runs/{id}` on a short interval. Polling (not WebSocket/SSE)
is chosen because progress is itself defined as a derived line-count estimate and the run is a
local subprocess; polling keeps the backend dependency-free and is simpler to reason about. The
task explicitly describes progress as "polling the growing observations.jsonl line count".

Video byte serving supports HTTP range requests so the `<video>` element can scrub and the
thumbnail endpoint and client seeks land on the right source `t_ms`.

---

## Frontend design

### Layout (one workspace, three areas)

```
┌───────────────┬───────────────────────────────────┬─────────────────┐
│ LEFT SIDEBAR  │ CENTER                            │ RIGHT PANEL     │
│ • Upload /    │  ┌─────────────────────────────┐  │ • Current zone  │
│   select      │  │  <video> + <canvas> overlay │  │   status chip   │
│ • Camera &    │  │  zone outline + relevant    │  │ • Active        │
│   view name   │  │  detections                 │  │   incident      │
│ • Saved zones │  └─────────────────────────────┘  │ • Incident      │
│ • Params      │  ┌─────────────────────────────┐  │   history       │
│ • Run         │  │ incident timeline (by t_ms) │  │   (cards)       │
│   analysis    │  └─────────────────────────────┘  │                 │
└───────────────┴───────────────────────────────────┴─────────────────┘
```

Video + timeline (center) and status/incidents (right) are the dominant regions by width and
visual weight (Requirement 7.4). A header keeps a persistent "Source time" clock distinct from
any processing/progress indicator (Requirement 7.3).

### player.js
Wraps `<video>` and an overlaid `<canvas>` sized to the video's displayed box. Exposes
`seek(t_ms)`, `pauseAt(t_ms)`, play/pause/scrub. The canvas redraws on `timeupdate` and on
resize, mapping normalized coordinates → displayed pixels using the video's rendered box (so
overlays stay aligned under letterboxing).

### annotator.js
Canvas polygon tool active only while paused. Click adds a vertex; drag moves a vertex; a
delete affordance removes one; undo pops the last; name field sets `zone_id`. On every change it
recomputes normalized coordinates against the **true** video dimensions (probed width/height),
not the displayed size.

Client-side pre-validation (mirrors the engine, which is still the final gate):
- ≥3 vertices
- all vertices within [0,1]
- non-zero area (shoelace area > epsilon)
- no self-intersection (pairwise non-adjacent segment intersection test)
- distinct vertices; polygon stored without a repeated closing vertex

Save posts to `/api/zones`; a 422 surfaces the server message inline. The saved polygon remains
drawn during playback (Requirement 2.6).

The params panel exposes `candidate_labels` (default chair/backpack/suitcase, person
disallowed), `confirm_ms` (5000), `resolve_ms` (3000), `min_confidence` (0.35). Other required
fields are filled from annotator defaults and are not user-facing in the MVP.

### status.js — state mapping (single source of truth in the UI)

```
function mapStatus(zoneState) {
  if (zoneState.observation_state === 'unknown')
    return { label: 'View unavailable or obscured', color: 'gray', key: 'unknown' };
  switch (zoneState.phase) {
    case 'CLEAR':     return { label: 'Access clear',          color: 'green', key: 'clear' };
    case 'POSSIBLE':  return { label: 'Checking obstruction',  color: 'amber', key: 'possible' };
    case 'CONFIRMED': return { label: 'Obstruction confirmed', color: 'red',   key: 'confirmed' };
    case 'RESOLVED':  return { label: 'Incident resolved',     color: 'blue',  key: 'resolved' };
  }
}
```

`unknown` takes precedence over phase color (Requirement 5.4). Every chip renders both an icon
and text, never color alone (accessibility; Requirement 5.1). Confirmation progress text:
`(positive_evidence_ms/1000).toFixed(1) + ' / ' + (confirm_ms/1000).toFixed(1) + ' seconds of evidence'`,
values read straight from the latest zone state and the saved zone's `confirm_ms`. If a
CONFIRMED incident's latest state is `unknown`, the active-incident card keeps showing the
incident and adds an "uncertain" badge; it never synthesizes a resolution (Requirement 5.3).

### timeline.js
Horizontal track under the player scaled to `duration_s`. Plots phase-change segments from
`states.jsonl` colored by the status map, and incident confirm/resolve markers from
`events.jsonl`, all positioned by source `t_ms`. Markers and segments are clickable → `seek(t_ms)`.

### incidents.js
Groups `events.jsonl` by `incident_id`. Each card shows zone_id, object labels from
`evidence.objects`, confirmation time (`confirmed_at_ms`), resolution time (`resolved_at_ms` or
"ongoing"), and a thumbnail `<img src="/api/runs/{id}/thumb?t_ms={evidence.t_ms}">`. Clicking the
card (or its timeline marker) calls `player.seek(evidence.t_ms)`.

### Debug detection toggle
Default overlay filters detections to the active zone's `candidate_labels`. A debug checkbox
switches to drawing every detection in `observations.jsonl` for the current frame (Requirement
7.2).

---

## Data flow — end to end

1. Upload video → `POST /api/videos` → `{video_id, duration_s, width, height, fps}`; player
   loads `/api/videos/{id}/file`.
2. Pause on a clear frame → draw polygon → name → Save → `POST /api/zones` (validated
   server-side) → `{zone_id}`.
3. Run analysis → `POST /api/runs {video_id, zone_id}` → backend spawns the demo CLI with
   `--source-fps = probed fps`, `--target-hz 5`, fresh `runs/<id>` → `{run_id}`.
4. UI polls `GET /api/runs/{id}` → progress bar from line-count estimate; Cancel →
   `POST .../cancel`.
5. On `done` → UI fetches `states.jsonl`, `events.jsonl`, `observations.jsonl`; renders status,
   timeline, incident cards; video source switches to the run's source video for seeking.
6. Click incident/marker → `player.seek(t_ms)`; thumbnails via `/thumb?t_ms=`.

---

## Sample videos and pre-analyzed sample run

### Bundled sample videos

The repo now ships four real recorded clips at the top level: `chair.mp4`, `chair_2.mp4`,
`chair_back.mp4`, `chair_front.mp4`. All are **464×832 portrait, 60 fps**, 17–35 s. Their
dimensions exactly match the `reference_frame` in the existing `configs/zones.json`, so that
bundled zone is a valid known-good config for these clips and can seed the "saved zones" list.
These clips drive the real end-to-end acceptance flow (upload/select → annotate → analyze →
review) and are the recommended quick-clip inputs (a 17.5 s clip at `--target-hz 5` yields
≈ 88 observations, so inference stays fast).

### Pre-analyzed sample run (build step 1 enabler)

`runs/` is gitignored and no `runs/exit-test-01/` exists in the clone, so build step 1 (viewer
before analysis) still needs a seed that does not wait on CPU inference. Two complementary
seeds:

1. `foresmoke/webapp/sample.py` (run automatically at server start) replays the repo's existing synthetic fixture
   (`foresmoke.reasoning.fixtures` "blocked_then_clear") through the real engine CLI
   (`python -m foresmoke.reasoning`) into `runs/exit-test-01/`, producing genuine
   `states.jsonl` + `events.jsonl` with the canonical CONFIRMED@5000ms / RESOLVED@9200ms
   incident, plus a synthesized `observations.jsonl` from the same fixture. This uses only the
   existing engine and fixtures (no new decision logic). This seed has no source video; the
   viewer degrades gracefully (Requirement 4.4): timeline, status, and incident cards render,
   and thumbnails/seek are disabled with a "no video for this run" note.
2. Optionally, a one-time real analysis of `chair.mp4` with the bundled `configs/zones.json`
   produces a run that has a source video, exercising thumbnails and seek-to-moment before the
   in-UI analysis path is wired. This is generated on demand, not committed (`runs/` is
   gitignored and inference requires model weights).

The synthetic seed is the guaranteed, dependency-light enabler for build step 1; the real clip
is used for full acceptance once analysis wiring (stage 3) lands.

---

## Error handling

- Probe failure, invalid config (422 from Shapely/pydantic), subprocess spawn failure, nonzero
  exit, cancelled run — each returns a structured `{error}` the UI renders inline without
  crashing.
- Missing/partial run files during polling are treated as "still running / 0 lines", not errors.
- Range-request and seek edge cases (seek past EOF) clamp to valid bounds.

## Testing strategy

- Backend unit tests (pytest, already a dev dep): `probe_video` on a tiny generated clip;
  `build_zone_config` + `validate_zone_config` accept a good polygon and reject a
  self-intersecting / zero-area / out-of-range one (asserting the engine's own validator is the
  gate); `RunManager` progress math and state transitions using a fake fast subprocess;
  JSONL-serving endpoints return bytes unaltered.
- A seed-run test asserts `seed_sample_run.py` yields a CONFIRMED and a RESOLVED event.
- Manual acceptance (documented in `docs/WEBAPP.md`): the full Requirement 9 flow in a browser.
- Tests do not assert incident logic correctness — that is the engine's own 86-test suite.

## Security / safety notes

- Uploaded filenames are never used as paths; the server generates ids and extensions are
  whitelisted. Subprocess args are passed as a list (no shell), so no command injection via
  filenames.
- The server binds to localhost by default (single-reviewer MVP).
- No deletion endpoints in the MVP; runs accumulate under `runs/` (cleanup deferred).
