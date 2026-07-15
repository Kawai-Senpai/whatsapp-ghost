from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whatsapp_ghost.api import create_app
from whatsapp_ghost.config import Settings
from whatsapp_ghost.tui import PhoneApp


def make_settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path, "http://testserver", "token", "secret", "verify", "strict", 0.01, "none")


def test_strict_service_window_message_lifecycle_and_webhook(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    headers = {"Authorization": "Bearer token"}
    with TestClient(app) as client:
        outbound = {"messaging_product": "whatsapp", "to": "15550002001", "type": "text", "text": {"body": "Hello"}}
        rejected = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=outbound)
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == 131047

        with client.websocket_connect("/_sandbox/clients/15550002001") as phone_socket:
            incoming = client.post("/_sandbox/phones/15550002001/messages", json={"type": "text", "text": "Hi"})
            assert incoming.status_code == 201
            assert incoming.json()["type"] == "text"
            assert incoming.json()["text"]["body"] == "Hi"
            assert phone_socket.receive_json()["event"] == "message"

        accepted = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=outbound)
        assert accepted.status_code == 200
        assert accepted.json()["messages"][0]["id"].startswith("wamid.")

        deliveries = client.get("/_sandbox/webhooks").json()["data"]
        assert deliveries
        assert deliveries[0]["request_body"]["object"] == "whatsapp_business_account"


def test_template_media_auth_and_clock(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    headers = {"Authorization": "Bearer token"}
    with TestClient(app) as client:
        templates = client.get("/v25.0/WABA_LOCAL/message_templates", headers=headers).json()["data"]
        assert templates[0]["status"] == "APPROVED"
        template = {"messaging_product": "whatsapp", "to": "15550002001", "type": "template", "template": {"name": "hello_world", "language": {"code": "en_US"}, "components": [{"type": "body", "parameters": [{"type": "text", "text": "Tester"}]}]}}
        assert client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=template).status_code == 200

        upload = client.post("/v25.0/PHONE_LOCAL/media", headers=headers, data={"messaging_product": "whatsapp"}, files={"file": ("hello.txt", b"hello", "text/plain")})
        assert upload.status_code == 200
        media_id = upload.json()["id"]
        metadata = client.get(f"/v25.0/{media_id}", headers=headers).json()
        assert metadata["sha256"] == hashlib.sha256(b"hello").hexdigest()
        assert client.get(f"/_sandbox/media/{media_id}").status_code == 401
        assert client.get(f"/_sandbox/media/{media_id}", headers=headers).content == b"hello"

        advanced = client.post("/_sandbox/clock", json={"action": "advance", "value": "25h"}).json()
        assert advanced["frozen"] is True


def test_auth_and_webhook_verification(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/v25.0/WABA_LOCAL")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == 190
        verified = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify", "hub.challenge": "123"})
        assert verified.text == "123"

        console = client.get("/console")
        assert console.status_code == 200
        assert "Meta for Developers" in console.text
        phone = client.get("/phone")
        assert phone.status_code == 200
        assert "WhatsApp Web" in phone.text

        local_app = client.post("/_sandbox/apps", json={"name": "Integration App"}).json()
        app_headers = {"Authorization": f"Bearer {local_app['access_token']}"}
        assert client.get("/v25.0/WABA_LOCAL", headers=app_headers).status_code == 200


@pytest.mark.asyncio
async def test_phone_tui_composes_without_a_server() -> None:
    phone = PhoneApp("15550002001", "http://127.0.0.1:1", "token", "none")
    async with phone.run_test():
        assert phone.query_one("#composer")
        assert "15550002001" in str(phone.query_one("#identity").render())
