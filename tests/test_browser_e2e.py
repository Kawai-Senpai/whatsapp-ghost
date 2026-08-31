from __future__ import annotations

import base64
import os
import re
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


def test_console_displays_actionable_meta_error_details(page: Page, live_server: tuple[str, Path]) -> None:
    base_url, _ = live_server
    page.goto(base_url + "/console")
    page.locator('.side-link[data-page="templates"]').click()
    page.get_by_role("button", name="New template").click()
    page.locator("#tpl-name").fill("Invalid-Template")
    page.locator("#tpl-body").fill("A valid body.")
    page.locator("#template-modal").get_by_role("button", name="Create & approve").click()

    toast = page.locator("#toast")
    expect(toast).to_have_class(re.compile(r"\bbad\b"))
    expect(toast).to_contain_text("Invalid message template")
    expect(toast).to_contain_text("lowercase letters, numbers, or underscores")
    expect(toast).to_contain_text("code 100")
    expect(toast).to_contain_text("Trace: LOCAL_")


def test_phone_renders_and_activates_template_buttons(page: Page, live_server: tuple[str, Path]) -> None:
    base_url, _ = live_server
    headers = {"Authorization": "Bearer browser-token"}
    created = httpx.post(
        base_url + "/v26.0/WABA_LOCAL/message_templates",
        headers=headers,
        json={
            "name": "browser_buttons",
            "language": "en_US",
            "category": "UTILITY",
            "_sandbox_auto_approve": True,
            "components": [
                {
                    "type": "BODY",
                    "text": "Hello {{1}}, your trip is ready.",
                    "example": {"body_text": [["Alex"]]},
                },
                {
                    "type": "BUTTONS",
                    "buttons": [
                        {"type": "QUICK_REPLY", "text": "Acknowledge"},
                        {
                            "type": "URL",
                            "text": "View trip",
                            "url": "https://example.test/trips/{{1}}",
                            "example": ["TRIP-1"],
                        },
                    ],
                },
            ],
        },
    )
    assert created.status_code == 200, created.text
    sent = httpx.post(
        base_url + "/v26.0/PHONE_LOCAL/messages",
        headers=headers,
        json={
            "messaging_product": "whatsapp",
            "to": "15550002001",
            "type": "template",
            "template": {
                "name": "browser_buttons",
                "language": {"code": "en_US"},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": "Alex"}]},
                    {
                        "type": "button",
                        "sub_type": "quick_reply",
                        "index": "0",
                        "parameters": [{"type": "payload", "payload": "ACKNOWLEDGE"}],
                    },
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "1",
                        "parameters": [{"type": "text", "text": "TRIP-42"}],
                    },
                ],
            },
        },
    )
    assert sent.status_code == 200, sent.text

    page.goto(base_url + "/phone?phone=15550002001&business=PHONE_LOCAL")
    message = page.locator(".msg.tpl").last
    expect(message.locator(".body")).to_have_text("Hello Alex, your trip is ready.")
    expect(message.get_by_role("button", name="Acknowledge")).to_be_visible()
    link = message.get_by_role("link", name="View trip")
    expect(link).to_have_attribute("href", "https://example.test/trips/TRIP-42")

    message.get_by_role("button", name="Acknowledge").click()
    expect(page.locator(".msg.out .body").last).to_have_text("Acknowledge")


