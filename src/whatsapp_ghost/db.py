from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .identity import generated_color


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS business_accounts (
  id TEXT PRIMARY KEY, business_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS developer_apps (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, app_secret TEXT NOT NULL,
  access_token TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phone_numbers (
  id TEXT PRIMARY KEY, waba_id TEXT NOT NULL REFERENCES business_accounts(id),
  display_phone_number TEXT NOT NULL UNIQUE, verified_name TEXT NOT NULL,
  quality_rating TEXT NOT NULL DEFAULT 'GREEN', registration_status TEXT NOT NULL DEFAULT 'REGISTERED',
  profile_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS simulated_users (
  wa_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, online INTEGER NOT NULL DEFAULT 1,
  blocked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  color TEXT, auto_created INTEGER NOT NULL DEFAULT 0,
  starred INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, phone_number_id TEXT NOT NULL, user_wa_id TEXT NOT NULL,
  last_user_message_at TEXT, service_window_expires_at TEXT, created_at TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0, pinned_at TEXT,
  UNIQUE(phone_number_id, user_wa_id)
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, direction TEXT NOT NULL,
  sender_id TEXT NOT NULL, recipient_id TEXT NOT NULL, message_type TEXT NOT NULL,
  payload_json TEXT NOT NULL, api_version TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, failure_code INTEGER
);
CREATE TABLE IF NOT EXISTS message_status_events (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL, status TEXT NOT NULL,
  timestamp TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY, waba_id TEXT NOT NULL, name TEXT NOT NULL, language TEXT NOT NULL,
  category TEXT NOT NULL, status TEXT NOT NULL, components_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(waba_id,name,language)
);
CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY, phone_number_id TEXT NOT NULL, mime_type TEXT NOT NULL,
  filename TEXT, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
  storage_path TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
  id TEXT PRIMARY KEY, waba_id TEXT NOT NULL, callback_url TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
  app_id TEXT, app_secret TEXT, verify_token TEXT
);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id TEXT PRIMARY KEY, event_type TEXT NOT NULL, destination_url TEXT,
  request_body BLOB NOT NULL, signature TEXT NOT NULL, status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0, last_status_code INTEGER,
  last_response_body BLOB, last_error TEXT, created_at TEXT NOT NULL, delivered_at TEXT,
  message_id TEXT
);
CREATE TABLE IF NOT EXISTS webhook_attempts (
  id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL, attempt_number INTEGER NOT NULL,
  requested_at TEXT NOT NULL, completed_at TEXT, status_code INTEGER,
  response_body BLOB, error TEXT
);
CREATE TABLE IF NOT EXISTS clock_state (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1), frozen_at TEXT
);
INSERT OR IGNORE INTO clock_state(singleton, frozen_at) VALUES(1, NULL);

-- Every list endpoint in the console orders by created_at DESC and joins on a
-- foreign key, and none of those columns were indexed: with a few thousand
-- messages and deliveries SQLite fell back to a full scan plus a temp B-tree
-- sort for each one, which is what made /console take minutes to paint.
-- IF NOT EXISTS means an existing database picks these up on the next start.
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient_id);
CREATE INDEX IF NOT EXISTS idx_messages_unread ON messages(direction, status);
CREATE INDEX IF NOT EXISTS idx_status_events_message ON message_status_events(message_id);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_wa_id, phone_number_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_created ON webhook_deliveries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deliveries_status ON webhook_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_attempts_delivery ON webhook_attempts(delivery_id, attempt_number);
CREATE INDEX IF NOT EXISTS idx_media_phone ON media(phone_number_id);
"""


#: 15-minute UTC slot a message falls in. 15 rather than 60 so a viewer at a
#: half- or quarter-hour offset (India +5:30, Nepal +5:45) still gets exact
#: local hour buckets out of the rollup.
_SLOT = ("strftime('%Y-%m-%dT%H:', {t}) || "
         "printf('%02d', (CAST(strftime('%M', {t}) AS INTEGER) / 15) * 15)")

# Every dashboard figure used to be a COUNT or GROUP BY over the whole messages
# table: 52 s for /_sandbox/analytics and 2.3 s for /_sandbox/unread at 2M
# messages, on every page load. These triggers keep per-conversation counters
# and a global 15-minute rollup current as messages are written, so reads cost
# the number of conversations or slots, not the number of messages.
COUNTER_TRIGGERS = f"""
CREATE TABLE IF NOT EXISTS traffic_rollup (
  bucket TEXT NOT NULL, direction TEXT NOT NULL, message_type TEXT NOT NULL,
  total INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(bucket, direction, message_type)
) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS trg_messages_counters_insert AFTER INSERT ON messages BEGIN
  UPDATE conversations SET
    message_count = message_count + 1,
    inbound_count = inbound_count + (NEW.direction = 'inbound'),
    outbound_count = outbound_count + (NEW.direction = 'outbound'),
    unread_count = unread_count + (NEW.direction = 'outbound' AND NEW.status IN ('accepted','sent','delivered')),
    last_message_at = CASE WHEN last_message_at IS NULL OR NEW.created_at > last_message_at
                           THEN NEW.created_at ELSE last_message_at END
  WHERE id = NEW.conversation_id;
  INSERT INTO traffic_rollup(bucket, direction, message_type, total)
    VALUES({_SLOT.format(t='NEW.created_at')}, NEW.direction, NEW.message_type, 1)
    ON CONFLICT(bucket, direction, message_type) DO UPDATE SET total = total + 1;
