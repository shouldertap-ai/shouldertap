"""spec/schemas/*.json are generated, never hand-written (see scripts/generate_schemas.py).
This fails the build if they've fallen out of sync with the contracts/kind schemas they're
derived from -- run `uv run python scripts/generate_schemas.py` to fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from generate_schemas import generate  # noqa: E402


def test_committed_schemas_match_generated_output() -> None:
    stale = []
    for path, expected in generate().items():
        actual = path.read_text() if path.exists() else None
        if actual != expected:
            stale.append(path)

    assert not stale, (
        f"spec/schemas/*.json out of date: {stale}. "
        "Run `uv run python scripts/generate_schemas.py` and commit the result."
    )
