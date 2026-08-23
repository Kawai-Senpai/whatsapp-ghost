from __future__ import annotations

import secrets

from fastapi.responses import JSONResponse


ERRORS = {
    100: "Invalid parameter",
    190: "Invalid OAuth access token",
    131008: "Required parameter is missing",
    131009: "Parameter value is not valid",
    131026: "Message undeliverable",
    131047: "Re-engagement message",
    131051: "Unsupported message type",
    131052: "Media download error",
    131053: "Media upload error",
    132000: "Template parameter count mismatch",
    132001: "Template name does not exist in the translation",
    132015: "Template is paused",
    132016: "Template is disabled",
}


def graph_error(
    code: int,
    details: str,
    *,
    status_code: int = 400,
    error_subcode: int | None = None,
    user_title: str | None = None,
) -> JSONResponse:
    title = ERRORS.get(code, "Request failed")
    error = {
        "message": f"(#{code}) {title}",
        "type": "OAuthException",
        "code": code,
        "is_transient": False,
        "error_user_title": user_title or title,
        "error_user_msg": details,
        "error_data": {"messaging_product": "whatsapp", "details": details},
        "fbtrace_id": "LOCAL_" + secrets.token_hex(8).upper(),
    }
    if error_subcode is not None:
        error["error_subcode"] = error_subcode
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
    )
