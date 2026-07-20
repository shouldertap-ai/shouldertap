from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shouldertap.cli.main import cli
from shouldertap.engine.transports.console import ConsoleTransport
from shouldertap.server.app import create_app

API_TOKEN = "test-token-123"


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SHOULDERTAP_API_TOKEN", API_TOKEN)
    path = tmp_path / "shouldertap.yaml"
    path.write_text(
        """
org:
  name: Test Org
  timezone: UTC
server:
  api_token_env: SHOULDERTAP_API_TOKEN
experts:
  - id: U1
    name: Dana
    topics: ["revenue metrics"]
"""
    )
    return path


@pytest.fixture
def app_and_transport(config_path: Path):
    transport = ConsoleTransport(interactive=False)
    app = create_app(config_path, transport=transport)
    return app, transport


def _fake_client(app: FastAPI) -> httpx.Client:
    # FastAPI's TestClient (not a bare httpx.Client(transport=ASGITransport(...))) is needed
    # here -- it does the sync/async bridging an ASGI app requires; a raw sync httpx.Client
    # pointed at ASGITransport doesn't support the context-manager protocol our CLI code uses.
    return TestClient(
        app, base_url="http://testserver/api/v1", headers={"Authorization": f"Bearer {API_TOKEN}"}
    )


def _patch_build_client(monkeypatch: pytest.MonkeyPatch, app: FastAPI, modules: list[str]) -> None:
    def fake_build_client(_config_path: Path) -> httpx.Client:
        return _fake_client(app)

    for module in modules:
        monkeypatch.setattr(f"shouldertap.cli.commands.{module}.build_client", fake_build_client)


def test_init_writes_config_and_env_example(tmp_path: Path) -> None:
    runner = CliRunner()
    config_path = tmp_path / "shouldertap.yaml"
    result = runner.invoke(
        cli, ["init", "--config", str(config_path), "--org-name", "Acme"], input="Acme\n"
    )
    assert result.exit_code == 0, result.output
    assert config_path.exists()
    assert "Acme" in config_path.read_text()
    assert (tmp_path / ".env.example").exists()


def test_ask_and_accept_end_to_end(
    monkeypatch: pytest.MonkeyPatch, config_path: Path, app_and_transport
) -> None:
    app, transport = app_and_transport
    _patch_build_client(monkeypatch, app, ["ask", "queue", "accept"])

    def fake_sleep(_seconds: float) -> None:
        # Simulate the expert replying, then accept it -- as if a human did this out-of-band
        # while `shtap ask` was polling. No `with` here: the outer `ask` command's own client
        # already has the app's lifespan (and thus its scheduler) running; a second `with`
        # would try to start that same scheduler again and blow up.
        transport.push_reply("paying accounts active in 90 days")
        client = _fake_client(app)
        proposals = client.get("/proposals", params={"status": "pending"}).json()
        client.post(f"/proposals/{proposals[0]['id']}/accept", json={"decided_by": "alice"})

    monkeypatch.setattr("shouldertap.cli.commands.ask.time.sleep", fake_sleep)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ask",
            "What does active customer mean?",
            "--topic",
            "revenue metrics",
            "--config",
            str(config_path),
            "--poll-interval",
            "0",
            "--timeout",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Submitted" in result.output
    assert "paying accounts active in 90 days" in result.output
    assert "Dana" in result.output


def test_queue_shows_pending_and_then_empty(
    monkeypatch: pytest.MonkeyPatch, config_path: Path, app_and_transport
) -> None:
    app, transport = app_and_transport
    _patch_build_client(monkeypatch, app, ["ask", "queue"])
    monkeypatch.setattr("shouldertap.cli.commands.ask.time.sleep", lambda _s: None)

    runner = CliRunner()
    empty_result = runner.invoke(cli, ["queue", "--config", str(config_path)])
    assert "Nothing pending" in empty_result.output

    runner.invoke(
        cli,
        [
            "ask",
            "What does active customer mean?",
            "--topic",
            "revenue metrics",
            "--config",
            str(config_path),
            "--poll-interval",
            "0",
            "--timeout",
            "0",
        ],
    )
    transport.push_reply("paying accounts")

    result = runner.invoke(cli, ["queue", "--config", str(config_path)])
    assert "paying accounts" in result.output


def test_accept_and_reject_report_404_for_unknown_proposal(
    monkeypatch: pytest.MonkeyPatch, config_path: Path, app_and_transport
) -> None:
    app, _ = app_and_transport
    _patch_build_client(monkeypatch, app, ["accept", "reject"])

    runner = CliRunner()
    result = runner.invoke(
        cli, ["accept", "nonexistent", "--config", str(config_path), "--decided-by", "alice"]
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower()

    result = runner.invoke(
        cli,
        [
            "reject",
            "nonexistent",
            "--reason",
            "bad",
            "--config",
            str(config_path),
            "--decided-by",
            "alice",
        ],
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_experts_lists_registry(
    monkeypatch: pytest.MonkeyPatch, config_path: Path, app_and_transport
) -> None:
    app, _ = app_and_transport
    _patch_build_client(monkeypatch, app, ["experts"])

    runner = CliRunner()
    result = runner.invoke(cli, ["experts", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "U1" in result.output
    assert "Dana" in result.output
    assert "revenue metrics" in result.output


def test_audit_reports_no_events_for_unknown_request(
    monkeypatch: pytest.MonkeyPatch, config_path: Path, app_and_transport
) -> None:
    app, _ = app_and_transport
    _patch_build_client(monkeypatch, app, ["audit"])

    runner = CliRunner()
    result = runner.invoke(cli, ["audit", "req_nonexistent", "--config", str(config_path)])
    assert result.exit_code == 0
    assert "No audit events" in result.output


def test_audit_shows_trail_after_a_tap(
    monkeypatch: pytest.MonkeyPatch, config_path: Path, app_and_transport
) -> None:
    app, transport = app_and_transport
    _patch_build_client(monkeypatch, app, ["ask", "audit"])
    monkeypatch.setattr("shouldertap.cli.commands.ask.time.sleep", lambda _s: None)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ask",
            "What does active customer mean?",
            "--topic",
            "revenue metrics",
            "--config",
            str(config_path),
            "--poll-interval",
            "0",
            "--timeout",
            "0",
        ],
    )
    request_id = result.output.splitlines()[0].split()[1]

    result = runner.invoke(cli, ["audit", request_id, "--config", str(config_path)])
    assert "request.received" in result.output
    assert "ask.sent" in result.output
