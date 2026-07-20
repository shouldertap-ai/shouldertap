"""spec §15 criterion 8, literal: "Fresh-machine setup: `pip install -e . && shtap init &&
shtap serve` reaches a working demo (with mock Slack transport `--transport console`... no .env
required)." Deliberately uses plain `pip`, not `uv` -- `uv` is the day-to-day dev workflow, but
this criterion is a distinct (and stricter) claim about plain-pip installability.

Venv creation uses `uv venv` rather than stdlib `venv.EnvBuilder`: the latter is broken for
`uv python install`-managed standalone CPython builds (their python3 binary looks for
libpython relative to `@executable_path`, which stdlib `venv` doesn't set up -- a known
python-build-standalone limitation, unrelated to shouldertap). `uv venv` configures this
correctly; a real `pip` binary is then bootstrapped into that venv via `uv pip install pip`
(not `ensurepip`, which hits the same broken-dylib path), so the actual install step below is
a literal `pip install -e .` invocation, matching the criterion's exact wording.

Slow: builds a real throwaway venv and installs the full dependency set. Not part of the fast
inner-loop `pytest` run; run it explicitly (`pytest -m slow`) or as part of a release check.
"""

from __future__ import annotations

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
def test_pip_install_init_and_serve_reach_a_working_demo(tmp_path: Path) -> None:
    venv_dir = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", str(venv_dir), "--python", "3.12"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    venv_python = venv_dir / "bin" / "python3.12"
    bootstrap_pip = subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), "pip"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert bootstrap_pip.returncode == 0, bootstrap_pip.stdout + bootstrap_pip.stderr

    pip = venv_dir / "bin" / "pip"
    shtap = venv_dir / "bin" / "shtap"

    install = subprocess.run(
        [str(pip), "install", "-e", str(_REPO_ROOT)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    workdir = tmp_path / "demo"
    workdir.mkdir()

    init_result = subprocess.run(
        [str(shtap), "init", "--org-name", "Acme"],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr

    config_path = workdir / "shouldertap.yaml"
    assert config_path.exists()
    assert (workdir / ".env.example").exists()
    assert not (workdir / ".env").exists()  # criterion: no .env required

    # Give the demo an expert to route to.
    config_path.write_text(
        config_path.read_text().replace(
            "experts: []",
            'experts:\n  - id: "U1"\n    name: "Dana"\n    topics: ["revenue metrics"]',
        )
    )

    env = dict(os.environ)
    env.pop("SHOULDERTAP_API_TOKEN", None)  # exercise the documented no-auth-configured path

    serve_proc = subprocess.Popen(
        [str(shtap), "serve", "--transport", "console", "--host", "127.0.0.1"],
        cwd=workdir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_server("http://127.0.0.1:8776/", timeout=30)

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
            timeout=30,
        )
        assert "Submitted" in ask_result.stdout, ask_result.stdout + ask_result.stderr
        assert "status: queued" in ask_result.stdout
    finally:
        serve_proc.terminate()
        try:
            serve_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            serve_proc.kill()

    server_output = serve_proc.stdout.read() if serve_proc.stdout else ""
    assert "ShoulderTap DM to Dana" in server_output  # the ask was actually delivered
    assert "I'm ShoulderTap, an automated assistant" in server_output
