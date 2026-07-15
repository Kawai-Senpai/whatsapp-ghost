from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import re
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .db import Store


SUPPORTED_MESSAGE_TYPES = {
    "text", "reaction", "image", "audio", "video", "document", "sticker",
    "location", "contacts", "interactive", "template",
}


def normalize_phone(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


class Engine:
    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings
        self.listeners: dict[str, set[Any]] = {}

    def phone(self, phone_id: str):
        return self.store.one("SELECT * FROM phone_numbers WHERE id=?", (phone_id,))

    def user(self, wa_id: str):
        return self.store.one("SELECT * FROM simulated_users WHERE wa_id=?", (normalize_phone(wa_id),))

    def conversation(self, phone_id: str, wa_id: str) -> str:
        wa_id = normalize_phone(wa_id)
        row = self.store.one("SELECT id FROM conversations WHERE phone_number_id=? AND user_wa_id=?", (phone_id, wa_id))
        if row:
            return row["id"]
        conversation_id = "conv_" + uuid.uuid4().hex
        now = self.store.now().isoformat()
        self.store.execute("INSERT INTO conversations(id,phone_number_id,user_wa_id,created_at) VALUES(?,?,?,?)", (conversation_id, phone_id, wa_id, now))
        return conversation_id

    def validate_outbound(self, phone_id: str, body: dict[str, Any]) -> tuple[int | None, str | None]:
        if not self.phone(phone_id):
            return 100, f"Unsupported post request. Phone number ID {phone_id} does not exist."
        if body.get("messaging_product") != "whatsapp":
            return 131009, "messaging_product must be whatsapp."
        to = normalize_phone(str(body.get("to", "")))
        if not to:
            return 131008, "Parameter to is required."
        if self.settings.mode == "strict" and not self.user(to):
            return 131026, "Recipient is not a registered simulated user. Create it under /_sandbox/phones."
        message_type = body.get("type")
        if not message_type and self.settings.mode == "loose":
            matches = [item for item in SUPPORTED_MESSAGE_TYPES if item in body]
            message_type = matches[0] if len(matches) == 1 else None
            body["type"] = message_type
        if message_type not in SUPPORTED_MESSAGE_TYPES:
            return 131051, f"Message type {message_type!r} is unsupported."
        if message_type != "template" and message_type not in body:
            return 131008, f"Parameter {message_type} is required."
        if message_type == "text" and not body.get("text", {}).get("body"):
            return 131008, "Parameter text.body is required."
        if message_type in {"image", "audio", "video", "document", "sticker"}:
            media = body.get(message_type, {})
            if not media.get("id") and not media.get("link"):
                return 131008, f"Parameter {message_type}.id or {message_type}.link is required."
            if media.get("id"):
                stored_media = self.store.one("SELECT phone_number_id FROM media WHERE id=?", (media["id"],))
                if not stored_media or stored_media["phone_number_id"] != phone_id:
                    return 131052, "The referenced media ID does not exist for this phone number."
        conversation = self.store.one("SELECT service_window_expires_at FROM conversations WHERE phone_number_id=? AND user_wa_id=?", (phone_id, to))
        window_open = bool(conversation and conversation["service_window_expires_at"] and self.store.now().isoformat() < conversation["service_window_expires_at"])
        if message_type != "template" and not window_open and self.settings.mode != "loose":
            return 131047, "More than 24 hours have passed since the recipient last replied. Use an approved template."
        if message_type == "template":
            template = body.get("template", {})
            language = template.get("language", {}).get("code")
            phone = self.phone(phone_id)
            row = self.store.one(
                "SELECT * FROM templates WHERE waba_id=? AND name=? AND language=?",
                (phone["waba_id"], template.get("name"), language),
            )
            if not row:
                return 132001, "Template name or language does not exist."
            if row["status"] == "PAUSED":
                return 132015, "The template is paused."
            if row["status"] != "APPROVED":
                return 132016, f"The template is {row['status'].lower()}."
            definitions = json.loads(row["components_json"])
            body_definition = next((item for item in definitions if item.get("type", "").upper() == "BODY"), {})
            expected = len(set(re.findall(r"\{\{(\d+)\}\}", body_definition.get("text", ""))))
            sent_body = next((item for item in template.get("components", []) if item.get("type", "").lower() == "body"), {})
            actual = len(sent_body.get("parameters", []))
            if expected != actual:
                return 132000, f"Template body expects {expected} parameter(s), but {actual} were supplied."
        return None, None

    async def send_outbound(self, version: str, phone_id: str, body: dict[str, Any]) -> dict[str, Any]:
        to = normalize_phone(str(body["to"]))
        conversation_id = self.conversation(phone_id, to)
        message_id = "wamid." + secrets.token_urlsafe(24)
        now = self.store.now().isoformat()
        self.store.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (message_id, conversation_id, "outbound", phone_id, to, body["type"], json.dumps(body), version, "accepted", now, now),
        )
        asyncio.create_task(self._deliver_later(message_id))
        return {"messaging_product": "whatsapp", "contacts": [{"input": str(body["to"]), "wa_id": to}], "messages": [{"id": message_id}]}

    async def _deliver_later(self, message_id: str) -> None:
        await asyncio.sleep(self.settings.status_delay_seconds)
        await self.set_status(message_id, "sent")
        await asyncio.sleep(self.settings.status_delay_seconds)
        message = self.store.one("SELECT * FROM messages WHERE id=?", (message_id,))
        if not message:
            return
        user = self.user(message["recipient_id"])
        if user and (not user["online"] or user["blocked"]):
            await self.set_status(message_id, "failed", 131026)
        else:
            await self.set_status(message_id, "delivered")
            await self.broadcast(message["recipient_id"], {"event": "message", "message": dict(message)})

    async def set_status(self, message_id: str, status: str, failure_code: int | None = None) -> None:
        now = self.store.now()
        self.store.execute("UPDATE messages SET status=?,updated_at=?,failure_code=? WHERE id=?", (status, now.isoformat(), failure_code, message_id))
        message = self.store.one("SELECT * FROM messages WHERE id=?", (message_id,))
        if not message:
            return
        status_payload: dict[str, Any] = {"id": message_id, "status": status, "timestamp": str(int(now.timestamp())), "recipient_id": message["recipient_id"]}
        if failure_code:
            status_payload["errors"] = [{"code": failure_code, "title": "Message undeliverable", "message": "Message undeliverable", "error_data": {"details": "Recipient is unavailable."}}]
        event_id = "evt_" + uuid.uuid4().hex
        self.store.execute("INSERT INTO message_status_events VALUES(?,?,?,?,?)", (event_id, message_id, status, now.isoformat(), json.dumps(status_payload)))
        await self.queue_webhook(message["sender_id"], "messages", statuses=[status_payload])
        if message["direction"] == "outbound":
            await self.broadcast(message["recipient_id"], {"event": "status", "message_id": message_id, "status": status})

    async def receive_inbound(self, phone_id: str, wa_id: str, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        wa_id = normalize_phone(wa_id)
        phone = self.phone(phone_id)
        if not phone or not self.user(wa_id):
            raise ValueError("Unknown business phone or simulated user")
        conversation_id = self.conversation(phone_id, wa_id)
        message_id = "wamid." + secrets.token_urlsafe(24)
        now = self.store.now()
        expires = now + timedelta(hours=24)
        normalized = {"from": wa_id, "id": message_id, "timestamp": str(int(now.timestamp())), "type": message_type, message_type: payload}
        self.store.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (message_id, conversation_id, "inbound", wa_id, phone_id, message_type, json.dumps(normalized), "v25.0", "delivered", now.isoformat(), now.isoformat()),
        )
        self.store.execute("UPDATE conversations SET last_user_message_at=?,service_window_expires_at=? WHERE id=?", (now.isoformat(), expires.isoformat(), conversation_id))
        await self.queue_webhook(phone_id, "messages", contacts=[{"profile": {"name": self.user(wa_id)["display_name"]}, "wa_id": wa_id}], messages=[normalized])
        await self.broadcast(wa_id, {"event": "message", "message": normalized})
        return normalized

    def webhook_envelope(self, phone_id: str, field: str, **content: Any) -> dict[str, Any]:
        phone = self.phone(phone_id)
        value = {"messaging_product": "whatsapp", "metadata": {"display_phone_number": phone["display_phone_number"], "phone_number_id": phone_id}}
        value.update({key: val for key, val in content.items() if val})
        return {"object": "whatsapp_business_account", "entry": [{"id": phone["waba_id"], "changes": [{"value": value, "field": field}]}]}

    async def queue_webhook(self, phone_id: str, field: str, **content: Any) -> None:
        phone = self.phone(phone_id)
        if not phone:
            return
        body = json.dumps(self.webhook_envelope(phone_id, field, **content), separators=(",", ":")).encode()
        subscriptions = self.store.all("SELECT * FROM webhook_subscriptions WHERE waba_id=? AND active=1", (phone["waba_id"],))
        if not subscriptions:
            signature = "sha256=" + hmac.new(self.settings.app_secret.encode(), body, hashlib.sha256).hexdigest()
            self.store.execute(
                "INSERT INTO webhook_deliveries(id,event_type,destination_url,request_body,signature,status,attempt_count,last_status_code,last_error,created_at,delivered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("whd_" + uuid.uuid4().hex, field, None, body, signature, "unrouted", 0, None, None, self.store.now().isoformat(), None),
            )
        for subscription in subscriptions:
            signing_secret = subscription["app_secret"] or self.settings.app_secret
            signature = "sha256=" + hmac.new(signing_secret.encode(), body, hashlib.sha256).hexdigest()
            delivery_id = "whd_" + uuid.uuid4().hex
            self.store.execute(
                "INSERT INTO webhook_deliveries(id,event_type,destination_url,request_body,signature,status,attempt_count,last_status_code,last_error,created_at,delivered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (delivery_id, field, subscription["callback_url"], body, signature, "pending", 0, None, None, self.store.now().isoformat(), None),
            )
            asyncio.create_task(self.deliver_webhook(delivery_id))

    async def deliver_webhook(self, delivery_id: str) -> None:
        delivery = self.store.one("SELECT * FROM webhook_deliveries WHERE id=?", (delivery_id,))
        if not delivery or not delivery["destination_url"]:
            return
        attempts = delivery["attempt_count"] + 1
        attempt_id = "wha_" + uuid.uuid4().hex
        requested_at = self.store.now().isoformat()
        self.store.execute(
            "INSERT INTO webhook_attempts(id,delivery_id,attempt_number,requested_at) VALUES(?,?,?,?)",
            (attempt_id, delivery_id, attempts, requested_at),
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(delivery["destination_url"], content=delivery["request_body"], headers={"Content-Type": "application/json", "X-Hub-Signature-256": delivery["signature"]})
            status = "delivered" if 200 <= response.status_code < 300 else "failed"
            completed_at = self.store.now().isoformat()
            self.store.execute("UPDATE webhook_deliveries SET status=?,attempt_count=?,last_status_code=?,last_response_body=?,last_error=?,delivered_at=? WHERE id=?", (status, attempts, response.status_code, response.content, None if status == "delivered" else response.text[:1000], completed_at if status == "delivered" else None, delivery_id))
            self.store.execute(
                "UPDATE webhook_attempts SET completed_at=?,status_code=?,response_body=? WHERE id=?",
                (completed_at, response.status_code, response.content, attempt_id),
            )
        except Exception as exc:
            self.store.execute("UPDATE webhook_deliveries SET status='failed',attempt_count=?,last_error=? WHERE id=?", (attempts, str(exc), delivery_id))
            self.store.execute(
                "UPDATE webhook_attempts SET completed_at=?,error=? WHERE id=?",
                (self.store.now().isoformat(), str(exc), attempt_id),
            )

    async def broadcast(self, wa_id: str, payload: dict[str, Any]) -> None:
        dead = []
        for socket in self.listeners.get(wa_id, set()):
            try:
                await socket.send_json(payload)
            except Exception:
                dead.append(socket)
        for socket in dead:
            self.listeners.get(wa_id, set()).discard(socket)

    def save_media(self, phone_id: str, content: bytes, mime_type: str, filename: str | None) -> str:
        media_id = str(secrets.randbelow(9_000_000_000_000_000) + 1_000_000_000_000_000)
        digest = hashlib.sha256(content).hexdigest()
        path: Path = self.settings.media_dir / media_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.store.execute("INSERT INTO media VALUES(?,?,?,?,?,?,?,?)", (media_id, phone_id, mime_type, filename, digest, len(content), str(path), self.store.now().isoformat()))
        return media_id
