"""Spec §2's actual promise: "`pip install shouldertap` + one YAML file + Slack app manifest =
running."

This is deliberately distinct from test_fresh_machine.py, which uses `pip install -e .` per
§15 criterion 8's literal wording. An *editable* install still points back at the source tree,
so it cannot catch the failure mode that matters most for real end users: files that live
outside the `shouldertap/` package simply aren't in the wheel. That's exactly how the Alembic
migrations broke -- an editable install found them at the repo root and passed, while a real
`pip install shouldertap` crashed on first `shtap serve` with a confusing
"Can't find Python file .../site-packages/alembic/env.py".

So this test builds a real wheel and installs it into a clean venv with no access to the
source tree, then drives the loop far enough to prove the packaged migrations, prompt
templates, and approval UI assets are all genuinely shipped.

Slow: real `uv build` + full dependency install. Run with `pytest -m slow`.
"""

from __future__ import annotations

import glob
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _wait_for_server(base_url: str, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            httpx.get(base_url, timeout=2.0)
            return
        except Exception as e:
            last_error = e
            time.sleep(0.5)
    raise TimeoutError(f"server never came up on {base_url}") from last_error


@pytest.mark.slow
def test_wheel_install_ships_migrations_and_assets_and_serves(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--out-dir", str(dist_dir)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    wheels = glob.glob(str(dist_dir / "*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    wheel_path = wheels[0]

    # Everything the running engine loads from disk at runtime must actually be in the wheel.
    import zipfile

    with zipfile.ZipFile(wheel_path) as zf:
        names = set(zf.namelist())
    for required in [
        "shouldertap/migrations/env.py",
        "shouldertap/migrations/versions/0001_initial.py",
        "shouldertap/engine/asker/prompts/draft_question.md",
        "shouldertap/engine/capture/prompts/structure_reply.md",
        "shouldertap/server/approval_ui/static/index.html",
        "shouldertap/py.typed",
    ]:
        assert required in names, f"{required} missing from the wheel"

    venv_dir = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", str(venv_dir), "--python", "3.12"],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    venv_python = venv_dir / "bin" / "python3.12"
    install = subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), wheel_path],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    shtap = venv_dir / "bin" / "shtap"
    workdir = tmp_path / "demo"
    workdir.mkdir()

    init_result = subprocess.run(
        [str(shtap), "init", "--org-name", "Acme"],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr
    # A pip-installed user has no checkout, so setup instructions must not point at a
    # repo-relative path for the Slack manifest.
    assert "https://" in init_result.stdout

    config_path = workdir / "shouldertap.yaml"
    # Set the port in config (not via `serve --port`) so the CLI client commands, which derive
    # their base URL from the same config, agree with the server. Also keeps this test off
    # 8776 so it can run alongside test_fresh_machine.py.
    config_path.write_text(
        config_path.read_text()
        .replace("port: 8776", "port: 8787")
        .replace(
            "experts: []",
            'experts:\n  - id: "U1"\n    name: "Dana"\n    topics: ["revenue metrics"]',
        )
    )

    env = dict(os.environ)
    env.pop("SHOULDERTAP_API_TOKEN", None)

    serve_proc = subprocess.Popen(
        [str(shtap), "serve", "--transport", "console", "--host", "127.0.0.1"],
        cwd=workdir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # Reaching this at all proves the packaged Alembic migrations ran: create_app() runs
        # them before the server can bind.
        _wait_for_server("http://127.0.0.1:8787/", timeout=60)

        # The approval UI's static asset is served from inside the installed package.
        ui = httpx.get("http://127.0.0.1:8787/", timeout=10.0)
        assert ui.status_code == 200
        assert "ShoulderTap" in ui.text

        ask_result = subprocess.run(
            [
                str(shtap),
                "ask",
                "What does active customer mean?",
                "--topic",
                "revenue metrics",
                "--poll-interval",
                "0.5",
                "--timeout",
                "3",
            ],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert "Submitted" in ask_result.stdout, ask_result.stdout + ask_result.stderr
    finally:
        serve_proc.terminate()
        try:
            serve_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            serve_proc.kill()

    server_output = serve_proc.stdout.read() if serve_proc.stdout else ""
    assert "ShoulderTap DM to Dana" in server_output
    assert "I'm ShoulderTap, an automated assistant" in server_output

    # The DB really was created and migrated by the installed package.
    assert (workdir / "shouldertap.db").exists()
