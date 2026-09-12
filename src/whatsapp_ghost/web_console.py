"""Loads the local developer console and WhatsApp Web phone simulator assets.

The HTML/CSS/JS live as editable files under ``web/`` so the UI can be tweaked
without touching Python. ``console.html`` and ``phone.html`` reference the shared
stylesheet and scripts served from ``/static``.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB_DIR = Path(__file__).parent / "web"

_ASSET_REF = re.compile(r'(?P<attr>href|src)="(?P<path>/static/[^"?]+\.(?:css|js))"')


def _stamp(path: str) -> str:
    """A short version token for one static file, from its mtime and size."""
    try:
        stat = (WEB_DIR / path.removeprefix("/static/")).stat()
    except OSError:
        return "0"
    return f"{int(stat.st_mtime)}-{stat.st_size}"


def _version_assets(html: str) -> str:
    """Append a per-file version to every /static css and js reference.

    The HTML is re-read from disk on every request, but the stylesheet and
    scripts it points at are served by StaticFiles and cached by the browser.
    Editing the UI therefore produced the worst possible state: new markup
    against an old stylesheet and an old script, so the page rendered unstyled
    and half its behaviour was missing. Stamping the URL makes an edited file a
    different URL, which no cache can satisfy from an old copy.
    """
    return _ASSET_REF.sub(
        lambda m: f'{m.group("attr")}="{m.group("path")}?v={_stamp(m.group("path"))}"',
        html,
    )


def asset(name: str) -> str:
    """Return the text of a file in the web asset directory (read fresh each call)."""
    text = (WEB_DIR / name).read_text(encoding="utf-8")
    return _version_assets(text) if name.endswith(".html") else text


_LAZY = {"CONSOLE_HTML": "console.html", "PHONE_HTML": "phone.html"}


def __getattr__(name: str) -> str:
    """Serve CONSOLE_HTML/PHONE_HTML fresh from disk on every access.

    Reading at import time would freeze the markup for the life of the process,
    so edits to the web/ files would need a server restart to show up.
    """
    if name in _LAZY:
        return asset(_LAZY[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
