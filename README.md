<div align="center">

# 👻 WhatsApp Ghost

**A persistent local sandbox for the WhatsApp Cloud API**

Build, test, and debug WhatsApp integrations without contacting real users or waiting on external services.

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-22C55E?style=flat-square)](pyproject.toml)
[![API: v25.0](https://img.shields.io/badge/Cloud_API-v25.0-25D366?style=flat-square)](contracts/v25.0/README.md)

[Quick start](#quick-start) · [Connect your app](#connect-your-app) · [Webhooks](#webhooks) · [Configuration](#configuration) · [Development](#development)

</div>

---

WhatsApp Ghost imitates the documented WhatsApp Cloud API contracts that developers commonly integrate with. Change only the hostname in your application:

```text
https://graph.facebook.com  →  http://127.0.0.1:8787
```

> [!IMPORTANT]
> WhatsApp Ghost is an independent development tool. It is not WhatsApp, does not connect to WhatsApp users, and does not implement Meta's private protocols.

## 🖼️ Screenshots

The developer console and the WhatsApp Web simulator, both served from the
container:

<table>
<tr>
<td width="50%"><a href="assets/screenshots/console-dashboard.png"><img src="assets/screenshots/console-dashboard.png" alt="Developer console dashboard" width="100%"></a><br><sub><b>Dashboard</b> · resources, drop-in endpoint and setup tasks</sub></td>
<td width="50%"><a href="assets/screenshots/console-credentials.png"><img src="assets/screenshots/console-credentials.png" alt="Credentials page" width="100%"></a><br><sub><b>Credentials</b> · every ID, token and secret in one place</sub></td>
</tr>
<tr>
<td><a href="assets/screenshots/console-webhooks.png"><img src="assets/screenshots/console-webhooks.png" alt="Webhooks page" width="100%"></a><br><sub><b>Webhooks</b> · signed deliveries, attempts and replay</sub></td>
<td><a href="assets/screenshots/console-templates.png"><img src="assets/screenshots/console-templates.png" alt="Message templates" width="100%"></a><br><sub><b>Templates</b> · approved local templates with variables</sub></td>
</tr>
<tr>
<td colspan="2"><a href="assets/screenshots/phone-simulator.png"><img src="assets/screenshots/phone-simulator.png" alt="Phone simulator" width="100%"></a><br><sub><b>Phone simulator</b> · a real conversation with inbound media, delivery ticks and live WebSocket updates</sub></td>
</tr>
</table>

## ✨ What you get

| | Capability | What it lets you test |
|:--:|---|---|
| 💬 | **Messages** | Text, media, templates, replies, and realistic `wamid` values |
| 📱 | **Phone simulators** | Customer conversations in the browser or terminal |
| 🪝 | **Webhooks** | Signed payloads, delivery history, inspection, and replay |
| ✅ | **Delivery lifecycle** | `sent`, `delivered`, `read`, and `failed` status transitions |
| 🧩 | **Accounts and senders** | Local apps, WABAs, tokens, and multiple business numbers |
| ⏰ | **Time travel** | Test the 24-hour customer-service window in seconds |
| 💾 | **Persistent state** | Shared SQLite state across the API, console, and phones |
| 🎛️ | **Testing modes** | Strict and loose validation for different integration stages |
| 🛡️ | **Template validation** | Meta's own shape rules, checked at submission |
| 📡 | **Live console** | New messages appear without a refresh, across every phone |

```mermaid
flowchart LR
    A[Your application] -->|Cloud API requests| G[👻 WhatsApp Ghost]
    P[📱 Simulated customer] <-->|Messages| G
    G -->|Signed events| W[Your webhook]
    G --- D[(SQLite state)]
```

<a id="quick-start"></a>

## 🚀 Quick start

Install [uv](https://docs.astral.sh/uv/) and run this from the project folder:

```powershell
uv sync
uv run ghost.py start
```

No configuration is required. Once the server is running, choose an interface:

| Destination | URL | Purpose |
|---|---|---|
| Developer console | [localhost:8787](http://127.0.0.1:8787) | Manage local apps, WABAs, senders, templates, webhooks, and time |
| Integration guide | [localhost:8787/guide](http://127.0.0.1:8787/guide) | Follow live, copyable examples |
| API reference | [localhost:8787/docs](http://127.0.0.1:8787/docs) | Explore and call the API interactively |
| Phone simulator | [localhost:8787/phone](http://127.0.0.1:8787/phone) | Chat as a simulated customer |

Or open the Textual phone in another terminal:

```powershell
uv run ghost.py phone open 15550002001
```

<details>
<summary><strong>Default local identities</strong></summary>

<br>

| Resource | Value |
|---|---|
| Access token | `local-dev-token` |
| App secret | `local-app-secret` |
| Verify token | `local-verify-token` |
| Business | `BUSINESS_LOCAL` |
| WABA | `WABA_LOCAL` |
| Phone number ID | `PHONE_LOCAL` |
| Business number | `15550001000` |
| Simulated customer | `15550002001` |
| API version used in examples | `v25.0` |

</details>

Run `uv run ghost.py doctor` if startup does not work. An installed `waba` command is also provided, but `ghost.py` works on Windows machines whose application-control policy blocks generated command launchers.

The web console and terminal phone share the same SQLite state, so a conversation started in either interface appears in both. The visual language is WhatsApp-inspired and internally branded as Ghost; it is intentionally not a copy of Meta's site or trademarks.

<a id="connect-your-app"></a>

## 🐳 Docker

Run the sandbox in a container, locally or on a shared server:

```bash
docker compose up -d --build
```

That is all it needs on your own machine. The console is on
[localhost:8787](http://127.0.0.1:8787), the database and uploaded media live in
the `ghost-data` volume, and the container restarts with Docker.

### Deploying to a server

Media download URLs are absolute, and they are built from `WABA_BASE_URL`. Set
it to the address your clients actually use, otherwise they receive
`http://127.0.0.1:8787/...` and try to download the file from themselves.

Create a `.env` next to `docker-compose.yml`:

```ini
WABA_BASE_URL=http://203.0.113.10:8787   # or https://ghost.example.com
WABA_ACCESS_TOKEN=pick-any-token
WABA_APP_SECRET=pick-any-secret
WABA_VERIFY_TOKEN=pick-any-verify-token
```

Then `docker compose up -d`. Every value has a working default, so you only need
the ones you want to change, but on a server `WABA_BASE_URL` is not optional.

`WABA_PORT` changes the published host port (`WABA_PORT=9000`) while the
container keeps listening on 8787 internally.

### Behind a reverse proxy

A complete, syntax-checked nginx config is in
[`examples/nginx/whatsapp-ghost.conf`](examples/nginx/whatsapp-ghost.conf).
It covers a dedicated hostname, a commented TLS block, a bare `IP:port`
deployment, and mounting Ghost under a subpath such as `/wa/`.

The three things that actually break when proxying:

| Problem | Fix |
|---|---|
| Phone simulator reconnects every 60s | Pass the `Upgrade`/`Connection` headers and raise `proxy_read_timeout` on `/_sandbox/clients/` |
| Media upload fails with `413` | Raise `client_max_body_size` (the example uses `32m`) |
| Your app downloads media from `127.0.0.1` and fails | Set `WABA_BASE_URL` to the public origin |

That last one catches people out: `GET /{version}/{media-id}` returns a JSON
body whose `url` field is built from `WABA_BASE_URL`, **not** from the incoming
request, so proxying alone does not fix it. Verify with:

```bash
curl -s -H "Authorization: Bearer $TOKEN"      https://ghost.example.com/v25.0/<media-id> | jq -r .url
```

The printed URL must be your public origin. If it still shows
`http://127.0.0.1:8787`, `WABA_BASE_URL` is unset.

> [!NOTE]
> Under a subpath (`/wa/`), the Cloud API, the sandbox routes and WebSockets
> all work, but the **browser console UI does not**: its HTML and JavaScript
> request root-absolute paths (`/static/...`, `/_sandbox/...`) that 404 behind
> the prefix. Use a subpath for backend integration only; give Ghost its own
> hostname or port if people need the console.

> **This is a test server with no real authentication.** Any caller holding the
> token can send messages and read history, so keep it on a private network or
> behind your own access control rather than open to the internet.

## 🔌 Connect your app

Keep your existing Cloud API code and change its configuration:

```text
META_GRAPH_BASE_URL=http://127.0.0.1:8787
WHATSAPP_API_VERSION=v25.0
WHATSAPP_PHONE_NUMBER_ID=PHONE_LOCAL
WHATSAPP_ACCESS_TOKEN=local-dev-token
WHATSAPP_APP_SECRET=local-app-secret
```

A normal request to `https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages` therefore becomes `http://127.0.0.1:8787/v25.0/PHONE_LOCAL/messages`; the path, headers, request JSON, response parsing, and `wamid` handling remain the same.

To test your project's webhook without commands:

1. Start your project and expose its local callback, such as `http://127.0.0.1:3000/webhook`.
2. Open **Webhooks** in the Ghost console, select the WABA, enter the callback and your project's verify token, then click **Verify and subscribe**.
3. Open **Phone Simulator**, select or create a customer, and send a message.
4. Your project receives the inbound message payload. Business replies sent by your project produce `sent`, `delivered`, `read`, or `failed` status payloads at the same callback.
5. Inspect signatures and raw payloads—or replay a delivery—from the console's Webhook Inspector.

The phone selector lets you switch among all simulated customers. You may also open the console in several browser windows or use separate Textual terminals for simultaneous users.

### Multiple sender numbers

One WABA can own multiple business sender numbers. In **API Setup & Numbers**, create a business and then use **Add sender** on its card for each additional number. Retrieve all of them through the compatible route:

```http
GET /v25.0/{WABA_ID}/phone_numbers
```

Choose the sender by placing its phone-number ID in the message URL: `POST /v25.0/{PHONE_NUMBER_ID}/messages`. There is no `from` field. Templates and webhook subscription are shared at WABA scope; sender identity, message history, ticks, and the 24-hour window are isolated for each sender/customer pair. Ghost does not impose Meta account-tier number quotas on local test rows.

## 🧪 Your first complete conversation

The sandbox starts in strict mode. Like the real platform, a free-form business message is rejected until the customer has opened the 24-hour service window. Send a customer message from the Textual phone, or use:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8787/_sandbox/phones/15550002001/messages `
  -ContentType application/json `
  -Body '{"type":"text","text":"Hello from my phone"}'
```

Now use the same Cloud API request your application would use:

```powershell
$headers = @{ Authorization = "Bearer local-dev-token" }
$body = @{
  messaging_product = "whatsapp"; recipient_type = "individual"
  to = "15550002001"; type = "text"
  text = @{ body = "Hello from the business"; preview_url = $false }
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8787/v25.0/PHONE_LOCAL/messages `
  -Headers $headers -ContentType application/json -Body $body
```

The response contains a `wamid.*` ID. The message progresses through `accepted → sent → delivered → read`; opening the conversation in a simulated phone marks delivered business messages read and emits the corresponding status webhook. Customer-side bubbles show single, double-grey, double-blue, and failure indicators from the stored status.

Outside the service window, the seeded approved template works:

```json
{
  "messaging_product": "whatsapp",
  "to": "15550002001",
  "type": "template",
  "template": {
    "name": "hello_world",
    "language": {"code": "en_US"},
    "components": [{"type": "body", "parameters": [{"type": "text", "text": "Ranit"}]}]
  }
}
```

<a id="webhooks"></a>

## 🪝 Webhooks

Run the included receiver in a separate terminal:

```powershell
uv run uvicorn examples.webhook_receiver:app --port 9000
```

Subscribe its callback:

```powershell
$headers = @{ Authorization = "Bearer local-dev-token" }
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8787/v25.0/WABA_LOCAL/subscribed_apps `
  -Headers $headers -ContentType application/json `
  -Body '{"callback_url":"http://127.0.0.1:9000/webhook"}'
```

Webhook bodies use the documented `whatsapp_business_account → entry → changes → value` envelope. `X-Hub-Signature-256` is an HMAC-SHA256 digest of the exact raw body using `local-app-secret`. Every event is stored before delivery. The web inspector retains every replay attempt with timestamps, HTTP status, response/error, destination, signature, and syntax-highlighted request JSON. Inspect from the console or with `uv run ghost.py webhooks list`.

The standard verification endpoint is `GET /webhook` with `hub.mode`, `hub.verify_token`, and `hub.challenge` parameters.

## 🖼️ Media and templates

Supported media flow:

1. `POST /v25.0/PHONE_LOCAL/media` as multipart form with `messaging_product=whatsapp` and `file`.
2. `GET /v25.0/{media-id}` to receive metadata and a temporary-style local URL.
3. `GET /_sandbox/media/{media-id}` with the Bearer token to download bytes.
4. `DELETE /v25.0/{media-id}`.

Outbound messages accept image, video, audio, document, and sticker references by uploaded `id` or external `link`. The Textual client renders useful attachment placeholders.

Template routes:

```text
GET    /v25.0/{waba-id}/message_templates
POST   /v25.0/{waba-id}/message_templates
GET    /v25.0/{template-id}
DELETE /v25.0/{template-id}
DELETE /v25.0/{waba-id}/message_templates?name={name}
```

Like Meta, newly submitted local templates return `PENDING` by default. Send
`"_sandbox_auto_approve": true` during creation when a test needs immediate
approval; that underscore-prefixed field is a simulator-only extension.

### Template validation

Submissions are validated against Meta's documented shape before they are
accepted, so a template that Ghost rejects would have been rejected upstream.
A failure returns a Meta-style error naming the offending component.

**Body**

- text at most 1024 characters
- parameters require an `example`, nested as `example.body_text` = `[[...]]`
- the example count must match the number of placeholders
- named parameters require `body_text_named_params`, matching the text exactly

**Header**

- text at most 60 characters, and at most **one** parameter
- a parameterized text header needs exactly one example
- `IMAGE` / `VIDEO` / `DOCUMENT` headers need one uploaded `header_handle` and
  no text
- `LOCATION` headers carry no text

**Footer**

- at most 60 characters, and no parameters at all

**Buttons**

- between 1 and 10 buttons, each an object with a 1-25 character label
- at most **3 quick replies**, **2 URL** and **1 phone number**
- quick-reply and call-to-action buttons cannot be interleaved: group them
- a URL button supports at most one parameter, and a dynamic URL needs one flat
  example value
- `PHONE_NUMBER` buttons require `phone_number`

**Parameter values at send time** cannot contain newlines, tabs, or more than
four consecutive spaces. This mirrors Meta and is the usual cause of a send
that looks well-formed but is refused.

## ⏱️ Time travel and multiple phones

```powershell
uv run ghost.py clock show
uv run ghost.py clock advance 25h
uv run ghost.py clock set 2026-07-15T10:00:00Z
uv run ghost.py clock reset
uv run ghost.py phone create 15550002002 --name Alice
uv run ghost.py phone spawn 15550002002
```

Each inbound message resets only that customer/business-phone pair's window. One simulated customer can independently chat with every configured business: choose a business in the dedicated `/phone` tab, and the first message creates that conversation, emits the correct WABA webhook, and opens its 24-hour window. `spawn` opens another terminal on Windows; `open` runs in the current terminal.

The Textual client also loads every configured sender. Use the contact list to switch businesses, or open a specific sender directly:

```powershell
uv run ghost.py phone open 15550002001 --business PHONE_LOCAL
uv run ghost.py phone spawn 15550002002 --business PHONE_SALES
```

<a id="configuration"></a>

## ⚙️ Modes and configuration

Defaults work immediately. `.env` is loaded automatically; `.env.example` documents the settings:

| Variable | Default | Meaning |
|---|---|---|
| `WABA_DATA_DIR` | `.whatsapp-ghost` | SQLite and media directory |
| `WABA_BASE_URL` | `http://127.0.0.1:8787` | API URL used by responses and CLI |
| `WABA_ACCESS_TOKEN` | `local-dev-token` | Accepted Bearer token |
| `WABA_APP_SECRET` | `local-app-secret` | Webhook signing secret |
| `WABA_VERIFY_TOKEN` | `local-verify-token` | Webhook challenge token |
| `WABA_MODE` | `strict` | `strict`, `loose`, or `chaos` |
| `WABA_STATUS_DELAY` | `0.05` | Seconds before delivery |
| `WABA_NOTIFY` | `bell` | `bell`, `desktop`, or `none` |

`strict` enforces recipient existence and the service window. `loose` optimizes early integration and infers an omitted message type when unambiguous. `chaos` is reserved for deterministic fault policies; it currently uses strict validation and does not inject random failures. Reset safely with `uv run ghost.py reset`.

## 🧰 Sandbox control API

Everything below `/_sandbox` is intentionally not Meta-compatible. The
separation prevents test conveniences from leaking into the compatibility
surface: your application only ever calls the Graph routes, and never needs to
know Ghost is not Meta.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/_sandbox/health` | liveness, mode, clock, database path |
| `GET` | `/_sandbox/config` | base URL, access token, demo ids |
| `POST` | `/_sandbox/reset` | wipe everything and re-seed the demo |
| `GET` `POST` | `/_sandbox/clock` | virtual clock (see below) |
| `GET` `POST` | `/_sandbox/phones` | list or create simulated customers |
| `PATCH` `DELETE` | `/_sandbox/phones/{wa_id}` | rename or remove one |
| `POST` | `/_sandbox/phones/{wa_id}/messages` | **send an inbound message** |
| `POST` | `/_sandbox/phones/{wa_id}/read` | mark our messages read |
| `GET` `POST` | `/_sandbox/phones/{wa_id}/pins` | pinned conversations |
| `GET` | `/_sandbox/messages` | everything sent, newest first |
| `POST` | `/_sandbox/messages/{id}/status` | force a delivery status |
| `GET` | `/_sandbox/conversations` | conversation list |
| `GET` | `/_sandbox/unread` | unread counts |
| `GET` | `/_sandbox/media/{media_id}` | download bytes |
| `GET` | `/_sandbox/webhooks` | delivery log with every attempt |
| `POST` | `/_sandbox/webhooks/{delivery_id}/replay` | re-deliver one |
| `GET` | `/_sandbox/webhook-subscriptions` | who is subscribed |
| `GET` `POST` | `/_sandbox/apps` | apps and tokens |
| `POST` | `/_sandbox/apps/{app_id}/rotate-token` | rotate a token |
| `GET` `POST` | `/_sandbox/businesses` | WABAs |
| `PATCH` | `/_sandbox/businesses/{waba_id}` | rename |
| `POST` | `/_sandbox/businesses/{waba_id}/phone-numbers` | add a sender |
| `PATCH` | `/_sandbox/phone-numbers/{phone_id}` | edit a sender |
| `WS` | `/_sandbox/clients/{wa_id}` | live events for one customer |

`GET /openapi.json` is the machine-readable version of all of it.

### Paging the busy endpoints

`/_sandbox/messages` and `/_sandbox/conversations` take filters, and reading a
long history without them is slow:

```text
GET /_sandbox/messages?wa_id=15550002001&limit=50
GET /_sandbox/messages?phone_number_id=PHONE_LOCAL&before={message_id}
GET /_sandbox/conversations?wa_id=15550002001
```

`limit` defaults to 100 and caps at 500; `before` takes a message id and pages
backwards. `scripts/seed_bulk_messages.py --count 1000` writes a long
conversation straight into SQLite when you need to test loading behaviour.

> [!WARNING]
> `GET /_sandbox/webhooks` is **not** paginated. It selects every delivery and
> runs a per-row query for its attempts, so on an instance with a large webhook
> log it can hang rather than answer. Until it takes a `limit`, inspect recent
> traffic from the console instead.

## 🎯 Fidelity and current boundary

Implemented behavior is based on Meta's documented Cloud API and official Postman examples, plus the supplied saved references. The goal is contract-compatible behavior for documented and tested scenarios. IDs, delivery timing, opaque implementation details, policy enforcement, and some error wording are approximations.

The first release does not yet implement Flows execution, commerce/catalogs, payments, QR codes, resumable template-media uploads, analytics, embedded signup, throughput token buckets, seven-day retry scheduling, automatic template-review delays, pricing cutovers, or a differential runner against an authorized Meta account. The boundaries allow those to be added without duplicating message rules.

> [!CAUTION]
> Do not use this project to impersonate WhatsApp, connect unauthorized accounts, or test undocumented private protocols.

<a id="development"></a>

## 🛠️ Development

```powershell
uv sync
uv run playwright install chromium
uv run pytest -q
uv run pytest --cov=whatsapp_ghost --cov-report=term-missing
```

The suite is split by behavior: resources/authentication, messages and 24-hour windows, media persistence/ownership, templates, webhooks and real callback delivery, restart/reset persistence, CLI, browser UI, and Textual UI. Browser tests start an actual Uvicorn process and drive Chromium through the console and phone—including image attachment, filesystem persistence, message ordering, ticks, and sender switching.

SQLite uses WAL mode. Original message payloads, status events, media metadata, and raw signed webhook bodies persist under `.whatsapp-ghost/`. Browser phone attachments use the same upload → media ID → message-reference flow as the API; they are stored in `.whatsapp-ghost/media/`, never embedded as transient data URLs.
