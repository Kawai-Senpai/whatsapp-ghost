from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


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
  blocked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, phone_number_id TEXT NOT NULL, user_wa_id TEXT NOT NULL,
  last_user_message_at TEXT, service_window_expires_at TEXT, created_at TEXT NOT NULL,
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
  last_response_body BLOB, last_error TEXT, created_at TEXT NOT NULL, delivered_at TEXT
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
"""


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self, access_token: str = "local-dev-token", app_secret: str = "local-app-secret") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            subscription_columns = {row[1] for row in db.execute("PRAGMA table_info(webhook_subscriptions)")}
            for column in ("app_id", "app_secret", "verify_token"):
                if column not in subscription_columns:
                    db.execute(f"ALTER TABLE webhook_subscriptions ADD COLUMN {column} TEXT")
            delivery_columns = {row[1] for row in db.execute("PRAGMA table_info(webhook_deliveries)")}
            if "last_response_body" not in delivery_columns:
                db.execute("ALTER TABLE webhook_deliveries ADD COLUMN last_response_body BLOB")
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
            db.execute("INSERT OR IGNORE INTO simulated_users VALUES(?,?,?,?,?)", ("15550002001", "Demo Customer", 1, 0, now))
            components = json.dumps([{"type": "BODY", "text": "Hello {{1}}, welcome to WhatsApp Ghost!"}])
            db.execute(
                "INSERT OR IGNORE INTO templates VALUES(?,?,?,?,?,?,?,?,?)",
                ("TPL_HELLO", "WABA_LOCAL", "hello_world", "en_US", "UTILITY", "APPROVED", components, now, now),
            )

    def one(self, sql: str, values: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute(sql, values).fetchone()

    def all(self, sql: str, values: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(sql, values).fetchall()

    def execute(self, sql: str, values: tuple[Any, ...] = ()) -> None:
        with self.connect() as db:
            db.execute(sql, values)

    def now(self) -> datetime:
        row = self.one("SELECT frozen_at FROM clock_state WHERE singleton=1")
        if row and row["frozen_at"]:
            return datetime.fromisoformat(row["frozen_at"])
        return datetime.now(timezone.utc)
