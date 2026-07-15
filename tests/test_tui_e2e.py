from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest
from textual.widgets import Input, ListView

from whatsapp_ghost.tui import PhoneApp


@pytest.fixture(scope="module")
def tui_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    data_dir = tmp_path_factory.mktemp("tui-server")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update({"WABA_DATA_DIR": str(data_dir), "WABA_BASE_URL": base_url, "WABA_ACCESS_TOKEN": "tui-token", "WABA_NOTIFY": "none"})
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "whatsapp_ghost.api:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=Path(__file__).parents[1], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if httpx.get(base_url + "/_sandbox/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise AssertionError("TUI test server did not start")
        created = httpx.post(base_url + "/_sandbox/businesses/WABA_LOCAL/phone-numbers", json={
            "verified_name": "Ghost Sales", "display_phone_number": "15550001001",
        })
        assert created.status_code == 201
        yield base_url
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.asyncio
async def test_textual_phone_loads_switches_and_sends_to_real_configured_sender(tui_server: str) -> None:
    phone = PhoneApp("15550002001", tui_server, "tui-token", "none")
    async with phone.run_test() as pilot:
        await pilot.pause(0.5)
        contacts = phone.query_one("#contacts", ListView)
        assert len(contacts.children) == 2
        assert phone.active_phone_id == "PHONE_LOCAL"

        contacts.focus()
        await pilot.press("down", "enter")
        await pilot.pause(0.2)
        assert phone.active_phone_id != "PHONE_LOCAL"
        selected_phone = phone.active_phone_id
        composer = phone.query_one("#composer", Input)
        composer.value = "Sent from the Textual client"
        composer.focus()
        await pilot.press("enter")
        await pilot.pause(0.2)

    history = httpx.get(tui_server + "/_sandbox/messages", params={
        "wa_id": "15550002001", "phone_number_id": selected_phone,
    }).json()["data"]
    assert history[0]["payload"]["text"]["body"] == "Sent from the Textual client"
