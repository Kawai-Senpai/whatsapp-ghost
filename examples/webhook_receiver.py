from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import PlainTextResponse, Response


APP_SECRET = "local-app-secret"
VERIFY_TOKEN = "local-verify-token"
app = FastAPI(title="WhatsApp Ghost example webhook receiver")


@app.get("/webhook", response_class=PlainTextResponse)
def verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return hub_challenge
    return PlainTextResponse("Forbidden", status_code=403)


@app.post("/webhook")
async def receive(request: Request, x_hub_signature_256: str | None = Header(default=None)):
    raw = await request.body()
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not x_hub_signature_256 or not hmac.compare_digest(expected, x_hub_signature_256):
        return Response(status_code=401)
    print(json.dumps(json.loads(raw), indent=2))
    return Response(status_code=200)
