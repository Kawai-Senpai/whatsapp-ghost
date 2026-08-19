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


_LAZY = {"CONSOLE_HTML": "console.html", "PHONE_HTML": "phone.html"}


def __getattr__(name: str) -> str:
    """Serve CONSOLE_HTML/PHONE_HTML fresh from disk on every access.

    Reading at import time would freeze the markup for the life of the process,
    so edits to the web/ files would need a server restart to show up.
    """
    if name in _LAZY:
        return asset(_LAZY[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
