from __future__ import annotations

import sys

import pytest

from whatsapp_ghost.notifications import notify


def test_none_notification_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    notify("Title", "Body", "none")
    assert capsys.readouterr().err == ""


def test_bell_notification_writes_terminal_bell(capsys: pytest.CaptureFixture[str]) -> None:
    notify("Title", "Body", "bell")
    assert capsys.readouterr().err == "\a"


def test_desktop_notification_uses_plyer(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = []
    from plyer import notification

    monkeypatch.setattr(notification, "notify", lambda **kwargs: calls.append(kwargs))
    notify("New message", "Hello", "desktop")
    assert calls == [{"title": "New message", "message": "Hello", "app_name": "WhatsApp Ghost", "timeout": 5}]
    assert capsys.readouterr().err == ""


def test_desktop_notification_falls_back_to_bell(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from plyer import notification

    def fail(**_kwargs):
        raise RuntimeError("desktop unavailable")

    monkeypatch.setattr(notification, "notify", fail)
    notify("New message", "Hello", "desktop")
    assert capsys.readouterr().err == "\a"
    assert sys.stderr is not None
