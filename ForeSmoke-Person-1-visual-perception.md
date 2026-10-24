# ForeSmoke — Person 1 implementation brief

**Role: visual perception, detection, tracking, capture reliability, and the integration runner.**

Paste this entire document into an AI coding assistant. It is self-contained, including the shared contract. The requested output from that assistant is working code, tests, runnable commands, and a short handoff report—not another proposal.

## Instructions to the implementing AI

You are implementing Person 1's portion of ForeSmoke for a two-person, one-day hackathon. Build a small working Python package in the existing repository. Inspect its instructions and code before editing; preserve unrelated work. Use the exact contract embedded below. Make reasonable local implementation choices and proceed. Ask only when an absent input truly blocks work; otherwise use the provided defaults and synthetic/video fixtures.

Complete the essential path before optional capabilities. Never claim a model, camera, GPU, test, or performance result worked unless you actually ran it. If hardware or downloads are unavailable, finish the adapter, fixtures, validation, and commands and clearly state which real inference checks remain unverified. Do not replace the detector with synthetic detections in the real demonstration without labeling the run synthetic.

## Objective and deliverables

Turn a webcam, a constant-frame-rate video, or a test image into a reliable, schema-valid sequence of current-frame detections. Each detection includes a class, score, normalized box, and a best-effort tracking ID. Keep perception independent of hazard policy: Person 2 owns decisions about unsafe zone occupancy and incident resolution.

Deliver:

1. `foresmoke.shared.contracts` with Pydantic definitions, semantic validators, and schema export for every shared model.
2. An input adapter for webcam and local video; image mode for smoke tests. Source selection must be explicit, so a filename such as `0.mp4` is never mistaken for camera zero.
3. A detector adapter, ByteTrack-backed tracking, and correct current-observation handling.
4. The public observation iterator and a CLI that saves JSONL, plus useful stderr diagnostics.
5. A small optional preview showing bounding boxes, labels, IDs, and after integration, zone/state overlays.
6. `foresmoke.demo`, which connects your observations to Person 2's engine through the documented API.
7. Tests, a small saved detection recording, a reproducible environment/dependency record, and setup/run instructions.

## Recommended stack and models

