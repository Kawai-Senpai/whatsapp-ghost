from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import typer
from typer.testing import CliRunner

from whatsapp_ghost import cli


runner = CliRunner()


@pytest.fixture(autouse=True)
def cli_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WABA_BASE_URL", "http://127.0.0.1:9876")
    monkeypatch.setenv("WABA_ACCESS_TOKEN", "cli-token")
    monkeypatch.setenv("WABA_NOTIFY", "none")


def test_init_creates_novice_env_and_protects_existing_file() -> None:
    first = runner.invoke(cli.app, ["init"])
    assert first.exit_code == 0
    env = Path(".env")
    assert env.exists()
    assert "WABA_BASE_URL=http://127.0.0.1:8787" in env.read_text(encoding="utf-8")
    env.write_text("CUSTOM=keep\n", encoding="utf-8")
    protected = runner.invoke(cli.app, ["init"])
    assert protected.exit_code == 0
    assert env.read_text(encoding="utf-8") == "CUSTOM=keep\n"
    forced = runner.invoke(cli.app, ["init", "--force"])
    assert forced.exit_code == 0
    assert "WABA_MODE=strict" in env.read_text(encoding="utf-8")


def test_doctor_reports_running_and_unreachable_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "api", lambda *_args, **_kwargs: {"status": "ok"})
    healthy = runner.invoke(cli.app, ["doctor"])
    assert healthy.exit_code == 0
    assert "ok" in healthy.output

    def unavailable(*_args, **_kwargs):
        raise typer.Exit(1)

    monkeypatch.setattr(cli, "api", unavailable)
    unhealthy = runner.invoke(cli.app, ["doctor"])
    assert unhealthy.exit_code == 0
    assert "not reachable" in unhealthy.output


@pytest.mark.parametrize(
    ("arguments", "expected_method", "expected_path", "expected_json"),
    [
        (["clock", "show"], "GET", "/_sandbox/clock", None),
        (["clock", "advance", "25h"], "POST", "/_sandbox/clock", {"action": "advance", "value": "25h"}),
        (["clock", "set", "2026-07-15T10:00:00Z"], "POST", "/_sandbox/clock", {"action": "set", "value": "2026-07-15T10:00:00Z"}),
        (["clock", "reset"], "POST", "/_sandbox/clock", {"action": "reset"}),
        (["phone", "create", "15550002009", "--name", "Alice"], "POST", "/_sandbox/phones", {"wa_id": "15550002009", "display_name": "Alice"}),
    ],
)
def test_control_commands_call_expected_sandbox_api(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected_method: str,
    expected_path: str,
    expected_json: dict | None,
) -> None:
    calls = []

    def fake_api(method: str, path: str, *, json=None):
        calls.append((method, path, json))
        if path == "/_sandbox/clock":
            return {"now": "2026-07-15T10:00:00+00:00", "frozen": arguments[-1] != "show"}
        return {"wa_id": "15550002009", "display_name": "Alice"}

    monkeypatch.setattr(cli, "api", fake_api)
    result = runner.invoke(cli.app, arguments)
    assert result.exit_code == 0, result.output
    assert calls == [(expected_method, expected_path, expected_json)]


def test_reset_yes_and_webhook_list(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_api(method: str, path: str, *, json=None):
        calls.append((method, path, json))
        if path == "/_sandbox/webhooks":
            return {"data": [{"id": "whd_1", "event_type": "messages", "status": "delivered", "attempt_count": 1, "destination_url": None}]}
        return {"success": True}

    monkeypatch.setattr(cli, "api", fake_api)
    assert runner.invoke(cli.app, ["reset", "--yes"]).exit_code == 0
    listed = runner.invoke(cli.app, ["webhooks", "list"])
    assert listed.exit_code == 0
    assert "whd_1" in listed.output
    assert calls[0][:2] == ("POST", "/_sandbox/reset")


def test_docs_and_console_open_correct_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert runner.invoke(cli.app, ["docs"]).exit_code == 0
    assert runner.invoke(cli.app, ["console"]).exit_code == 0
    assert opened == ["http://127.0.0.1:9876/docs", "http://127.0.0.1:9876/console"]


def test_phone_open_passes_selected_business_to_textual(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class FakePhone:
        def __init__(self, *args):
            captured["args"] = args

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr("whatsapp_ghost.tui.PhoneApp", FakePhone)
    result = runner.invoke(cli.app, ["phone", "open", "15550002001", "--business", "PHONE_SALES"])
    assert result.exit_code == 0, result.output
    assert captured["args"][-1] == "PHONE_SALES"
    assert captured["ran"] is True


def test_phone_spawn_builds_business_aware_command(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = []
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command: commands.append(command))
    result = runner.invoke(cli.app, ["phone", "spawn", "15550002001", "--business", "PHONE_SALES"])
    assert result.exit_code == 0
    assert commands[0][-2:] == ["--business", "PHONE_SALES"]
    assert sys.executable in commands[0]


def test_start_prints_actual_host_port_and_passes_options(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))
    result = runner.invoke(cli.app, ["start", "--host", "0.0.0.0", "--port", "9999", "--mode", "loose"])
    assert result.exit_code == 0, result.output
    assert "http://0.0.0.0:9999/console" in result.output
    assert "http://0.0.0.0:9999/guide" in result.output
    assert calls[0][1] == {"host": "0.0.0.0", "port": 9999, "reload": False}


def test_api_helper_reports_connection_and_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("GET", "http://127.0.0.1:9876/fail")

    def connection_error(*_args, **_kwargs):
        raise httpx.ConnectError("no server", request=request)

    monkeypatch.setattr(cli.httpx, "request", connection_error)
    with pytest.raises(typer.Exit):
        cli.api("GET", "/fail")

    def status_error(*_args, **_kwargs):
        return httpx.Response(500, request=request, json={"error": "failed"})

    monkeypatch.setattr(cli.httpx, "request", status_error)
    with pytest.raises(typer.Exit):
        cli.api("GET", "/fail")
