"""Export Pydantic structural schemas; semantic validators remain authoritative."""
import argparse
import json
from pathlib import Path
from .contracts import FrameObservation, ZoneConfig, HazardEvent, ZoneState

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="schemas/generated")
    parser.add_argument("--combined", default="schemas/foresmoke-v1.schema.json")
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    models = (FrameObservation, ZoneConfig, HazardEvent, ZoneState)
    definitions = {}
    for model in models:
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **model.model_json_schema()}
        (directory / f"{model.__name__}.schema.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        combined_part = model.model_json_schema(ref_template="#/$defs/{model}")
        definitions.update(combined_part.pop("$defs", {}))
        definitions[model.__name__] = combined_part
    combined = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ForeSmoke shared exchange contract",
        "oneOf": [{"$ref": f"#/$defs/{model.__name__}"} for model in models],
        "$defs": definitions,
    }
    combined_path = Path(args.combined)
    combined_path.parent.mkdir(parents=True, exist_ok=True)
    combined_path.write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()