def test_phone_pin_search_emoji_reply_reaction_and_normal_messages(
    page: Page, live_server: tuple[str, Path]
) -> None:
    base_url, _ = live_server
    headers = {"Authorization": "Bearer browser-token"}
    page.goto(base_url + "/phone?phone=15550002001&business=PHONE_LOCAL")

    page.locator("#msg-input").fill("Hello from customer")
    page.locator("#send-btn").click()
    expect(page.locator(".msg.out .body").last).to_have_text("Hello from customer")

    response = httpx.post(
        base_url + "/v26.0/PHONE_LOCAL/messages",
        headers=headers,
        json={
            "messaging_product": "whatsapp",
            "to": "15550002001",
            "type": "text",
            "text": {"body": "Normal business response"},
        },
    )
    assert response.status_code == 200, response.text
    page.reload()
    business_message = page.locator(".msg.in", has_text="Normal business response").last
    expect(business_message).to_be_visible()

    page.locator("#convo-menu-btn").click()
    page.locator("#menu-pin-chat").click()
    expect(page.locator(".chat-row .c-pin")).to_be_visible()
    page.reload()
    expect(page.locator(".chat-row .c-pin")).to_be_visible()

    page.locator("#emoji-btn").click()
    page.locator('[data-compose-emoji="😀"]').click()
    expect(page.locator("#msg-input")).to_have_value("😀")
    page.locator("#send-btn").click()
    expect(page.locator(".msg.out .body").last).to_have_text("😀")

    business_message = page.locator(".msg.in", has_text="Normal business response").last
    business_message.hover()
    business_message.locator(".msg-action-toggle").click()
    business_message.get_by_role("button", name="Reply").click()
    expect(page.locator("#reply-composer")).to_be_visible()
    page.locator("#msg-input").fill("Reply from customer")
    page.locator("#send-btn").click()
    stored = httpx.get(
        base_url + "/_sandbox/messages",
        params={"wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL", "limit": 20},
    ).json()["data"]
    stored_reply = next(item for item in stored if item["payload"].get("text", {}).get("body") == "Reply from customer")
    assert stored_reply["payload"].get("context", {}).get("id")
    reply = page.locator(".msg.out", has_text="Reply from customer").last
    expect(reply.locator(".reply-quote")).to_contain_text("Normal business response")

    business_message = page.locator(".msg.in", has_text="Normal business response").last
    business_message.hover()
    business_message.locator(".msg-action-toggle").click()
    business_message.locator('[data-emoji="👍"]').click()
    expect(business_message.locator(".reaction-badge")).to_have_text("👍")

    page.locator("#convo-search-btn").click()
    page.locator("#convo-search-input").fill("Normal business")
    expect(page.locator(".msg")).to_have_count(1)
    expect(page.locator(".msg .body")).to_have_text("Normal business response")
    page.locator("#close-convo-search").click()
    expect(page.locator(".msg").nth(1)).to_be_visible()


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

    first_bubble = page.locator(".msg.out", has_text="First browser message")
    expect(first_bubble.locator(".ticks.read")).to_have_count(1)
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


def _seed_conversation(base_url: str, wa_id: str, name: str, body: str) -> None:
    """Register a customer, open the service window, then have the business reply."""
    httpx.post(f"{base_url}/_sandbox/phones", json={"wa_id": wa_id, "display_name": name}, timeout=5)
    httpx.post(
        f"{base_url}/_sandbox/phones/{wa_id}/messages",
        json={"phone_number_id": "PHONE_LOCAL", "type": "text", "text": "hi"},
        timeout=5,
    )
    httpx.post(
        f"{base_url}/v25.0/PHONE_LOCAL/messages",
        headers={"Authorization": "Bearer browser-token"},
        json={"messaging_product": "whatsapp", "to": wa_id, "type": "text", "text": {"body": body}},
        timeout=5,
    )


def test_phone_live_inbox_shows_another_mobiles_message_without_a_reload(
    page: Page, live_server: tuple[str, Path]
) -> None:
    """The complaint this feature exists for: no manual refresh.

    The page is opened as one customer and never reloaded; a message is then
    sent to a *different* customer. Before /_sandbox/observer the page held only
    /_sandbox/clients/{wa} and could not learn about this at all.
    """
    base_url, _ = live_server
    _seed_conversation(base_url, "15550007001", "Watcher", "seed")
    page.goto(f"{base_url}/phone?phone=15550007001&business=PHONE_LOCAL")
    expect(page.locator("#inbox-status")).to_have_class(re.compile(r"live"))

    _seed_conversation(base_url, "15550007002", "Other Mobile", "arrived while you watched")

    feed = page.locator("#feed")
    expect(feed).to_contain_text("Other Mobile")
    # The body must be readable: the socket payload is stored as JSON text, and
    # an unparsed one renders as "(no body)".
    expect(feed).to_contain_text("arrived while you watched")
    expect(feed).not_to_contain_text("(no body)")

    # And the other mobile carries an unread badge without any interaction.
    row = page.locator('.mobile-row[data-open-wa="15550007002"]')
    expect(row.locator(".badge-unread")).to_have_text("1")


def test_phone_live_inbox_shows_an_auto_created_mobile_without_a_reload(
    page: Page, live_server: tuple[str, Path]
) -> None:
    base_url, _ = live_server
    _seed_conversation(base_url, "15550007010", "Roster Watcher", "seed")
    page.goto(f"{base_url}/phone?phone=15550007010&business=PHONE_LOCAL")
    expect(page.locator("#inbox-status")).to_have_class(re.compile(r"live"))

    # An outbound send to an unknown number auto-creates the mobile server-side.
    httpx.post(
        f"{base_url}/v25.0/PHONE_LOCAL/messages",
        headers={"Authorization": "Bearer browser-token"},
        json={
            "messaging_product": "whatsapp",
            "to": "15550007011",
            "type": "template",
            "template": {
                "name": "hello_world",
                "language": {"code": "en_US"},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": "Newcomer"}]}],
            },
        },
        timeout=5,
    )
    expect(page.locator('.mobile-row[data-open-wa="15550007011"]')).to_be_visible()


