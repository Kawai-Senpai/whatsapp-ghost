from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from conftest import open_window
from whatsapp_ghost.api import create_app
from whatsapp_ghost.config import Settings


def test_sqlite_messages_resources_and_media_survive_app_restart(settings: Settings, headers: dict[str, str]) -> None:
    with TestClient(create_app(settings)) as first:
        first.post("/_sandbox/phones", json={"wa_id": "15550002002", "display_name": "Persistent Alice"})
        business = first.post("/_sandbox/businesses", json={
            "name": "Persistent Business", "verified_name": "Persistent Sender", "display_phone_number": "15550003000",
        }).json()
        open_window(first, "15550002002", business["phone_number_id"])
        upload = first.post(
            f"/v25.0/{business['phone_number_id']}/media",
            headers=headers,
            data={"messaging_product": "whatsapp"},
            files={"file": ("persist.txt", b"persistent bytes", "text/plain")},
        )
        media_id = upload.json()["id"]

    with TestClient(create_app(settings)) as second:
        assert any(user["wa_id"] == "15550002002" for user in second.get("/_sandbox/phones").json()["data"])
        businesses = second.get("/_sandbox/businesses").json()["data"]
        assert any(item["id"] == business["waba_id"] for item in businesses)
        messages = second.get("/_sandbox/messages", params={
            "wa_id": "15550002002", "phone_number_id": business["phone_number_id"],
        }).json()["data"]
        assert messages[0]["payload"]["text"]["body"] == "Hello business"
        assert second.get(f"/_sandbox/media/{media_id}", headers=headers).content == b"persistent bytes"


def test_reset_removes_custom_state_and_reseeds_defaults(client: TestClient, headers: dict[str, str], settings: Settings) -> None:
    client.post("/_sandbox/phones", json={"wa_id": "15550002009", "display_name": "Temporary"})
    media_id = client.post(
        "/v25.0/PHONE_LOCAL/media",
        headers=headers,
        data={"messaging_product": "whatsapp"},
        files={"file": ("temporary.txt", b"temporary", "text/plain")},
    ).json()["id"]
    assert (settings.media_dir / media_id).exists()

    assert client.post("/_sandbox/reset").json() == {"success": True}
    assert not (settings.media_dir / media_id).exists()
    assert {user["wa_id"] for user in client.get("/_sandbox/phones").json()["data"]} == {"15550002001"}
    assert {business["id"] for business in client.get("/_sandbox/businesses").json()["data"]} == {"WABA_LOCAL"}
    assert client.get("/_sandbox/messages").json()["data"] == []
    assert client.get("/v25.0/WABA_LOCAL/message_templates", headers=headers).json()["data"][0]["name"] == "hello_world"


def test_clock_set_advance_and_reset(client: TestClient) -> None:
    initial = client.get("/_sandbox/clock").json()
    assert initial["frozen"] is False

    set_clock = client.post("/_sandbox/clock", json={"action": "set", "value": "2026-07-15T10:00:00Z"}).json()
    assert set_clock["frozen"] is True
    assert datetime.fromisoformat(set_clock["now"]).hour == 10

    advanced = client.post("/_sandbox/clock", json={"action": "advance", "value": "1d2h30m"}).json()
    advanced_time = datetime.fromisoformat(advanced["now"])
    assert (advanced_time.day, advanced_time.hour, advanced_time.minute) == (16, 12, 30)

    reset = client.post("/_sandbox/clock", json={"action": "reset"}).json()
    assert reset["frozen"] is False


def test_invalid_clock_action_is_rejected(client: TestClient) -> None:
    response = client.post("/_sandbox/clock", json={"action": "warp", "value": "3h"})
    assert response.status_code == 400
    assert "set, advance, or reset" in response.json()["error"]
