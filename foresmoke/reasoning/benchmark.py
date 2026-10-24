"""Measure real reasoning latency on synthetic 50-object observations."""
import argparse
import importlib.metadata
import json
import math
import platform
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from .config import load_config
from .engine import ReasoningEngine
from .fixtures import CHAIR, observation

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zones", default="configs/zones.json")
    parser.add_argument("--output", default="runs/benchmark.json")
    parser.add_argument("--samples", type=int, default=1000)
    args = parser.parse_args()
    if args.samples < 100:
        parser.error("--samples must be at least 100")
    engine = ReasoningEngine(load_config(args.zones))
    detections = [{**CHAIR, "track_id": str(i)} for i in range(50)]
    timings = []
    for i in range(100 + args.samples):
        packet = observation(i * 200, detections=detections)
        start = perf_counter_ns()
        engine.process(packet, i)
        elapsed_ms = (perf_counter_ns() - start) / 1_000_000
        if i >= 100:
            timings.append(elapsed_ms)
    timings.sort()
    result = {
        "data": "SYNTHETIC; 50 detections, one configured zone",
        "measurement": "process(): validation + geometry + temporal rules; excludes capture/model/file I/O",
        "warmup_frames": 100, "sample_frames": args.samples,
        "p50_ms": median(timings), "p95_ms": timings[math.ceil(len(timings) * .95) - 1],
        "max_ms": max(timings), "python": platform.python_version(), "platform": platform.platform(),
        "dependencies": {name: importlib.metadata.version(name) for name in ("pydantic", "shapely", "numpy")},
    }
    result["p95_under_10ms_target_met"] = result["p95_ms"] < 10
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()