END;
CREATE TRIGGER IF NOT EXISTS trg_messages_counters_delete AFTER DELETE ON messages BEGIN
  UPDATE conversations SET
    message_count = message_count - 1,
    inbound_count = inbound_count - (OLD.direction = 'inbound'),
    outbound_count = outbound_count - (OLD.direction = 'outbound'),
    unread_count = unread_count - (OLD.direction = 'outbound' AND OLD.status IN ('accepted','sent','delivered')),
    last_message_at = (SELECT MAX(created_at) FROM messages WHERE conversation_id = OLD.conversation_id)
  WHERE id = OLD.conversation_id;
  UPDATE traffic_rollup SET total = total - 1
    WHERE bucket = {_SLOT.format(t='OLD.created_at')} AND direction = OLD.direction AND message_type = OLD.message_type;
END;
CREATE TRIGGER IF NOT EXISTS trg_messages_counters_status AFTER UPDATE OF status ON messages
WHEN OLD.status IS NOT NEW.status BEGIN
  UPDATE conversations SET unread_count = unread_count
    - (OLD.direction = 'outbound' AND OLD.status IN ('accepted','sent','delivered'))
    + (NEW.direction = 'outbound' AND NEW.status IN ('accepted','sent','delivered'))
  WHERE id = NEW.conversation_id;
END;
"""

# Per-customer totals, rolled up from the conversation counters, so the roster
# can be paged in "favourites, then most recent" order straight off an index
# instead of aggregating every conversation for every page.
USER_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS trg_conversations_user_totals
AFTER UPDATE OF message_count, unread_count, last_message_at ON conversations BEGIN
  UPDATE simulated_users SET
    unread_total = unread_total + NEW.unread_count - OLD.unread_count,
    message_total = message_total + NEW.message_count - OLD.message_count,
    last_activity_at = CASE
      WHEN NEW.last_message_at IS NOT NULL AND NEW.last_message_at > last_activity_at THEN NEW.last_message_at
      WHEN OLD.last_message_at IS NOT NULL AND OLD.last_message_at = last_activity_at
           AND (NEW.last_message_at IS NULL OR NEW.last_message_at < OLD.last_message_at)
        THEN COALESCE((SELECT MAX(last_message_at) FROM conversations WHERE user_wa_id = NEW.user_wa_id), '')
      ELSE last_activity_at END
  WHERE wa_id = NEW.user_wa_id;
END;
"""

_COUNTER_COLUMNS = ("message_count", "inbound_count", "outbound_count", "unread_count")


