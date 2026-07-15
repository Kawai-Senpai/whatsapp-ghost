from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_ghost.tui import PhoneApp


def test_console_contains_every_primary_management_surface(client: TestClient) -> None:
    html = client.get("/console").text
    for page in ("overview", "setup", "resources", "apps", "templates", "webhooks", "guide", "simulator"):
        assert f'id="{page}"' in html
        assert f'data-page="{page}"' in html
    for modal in ("app-modal", "business-modal", "sender-modal", "template-modal", "webhook-modal", "phone-modal"):
        assert f'id="{modal}"' in html


def test_phone_page_has_real_chat_controls(client: TestClient) -> None:
    html = client.get("/phone").text
    assert 'id="chat-list"' in html
    assert 'id="messages"' in html
    assert 'id="send-form"' in html
    assert 'id="file-input"' in html
    assert 'accept="image/*"' in html


def test_static_assets_are_served_and_javascript_has_no_inline_media_shortcut(client: TestClient) -> None:
    phone_js = client.get("/static/phone.js")
    console_js = client.get("/static/console.js")
    phone_css = client.get("/static/phone.css")
    assert phone_js.status_code == console_js.status_code == phone_css.status_code == 200
    assert "FormData" in phone_js.text
    assert "/media`" in phone_js.text
    assert "uploaded.id" in phone_js.text
    assert "readAsDataURL" not in phone_js.text
    assert "d.data.reverse()" in phone_js.text
    assert "new WebSocket" in phone_js.text


def test_guide_contains_copyable_end_to_end_sections(client: TestClient) -> None:
    html = client.get("/guide").text
    for section in ("guide-1", "guide-2", "guide-3", "guide-4", "guide-5", "guide-help"):
        assert f'id="{section}"' in html
    javascript = client.get("/static/console.js").text
    assert "guideLanguage" in javascript
    assert "X-Hub-Signature-256" in javascript
    assert "131047" not in javascript or "guide" in html


@pytest.mark.asyncio
async def test_textual_phone_composes_without_server() -> None:
    phone = PhoneApp("15550002001", "http://127.0.0.1:1", "token", "none")
    async with phone.run_test():
        assert phone.query_one("#composer")
        assert phone.query_one("#messages")
        assert "15550002001" in str(phone.query_one("#identity").render())


def test_all_packaged_web_assets_exist() -> None:
    web = Path(__file__).parents[1] / "src" / "whatsapp_ghost" / "web"
    expected = {"console.html", "console.css", "console.js", "phone.html", "phone.css", "phone.js"}
    assert expected <= {path.name for path in web.iterdir() if path.is_file()}
