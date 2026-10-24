"""Export Pydantic structural schemas; semantic validators remain authoritative."""
import argparse
import json
from pathlib import Path
from .contracts import FrameObservation, ZoneConfig, HazardEvent, ZoneState

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="schemas/generated")
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    for model in (FrameObservation, ZoneConfig, HazardEvent, ZoneState):
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
        (directory / f"{model.__name__}.schema.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