def test_console_simulator_activity_and_unread_are_live(
    page: Page, live_server: tuple[str, Path]
) -> None:
    """The console must not need a refresh either."""
    base_url, _ = live_server
    page.goto(base_url + "/console")
    page.locator('.side-link[data-page="simulator"]').click()
    expect(page.locator("#sim-live")).to_have_class(re.compile(r"on"))

    _seed_conversation(base_url, "15550007020", "Console Watcher", "console live body")

    activity = page.locator("#sim-activity")
    expect(activity).to_contain_text("Console Watcher")
    expect(activity).to_contain_text("console live body")
    expect(page.locator("#user-list")).to_contain_text("Console Watcher")


def test_console_can_delete_a_template(page: Page, live_server: tuple[str, Path]) -> None:
    """Deleting was already in the API but had no control in the UI."""
    base_url, _ = live_server
    page.goto(base_url + "/console")
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator('.side-link[data-page="templates"]').click()
    rows = page.locator("#template-list .item")
    expect(rows.first).to_be_visible()
    # Other tests in this module share the live server and may have added
    # templates, so assert one fewer rather than an empty list.
    before = rows.count()
    page.locator("#template-list").get_by_role("button", name="Delete").first.click()
    expect(rows).to_have_count(before - 1)


def test_console_returns_to_the_page_you_were_on_after_a_reload(
    page: Page, live_server: tuple[str, Path]
) -> None:
    base_url, _ = live_server
    page.goto(base_url + "/console")
    page.locator('.side-link[data-page="webhooks"]').click()
    expect(page.locator("#webhooks")).to_have_class(re.compile(r"active"))
    page.reload()
    expect(page.locator("#webhooks")).to_have_class(re.compile(r"active"))


def test_console_customers_sort_by_recency_when_nothing_is_unread(
    page: Page, live_server: tuple[str, Path]
) -> None:
    """Unread alone cannot order the list.

    Once every message is read each customer sits at 0 unread, and sorting by
    unread then name froze the order alphabetically forever - a customer who
    had just messaged never moved. Recency is the tiebreak that fixes it.
    """
    base_url, _ = live_server
    # Names are generated, so pick numbers and read back what they were called.
    older, newer = "15550008001", "15550008002"
    for wa in (older, newer):
        httpx.post(f"{base_url}/_sandbox/phones", json={"wa_id": wa}, timeout=5)
    page.goto(base_url + "/console")
    page.locator('.side-link[data-page="simulator"]').click()
    expect(page.locator("#sim-live")).to_have_class(re.compile(r"on"))

    def names() -> list[str]:
        return [t.strip() for t in page.locator("#user-list .user-id b").all_inner_texts()]

    def name_of(wa: str) -> str:
        row = httpx.get(f"{base_url}/_sandbox/phones", timeout=5).json()["data"]
        return next(u["display_name"] for u in row if u["wa_id"] == wa)

    # Inbound only, so nothing is ever unread: unread counts outbound messages.
    for wa in (older, newer):
        httpx.post(
            f"{base_url}/_sandbox/phones/{wa}/messages",
            json={"phone_number_id": "PHONE_LOCAL", "type": "text", "text": "hello"},
            timeout=5,
        )

    # Other tests in this module share the server and may leave customers with
    # unread messages, which outrank recency by design. Compare only these two.
    shown = names()
    assert name_of(newer) in shown and name_of(older) in shown
    assert shown.index(name_of(newer)) < shown.index(name_of(older)), shown

    # And the order must follow new activity live, with no reload.
    httpx.post(
        f"{base_url}/_sandbox/phones/{older}/messages",
        json={"phone_number_id": "PHONE_LOCAL", "type": "text", "text": "now me"},
        timeout=5,
    )

    def older_now_leads() -> bool:
        current = names()
        return current.index(name_of(older)) < current.index(name_of(newer))

    page.wait_for_function(
        """([older, newer]) => {
            const names = [...document.querySelectorAll('#user-list .user-id b')]
              .map(e => e.textContent.trim());
            return names.indexOf(older) > -1 && names.indexOf(older) < names.indexOf(newer);
        }""",
        arg=[name_of(older), name_of(newer)],
        timeout=5000,
    )
    assert older_now_leads(), names()


