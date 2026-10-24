"""Replay JSONL or consume a live stdin stream without a dashboard."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import logging
from pathlib import Path
import queue
import sys
import threading
import time
from .config import load_config
from .engine import ReasoningEngine
from .events import explain
from foresmoke.shared.contracts import FrameObservation

LOG = logging.getLogger(__name__)
_END = object()

def _stdin_reader(messages: queue.Queue) -> None:
    try:
        for number, line in enumerate(sys.stdin, 1):
            messages.put((number, line))
    except Exception as error:
        messages.put(error)
    finally:
        messages.put(_END)

def _clock_ms() -> int:
    return time.monotonic_ns() // 1_000_000

def _write_states(engine, handle) -> None:
    for state in engine.snapshots():
        handle.write(state.model_dump_json() + "\n")
    handle.flush()

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Observation JSONL path, or - for streaming stdin")
    parser.add_argument("--zones", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--force", action="store_true", help="Replace existing output files")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(levelname)s %(message)s")
    engine, states = None, None
    try:
        protected = [Path(args.zones).resolve()]
        if args.input != "-":
            protected.append(Path(args.input).resolve())
        outputs = [Path(args.events).resolve(), Path(args.snapshots).resolve()]
        if outputs[0] == outputs[1] or any(p in protected for p in outputs):
            raise ValueError("events/snapshots must be distinct and cannot overwrite inputs or configuration")
        for p in outputs:
            if p.exists() and not args.force:
                raise FileExistsError(f"{p} exists; use a new run directory or --force")
        engine = ReasoningEngine(load_config(args.zones), live_watchdog=args.input == "-")
        count, event_count, explicit_eof = 0, 0, False
        with ExitStack() as stack:
            source = None if args.input == "-" else stack.enter_context(open(args.input, encoding="utf-8-sig"))
            for p in outputs:
                p.parent.mkdir(parents=True, exist_ok=True)
            events = stack.enter_context(outputs[0].open("w" if args.force else "x", encoding="utf-8"))
            states = stack.enter_context(outputs[1].open("w" if args.force else "x", encoding="utf-8"))
            messages = None
            if source is None:
                messages = queue.Queue(maxsize=2)
                threading.Thread(target=_stdin_reader, args=(messages,), daemon=True).start()
                iterator = None
            else:
                iterator = iter(enumerate(source, 1))
            try:
                while True:
                    if messages is not None:
                        try:
                            item = messages.get(timeout=0.25)
                        except queue.Empty:
                            before = engine.snapshots()
                            engine.tick(_clock_ms())
                            if engine.snapshots() != before:
                                _write_states(engine, states)
                            continue
                        if item is _END:
                            break
                        if isinstance(item, Exception):
                            raise item
                        number, line = item
                    else:
                        item = next(iterator, None)
                        if item is None:
                            break
                        number, line = item
                    if not line.strip():
                        raise ValueError(f"line {number}: blank lines are not observation records")
                    try:
                        packet = FrameObservation.model_validate_json(line)
                        new_events = engine.process(packet, _clock_ms())
                    except (ValueError, TypeError) as error:
                        raise ValueError(f"line {number}: {error}") from error
                    count += 1
                    for event in new_events:
                        events.write(event.model_dump_json() + "\n")
                        LOG.info("%s | %s", event.event_type, explain(event))
                    events.flush()
                    event_count += len(new_events)
                    _write_states(engine, states)
                    if engine.input_ended:
                        explicit_eof = True
                        break
                if not explicit_eof:
                    engine.close_input()
                    _write_states(engine, states)
            except KeyboardInterrupt:
                engine.close_input()
                _write_states(engine, states)
                LOG.warning("Input interrupted; active incidents retained")
                return 130
            except Exception:
                engine.invalidate_input()
                _write_states(engine, states)
                raise
        LOG.info("Processed %s records; emitted %s lifecycle events", count, event_count)
        return 0
    except (OSError, ValueError, TypeError) as error:
        LOG.error("%s", error)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())