Use Python 3.11, `opencv-python` for capture/preview, NumPy, Pydantic v2, pytest, and Ultralytics with `yolo26n.pt` plus explicitly selected `bytetrack.yaml`. Keep one model/tracker instance per source; use `persist=True` for consecutive frames from that source. [Official tracking API](https://docs.ultralytics.com/modes/track/).

Start on CPU unless a working GPU environment already exists. After the basic test passes, use a compatible CUDA device when available. Begin with `imgsz=640`, FP32, and a five-observation-per-second target. Reduce to 416 or 320 if measured performance requires it, then recheck the chosen prop. Do not spend the hackathon installing a GPU stack that is not already working. A working previously installed YOLO11n/YOLOv8n adapter is an acceptable fallback; record the actual weights and package versions rather than claiming YOLO26 was used.

Install a coherent environment, run one real image inference, then freeze exact working versions and record OS, Python, device, model filename/checksum, image size, inference threshold, tracking settings, and capture dimensions. Suggested initial dependencies are `ultralytics`, `opencv-python`, `numpy`, `pydantic>=2,<3`, and `pytest`; choose compatible resolved versions rather than inventing untested pins. Keep reasoning dependencies in a separate package extra and do not install both OpenCV GUI and headless wheels in the same environment.

Use the default model vocabulary. Export all detected classes initially so Person 2 can see person occluders and apply its own allowlist. Start the detector output threshold at 0.15, below the reasoner's 0.35 threshold. A detector filter higher than the reasoning threshold irreversibly discards useful evidence. Record any tracker-specific thresholds and verify they do not suppress an otherwise valid obstruction.

Grounding DINO is a later option for text-prompted objects such as “cardboard box” and “fire extinguisher”; its official project supports open-set detection. It is not part of the critical path because adding a second detector, dependencies, tracking association, and score calibration costs time. [Official Grounding DINO project](https://github.com/IDEA-Research/GroundingDINO). No LLM or vision-language model is needed to classify the MVP's deterministic obstruction rule.

## Architecture

```text
webcam/video/image
  -> acquire original image + source timestamp + source frame index
  -> choose fresh frame at target cadence
  -> detector -> current boxes -> tracker association
  -> normalize to original image coordinates
  -> shared model validation
  -> FrameObservation -> JSONL and/or integration consumer
  -> optional local preview cache (not part of the wire contract)
```

Use small components: `sources.py`, `detector.py`, `tracking.py` if required, `normalize.py`, `pipeline.py`, `preview.py`, and `__main__.py`. Avoid an abstract framework with many interchangeable backends; one tested implementation and a narrow adapter are sufficient.

## Exact implementation sequence

### 1. Establish the contract and runnable package

Create importable modules and package extras: core contains Pydantic; perception adds model/OpenCV dependencies; reasoning adds Person 2's geometry dependencies; dev adds tests. Ensure imports of `foresmoke.shared` never import the detector or initialize a GPU. Implement every field and cross-field constraint in the embedded contract, and export schemas. Send Person 2 a valid observation immediately.

### 2. Verify the detector before building around it

Run inference on a real image of the actual demo chair/backpack. Print model class names to a diagnostic log and inspect the class/box visually. Confirm the available weights actually detect the selected prop and use the expected vocabulary. Keep this as a manual smoke test; automated unit tests must not download weights or require a webcam.

### 3. Implement source adapters and source-time handling

Open and release `VideoCapture` reliably with `try/finally`. Read metadata, validate FPS for videos, and report an actionable error if the source cannot open. Preserve the original frame index even when inference skips frames. For video, sample original frames near a five-Hz cadence and derive time from their original indices and validated FPS. Offline processing must not use wall-clock speed as video time.

For live capture, prioritize a recent frame over a growing backlog. Timestamp when a frame is acquired, not after inference. A capture thread may hold one latest frame; do not re-infer the same frame as if it were new. Use source deadlines and backoff where supported. Read/inference stalls are detected by the integrated consumer's independent watchdog even if a driver call blocks. A capture error produces unavailable, not an empty successful result. A video EOF produces eof. A still image produces one ok and then eof, with no synthetic duration.

### 4. Normalize detections and attach tracking IDs

Use an explicit tracker configuration. A typical initial call is:

```python
result = model.track(
    frame, persist=True, tracker="bytetrack.yaml",
    conf=0.15, imgsz=640, verbose=False,
)[0]
```

Treat this as an API starting point; inspect the installed version's output. IDs may be absent, and track-mode output may exclude newly unmatched detections. Capture raw current detector boxes before tracker filtering when necessary. Associate tracker IDs to current detections using deterministic same-class IoU matching at >=0.5, one-to-one and highest IoU first; matched current detections receive IDs, unmatched ones receive null. Do not emit a raw detection and its tracked duplicate twice. Prefer accessing a supported detector/tracker adapter or callback over a second inference pass. If a second pass is necessary, measure its cost and record it.

An acceptable simpler baseline is to emit only the chosen API's results **after verifying** it exposes all relevant current detections and does not output unmatched predicted tracks. Do not assume that behavior. If tracking integration blocks progress, temporarily publish the real detector outputs with null IDs so Person 2 can integrate; finish best-effort IDs before final acceptance.

For each raw box, map coordinates to the original frame, clamp tiny boundary excursions to [0,1], reject non-finite or zero/inverted-area boxes, normalize names, and validate the envelope. Report rejected box counts. When resetting the tracker, increment its epoch. Never preserve one tracker across two unrelated sources.

### 5. Build serialization and health behavior

Serialize one validated record per line. Create parent run directories. Provide `--output -` for stdout; all diagnostics still go to stderr. Flush live output. Keep bounded memory and release model/camera/file resources on Ctrl+C. Do not synthesize a resolved incident on exit. Emit stable errors for capture/model failure; if the pipeline cannot continue, terminate nonzero after recording the available health evidence.

Log camera/view/run identities, source FPS and dimensions, observed cadence, capture-to-emission latency, inference p50/p95, dropped live frames, rejected detections, and tracker resets. Never present decode FPS as inference FPS. Store these in a separate diagnostics file or stderr, not undeclared fields in the shared payload.

### 6. Provide preview and evidence without building a dashboard

Draw against the exact frame used for inference. Pixel projection is x*width and y*height, clamped only for raster drawing. Show source time, class, confidence, and track ID; use `untracked` for null. Person 2 supplies normalized zone geometry and snapshot values. Show CLEAR/POSSIBLE/CONFIRMED/RESOLVED alongside observed/unknown; for example, `CONFIRMED / UNKNOWN` on feed loss.

Save optional sampled evidence JPEGs in `runs/<run>/frames/`, with relative references in observations. Make saving configurable; it must not be required for reasoning. A small run may save each inferred frame, but an unbounded camera session should have a duration/size limit or disable saving. Avoid recording camera credentials in logs. The primary demo is local and does not require external messaging or cloud uploads.

### 7. Integrate with Person 2 and verify

Connect the iterator to a bounded producer queue and invoke the engine from one consumer. Write observations, events, and snapshots to separate JSONL files. Run all shared fixtures first, then recorded video, then webcam. Start the live watchdog independently of inference and call it every 250 ms. Verify a stalled detector cannot leave the preview indefinitely reporting observed clear.

For replay, preserve run_id, seq, timestamps, labels, and geometry exactly. Do not regenerate IDs or re-time packets when feeding the reasoner. Record three complete demo cycles and keep the most reliable recording as a fallback. Freeze optional changes after hour 6.

## Person 1 test cases and acceptance criteria

| Test | Required outcome |
|---|---|
| Box [320,180,640,360] on a 1280x720 frame | Exact normalized [0.25,0.25,0.5,0.5] |
| Model letterboxes a non-square image | Output boxes align with the original image when drawn |
| Model detects zero objects | Valid ok packet with an empty list |
| Capture or inference raises | Unavailable or explicit failed run; never a successful empty frame |
| Detector sees an object before tracker assigns an ID | Detection survives with null ID |
| Tracker predicts a lost object | Prediction is not exported as a fresh detection |
| One object remains visible for ten seconds | IDs normally persist; changes are logged and never corrupt timestamps |
| Tracker resets | tracker_epoch increments and no old-key reuse occurs |
| 30-FPS video sampled every six frames | Source timestamps differ by 200 ms independent of runtime speed |
| Webcam disconnect/inference stall | Integrated state becomes unknown within the two-second watchdog plus one tick |
| JSONL consumer and serializer round trip | All records validate; no debug text in JSON data |
| Headless execution | Recorded observations and events still work without a preview window |

Automate normalization, serialization, failure mapping, source-time sampling, and raw/tracked-box association with small fake model/source adapters. Use actual video and a real camera for the integration checks; do not label mocked tests as measured model performance.

Completion means both independent CLIs and the integrated command run, all contract tests pass, the chair is detected reliably in the chosen view, and the full incident lifecycle succeeds three times. Aim for at least five usable observations/sec at the chosen settings; two/sec is the minimum demo target with no healthy gap above one second. Measure on the actual machine and report the result, including p95 latency. If the cadence target cannot be met, report the limit and use the recorded fallback; do not invent an FPS guarantee.

## Failure modes and debugging order

1. **No chair detected:** inspect the raw image, model vocabulary, lighting, distance, inference size, and actual scores before changing reasoning rules. Select a clearer prop/view if needed.
2. **Boxes look shifted:** compare original dimensions with inference dimensions and letterbox padding; test with a known rectangle. Disable any preview mirroring or apply the same transform everywhere with a new calibration.
3. **Incidents appear late:** compare capture timestamp, emission time, consumer receipt, and source t_ms. Check camera buffering and inference backlog before shortening the five-second rule.
4. **IDs flicker:** inspect current-frame scores, tracker thresholds, cadence, and epoch resets. ID instability should not split a zone incident.
5. **Object disappears from JSON while visible:** inspect raw detector output versus tracker output and class filters. Unmatched detections must survive.
6. **No detections during a crash:** distinguish the exception path from successful empty inference. The packet must say unavailable.
7. **Integration rejects data:** inspect schema version, field spelling, null handling, finite values, normalized coordinates, camera/view identifiers, and time units. Do not “fix” this with permissive parsing.

## Stretch goals, only after acceptance

In order: cleaner live preview and saved incident images; configurable second zone; ONNX/runtime optimization if already familiar; open-vocabulary adapter for boxes; phone/IP camera input; segmentation for improved footprint estimates. Each new detector must preserve the wire contract and be tested against its own real class vocabulary and score behavior.

## Explicit non-goals

No custom model training or dataset labeling, smoke/fire classification, automatic exit-sign/zone discovery, face recognition, person identity, cross-camera tracking, real-world distance estimation, LLM decision calls, dashboard, API server, database, automated alerts, or production deployment. Do not implement Person 2's hazard engine yourself except for a clearly temporary interface stub.

## Final handoff required from the implementing AI

Provide changed files, exact setup and run commands for the actual OS, dependency/model versions, measured performance, test results, the selected demo prop and view, and paths to observations and the reliable fallback video. Distinguish complete, manually verified, mocked, and unverified items. Document any contract deviation and resolve it with Person 2 before merging; undocumented deviation is a failed handoff.

---

## Shared product definition and integration contract — ForeSmoke 1.0

This entire section is identical in both implementation briefs. It is normative: do not rename fields, change units, or reinterpret state transitions in only one half. Where a schema cannot express an invariant, the rules below still apply. These thresholds are initial hackathon design choices, not validated safety limits.

### Product objective and bounded MVP

Demonstrate: fixed camera observes a chair or backpack in a manually marked exit-access zone; sustained occupancy creates one `blocked_exit` incident; removal followed by sustained clear observations resolves the same incident. A person walking through does not create an incident. Camera loss produces an unknown observation state and never resolves an incident.

Use one camera, one view, one zone, and one process for the integrated demonstration. Also provide JSON Lines files so each half can be developed and replayed independently. Camera motion, real-world clearance dimensions, fire/smoke recognition, and legal compliance are outside this prototype. The output means “persistent visual obstruction under configured rules,” not a certified fire-safety determination.

Use a chair as the primary prop and a backpack as the secondary prop. Pretrained COCO class lists include these; generic cardboard boxes, exit signs, and fire extinguishers are not COCO classes. Do not pretend a suitcase detection is a box detection. [Official COCO classes](https://docs.ultralytics.com/datasets/detect/coco/).

### Ownership and repository boundaries

```text
foresmoke/
  shared/contracts.py             Person 1: models, validation, schema export
  perception/                     Person 1: capture, detector, tracker, CLI
  reasoning/                      Person 2: geometry, state machine, events, CLI
  demo.py                         Person 1: small integration runner
configs/perception.json           Person 1
configs/zones.json                Person 2
schemas/                         Person 1: exported contract schemas
tests/perception/                 Person 1
tests/reasoning/                  Person 2
tests/fixtures/                   Person 2: synthetic observation sequences
runs/                            Generated recordings, logs, and events
README.md                         Both: their own setup/run sections
pyproject.toml                    Person 1: shared, perception, dev extras;
                                  Person 2 supplies reasoning dependencies
```

First 30 minutes: agree on this contract, commit the shared definitions, and exchange one valid populated frame, one valid empty frame, and one unavailable packet. Person 2 can initially reproduce the shared model definitions from this brief in an isolated checkout; at integration, keep Person 1's single canonical module and run both test suites against it. Do not maintain two contract implementations after integration. Person 2 must be able to install/import reasoning without Torch or Ultralytics.

### Transport and callable interfaces

Use Python 3.11 and JSON Lines: UTF-8, one complete JSON object per line, newline after every record, no NaN or infinity, flush live records immediately. Logs go to stderr. Never mix progress bars or model output into stdout when stdout carries JSON. Paths in JSON use forward slashes and are relative to the run directory; optional image references may be null. Frames are never base64-encoded into observations.

Required Python interfaces, implemented with type hints:

```python
# Person 1; FrameObservation is a Pydantic model from shared.contracts.
def iter_observations(config: PerceptionConfig) -> Iterator[FrameObservation]: ...

# Person 2; models also come from shared.contracts.
class ReasoningEngine:
    def __init__(self, config: ZoneConfig): ...
    def process(self, packet: FrameObservation, received_ms: int) -> list[HazardEvent]: ...
    def tick(self, now_ms: int) -> None: ...
    def close_input(self) -> None: ...
    def snapshots(self) -> list[ZoneState]: ...
```

`received_ms` and `now_ms` are from the consumer's monotonic clock and use the same local origin. They are only for live-input watchdogs. All hazard-duration calculations use `packet.t_ms`. Calls to the engine are serialized by one consumer; the engine is not required to be thread-safe. `process` returns only newly created lifecycle events, never the whole history. `snapshots` returns current states without changing them. `close_input` marks observations unknown with reason `source_ended`, breaks continuity, preserves active incidents, and emits no lifecycle event; use it when the input closes without an explicit EOF packet.

Person 1's live runner uses a producer thread for perception and a bounded queue; the main consumer calls `tick` at least every 250 ms even when capture/inference blocks. Live queue capacity is two; drop the oldest queued observation if needed and report drops. Never invent observations for dropped frames. Offline replay reads all records without dropping. The reasoner handles sequence gaps using timestamps.

### Observation semantics

- `schema_version` is exactly `1.0`. Unknown versions fail with an actionable error.
- `camera_id` identifies the camera; `view_id` identifies its calibrated, fixed framing. Both must match the zone configuration. Moving, cropping, rotating, or mirroring the view requires recalibration and a new `view_id`; automatic camera-motion recognition is not required.
- `run_id` is a UUID generated once per acquisition run. A replay preserves its recorded ID. A process restart or intentional source replacement creates a new run. Never append a new run to an old incident silently. The MVP reasoner binds to one run and rejects a different run until restarted. Old incidents remain unresolved in the old log; the MVP does not restore them automatically.
- Bind `source_kind` on the first packet and reject changes within the run. `tracker_epoch` cannot decrease. Configuration identifiers exclude the `|` character so the deterministic incident-ID input is unambiguous.
- `seq` begins at zero and increases by one for each emitted packet, including health packets. Duplicate or decreasing sequence numbers are logged and ignored, with no changes to state or watchdog time. Sequence gaps are allowed; they do not imply observed clear frames.
- `t_ms` is elapsed source time in integer milliseconds. Live sources use capture-time monotonic elapsed time, never UTC clock differences. Video uses original frame index / validated source FPS; use `--source-fps` if FPS is missing or unreliable. Variable-frame-rate video requires valid presentation timestamps or conversion to constant frame rate before the demo. Image mode has one observation at zero. Processing speed does not change recorded-video durations.
- Timestamps must be nondecreasing across all packets, and strictly increasing across `ok` observations. A same-time EOF is allowed. Reject timestamp regressions. `frame.index` is the original decoded frame index, not `seq` or the number of processed frames.
- `status=ok` means a newly captured/decoded frame was successfully inferred. Its `frame` is non-null, `error` is null, and `detections=[]` means the detector found none in that frame. It does not prove every possible obstacle is absent.
- `status=unavailable` means capture or inference failed; `frame=null`, `detections=[]`, and `error` contains a short stable code such as `capture_failed` or `inference_failed`. `status=eof` means a finite input ended; `frame=null`, `detections=[]`, `error=null`. EOF does not add clear evidence.
- Do not emit successful packets from cached images, tracker-only predictions, skipped inference, or a failed model call. A tracker may retain internal state, but emitted boxes must have current-frame detection support. If the chosen library suppresses unmatched current detections, preserve them with `track_id=null` instead of converting a detected obstruction into an empty frame.
- `tracker_epoch` starts at zero and increments on a tracker reset/reconnect within a live run. A track key is `(run_id, camera_id, tracker_epoch, track_id)`. `track_id` is a string or null; it is never the incident identity.
- `label` is the canonical model class name in lowercase with spaces replaced by underscores. `confidence` is the detector score, not the probability of a safety violation.
- `bbox_xyxy=[x1,y1,x2,y2]` uses coordinates normalized to the original, unmirrored frame: top-left origin, x rightward, y downward; `0 <= x1 < x2 <= 1` and `0 <= y1 < y2 <= 1`. Map any model resize/letterbox back before normalization. Do not round coordinates until display; use full numeric precision in JSON.
- `frame.width` and `frame.height` describe that same original frame. Resolution may change with identical aspect ratio and framing; reject an aspect-ratio difference above 1% from the zone reference. Bbox ordering, unique non-null IDs within a frame, and status/frame/error consistency require runtime validators beyond JSON Schema.

### Zone configuration and spatial rule

The complete example configuration appears below. Its polygon is in the same normalized coordinates as detections. Draw the protected floor/access region in a saved reference frame, not the exit sign. Hardcode vertices initially; no drawing UI is required. Reject self-intersecting, zero-area, out-of-range, or fewer-than-three-distinct-point polygons. Do not silently repair them. Vertex order can be clockwise or counterclockwise; the final vertex must not repeat the first. Require `0 < exit_overlap <= enter_overlap <= 1` and `0 < person_occlusion_ratio <= 1`; a zero overlap threshold would incorrectly qualify non-overlapping objects.

Default eligible obstruction labels are `chair`, `backpack`, and `suitcase`. All other labels are excluded from obstruction decisions; a `person` is used only for the visibility heuristic below. This MVP ignores standing people too. Never describe it as recognizing every obstruction.

For each eligible current detection with confidence at least 0.35:

```text
F = box(x1, y2 - 0.20 * (y2 - y1), x2, y2)  # bottom strip, image-space proxy
overlap = area(F intersect zone_polygon) / area(F)
```

The denominator is the footprint-strip area, not the zone area and not IoU. A full image bounding box overlapping the zone is insufficient. This is a 2D heuristic, not a measured physical footprint. The bottom-center point may be drawn for debugging but does not change the decision.

Use `enter_overlap=0.30` while phase is CLEAR/RESOLVED and `exit_overlap=0.15` while POSSIBLE/CONFIRMED. A qualifying candidate meets or exceeds that phase's threshold. This hysteresis applies at the zone level, including after an ID change. It is deliberately not per-object hysteresis. If any candidate qualifies, the zone observation is POSITIVE.

If none qualifies, check any `person` with confidence >= 0.35: if `area(person_bbox intersect zone)/area(zone) >= 0.20`, label this observation UNKNOWN because the person may hide the zone. Otherwise label it NEGATIVE. A visible qualifying obstruction takes precedence over this person heuristic. The heuristic only handles obvious person occlusion; it does not establish complete visibility or detect all occluders.

### Temporal logic: exact behavior

Track one state per `(run_id, camera_id, zone_id)`, not per track ID. Continuous zone occupancy may be produced by successive objects; that is intentional. Individual-object stationarity is not an MVP requirement.

Maintain `phase`, `observation_state`, `positive_evidence_ms`, `negative_run_ms`, `first_seen_ms`, `confirmed_at_ms`, `incident_id`, `last_positive_seq`, previous classification/time, and a per-zone incident ordinal. Start with `phase=CLEAR`, `observation_state=unknown`, no incident, and zero timers. CLEAR before the first observation is an initialized lifecycle phase, not evidence of safety.

Defaults: confirm after 5,000 ms of accumulated positive evidence; abandon a possible incident after 1,000 ms of consecutive negative evidence; resolve a confirmed incident after 3,000 ms of consecutive negative evidence. `max_observation_gap_ms=1000` and `live_stale_after_ms=2000`.

1. Validate the packet and identity before changing anything. Reject malformed packets; log the error and invalidate temporal continuity so the next valid packet cannot bridge the bad data. Duplicate/decreasing sequences are ignored as described above. Reject a mismatched camera, view, run, or aspect ratio and mark observation state unknown.
2. An unavailable/EOF packet, an UNKNOWN spatial classification, a live watchdog expiry, or a gap greater than 1,000 ms between accepted `ok` observations breaks continuity. Set observation state to unknown and reset both evidence timers and the previous classification. Preserve phase and any existing confirmed incident. For POSSIBLE also clear `first_seen_ms`; fresh positive evidence must start again. EOF terminates processing without any synthetic resolution.
3. For a valid new visible frame, set observation state to observed. Let `dt` be source-time difference from the preceding accepted visible frame only when no continuity break occurred and `dt <= 1000`; otherwise use zero. The first frame after a break starts a new evidence segment, even if it arrives hours later.
4. On POSITIVE: reset `negative_run_ms` and update `last_positive_seq`. CLEAR or RESOLVED becomes POSSIBLE; reset positive evidence to zero, clear old completed-incident fields/ID, and set `first_seen_ms` to the current time. If POSSIBLE has a cleared first_seen after a break, set it now. Add `dt` to `positive_evidence_ms` only if the previous classification was also POSITIVE. A single positive frame contributes zero duration. Short NEGATIVE interruptions freeze, rather than add to, positive evidence. Save the current classification/time after processing so the next frame sees the correct previous values.
5. While POSSIBLE, confirm when positive evidence reaches 5,000 ms. Allocate a new incident ordinal and ID, set `confirmed_at_ms` to current time, and emit exactly one `incident_confirmed` event. Preserve the accumulated duration at confirmation as `confirmation_evidence_ms`. Track-ID or object-count changes do not create more events.
6. On NEGATIVE: add `dt` to `negative_run_ms` only if the previous classification was NEGATIVE; otherwise set it to zero. While POSSIBLE, once negative evidence reaches 1,000 ms return to CLEAR, clearing candidate timers/first_seen. While CONFIRMED, once it reaches 3,000 ms become RESOLVED and emit exactly one `incident_resolved` using the existing incident ID. A new POSITIVE interrupts clearing immediately. CLEAR and RESOLVED remain as they are on further negatives and do not emit events.
7. After a resolution, the next positive begins a new POSSIBLE period and can eventually create a new incident. Preserve completed incidents in the event log. An active incident followed by unknown data remains active with unknown observation state, even at shutdown.

The live watchdog compares consumer monotonic times; stale/missing input never advances evidence. Update watchdog receipt time only for newly accepted input packets, not duplicates; unavailable packets explicitly keep the observation unknown. `tick` never increments occupancy or clearance durations and is disabled for deterministic file replay. If live input recovers after a watchdog expiry, treat the first valid frame as a fresh segment.

### Events and snapshot contract

Emit only lifecycle transitions to `events.jsonl`. `incident_confirmed` has phase CONFIRMED and a null resolution time; `incident_resolved` has phase RESOLVED and a non-null resolution time. `first_seen_ms` is the start of the candidate evidence period that resulted in confirmation. `confirmation_evidence_ms` is fixed at its confirmation value; `clear_evidence_ms` is zero at confirmation and >= 3000 at resolution. `reason_code` is respectively `persistent_zone_occupancy` or `observed_clear_interval`.

Use deterministic IDs so replay tests are easy: `incident_id = str(uuid5(NAMESPACE_URL, "foresmoke|" + run_id + "|" + camera_id + "|" + zone_id + "|" + str(ordinal)))`, where ordinal starts at one and increments on confirmation. `event_id = str(uuid5(UUID(incident_id), event_type))`. IDs remain stable for a given input/configuration. The MVP does not guarantee deduplication across a brand-new run.

`evidence` describes the exact current frame that triggered the transition, including qualifying candidates with their calculated overlap. At resolution `objects=[]`; `last_positive_seq` points to the last frame with a qualifying obstruction. `frame_ref` may be null; resolve it relative to the run directory when present. The event's detector confidences and overlap values are evidence, not a synthesized safety confidence score.

Runtime event validators require `first_seen_ms <= confirmed_at_ms <= t_ms`, `evidence.t_ms == t_ms`, `evidence.seq >= last_positive_seq`, and consistent frame/identity references. At confirmation require `confirmed_at_ms == t_ms`; at resolution require `resolved_at_ms == t_ms`. Check confirmation/clear evidence against the actual configured durations, not hardcoded schema constants. Snapshot phase CONFIRMED/RESOLVED requires an incident ID; CLEAR/POSSIBLE requires null. Unknown snapshots have no current candidates and zero evidence counters.

`snapshots()` returns one `ZoneState` per configured zone with these required fields:

| Field | Type and meaning |
|---|---|
| schema_version | Literal `1.0` |
| run_id | UUID string or null before the first packet |
| camera_id, view_id, zone_id | Strings from configuration |
| phase | CLEAR, POSSIBLE, CONFIRMED, or RESOLVED |
| observation_state | `observed` or `unknown` |
| observation_reason | `positive`, `negative`, `initializing`, `source_unavailable`, `source_ended`, `occluded`, `stale_input`, `observation_gap`, or `invalid_input` |
| incident_id | UUID string or null |
| t_ms | Last accepted source timestamp or null |
| positive_evidence_ms, negative_run_ms | Nonnegative integer timers |
| candidates | List of current qualifying candidate evidence objects; empty while unknown |

On a gap followed by a usable current frame, reset continuity first, then classify that frame; its final snapshot can be observed. A confirmed phase plus unknown observation state must remain visibly distinct from RESOLVED in logs and overlays.

### Shared configuration, example payloads, and schemas

The following JSON documents are complete examples. JSON Schema checks structure; add the semantic validators described above. Use Pydantic v2 with forbidden extra fields and finite numbers, and export Draft 2020-12 schemas with `model_json_schema()`. [Pydantic schema documentation](https://docs.pydantic.dev/latest/concepts/json_schema/).

**Zone configuration (`configs/zones.json`)**

```json
{
  "schema_version": "1.0",
  "camera_id": "cam01",
  "view_id": "exit-view-v1",
  "reference_frame": {
    "width": 1280,
    "height": 720
  },
  "max_observation_gap_ms": 1000,
  "live_stale_after_ms": 2000,
  "zones": [
    {
      "zone_id": "east_exit",
      "hazard_type": "blocked_exit",
      "polygon": [
        [
          0.3,
          0.55
        ],
        [
          0.8,
          0.55
        ],
        [
          0.8,
          0.95
        ],
        [
          0.3,
          0.95
        ]
      ],
      "candidate_labels": [
        "chair",
        "backpack",
        "suitcase"
      ],
      "min_confidence": 0.35,
      "footprint_height_fraction": 0.2,
      "enter_overlap": 0.3,
      "exit_overlap": 0.15,
      "person_occlusion_ratio": 0.2,
      "confirm_ms": 5000,
      "candidate_clear_ms": 1000,
      "resolve_ms": 3000
    }
  ]
}
```


**Populated observation, at t=5000 ms**

```json
{
  "schema_version": "1.0",
  "run_id": "01234567-89ab-4cde-8fab-0123456789ab",
  "camera_id": "cam01",
  "view_id": "exit-view-v1",
  "source_kind": "video",
  "seq": 25,
  "t_ms": 5000,
  "tracker_epoch": 0,
  "status": "ok",
  "frame": {
    "index": 150,
    "width": 1280,
    "height": 720,
    "frame_ref": null
  },
  "detections": [
    {
      "label": "chair",
      "confidence": 0.91,
      "bbox_xyxy": [
        0.38,
        0.28,
        0.56,
        0.84
      ],
      "track_id": "7"
    }
  ],
  "error": null
}
```


**Successful empty observation, at t=6200 ms**

```json
{
  "schema_version": "1.0",
  "run_id": "01234567-89ab-4cde-8fab-0123456789ab",
  "camera_id": "cam01",
  "view_id": "exit-view-v1",
  "source_kind": "video",
  "seq": 31,
  "t_ms": 6200,
  "tracker_epoch": 0,
  "status": "ok",
  "frame": {
    "index": 186,
    "width": 1280,
    "height": 720,
    "frame_ref": null
  },
  "detections": [],
  "error": null
}
```


**Unavailable observation (separate live-failure example)**

```json
{
  "schema_version": "1.0",
  "run_id": "01234567-89ab-4cde-8fab-0123456789ab",
  "camera_id": "cam01",
  "view_id": "exit-view-v1",
  "source_kind": "live",
  "seq": 26,
  "t_ms": 5200,
  "tracker_epoch": 0,
  "status": "unavailable",
  "frame": null,
  "detections": [],
  "error": "capture_failed"
}
```


**Confirmation event for the canonical fixture**

```json
{
  "schema_version": "1.0",
  "event_type": "incident_confirmed",
  "event_id": "7e37dbba-a8f3-51cc-9b1f-6ce1f36390b7",
  "incident_id": "611244ea-49cf-5644-8685-f210219194e2",
  "run_id": "01234567-89ab-4cde-8fab-0123456789ab",
  "camera_id": "cam01",
  "view_id": "exit-view-v1",
  "zone_id": "east_exit",
  "hazard_type": "blocked_exit",
  "phase": "CONFIRMED",
  "t_ms": 5000,
  "first_seen_ms": 0,
  "confirmed_at_ms": 5000,
  "resolved_at_ms": null,
  "confirmation_evidence_ms": 5000,
  "clear_evidence_ms": 0,
  "reason_code": "persistent_zone_occupancy",
  "last_positive_seq": 25,
  "evidence": {
    "seq": 25,
    "t_ms": 5000,
    "frame_index": 150,
    "frame_ref": null,
    "tracker_epoch": 0,
    "objects": [
      {
        "label": "chair",
        "confidence": 0.91,
        "bbox_xyxy": [
          0.38,
          0.28,
          0.56,
          0.84
        ],
        "track_id": "7",
        "zone_overlap": 1.0
      }
    ]
  }
}
```


**Resolution event for the same incident**

```json
{
  "schema_version": "1.0",
  "event_type": "incident_resolved",
  "event_id": "51da9bfa-b82e-59a8-a11c-7fd850547f10",
  "incident_id": "611244ea-49cf-5644-8685-f210219194e2",
  "run_id": "01234567-89ab-4cde-8fab-0123456789ab",
  "camera_id": "cam01",
  "view_id": "exit-view-v1",
  "zone_id": "east_exit",
  "hazard_type": "blocked_exit",
  "phase": "RESOLVED",
  "t_ms": 9200,
  "first_seen_ms": 0,
  "confirmed_at_ms": 5000,
  "resolved_at_ms": 9200,
  "confirmation_evidence_ms": 5000,
  "clear_evidence_ms": 3000,
  "reason_code": "observed_clear_interval",
  "last_positive_seq": 30,
  "evidence": {
    "seq": 46,
    "t_ms": 9200,
    "frame_index": 276,
    "frame_ref": null,
    "tracker_epoch": 0,
    "objects": []
  }
}
```


**Snapshot during a separate camera-failure scenario**

```json
{
  "schema_version": "1.0",
  "run_id": "01234567-89ab-4cde-8fab-0123456789ab",
  "camera_id": "cam01",
  "view_id": "exit-view-v1",
  "zone_id": "east_exit",
  "phase": "CONFIRMED",
  "observation_state": "unknown",
  "observation_reason": "source_unavailable",
  "incident_id": "611244ea-49cf-5644-8685-f210219194e2",
  "t_ms": 5200,
  "positive_evidence_ms": 0,
  "negative_run_ms": 0,
  "candidates": []
}
```


**JSON Schema — complete structural contract**

Save this as `schemas/foresmoke-v1.schema.json`. The root accepts an observation, zone config, lifecycle event, or zone snapshot. Validate a specific type against its `$defs` entry while retaining the entire definition map. Also implement semantic validators for bbox ordering, identity/time consistency, polygon validity, thresholds, and event/evidence timestamp equality; JSON Schema alone does not express these.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "ForeSmoke 1.0 shared exchange contract",
  "oneOf": [
    {
      "$ref": "#/$defs/FrameObservation"
    },
    {
      "$ref": "#/$defs/ZoneConfig"
    },
    {
      "$ref": "#/$defs/HazardEvent"
    },
    {
      "$ref": "#/$defs/ZoneState"
    }
  ],
  "$defs": {
    "Detection": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "label",
        "confidence",
        "bbox_xyxy",
        "track_id"
      ],
      "properties": {
        "label": {
          "type": "string",
          "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"
        },
        "confidence": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "bbox_xyxy": {
          "type": "array",
          "items": {
            "type": "number",
            "minimum": 0,
            "maximum": 1
          },
          "minItems": 4,
          "maxItems": 4
        },
        "track_id": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "null"
            }
          ]
        }
      }
    },
    "Candidate": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "label",
        "confidence",
        "bbox_xyxy",
        "track_id",
        "zone_overlap"
      ],
      "properties": {
        "label": {
          "type": "string",
          "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"
        },
        "confidence": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "bbox_xyxy": {
          "type": "array",
          "items": {
            "type": "number",
            "minimum": 0,
            "maximum": 1
          },
          "minItems": 4,
          "maxItems": 4
        },
        "track_id": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "null"
            }
          ]
        },
        "zone_overlap": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        }
      }
    },
    "FrameMeta": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "index",
        "width",
        "height",
        "frame_ref"
      ],
      "properties": {
        "index": {
          "type": "integer",
          "minimum": 0
        },
        "width": {
          "type": "integer",
          "minimum": 1
        },
        "height": {
          "type": "integer",
          "minimum": 1
        },
        "frame_ref": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "null"
            }
          ]
        }
      }
    },
    "FrameObservation": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "schema_version",
        "run_id",
        "camera_id",
        "view_id",
        "source_kind",
        "seq",
        "t_ms",
        "tracker_epoch",
        "status",
        "frame",
        "detections",
        "error"
      ],
      "properties": {
        "schema_version": {
          "const": "1.0"
        },
        "run_id": {
          "type": "string",
          "format": "uuid"
        },
        "camera_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "view_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "source_kind": {
          "enum": [
            "live",
            "video",
            "image"
          ]
        },
        "seq": {
          "type": "integer",
          "minimum": 0
        },
        "t_ms": {
          "type": "integer",
          "minimum": 0
        },
        "tracker_epoch": {
          "type": "integer",
          "minimum": 0
        },
        "status": {
          "enum": [
            "ok",
            "unavailable",
            "eof"
          ]
        },
        "frame": {
          "anyOf": [
            {
              "$ref": "#/$defs/FrameMeta"
            },
            {
              "type": "null"
            }
          ]
        },
        "detections": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/Detection"
          },
          "minItems": 0
        },
        "error": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "allOf": [
        {
          "if": {
            "properties": {
              "status": {
                "const": "ok"
              }
            }
          },
          "then": {
            "properties": {
              "frame": {
                "$ref": "#/$defs/FrameMeta"
              },
              "error": {
                "type": "null"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "status": {
                "enum": [
                  "unavailable",
                  "eof"
                ]
              }
            }
          },
          "then": {
            "properties": {
              "frame": {
                "type": "null"
              },
              "detections": {
                "maxItems": 0
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "status": {
                "const": "unavailable"
              }
            }
          },
          "then": {
            "properties": {
              "error": {
                "type": "string",
                "minLength": 1
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "status": {
                "const": "eof"
              }
            }
          },
          "then": {
            "properties": {
              "error": {
                "type": "null"
              }
            }
          }
        }
      ]
    },
    "Zone": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "zone_id",
        "hazard_type",
        "polygon",
        "candidate_labels",
        "min_confidence",
        "footprint_height_fraction",
        "enter_overlap",
        "exit_overlap",
        "person_occlusion_ratio",
        "confirm_ms",
        "candidate_clear_ms",
        "resolve_ms"
      ],
      "properties": {
        "zone_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "hazard_type": {
          "const": "blocked_exit"
        },
        "polygon": {
          "type": "array",
          "items": {
            "type": "array",
            "items": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            },
            "minItems": 2,
            "maxItems": 2
          },
          "minItems": 3
        },
        "candidate_labels": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"
          },
          "minItems": 1,
          "uniqueItems": true
        },
        "min_confidence": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "footprint_height_fraction": {
          "type": "number",
          "exclusiveMinimum": 0,
          "maximum": 1
        },
        "enter_overlap": {
          "type": "number",
          "exclusiveMinimum": 0,
          "maximum": 1
        },
        "exit_overlap": {
          "type": "number",
          "exclusiveMinimum": 0,
          "maximum": 1
        },
        "person_occlusion_ratio": {
          "type": "number",
          "exclusiveMinimum": 0,
          "maximum": 1
        },
        "confirm_ms": {
          "type": "integer",
          "minimum": 1
        },
        "candidate_clear_ms": {
          "type": "integer",
          "minimum": 1
        },
        "resolve_ms": {
          "type": "integer",
          "minimum": 1
        }
      }
    },
    "ZoneConfig": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "schema_version",
        "camera_id",
        "view_id",
        "reference_frame",
        "max_observation_gap_ms",
        "live_stale_after_ms",
        "zones"
      ],
      "properties": {
        "schema_version": {
          "const": "1.0"
        },
        "camera_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "view_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "reference_frame": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "width",
            "height"
          ],
          "properties": {
            "width": {
              "type": "integer",
              "minimum": 1
            },
            "height": {
              "type": "integer",
              "minimum": 1
            }
          }
        },
        "max_observation_gap_ms": {
          "type": "integer",
          "minimum": 1
        },
        "live_stale_after_ms": {
          "type": "integer",
          "minimum": 1
        },
        "zones": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/Zone"
          },
          "minItems": 1
        }
      }
    },
    "Evidence": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "seq",
        "t_ms",
        "frame_index",
        "frame_ref",
        "tracker_epoch",
        "objects"
      ],
      "properties": {
        "seq": {
          "type": "integer",
          "minimum": 0
        },
        "t_ms": {
          "type": "integer",
          "minimum": 0
        },
        "frame_index": {
          "type": "integer",
          "minimum": 0
        },
        "frame_ref": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "null"
            }
          ]
        },
        "tracker_epoch": {
          "type": "integer",
          "minimum": 0
        },
        "objects": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/Candidate"
          },
          "minItems": 0
        }
      }
    },
    "HazardEvent": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "schema_version",
        "event_type",
        "event_id",
        "incident_id",
        "run_id",
        "camera_id",
        "view_id",
        "zone_id",
        "hazard_type",
        "phase",
        "t_ms",
        "first_seen_ms",
        "confirmed_at_ms",
        "resolved_at_ms",
        "confirmation_evidence_ms",
        "clear_evidence_ms",
        "reason_code",
        "last_positive_seq",
        "evidence"
      ],
      "properties": {
        "schema_version": {
          "const": "1.0"
        },
        "event_type": {
          "enum": [
            "incident_confirmed",
            "incident_resolved"
          ]
        },
        "event_id": {
          "type": "string",
          "format": "uuid"
        },
        "incident_id": {
          "type": "string",
          "format": "uuid"
        },
        "run_id": {
          "type": "string",
          "format": "uuid"
        },
        "camera_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "view_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "zone_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "hazard_type": {
          "const": "blocked_exit"
        },
        "phase": {
          "enum": [
            "CONFIRMED",
            "RESOLVED"
          ]
        },
        "t_ms": {
          "type": "integer",
          "minimum": 0
        },
        "first_seen_ms": {
          "type": "integer",
          "minimum": 0
        },
        "confirmed_at_ms": {
          "type": "integer",
          "minimum": 0
        },
        "resolved_at_ms": {
          "anyOf": [
            {
              "type": "integer",
              "minimum": 0
            },
            {
              "type": "null"
            }
          ]
        },
        "confirmation_evidence_ms": {
          "type": "integer",
          "minimum": 0
        },
        "clear_evidence_ms": {
          "type": "integer",
          "minimum": 0
        },
        "reason_code": {
          "enum": [
            "persistent_zone_occupancy",
            "observed_clear_interval"
          ]
        },
        "last_positive_seq": {
          "type": "integer",
          "minimum": 0
        },
        "evidence": {
          "$ref": "#/$defs/Evidence"
        }
      },
      "allOf": [
        {
          "if": {
            "properties": {
              "event_type": {
                "const": "incident_confirmed"
              }
            }
          },
          "then": {
            "properties": {
              "phase": {
                "const": "CONFIRMED"
              },
              "resolved_at_ms": {
                "type": "null"
              },
              "clear_evidence_ms": {
                "const": 0
              },
              "reason_code": {
                "const": "persistent_zone_occupancy"
              },
              "evidence": {
                "properties": {
                  "objects": {
                    "minItems": 1
                  }
                }
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "event_type": {
                "const": "incident_resolved"
              }
            }
          },
          "then": {
            "properties": {
              "phase": {
                "const": "RESOLVED"
              },
              "resolved_at_ms": {
                "type": "integer",
                "minimum": 0
              },
              "reason_code": {
                "const": "observed_clear_interval"
              },
              "evidence": {
                "properties": {
                  "objects": {
                    "maxItems": 0
                  }
                }
              }
            }
          }
        }
      ]
    },
    "ZoneState": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "schema_version",
        "run_id",
        "camera_id",
        "view_id",
        "zone_id",
        "phase",
        "observation_state",
        "observation_reason",
        "incident_id",
        "t_ms",
        "positive_evidence_ms",
        "negative_run_ms",
        "candidates"
      ],
      "properties": {
        "schema_version": {
          "const": "1.0"
        },
        "run_id": {
          "anyOf": [
            {
              "type": "string",
              "format": "uuid"
            },
            {
              "type": "null"
            }
          ]
        },
        "camera_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "view_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "zone_id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        },
        "phase": {
          "enum": [
            "CLEAR",
            "POSSIBLE",
            "CONFIRMED",
            "RESOLVED"
          ]
        },
        "observation_state": {
          "enum": [
            "observed",
            "unknown"
          ]
        },
        "observation_reason": {
          "enum": [
            "positive",
            "negative",
            "initializing",
            "source_unavailable",
            "source_ended",
            "occluded",
            "stale_input",
            "observation_gap",
            "invalid_input"
          ]
        },
        "incident_id": {
          "anyOf": [
            {
              "type": "string",
              "format": "uuid"
            },
            {
              "type": "null"
            }
          ]
        },
        "t_ms": {
          "anyOf": [
            {
              "type": "integer",
              "minimum": 0
            },
            {
              "type": "null"
            }
          ]
        },
        "positive_evidence_ms": {
          "type": "integer",
          "minimum": 0
        },
        "negative_run_ms": {
          "type": "integer",
          "minimum": 0
        },
        "candidates": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/Candidate"
          },
          "minItems": 0
        }
      }
    }
  }
}
```


### Required command-line interoperability

These are target commands the implementation must provide; the briefs themselves do not constitute a built application. Paths are relative to the eventual implementation repository.

```text
python -m foresmoke.perception --source demo.mp4 --source-kind video --camera-id cam01 --view-id exit-view-v1 --output runs/demo/observations.jsonl
python -m foresmoke.reasoning --input runs/demo/observations.jsonl --zones configs/zones.json --events runs/demo/events.jsonl --snapshots runs/demo/states.jsonl
python -m foresmoke.demo --source 0 --source-kind live --zones configs/zones.json --run-dir runs/live --preview
python -m foresmoke.reasoning --input tests/fixtures/blocked_then_clear.jsonl --zones configs/zones.json --events runs/fixture/events.jsonl --snapshots runs/fixture/states.jsonl
```

Person 1 owns `foresmoke.demo`, using the perception iterator, Person 2's engine, and the camera/view identities from the zone config. Preview is an optional OpenCV window; the event loop must also work headlessly. Person 1 can keep the last two frames in an in-process preview cache keyed by `seq`; if a frame is already unavailable, skip that preview rather than drawing boxes on a different image. Image mode exercises detection/geometry only and cannot accumulate a five-second incident from one image.

### Integration fixtures and acceptance sequence

Person 2 creates deterministic JSONL fixtures at 5 Hz (`t_ms=0,200,400,...`), with valid frame metadata and UUID run IDs. Both people use the same files. An EOF packet uses the next sequence number and the last source timestamp.

1. **Blocked then clear:** chair footprint fully inside from t=0 through t=6000 inclusive; empty frames from t=6200 through t=10000. Expect POSSIBLE at zero, confirmation at 5000, resolution at 9200, and exactly two events sharing one incident ID.
2. **Brief obstruction:** positive through 2000; negative starting at 2200. Expect no incident and CLEAR at 3200.
3. **Walking person:** person only; no incident. Large overlap may make observation unknown, which is expected.
4. **Outside zone:** eligible object whose bottom strip is outside; no incident even if its upper bounding box crosses the zone.
5. **ID switch:** change track ID at 2600 while occupancy remains positive. Confirm at 5000 with one incident.
6. **Brief detector miss:** in a continuously positive sequence, replace only t=2000 with a visible empty frame. Confirmation moves from 5000 to 5400 because the two 200 ms intervals bordering the miss add no positive evidence.
7. **Failure after confirmation:** confirm at 5000, emit unavailable at 5200, then later EOF. Expect only confirmation, an active incident, and unknown observation state.
8. **Recovery:** after that failure, send visible empty frames at 10000,10200,...,13000. Resolve at 13000; do not credit the missing interval.
9. **Large gap:** positive at 0 and 10000 only cannot confirm. A gap after confirmation cannot resolve it.
10. **Two objects:** two qualifying objects in the same zone still create one incident. Removing one while another remains never resolves it.
11. **New occurrence:** a positive period after resolution creates a different incident ID after a fresh five-second qualification.
12. **Bad input:** invalid bbox, unknown schema version, bad polygon, duplicate seq, out-of-order timestamps, wrong view/aspect/run, and low-confidence objects behave as specified. In particular, duplicate sequences cannot advance timers.

The actual video demo also needs a clear baseline, a person walk-through, an obstruction held for at least eight seconds, removal observed for at least four seconds, and a simulated camera failure while confirmed. Run the same recording at different processing speeds: source-time event timestamps must be identical.

### One-day team schedule and stop rules

| Elapsed time | Person 1 | Person 2 | Joint checkpoint |
|---|---|---|---|
| 0:00–0:30 | Shared models and model smoke test | Zone config and sample observations | Freeze contract and pick physical prop/view |
| 0:30–2:00 | Capture, detection, JSONL | Geometry and synthetic fixtures | Exchange real observations before hour 2 |
| 2:00–3:30 | Tracking and health handling | Temporal engine, event IDs | Fixture lifecycle passes |
| 3:30–4:30 | Integration runner | Connect engine and fix boundary issues | First end-to-end recorded demo |
| 4:30–6:00 | Performance, live feed, preview | Failures, dropout tests, thresholds | Live demo plus camera-loss test |
| 6:00–7:00 | Stable recording and environment lock | Acceptance report and event evidence | Freeze features |
| 7:00–8:00 | Rehearse and package | Rehearse and explain rules | Three successful demonstrations |

If model setup consumes more than 30 minutes, use a previously working small COCO model through the same adapter and record the substitution. If live-camera integration is still failing at hour 5, finish the prerecorded-video demonstration. If inference cadence falls below two usable observations per second, reduce inference size, choose a smaller model, or use a suitable machine; do not silently weaken temporal semantics. Any timing/config changes must be shared and rerun against fixtures.

No dashboard, HTTP service, message broker, database, Docker requirement, training pipeline, phone-stream integration, or extra model may displace the working lifecycle demo. Only begin stretch goals after the shared acceptance sequence passes.

