# ForeSmoke — Person 2 reasoning MVP

A working Python engine that turns detector observations into persistent exit-zone obstruction
incidents. It confirms after **5 seconds of positive evidence** and resolves after **3 seconds
of observed clearance**. Missing data, camera loss, and person occlusion never count as clearance.

**Current demo: synthetic observations, not camera footage.** The example zone must be
calibrated to Person 1's actual camera before interpreting real detections.

## Quick start on Windows

The project environment is already installed in .venv. From this repository:

    .\.venv\Scripts\python.exe run_reasoning_demo.py

The demo prints confirmation at **5000 ms**, resolution at **9200 ms**, and writes:

- runs/fixture/events.jsonl — two lifecycle events sharing one incident ID.
- runs/fixture/states.jsonl — lifecycle and observation states for each packet.
- runs/fixture/observations.jsonl — the synthetic stream used.

The demo helper replaces only these generated demo outputs on each run.

For a fresh checkout, use Python 3.11:

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements-reasoning.lock.txt
    .\.venv\Scripts\python.exe -m pip install --no-deps -e .
    .\.venv\Scripts\python.exe run_reasoning_demo.py

Alternatively, resolve compatible dependencies:

    .\.venv\Scripts\python.exe -m pip install -e ".[reasoning,dev]"

No Torch, Ultralytics, camera, GPU, or model download is required for Person 2.
On macOS/Linux use .venv/bin/python instead of the Windows environment executable.

## Replay Person 1's observations

    .\.venv\Scripts\python.exe -m foresmoke.reasoning --input runs/input/observations.jsonl --zones configs/zones.json --events runs/replay/events.jsonl --snapshots runs/replay/states.jsonl

Use a new run directory for each recording. The --force flag explicitly permits replacing
existing outputs; inputs and configuration are protected from output-path collisions.
The --debug flag logs overlap, classification, credited time, and state transitions.
Diagnostics go to stderr; event and state files contain JSON only.

Replay preserves source timestamps, so accelerated playback does not shorten thresholds.
Even a recording labeled source_kind=live has the wall-clock watchdog disabled in file replay.

For a live JSONL pipe, use --input -. A separate reader thread allows freshness checks every
250 ms. The two-second watchdog applies when packets declare source_kind=live. PowerShell
pipelines may buffer; prefer the in-process API for the final camera integration.

## Test and benchmark

    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\python.exe -m foresmoke.reasoning.benchmark
    .\.venv\Scripts\python.exe -m foresmoke.reasoning.fixtures
    .\.venv\Scripts\python.exe -m foresmoke.shared.export_schemas

The delivered run passed **86 tests**. Measured p95 reasoning latency was **2.184 ms**
for 50 synthetic detections and one zone. This excludes camera/model time.
See docs/ACCEPTANCE.md for measurements, coverage, and remaining real-world checks.

## What is implemented

- Strict Pydantic models and the original JSON Schema from the brief.
- Validated polygons and actual bottom-strip/zone intersection area, including concave zones.
- Class/score filtering, overlap hysteresis, and person-occlusion handling.
- CLEAR → POSSIBLE → CONFIRMED → RESOLVED, with separate observed/unknown status.
- Stable incident/event IDs; occupancy tolerates tracker ID changes and null IDs.
- Timestamp/run/view/aspect validation, duplicate suppression, watchdogs, and safe EOF handling.
- Replay CLI, streaming stdin, 19 synthetic fixtures, tests, and a benchmark.
- Independently configured zones; the default demo uses one.

## Package boundaries

| Path | Purpose |
|---|---|
| foresmoke/reasoning/engine.py | Public API and temporal state |
| foresmoke/reasoning/geometry.py | Spatial classifier |
| foresmoke/reasoning/config.py | Configuration and geometric validation |
| foresmoke/reasoning/events.py | Stable IDs and summaries |
| foresmoke/reasoning/__main__.py | Replay and streaming CLI |
| foresmoke/shared/contracts.py | Shared model bootstrap for Person 1 to adopt |
| configs/zones.json | **Synthetic, uncalibrated** example zone |
| schemas/foresmoke-v1.schema.json | Exact contract from the supplied brief |
| schemas/generated/ | Exported Pydantic structural schemas |
| tests/fixtures/ | Synthetic normal and intentionally malformed inputs |
| docs/PERSON_1_HANDOFF.md | Integration API and ownership notes |
| docs/CALIBRATION.md | Replacing the example zone with a real camera view |

No shared implementation existed, so Person 2 bootstrapped the contract module from the brief.
Person 1 should reuse/adopt this module rather than maintaining another copy. Shared imports
do not load geometry or detector libraries.

## Limitations

This detects configured visual zone occupancy, not smoke, fire, legal compliance, physical
clearance width, or guaranteed exit safety. People are excluded as obstruction candidates.
The person-occlusion heuristic is incomplete; a healthy detector can still miss an object.
Open incidents are not persisted/restored across acquisition runs.

Person 1's camera/detector and final preview runner remain Person 1's work. No real recording
or camera reference image was available. The synthetic tests verify reasoning rules;
real detection accuracy, zone calibration, and the complete live demonstration remain unverified.

