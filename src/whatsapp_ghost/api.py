from __future__ import annotations

import asyncio
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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles

from .clock import parse_datetime, parse_duration
from .config import Settings
from .db import Store
from .identity import generated_color, generated_name
from .engine import Engine, normalize_phone
from .errors import graph_error
from .template_validation import validate_template
from .web_console import asset, WEB_DIR


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
        store.initialize(settings.access_token, settings.app_secret)
        # Sync route handlers run off the loop and cannot find it themselves.
        engine.loop = asyncio.get_running_loop()
        try:
            yield
        finally:
            engine.loop = None

    app = FastAPI(
        title="WhatsApp Ghost",
        version="0.1.0",
        description="Contract-compatible local WhatsApp Cloud API sandbox. Sandbox-only controls are under /_sandbox.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.engine = engine

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        """Return a clean 422 even when the request carried binary data.

        FastAPI's default handler echoes the offending input back in the error
        body, which raises UnicodeDecodeError (a 500) when that input is an
        uploaded image. Drop the raw input so multipart uploads report the
        actual validation problem instead of crashing.
        """
        details = []
        for err in exc.errors():
            cleaned = {k: v for k, v in err.items() if k != "input"}
            value = err.get("input")
            if isinstance(value, bytes):
                cleaned["input"] = f"<{len(value)} bytes of binary data>"
            elif value is not None:
                cleaned["input"] = value
            details.append(cleaned)
        return JSONResponse({"detail": jsonable_encoder(details)}, status_code=422)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    def valid_access_token(token: str) -> bool:
        return token == settings.access_token or bool(store.one("SELECT id FROM developer_apps WHERE access_token=?", (token,)))

    @app.middleware("http")
    async def authentication(request: Request, call_next):
        if request.url.path == "/" or request.url.path.startswith(("/_sandbox", "/webhook", "/console", "/guide", "/phone", "/static", "/docs", "/openapi.json", "/redoc")):
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ").strip()
        if not valid_access_token(token):
            return graph_error(190, "The access token is invalid or missing.", status_code=401)
        return await call_next(request)

    @app.get("/_sandbox/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "mode": settings.mode, "now": store.now().isoformat(), "database": str(settings.database_path)}

    @app.get("/_sandbox/config")
    def public_config() -> dict[str, Any]:
        return {
            "base_url": settings.base_url,
            "mode": settings.mode,
            "access_token": settings.access_token,
            "media_dir": str(settings.media_dir),
            "demo": {"business_id": "BUSINESS_LOCAL", "waba_id": "WABA_LOCAL", "phone_number_id": "PHONE_LOCAL", "business_phone": "15550001000", "customer": "15550002001"},
        }

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/console")

    # The markup is read from disk per request and carries version-stamped asset
    # URLs, so it must never itself be cached: a cached page would keep pointing
    # at the previous stylesheet and script.
    NO_STORE = {"Cache-Control": "no-store, must-revalidate"}

    @app.get("/console", response_class=HTMLResponse, include_in_schema=False)
    def console_page():
        return HTMLResponse(asset("console.html"), headers=NO_STORE)

    @app.get("/guide", response_class=HTMLResponse, include_in_schema=False)
    def guide_page():
        return HTMLResponse(asset("console.html"), headers=NO_STORE)

    @app.get("/phone", response_class=HTMLResponse, include_in_schema=False)
    def phone_page():
        return HTMLResponse(asset("phone.html"), headers=NO_STORE)

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

    @app.post("/_sandbox/businesses/{waba_id}/phone-numbers", status_code=201)
    def sandbox_phone_number_create(waba_id: str, body: dict[str, Any] = Body(...)):
        account = store.one("SELECT * FROM business_accounts WHERE id=?", (waba_id,))
        if not account:
            return JSONResponse({"error": "business account not found"}, status_code=404)
        display_number = normalize_phone(str(body.get("display_phone_number", "")))
        if not display_number:
            return JSONResponse({"error": "display_phone_number is required"}, status_code=400)
        phone_id = "PHONE_" + secrets.token_hex(4).upper()
        try:
            store.execute(
                "INSERT INTO phone_numbers(id,waba_id,display_phone_number,verified_name,created_at) VALUES(?,?,?,?,?)",
                (phone_id, waba_id, display_number, body.get("verified_name") or account["name"], store.now().isoformat()),
            )
        except Exception as exc:
            return JSONResponse({"error": f"Could not add sender: {exc}"}, status_code=400)
        return dict(store.one("SELECT * FROM phone_numbers WHERE id=?", (phone_id,)))

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
        store.initialize(settings.access_token, settings.app_secret)
        return {"success": True}

    def _future_messages() -> list[Any]:
        """Messages stamped after the current clock.

        Time travel is a feature, but resetting the clock leaves anything
        written while it was ahead sitting in the future, where it sorts to
        the bottom of every transcript and never ages out.
        """
        return store.all(
            "SELECT id,conversation_id,direction,created_at FROM messages WHERE created_at > ? ORDER BY created_at",
            (store.now().isoformat(),),
        )

    @app.get("/_sandbox/clock")
    def clock_get() -> dict[str, Any]:
        frozen = store.one("SELECT frozen_at FROM clock_state WHERE singleton=1")["frozen_at"]
        payload: dict[str, Any] = {"now": store.now().isoformat(), "frozen": bool(frozen)}
        if future := _future_messages():
            payload["future_messages"] = len(future)
            payload["warning"] = (
                f"{len(future)} message(s) are stamped ahead of the current clock. "
                "POST /_sandbox/clock {\"action\":\"discard_future\"} to remove them."
            )
        return payload

    @app.post("/_sandbox/clock")
    def clock_set(body: dict[str, Any] = Body(...)):
        action = body.get("action")
        try:
            if action == "reset":
                store.execute("UPDATE clock_state SET frozen_at=NULL WHERE singleton=1")
            elif action == "discard_future":
                doomed = _future_messages()
                for row in doomed:
                    store.execute("DELETE FROM message_status_events WHERE message_id=?", (row["id"],))
                    store.execute("DELETE FROM messages WHERE id=?", (row["id"],))
                return {**clock_get(), "discarded": len(doomed)}
            elif action == "set":
                store.execute("UPDATE clock_state SET frozen_at=? WHERE singleton=1", (parse_datetime(body["value"]).isoformat(),))
            elif action == "advance":
                value = (store.now() + parse_duration(body["value"])).astimezone(timezone.utc)
                store.execute("UPDATE clock_state SET frozen_at=? WHERE singleton=1", (value.isoformat(),))
            else:
                return JSONResponse(
                    {"error": "action must be set, advance, reset, or discard_future"}, status_code=400
                )
        except (KeyError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc) or "A valid clock value is required"}, status_code=400)
        return clock_get()

    @app.get("/_sandbox/stats")
    def sandbox_stats() -> dict[str, Any]:
        """Counts for the dashboard tiles, as COUNT(*) rather than array lengths.

        The console used to derive these from the collections it had fetched, so
        the message tile was really "how many messages we downloaded", capped at
        the page size and wrong the moment a chat got busy.
        """
        def count(table: str) -> int:
            row = store.one(f"SELECT COUNT(*) AS total FROM {table}")
            return row["total"] if row else 0

        return {
            "apps": count("developer_apps"),
            "businesses": count("business_accounts"),
            "phone_numbers": count("phone_numbers"),
            "customers": count("simulated_users"),
            "conversations": count("conversations"),
            "messages": count("messages"),
            "templates": count("templates"),
            "webhooks": count("webhook_deliveries"),
            "media": count("media"),
        }

    @app.get("/_sandbox/phones")
    def sandbox_phones() -> dict[str, Any]:
        return {"data": rows(store.all("SELECT * FROM simulated_users ORDER BY created_at"))}

    @app.get("/_sandbox/unread")
    def sandbox_unread() -> dict[str, Any]:
        """Unread counts per simulated customer, per business number.

        Unread is derived from message status rather than tracked separately:
        an outbound message the customer has not opened yet sits at accepted /
        sent / delivered, and opening the chat POSTs .../read which moves it to
        "read". That is the same signal the sandbox already reports to webhooks,
        so a badge here can never disagree with a read receipt, and it survives
        a page reload for free.
        """
        result = store.all(
            "SELECT c.user_wa_id AS wa_id, c.phone_number_id AS phone_number_id,"
            " COUNT(*) AS unread, MAX(m.created_at) AS last_at"
            " FROM messages m JOIN conversations c ON c.id=m.conversation_id"
            " WHERE m.direction='outbound' AND m.status IN ('accepted','sent','delivered')"
            " GROUP BY c.user_wa_id, c.phone_number_id"
        )
        activity = store.all(
            "SELECT c.user_wa_id AS wa_id, c.phone_number_id AS phone_number_id,"
            " MAX(m.created_at) AS last_at, COUNT(*) AS total"
            " FROM messages m JOIN conversations c ON c.id=m.conversation_id"
            " GROUP BY c.user_wa_id, c.phone_number_id"
        )
        # Activity covers read messages too, so the chat list can order by most
        # recent regardless of whether anything is still unread.
        return {"data": rows(result), "activity": rows(activity)}

    @app.post("/_sandbox/phones", status_code=201)
    def sandbox_phone_create(body: dict[str, Any] = Body(...)):
        wa_id = normalize_phone(str(body.get("wa_id", "")))
        if not wa_id:
            return JSONResponse({"error": "wa_id is required"}, status_code=400)
        # INSERT OR REPLACE rewrites the whole row, so an existing favourite has
        # to be carried over explicitly or re-adding a number silently unstars it.
        existing = engine.user(wa_id)
        was_starred = bool(existing["starred"]) if existing and "starred" in existing.keys() else False
        store.execute(
            "INSERT OR REPLACE INTO simulated_users(wa_id,display_name,online,blocked,created_at,color,auto_created,starred)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                wa_id,
                body.get("display_name") or generated_name(wa_id),
                int(body.get("online", True)),
                int(body.get("blocked", False)),
                store.now().isoformat(),
                body.get("color") or generated_color(wa_id),
                0,
                int(body.get("starred", was_starred)),
            ),
        )
        created = dict(engine.user(wa_id))
        engine._announce({"event": "phone_created", "wa_id": wa_id, "user": created})
        return created

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
        engine._announce({"event": "phone_deleted", "wa_id": normalized})
        return {"success": True}

    @app.patch("/_sandbox/phones/{wa_id}")
    def sandbox_phone_update(wa_id: str, body: dict[str, Any] = Body(...)):
        current = engine.user(wa_id)
        if not current:
            return JSONResponse({"error": "phone not found"}, status_code=404)
        # "starred" is a favourite in the simulator's number list: starred
        # numbers sort above everything else so the handful you actually test
        # with stay reachable once autocreate has filled the roster.
        current_starred = bool(current["starred"]) if "starred" in current.keys() else False
        store.execute(
            "UPDATE simulated_users SET display_name=?,online=?,blocked=?,color=?,starred=? WHERE wa_id=?",
            (
                body.get("display_name") or current["display_name"],
                int(body.get("online", bool(current["online"]))),
                int(body.get("blocked", bool(current["blocked"]))),
                body.get("color") or current["color"] or generated_color(normalize_phone(wa_id)),
                int(body.get("starred", current_starred)),
                normalize_phone(wa_id),
            ),
        )
        updated = dict(engine.user(wa_id))
        engine._announce({"event": "phone_updated", "wa_id": normalize_phone(wa_id), "user": updated})
        return updated

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
        phone_number_id = body.get("phone_number_id", "PHONE_LOCAL")
        if message_type in {"image", "audio", "video", "document", "sticker"} and payload.get("id"):
            media = store.one("SELECT phone_number_id FROM media WHERE id=?", (payload["id"],))
            if not media or media["phone_number_id"] != phone_number_id:
                return JSONResponse({"error": "The referenced media ID does not exist for this phone number"}, status_code=400)
        try:
            return await engine.receive_inbound(
                phone_number_id, wa_id, message_type, payload, body.get("context")
            )
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

    @app.get("/_sandbox/analytics")
    def sandbox_analytics(
        tz_offset: int = Query(0, ge=-840, le=840, description="Minutes to add to UTC for the viewer's local time."),
        top: int = Query(12, ge=1, le=100, description="How many numbers to rank."),
    ):
        """Traffic across every number, not just one chat.

        Answers "which numbers got what", which the per-chat view cannot: it is
        one aggregate over the whole sandbox, plus a ranked table per customer
        and per business sender. Like the per-chat version, every figure is a
        GROUP BY in SQLite, so this costs the same whether the sandbox holds a
        hundred messages or a million.
        """
        shift = f"{tz_offset} minutes"
        totals = store.one(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN direction='inbound' THEN 1 ELSE 0 END) AS inbound,"
            " SUM(CASE WHEN direction='outbound' THEN 1 ELSE 0 END) AS outbound,"
            " MIN(created_at) AS first_at, MAX(created_at) AS last_at FROM messages"
        )
        by_hour = {int(row["hour"]): row["total"] for row in store.all(
            "SELECT CAST(strftime('%H', created_at, ?) AS INTEGER) AS hour, COUNT(*) AS total"
            " FROM messages GROUP BY hour", (shift,),
        )}
        by_day = rows(store.all(
            "SELECT date(created_at, ?) AS day, COUNT(*) AS total,"
            " SUM(CASE WHEN direction='inbound' THEN 1 ELSE 0 END) AS inbound,"
            " SUM(CASE WHEN direction='outbound' THEN 1 ELSE 0 END) AS outbound"
            " FROM messages GROUP BY day ORDER BY day", (shift,),
        ))
        by_type = {row["message_type"]: row["total"] for row in store.all(
            "SELECT message_type, COUNT(*) AS total FROM messages GROUP BY message_type ORDER BY total DESC"
        )}
        # Per customer. LEFT JOIN so a number with no traffic still appears with
        # zeroes rather than vanishing from a list of "all numbers".
        customers = rows(store.all(
            "SELECT u.wa_id, u.display_name, u.color, u.starred, u.auto_created,"
            " COUNT(m.id) AS total,"
            " SUM(CASE WHEN m.direction='inbound' THEN 1 ELSE 0 END) AS inbound,"
            " SUM(CASE WHEN m.direction='outbound' THEN 1 ELSE 0 END) AS outbound,"
            " MAX(m.created_at) AS last_at,"
            " COUNT(DISTINCT c.phone_number_id) AS businesses"
            " FROM simulated_users u"
            " LEFT JOIN conversations c ON c.user_wa_id=u.wa_id"
            " LEFT JOIN messages m ON m.conversation_id=c.id"
            " GROUP BY u.wa_id ORDER BY total DESC, u.display_name LIMIT ?",
            (top,),
        ))
        senders = rows(store.all(
            "SELECT p.id AS phone_number_id, p.verified_name, p.display_phone_number,"
            " COUNT(m.id) AS total,"
            " SUM(CASE WHEN m.direction='inbound' THEN 1 ELSE 0 END) AS inbound,"
            " SUM(CASE WHEN m.direction='outbound' THEN 1 ELSE 0 END) AS outbound,"
            " COUNT(DISTINCT c.user_wa_id) AS customers, MAX(m.created_at) AS last_at"
            " FROM phone_numbers p"
            " LEFT JOIN conversations c ON c.phone_number_id=p.id"
            " LEFT JOIN messages m ON m.conversation_id=c.id"
            " GROUP BY p.id ORDER BY total DESC"
        ))
        # The busiest individual pairings, which is the "map across numbers":
        # which customer talks to which sender, and how much.
        pairs = rows(store.all(
            "SELECT c.user_wa_id AS wa_id, c.phone_number_id AS phone_number_id,"
            " COUNT(m.id) AS total,"
            " SUM(CASE WHEN m.direction='inbound' THEN 1 ELSE 0 END) AS inbound,"
            " SUM(CASE WHEN m.direction='outbound' THEN 1 ELSE 0 END) AS outbound,"
            " MAX(m.created_at) AS last_at"
            " FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id"
            " GROUP BY c.id HAVING total > 0 ORDER BY total DESC LIMIT ?",
            (top,),
        ))
        return {
            "tz_offset": tz_offset,
            "total": (totals["total"] if totals else 0) or 0,
            "inbound": (totals["inbound"] if totals else 0) or 0,
            "outbound": (totals["outbound"] if totals else 0) or 0,
            "first_at": totals["first_at"] if totals else None,
            "last_at": totals["last_at"] if totals else None,
            "by_hour": [by_hour.get(hour, 0) for hour in range(24)],
            "by_day": by_day,
            "by_type": by_type,
            "customers": customers,
            "senders": senders,
            "pairs": pairs,
        }

    @app.get("/_sandbox/phones/{wa_id}/analytics")
    def sandbox_chat_analytics(
        wa_id: str,
        phone_number_id: str | None = None,
        tz_offset: int = Query(0, ge=-840, le=840, description="Minutes to add to UTC for the viewer's local time."),
    ):
        """Traffic shape for one chat: when messages arrived, and how many.

        Every bucket is aggregated in SQL rather than by shipping the transcript
        and counting in the browser, so the cost is the same for a 20-message
        chat and a 200,000-message one. tz_offset is applied inside SQLite so
        the hour-of-day histogram and the day buckets line up with the clock the
        reader is actually looking at, instead of drifting by their UTC offset.
        """
        normalized = normalize_phone(wa_id)
        if not engine.user(normalized):
            return JSONResponse({"error": "phone not found"}, status_code=404)
        shift = f"{tz_offset} minutes"
        where = "c.user_wa_id=?"
        values: list[Any] = [normalized]
        if phone_number_id:
            where += " AND c.phone_number_id=?"
            values.append(phone_number_id)
        join = (" FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE " + where)

        totals = store.one(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN m.direction='inbound' THEN 1 ELSE 0 END) AS inbound,"
            " SUM(CASE WHEN m.direction='outbound' THEN 1 ELSE 0 END) AS outbound,"
            " MIN(m.created_at) AS first_at, MAX(m.created_at) AS last_at" + join,
            tuple(values),
        )
        by_hour = {int(row["hour"]): row["total"] for row in store.all(
            "SELECT CAST(strftime('%H', m.created_at, ?) AS INTEGER) AS hour, COUNT(*) AS total"
            + join + " GROUP BY hour", (shift, *values),
        )}
        by_day = rows(store.all(
            "SELECT date(m.created_at, ?) AS day, COUNT(*) AS total,"
            " SUM(CASE WHEN m.direction='inbound' THEN 1 ELSE 0 END) AS inbound,"
            " SUM(CASE WHEN m.direction='outbound' THEN 1 ELSE 0 END) AS outbound"
            + join + " GROUP BY day ORDER BY day", (shift, *values),
        ))
        by_type = {row["message_type"]: row["total"] for row in store.all(
            "SELECT m.message_type AS message_type, COUNT(*) AS total" + join
            + " GROUP BY m.message_type ORDER BY total DESC", tuple(values),
        )}
        by_status = {row["status"]: row["total"] for row in store.all(
            "SELECT m.status AS status, COUNT(*) AS total" + join
            + " AND m.direction='outbound' GROUP BY m.status", tuple(values),
        )}
        return {
            "wa_id": normalized,
            "phone_number_id": phone_number_id,
            "tz_offset": tz_offset,
            "total": (totals["total"] if totals else 0) or 0,
            "inbound": (totals["inbound"] if totals else 0) or 0,
            "outbound": (totals["outbound"] if totals else 0) or 0,
            "first_at": totals["first_at"] if totals else None,
            "last_at": totals["last_at"] if totals else None,
            # Always 24 slots, so the histogram has a stable x axis even when a
            # chat only ever saw traffic in one hour of the day.
            "by_hour": [by_hour.get(hour, 0) for hour in range(24)],
            "by_day": by_day,
            "by_type": by_type,
            "by_status": by_status,
        }

    @app.delete("/_sandbox/phones/{wa_id}/messages")
    async def sandbox_clear_chat(wa_id: str, phone_number_id: str | None = None):
        """Erase a customer's transcript, for one business chat or all of them.

        The conversation row itself is kept, so the 24-hour service window, the
        pin and the chat's place in the list all survive: clearing a chat in a
        real client removes the messages, not the contact. Status events go with
        the messages because they are only ever read through them, and leaving
        them behind would grow the table without bound.
        """
        normalized = normalize_phone(wa_id)
        if not engine.user(normalized):
            return JSONResponse({"error": "phone not found"}, status_code=404)
        if phone_number_id:
            conversations = store.all(
                "SELECT id FROM conversations WHERE user_wa_id=? AND phone_number_id=?",
                (normalized, phone_number_id),
            )
        else:
            conversations = store.all(
                "SELECT id FROM conversations WHERE user_wa_id=?", (normalized,)
            )
        ids = [row["id"] for row in conversations]
        deleted = 0
        if ids:
            placeholders = ",".join("?" for _ in ids)
            # One connection for the whole delete: each store.execute() opens
            # its own, and a chat with thousands of messages would otherwise
            # pay that cost twice over per conversation.
            with store.connect() as db:
                deleted = db.execute(
                    f"SELECT COUNT(*) AS total FROM messages WHERE conversation_id IN ({placeholders})",
                    tuple(ids),
                ).fetchone()["total"]
                db.execute(
                    "DELETE FROM message_status_events WHERE message_id IN"
                    f" (SELECT id FROM messages WHERE conversation_id IN ({placeholders}))",
                    tuple(ids),
                )
                db.execute(
                    f"DELETE FROM messages WHERE conversation_id IN ({placeholders})",
                    tuple(ids),
                )
        # Observers hold their own copy of the transcript and the unread badges,
        # so every open tab has to be told rather than left to drift.
        await engine.broadcast(normalized, {
            "event": "chat_cleared",
            "phone_number_id": phone_number_id,
            "deleted": deleted,
        })
        return {"success": True, "deleted": deleted, "phone_number_id": phone_number_id}

    @app.get("/_sandbox/phones/{wa_id}/pins")
    def sandbox_pins(wa_id: str):
        """Pinned business chats for one simulated customer."""
        pins = store.all(
            "SELECT phone_number_id,pinned_at FROM conversations "
            "WHERE user_wa_id=? AND pinned=1 ORDER BY pinned_at DESC",
            (normalize_phone(wa_id),),
        )
        return {"data": [row["phone_number_id"] for row in pins]}

    @app.post("/_sandbox/phones/{wa_id}/pins")
    def sandbox_pin_set(wa_id: str, body: dict[str, Any] = Body(...)):
        phone_number_id = body.get("phone_number_id")
        if not phone_number_id:
            return JSONResponse({"error": "phone_number_id is required"}, status_code=400)
        if not store.one("SELECT id FROM phone_numbers WHERE id=?", (phone_number_id,)):
            return JSONResponse({"error": "phone number not found"}, status_code=404)
        normalized = normalize_phone(wa_id)
        if not engine.user(normalized):
            return JSONResponse({"error": "phone not found"}, status_code=404)
        pinned = bool(body.get("pinned", True))
        # The chat may have no conversation row yet, so create it before pinning.
        engine.conversation(phone_number_id, normalized)
        store.execute(
            "UPDATE conversations SET pinned=?,pinned_at=? WHERE phone_number_id=? AND user_wa_id=?",
            (int(pinned), store.now().isoformat() if pinned else None, phone_number_id, normalized),
        )
        return {"success": True, "phone_number_id": phone_number_id, "pinned": pinned}

    @app.get("/_sandbox/messages")
    def sandbox_messages(
        wa_id: str | None = None,
        phone_number_id: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        before: str | None = Query(None, description="Message id to page backwards from."),
    ):
        """Newest messages first, optionally paged backwards from ``before``.

        Pagination is keyset, not OFFSET: the cursor is the (created_at, rowid)
        of a message the caller already has. OFFSET would drift as new messages
        arrive during a conversation, silently skipping or repeating rows, and
        it makes the database walk the rows it is about to discard. The rowid
        tiebreak matters because the sandbox can write several messages inside
        the same clock tick, which a created_at-only cursor would either lose
        or replay forever.
        """
        anchor: tuple[str, int] | None = None
        if before:
            row = store.one("SELECT created_at, rowid FROM messages WHERE id=?", (before,))
            if not row:
                return JSONResponse({"error": "before cursor is not a known message id"}, status_code=400)
            anchor = (row["created_at"], row["rowid"])

        # One extra row is fetched purely to answer "is there more?" without a
        # second COUNT query; it is dropped before the response is built.
        probe = limit + 1
        keyset = "AND (m.created_at, m.rowid) < (?, ?) " if anchor else ""
        if wa_id and phone_number_id:
            result = store.all(
                "SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                "WHERE c.user_wa_id=? AND c.phone_number_id=? " + keyset +
                "ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?",
                (normalize_phone(wa_id), phone_number_id, *(anchor or ()), probe),
            )
        elif wa_id:
            result = store.all(
                "SELECT m.* FROM messages m WHERE (m.sender_id=? OR m.recipient_id=?) " + keyset +
                "ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?",
                (normalize_phone(wa_id), normalize_phone(wa_id), *(anchor or ()), probe),
            )
        else:
            result = store.all(
                "SELECT m.* FROM messages m WHERE 1=1 " + keyset +
                "ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?",
                (*(anchor or ()), probe),
            )
        data = rows(result)
        has_more = len(data) > limit
        data = data[:limit]
        for item in data:
            item["payload"] = json.loads(item.pop("payload_json"))
            item.pop("rowid", None)
        # The cursor for the next page is the oldest id in this one.
        return {
            "data": data,
            "has_more": has_more,
            "next_before": data[-1]["id"] if data and has_more else None,
        }

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

    @app.get("/_sandbox/messages/{message_id}/diagnostics")
    def sandbox_message_diagnostics(message_id: str):
        """Everything that happened to one message: status hops and webhooks.

        This is the "why did my integration not see this" view. Each hop carries
        the delay since the message was created, and each webhook carries the
        HTTP result and the error, so a failure is attributable without digging
        through the whole delivery log.

        Deliveries are matched by scanning for the message id inside the stored
        request body. That is a LIKE scan, so it is bounded two ways: only
        deliveries at or after the message's own timestamp are considered (a
        webhook about a message cannot predate it) and at most 25 are returned.
        The created_at index makes that range the only part actually walked.
        """
        message = store.one("SELECT * FROM messages WHERE id=?", (message_id,))
        if not message:
            return JSONResponse({"error": "message not found"}, status_code=404)
        created_at = message["created_at"]

        def delay_ms(value: str | None) -> float | None:
            if not value:
                return None
            try:
                return round((parse_datetime(value) - parse_datetime(created_at)).total_seconds() * 1000, 1)
            except (TypeError, ValueError):
                return None

        events = []
        for row in store.all(
            "SELECT status, timestamp FROM message_status_events WHERE message_id=? ORDER BY timestamp",
            (message_id,),
        ):
            events.append({
                "status": row["status"],
                "at": row["timestamp"],
                "delay_ms": delay_ms(row["timestamp"]),
            })

        deliveries = []
        for row in store.all(
            "SELECT id, event_type, destination_url, status, attempt_count, last_status_code,"
            " last_error, created_at, delivered_at FROM webhook_deliveries"
            " WHERE created_at >= ? AND request_body LIKE ?"
            " ORDER BY created_at LIMIT 25",
            (created_at, f"%{message_id}%"),
        ):
            item = dict(row)
            item["queued_delay_ms"] = delay_ms(row["created_at"])
            item["delivered_delay_ms"] = delay_ms(row["delivered_at"])
            item["attempts"] = rows(store.all(
                "SELECT attempt_number, requested_at, completed_at, status_code, error"
                " FROM webhook_attempts WHERE delivery_id=? ORDER BY attempt_number",
                (row["id"],),
            ))
            deliveries.append(item)

        # A message whose webhooks all failed or were never routed is the case
        # worth flagging: the sandbox accepted it, the integration never saw it.
        statuses = {item["status"] for item in deliveries}
        if not deliveries:
            verdict = "no_webhook"
        elif "failed" in statuses:
            verdict = "webhook_failed"
        elif statuses == {"unrouted"}:
            verdict = "unrouted"
        elif "pending" in statuses:
            verdict = "pending"
        else:
            verdict = "delivered"

        return {
            "id": message_id,
            "direction": message["direction"],
            "message_type": message["message_type"],
            "status": message["status"],
            "failure_code": message["failure_code"],
            "created_at": created_at,
            "updated_at": message["updated_at"],
            "events": events,
            "deliveries": deliveries,
            "verdict": verdict,
        }

    @app.post("/_sandbox/messages/{message_id}/status")
    async def sandbox_status(message_id: str, body: dict[str, Any] = Body(...)):
        if not store.one("SELECT id FROM messages WHERE id=?", (message_id,)):
            return JSONResponse({"error": "message not found"}, status_code=404)
        if body.get("status") not in {"accepted", "sent", "delivered", "read", "failed"}:
            return JSONResponse({"error": "status must be accepted, sent, delivered, read, or failed"}, status_code=400)
        await engine.set_status(message_id, body["status"])
        return {"success": True}

    @app.get("/_sandbox/webhooks/stats")
    def sandbox_webhook_stats() -> dict[str, Any]:
        """Delivery counts by status, without shipping a single delivery body.

        The console header needs four numbers. It used to get them by counting
        an unbounded array of full deliveries in the browser, so painting the
        page cost every request body ever recorded.
        """
        counts = {row["status"]: row["total"] for row in store.all(
            "SELECT status, COUNT(*) AS total FROM webhook_deliveries GROUP BY status"
        )}
        return {
            "total": sum(counts.values()),
            "delivered": counts.get("delivered", 0),
            "failed": counts.get("failed", 0),
            "unrouted": counts.get("unrouted", 0),
            "pending": counts.get("pending", 0),
            "by_status": counts,
        }

    @app.get("/_sandbox/webhooks")
    def sandbox_webhooks(
        limit: int = Query(100, ge=1, le=500),
        before: str | None = Query(None, description="Delivery id to page backwards from."),
        status: str | None = Query(None, description="Only deliveries in this status."),
    ):
        """Newest deliveries first, paged the same way messages are.

        This endpoint used to return every delivery ever recorded and then run
        one more query per delivery for its attempts. Because Store.all opens a
        fresh SQLite connection per call, a few thousand deliveries meant a few
        thousand connection opens inside one request: it took minutes, and the
        console blocked on it during boot. Attempts are now fetched for the
        current page in a single query and grouped in memory.
        """
        anchor: tuple[str, int] | None = None
        if before:
            row = store.one("SELECT created_at, rowid FROM webhook_deliveries WHERE id=?", (before,))
            if not row:
                return JSONResponse({"error": "before cursor is not a known delivery id"}, status_code=400)
            anchor = (row["created_at"], row["rowid"])

        clauses: list[str] = []
        values: list[Any] = []
        if status:
            clauses.append("status=?")
            values.append(status)
        if anchor:
            clauses.append("(created_at, rowid) < (?, ?)")
            values.extend(anchor)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # One extra row answers "is there more?" without a second COUNT query.
        probe = limit + 1
        data = rows(store.all(
            "SELECT * FROM webhook_deliveries" + where
            + " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (*values, probe),
        ))
        has_more = len(data) > limit
        data = data[:limit]

        attempts_by_delivery: dict[str, list[dict[str, Any]]] = {}
        if data:
            placeholders = ",".join("?" for _ in data)
            for attempt in rows(store.all(
                f"SELECT * FROM webhook_attempts WHERE delivery_id IN ({placeholders})"
                " ORDER BY delivery_id, attempt_number",
                tuple(item["id"] for item in data),
            )):
                if isinstance(attempt.get("response_body"), bytes):
                    attempt["response_body"] = attempt["response_body"].decode("utf-8", errors="replace")
                attempts_by_delivery.setdefault(attempt["delivery_id"], []).append(attempt)

        for item in data:
            item.pop("rowid", None)
            try:
                item["request_body"] = json.loads(bytes(item["request_body"]))
            except (TypeError, ValueError):
                item["request_body"] = {}
            if isinstance(item.get("last_response_body"), bytes):
                item["last_response_body"] = item["last_response_body"].decode("utf-8", errors="replace")
            item["attempts"] = attempts_by_delivery.get(item["id"], [])
        return {
            "data": data,
            "has_more": has_more,
            "next_before": data[-1]["id"] if data and has_more else None,
        }

    @app.delete("/_sandbox/webhooks")
    def sandbox_webhooks_clear():
        """Drop the whole delivery log. Retained history is debugging noise once
        it is old, and a long-running sandbox accumulates a lot of it."""
        with store.connect() as db:
            removed = db.execute("SELECT COUNT(*) AS total FROM webhook_deliveries").fetchone()["total"]
            db.execute("DELETE FROM webhook_attempts")
            db.execute("DELETE FROM webhook_deliveries")
        return {"success": True, "deleted": removed}

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

    @app.websocket("/_sandbox/observer")
    async def observer_socket(websocket: WebSocket):
        """Every sandbox event, tagged with the wa_id it belongs to.

        Read-only on purpose: sending is still done over the per-customer
        socket, which knows which customer it is acting as. This one exists so a
        page can watch mobiles it is not currently acting as.
        """
        await websocket.accept()
        engine.observers.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            engine.observers.discard(websocket)

    @app.websocket("/_sandbox/clients/{wa_id}")
    async def client_socket(websocket: WebSocket, wa_id: str):
        await websocket.accept()
        wa_id = normalize_phone(wa_id)
        engine.listeners.setdefault(wa_id, set()).add(websocket)
        try:
            while True:
                payload = await websocket.receive_json()
                if payload.get("action") == "send":
                    message = await engine.receive_inbound(
                        payload.get("phone_number_id", "PHONE_LOCAL"),
                        wa_id,
                        payload.get("type", "text"),
                        payload.get("payload", {"body": ""}),
                        payload.get("context"),
                    )
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
            if body.get("messaging_product") != "whatsapp":
                return graph_error(131009, "messaging_product must be whatsapp.")
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
    def download_media(
        media_id: str,
        authorization: str | None = Header(default=None),
        access_token: str | None = Query(default=None),
    ):
        header_token = (authorization or "").removeprefix("Bearer ").strip()
        if not valid_access_token(header_token) and not valid_access_token(access_token or ""):
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
        if not store.one("SELECT id FROM business_accounts WHERE id=?", (waba_id,)):
            return graph_error(100, "WhatsApp Business Account does not exist.", status_code=404)
        if validation_error := validate_template(body):
            return graph_error(
                100,
                validation_error.details,
                error_subcode=validation_error.subcode,
                user_title=validation_error.title,
            )
        template_id = str(secrets.randbelow(9_000_000_000_000_000) + 1_000_000_000_000_000)
        now = store.now().isoformat()
        status = "APPROVED" if body.get("_sandbox_auto_approve") is True else "PENDING"
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
        if not store.one("SELECT id FROM business_accounts WHERE id=?", (waba_id,)):
            return graph_error(100, "WhatsApp Business Account does not exist.", status_code=404)
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
        # Re-subscribing the same callback replaces the previous registration.
        # Without this each call added another active row and every event was
        # delivered once per duplicate.
        store.execute(
            "UPDATE webhook_subscriptions SET active=0 WHERE waba_id=? AND callback_url=? AND active=1",
            (waba_id, callback),
        )
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
        if not engine.phone(phone_id):
            return graph_error(100, "Phone number ID does not exist.", status_code=404)
        if body.get("messaging_product") != "whatsapp":
            return graph_error(131009, "messaging_product must be whatsapp.")
        profile = {key: value for key, value in body.items() if key != "messaging_product"}
        store.execute("UPDATE phone_numbers SET profile_json=? WHERE id=?", (json.dumps(profile), phone_id))
        return {"success": True}

    return app


app = create_app()
