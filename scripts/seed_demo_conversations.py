"""Populate a Ghost sandbox with realistic travel-support traffic.

    uv run scripts/seed_demo_conversations.py [BASE_URL] [ACCESS_TOKEN]

Used to produce the README screenshots, and useful for trying the console and
the analytics panels against something other than an empty database.

Everything goes through the public API, so the seeded state is exactly what a
real integration would create: service windows open from customer messages,
statuses progress through the engine, and every message produces the webhook
deliveries it normally would.

Two details make the result look real rather than synthetic:

* A callback is subscribed BEFORE any traffic. Without a subscriber every
  customer message stays at one tick, because for that direction the webhook is
  the delivery. Point it at your own receiver, or run the bundled one:
  ``uv run uvicorn examples.webhook_receiver:app --port 9000``.
* The sandbox clock starts several days in the past and is advanced through the
  script, so the history ends at roughly "now" and spreads across the hours of
  several days. Seeding forward from now instead would leave the whole history
  in the future, and anything sent afterwards would sort underneath it.
"""
from __future__ import annotations

import random
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "local-dev-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
random.seed(20260912)

http = httpx.Client(base_url=BASE, timeout=30)


def api(method: str, path: str, **kwargs):
    response = http.request(method, path, **kwargs)
    if response.status_code >= 400:
        print(f"  ! {method} {path} -> {response.status_code} {response.text[:160]}")
    return response


def clock(action: str, value: str | None = None):
    body = {"action": action}
    if value:
        body["value"] = value
    api("POST", "/_sandbox/clock", json=body)


# ---------------------------------------------------------------- businesses
SENDERS: dict[str, str] = {}

def ensure_business(name: str, verified_name: str, number: str) -> str:
    created = api("POST", "/_sandbox/businesses", json={
        "name": name, "verified_name": verified_name, "display_phone_number": number,
    })
    if created.status_code < 400:
        data = created.json()
        return data.get("phone_number_id") or data["phone_numbers"][0]["id"]
    # Already present: find it.
    for business in api("GET", "/_sandbox/businesses").json()["data"]:
        for phone in business["phone_numbers"]:
            if phone["display_phone_number"] == number:
                return phone["id"]
    raise SystemExit(f"could not create or find sender {number}")


# Subscribe a callback BEFORE any traffic, so customer messages actually reach
# "the business" and earn their second tick. Without a subscriber every inbound
# stays at one tick, which is correct but makes for a dull demo.
def subscribe_all(callback_url: str = "http://127.0.0.1:9000/webhook") -> None:
    for business in api("GET", "/_sandbox/businesses").json()["data"]:
        api("POST", f"/v25.0/{business['id']}/subscribed_apps", headers=AUTH, json={
            "callback_url": callback_url, "verify_token": "ghost-verify",
        })


print("businesses…")
SENDERS["support"] = ensure_business("Travel XS", "Travel XS Support", "919876500100")
SENDERS["ops"] = ensure_business("Travel XS Operations", "Travel XS Ops", "919876500200")

WABAS = {b["phone_numbers"][0]["id"]: b["id"]
         for b in api("GET", "/_sandbox/businesses").json()["data"] if b["phone_numbers"]}
# Map each sender id back to its WABA for template creation.
SENDER_WABA = {}
for business in api("GET", "/_sandbox/businesses").json()["data"]:
    for phone in business["phone_numbers"]:
        SENDER_WABA[phone["id"]] = business["id"]


# ----------------------------------------------------------------- templates
TEMPLATES = [
    ("booking_confirmed_v3", "UTILITY",
     "Hi {{1}}, your booking {{2}} is confirmed for {{3}}. Your driver details arrive 2 hours before pickup.",
     [["Priya", "BK-448804", "Sat 12 Sep, 6:40 am"]],
     [{"type": "URL", "text": "View itinerary", "url": "https://travel-xs.com/trips/{{1}}",
       "example": ["https://travel-xs.com/trips/BK-448804"]},
      {"type": "QUICK_REPLY", "text": "Change plan"}]),
    ("driver_assigned_v2", "UTILITY",
     "Your driver for {{1}} is {{2}} in a {{3}}. They will reach the pickup point 10 minutes early.",
     [["BK-448804", "Imran S.", "white Innova Crysta"]],
     [{"type": "PHONE_NUMBER", "text": "Call driver", "phone_number": "+919876543210"},
      {"type": "QUICK_REPLY", "text": "Share live location"}]),
    ("ops_arrival_details_missing_v1", "UTILITY",
     "Hi {{1}}, we are missing arrival details for booking {{2}}. Please share your flight number so we can time the airport pickup.",
     [["Priya", "BK-448804"]],
     [{"type": "QUICK_REPLY", "text": "Send flight number"}]),
    ("trip_feedback_v1", "MARKETING",
     "Thanks for travelling with Travel XS, {{1}}. How was your trip on {{2}}?",
     [["Priya", "Sat 12 Sep"]],
     [{"type": "QUICK_REPLY", "text": "Great"},
      {"type": "QUICK_REPLY", "text": "Could be better"}]),
    ("invoice_ready_v1", "UTILITY",
     "Invoice {{1}} for booking {{2}} is ready. Amount payable: {{3}}.",
     [["INV-99213", "BK-448804", "INR 4,820"]],
     [{"type": "URL", "text": "Download invoice", "url": "https://travel-xs.com/invoices/{{1}}",
       "example": ["https://travel-xs.com/invoices/INV-99213"]}]),
]

