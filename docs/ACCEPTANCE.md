# Person 2 acceptance report

Implemented and checked on 2026-10-24. All observation data was **synthetic**.

## Results

- Automated tests: **86 passed**, zero failures/errors.
- Final pytest run: 7.71 seconds; XML evidence in runs/acceptance-tests.xml.
- Editable installation succeeded; pip check found no broken requirements.
- Canonical events: confirmation at 5000 ms, resolution at 9200 ms, shared stable incident ID.
- Outputs validate against the original JSON Schema from the supplied brief.
- Live stdin stall was exercised with an actual subprocess timeout. The active incident
  became unknown, with no fabricated resolution.
- Snapshot/config isolation, independent zones, and detector-free imports passed.

## Measured performance

Machine: Windows-10-10.0.26200-SP0, Python 3.11.9.
100 warmup frames, 1000 measured frames, 50 detections and one zone per frame.

| Metric | Value |
|---|---:|
| Median processing time | 1.388 ms |
| p95 processing time | 2.184 ms |
| Maximum processing time | 3.120 ms |
| p95 under 10 ms target | Passed |

Includes validation, geometry, and temporal reasoning. Excludes capture, inference, preview,
and file I/O. Raw evidence: runs/benchmark.json.

## Coverage

Persistent/brief occupancy; outside footprints; exact thresholds; hysteresis; concave polygons;
person occlusion; score filtering; ignored classes; null IDs; tracker changes; short misses;
long gaps; camera failure/recovery; multiple objects/zones; repeat incidents; deterministic
IDs/replay; malformed fields; invalid polygons; identity/aspect/time mismatches; duplicates;
live stale input; EOF/implicit close/duplicate EOF; single-image duration; malformed JSON after
confirmation; output protection; stderr separation; real streaming stdin watchdog behavior.

19 JSONL fixtures: 13 valid/retry scenarios and 6 intentionally invalid streams.
tests/fixtures/expected_events.json comes directly from the brief's examples.
tests/fixtures/expected_transitions.json lists the exact expected times.

## Integration status

Person 2's engine, replay/streaming CLI, fixtures, shared-model bootstrap, schemas, tests, and
documentation are complete. Person 1 has not yet supplied observations or a reference image.

Pending real-world checks:
1. Calibrate the example zone to the actual fixed camera.
2. Validate real detector output against the shared models.
3. Replay a real scene at different processing speeds.
4. Connect live detection/preview and the independent watchdog.
5. Rehearse person walk-through, chair obstruction, removal, and camera failure.

These checks require Person 1's inputs. No real camera accuracy, exit calibration, or complete
camera demonstration is claimed. No dashboard, smoke/fire model, physical clearance
measurement, or legal safety judgment is implemented.

## Environment and contract

Exact versions: requirements-reasoning.lock.txt.
Pydantic 2.14.1, Shapely 2.2.0,
NumPy 2.4.6. No model weights/GPU libraries are required.

Wire contract unchanged. Shared definitions were bootstrapped because none existed.
Additive convenience APIs: live_watchdog=False for file replay, invalidate_input for parse
errors, read-only input_ended. Geometry comparisons tolerate 1e-12 floating-point error.
