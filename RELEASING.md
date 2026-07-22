# Releasing

ShoulderTap publishes to PyPI via GitHub Actions using [Trusted Publishing][tp] (OIDC), so no
long-lived PyPI API token is ever stored in this repository.

[tp]: https://docs.pypi.org/trusted-publishers/

## One-time setup (before the first release)

This has to happen on the PyPI side — it can't be scripted from here.

1. **Reserve the name.** `shouldertap` was unclaimed as of this writing; whoever publishes
   first owns it. Consider doing the TestPyPI dry run below early just to establish it.

2. **Register the trusted publisher on PyPI.** Go to
   <https://pypi.org/manage/account/publishing/> and add a *pending* publisher (this works
   even before the project exists on PyPI):

   | Field | Value |
   |---|---|
   | PyPI project name | `shouldertap` |
   | Owner | `shouldertap-ai` |
   | Repository name | `shouldertap` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. **Repeat on TestPyPI** at <https://test.pypi.org/manage/account/publishing/>, using
   environment name `testpypi`.

4. **Create the two GitHub environments** (Settings → Environments): `pypi` and `testpypi`.
   Adding required reviewers to `pypi` is recommended — it makes publishing an explicit,
   approved action rather than an automatic consequence of tagging.

## Dry run to TestPyPI

Actions → *Publish to PyPI* → **Run workflow** → target `testpypi`. Then verify the artifact
installs cleanly *from the index*, in a throwaway venv:

```bash
uv venv /tmp/st-testpypi --python 3.12
uv pip install --python /tmp/st-testpypi/bin/python3.12 \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  shouldertap
cd "$(mktemp -d)" && /tmp/st-testpypi/bin/shtap init --org-name Acme && /tmp/st-testpypi/bin/shtap serve --transport console
```

(The `--extra-index-url` is needed because TestPyPI doesn't mirror real dependencies.)

## Cutting a real release

1. Bump `version` in `pyproject.toml` (this project follows [SemVer][semver]; `0.x` means the
   protocol in `spec/` may still shift between minors).
2. Commit, then tag and push:
   ```bash
   git tag -a v0.1.0 -m "v0.1.0"
   git push origin v0.1.0
   ```
3. Create a GitHub Release for that tag. Publishing the release triggers `publish.yml`, which
   re-runs the full test suite **plus the wheel-install acceptance test** before uploading.

[semver]: https://semver.org/

## Why the wheel-install test gates the release

`tests/acceptance/test_wheel_install.py` builds a real wheel, installs it into a clean venv
with no access to the source tree, and drives an actual tap through it.

This exists because an *editable* install (`pip install -e .`) still resolves paths back into
the checkout, so it structurally cannot catch files that were never packaged. That is exactly
how the Alembic migrations once shipped broken: every test passed, and `pip install shouldertap`
still died on first `shtap serve` with `Can't find Python file .../site-packages/alembic/env.py`.
Any file the engine loads from disk at runtime — migrations, prompt templates, the approval-UI
assets — must be inside `shouldertap/`, and this test is what proves it stayed that way.

## Manual publish (fallback)

If Actions is unavailable:

```bash
uv build
uv run --with twine twine check dist/*
uv run --with twine twine upload dist/*   # prompts for a PyPI API token
```
