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


def graph_error(code: int, details: str, *, status_code: int = 400) -> JSONResponse:
    title = ERRORS.get(code, "Request failed")
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": f"(#{code}) {title}",
                "type": "OAuthException",
                "code": code,
                "error_data": {"messaging_product": "whatsapp", "details": details},
                "fbtrace_id": "LOCAL_" + secrets.token_hex(8).upper(),
            }
        },
    )

