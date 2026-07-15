"""Loads the local developer console and WhatsApp Web phone simulator assets.

The HTML/CSS/JS live as editable files under ``web/`` so the UI can be tweaked
without touching Python. ``console.html`` and ``phone.html`` reference the shared
stylesheet and scripts served from ``/static``.
"""

from __future__ import annotations

from pathlib import Path

WEB_DIR = Path(__file__).parent / "web"


def asset(name: str) -> str:
    """Return the text of a file in the web asset directory (read fresh each call)."""
    return (WEB_DIR / name).read_text(encoding="utf-8")


CONSOLE_HTML = asset("console.html")
PHONE_HTML = asset("phone.html")
