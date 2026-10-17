"""Template sends Meta rejects beyond a wrong body parameter count.

Each case here once passed the sandbox and failed only in production: a media
header sent without its media, a dynamic URL button without its suffix, and an
empty text value.
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def _template(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/v25.0/WABA_LOCAL/message_templates", headers=headers, json={
        "name": "parity_trip_ready",
        "language": "en_US",
        "category": "UTILITY",
        "_sandbox_auto_approve": True,
        "components": [
            {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["https://example.test/a.png"]}},
            {"type": "BODY", "text": "Hello {{1}}, your trip is ready to view.", "example": {"body_text": [["Alex"]]}},
            {"type": "BUTTONS", "buttons": [
                {"type": "QUICK_REPLY", "text": "Thanks"},
                {"type": "URL", "text": "View trip", "url": "https://example.test/trip/{{1}}", "example": ["abc"]},
            ]},
        ],
    })
    assert response.status_code == 200, response.text


def _send(client: TestClient, headers: dict[str, str], components: list[dict[str, Any]]):
    return client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json={
        "messaging_product": "whatsapp", "to": "15550002001", "type": "template",
        "template": {"name": "parity_trip_ready", "language": {"code": "en_US"}, "components": components},
    })


HEADER = {"type": "header", "parameters": [{"type": "image", "image": {"link": "https://example.test/a.png"}}]}
BODY = {"type": "body", "parameters": [{"type": "text", "text": "Alex"}]}
BUTTON = {"type": "button", "sub_type": "url", "index": "1", "parameters": [{"type": "text", "text": "abc"}]}


def test_complete_send_is_accepted(client: TestClient, headers: dict[str, str]) -> None:
    _template(client, headers)
    assert _send(client, headers, [HEADER, BODY, BUTTON]).status_code == 200


def test_media_header_without_media_is_rejected(client: TestClient, headers: dict[str, str]) -> None:
    _template(client, headers)
    response = _send(client, headers, [BODY, BUTTON])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 132012


def test_dynamic_url_button_without_suffix_is_rejected(client: TestClient, headers: dict[str, str]) -> None:
    _template(client, headers)
    response = _send(client, headers, [HEADER, BODY])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 131008


def test_button_index_that_does_not_exist_is_rejected(client: TestClient, headers: dict[str, str]) -> None:
    _template(client, headers)
    stray = {**BUTTON, "index": "5"}
    response = _send(client, headers, [HEADER, BODY, BUTTON, stray])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 131009


def test_empty_text_parameter_is_rejected(client: TestClient, headers: dict[str, str]) -> None:
    _template(client, headers)
    empty = {"type": "body", "parameters": [{"type": "text", "text": "  "}]}
    response = _send(client, headers, [HEADER, empty, BUTTON])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 131008
