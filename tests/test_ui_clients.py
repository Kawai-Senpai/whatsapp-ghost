from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_ghost.config import Settings
from whatsapp_ghost.tui import PhoneApp


def test_console_contains_every_primary_management_surface(client: TestClient) -> None:
    html = client.get("/console").text
    for page in ("overview", "setup", "resources", "apps", "templates", "webhooks", "guide", "simulator"):
        assert f'id="{page}"' in html
        assert f'data-page="{page}"' in html
    for modal in ("app-modal", "business-modal", "sender-modal", "template-modal", "webhook-modal", "phone-modal"):
        assert f'id="{modal}"' in html


def test_webhook_ui_selects_signing_app_and_can_unsubscribe(client: TestClient) -> None:
    html = client.get("/console").text
    javascript = client.get("/static/console.js").text

    assert 'id="wh-app"' in html
    assert "app.access_token" in javascript
    assert "function unsubscribeWebhook" in javascript
    assert "method:'DELETE'" in javascript


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


def test_phone_client_renders_template_bodies_with_variables(client: TestClient) -> None:
    """A real client shows the rendered template body, not the template name,
    so the phone UI must substitute the sent positional parameters."""
    phone_js = client.get("/static/phone.js").text
    assert "function renderTemplateBody" in phone_js
    assert "loadTemplateDefinitions" in phone_js
    # substitutes {{n}} and falls back through currency/date_time fallback values
    assert "fallback_value" in phone_js
    assert "val.text || val.name" in phone_js
    assert "function renderTemplateButtons" in phone_js
    assert "data-template-reply" in phone_js


def test_console_exposes_a_credentials_page(client: TestClient) -> None:
    """Every ID, token and secret should be reachable from one page, with
    ready-to-paste config for the common integrations."""
    html = client.get("/console").text
    assert 'data-page="credentials"' in html
    assert 'id="cred-grid"' in html

    console_js = client.get("/static/console.js").text
    assert "function renderCredentials" in console_js
    # the .env block must use the same names the services actually read
    for key in ("WHATSAPP_GRAPH_BASE_URL", "WHATSAPP_ACCESS_TOKEN",
                "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_BUSINESS_ACCOUNT_ID",
                "WHATSAPP_APP_SECRET", "WHATSAPP_WEBHOOK_VERIFY_TOKEN"):
        assert key in console_js
    # onclick handlers must not be broken by embedded double quotes
    assert "onclick=\"setCredFormat('" in console_js


def test_docker_compose_defaults_are_deployable() -> None:
    """The container must be able to serve a non-localhost address: it listens
    on 0.0.0.0, keeps its data on a volume, and lets WABA_BASE_URL be
    overridden so absolute media URLs point at the real host."""
    root = Path(__file__).resolve().parent.parent
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")

    assert "--host" in dockerfile and "0.0.0.0" in dockerfile
    assert "WABA_DATA_DIR=/data" in dockerfile
    # media and the database must outlive the container
    assert "ghost-data:/data" in compose
    # every credential and the base URL must be overridable from the environment
    for key in ("WABA_BASE_URL", "WABA_ACCESS_TOKEN", "WABA_APP_SECRET", "WABA_VERIFY_TOKEN"):
        assert key in compose


def test_binary_upload_to_a_json_endpoint_reports_422_not_a_server_error(client: TestClient) -> None:
    """A multipart image sent to a JSON-only endpoint is a client mistake.

    FastAPI's default validation handler echoes the offending input back and
    calls .decode() on it, so raw PNG bytes surfaced as a 500 UnicodeDecodeError
    instead of a validation error.
    """
    png = bytes.fromhex("89504e470d0a1a0a") + b"\x00\xff" * 40
    response = client.post(
        "/_sandbox/phones/15550002001/messages",
        files={"file": ("test.png", png, "image/png")},
        data={"type": "image"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    # the binary payload must be summarised, never echoed back raw
    assert "binary data" in json.dumps(detail)


def test_console_html_is_read_fresh_so_edits_need_no_restart(
    settings: Settings, tmp_path: Path
) -> None:
    """Reading the markup at import time froze it for the process lifetime,
    so edits to web/console.html only appeared after a server restart."""
    from whatsapp_ghost import web_console

    first = web_console.CONSOLE_HTML
    target = web_console.WEB_DIR / "console.html"
    original = target.read_text(encoding="utf-8")
    try:
        target.write_text(original + "\n<!-- edited -->", encoding="utf-8")
        assert "<!-- edited -->" in web_console.CONSOLE_HTML
        assert "<!-- edited -->" not in first
    finally:
        target.write_text(original, encoding="utf-8")
