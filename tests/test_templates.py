from __future__ import annotations

import pytest
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
        "components": [{
            "type": "BODY",
            "text": "Order {{1}} is ready for {{2}}.",
            "example": {"body_text": [["#123", "Alice"]]},
        }],
        "_sandbox_auto_approve": True,
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
        "components": [{"type": "BODY", "text": "Pending"}],
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
        "components": [{
            "type": "BODY", "text": "Duplicate value {{1}}.",
            "example": {"body_text": [["sample"]]},
        }],
    }
    assert client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json=duplicate).json()["error"]["code"] == 100
    missing = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json={"name": "incomplete"})
    assert missing.json()["error"]["code"] == 100
    unknown_waba = client.post("/v25.0/UNKNOWN/message_templates", headers=headers, json=duplicate)
    assert unknown_waba.status_code == 404
    assert unknown_waba.json()["error"]["code"] == 100


def test_templates_are_waba_scoped_when_sending(client: TestClient, headers: dict[str, str]) -> None:
    other = client.post("/_sandbox/businesses", json={
        "name": "Other", "verified_name": "Other Sender", "display_phone_number": "15550009999",
    }).json()
    created = client.post(f"/v25.0/{other['waba_id']}/message_templates", headers=headers, json={
        "name": "other_only", "language": "en_US", "category": "UTILITY",
        "components": [{
            "type": "BODY", "text": "Other value {{1}}.",
            "example": {"body_text": [["Tester"]]},
        }],
        "_sandbox_auto_approve": True,
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


def test_template_creation_defaults_to_meta_pending_response(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json={
        "name": "meta_pending", "language": "en_US", "category": "UTILITY",
        "components": [{"type": "BODY", "text": "A plain notification."}],
    })

    assert response.status_code == 200
    assert response.json().keys() == {"id", "status", "category"}
    assert response.json()["status"] == "PENDING"


def test_body_parameters_require_meta_nested_examples(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json={
        "name": "bad_examples", "language": "en_US", "category": "UTILITY",
        "components": [{
            "type": "BODY", "text": "Hello {{1}}, order {{2}} is ready.",
            "example": {"body_text": ["Alex", "A123"]},
        }],
    })

    error = response.json()["error"]
    assert response.status_code == 400
    assert error["code"] == 100
    assert error["error_subcode"] == 2388043
    assert error["is_transient"] is False
    assert error["error_user_msg"]


def test_named_parameters_and_dynamic_url_are_accepted(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json={
        "name": "named_order", "language": "en_US", "category": "UTILITY",
        "parameter_format": "NAMED",
        "components": [
            {
                "type": "BODY",
                "text": "Hello {{customer_name}}, order {{order_number}} is ready.",
                "example": {"body_text_named_params": [
                    {"param_name": "customer_name", "example": "Alex"},
                    {"param_name": "order_number", "example": "A123"},
                ]},
            },
            {"type": "BUTTONS", "buttons": [{
                "type": "URL", "text": "View order",
                "url": "https://example.test/orders/{{1}}", "example": ["A123"],
            }]},
        ],
    })

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"


def test_authentication_template_uses_meta_controlled_body(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json={
        "name": "login_code", "language": "en_US", "category": "AUTHENTICATION",
        "components": [
            {"type": "BODY", "add_security_recommendation": True},
            {"type": "FOOTER", "code_expiration_minutes": 10},
            {"type": "BUTTONS", "buttons": [
                {"type": "OTP", "otp_type": "COPY_CODE", "text": "Copy Code"},
            ]},
        ],
    })

    assert response.status_code == 200
    assert response.json()["category"] == "AUTHENTICATION"


def test_media_header_requires_uploaded_handle(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json={
        "name": "image_offer", "language": "en_US", "category": "MARKETING",
        "components": [
            {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["4::HANDLE"]}},
            {"type": "BODY", "text": "See our latest offer."},
        ],
    })

    assert response.status_code == 200


def _handoff_template(url: str, **button: object) -> dict[str, object]:
    return {
        "name": "ops_handoff", "language": "en_US", "category": "UTILITY",
        "components": [
            {"type": "BODY", "text": "Hi {{1}}, our operations team looks after your trip.",
             "example": {"body_text": [["Alex"]]}},
            {"type": "BUTTONS", "buttons": [
                {"type": "URL", "text": "Chat with Operations", "url": url, **button},
            ]},
        ],
    }


@pytest.mark.parametrize("url", [
    "https://wa.me/{{1}}",
    "https://api.whatsapp.com/send?phone={{1}}",
    "http://WA.ME/{{1}}",
])
def test_whatsapp_link_url_buttons_are_rejected(
    client: TestClient, headers: dict[str, str], url: str,
) -> None:
    response = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers,
                           json=_handoff_template(url, example=["919876543210"]))

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == 100
    assert error["error_subcode"] == 2388081
    assert error["type"] == "OAuthException"
    assert error["is_transient"] is False
    assert error["error_user_title"] == "Error while adding button URL"
    assert error["error_user_msg"] == "Direct links to WhatsApp aren't allowed for buttons."


@pytest.mark.parametrize("url", [
    "https://travel-xs.test/ops/{{1}}",
    "https://example.test/guides/wa.me-explained",
])
def test_non_whatsapp_url_buttons_are_still_accepted(
    client: TestClient, headers: dict[str, str], url: str,
) -> None:
    response = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers,
                           json=_handoff_template(url, example=["919876543210"]))

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"


def test_missing_button_example_is_reported_before_the_whatsapp_link(
    client: TestClient, headers: dict[str, str],
) -> None:
    """Meta validates the absent example first; verified against the live Graph API."""
    response = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers,
                           json=_handoff_template("https://wa.me/{{1}}"))

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["error_subcode"] == 2388043
    assert error["error_user_msg"] == "component of type BUTTONS is missing expected field(s) (example)"