print("subscriptions…")
subscribe_all()

print("templates…")
for name, category, body, example, buttons in TEMPLATES:
    for waba in set(SENDER_WABA.values()):
        api("POST", f"/v25.0/{waba}/message_templates", headers=AUTH, json={
            "name": name, "language": "en_US", "category": category,
            "_sandbox_auto_approve": True,
            "components": [
                {"type": "BODY", "text": body, "example": {"body_text": example}},
                {"type": "BUTTONS", "buttons": buttons},
            ],
        })


# ----------------------------------------------------------------- customers
CUSTOMERS = [
    ("919845012233", "Priya Nair", "#25D366", True),
    ("917012889450", "Arjun Menon", "#1877F2", True),
    ("919920045517", "Fatima Sheikh", "#9C27B0", False),
    ("918800231190", "Rohan Gupta", "#F4511E", False),
    ("917899034412", "Meera Iyer", "#00897B", True),
    ("919711556602", "Daniel Rozario", "#5E35B1", False),
    ("919035778821", "Ananya Bose", "#C2185B", False),
    ("918123440097", "Vikram Shetty", "#455A64", False),
]

print("customers…")
for wa_id, name, color, starred in CUSTOMERS:
    api("POST", "/_sandbox/phones", json={
        "wa_id": wa_id, "display_name": name, "color": color, "starred": starred,
    })


# ------------------------------------------------------------- conversations
def customer(wa_id: str, sender: str, text: str) -> None:
    api("POST", f"/_sandbox/phones/{wa_id}/messages",
        json={"type": "text", "text": text, "phone_number_id": sender})


def business(sender: str, wa_id: str, text: str) -> None:
    api("POST", f"/v25.0/{sender}/messages", headers=AUTH, json={
        "messaging_product": "whatsapp", "to": wa_id, "type": "text",
        "text": {"body": text},
    })


def template(sender: str, wa_id: str, name: str, *params: str) -> None:
    api("POST", f"/v25.0/{sender}/messages", headers=AUTH, json={
        "messaging_product": "whatsapp", "to": wa_id, "type": "template",
        "template": {
            "name": name, "language": {"code": "en_US"},
            "components": [{"type": "body",
                            "parameters": [{"type": "text", "text": p} for p in params]}],
        },
    })


SUPPORT = SENDERS["support"]
OPS = SENDERS["ops"]

