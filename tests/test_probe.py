"""The probe endpoint: one call that diagnoses the sandbox."""
from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import CallbackRecorder, open_window
from test_webhooks import wait_for_posts


def codes(probe: dict) -> set[str]:
    return {item["code"] for item in probe["findings"]}


def test_probe_flags_a_sandbox_with_nothing_subscribed(client: TestClient) -> None:
    open_window(client)

    probe = client.get("/_sandbox/probe").json()

    assert "no_subscriptions" in codes(probe)
    assert probe["subscriptions"]["active"] == 0
    assert probe["webhooks"]["totals"].get("unrouted")


def test_probe_reports_healthy_when_delivery_is_working(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder,
) -> None:
    client.post("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers, json={
        "callback_url": callback.url, "verify_token": "receiver-token",
    })
    open_window(client)
    wait_for_posts(callback, 1)

    probe = client.get("/_sandbox/probe").json()

    assert probe["subscriptions"]["active"] == 1
    assert probe["webhooks"]["window_failed"] == 0
    assert "delivery_failing" not in codes(probe)
    assert "no_subscriptions" not in codes(probe)


def test_probe_names_the_rate_limit_and_the_failure_rate(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder,
) -> None:
    """This is the deployed failure, reproduced: the probe must say '429'."""
    client.post("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers, json={
        "callback_url": callback.url, "verify_token": "receiver-token",
    })
    callback.response_status = 429
    callback.response_body = b'{"error":"Rate limit exceeded: 200 per 1 minute"}'
    open_window(client)
    wait_for_posts(callback, 1)

    probe = client.get("/_sandbox/probe").json()

    assert "rate_limited" in codes(probe)
    assert "delivery_failing" in codes(probe)
    assert probe["webhooks"]["failures_by_status_code"]["429"] >= 1
    assert any("Rate limit exceeded" in reason
               for reason in probe["webhooks"]["failures_by_error"])
    detail = next(f["detail"] for f in probe["findings"] if f["code"] == "rate_limited")
    assert "429" in detail


def test_probe_counts_inbound_events_the_integration_never_acknowledged(
    client: TestClient,
) -> None:
    client.post("/_sandbox/phones", json={"wa_id": "15550002001", "display_name": "Asha"})
    client.post("/_sandbox/phones/15550002001/messages", json={
        "phone_number_id": "PHONE_LOCAL", "type": "text", "text": "hello",
    })

    probe = client.get("/_sandbox/probe").json()

    assert probe["messages"]["unconfirmed_inbound"] >= 1
    assert "unconfirmed_inbound" in codes(probe)


def test_probe_reports_a_frozen_clock(client: TestClient) -> None:
    client.post("/_sandbox/clock", json={"action": "advance", "value": "24h"})

    probe = client.get("/_sandbox/probe").json()

    assert probe["clock_frozen"] is True
    assert "clock_frozen" in codes(probe)


def test_webhook_log_can_be_filtered_to_one_message(client: TestClient) -> None:
    client.post("/_sandbox/phones", json={"wa_id": "15550002001", "display_name": "Asha"})
    first = client.post("/_sandbox/phones/15550002001/messages", json={
        "phone_number_id": "PHONE_LOCAL", "type": "text", "text": "one",
    }).json()
    client.post("/_sandbox/phones/15550002001/messages", json={
        "phone_number_id": "PHONE_LOCAL", "type": "text", "text": "two",
    })

    filtered = client.get("/_sandbox/webhooks", params={"message_id": first["id"]}).json()["data"]

    assert filtered, "the message's own webhook must be findable by its id"
    assert all(first["id"] in str(item["request_body"]) for item in filtered)
    assert len(filtered) < len(client.get("/_sandbox/webhooks").json()["data"])


def test_webhook_log_can_be_filtered_by_event_type_and_time(client: TestClient) -> None:
    open_window(client)
    everything = client.get("/_sandbox/webhooks").json()["data"]
    cutoff = everything[0]["created_at"]

    assert client.get("/_sandbox/webhooks", params={"event_type": "messages"}).json()["data"]
    assert not client.get("/_sandbox/webhooks", params={"event_type": "nope"}).json()["data"]
    since = client.get("/_sandbox/webhooks", params={"since": cutoff}).json()["data"]
    assert len(since) <= len(everything)
    assert all(item["created_at"] >= cutoff for item in since)


def test_message_diagnostics_exposes_the_receivers_response_body(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder,
) -> None:
    """A successful delivery should show what the receiver actually said."""
    client.post("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers, json={
        "callback_url": callback.url, "verify_token": "receiver-token",
    })
    callback.response_body = b'{"received":true,"queued":7}'
    client.post("/_sandbox/phones", json={"wa_id": "15550002001", "display_name": "Asha"})
    message = client.post("/_sandbox/phones/15550002001/messages", json={
        "phone_number_id": "PHONE_LOCAL", "type": "text", "text": "hello",
    }).json()
    wait_for_posts(callback, 1)

    diagnostics = client.get(f"/_sandbox/messages/{message['id']}/diagnostics").json()

    delivery = diagnostics["deliveries"][0]
    assert delivery["last_response_body"] == '{"received":true,"queued":7}'