def test_phone_chat_list_reorders_by_most_recent_message(
    page: Page, live_server: tuple[str, Path]
) -> None:
    """Chats must move to the top when they receive something, like a real client.

    Before this the order was whatever businessPhones() happened to return, so
    the chat you had just been messaged on stayed wherever it was.
    """
    base_url, _ = live_server
    second = httpx.post(
        f"{base_url}/_sandbox/businesses/WABA_LOCAL/phone-numbers",
        json={"verified_name": "Second Desk", "display_phone_number": "15550009999"},
        timeout=5,
    ).json()["id"]
    wa = "15550008100"
    httpx.post(f"{base_url}/_sandbox/phones", json={"wa_id": wa}, timeout=5)
    for phone_id in ("PHONE_LOCAL", second):
        httpx.post(
            f"{base_url}/_sandbox/phones/{wa}/messages",
            json={"phone_number_id": phone_id, "type": "text", "text": "hi"},
            timeout=5,
        )

    page.goto(f"{base_url}/phone?phone={wa}&business=PHONE_LOCAL")
    # The second desk spoke last, so it leads.
    expect(page.locator(".chat-row .c-name").first).to_have_text("Second Desk")

    # A message on the other chat moves it up, live.
    httpx.post(
        f"{base_url}/_sandbox/phones/{wa}/messages",
        json={"phone_number_id": "PHONE_LOCAL", "type": "text", "text": "now me"},
        timeout=5,
    )
    expect(page.locator(".chat-row .c-name").first).not_to_have_text("Second Desk")


def test_console_activity_rows_open_the_exact_conversation(
    page: Page, live_server: tuple[str, Path]
) -> None:
    """A row must open its own customer AND its own business.

    Opening the first business would be wrong as soon as more than one sender
    exists, which is the normal case this sandbox is for.
    """
    base_url, _ = live_server
    second = httpx.post(
        f"{base_url}/_sandbox/businesses/WABA_LOCAL/phone-numbers",
        json={"verified_name": "Row Target Desk", "display_phone_number": "15550009123"},
        timeout=5,
    ).json()["id"]
    wa = "15550008200"
    httpx.post(f"{base_url}/_sandbox/phones", json={"wa_id": wa}, timeout=5)

    page.goto(base_url + "/console")
    page.locator('.side-link[data-page="simulator"]').click()
    expect(page.locator("#sim-live")).to_have_class(re.compile(r"on"))

    httpx.post(
        f"{base_url}/_sandbox/phones/{wa}/messages",
        json={"phone_number_id": second, "type": "text", "text": "row target body"},
        timeout=5,
    )
    row = page.locator(f'.sim-act[data-open-wa="{wa}"]').first
    expect(row).to_be_visible()
    expect(row).to_contain_text("Row Target Desk")
    expect(row).to_have_attribute("data-open-business", second)

    # The click opens a tab; capture the URL rather than managing a popup.
    opened = page.evaluate(
        """() => new Promise(resolve => {
            const original = window.open;
            window.open = (url) => { window.open = original; resolve(url); return null; };
            document.querySelector('.sim-act[data-open-wa]').click();
        })"""
    )
    assert f"phone={wa}" in opened, opened
    assert f"business={second}" in opened, opened


def test_live_feeds_show_both_directions(page: Page, live_server: tuple[str, Path]) -> None:
    """The feed is a record of traffic, not only of notifications.

    Outbound-only was the original behaviour and hid the customer's own replies,
    which made it useless for following a conversation as it happened.
    """
    base_url, _ = live_server
    wa = "15550008300"
    _seed_conversation(base_url, wa, "Both Ways", "business speaking")

    page.goto(f"{base_url}/phone?phone={wa}&business=PHONE_LOCAL")
    expect(page.locator("#inbox-status")).to_have_class(re.compile(r"live"))

    httpx.post(
        f"{base_url}/v25.0/PHONE_LOCAL/messages",
        headers={"Authorization": "Bearer browser-token"},
        json={"messaging_product": "whatsapp", "to": wa, "type": "text",
              "text": {"body": "from the business"}},
        timeout=5,
    )
    httpx.post(
        f"{base_url}/_sandbox/phones/{wa}/messages",
        json={"phone_number_id": "PHONE_LOCAL", "type": "text", "text": "from the customer"},
        timeout=5,
    )

    feed = page.locator("#feed")
    expect(feed).to_contain_text("from the business")
    expect(feed).to_contain_text("from the customer")
    # Both rails must be represented, and an inbound row must still name the
    # business it reached - the Meta inbound payload carries no recipient.
    expect(feed.locator(".feed-item.is-in")).not_to_have_count(0)
    expect(feed.locator(".feed-item.is-out").first).to_contain_text("Ghost Demo")
