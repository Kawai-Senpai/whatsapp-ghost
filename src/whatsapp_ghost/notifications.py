from __future__ import annotations

import sys


def notify(title: str, body: str, adapter: str = "bell") -> None:
    if adapter == "none":
        return
    if adapter == "desktop":
        try:
            from plyer import notification
            notification.notify(title=title, message=body, app_name="WhatsApp Ghost", timeout=5)
            return
        except Exception:
            pass
    if adapter in {"bell", "desktop"}:
        sys.stderr.write("\a")
        sys.stderr.flush()

