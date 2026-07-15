from __future__ import annotations

import hashlib
from dataclasses import replace
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
        assert client.get(f"/_sandbox/media/{media_id}", params={"access_token": "token"}).content == b"hello"

        image_bytes = b"\x89PNG\r\n\x1a\nlocal-image"
        image_upload = client.post(
            "/v25.0/PHONE_LOCAL/media",
            headers=headers,
            data={"messaging_product": "whatsapp"},
            files={"file": ("photo.png", image_bytes, "image/png")},
        )
        assert image_upload.status_code == 200
        image_id = image_upload.json()["id"]
        assert (tmp_path / "media" / image_id).read_bytes() == image_bytes
        image_meta = client.get(f"/v25.0/{image_id}", headers=headers).json()
        inbound_image = client.post("/_sandbox/phones/15550002001/messages", json={
            "type": "image",
            "phone_number_id": "PHONE_LOCAL",
            "image": {"id": image_id, "mime_type": image_meta["mime_type"], "sha256": image_meta["sha256"], "caption": "Receipt"},
        })
        assert inbound_image.status_code == 201
        assert inbound_image.json()["image"]["id"] == image_id

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
        guide = client.get("/guide")
        assert guide.status_code == 200
        assert "Connect an application in minutes" in guide.text
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


def test_multi_business_conversations_and_read_ticks(tmp_path: Path) -> None:
    app = create_app(replace(make_settings(tmp_path), status_delay_seconds=60))
    headers = {"Authorization": "Bearer token"}
    with TestClient(app) as client:
        created = client.post("/_sandbox/businesses", json={
            "name": "Second Business", "verified_name": "Second Sender", "display_phone_number": "15550009999",
        }).json()
        second_phone = created["phone_number_id"]

        first = client.post("/_sandbox/phones/15550002001/messages", json={
            "type": "text", "text": "Hello first", "phone_number_id": "PHONE_LOCAL",
        }).json()
        client.post("/_sandbox/phones/15550002001/messages", json={
            "type": "text", "text": "Hello second", "phone_number_id": second_phone,
        })
        first_history = client.get("/_sandbox/messages", params={"wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL"}).json()["data"]
        second_history = client.get("/_sandbox/messages", params={"wa_id": "15550002001", "phone_number_id": second_phone}).json()["data"]
        assert [item["payload"]["text"]["body"] for item in first_history] == ["Hello first"]
        assert [item["payload"]["text"]["body"] for item in second_history] == ["Hello second"]
        assert len(client.get("/_sandbox/conversations", params={"wa_id": "15550002001"}).json()["data"]) == 2

        marked = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json={
            "messaging_product": "whatsapp", "status": "read", "message_id": first["id"],
        })
        assert marked.json() == {"success": True}
        assert client.get("/_sandbox/messages", params={"wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL"}).json()["data"][0]["status"] == "read"

        sent = client.post(f"/v25.0/{second_phone}/messages", headers=headers, json={
            "messaging_product": "whatsapp", "to": "15550002001", "type": "text", "text": {"body": "Business reply"},
        })
        assert sent.status_code == 200
        client.post(f"/_sandbox/messages/{sent.json()['messages'][0]['id']}/status", json={"status": "delivered"})
        read = client.post("/_sandbox/phones/15550002001/read", json={"phone_number_id": second_phone}).json()
        assert read["read"] >= 1
        webhook_payloads = client.get("/_sandbox/webhooks").json()["data"]
        message_statuses = [
            status["status"]
            for delivery in webhook_payloads
            for status in delivery["request_body"]["entry"][0]["changes"][0]["value"].get("statuses", [])
        ]
        assert "read" in message_statuses


def test_multiple_sender_numbers_under_one_waba(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    headers = {"Authorization": "Bearer token"}
    with TestClient(app) as client:
        added = client.post("/_sandbox/businesses/WABA_LOCAL/phone-numbers", json={
            "verified_name": "Ghost Sales", "display_phone_number": "+1 (555) 000-1001",
        })
        assert added.status_code == 201
        second_phone = added.json()["id"]
        assert added.json()["waba_id"] == "WABA_LOCAL"
        assert added.json()["display_phone_number"] == "15550001001"

        numbers = client.get("/v25.0/WABA_LOCAL/phone_numbers", headers=headers).json()["data"]
        assert {item["id"] for item in numbers} == {"PHONE_LOCAL", second_phone}

        client.post("/_sandbox/phones/15550002001/messages", json={
            "type": "text", "text": "Message sales", "phone_number_id": second_phone,
        })
        client.post("/_sandbox/phones/15550002001/messages", json={
            "type": "text", "text": "Message support", "phone_number_id": "PHONE_LOCAL",
        })
        sales = client.get("/_sandbox/messages", params={
            "wa_id": "15550002001", "phone_number_id": second_phone,
        }).json()["data"]
        support = client.get("/_sandbox/messages", params={
            "wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL",
        }).json()["data"]
        assert sales[0]["payload"]["text"]["body"] == "Message sales"
        assert support[0]["payload"]["text"]["body"] == "Message support"


def test_message_order_is_stable_when_clock_is_frozen(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        client.post("/_sandbox/clock", json={"action": "set", "value": "2026-07-15T12:00:00Z"})
        for text in ("First", "Second"):
            response = client.post("/_sandbox/phones/15550002001/messages", json={
                "type": "text", "text": text, "phone_number_id": "PHONE_LOCAL",
            })
            assert response.status_code == 201

        history = client.get("/_sandbox/messages", params={
            "wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL",
        }).json()["data"]
        assert history[0]["created_at"] == history[1]["created_at"]
        assert [message["payload"]["text"]["body"] for message in history] == ["Second", "First"]
