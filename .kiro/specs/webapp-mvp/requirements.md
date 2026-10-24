# Requirements — ForeSmoke Webapp MVP

## Introduction

ForeSmoke is a deterministic perception-to-incident reasoning system. The backend
(the `foresmoke` package: perception + reasoning engine) owns all object detection,
temporal reasoning, and incident lifecycle decisions (CLEAR / POSSIBLE / CONFIRMED /
RESOLVED). This spec covers a web application that lets a non-technical teammate run the
full recorded-video exit-obstruction review flow through a browser — load a video, draw an
exit zone, run analysis, and review a confirmed-and-resolved incident — with **zero JSON
editing and zero terminal use**.

The webapp is a thin presentation + orchestration layer. It never re-implements detection,
timing, thresholds, or the CLEAR/POSSIBLE/CONFIRMED/RESOLVED state machine. It displays
backend states/events verbatim and collects a zone configuration that conforms to the
existing `ZoneConfig` schema.

### Hard integration constraints (apply to every requirement)

1. The backend owns all detection, timing, and incident decisions. The frontend only
   displays backend states/events and collects a zone config.
2. The frontend MUST NOT re-implement the CLEAR/POSSIBLE/CONFIRMED/RESOLVED logic or its
   thresholds.
3. No modification to: the JSON schema (`schemas/foresmoke-v1.schema.json`), the reasoning
   engine, the perception package, or any JSONL field name.
4. Analysis runs are produced only by spawning the existing CLI:
   `python -m foresmoke.demo --source <uploaded> --source-kind video --source-fps <detected_fps> --zones <saved_zones.json> --run-dir runs/<new_id> --target-hz 5`.
5. All times shown on the timeline and incident cards are **source-video** `t_ms` values,
   never wall-clock or processing time.
6. All new code lives in a new `foresmoke/webapp/` package, cleanly separable from the engine.

---

## Requirement 1 — Video upload and probing

**User story:** As a reviewer, I want to upload a recorded video and immediately see its
duration and dimensions, so I know the clip loaded and can annotate against its true frame.

### Acceptance Criteria

1. WHEN a user uploads a video file THEN the backend SHALL store the file and return its
   `duration_s`, `width`, `height`, and `fps`.
2. WHEN probing fails (unreadable file, missing/zero fps) THEN the backend SHALL return a
   clear error and the UI SHALL display it without crashing.
3. WHEN a video is loaded THEN the UI SHALL display duration and pixel dimensions.
4. The probed `fps` returned by the backend SHALL be the value later passed to the CLI as
   `--source-fps`, and the probed `width`/`height` SHALL be the values written into the zone
   config `reference_frame`.
5. The UI SHALL be able to play, pause, and scrub the uploaded video, and pause on a chosen
   frame for annotation.

---

## Requirement 2 — Zone annotation and config saving

**User story:** As a reviewer, I want to draw and name an exit zone on a paused frame, so the
engine knows which floor area must stay clear.

### Acceptance Criteria

1. WHEN the video is paused THEN the user SHALL be able to click points to draw a polygon
   overlaid on the frame, with support for undo, dragging a vertex, deleting a vertex, and
   naming the zone.
2. WHEN converting clicks to coordinates THEN the UI SHALL convert displayed-pixel clicks to
   normalized coordinates (`x/width`, `y/height`) against the displayed frame's **true**
   dimensions (the probed video width/height), independent of on-screen scaling.
3. WHEN saving THEN the config SHALL set `reference_frame` to the uploaded video's
   `width`/`height` and SHALL include a generated `view_id`.
4. BEFORE saving THEN the UI SHALL validate client-side that the polygon has ≥3 vertices,
   no self-intersection, non-zero area, and all vertices inside the frame (0–1), and SHALL
   block saving with a specific message if any check fails. This mirrors the engine's Shapely
   validation so a saved zone is not rejected at analysis time.
5. The saved config SHALL conform to the `ZoneConfig` schema exactly (all required zone
   fields present, `hazard_type` = `blocked_exit`, vertices distinct and un-closed, no
   `person` label, `exit_overlap <= enter_overlap`).
6. The saved polygon SHALL remain visible as an overlay during playback.
7. The UI SHALL expose a params panel with defaults: `candidate_labels` (chair, backpack,
   suitcase), `confirm_ms` = 5000, `resolve_ms` = 3000, `min_confidence` = 0.35. Remaining
   schema-required zone fields SHALL be filled from the existing defaults used by the
   annotator (`footprint_height_fraction` 0.20, `enter_overlap` 0.30, `exit_overlap` 0.15,
   `person_occlusion_ratio` 0.20, `candidate_clear_ms` 1000).
8. The frame used for annotation SHALL come from the same uploaded video that will be
   analyzed (enforced because annotation happens against the uploaded file's probed
   dimensions and the saved `reference_frame` carries those dimensions).

---

## Requirement 3 — Running analysis

**User story:** As a reviewer, I want to start analysis and watch progress, so I know the
clip is being processed and when it finishes.

### Acceptance Criteria

1. WHEN the user clicks "Run analysis" THEN the backend SHALL create a fresh, empty
   `runs/<id>` directory and spawn
   `python -m foresmoke.demo --source <uploaded> --source-kind video --source-fps <detected_fps> --zones <saved_zones.json> --run-dir runs/<id> --target-hz 5`.
