"""Fill a throwaway sandbox with millions of messages and time the UI endpoints.

    uv run python scripts/bench_large_sandbox.py --messages 2000000 --customers 5000

The database is built with bulk SQL rather than the API, so building 2M rows
takes seconds rather than hours. Re-running against an existing --data-dir
skips the build and only times the endpoints.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import random
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from whatsapp_ghost.api import create_app
from whatsapp_ghost.config import Settings


def build(path: Path, messages: int, customers: int, deliveries: int) -> None:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=OFF")
    now = datetime.now(timezone.utc)
    phone = db.execute("SELECT id FROM phone_numbers LIMIT 1").fetchone()[0]
    users = [(f"1555{i:07d}", f"Customer {i}", now.isoformat()) for i in range(customers)]
    db.executemany("INSERT OR IGNORE INTO simulated_users(wa_id,display_name,created_at) VALUES(?,?,?)", users)
    db.executemany(
        "INSERT OR IGNORE INTO conversations(id,phone_number_id,user_wa_id,created_at) VALUES(?,?,?,?)",
        [(f"conv_{u[0]}", phone, u[0], u[2]) for u in users],
    )
    payload = json.dumps({"type": "text", "text": {"body": "Your pickup is confirmed for 9:00 tomorrow."}})
    start = now - timedelta(days=90)
    step = timedelta(days=90) / max(messages, 1)
    batch = []
    for i in range(messages):
        user = users[random.randrange(customers)][0]
        outbound = i % 3 != 0
        at = (start + step * i).isoformat()
        status = random.choice(("delivered", "read", "read", "sent")) if outbound else "received"
        batch.append((
            f"wamid.bench{i}", f"conv_{user}", "outbound" if outbound else "inbound",
            phone if outbound else user, user if outbound else phone, "text", payload, "v25.0",
            status, at, at,
        ))
        if len(batch) == 50_000:
            db.executemany("INSERT INTO messages(id,conversation_id,direction,sender_id,recipient_id,"
                           "message_type,payload_json,api_version,status,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?)", batch)
            batch.clear()
    if batch:
        db.executemany("INSERT INTO messages(id,conversation_id,direction,sender_id,recipient_id,"
                       "message_type,payload_json,api_version,status,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)", batch)
    db.executemany(
        "INSERT INTO webhook_deliveries(id,event_type,request_body,signature,status,created_at,message_id) "
        "VALUES(?,?,?,?,?,?,?)",
        [(f"del_{i}", "messages", b"{}", "sig", "delivered", (start + step * i).isoformat(), f"wamid.bench{i}")
         for i in range(deliveries)],
    )
    db.commit()
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("tmp/bench"))
    parser.add_argument("--messages", type=int, default=2_000_000)
    parser.add_argument("--customers", type=int, default=5_000)
    parser.add_argument("--deliveries", type=int, default=20_000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env()
    settings = dataclasses.replace(settings, data_dir=args.data_dir)
    fresh = not settings.database_path.exists()
    with TestClient(create_app(settings)) as client:
        if fresh:
            began = time.perf_counter()
            build(settings.database_path, args.messages, args.customers, args.deliveries)
            print(f"built {args.messages:,} messages in {time.perf_counter() - began:.1f}s")
            client.get("/_sandbox/health")
        wa = "15550000042"
        phone = client.get("/_sandbox/config").json().get("phone_number_id", "")
        endpoints = [
            "/_sandbox/config", "/_sandbox/stats", "/_sandbox/phones", "/_sandbox/unread",
            "/_sandbox/conversations", f"/_sandbox/messages?wa_id={wa}&phone_number_id={phone}&limit=50",
            f"/_sandbox/messages?wa_id={wa}&limit=50", "/_sandbox/messages?limit=50",
            "/_sandbox/webhooks?limit=50", "/_sandbox/webhooks/stats", "/_sandbox/analytics",
            f"/_sandbox/phones/{wa}/analytics", "/v25.0/WABA_LOCAL/message_templates",
        ]
        print(f"{'endpoint':70} {'median ms':>10} {'bytes':>10}")
        for url in endpoints:
            samples, size = [], 0
            for _ in range(args.repeat):
                began = time.perf_counter()
                response = client.get(url, headers={"Authorization": f"Bearer {settings.access_token}"})
                samples.append((time.perf_counter() - began) * 1000)
                size = len(response.content)
            print(f"{url[:70]:70} {statistics.median(samples):10.1f} {size:10,}")


if __name__ == "__main__":
    main()
