"""Location sharing, checked against the Cloud API wire shape.

The sandbox's value is that a payload it emits is indistinguishable from one
Meta emits, so these assert the exact envelope rather than "a webhook arrived".
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import CallbackRecorder, open_window
from test_webhooks import wait_for_posts

GATEWAY = {
    "latitude": 18.9220,
    "longitude": 72.8347,
    "name": "Gateway of India",
    "address": "Apollo Bandar, Colaba, Mumbai 400001",
}


def send_location(client: TestClient, headers: dict[str, str], location: dict) -> object:
    return client.post("/v26.0/PHONE_LOCAL/messages", headers=headers, json={
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": "15550002001", "type": "location", "location": location,
    })


def test_outbound_location_returns_the_meta_send_response(
    client: TestClient, headers: dict[str, str],
) -> None:
    open_window(client)

    response = send_location(client, headers, GATEWAY)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["messaging_product"] == "whatsapp"
    assert payload["contacts"] == [{"input": "15550002001", "wa_id": "15550002001"}]
    assert payload["messages"][0]["id"].startswith("wamid.")


def test_outbound_location_is_stored_with_the_location_untouched(
    client: TestClient, headers: dict[str, str],
) -> None:
    open_window(client)
    sent = send_location(client, headers, GATEWAY)
    message_id = sent.json()["messages"][0]["id"]

    stored = next(
        item for item in client.get("/_sandbox/messages", params={"limit": 50}).json()["data"]
        if item["id"] == message_id
    )

    assert stored["message_type"] == "location"
    assert stored["payload"]["type"] == "location"
    assert stored["payload"]["location"] == GATEWAY


def test_inbound_location_webhook_is_the_exact_meta_envelope(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder,
) -> None:
    client.post("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers, json={
        "callback_url": callback.url, "verify_token": "receiver-token",
    })
    client.post("/_sandbox/phones", json={"wa_id": "15550002001", "display_name": "Asha"})
    callback.posts.clear()

    created = client.post("/_sandbox/phones/15550002001/messages", json={
        "phone_number_id": "PHONE_LOCAL", "type": "location", "location": GATEWAY,
    })
    assert created.status_code == 201, created.text
    wait_for_posts(callback, 1)

    value = callback.posts[0]["json"]["entry"][0]["changes"][0]["value"]
    assert callback.posts[0]["json"]["object"] == "whatsapp_business_account"
    assert callback.posts[0]["json"]["entry"][0]["changes"][0]["field"] == "messages"
    assert value["messaging_product"] == "whatsapp"
    assert value["metadata"] == {
        "display_phone_number": "15550001000", "phone_number_id": "PHONE_LOCAL",
    }
    assert value["contacts"] == [{"profile": {"name": "Asha"}, "wa_id": "15550002001"}]

    message = value["messages"][0]
    assert set(message) == {"from", "id", "timestamp", "type", "location"}
    assert message["from"] == "15550002001"
    assert message["type"] == "location"
    assert message["id"].startswith("wamid.")
    assert message["timestamp"].isdigit()
    assert message["location"] == GATEWAY


def test_inbound_location_omits_name_and_address_when_not_given(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder,
) -> None:
    """Meta omits optional fields; it does not send them as empty strings."""
    client.post("/v25.0/WABA_LOCAL/subscribed_apps", headers=headers, json={
        "callback_url": callback.url, "verify_token": "receiver-token",
    })
    client.post("/_sandbox/phones", json={"wa_id": "15550002001", "display_name": "Asha"})
    callback.posts.clear()

    client.post("/_sandbox/phones/15550002001/messages", json={
        "phone_number_id": "PHONE_LOCAL", "type": "location",
        "location": {"latitude": 28.6129, "longitude": 77.2295},
    })
    wait_for_posts(callback, 1)

    location = callback.posts[0]["json"]["entry"][0]["changes"][0]["value"]["messages"][0]["location"]
    assert location == {"latitude": 28.6129, "longitude": 77.2295}
    assert "name" not in location and "address" not in location


@pytest.mark.parametrize("location, code, reason", [
    ({"longitude": 72.8347}, 131008, "location.latitude is required"),
    ({"latitude": 18.9220}, 131008, "location.longitude is required"),
    ({"latitude": "north", "longitude": 72.8}, 131009, "location.latitude must be a number"),
    ({"latitude": 91, "longitude": 72.8}, 131009, "location.latitude must be between"),
    ({"latitude": 18.9, "longitude": 181}, 131009, "location.longitude must be between"),
    ({"latitude": 18.9, "longitude": 72.8, "address": "Colaba"}, 131008, "location.name is required"),
])
def test_outbound_location_validation_matches_meta(
    client: TestClient, headers: dict[str, str], location: dict, code: int, reason: str,
) -> None:
    open_window(client)

    response = send_location(client, headers, location)

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert reason in error["error_data"]["details"]


def test_swapped_coordinates_are_rejected_rather_than_silently_mapped(
    client: TestClient, headers: dict[str, str],
) -> None:
    """A lat/long pair entered the wrong way round is the usual way this breaks.

    181 is not a latitude, so the swap is detectable and worth failing on rather
    than plotting a pin somewhere meaningless.
    """
    open_window(client)

    response = send_location(client, headers, {"latitude": 72.8347, "longitude": 118.9220})
    assert response.status_code == 200, "a legal pair must still be accepted"

    swapped = send_location(client, headers, {"latitude": 118.9220, "longitude": 72.8347})
    assert swapped.status_code == 400
    assert swapped.json()["error"]["code"] == 131009


def test_inbound_location_with_bad_coordinates_is_rejected(client: TestClient) -> None:
    client.post("/_sandbox/phones", json={"wa_id": "15550002001", "display_name": "Asha"})

    response = client.post("/_sandbox/phones/15550002001/messages", json={
        "phone_number_id": "PHONE_LOCAL", "type": "location",
        "location": {"latitude": 200, "longitude": 0},
    })

    assert response.status_code == 400
    assert "must be between" in response.json()["error"]


def test_phone_client_renders_location_bubbles(client: TestClient) -> None:
    phone_js = client.get("/static/phone.js").text
    assert "function locationHtml" in phone_js
    assert "function locationPlate" in phone_js
    assert "openstreetmap.org" in phone_js
    assert "loc-card" in phone_js