2. WHEN a run is in progress THEN the backend SHALL report progress by comparing the growing
   `observations.jsonl` line count to an estimate of `duration_s × target_hz`, and the UI
   SHALL show a live progress indicator.
3. WHEN the user cancels THEN the backend SHALL terminate the subprocess and mark the run
   cancelled.
4. WHEN the subprocess exits THEN the backend SHALL expose a clear state: done on exit 0,
   failed on any nonzero exit.
5. The UI SHALL display a clear completion message and SHALL note that processing is not
   instant (CPU inference) and that short clips analyze fastest.
6. Each run SHALL be saved under its own `runs/<id>` directory and SHALL never reuse a
   non-empty directory (the CLI refuses a non-empty run dir).

---

## Requirement 4 — Serving and listing runs

**User story:** As a reviewer, I want to open a finished run and also see prior runs, so I can
review results without touching files.

### Acceptance Criteria

1. The backend SHALL serve a finished run's `observations.jsonl`, `states.jsonl`, and
   `events.jsonl` to the UI unaltered (no field renaming or recomputation).
2. The backend SHALL list prior runs with enough metadata to select and open one.
3. The backend SHALL serve the run's source video for playback and seeking.
4. WHEN a run has no video (e.g. a seeded fixture run) THEN the UI SHALL still render states,
   events, and timeline, and SHALL degrade gracefully where a video frame would be shown.

---

## Requirement 5 — Understandable status (state mapping)

**User story:** As a reviewer, I want plain-language status with color and text, so I
understand exit access at a glance without reading engine jargon.

### Acceptance Criteria

1. The UI SHALL map engine states to a label + color + text, never color alone:
   - `CLEAR` → "Access clear" / green
   - `POSSIBLE` → "Checking obstruction" / amber
   - `CONFIRMED` → "Obstruction confirmed" / red
   - `RESOLVED` → "Incident resolved" / blue
   - `observation_state = unknown` → "View unavailable or obscured" / gray
2. WHEN a zone is POSSIBLE/confirming THEN the UI SHALL show confirmation progress as
   "X.X / Y.Y seconds of evidence" derived from `positive_evidence_ms` versus the zone's
   `confirm_ms`. These values are read from backend state; the UI does not compute the
   decision.
3. WHEN a CONFIRMED incident's `observation_state` becomes `unknown` THEN the UI SHALL keep
   the incident visible, add an uncertainty badge, and SHALL NOT fabricate a resolution.
4. The mapping SHALL derive `observation_state = unknown` directly from the backend state
   field, taking precedence over the phase color when unknown.

---

## Requirement 6 — Incident review and seek-to-moment

**User story:** As a reviewer, I want incident cards with evidence that jump the player to the
exact source moment, so I can verify what the engine saw.

### Acceptance Criteria

1. The UI SHALL render an incident card per incident showing: zone, detected object label(s)
   (from `evidence.objects` labels), confirmation time, resolution time, and an evidence
   thumbnail.
2. The evidence thumbnail SHALL be produced by seeking the video to the event's
   `evidence.t_ms` (source-video time).
3. WHEN a user clicks an incident card or a timeline marker THEN the player SHALL seek to that
   source moment (`t_ms`).
4. The center timeline SHALL place every status change and incident marker by source `t_ms`,
   and timeline markers SHALL be clickable to seek.
5. Confirmation and resolution times shown SHALL come from `confirmed_at_ms` and
   `resolved_at_ms` on the event records.

---

## Requirement 7 — Layout and detection relevance

**User story:** As a reviewer, I want one workspace where the video and status dominate, so I
can focus on exit safety.

### Acceptance Criteria

1. The UI SHALL present one workspace with three areas:
   - Left sidebar: video selection/upload, camera & view name, saved zones, "Run analysis".
   - Center: large video player with zone outlines and relevant detections overlaid, plus a
     clickable incident timeline beneath it.
   - Right panel: current zone status, active incident, incident history.
2. By default the center overlay SHALL show only detections whose label is in the zone's
   `candidate_labels`; a debug toggle SHALL reveal all model detections.
3. The UI SHALL keep source-video time visually distinct from processing/wall-clock time.
4. Video and incident status SHALL be the visually dominant elements.

---

## Requirement 8 — Launch and docs

**User story:** As a teammate, I want a one-command launch and a short doc, so I can start the
app without hunting through code.

### Acceptance Criteria

1. The repo SHALL include a launch script and/or make target that starts the backend serving
   the single-page frontend using the repo's existing virtual environment and package.
2. The repo SHALL include `docs/WEBAPP.md` describing setup, launch, and the review flow.
3. The launch path SHALL not require editing JSON or running the engine CLI by hand.

---

## Requirement 9 — Acceptance scenario (end to end)

**User story:** As a teammate, I want to complete the whole flow in the browser, so I can
review a real incident with no JSON or terminal.

### Acceptance Criteria

1. A teammate SHALL be able to, entirely in the UI: load a video, draw a valid zone, run
   analysis, and review one confirmed-and-resolved incident with a working seek-to-moment.
2. The flow SHALL require zero JSON editing and zero terminal use after launch.
3. A pre-analyzed sample run SHALL be available so the viewer (player + saved-run review)
   works before any new analysis is run.
