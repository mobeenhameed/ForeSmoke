# Implementation Plan — ForeSmoke Webapp MVP

Build order follows the task's five stages: (1) player + saved-run viewer against a seeded
sample run, (2) annotation + zone saving, (3) analysis progress + subprocess wiring, (4)
incidents/timeline/thumbnails, (5) polish. Each task is incremental and leaves the app runnable.

- [x] 0. Environment, package skeleton, and launch path
  - [x] 0.1 Create the `.venv` with `python3 -m venv .venv`, install `-e '.[perception,reasoning,dev]'`, and add FastAPI + uvicorn + python-multipart to the webapp's dependencies (declare a `webapp` extra in `pyproject.toml`).
  - [x] 0.2 Create `foresmoke/webapp/` package (`__init__.py`, empty `static/`) and `store.py` with repo-root resolution, id generation, and `webapp_data/` layout; add `webapp_data/` to `.gitignore`.
  - [x] 0.3 Create a minimal `server.py` (FastAPI app, serves `static/index.html` at `/` and static assets) plus `scripts/run_webapp.sh` and a `make webapp` target that launch uvicorn with `sys.executable`/the venv. Verify the server starts and serves a placeholder page.
  - _Requirements: 8.1, 8.3_

- [x] 1. Video player + saved-run viewer (works before any analysis)
  - [x] 1.1 Implement `foresmoke/webapp/sample.py` (`make webapp-sample`) that replays the `blocked_then_clear` fixture via `python -m foresmoke.reasoning` into `runs/exit-test-01/` (states + events) and synthesizes `observations.jsonl` from the same fixture. Assert it produces CONFIRMED and RESOLVED events.
  - [x] 1.2 Backend: `store.py` run listing + `GET /api/runs`, `GET /api/runs/{id}/states|events|observations` serving JSONL bytes unaltered; `GET /api/videos/{id}/file` with HTTP range support (used later — stub video-less run handling now).
  - [x] 1.3 Frontend shell: `index.html` three-area layout, `styles.css`, `api.js`; `player.js` (`<video>` + overlay `<canvas>`, play/pause/scrub/seek, normalized→displayed coordinate mapping).
  - [x] 1.4 `status.js` state map (label+color+text, unknown precedence) and right-panel current-status chip; render current zone status from the seeded run's `states.jsonl`.
  - [x] 1.5 Load `runs/exit-test-01` in the UI and confirm states render and the player degrades gracefully with no source video (Requirement 4.4).
  - _Requirements: 1.5, 4.1, 4.2, 4.4, 5.1, 5.2, 5.4, 7.1, 7.3, 7.4, 9.3_

- [x] 2. Video upload/probe + polygon annotation + zone saving
  - [x] 2.1 Backend: `probe.py` (`probe_video`) and `POST /api/videos` (store upload, probe, return duration/width/height/fps); `GET /api/videos` list. Handle probe failure with a clear error.
  - [x] 2.2 Backend: `zones.py` (`build_zone_config`, `validate_zone_config` via `foresmoke.reasoning.load_config`), `POST /api/zones/validate`, `POST /api/zones`, `GET /api/zones/{id}`. Invalid polygon → 422 with message.
  - [x] 2.3 Frontend: upload control + display duration/dimensions; pause-to-annotate; `annotator.js` polygon drawing with add/undo/drag/delete/name; convert clicks to normalized coords against true (probed) dimensions.
  - [x] 2.4 Frontend: client-side polygon pre-validation (≥3 vertices, in-frame, non-zero area, no self-intersection); params panel (candidate_labels, confirm_ms 5000, resolve_ms 3000, min_confidence 0.35); Save → `/api/zones`; keep saved polygon visible during playback; surface 422 inline.
  - [x] 2.5 Backend tests: `build_zone_config`/`validate_zone_config` accept a valid polygon and reject self-intersecting/zero-area/out-of-range; `reference_frame` equals probed dimensions and a `view_id` is generated.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

- [x] 3. Analysis run: subprocess wiring + progress + cancel
  - [x] 3.1 Backend: `runner.py` `RunManager.start` spawning `python -m foresmoke.demo --source <uploaded> --source-kind video --source-fps <probed_fps> --zones <saved_zones.json> --run-dir runs/<id> --target-hz 5` via `sys.executable`, list args (no shell), fresh uuid run dir, stdout/stderr → log.
  - [x] 3.2 Backend: `status()` progress from `observations.jsonl` line count vs `ceil(duration_s*5)` (capped <1 while running); `done` on exit 0, `failed` on nonzero; `cancel()` terminates subprocess. Endpoints `POST /api/runs`, `GET /api/runs/{id}`, `POST /api/runs/{id}/cancel`.
  - [x] 3.3 Frontend: "Run analysis" button; poll status for live progress bar; Cancel button; completion message; note about CPU inference / short clips fastest. On done, auto-load the new run's JSONL and switch the player to the run's source video.
  - [x] 3.4 Backend tests: `RunManager` progress math and state transitions (done/failed/cancelled) using a fake fast subprocess; fresh-dir allocation.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.3_

- [x] 4. Incident cards, timeline, evidence thumbnails, seek-to-moment
  - [x] 4.1 Backend: `GET /api/runs/{id}/thumb?t_ms=` seeking the run's source video to `t_ms` and returning a JPEG (via OpenCV); graceful 404/placeholder for video-less runs.
  - [x] 4.2 Frontend: `timeline.js` — phase segments from states + confirm/resolve markers from events, positioned by source `t_ms`, clickable to seek.
  - [x] 4.3 Frontend: `incidents.js` — one card per `incident_id` with zone, `evidence.objects` labels, `confirmed_at_ms`, `resolved_at_ms`, thumbnail from `evidence.t_ms`; click card/marker → `player.seek`.
  - [x] 4.4 Frontend: active-incident handling when latest state is `unknown` — keep incident visible, add uncertainty badge, never fabricate resolution; confirmation-progress text from `positive_evidence_ms` vs `confirm_ms`.
  - [x] 4.5 Frontend: center overlay shows only `candidate_labels` detections by default; debug toggle shows all `observations.jsonl` detections.
  - _Requirements: 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 7.2_

- [x] 5. Presentation polish, docs, and end-to-end acceptance
  - [x] 5.1 Polish: emphasize video + status visually, keep source time distinct from processing time, responsive three-area layout, consistent status colors+icons+text.
  - [x] 5.2 Write `docs/WEBAPP.md`: setup (venv + webapp extra), launch (`make webapp` / `scripts/run_webapp.sh`), and the step-by-step review flow; note the seeded sample run.
  - [x] 5.3 Manual end-to-end acceptance using a bundled clip (`chair.mp4`, 464×832 @ 60fps): in-browser load video → draw valid zone → run analysis → review one confirmed-and-resolved incident with working seek-to-moment, zero JSON/terminal. Record the result in `docs/WEBAPP.md`.
  - _Requirements: 7.3, 7.4, 8.1, 8.2, 8.3, 9.1, 9.2, 9.3_
