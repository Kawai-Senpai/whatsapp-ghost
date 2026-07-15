from __future__ import annotations

import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from conftest import CallbackRecorder, open_window, text_message
from whatsapp_ghost.api import create_app


def wait_for_posts(callback: CallbackRecorder, count: int, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while len(callback.posts) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(callback.posts) >= count


def wait_for_delivery(client: TestClient, delivery_id: str, status: str, timeout: float = 2) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        delivery = next(item for item in client.get("/_sandbox/webhooks").json()["data"] if item["id"] == delivery_id)
        if delivery["status"] == status:
            return delivery
        time.sleep(0.01)
    raise AssertionError(f"webhook {delivery_id} did not reach {status}")


def subscribe(client: TestClient, headers: dict[str, str], callback: CallbackRecorder) -> None:
    response = client.post("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers, json={
        "callback_url": callback.url, "verify_token": "receiver-token",
    })
    assert response.status_code == 200, response.text
    assert response.json() == {"success": True}


def test_standard_webhook_verification_endpoint(client: TestClient) -> None:
    valid = client.get("/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "verify", "hub.challenge": "challenge-123",
    })
    assert valid.status_code == 200
    assert valid.text == "challenge-123"
    invalid = client.get("/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "challenge-123",
    })
    assert invalid.status_code == 403


def test_subscription_performs_real_challenge_and_lists_subscription(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder
) -> None:
    subscribe(client, headers, callback)
    assert callback.gets
    verification = callback.gets[0]
    assert verification["hub.mode"] == ["subscribe"]
    assert verification["hub.verify_token"] == ["receiver-token"]
    assert verification["hub.challenge"][0]

    compatible = client.get("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers).json()["data"]
    assert compatible[0]["callback_url"] == callback.url
    inspector = client.get("/_sandbox/webhook-subscriptions").json()["data"]
    assert inspector[0]["active"] == 1
    assert inspector[0]["business_name"] == "Ghost Demo Business"


def test_inbound_webhook_envelope_signature_and_delivery_history(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder
) -> None:
    subscribe(client, headers, callback)
    inbound = open_window(client)
    wait_for_posts(callback, 1)
    received = callback.posts[0]
    expected = "sha256=" + hmac.new(b"secret", received["raw"], hashlib.sha256).hexdigest()
    assert received["signature"] == expected

    payload = received["json"]
    assert payload["object"] == "whatsapp_business_account"
    assert payload["entry"][0]["id"] == "WABA_LOCAL"
    value = payload["entry"][0]["changes"][0]["value"]
    assert value["metadata"] == {"display_phone_number": "15550001000", "phone_number_id": "PHONE_LOCAL"}
    assert value["contacts"][0]["wa_id"] == "15550002001"
    assert value["messages"][0]["id"] == inbound["id"]

    history = client.get("/_sandbox/webhooks").json()["data"]
    assert history[0]["status"] == "delivered"
    assert history[0]["attempt_count"] == 1
    assert history[0]["last_status_code"] == 200
    assert history[0]["last_response_body"] == '{"received":true}'
    assert history[0]["attempts"][0]["status_code"] == 200


def test_status_webhooks_cover_sent_delivered_and_read(
    slow_settings, headers: dict[str, str], callback: CallbackRecorder
) -> None:
    with TestClient(create_app(slow_settings)) as client:
        subscribe(client, headers, callback)
        open_window(client)
        wait_for_posts(callback, 1)
        callback.posts.clear()
        sent = client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message()).json()
        message_id = sent["messages"][0]["id"]

        client.post(f"/_sandbox/messages/{message_id}/status", json={"status": "sent"})
        client.post(f"/_sandbox/messages/{message_id}/status", json={"status": "delivered"})
        client.post("/_sandbox/phones/15550002001/read", json={"phone_number_id": "PHONE_LOCAL"})
        wait_for_posts(callback, 3)
        statuses = [
            post["json"]["entry"][0]["changes"][0]["value"]["statuses"][0]["status"]
            for post in callback.posts
        ]
        assert statuses == ["sent", "delivered", "read"]


def test_unsubscribed_events_are_retained_as_unrouted(client: TestClient) -> None:
    open_window(client)
    history = client.get("/_sandbox/webhooks").json()["data"]
    assert history[0]["status"] == "unrouted"
    assert history[0]["destination_url"] is None
    assert history[0]["attempt_count"] == 0
    assert history[0]["attempts"] == []
    assert history[0]["signature"].startswith("sha256=")


def test_failed_callback_records_http_response_and_replay(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder
) -> None:
    subscribe(client, headers, callback)
    callback.response_status = 503
    callback.response_body = b'{"error":"temporarily unavailable"}'
    open_window(client)
    history = client.get("/_sandbox/webhooks").json()["data"]
    delivery = wait_for_delivery(client, history[0]["id"], "failed")
    assert delivery["last_status_code"] == 503
    assert delivery["last_response_body"] == '{"error":"temporarily unavailable"}'

    callback.response_status = 200
    callback.response_body = b'{"replayed":true}'
    replayed = client.post(f"/_sandbox/webhooks/{delivery['id']}/replay")
    assert replayed.status_code == 200
    refreshed = wait_for_delivery(client, delivery["id"], "delivered")
    assert refreshed["status"] == "delivered"
    assert refreshed["attempt_count"] == 2
    assert [attempt["status_code"] for attempt in refreshed["attempts"]] == [503, 200]


def test_unsubscribe_stops_delivery_but_keeps_history(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder
) -> None:
    subscribe(client, headers, callback)
    assert client.delete("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers).json() == {"success": True}
    open_window(client)
    assert callback.posts == []
    assert client.get("/_sandbox/webhooks").json()["data"][0]["status"] == "unrouted"


def test_subscription_and_replay_error_paths(client: TestClient, headers: dict[str, str]) -> None:
    missing_callback = client.post("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers, json={})
    assert missing_callback.json()["error"]["code"] == 131008
    failed_verification = client.post("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers, json={
        "callback_url": "http://127.0.0.1:1/webhook", "verify_token": "no-server",
    })
    assert failed_verification.status_code == 400
    assert failed_verification.json()["error"]["code"] == 100
    unknown_waba = client.post("/v25.0/UNKNOWN/subscribed_apps", headers=headers, json={
        "callback_url": "http://127.0.0.1:9000/webhook",
    })
    assert unknown_waba.status_code == 404
    assert client.post("/_sandbox/webhooks/UNKNOWN/replay").status_code == 404


def test_webhook_request_body_is_exactly_the_signed_json(client: TestClient) -> None:
    open_window(client)
    event = client.get("/_sandbox/webhooks").json()["data"][0]
    canonical = json.dumps(event["request_body"], separators=(",", ":")).encode()
    expected = "sha256=" + hmac.new(b"secret", canonical, hashlib.sha256).hexdigest()
    assert event["signature"] == expected
