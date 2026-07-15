from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_config_and_all_web_pages_are_available(client: TestClient) -> None:
    health = client.get("/_sandbox/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mode"] == "strict"

    config = client.get("/_sandbox/config").json()
    assert config["base_url"] == "http://testserver"
    assert config["media_dir"].endswith("media")
    assert config["demo"]["phone_number_id"] == "PHONE_LOCAL"

    assert client.get("/").history[0].headers["location"] == "/console"
    for path, marker in (("/console", "Developer Console"), ("/guide", "Integration Guide"), ("/phone", "WhatsApp Web"), ("/docs", "Swagger UI")):
        response = client.get(path)
        assert response.status_code == 200
        assert marker in response.text


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic token"])
def test_graph_routes_reject_missing_or_invalid_tokens(client: TestClient, authorization: str | None) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    response = client.get("/v25.0/WABA_LOCAL", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == 190


def test_created_app_token_authenticates_and_rotation_invalidates_old_token(client: TestClient) -> None:
    created = client.post("/_sandbox/apps", json={"name": "Orders"})
    assert created.status_code == 201
    app = created.json()
    old_headers = {"Authorization": f"Bearer {app['access_token']}"}
    assert client.get("/v25.0/WABA_LOCAL", headers=old_headers).status_code == 200

    rotated = client.post(f"/_sandbox/apps/{app['id']}/rotate-token")
    assert rotated.status_code == 200
    assert rotated.json()["access_token"] != app["access_token"]
    assert client.get("/v25.0/WABA_LOCAL", headers=old_headers).status_code == 401
    new_headers = {"Authorization": f"Bearer {rotated.json()['access_token']}"}
    assert client.get("/v25.0/WABA_LOCAL", headers=new_headers).status_code == 200


def test_business_waba_sender_hierarchy_and_updates(client: TestClient, headers: dict[str, str]) -> None:
    created = client.post("/_sandbox/businesses", json={
        "name": "Acme", "verified_name": "Acme Support", "display_phone_number": "+1 555 900 1000",
    })
    assert created.status_code == 201
    ids = created.json()

    wabas = client.get(f"/v25.0/{ids['business_id']}/owned_whatsapp_business_accounts", headers=headers).json()["data"]
    assert wabas == [{"id": ids["waba_id"], "name": "Acme"}]
    numbers = client.get(f"/v25.0/{ids['waba_id']}/phone_numbers", headers=headers).json()["data"]
    assert numbers[0]["id"] == ids["phone_number_id"]
    assert numbers[0]["display_phone_number"] == "15559001000"

    second = client.post(f"/_sandbox/businesses/{ids['waba_id']}/phone-numbers", json={
        "verified_name": "Acme Sales", "display_phone_number": "15559001001",
    })
    assert second.status_code == 201
    numbers = client.get(f"/v25.0/{ids['waba_id']}/phone_numbers", headers=headers).json()["data"]
    assert {item["verified_name"] for item in numbers} == {"Acme Support", "Acme Sales"}

    assert client.patch(f"/_sandbox/businesses/{ids['waba_id']}", json={"name": "Acme Ltd"}).json()["name"] == "Acme Ltd"
    edited = client.patch(f"/_sandbox/phone-numbers/{second.json()['id']}", json={
        "verified_name": "Acme Revenue", "display_phone_number": "15559001002",
    }).json()
    assert edited["verified_name"] == "Acme Revenue"
    assert edited["display_phone_number"] == "15559001002"


@pytest.mark.parametrize(
    ("path", "body", "status"),
    [
        ("/_sandbox/businesses", {"name": "Missing number"}, 400),
        ("/_sandbox/businesses/UNKNOWN/phone-numbers", {"display_phone_number": "15559999999"}, 404),
        ("/_sandbox/businesses/WABA_LOCAL/phone-numbers", {"verified_name": "Missing number"}, 400),
        ("/_sandbox/phone-numbers/UNKNOWN", {"verified_name": "None"}, 404),
        ("/_sandbox/businesses/UNKNOWN", {"name": "None"}, 404),
    ],
)
def test_resource_creation_errors_are_explicit(client: TestClient, path: str, body: dict[str, str], status: int) -> None:
    method = client.patch if path == "/_sandbox/phone-numbers/UNKNOWN" or path == "/_sandbox/businesses/UNKNOWN" else client.post
    response = method(path, json=body)
    assert response.status_code == status
    assert response.json()["error"]


def test_business_profile_round_trip_and_unknown_phone(client: TestClient, headers: dict[str, str]) -> None:
    update = client.post("/v25.0/PHONE_LOCAL/whatsapp_business_profile", headers=headers, json={
        "messaging_product": "whatsapp", "about": "Local support", "email": "support@example.test",
    })
    assert update.json() == {"success": True}
    profile = client.get("/v25.0/PHONE_LOCAL/whatsapp_business_profile", headers=headers).json()["data"][0]
    assert profile == {"about": "Local support", "email": "support@example.test"}
    assert client.get("/v25.0/UNKNOWN/whatsapp_business_profile", headers=headers).status_code == 404
    assert client.post(
        "/v25.0/UNKNOWN/whatsapp_business_profile",
        headers=headers,
        json={"messaging_product": "whatsapp", "about": "Should fail"},
    ).status_code == 404


def test_simulated_customer_crud(client: TestClient) -> None:
    created = client.post("/_sandbox/phones", json={"wa_id": "+91 99999-99999", "display_name": "Alice"})
    assert created.status_code == 201
    assert created.json()["wa_id"] == "919999999999"
    edited = client.patch("/_sandbox/phones/919999999999", json={"display_name": "Alice B", "online": False, "blocked": True})
    assert edited.json()["display_name"] == "Alice B"
    assert edited.json()["online"] == 0
    assert edited.json()["blocked"] == 1
    assert client.delete("/_sandbox/phones/919999999999").json() == {"success": True}
    assert client.delete("/_sandbox/phones/919999999999").status_code == 404
