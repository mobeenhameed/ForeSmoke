# Person 1 integration handoff

## Ready-to-use API

    from foresmoke.shared.contracts import FrameObservation
    from foresmoke.reasoning import ReasoningEngine, load_config

    engine = ReasoningEngine(load_config("configs/zones.json"))
    events = engine.process(packet, received_ms=monotonic_ms)
    states = engine.snapshots()
    engine.tick(now_ms=monotonic_ms)
    engine.close_input()

Use integer monotonic milliseconds from one consumer clock origin. Evidence uses packet
t_ms, never receipt time. Serialize all engine calls on one consumer thread.

process accepts the specified FrameObservation model, or a dictionary as a convenience.
It returns newly emitted events only. Snapshots are independent copies. Malformed input raises
ValueError/Pydantic ValidationError and resets continuity; confirmed incidents remain active
and unknown. Duplicate/decreasing sequences are logged and ignored. Identity mismatches fail.

Call tick at least every 250 ms independently of capture/inference. close_input ends observation
without resolving incidents. Accepted EOF packets do the same. input_ended is a read-only
property; ignored duplicate EOF records do not end input.

## Consumer example

Your capture/inference producer owns the bounded queue. For live input use capacity two,
dropping the oldest when full. Do not let a blocked model stop this consumer:

    from queue import Empty
    from time import monotonic_ns

    def now_ms():
        return monotonic_ns() // 1_000_000

    while not engine.input_ended:
        try:
            packet = observations.get(timeout=0.25)
        except Empty:
            engine.tick(now_ms())
        else:
            if packet is None:  # local producer-finished sentinel, not wire JSON
                engine.close_input()
                break
            try:
                for event in engine.process(packet, received_ms=now_ms()):
                    event_file.write(event.model_dump_json() + "\n")
                event_file.flush()
            except ValueError as error:
                logger.error("Invalid perception packet: %s", error)
        for state in engine.snapshots():
            render_state(state)

Your runner opens/closes files and handles producer exceptions and shutdown. Draw
CONFIRMED / UNKNOWN distinctly from RESOLVED. Keep images in your preview cache; the engine
consumes no pixels. Never send cached images or predicted-only boxes as fresh observations.

The optional live_watchdog=False constructor argument is for offline replay of recorded live
packets; the default is True. invalidate_input() handles parser errors that occur before model
construction. These are additive APIs; **wire fields and baseline thresholds are unchanged**.

## Input checklist

- schema_version is 1.0; boxes are finite normalized original/unmirrored xyxy coordinates.
- Current synthetic config uses camera_id cam01 and view_id exit-view-v1.
- UUID run ID; increasing emitted sequence and successful-frame source timestamps.
- frame.index is the decoded source index, not the processed sequence number.
- Successful empty inference: status ok, empty detections, non-null frame metadata.
- Unavailable: null frame, empty detections, non-null error.
- EOF: null frame, empty detections, null error; adds no duration.
- Track IDs may be null; retain current detections that tracking has not assigned.
- Candidates: chair, backpack, suitcase. Person is only an occlusion cue.
- Gaps above 1000 ms break continuity; live freshness expires after 2000 ms.
- Still images permit one successful observation at zero and cannot create timed incidents.

The canonical fixture matches the exact event examples and IDs in both briefs.

## Ownership and compatibility

Shared contracts, schema export, package metadata, and a minimal README were bootstrapped
because the repository initially contained only briefs. Adopt these shared definitions.
Add perception extras/modules without making shared/reasoning imports depend on your detector.

The original schema remains in schemas/foresmoke-v1.schema.json. Generated schemas describe
structural types; additional semantic/time checks run in Python. Polygon topology is checked
when loading reasoning config, keeping Shapely out of the shared package.

Geometry uses 1e-12 tolerance at exact numeric thresholds. Person occlusion uses the brief's
fixed confidence threshold 0.35; obstacle confidence is zone-configurable. Person cannot be
added to the obstruction allowlist in contract 1.0.

## Integration checklist

1. Run pytest and run_reasoning_demo.py in the project environment.
2. Validate your first populated, empty, and unavailable packets against FrameObservation.
3. Replay your JSONL through the reasoning CLI.
4. Calibrate configs/zones.json using the original camera reference frame.
5. Connect the in-process API and independent watchdog.
6. Demonstrate walking person, sustained chair obstruction, removal, and camera failure.

All delivered observation fixtures are synthetic. Real-camera acceptance remains pending.
