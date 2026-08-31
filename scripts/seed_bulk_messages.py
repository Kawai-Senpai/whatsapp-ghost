"""Fill a conversation with a lot of messages, to test loading behaviour.

The phone page used to fetch and re-render every message on every event, which
only hurts once a conversation is long. Reproducing that needs hundreds of
messages, and sending them over HTTP one at a time is far slower than writing
them where they end up anyway.

Usage:
    python scripts/seed_bulk_messages.py --count 1000
    python scripts/seed_bulk_messages.py --count 1000 --data-dir /tmp/ghost-perf
"""

from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BODIES = [
    "Your driver is on the way and will reach you shortly",
    "Trip confirmed. Your itinerary has been emailed to you",
    "Thanks, I can see it now",
    "Could you share the pickup point again?",
    "The cab is waiting at gate number 3",
    "Running about ten minutes late, sorry",
    "Your invoice for last week's trip is ready",
    "Perfect, see you then",
    "Please rate your last journey when you get a moment",
    "Booking amended as requested",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--data-dir", default=".whatsapp-ghost")
    parser.add_argument("--wa-id", default="15550002001")
    parser.add_argument("--phone-id", default="PHONE_LOCAL")
    args = parser.parse_args()

    database = Path(args.data_dir).expanduser().resolve() / "whatsapp-ghost.db"
    if not database.exists():
        raise SystemExit(f"No database at {database}. Start the server once first.")

    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)

    user = db.execute("SELECT wa_id FROM simulated_users WHERE wa_id=?", (args.wa_id,)).fetchone()
    if not user:
        db.execute(
            "INSERT INTO simulated_users(wa_id,display_name,online,blocked,created_at,color,auto_created)"
            " VALUES(?,?,?,?,?,?,?)",
            (args.wa_id, "Bulk Tester", 1, 0, now.isoformat(), "#7E57C2", 0),
        )

    row = db.execute(
        "SELECT id FROM conversations WHERE phone_number_id=? AND user_wa_id=?",
        (args.phone_id, args.wa_id),
    ).fetchone()
    if row:
        conversation_id = row["id"]
    else:
        conversation_id = "conv_" + uuid.uuid4().hex
        db.execute(
            "INSERT INTO conversations(id,phone_number_id,user_wa_id,created_at) VALUES(?,?,?,?)",
            (conversation_id, args.phone_id, args.wa_id, now.isoformat()),
        )

    # Spread backwards in time so the day separators and ordering are realistic
    # rather than every message sharing one timestamp.
    start = now - timedelta(minutes=args.count)
    for index in range(args.count):
        created = (start + timedelta(minutes=index)).isoformat()
        inbound = index % 3 == 0
        body = BODIES[index % len(BODIES)] + f" (#{index + 1})"
        message_id = "wamid." + secrets.token_urlsafe(24)
        if inbound:
            payload = {"from": args.wa_id, "id": message_id, "timestamp": "0",
                       "type": "text", "text": {"body": body}}
            sender, recipient, direction, status = args.wa_id, args.phone_id, "inbound", "delivered"
        else:
            payload = {"messaging_product": "whatsapp", "to": args.wa_id,
                       "type": "text", "text": {"body": body}}
            sender, recipient, direction, status = args.phone_id, args.wa_id, "outbound", "read"
        db.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (message_id, conversation_id, direction, sender, recipient, "text",
             json.dumps(payload), "v25.0", status, created, created),
        )

    db.execute(
        "UPDATE conversations SET last_user_message_at=?,service_window_expires_at=? WHERE id=?",
        (now.isoformat(), (now + timedelta(hours=24)).isoformat(), conversation_id),
    )
    db.commit()
    total = db.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (conversation_id,)
    ).fetchone()["n"]
    db.close()
    print(f"Inserted {args.count} messages. Conversation now holds {total}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
