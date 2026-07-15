from __future__ import annotations

import json
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, File, Form, Header, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .clock import parse_datetime, parse_duration
from .config import Settings
from .db import Store
from .engine import Engine, normalize_phone
from .errors import graph_error
from .web_console import CONSOLE_HTML, PHONE_HTML, WEB_DIR


def rows(items: list[Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in items]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.database_path)
    engine = Engine(store, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        store.initialize()
        yield

    app = FastAPI(
        title="WhatsApp Ghost",
        version="0.1.0",
        description="Contract-compatible local WhatsApp Cloud API sandbox. Sandbox-only controls are under /_sandbox.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        if request.url.path == "/" or request.url.path.startswith(("/_sandbox", "/webhook", "/console", "/phone", "/static", "/docs", "/openapi.json", "/redoc")):
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ").strip()
        known_token = token == settings.access_token or bool(store.one("SELECT id FROM developer_apps WHERE access_token=?", (token,)))
        if not known_token:
            return graph_error(190, "The access token is invalid or missing.", status_code=401)
        return await call_next(request)

    @app.get("/_sandbox/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "mode": settings.mode, "now": store.now().isoformat(), "database": str(settings.database_path)}

    @app.get("/_sandbox/config")
    def public_config() -> dict[str, Any]:
        return {"base_url": settings.base_url, "mode": settings.mode, "access_token": settings.access_token, "demo": {"business_id": "BUSINESS_LOCAL", "waba_id": "WABA_LOCAL", "phone_number_id": "PHONE_LOCAL", "business_phone": "15550001000", "customer": "15550002001"}}

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/console")

    @app.get("/console", response_class=HTMLResponse, include_in_schema=False)
    def console_page():
        return HTMLResponse(CONSOLE_HTML)

    @app.get("/phone", response_class=HTMLResponse, include_in_schema=False)
    def phone_page():
        return HTMLResponse(PHONE_HTML)

    @app.get("/_sandbox/apps")
    def sandbox_apps():
        return {"data": rows(store.all("SELECT * FROM developer_apps ORDER BY created_at"))}

    @app.post("/_sandbox/apps", status_code=201)
    def sandbox_app_create(body: dict[str, Any] = Body(...)):
        app_id = "APP_" + secrets.token_hex(5).upper()
        app_secret = secrets.token_hex(20)
        token = "EAA_LOCAL_" + secrets.token_urlsafe(30)
        store.execute("INSERT INTO developer_apps VALUES(?,?,?,?,?)", (app_id, body.get("name", "Untitled App"), app_secret, token, store.now().isoformat()))
        return dict(store.one("SELECT * FROM developer_apps WHERE id=?", (app_id,)))

    @app.post("/_sandbox/apps/{app_id}/rotate-token")
    def sandbox_rotate_token(app_id: str):
        if not store.one("SELECT id FROM developer_apps WHERE id=?", (app_id,)):
            return JSONResponse({"error": "app not found"}, status_code=404)
        token = "EAA_LOCAL_" + secrets.token_urlsafe(30)
        store.execute("UPDATE developer_apps SET access_token=? WHERE id=?", (token, app_id))
        return {"access_token": token}

    @app.get("/_sandbox/businesses")
    def sandbox_businesses():
        result = []
        for account in store.all("SELECT * FROM business_accounts ORDER BY created_at"):
            item = dict(account)
            item["phone_numbers"] = rows(store.all("SELECT * FROM phone_numbers WHERE waba_id=?", (account["id"],)))
            result.append(item)
        return {"data": result}

    @app.post("/_sandbox/businesses", status_code=201)
    def sandbox_business_create(body: dict[str, Any] = Body(...)):
        display_number = normalize_phone(str(body.get("display_phone_number", "")))
        if not display_number:
            return JSONResponse({"error": "display_phone_number is required"}, status_code=400)
        business_id = "BUSINESS_" + secrets.token_hex(4).upper()
        waba_id = "WABA_" + secrets.token_hex(4).upper()
        phone_id = "PHONE_" + secrets.token_hex(4).upper()
        now = store.now().isoformat()
        try:
            store.execute("INSERT INTO business_accounts VALUES(?,?,?,?)", (waba_id, business_id, body.get("name", "Local Business"), now))
            store.execute("INSERT INTO phone_numbers(id,waba_id,display_phone_number,verified_name,created_at) VALUES(?,?,?,?,?)", (phone_id, waba_id, display_number, body.get("verified_name", body.get("name", "Local Business")), now))
        except Exception as exc:
            return JSONResponse({"error": f"Could not create business: {exc}"}, status_code=400)
        return {"business_id": business_id, "waba_id": waba_id, "phone_number_id": phone_id, "display_phone_number": display_number}

    @app.patch("/_sandbox/businesses/{waba_id}")
    def sandbox_business_update(waba_id: str, body: dict[str, Any] = Body(...)):
        account = store.one("SELECT * FROM business_accounts WHERE id=?", (waba_id,))
        if not account:
            return JSONResponse({"error": "business not found"}, status_code=404)
        store.execute("UPDATE business_accounts SET name=? WHERE id=?", (body.get("name", account["name"]), waba_id))
        item = dict(store.one("SELECT * FROM business_accounts WHERE id=?", (waba_id,)))
        item["phone_numbers"] = rows(store.all("SELECT * FROM phone_numbers WHERE waba_id=?", (waba_id,)))
        return item

    @app.patch("/_sandbox/phone-numbers/{phone_id}")
    def sandbox_phone_number_update(phone_id: str, body: dict[str, Any] = Body(...)):
        phone = store.one("SELECT * FROM phone_numbers WHERE id=?", (phone_id,))
        if not phone:
            return JSONResponse({"error": "phone number not found"}, status_code=404)
        display = phone["display_phone_number"]
        if "display_phone_number" in body:
            display = normalize_phone(str(body["display_phone_number"])) or display
        store.execute(
            "UPDATE phone_numbers SET display_phone_number=?,verified_name=? WHERE id=?",
            (display, body.get("verified_name", phone["verified_name"]), phone_id),
        )
        return dict(store.one("SELECT * FROM phone_numbers WHERE id=?", (phone_id,)))

    @app.post("/_sandbox/reset")
    def reset() -> dict[str, bool]:
        for suffix in ("", "-wal", "-shm"):
            Path(str(settings.database_path) + suffix).unlink(missing_ok=True)
        if settings.media_dir.exists():
            shutil.rmtree(settings.media_dir)
        settings.media_dir.mkdir(parents=True)
        store.initialize()
        return {"success": True}

    @app.get("/_sandbox/clock")
    def clock_get() -> dict[str, Any]:
        frozen = store.one("SELECT frozen_at FROM clock_state WHERE singleton=1")["frozen_at"]
        return {"now": store.now().isoformat(), "frozen": bool(frozen)}

    @app.post("/_sandbox/clock")
    def clock_set(body: dict[str, Any] = Body(...)):
        action = body.get("action")
        if action == "reset":
            store.execute("UPDATE clock_state SET frozen_at=NULL WHERE singleton=1")
        elif action == "set":
            store.execute("UPDATE clock_state SET frozen_at=? WHERE singleton=1", (parse_datetime(body["value"]).isoformat(),))
        elif action == "advance":
            value = (store.now() + parse_duration(body["value"])).astimezone(timezone.utc)
            store.execute("UPDATE clock_state SET frozen_at=? WHERE singleton=1", (value.isoformat(),))
        else:
            return JSONResponse({"error": "action must be set, advance, or reset"}, status_code=400)
        return clock_get()

    @app.get("/_sandbox/phones")
    def sandbox_phones() -> dict[str, Any]:
        return {"data": rows(store.all("SELECT * FROM simulated_users ORDER BY created_at"))}

    @app.post("/_sandbox/phones", status_code=201)
    def sandbox_phone_create(body: dict[str, Any] = Body(...)):
        wa_id = normalize_phone(str(body.get("wa_id", "")))
        if not wa_id:
            return JSONResponse({"error": "wa_id is required"}, status_code=400)
        store.execute("INSERT OR REPLACE INTO simulated_users VALUES(?,?,?,?,?)", (wa_id, body.get("display_name", wa_id), int(body.get("online", True)), int(body.get("blocked", False)), store.now().isoformat()))
        return dict(engine.user(wa_id))

    @app.delete("/_sandbox/phones/{wa_id}")
    def sandbox_phone_delete(wa_id: str):
        normalized = normalize_phone(wa_id)
        if not engine.user(normalized):
            return JSONResponse({"error": "phone not found"}, status_code=404)
        conversations = store.all("SELECT id FROM conversations WHERE user_wa_id=?", (normalized,))
        for conversation in conversations:
            store.execute("DELETE FROM message_status_events WHERE message_id IN (SELECT id FROM messages WHERE conversation_id=?)", (conversation["id"],))
            store.execute("DELETE FROM messages WHERE conversation_id=?", (conversation["id"],))
        store.execute("DELETE FROM conversations WHERE user_wa_id=?", (normalized,))
        store.execute("DELETE FROM simulated_users WHERE wa_id=?", (normalized,))
        return {"success": True}

    @app.patch("/_sandbox/phones/{wa_id}")
    def sandbox_phone_update(wa_id: str, body: dict[str, Any] = Body(...)):
        current = engine.user(wa_id)
        if not current:
            return JSONResponse({"error": "phone not found"}, status_code=404)
        store.execute("UPDATE simulated_users SET display_name=?,online=?,blocked=? WHERE wa_id=?", (body.get("display_name", current["display_name"]), int(body.get("online", bool(current["online"]))), int(body.get("blocked", bool(current["blocked"]))), normalize_phone(wa_id)))
        return dict(engine.user(wa_id))

    @app.post("/_sandbox/phones/{wa_id}/messages", status_code=201)
    async def sandbox_inbound(wa_id: str, body: dict[str, Any] = Body(...)):
        message_type = body.get("type", "text")
        supplied_payload = body.get(message_type)
        if message_type == "text" and isinstance(supplied_payload, str):
            payload = {"body": supplied_payload}
        elif isinstance(supplied_payload, dict):
            payload = supplied_payload
        else:
            payload = {"body": ""} if message_type == "text" else {}
        try:
            return await engine.receive_inbound(body.get("phone_number_id", "PHONE_LOCAL"), wa_id, message_type, payload)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/_sandbox/phones/{wa_id}/read")
    async def sandbox_phone_read(wa_id: str, body: dict[str, Any] = Body(...)):
        phone_number_id = body.get("phone_number_id")
        if not phone_number_id:
            return JSONResponse({"error": "phone_number_id is required"}, status_code=400)
        unread = store.all(
            "SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE c.user_wa_id=? AND c.phone_number_id=? AND m.direction='outbound' "
            "AND m.status='delivered' ORDER BY m.created_at",
            (normalize_phone(wa_id), phone_number_id),
        )
        for message in unread:
            await engine.set_status(message["id"], "read")
        return {"success": True, "read": len(unread)}

    @app.get("/_sandbox/messages")
    def sandbox_messages(
        wa_id: str | None = None,
        phone_number_id: str | None = None,
        limit: int = Query(100, ge=1, le=500),
    ):
        if wa_id and phone_number_id:
            result = store.all(
                "SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                "WHERE c.user_wa_id=? AND c.phone_number_id=? ORDER BY m.created_at DESC LIMIT ?",
                (normalize_phone(wa_id), phone_number_id, limit),
            )
        elif wa_id:
            result = store.all("SELECT * FROM messages WHERE sender_id=? OR recipient_id=? ORDER BY created_at DESC LIMIT ?", (normalize_phone(wa_id), normalize_phone(wa_id), limit))
        else:
            result = store.all("SELECT * FROM messages ORDER BY created_at DESC LIMIT ?", (limit,))
        data = rows(result)
        for item in data:
            item["payload"] = json.loads(item.pop("payload_json"))
        return {"data": data}

    @app.get("/_sandbox/conversations")
    def sandbox_conversations(wa_id: str | None = None, phone_number_id: str | None = None):
        clauses: list[str] = []
        values: list[Any] = []
        if wa_id:
            clauses.append("c.user_wa_id=?")
            values.append(normalize_phone(wa_id))
        if phone_number_id:
            clauses.append("c.phone_number_id=?")
            values.append(phone_number_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        data = rows(store.all(
            "SELECT c.*,p.display_phone_number,p.verified_name,b.name AS business_name "
            "FROM conversations c JOIN phone_numbers p ON p.id=c.phone_number_id "
            "JOIN business_accounts b ON b.id=p.waba_id" + where + " ORDER BY c.created_at DESC",
            tuple(values),
        ))
        now = store.now().isoformat()
        for item in data:
            item["service_window_open"] = bool(item["service_window_expires_at"] and now < item["service_window_expires_at"])
        return {"data": data}

    @app.post("/_sandbox/messages/{message_id}/status")
    async def sandbox_status(message_id: str, body: dict[str, Any] = Body(...)):
        if not store.one("SELECT id FROM messages WHERE id=?", (message_id,)):
            return JSONResponse({"error": "message not found"}, status_code=404)
        await engine.set_status(message_id, body["status"])
        return {"success": True}

    @app.get("/_sandbox/webhooks")
    def sandbox_webhooks():
        data = rows(store.all("SELECT * FROM webhook_deliveries ORDER BY created_at DESC"))
        for item in data:
            item["request_body"] = json.loads(bytes(item["request_body"]))
            if isinstance(item.get("last_response_body"), bytes):
                item["last_response_body"] = item["last_response_body"].decode("utf-8", errors="replace")
            item["attempts"] = rows(store.all(
                "SELECT * FROM webhook_attempts WHERE delivery_id=? ORDER BY attempt_number",
                (item["id"],),
            ))
            for attempt in item["attempts"]:
                if isinstance(attempt.get("response_body"), bytes):
                    attempt["response_body"] = attempt["response_body"].decode("utf-8", errors="replace")
        return {"data": data}

    @app.get("/_sandbox/webhook-subscriptions")
    def sandbox_webhook_subscriptions():
        return {"data": rows(store.all(
            "SELECT s.*,b.name AS business_name,a.name AS app_name FROM webhook_subscriptions s "
            "LEFT JOIN business_accounts b ON b.id=s.waba_id "
            "LEFT JOIN developer_apps a ON a.id=s.app_id ORDER BY s.created_at DESC"
        ))}

    @app.post("/_sandbox/webhooks/{delivery_id}/replay")
    async def webhook_replay(delivery_id: str):
        if not store.one("SELECT id FROM webhook_deliveries WHERE id=?", (delivery_id,)):
            return JSONResponse({"error": "delivery not found"}, status_code=404)
        await engine.deliver_webhook(delivery_id)
        return dict(store.one("SELECT * FROM webhook_deliveries WHERE id=?", (delivery_id,)))

    @app.websocket("/_sandbox/clients/{wa_id}")
    async def client_socket(websocket: WebSocket, wa_id: str):
        await websocket.accept()
        wa_id = normalize_phone(wa_id)
        engine.listeners.setdefault(wa_id, set()).add(websocket)
        try:
            while True:
                payload = await websocket.receive_json()
                if payload.get("action") == "send":
                    message = await engine.receive_inbound(payload.get("phone_number_id", "PHONE_LOCAL"), wa_id, payload.get("type", "text"), payload.get("payload", {"body": ""}))
                    await websocket.send_json({"event": "accepted", "message": message})
        except WebSocketDisconnect:
            engine.listeners.get(wa_id, set()).discard(websocket)

    @app.get("/webhook", response_class=PlainTextResponse)
    def verify_webhook(hub_mode: str = Query(alias="hub.mode"), hub_verify_token: str = Query(alias="hub.verify_token"), hub_challenge: str = Query(alias="hub.challenge")):
        if hub_mode == "subscribe" and hub_verify_token == settings.verify_token:
            return hub_challenge
        return PlainTextResponse("Verification failed", status_code=403)

    @app.get("/{version}/{object_id}")
    def graph_object(version: str, object_id: str):
        if row := store.one("SELECT * FROM business_accounts WHERE id=?", (object_id,)):
            return {"id": row["id"], "name": row["name"], "timezone_id": "1"}
        if row := store.one("SELECT * FROM phone_numbers WHERE id=?", (object_id,)):
            return {key: row[key] for key in ("id", "verified_name", "display_phone_number", "quality_rating")}
        if row := store.one("SELECT * FROM templates WHERE id=?", (object_id,)):
            result = dict(row)
            result["components"] = json.loads(result.pop("components_json"))
            return result
        if row := store.one("SELECT * FROM media WHERE id=?", (object_id,)):
            return {"url": f"{settings.base_url}/_sandbox/media/{object_id}", "mime_type": row["mime_type"], "sha256": row["sha256"], "file_size": row["size_bytes"], "id": row["id"], "messaging_product": "whatsapp"}
        return graph_error(100, f"Unsupported get request. Object with ID {object_id} does not exist.", status_code=404)

    @app.delete("/{version}/{object_id}")
    def graph_delete(version: str, object_id: str):
        if row := store.one("SELECT storage_path FROM media WHERE id=?", (object_id,)):
            Path(row["storage_path"]).unlink(missing_ok=True)
            store.execute("DELETE FROM media WHERE id=?", (object_id,))
            return {"success": True}
        if store.one("SELECT id FROM templates WHERE id=?", (object_id,)):
            store.execute("DELETE FROM templates WHERE id=?", (object_id,))
            return {"success": True}
        return graph_error(100, f"Object with ID {object_id} does not exist.", status_code=404)

    @app.get("/{version}/{business_id}/owned_whatsapp_business_accounts")
    def owned_wabas(version: str, business_id: str):
        return {"data": rows(store.all("SELECT id,name FROM business_accounts WHERE business_id=?", (business_id,)))}

    @app.get("/{version}/{waba_id}/phone_numbers")
    def phone_numbers(version: str, waba_id: str):
        return {"data": rows(store.all("SELECT verified_name,display_phone_number,id,quality_rating FROM phone_numbers WHERE waba_id=?", (waba_id,)))}

    @app.post("/{version}/{phone_id}/messages")
    async def send_message(version: str, phone_id: str, body: dict[str, Any] = Body(...)):
        if body.get("status") == "read":
            message = store.one("SELECT * FROM messages WHERE id=? AND direction='inbound' AND recipient_id=?", (body.get("message_id"), phone_id))
            if not message:
                return graph_error(131009, "The message ID is invalid or does not belong to this phone number.")
            store.execute("UPDATE messages SET status='read',updated_at=? WHERE id=?", (store.now().isoformat(), message["id"]))
            await engine.broadcast(message["sender_id"], {"event": "read", "message_id": message["id"], "typing_indicator": body.get("typing_indicator")})
            return {"success": True}
        code, details = engine.validate_outbound(phone_id, body)
        if code:
            return graph_error(code, details or "Request rejected")
        return await engine.send_outbound(version, phone_id, body)

    @app.post("/{version}/{phone_id}/media")
    async def upload_media(version: str, phone_id: str, messaging_product: str = Form(...), file: UploadFile = File(...)):
        if not engine.phone(phone_id):
            return graph_error(100, "Phone number ID does not exist.", status_code=404)
        if messaging_product != "whatsapp":
            return graph_error(131009, "messaging_product must be whatsapp.")
        content = await file.read()
        if not content:
            return graph_error(131053, "Uploaded file is empty.")
        mime = file.content_type or "application/octet-stream"
        limits = {
            "image/jpeg": 5_000_000, "image/png": 5_000_000, "image/webp": 500_000,
            "audio/aac": 16_000_000, "audio/mp4": 16_000_000, "audio/mpeg": 16_000_000,
            "audio/amr": 16_000_000, "audio/ogg": 16_000_000, "video/mp4": 16_000_000,
            "video/3gpp": 16_000_000, "text/plain": 100_000_000, "application/pdf": 100_000_000,
            "application/msword": 100_000_000, "application/vnd.ms-excel": 100_000_000,
            "application/vnd.ms-powerpoint": 100_000_000,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": 100_000_000,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": 100_000_000,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": 100_000_000,
        }
        if settings.mode == "strict" and mime not in limits:
            return graph_error(131053, f"Media MIME type {mime} is not supported.")
        if len(content) > limits.get(mime, 100_000_000):
            return graph_error(131053, f"Media exceeds the size limit for {mime}.")
        media_id = engine.save_media(phone_id, content, mime, file.filename)
        return {"id": media_id}

    @app.get("/_sandbox/media/{media_id}")
    def download_media(media_id: str, authorization: str | None = Header(default=None)):
        if authorization != f"Bearer {settings.access_token}":
            return graph_error(190, "A valid access token is required.", status_code=401)
        row = store.one("SELECT * FROM media WHERE id=?", (media_id,))
        if not row:
            return graph_error(100, "Media ID does not exist.", status_code=404)
        return FileResponse(row["storage_path"], media_type=row["mime_type"], filename=row["filename"])

    @app.get("/{version}/{waba_id}/message_templates")
    def list_templates(version: str, waba_id: str, name: str | None = None):
        sql = "SELECT * FROM templates WHERE waba_id=?" + (" AND name=?" if name else "")
        values = (waba_id, name) if name else (waba_id,)
        data = rows(store.all(sql, values))
        for item in data:
            item["components"] = json.loads(item.pop("components_json"))
            item.pop("waba_id", None)
            item.pop("created_at", None)
            item.pop("updated_at", None)
        return {"data": data}

    @app.post("/{version}/{waba_id}/message_templates")
    def create_template(version: str, waba_id: str, body: dict[str, Any] = Body(...)):
        for field in ("name", "language", "category", "components"):
            if field not in body:
                return graph_error(131008, f"Parameter {field} is required.")
        template_id = str(secrets.randbelow(9_000_000_000_000_000) + 1_000_000_000_000_000)
        now = store.now().isoformat()
        status = "APPROVED" if body.get("_sandbox_auto_approve", True) else "PENDING"
        try:
            store.execute("INSERT INTO templates VALUES(?,?,?,?,?,?,?,?,?)", (template_id, waba_id, body["name"], body["language"], body["category"].upper(), status, json.dumps(body["components"]), now, now))
        except Exception:
            return graph_error(100, "A template with this name and language already exists.")
        return {"id": template_id, "status": status, "category": body["category"].upper()}

    @app.delete("/{version}/{waba_id}/message_templates")
    def delete_template_by_name(version: str, waba_id: str, name: str = Query(...)):
        store.execute("DELETE FROM templates WHERE waba_id=? AND name=?", (waba_id, name))
        return {"success": True}

    @app.get("/{version}/{waba_id}/subscribed_apps")
    def subscriptions(version: str, waba_id: str):
        return {"data": rows(store.all("SELECT id,callback_url FROM webhook_subscriptions WHERE waba_id=? AND active=1", (waba_id,)))}

    @app.post("/{version}/{waba_id}/subscribed_apps")
    async def subscribe(
        version: str,
        waba_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        authorization: str | None = Header(default=None),
    ):
        callback = body.get("override_callback_uri") or body.get("callback_url")
        if not callback:
            return graph_error(131008, "For the local simulator, callback_url or override_callback_uri is required.")
        verify_token = body.get("verify_token")
        if verify_token:
            challenge = secrets.token_urlsafe(18)
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    verification = await client.get(callback, params={"hub.mode": "subscribe", "hub.verify_token": verify_token, "hub.challenge": challenge})
                if verification.status_code != 200 or verification.text.strip() != challenge:
                    return graph_error(100, "Webhook verification failed: callback did not return hub.challenge.")
            except Exception as exc:
                return graph_error(100, f"Webhook verification failed: {exc}")
        token = (authorization or "").removeprefix("Bearer ").strip()
        local_app = store.one("SELECT id,app_secret FROM developer_apps WHERE access_token=?", (token,))
        app_id = local_app["id"] if local_app else "APP_LOCAL"
        app_secret = local_app["app_secret"] if local_app else settings.app_secret
        store.execute(
            "INSERT INTO webhook_subscriptions(id,waba_id,callback_url,active,created_at,app_id,app_secret,verify_token) VALUES(?,?,?,?,?,?,?,?)",
            ("sub_" + uuid.uuid4().hex, waba_id, callback, 1, store.now().isoformat(), app_id, app_secret, verify_token),
        )
        return {"success": True}

    @app.delete("/{version}/{waba_id}/subscribed_apps")
    def unsubscribe(version: str, waba_id: str):
        store.execute("UPDATE webhook_subscriptions SET active=0 WHERE waba_id=?", (waba_id,))
        return {"success": True}

    @app.get("/{version}/{phone_id}/whatsapp_business_profile")
    def business_profile(version: str, phone_id: str):
        phone = engine.phone(phone_id)
        if not phone:
            return graph_error(100, "Phone number ID does not exist.", status_code=404)
        return {"data": [json.loads(phone["profile_json"])]}

    @app.post("/{version}/{phone_id}/whatsapp_business_profile")
    def business_profile_update(version: str, phone_id: str, body: dict[str, Any] = Body(...)):
        if body.get("messaging_product") != "whatsapp":
            return graph_error(131009, "messaging_product must be whatsapp.")
        profile = {key: value for key, value in body.items() if key != "messaging_product"}
        store.execute("UPDATE phone_numbers SET profile_json=? WHERE id=?", (json.dumps(profile), phone_id))
        return {"success": True}

    return app


app = create_app()
