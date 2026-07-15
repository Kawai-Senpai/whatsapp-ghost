from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import open_window


PNG = b"\x89PNG\r\n\x1a\nwhatsapp-ghost-image"


def upload(client: TestClient, headers: dict[str, str], data: bytes = PNG, mime: str = "image/png", name: str = "photo.png"):
    return client.post(
        "/v25.0/PHONE_LOCAL/media",
        headers=headers,
        data={"messaging_product": "whatsapp"},
        files={"file": (name, data, mime)},
    )


def test_media_upload_persists_bytes_and_returns_graph_metadata(
    client: TestClient, headers: dict[str, str], settings
) -> None:
    response = upload(client, headers)
    assert response.status_code == 200
    media_id = response.json()["id"]
    stored = settings.media_dir / media_id
    assert stored.is_file()
    assert stored.read_bytes() == PNG

    metadata = client.get(f"/v25.0/{media_id}", headers=headers).json()
    assert metadata == {
        "url": f"http://testserver/_sandbox/media/{media_id}",
        "mime_type": "image/png",
        "sha256": hashlib.sha256(PNG).hexdigest(),
        "file_size": len(PNG),
        "id": media_id,
        "messaging_product": "whatsapp",
    }


def test_media_download_requires_auth_and_supports_browser_preview_token(client: TestClient, headers: dict[str, str]) -> None:
    media_id = upload(client, headers).json()["id"]
    assert client.get(f"/_sandbox/media/{media_id}").status_code == 401
    assert client.get(f"/_sandbox/media/{media_id}", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(f"/_sandbox/media/{media_id}", headers=headers).content == PNG
    preview = client.get(f"/_sandbox/media/{media_id}", params={"access_token": "token"})
    assert preview.status_code == 200
    assert preview.content == PNG
    assert preview.headers["content-type"] == "image/png"

    app = client.post("/_sandbox/apps", json={"name": "Media consumer"}).json()
    app_token = app["access_token"]
    assert client.get(
        f"/_sandbox/media/{media_id}", headers={"Authorization": f"Bearer {app_token}"}
    ).content == PNG
    assert client.get(f"/_sandbox/media/{media_id}", params={"access_token": app_token}).content == PNG


def test_browser_phone_media_journey_uploads_then_sends_media_id(
    client: TestClient, headers: dict[str, str], settings
) -> None:
    media_id = upload(client, headers).json()["id"]
    metadata = client.get(f"/v25.0/{media_id}", headers=headers).json()
    inbound = client.post("/_sandbox/phones/15550002001/messages", json={
        "type": "image",
        "phone_number_id": "PHONE_LOCAL",
        "image": {
            "id": media_id,
            "mime_type": metadata["mime_type"],
            "sha256": metadata["sha256"],
            "caption": "Proof of delivery",
        },
    })
    assert inbound.status_code == 201
    assert inbound.json()["image"]["id"] == media_id
    assert "link" not in inbound.json()["image"]
    assert (settings.media_dir / media_id).read_bytes() == PNG

    history = client.get("/_sandbox/messages", params={
        "wa_id": "15550002001", "phone_number_id": "PHONE_LOCAL",
    }).json()["data"]
    assert history[0]["message_type"] == "image"
    assert history[0]["payload"]["image"]["id"] == media_id
    assert history[0]["payload"]["image"]["caption"] == "Proof of delivery"

    webhooks = client.get("/_sandbox/webhooks").json()["data"]
    message = webhooks[0]["request_body"]["entry"][0]["changes"][0]["value"]["messages"][0]
    assert message["image"]["id"] == media_id
    assert message["image"]["sha256"] == hashlib.sha256(PNG).hexdigest()


@pytest.mark.parametrize(
    ("product", "data", "mime", "expected_code"),
    [
        ("invalid", PNG, "image/png", 131009),
        ("whatsapp", b"", "image/png", 131053),
        ("whatsapp", b"gif", "image/gif", 131053),
    ],
)
def test_media_upload_validation(
    client: TestClient,
    headers: dict[str, str],
    product: str,
    data: bytes,
    mime: str,
    expected_code: int,
) -> None:
    response = client.post(
        "/v25.0/PHONE_LOCAL/media",
        headers=headers,
        data={"messaging_product": product},
        files={"file": ("file.bin", data, mime)},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == expected_code


def test_media_size_limits_are_enforced(client: TestClient, headers: dict[str, str]) -> None:
    too_large_webp = b"x" * 500_001
    response = upload(client, headers, too_large_webp, "image/webp", "large.webp")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 131053


def test_outbound_media_by_id_and_external_link(client: TestClient, headers: dict[str, str]) -> None:
    open_window(client)
    media_id = upload(client, headers).json()["id"]
    by_id = {
        "messaging_product": "whatsapp", "to": "15550002001", "type": "image",
        "image": {"id": media_id, "caption": "Stored"},
    }
    by_link = {
        "messaging_product": "whatsapp", "to": "15550002001", "type": "image",
        "image": {"link": "https://example.test/photo.png", "caption": "Remote"},
    }
    assert client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=by_id).status_code == 200
    assert client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=by_link).status_code == 200


def test_stored_media_ids_cannot_cross_sender_ownership(client: TestClient, headers: dict[str, str]) -> None:
    media_id = upload(client, headers).json()["id"]
    second = client.post("/_sandbox/businesses/WABA_LOCAL/phone-numbers", json={
        "verified_name": "Second", "display_phone_number": "15550001009",
    }).json()["id"]
    open_window(client, phone_id=second)
    payload = {
        "messaging_product": "whatsapp", "to": "15550002001", "type": "image", "image": {"id": media_id},
    }
    response = client.post(f"/v25.0/{second}/messages", headers=headers, json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 131052
    inbound = client.post("/_sandbox/phones/15550002001/messages", json={
        "type": "image", "phone_number_id": second, "image": {"id": media_id},
    })
    assert inbound.status_code == 400


def test_deleting_media_removes_database_record_and_file(
    client: TestClient, headers: dict[str, str], settings
) -> None:
    media_id = upload(client, headers).json()["id"]
    path = settings.media_dir / media_id
    assert path.exists()
    assert client.delete(f"/v25.0/{media_id}", headers=headers).json() == {"success": True}
    assert not path.exists()
    assert client.get(f"/v25.0/{media_id}", headers=headers).status_code == 404
    assert client.delete(f"/v25.0/{media_id}", headers=headers).status_code == 404


def test_media_directory_is_absolute_and_inside_configured_data_dir(settings) -> None:
    assert isinstance(settings.media_dir, Path)
    assert settings.media_dir == settings.data_dir / "media"
