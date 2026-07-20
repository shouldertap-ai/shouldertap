"""Regenerates spec/schemas/*.json from the Pydantic contracts and built-in kind schemas --
these are never hand-written. Run after changing engine/contracts.py or engine/capture.py's
BUILTIN_KIND_SCHEMAS:

    uv run python scripts/generate_schemas.py

tests/acceptance/test_schema_drift.py fails the build if the committed files fall out of sync.
"""

from __future__ import annotations

import json
from pathlib import Path

from shouldertap.engine.capture import BUILTIN_KIND_SCHEMAS
from shouldertap.engine.contracts import (
    ConsumerRegistration,
    ContextProposal,
    ContextRequest,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMAS_DIR = _REPO_ROOT / "spec" / "schemas"

_MODEL_SCHEMAS = {
    "context_request.json": ContextRequest,
    "context_proposal.json": ContextProposal,
    "consumer_registration.json": ConsumerRegistration,
}

_KIND_FILENAMES = {
    "glossary.definition": "glossary_definition.json",
    "freeform.answer": "freeform_answer.json",
}


def generate() -> dict[Path, str]:
    """Returns {output_path: contents}, without touching disk -- generate_and_write() does
    that, and the drift test compares this dict's contents against what's already on disk.
    """
    files: dict[Path, str] = {}

    for filename, model in _MODEL_SCHEMAS.items():
        schema = model.model_json_schema()
        files[_SCHEMAS_DIR / filename] = json.dumps(schema, indent=2, sort_keys=True) + "\n"

    for kind, filename in _KIND_FILENAMES.items():
        schema = BUILTIN_KIND_SCHEMAS[kind]
        files[_SCHEMAS_DIR / "kinds" / filename] = (
            json.dumps(schema, indent=2, sort_keys=True) + "\n"
        )

    return files


def generate_and_write() -> None:
    for path, contents in generate().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)


if __name__ == "__main__":
    generate_and_write()
    print(f"Wrote {len(generate())} schema files under {_SCHEMAS_DIR}")
