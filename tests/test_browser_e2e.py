from __future__ import annotations

import base64
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    data_dir = tmp_path_factory.mktemp("browser-server")
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update({
        "WABA_DATA_DIR": str(data_dir),
        "WABA_BASE_URL": base_url,
        "WABA_ACCESS_TOKEN": "browser-token",
        "WABA_APP_SECRET": "browser-secret",
        "WABA_VERIFY_TOKEN": "browser-verify",
        "WABA_STATUS_DELAY": "0.01",
        "WABA_NOTIFY": "none",
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "whatsapp_ghost.api:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=Path(__file__).parents[1],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
            raise AssertionError("live Ghost server did not start")
        yield base_url, data_dir
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    current = context.new_page()
    errors: list[str] = []
    current.on("pageerror", lambda error: errors.append(str(error)))
    try:
        yield current
        assert errors == [], f"browser page errors: {errors}"
    finally:
        context.close()


def test_console_can_add_second_sender_and_populates_live_guide(page: Page, live_server: tuple[str, Path]) -> None:
    base_url, _ = live_server
    page.goto(base_url + "/console")
    expect(page.locator("#m-numbers")).to_have_text("1")
    page.locator('.side-link[data-page="resources"]').click()
    page.get_by_role("button", name="Add sender").click()
    page.locator("#sender-verified").fill("Ghost Sales")
    page.locator("#sender-number").fill("15550001001")
    page.locator("#sender-modal").get_by_role("button", name="Add sender").click()
    expect(page.locator("#business-list")).to_contain_text("2 registered senders")
    expect(page.locator("#business-list")).to_contain_text("Ghost Sales")

    page.locator('.side-link[data-page="guide"]').click()
    expect(page.locator("#guide-phone-id")).to_have_text("PHONE_LOCAL")
    expect(page.locator("#guide-send-code")).to_contain_text(f"{base_url}/v25.0/PHONE_LOCAL/messages")
    expect(page.locator("#guide-send-code")).to_contain_text("browser-token")


def test_browser_phone_text_order_media_persistence_and_read_ticks(
    page: Page, live_server: tuple[str, Path], tmp_path: Path
) -> None:
    base_url, data_dir = live_server
    page.goto(base_url + "/phone?phone=15550002001&business=PHONE_LOCAL")
    expect(page.locator("#convo")).not_to_have_class("hidden")

    for text in ("First browser message", "Second browser message"):
        page.locator("#msg-input").fill(text)
        page.locator("#send-btn").click()
        expect(page.locator(".msg.out .body").last).to_have_text(text)
    assert page.locator(".msg.out .body").all_text_contents()[-2:] == ["First browser message", "Second browser message"]

    image_path = tmp_path / "e2e.png"
    image_path.write_bytes(ONE_PIXEL_PNG)
    page.locator("#file-input").set_input_files(image_path)
    expect(page.locator(".msg.out img.media-thumb")).to_have_count(1)
    expect(page.locator(".msg.out img.media-thumb")).to_have_js_property("complete", True)
    assert page.locator(".msg.out img.media-thumb").evaluate("image => image.naturalWidth") > 0

    media_files = [path for path in (data_dir / "media").iterdir() if path.is_file()]
    assert len(media_files) == 1
    assert media_files[0].read_bytes() == ONE_PIXEL_PNG
    with httpx.Client(headers={"Authorization": "Bearer browser-token"}) as api:
        history = api.get(base_url + "/_sandbox/messages", params={
            "wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL",
        }).json()["data"]
        assert history[0]["message_type"] == "image"
        media_id = history[0]["payload"]["image"]["id"]
        assert media_id == media_files[0].name
        first_message = next(
            item for item in history if item["payload"].get("text", {}).get("body") == "First browser message"
        )
        marked = api.post(base_url + "/v25.0/PHONE_LOCAL/messages", json={
            "messaging_product": "whatsapp", "status": "read", "message_id": first_message["id"],
        })
        assert marked.json() == {"success": True}
        sent = api.post(base_url + "/v25.0/PHONE_LOCAL/messages", json={
            "messaging_product": "whatsapp", "to": "15550002001", "type": "text",
            "text": {"body": "Reply from the business API"},
        })
        assert sent.status_code == 200

    expect(page.locator(".msg.out").first.locator(".ticks.read")).to_have_count(1)
    expect(page.locator(".msg.in .body").last).to_have_text("Reply from the business API")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        messages = httpx.get(base_url + "/_sandbox/messages", params={
            "wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL",
        }).json()["data"]
        reply = next(item for item in messages if item["payload"].get("text", {}).get("body") == "Reply from the business API")
        if reply["status"] == "read":
            break
        time.sleep(0.02)
    assert reply["status"] == "read"


def test_phone_can_switch_between_senders_without_mixing_history(page: Page, live_server: tuple[str, Path]) -> None:
    base_url, _ = live_server
    page.goto(base_url + "/phone?phone=15550002001&business=PHONE_LOCAL")
    expect(page.locator("#chat-list .chat-row")).to_have_count(2)
    page.get_by_text("Ghost Sales", exact=True).click()
    expect(page.locator("#convo-name")).to_have_text("Ghost Sales")
    expect(page.locator("#messages")).to_contain_text("No messages yet")
    page.locator("#msg-input").fill("Sales-only conversation")
    page.locator("#send-btn").click()
    expect(page.locator(".msg.out .body")).to_have_text("Sales-only conversation")

    page.get_by_text("Ghost Demo", exact=True).click()
    expect(page.locator("#messages")).to_contain_text("First browser message")
    expect(page.locator("#messages")).not_to_contain_text("Sales-only conversation")

