# Test suite

This suite verifies behavior at four levels:

1. Pure contract and validation tests through FastAPI's in-process client.
2. Persistence tests that destroy and recreate the application against the same SQLite/media directory.
3. Network tests that run a real callback HTTP server and validate webhook challenge, signature, payload, response, failure, and replay behavior.
4. End-to-end UI tests that start a real Uvicorn process, drive headless Chromium, and operate the real Textual client.

## Modules

| Module | Primary coverage |
|---|---|
| `test_resources_and_auth.py` | Apps, tokens, WABAs, sender numbers, profiles, customer CRUD, errors |
| `test_messages_and_windows.py` | Strict validation, 24-hour windows, statuses, ticks, WebSockets, pair isolation, ordering |
| `test_media.py` | Uploads, MIME/size validation, bytes on disk, metadata, download auth, ownership, deletion |
| `test_templates.py` | CRUD, approval states, parameter counts, language/name errors, WABA isolation |
| `test_webhooks.py` | Verification, real HTTP delivery, HMAC, envelopes, attempts, failures, replay, unsubscribe |
| `test_persistence_clock_and_reset.py` | Restart durability, reset cleanup/reseed, time travel |
| `test_browser_e2e.py` | Console provisioning/guide, browser chat, image attachment, ordering, ticks, sender switching |
| `test_tui_e2e.py` | Real server discovery, sender switching, Textual message submission |
| `test_cli.py` | Setup, doctor, clock, reset, phone commands, URLs, errors |
| `test_ui_clients.py` | Packaged pages/assets and offline Textual composition |
| `test_notifications.py` | Silent, terminal bell, desktop, and fallback adapters |

## Run

```powershell
uv sync
uv run playwright install chromium
uv run pytest -q
uv run pytest --cov=whatsapp_ghost --cov-report=term-missing
```

The browser and TUI E2E modules use isolated temporary data directories and free localhost ports. They never touch `.whatsapp-ghost/` in the project directory.
