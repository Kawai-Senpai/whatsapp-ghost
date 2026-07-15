from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from whatsapp_ghost.api import create_app
from whatsapp_ghost.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path, "http://testserver", "token", "secret", "verify", "strict", 0.001, "none")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def headers() -> dict[str, str]:
    return {"Authorization": "Bearer token"}


@pytest.fixture
def slow_settings(settings: Settings) -> Settings:
    return replace(settings, status_delay_seconds=60)


def open_window(client: TestClient, customer: str = "15550002001", phone_id: str = "PHONE_LOCAL") -> dict[str, Any]:
    response = client.post(
        f"/_sandbox/phones/{customer}/messages",
        json={"type": "text", "text": "Hello business", "phone_number_id": phone_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def text_message(to: str = "15550002001", body: str = "Hello") -> dict[str, Any]:
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}


class CallbackRecorder:
    def __init__(self) -> None:
        self.gets: list[dict[str, list[str]]] = []
        self.posts: list[dict[str, Any]] = []
        self.response_status = 200
        self.response_body = b'{"received":true}'
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                query = parse_qs(urlparse(self.path).query)
                recorder.gets.append(query)
                challenge = query.get("hub.challenge", [""])[0]
                body = challenge.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                size = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(size)
                recorder.posts.append({
                    "raw": raw,
                    "json": json.loads(raw),
                    "signature": self.headers.get("X-Hub-Signature-256"),
                })
                self.send_response(recorder.response_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(recorder.response_body)))
                self.end_headers()
                self.wfile.write(recorder.response_body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}/webhook"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def callback() -> Iterator[CallbackRecorder]:
    recorder = CallbackRecorder()
    try:
        yield recorder
    finally:
        recorder.close()
