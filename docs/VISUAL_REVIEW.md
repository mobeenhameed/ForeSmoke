# Kiro visual review

ForeSmoke can supplement its deterministic detector/geometry/timing result with an asynchronous
image assessment from the official Kiro CLI. The feature is off by default. Kiro never changes an
engine phase, incident, timer, or zone.

## Prerequisites and setup

Install ForeSmoke normally, then install and authenticate the official Kiro CLI:

```powershell
kiro-cli --version
kiro-cli whoami
kiro-cli chat --list-models --format json
```

Local interactive authentication can be completed with `kiro-cli login`. Official headless
automation supports `KIRO_API_KEY`; API keys require an eligible paid Kiro plan and may be disabled
by an organization administrator. Never place the key in a command, config file, or log.

The implementation was tested with Kiro CLI `2.34.0` and `claude-sonnet-5.5`. The model ID is
explicit and configurable; ForeSmoke does not silently fall back. Each request starts a fresh V3
headless session using `stream-json`.

## Running

```powershell
.\.venv\Scripts\python.exe -m foresmoke.demo `
  --source chair_front.mp4 `
  --source-kind video `
  --source-fps 60 `
  --zones configs/zones.json `
  --run-dir runs\chair-reviewed `
  --visual-review `
  --review-model claude-sonnet-5.5
```

Useful controls:

- `--review-timeout-s 60`
- `--review-candidate-delay-ms 2000`
- `--review-cooldown-ms 5000`
- `--review-queue-capacity 3`
- `--review-max-per-run 10`
- `--review-clear-reference PATH` for an explicitly approved clear image
- `--review-clear-reference-t-ms N` when that image has a known source timestamp
- `--review-max-frames 3`, `--review-max-image-dimension 1280`, and
  `--review-max-image-bytes 1500000`
- `--review-evidence-dir PATH` to relocate immutable bundles

For the webapp, set `FORESMOKE_VISUAL_REVIEW=1` before starting it. The CLI executable, model,
timeout, delays, queue capacity, run limit, and clear reference can be set with the corresponding
`FORESMOKE_KIRO_CLI`, `FORESMOKE_REVIEW_MODEL`, `FORESMOKE_REVIEW_TIMEOUT_S`,
`FORESMOKE_REVIEW_CANDIDATE_DELAY_MS`, `FORESMOKE_REVIEW_COOLDOWN_MS`,
`FORESMOKE_REVIEW_QUEUE_CAPACITY`, `FORESMOKE_REVIEW_MAX_PER_RUN`, and
`FORESMOKE_REVIEW_CLEAR_REFERENCE` environment variables.

## Runtime and security behavior

Candidate reviews use source-video time, not replay speed. A candidate is requested once after the
configured delay. A recent candidate review suppresses a duplicate confirmation request during the
source-time cooldown. Resolution evidence is reviewed separately. One worker runs by default, the
queue is bounded, newer pending evidence replaces older pending evidence for the same occurrence,
and every skip, replacement, transition, or failure is appended to the audit log.

Each request gets a UUID directory under `RUN_DIR/visual_reviews/`. It contains:

- `manifest.json`, with exact sequence numbers, source timestamps, source frame indexes, normalized
  zone geometry, detections, measured overlaps, and engine timing;
- up to three exact current raw frames and corresponding neutral annotations;
- an optional copied clear reference, only when explicitly configured;
- a per-bundle Kiro agent configuration.

Images preserve aspect ratio and are capped by dimension and encoded byte size. The annotation uses
a neutral outline and stable object IDs, without engine state labels or red/green verdict colors.
Future frames and cached frames with a different timestamp are rejected.

The per-bundle Kiro agent exposes only the image/file read tool and grants read permission only to
that manifest, its frame directory, and optional reference. It has no shell, write, network, MCP, or
unrelated repository access. The prompt states that scene text and metadata are untrusted data.

## Frontend/API handoff

`reviews.jsonl` is append-only and available at:

```text
GET /api/runs/{run_id}/reviews
```

Fold records by `review_id`; the last record is the current status for that request. Join to an
incident using `incident_id` when present. Candidate reviews may have `incident_id: null`; use
`run_id`, `zone_id`, trigger, and source timestamp for display. Keep the deterministic engine status
and visual review status as separate fields.

Review status is `pending`, `running`, `completed`, `failed`, or `skipped`. A completed assessment
is `supports_obstruction`, `does_not_support_obstruction`, or `uncertain`. Failures have
`assessment: null`; they must never be displayed as “clear” or model-produced uncertainty.
`stale_for_current_state: true` means the result belongs in history and must not be shown as the
current incident assessment. A model disagreement is informative only and never clears or resolves
the engine incident.

Example completed terminal record:

```json
{
  "schema_version": "1.0",
  "review_id": "79e96a05-06e0-48bc-86d2-782f5ce935c3",
  "camera_id": "cam01",
  "run_id": "31234567-89ab-4cde-8fab-0123456789ab",
  "view_id": "exit-view-v2",
  "zone_id": "east_exit",
  "incident_id": null,
  "trigger": "candidate",
  "source_timestamp_ms": 2000,
  "status": "completed",
  "provider": "kiro_cli",
  "requested_model": "claude-sonnet-5.5",
  "reported_model": null,
  "latency_ms": 3200,
  "stale_for_current_state": false,
  "evidence_manifest": "visual_reviews/79e96a05-06e0-48bc-86d2-782f5ce935c3/manifest.json",
  "assessment": {
    "assessment": "supports_obstruction",
    "objects_of_interest": [],
    "visibility": "adequate",
    "visible_evidence": [
      {"frame_id": "frame_10", "observation": "A chair is visible inside the outlined area."}
    ],
    "summary": "The visible chair supports an obstruction assessment.",
    "recommended_action": "Move the chair outside the protected access area.",
    "limitations": ["Physical clearance is not established by these images."]
  },
  "error": null
}
```

Example failure fields:

```json
{
  "status": "failed",
  "assessment": null,
  "error": "authentication_failed: Kiro CLI authentication is required",
  "stale_for_current_state": false
}
```

The real object contains all correlation and provider fields shown in the completed example.

## Verification and limitations

A synthetic, neutrally named image test verified that Kiro actually read pixels: it identified a
purple lower-left rectangle, a cyan upper-right circle, and three central diagonal bars. A full
adapter run returned and validated an `uncertain` assessment because the synthetic scene did not
show a real exit; latency was about 17.7 seconds. This is intentionally labeled synthetic and is not
evidence about an actual exit.

Routine tests use fake adapters and consume no Kiro credits. Real reviews consume the selected
model's account credits; `claude-sonnet-5.5` was listed by the tested account at a 1.3x multiplier.
Actual plan quota, organization governance, and API-key eligibility vary by account and are not
inferred by ForeSmoke.

The reviewer handles only the configured exit-access or registered-extinguisher-access objective.
It cannot detect a missing extinguisher, measure physical or legal clearance, prove safety,
discover zones, or compensate for missing/incorrect footage.
`reported_model` remains null because the observed CLI stream did not expose a trustworthy model ID.
