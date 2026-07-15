from __future__ import annotations

from fastapi.testclient import TestClient


def template_payload(name: str, language: str = "en_US", parameters: list[str] | None = None) -> dict:
    components = []
    if parameters is not None:
        components = [{
            "type": "body",
            "parameters": [{"type": "text", "text": value} for value in parameters],
        }]
    return {
        "messaging_product": "whatsapp",
        "to": "15550002001",
        "type": "template",
        "template": {"name": name, "language": {"code": language}, "components": components},
    }


def test_seeded_template_can_start_conversation_outside_window(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post(
        "/v25.0/PHONE_LOCAL/messages",
        headers=headers,
        json=template_payload("hello_world", parameters=["Tester"]),
    )
    assert response.status_code == 200
    assert response.json()["messages"][0]["id"].startswith("wamid.")


def test_template_create_list_get_and_delete(client: TestClient, headers: dict[str, str]) -> None:
    body = {
        "name": "order_ready",
        "language": "en_US",
        "category": "utility",
        "components": [{"type": "BODY", "text": "Order {{1}} is ready for {{2}}."}],
    }
    created = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json=body)
    assert created.status_code == 200
    assert created.json()["status"] == "APPROVED"
    template_id = created.json()["id"]

    listed = client.get("/v25.0/WABA_LOCAL/message_templates", headers=headers, params={"name": "order_ready"}).json()["data"]
    assert len(listed) == 1
    assert listed[0]["components"] == body["components"]
    fetched = client.get(f"/v25.0/{template_id}", headers=headers).json()
    assert fetched["name"] == "order_ready"
    assert fetched["components"] == body["components"]

    sent = client.post(
        "/v25.0/PHONE_LOCAL/messages",
        headers=headers,
        json=template_payload("order_ready", parameters=["#123", "Alice"]),
    )
    assert sent.status_code == 200
    assert client.delete(
        "/v25.0/WABA_LOCAL/message_templates", headers=headers, params={"name": "order_ready"}
    ).json() == {"success": True}
    assert client.get("/v25.0/WABA_LOCAL/message_templates", headers=headers, params={"name": "order_ready"}).json()["data"] == []


def test_pending_template_cannot_send(client: TestClient, headers: dict[str, str]) -> None:
    created = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json={
        "name": "pending_notice", "language": "en_US", "category": "UTILITY",
        "components": [{"type": "BODY", "text": "Pending"}], "_sandbox_auto_approve": False,
    })
    assert created.json()["status"] == "PENDING"
    response = client.post(
        "/v25.0/PHONE_LOCAL/messages", headers=headers, json=template_payload("pending_notice", parameters=[])
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 132016


def test_template_parameter_count_language_and_name_validation(client: TestClient, headers: dict[str, str]) -> None:
    wrong_count = client.post(
        "/v25.0/PHONE_LOCAL/messages", headers=headers, json=template_payload("hello_world", parameters=[])
    )
    assert wrong_count.json()["error"]["code"] == 132000
    wrong_language = client.post(
        "/v25.0/PHONE_LOCAL/messages", headers=headers, json=template_payload("hello_world", "fr", ["Tester"])
    )
    assert wrong_language.json()["error"]["code"] == 132001
    wrong_name = client.post(
        "/v25.0/PHONE_LOCAL/messages", headers=headers, json=template_payload("missing", parameters=[])
    )
    assert wrong_name.json()["error"]["code"] == 132001


def test_duplicate_and_missing_template_fields_return_graph_errors(client: TestClient, headers: dict[str, str]) -> None:
    duplicate = {
        "name": "hello_world", "language": "en_US", "category": "UTILITY",
        "components": [{"type": "BODY", "text": "Duplicate {{1}}"}],
    }
    assert client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json=duplicate).json()["error"]["code"] == 100
    missing = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json={"name": "incomplete"})
    assert missing.json()["error"]["code"] == 131008
    unknown_waba = client.post("/v25.0/UNKNOWN/message_templates", headers=headers, json=duplicate)
    assert unknown_waba.status_code == 404
    assert unknown_waba.json()["error"]["code"] == 100


def test_templates_are_waba_scoped_when_sending(client: TestClient, headers: dict[str, str]) -> None:
    other = client.post("/_sandbox/businesses", json={
        "name": "Other", "verified_name": "Other Sender", "display_phone_number": "15550009999",
    }).json()
    created = client.post(f"/v25.0/{other['waba_id']}/message_templates", headers=headers, json={
        "name": "other_only", "language": "en_US", "category": "UTILITY",
        "components": [{"type": "BODY", "text": "Other {{1}}"}],
    })
    assert created.status_code == 200

    wrong_sender = client.post(
        "/v25.0/PHONE_LOCAL/messages", headers=headers, json=template_payload("other_only", parameters=["Tester"])
    )
    assert wrong_sender.status_code == 400
    assert wrong_sender.json()["error"]["code"] == 132001
    correct_sender = client.post(
        f"/v25.0/{other['phone_number_id']}/messages",
        headers=headers,
        json=template_payload("other_only", parameters=["Tester"]),
    )
    assert correct_sender.status_code == 200