# Each entry is (day_offset_hours, script). The clock is advanced between them
# so the traffic lands across several days and across the working hours of each,
# which is what makes the hour-of-day histogram meaningful.
SCRIPTS = [
    ("72h", lambda: [
        customer("919845012233", SUPPORT, "Hi, I need to book an airport transfer for Saturday morning."),
        business(SUPPORT, "919845012233", "Happy to help. Which airport and what time is your flight?"),
        customer("919845012233", SUPPORT, "Bengaluru, BLR. Flight is at 9:15 am so pickup around 6:40 would work."),
        business(SUPPORT, "919845012233", "Booked. Sending the confirmation now."),
        template(SUPPORT, "919845012233", "booking_confirmed_v3", "Priya", "BK-448804", "Sat 12 Sep, 6:40 am"),
    ]),
    ("3h", lambda: [
        customer("917012889450", SUPPORT, "My invoice for last week's trip hasn't arrived yet."),
        business(SUPPORT, "917012889450", "Checking that now, one moment."),
        template(SUPPORT, "917012889450", "invoice_ready_v1", "INV-99184", "BK-447120", "INR 3,150"),
        customer("917012889450", SUPPORT, "Got it, thanks. That was quick."),
    ]),
    ("14h", lambda: [
        customer("919920045517", OPS, "The pickup point for tomorrow is confusing. Which gate at T2?"),
        business(OPS, "919920045517", "Gate 3, arrivals level. The driver waits by the Travel XS board."),
        customer("919920045517", OPS, "Perfect. Also can I add one more passenger?"),
        business(OPS, "919920045517", "Added. The vehicle seats 6, so you are fine."),
    ]),
    ("9h", lambda: [
        template(OPS, "918800231190", "ops_arrival_details_missing_v1", "Rohan", "BK-449001"),
        customer("918800231190", OPS, "Flight AI 512, landing 11:20 pm."),
        business(OPS, "918800231190", "Noted. Pickup is timed to 11:50 pm to allow for baggage."),
        template(OPS, "918800231190", "driver_assigned_v2", "BK-449001", "Imran S.", "white Innova Crysta"),
    ]),
    ("5h", lambda: [
        customer("917899034412", SUPPORT, "Can I reschedule BK-448560 to Monday instead?"),
        business(SUPPORT, "917899034412", "Yes, no charge for changes more than 24 hours out. Monday what time?"),
        customer("917899034412", SUPPORT, "Same time, 7 am."),
        business(SUPPORT, "917899034412", "Done. Updated confirmation below."),
        template(SUPPORT, "917899034412", "booking_confirmed_v3", "Meera", "BK-448560", "Mon 14 Sep, 7:00 am"),
    ]),
    ("11h", lambda: [
        customer("919711556602", SUPPORT, "Driver hasn't arrived and it's been 20 minutes past pickup."),
        business(SUPPORT, "919711556602", "Sorry about that, escalating to ops right now."),
        business(SUPPORT, "919711556602", "Driver is 4 minutes away, he was held at the terminal barrier."),
        customer("919711556602", SUPPORT, "He's here now. Thanks for chasing it."),
    ]),
    ("2h", lambda: [
        customer("919035778821", OPS, "Is there a child seat available for the Thursday booking?"),
        business(OPS, "919035778821", "Yes, forward-facing seat for ages 1-4. Added at no cost."),
    ]),
    ("21h", lambda: [
        template(SUPPORT, "918123440097", "trip_feedback_v1", "Vikram", "Thu 10 Sep"),
        customer("918123440097", SUPPORT, "Great, driver was early and the car was spotless."),
        business(SUPPORT, "918123440097", "Thank you, passing that on to Imran."),
        customer("918123440097", SUPPORT, "Details are at https://travel-xs.com/trips/BK-446880 if you need the reference."),
    ]),
    ("4h", lambda: [
        customer("919845012233", OPS, "Quick one, can the Saturday driver call before arriving?"),
        business(OPS, "919845012233", "Yes, he will ring 10 minutes out on the number ending 2233."),
        template(OPS, "919845012233", "driver_assigned_v2", "BK-448804", "Suresh K.", "grey Toyota Etios"),
        customer("919845012233", OPS, "Brilliant, thank you."),
    ]),
    ("6h", lambda: [
        customer("917012889450", OPS, "Mail the receipt to mail@ranitbhowmick.com as well please."),
        business(OPS, "917012889450", "Sent to that address too."),
    ]),
]

# Start in the past and advance up to roughly "now", rather than advancing
# forward from now: otherwise the whole seeded history sits in the future and
# anything sent afterwards sorts underneath it.
TOTAL_HOURS = sum(int(step[0].rstrip("h")) for step in SCRIPTS)
clock("set", (datetime.now(timezone.utc) - timedelta(hours=TOTAL_HOURS + 2)).isoformat())

print("conversations…")
for advance, script in SCRIPTS:
    clock("advance", advance)
    script()
    time.sleep(0.35)          # let the engine settle each status chain

# Leave the clock where the real world is, so "now" in the UI is not in the past.
clock("reset")

# Some chats read, some left unread, so the badges and the unread ordering in
# the roster are not uniformly zero.
for wa_id in ("919845012233", "918123440097", "917012889450"):
    for sender in (SUPPORT, OPS):
        api("POST", f"/_sandbox/phones/{wa_id}/read", json={"phone_number_id": sender})

stats = api("GET", "/_sandbox/stats").json()
print(f"\ndone: {stats['messages']} messages, {stats['customers']} customers, "
      f"{stats['templates']} templates, {stats['webhooks']} webhook deliveries")
