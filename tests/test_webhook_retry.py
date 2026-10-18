"""A transiently failed webhook is retried, like Meta does, instead of being lost.

On dev, Ghost ran out of file descriptors and every webhook failed with
"ConnectError: Too many open files"; the customer replies they carried never
reached the business, and deliveries in flight at a restart stayed pending
forever.
"""
from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

from conftest import CallbackRecorder
from test_webhooks import subscribe, wait_for_delivery, wait_for_posts


def _send(client: TestClient) -> str:
    client.post("/_sandbox/phones", json={"wa_id": "15550002101", "display_name": "R"})
    message = client.post("/_sandbox/phones/15550002101/messages", json={
        "phone_number_id": "PHONE_LOCAL", "type": "text", "text": {"body": "hello"},
    }).json()
    return next(item["id"] for item in client.get("/_sandbox/webhooks").json()["data"]
                if item["message_id"] == message["id"])


def test_a_connection_error_is_retried_until_it_lands(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder, monkeypatch,
) -> None:
    subscribe(client, headers, callback)
    engine = client.app.state.engine
    real_http = engine.http

    class Broken:
        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("[Errno 24] Too many open files")

    monkeypatch.setattr(engine, "http", Broken())
    delivery_id = _send(client)
    failed = wait_for_delivery(client, delivery_id, "failed")
    assert "Too many open files" in failed["last_error"]
    row = engine.store.one("SELECT next_attempt_at FROM webhook_deliveries WHERE id=?", (delivery_id,))
    assert row["next_attempt_at"], "a transient failure is scheduled for retry"

    # The receiver is reachable again and the retry falls due.
    monkeypatch.setattr(engine, "http", real_http)
    engine.store.execute("UPDATE webhook_deliveries SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE id=?", (delivery_id,))
    retried = asyncio.run_coroutine_threadsafe(engine.retry_due_deliveries(), engine.loop).result(5)
    assert retried == 1
    wait_for_delivery(client, delivery_id, "delivered")
    wait_for_posts(callback, 1)


def test_a_rejected_webhook_is_not_retried(
    client: TestClient, headers: dict[str, str], callback: CallbackRecorder, monkeypatch,
) -> None:
    subscribe(client, headers, callback)
    engine = client.app.state.engine

    class Rejecting:
        async def post(self, url, **kwargs):
            return httpx.Response(400, text="bad payload", request=httpx.Request("POST", url))

    monkeypatch.setattr(engine, "http", Rejecting())
    delivery_id = _send(client)
    wait_for_delivery(client, delivery_id, "failed")
    row = engine.store.one("SELECT next_attempt_at FROM webhook_deliveries WHERE id=?", (delivery_id,))
    assert row["next_attempt_at"] is None


def test_customer_messages_left_in_flight_by_a_dead_process_are_resumed(client: TestClient) -> None:
    engine = client.app.state.engine
    now = engine._real_now().isoformat()
    for delivery_id, message_id in (("whd_reply", "wamid.reply"), ("whd_receipt", None)):
        engine.store.execute(
            "INSERT INTO webhook_deliveries(id,event_type,destination_url,request_body,signature,status,attempt_count,created_at,message_id) "
            "VALUES(?,'messages','http://127.0.0.1:9/x',x'7b7d','sig','pending',0,?,?)",
            (delivery_id, now, message_id),
        )
    engine.resume_interrupted_deliveries()
    resumed = {row["id"]: row["next_attempt_at"] for row in engine.store.all(
        "SELECT id, next_attempt_at FROM webhook_deliveries WHERE id IN ('whd_reply','whd_receipt')")}
    # The customer's reply is retried; a stale status receipt is not replayed.
    assert resumed["whd_reply"]
    assert resumed["whd_receipt"] is None
    receipt = engine.store.one("SELECT status, last_error FROM webhook_deliveries WHERE id='whd_receipt'")
    assert receipt["status"] == "failed" and "restarted" in receipt["last_error"]
