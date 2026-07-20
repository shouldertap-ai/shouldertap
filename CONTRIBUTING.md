# Contributing to ShoulderTap

Thanks for considering a contribution.

## Developer Certificate of Origin

This project uses the [Developer Certificate of Origin](https://developercertificate.org/) (DCO) instead of a CLA. Every commit must be signed off, certifying that you wrote it (or otherwise have the right to submit it under the project's license):

```
git commit -s -m "your commit message"
```

This appends a `Signed-off-by: Your Name <your.email@example.com>` line to the commit message. Pull requests with unsigned commits will be asked to amend before merge.

## Development setup

```
uv sync
uv run pytest
uv run mypy --strict shouldertap/
uv run ruff check .
uv run ruff format --check .
```

## Code style

- Type-checked with `mypy --strict`; keep it clean rather than reaching for `# type: ignore`.
- Formatted and linted with `ruff`.
- No new abstractions beyond what a change needs — see the project's own bias toward simplicity in `spec/`.

## Where things live

See `spec/protocol.md` for the stable public contracts (`ContextRequest`/`ContextProposal`, HTTP API, MCP tool interface) — changes to these are protocol changes and should be flagged as such in your PR description.