class Store:
    def __init__(self, path: Path):
        self.path = path
        # One long-lived connection per thread instead of a new one per query:
        # opening SQLite, re-reading the schema and re-applying pragmas on every
        # call was a fixed cost on every request. The generation lets reset()
        # retire every connection at once before it deletes the files.
        self._local = threading.local()
        self._lock = threading.Lock()
        self._generation = 0
        # Each connection is kept with the thread that owns it. Worker threads
        # come and go (anyio retires an idle pool thread after a few seconds),
        # and a connection whose thread has exited would otherwise stay open
        # forever, holding two file descriptors (db + wal) until the process
        # hits its open-files limit and every request fails with EMFILE.
        self._connections: list[tuple[threading.Thread, sqlite3.Connection]] = []

    def _reap_dead(self) -> None:
        """Close connections whose owning thread has exited."""
        with self._lock:
            dead = [entry for entry in self._connections if not entry[0].is_alive()]
            if dead:
                self._connections = [entry for entry in self._connections if entry[0].is_alive()]
        for _, connection in dead:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def open_connection_count(self) -> int:
        with self._lock:
            return len(self._connections)

    def _open(self) -> sqlite3.Connection:
        self._reap_dead()
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        for pragma in (
            "PRAGMA foreign_keys=ON",
            "PRAGMA synchronous=NORMAL",   # safe under WAL; one fsync per checkpoint
            "PRAGMA temp_store=MEMORY",
            # Private heap per connection, and there is one connection per
            # worker thread (up to 40), so this multiplies: at 64 MB a busy
            # server held 4 GB. Reads are served by mmap below, which is the
            # shared OS page cache, so a small private cache costs nothing.
            "PRAGMA cache_size=-8192",     # 8 MB page cache per connection
            "PRAGMA mmap_size=268435456",  # 256 MB memory-mapped reads
            "PRAGMA busy_timeout=10000",
        ):
            connection.execute(pragma)
        with self._lock:
            self._connections.append((threading.current_thread(), connection))
        return connection

    def close_all(self) -> None:
        """Close every connection so the database files can be deleted."""
        with self._lock:
            self._generation += 1
            connections, self._connections = self._connections, []
        for _, connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        local = self._local
        if getattr(local, "generation", None) != self._generation or getattr(local, "connection", None) is None:
            local.connection = self._open()
            local.generation = self._generation
            local.depth = 0
        connection = local.connection
        local.depth += 1
        try:
            yield connection
        except BaseException:
            local.depth -= 1
            if local.depth == 0:
                connection.rollback()
            raise
        else:
            local.depth -= 1
            # Nested use shares the outer transaction and commits with it.
            if local.depth == 0:
                connection.commit()

    def _migrate_counters(self, db: sqlite3.Connection) -> None:
        columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
        added = False
        for column in _COUNTER_COLUMNS:
            if column not in columns:
                db.execute(f"ALTER TABLE conversations ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
                added = True
        if "last_message_at" not in columns:
            db.execute("ALTER TABLE conversations ADD COLUMN last_message_at TEXT")
            added = True
        rollup_was_missing = not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='traffic_rollup'"
        ).fetchone()
        user_columns = {row[1] for row in db.execute("PRAGMA table_info(simulated_users)")}
        for column, ddl in (("unread_total", "INTEGER NOT NULL DEFAULT 0"),
                            ("message_total", "INTEGER NOT NULL DEFAULT 0"),
                            ("last_activity_at", "TEXT NOT NULL DEFAULT ''")):
            if column not in user_columns:
                db.execute(f"ALTER TABLE simulated_users ADD COLUMN {column} {ddl}")
                added = True
        db.executescript(COUNTER_TRIGGERS)
        db.executescript(USER_TRIGGERS)
        db.execute("CREATE INDEX IF NOT EXISTS idx_conversations_activity ON conversations(last_message_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_roster ON simulated_users(starred DESC, last_activity_at DESC, wa_id)")
        has_messages = db.execute("SELECT 1 FROM messages LIMIT 1").fetchone()
        # One pass over an existing database the first time it meets the
        # counters; every later write keeps them current through the triggers.
        if has_messages and (added or rollup_was_missing):
            db.execute(
                "UPDATE conversations SET (message_count, inbound_count, outbound_count, unread_count, last_message_at) = ("
                " SELECT COUNT(*), SUM(direction='inbound'), SUM(direction='outbound'),"
                " SUM(direction='outbound' AND status IN ('accepted','sent','delivered')), MAX(created_at)"
                " FROM messages m WHERE m.conversation_id = conversations.id)"
                " WHERE EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = conversations.id)"
            )
            db.execute("DELETE FROM traffic_rollup")
            db.execute(
                "INSERT INTO traffic_rollup(bucket, direction, message_type, total)"
                f" SELECT {_SLOT.format(t='created_at')} AS bucket, direction, message_type, COUNT(*)"
                " FROM messages GROUP BY bucket, direction, message_type"
            )
        if has_messages and added or rollup_was_missing and has_messages:
            # The conversation backfill above fired the per-user trigger with
            # deltas from whatever was there before, so set the totals outright.
            db.execute(
                "UPDATE simulated_users SET"
                " unread_total = COALESCE((SELECT SUM(unread_count) FROM conversations c WHERE c.user_wa_id = wa_id), 0),"
                " message_total = COALESCE((SELECT SUM(message_count) FROM conversations c WHERE c.user_wa_id = wa_id), 0),"
                " last_activity_at = COALESCE((SELECT MAX(last_message_at) FROM conversations c WHERE c.user_wa_id = wa_id), '')"
            )

    def initialize(self, access_token: str = "local-dev-token", app_secret: str = "local-app-secret") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            subscription_columns = {row[1] for row in db.execute("PRAGMA table_info(webhook_subscriptions)")}
            for column in ("app_id", "app_secret", "verify_token"):
                if column not in subscription_columns:
                    db.execute(f"ALTER TABLE webhook_subscriptions ADD COLUMN {column} TEXT")
            user_columns = {row[1] for row in db.execute("PRAGMA table_info(simulated_users)")}
            if "color" not in user_columns:
                db.execute("ALTER TABLE simulated_users ADD COLUMN color TEXT")
            if "auto_created" not in user_columns:
                db.execute("ALTER TABLE simulated_users ADD COLUMN auto_created INTEGER NOT NULL DEFAULT 0")
            # Favourites are stored rather than kept in browser storage so the
            # same numbers stay at the top in every tab and after a reload.
            if "starred" not in user_columns:
                db.execute("ALTER TABLE simulated_users ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
            # Backfill colors so pre-upgrade rows render like autocreated ones.
            for row in db.execute("SELECT wa_id FROM simulated_users WHERE color IS NULL OR color=''"):
                db.execute("UPDATE simulated_users SET color=? WHERE wa_id=?", (generated_color(row[0]), row[0]))
            conversation_columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
            if "pinned" not in conversation_columns:
                db.execute("ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            if "pinned_at" not in conversation_columns:
                db.execute("ALTER TABLE conversations ADD COLUMN pinned_at TEXT")
            delivery_columns = {row[1] for row in db.execute("PRAGMA table_info(webhook_deliveries)")}
            if "last_response_body" not in delivery_columns:
                db.execute("ALTER TABLE webhook_deliveries ADD COLUMN last_response_body BLOB")
            # Links a delivery back to the inbound message it carries, so the
            # message's own status can follow whether the business actually
            # received it. See Engine.receive_inbound.
            if "message_id" not in delivery_columns:
                db.execute("ALTER TABLE webhook_deliveries ADD COLUMN message_id TEXT")
            # Indexed here rather than in SCHEMA: SCHEMA runs first, and on a
            # database that predates the column CREATE TABLE IF NOT EXISTS is a
            # no-op, so indexing it up there fails startup with "no such column".
            db.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_message ON webhook_deliveries(message_id)")
            self._migrate_counters(db)
            now = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT OR IGNORE INTO business_accounts VALUES(?,?,?,?)", ("WABA_LOCAL", "BUSINESS_LOCAL", "Ghost Demo Business", now))
            db.execute(
                "INSERT OR IGNORE INTO developer_apps VALUES(?,?,?,?,?)",
                ("APP_LOCAL", "Ghost Demo App", app_secret, access_token, now),
            )
            db.execute(
                "INSERT OR IGNORE INTO phone_numbers(id,waba_id,display_phone_number,verified_name,created_at) VALUES(?,?,?,?,?)",
                ("PHONE_LOCAL", "WABA_LOCAL", "15550001000", "Ghost Demo", now),
            )
            db.execute(
                "INSERT OR IGNORE INTO simulated_users(wa_id,display_name,online,blocked,created_at,color,auto_created) VALUES(?,?,?,?,?,?,?)",
                ("15550002001", "Demo Customer", 1, 0, now, generated_color("15550002001"), 0),
            )
            components = json.dumps([{"type": "BODY", "text": "Hello {{1}}, welcome to WhatsApp Ghost!"}])
            db.execute(
                "INSERT OR IGNORE INTO templates VALUES(?,?,?,?,?,?,?,?,?)",
                ("TPL_HELLO", "WABA_LOCAL", "hello_world", "en_US", "UTILITY", "APPROVED", components, now, now),
            )

    def one(self, sql: str, values: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connect() as db:
            # Close the cursor: on a long-lived connection an unfinished
            # statement keeps its WAL read snapshot open, and every later read
            # on this thread would see the database as it was at that moment.
            cursor = db.execute(sql, values)
            try:
                return cursor.fetchone()
            finally:
                cursor.close()

    def all(self, sql: str, values: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(sql, values).fetchall()

    def execute(self, sql: str, values: tuple[Any, ...] = ()) -> None:
        with self.connect() as db:
            db.execute(sql, values)

    def now(self) -> datetime:
        row = self.one("SELECT frozen_at FROM clock_state WHERE singleton=1")
        if row and row["frozen_at"]:
            frozen = datetime.fromisoformat(row["frozen_at"])
            # A frozen value stored without an offset would be read back as
            # naive, and .timestamp() would then reinterpret it as local time,
            # writing wire timestamps hours away from the created_at beside it.
            return frozen if frozen.tzinfo else frozen.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)
