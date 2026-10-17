"""The trigger-maintained counters must always equal a recount of messages.

Dashboard reads (unread badges, stats, analytics) come from counters that
SQLite triggers keep current instead of scanning the messages table. A counter
that drifts would show a wrong badge forever, so every write path that touches
messages is exercised here and checked against a brute-force recount.
"""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from whatsapp_ghost.config import Settings

from conftest import open_window as _open_window, text_message


def open_window(client: TestClient, customer: str) -> None:
    client.post("/_sandbox/phones", json={"wa_id": customer})
    _open_window(client, customer)


def _recount(settings: Settings) -> tuple[list[tuple], list[tuple]]:
    db = sqlite3.connect(settings.database_path)
    counters = db.execute(
        "SELECT id, message_count, inbound_count, outbound_count, unread_count, last_message_at"
        " FROM conversations ORDER BY id"
    ).fetchall()
    truth = db.execute(
        "SELECT c.id, COUNT(m.id), COALESCE(SUM(m.direction='inbound'),0), COALESCE(SUM(m.direction='outbound'),0),"
        " COALESCE(SUM(m.direction='outbound' AND m.status IN ('accepted','sent','delivered')),0), MAX(m.created_at)"
        " FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id GROUP BY c.id ORDER BY c.id"
    ).fetchall()
    rollup = db.execute(
        "SELECT direction, message_type, SUM(total) FROM traffic_rollup GROUP BY direction, message_type"
        " HAVING SUM(total) > 0 ORDER BY 1, 2"
    ).fetchall()
    raw = db.execute(
        "SELECT direction, message_type, COUNT(*) FROM messages GROUP BY direction, message_type ORDER BY 1, 2"
    ).fetchall()
    db.close()
    assert counters == truth
    assert rollup == raw
    return counters, rollup


def test_counters_follow_sends_reads_and_clears(client: TestClient, headers: dict[str, str], settings: Settings) -> None:
    for customer in ("15550009001", "15550009002"):
        open_window(client, customer)
        for body in ("one", "two", "three"):
            assert client.post("/v25.0/PHONE_LOCAL/messages", headers=headers,
                               json=text_message(to=customer, body=body)).status_code == 200
    _recount(settings)

    client.post("/_sandbox/phones/15550009001/read", json={"phone_number_id": "PHONE_LOCAL"})
    counters, _ = _recount(settings)
    assert any(row[4] == 0 and row[1] > 0 for row in counters)

    client.delete("/_sandbox/phones/15550009002/messages")
    counters, _ = _recount(settings)
    assert any(row[1] == 0 and row[5] is None for row in counters)


def test_dashboard_reads_match_a_full_recount(client: TestClient, headers: dict[str, str]) -> None:
    open_window(client, "15550009003")
    for _ in range(4):
        client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message(to="15550009003"))
    analytics = client.get("/_sandbox/analytics").json()
    stats = client.get("/_sandbox/stats").json()
    assert analytics["total"] == stats["messages"] == analytics["inbound"] + analytics["outbound"]
    assert sum(analytics["by_hour"]) == analytics["total"]
    assert sum(day["total"] for day in analytics["by_day"]) == analytics["total"]
    pair = next(p for p in analytics["pairs"] if p["wa_id"] == "15550009003")
    assert pair["outbound"] == 4


def test_existing_database_is_backfilled_once(settings: Settings, client: TestClient, headers: dict[str, str]) -> None:
    open_window(client, "15550009004")
    client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message(to="15550009004"))
    # Simulate a database written before the counters existed.
    db = sqlite3.connect(settings.database_path)
    db.execute("UPDATE conversations SET message_count=0, inbound_count=0, outbound_count=0, unread_count=0, last_message_at=NULL")
    db.execute("DROP TABLE traffic_rollup")
    db.commit()
    db.close()
    client.app.state.store.initialize(settings.access_token, settings.app_secret)
    _recount(settings)


def test_customer_totals_and_paged_roster(client: TestClient, headers: dict[str, str], settings: Settings) -> None:
    customers = [f"155500091{i:02d}" for i in range(7)]
    for index, customer in enumerate(customers):
        open_window(client, customer)
        for _ in range(index):
            client.post("/v25.0/PHONE_LOCAL/messages", headers=headers, json=text_message(to=customer))
    client.patch(f"/_sandbox/phones/{customers[0]}", json={"starred": True})

    db = sqlite3.connect(settings.database_path)
    totals = db.execute("SELECT wa_id, unread_total, message_total, last_activity_at FROM simulated_users ORDER BY wa_id").fetchall()
    truth = db.execute(
        "SELECT u.wa_id, COALESCE(SUM(c.unread_count),0), COALESCE(SUM(c.message_count),0), COALESCE(MAX(c.last_message_at),'')"
        " FROM simulated_users u LEFT JOIN conversations c ON c.user_wa_id=u.wa_id GROUP BY u.wa_id ORDER BY u.wa_id"
    ).fetchall()
    db.close()
    assert totals == truth

    seen, cursor = [], None
    while True:
        page = client.get("/_sandbox/phones", params={"limit": 3, **({"after": cursor} if cursor else {})}).json()
        seen += [row["wa_id"] for row in page["data"]]
        if not page["has_more"]:
            break
        cursor = page["next_after"]
    everyone = [row["wa_id"] for row in client.get("/_sandbox/phones").json()["data"]]
    assert sorted(seen) == sorted(everyone) and len(seen) == len(set(seen))
    assert seen[0] == customers[0]  # favourites first
    found = client.get("/_sandbox/phones", params={"limit": 10, "q": customers[3][-4:]}).json()["data"]
    assert [row["wa_id"] for row in found] == [customers[3]]
    mine = client.get("/_sandbox/unread", params={"wa_id": customers[4]}).json()
    assert {row["wa_id"] for row in mine["data"]} == {customers[4]}
