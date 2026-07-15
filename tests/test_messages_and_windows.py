from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from conftest import open_window, text_message
from whatsapp_ghost.api import create_app
from whatsapp_ghost.config import Settings


def message_status(client: TestClient, message_id: str) -> str:
    rows = client.get("/_sandbox/messages", params={"limit": 500}).json()["data"]
    return next(item["status"] for item in rows if item["id"] == message_id)


def wait_for_status(client: TestClient, message_id: str, expected: str, timeout: float = 1) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if message_status(client, message_id) == expected:
            return
        time.sleep(0.01)
    pytest.fail(f"message {message_id} did not reach {expected}; last status={message_status(client, message_id)}")


def test_strict_mode_rejects_free_form_before_customer_window(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 131047


def test_inbound_opens_window_then_outbound_reaches_delivered(client: TestClient, headers: dict[str, str]) -> None:
    inbound = open_window(client)
    assert inbound["from"] == "15550002001"
    assert inbound["type"] == "text"

    sent = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message(body="Business reply"))
    assert sent.status_code == 200
    message_id = sent.json()["messages"][0]["id"]
    assert message_id.startswith("wamid.")
    wait_for_status(client, message_id, "delivered")

    conversation = client.get("/_sandbox/conversations", params={
        "wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL",
    }).json()["data"][0]
    assert conversation["service_window_open"] is True


def test_opening_phone_marks_delivered_business_messages_read(client: TestClient, headers: dict[str, str]) -> None:
    open_window(client)
    sent = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message()).json()
    message_id = sent["messages"][0]["id"]
    wait_for_status(client, message_id, "delivered")
    marked = client.post("/_sandbox/phones/15550002001/read", json={"phone_number_id": "PHONE_LOCAL"})
    assert marked.status_code == 200
    assert marked.json()["read"] == 1
    assert message_status(client, message_id) == "read"


def test_official_mark_read_validates_product_and_message_ownership(client: TestClient, headers: dict[str, str]) -> None:
    inbound = open_window(client)
    missing_product = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json={
        "status": "read", "message_id": inbound["id"],
    })
    assert missing_product.json()["error"]["code"] == 131009
    marked = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json={
        "messaging_product": "whatsapp", "status": "read", "message_id": inbound["id"],
    })
    assert marked.json() == {"success": True}
    unknown = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json={
        "messaging_product": "whatsapp", "status": "read", "message_id": "wamid.missing",
    })
    assert unknown.json()["error"]["code"] == 131009


def test_sandbox_status_endpoint_rejects_invalid_transitions(client: TestClient) -> None:
    inbound = open_window(client)
    response = client.post(f"/_sandbox/messages/{inbound['id']}/status", json={"status": "teleported"})
    assert response.status_code == 400
    assert "accepted" in response.json()["error"]


def test_window_expires_after_time_travel_but_template_still_sends(client: TestClient, headers: dict[str, str]) -> None:
    open_window(client)
    client.post("/_sandbox/clock", json={"action": "advance", "value": "25h"})
    rejected = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message())
    assert rejected.json()["error"]["code"] == 131047

    template = {
        "messaging_product": "whatsapp",
        "to": "15550002001",
        "type": "template",
        "template": {
            "name": "hello_world",
            "language": {"code": "en_US"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": "Tester"}]}],
        },
    }
    assert client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=template).status_code == 200


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"to": "15550002001", "type": "text", "text": {"body": "x"}}, 131009),
        ({"messaging_product": "whatsapp", "type": "text", "text": {"body": "x"}}, 131008),
        ({"messaging_product": "whatsapp", "to": "15550002001", "type": "unknown", "unknown": {}}, 131051),
        ({"messaging_product": "whatsapp", "to": "15550002001", "type": "text", "text": {}}, 131008),
        ({"messaging_product": "whatsapp", "to": "15550002001", "type": "image", "image": {}}, 131008),
        ({"messaging_product": "whatsapp", "to": "15550002001", "type": "image", "image": {"id": "missing"}}, 131052),
    ],
)
def test_message_validation_error_codes(client: TestClient, headers: dict[str, str], payload: dict, code: int) -> None:
    response = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


def test_unknown_sender_and_unknown_strict_recipient(client: TestClient, headers: dict[str, str]) -> None:
    unknown_sender = client.post("/v25.0/UNKNOWN/messages", headers=headers, json=text_message())
    assert unknown_sender.json()["error"]["code"] == 100
    unknown_recipient = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message(to="15551112222"))
    assert unknown_recipient.json()["error"]["code"] == 131026


def test_offline_and_blocked_customers_fail_delivery(settings: Settings, headers: dict[str, str]) -> None:
    for state_change in ({"online": False}, {"blocked": True}):
        with TestClient(create_app(settings)) as client:
            open_window(client)
            client.patch("/_sandbox/phones/15550002001", json=state_change)
            sent = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message()).json()
            wait_for_status(client, sent["messages"][0]["id"], "failed")


def test_websocket_receives_inbound_and_status_events(client: TestClient, headers: dict[str, str]) -> None:
    with client.websocket_connect("/_sandbox/clients/15550002001") as socket:
        inbound = client.post("/_sandbox/phones/15550002001/messages", json={"type": "text", "text": "Hello"})
        assert inbound.status_code == 201
        assert socket.receive_json()["event"] == "message"

        sent = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message()).json()
        message_id = sent["messages"][0]["id"]
        events = [socket.receive_json(), socket.receive_json(), socket.receive_json()]
        statuses = [event.get("status") for event in events if event["event"] == "status"]
        assert statuses == ["sent", "delivered"]
        assert any(event["event"] == "message" for event in events)
        assert message_id in {event.get("message_id") for event in events}


def test_multiple_senders_and_customers_have_isolated_windows_and_histories(client: TestClient, headers: dict[str, str]) -> None:
    sender = client.post("/_sandbox/businesses/WABA_LOCAL/phone-numbers", json={
        "verified_name": "Ghost Sales", "display_phone_number": "15550001001",
    }).json()["id"]
    client.post("/_sandbox/phones", json={"wa_id": "15550002002", "display_name": "Alice"})

    open_window(client, "15550002001", "PHONE_LOCAL")
    assert client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message()).status_code == 200
    assert client.post(f"/v25.0/{sender}/messages", headers=headers, json=text_message()).json()["error"]["code"] == 131047
    assert client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message(to="15550002002")).json()["error"]["code"] == 131047

    open_window(client, "15550002001", sender)
    open_window(client, "15550002002", "PHONE_LOCAL")
    conversations = client.get("/_sandbox/conversations").json()["data"]
    assert {(c["user_wa_id"], c["phone_number_id"]) for c in conversations} >= {
        ("15550002001", "PHONE_LOCAL"), ("15550002001", sender), ("15550002002", "PHONE_LOCAL"),
    }


def test_message_order_is_stable_with_identical_frozen_timestamps(client: TestClient) -> None:
    client.post("/_sandbox/clock", json={"action": "set", "value": "2026-07-15T12:00:00Z"})
    for body in ("First", "Second", "Third"):
        open_window(client, phone_id="PHONE_LOCAL") if body == "First" else client.post(
            "/_sandbox/phones/15550002001/messages",
            json={"type": "text", "text": body, "phone_number_id": "PHONE_LOCAL"},
        )
    history = client.get("/_sandbox/messages", params={
        "wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL",
    }).json()["data"]
    assert [item["payload"]["text"]["body"] for item in history] == ["Third", "Second", "Hello business"]
    assert len({item["created_at"] for item in history}) == 1
