"""The phone page must stay live without a manual refresh.

Two things used to force one. A mobile auto-created by an inbound send did not
appear until the page was reloaded, because the roster was only re-fetched on
window focus. And a message arriving for a *different* mobile was invisible,
because the only socket the page held was /_sandbox/clients/{wa}, which by
design carries events for one customer.

The fix is /_sandbox/observer, a read-only firehose tagging every event with
the wa_id it belongs to, plus /_sandbox/unread for badge counts derived from
message status. These tests pin both, at the API level here and through a real
browser in test_browser_e2e.py.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _open_window(client: TestClient, customer: str) -> None:
    """Register the mobile and let the business reply as plain text.

    Two constraints meet here. The inbound route refuses an unknown wa_id (only
    the outbound path auto-creates), so the mobile is registered first. And
    strict mode refuses a free-form business send outside the 24-hour service
    window, so the customer has to speak before the business can.
    """
    client.post("/_sandbox/phones", json={"wa_id": customer})
    response = client.post(
        f"/_sandbox/phones/{customer}/messages",
        json={"type": "text", "text": "hi", "phone_number_id": "PHONE_LOCAL"},
    )
    assert response.status_code == 201, response.text


def _send(client: TestClient, headers: dict[str, str], to: str, body: str) -> None:
    response = client.post(
        "/v25.0/PHONE_LOCAL/messages",
        headers=headers,
        json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}},
    )
    assert response.status_code == 200, response.text


def test_observer_receives_events_for_a_mobile_it_is_not_acting_as(
    client: TestClient, headers: dict[str, str]
) -> None:
    """The whole point: one socket, every mobile.

    Subscribing per wa_id is what made the old page unable to notice traffic on
    another mobile, so this asserts the tag is present - without wa_id the page
    could not decide which row to badge.
    """
    _open_window(client, "15551230001")
    with client.websocket_connect("/_sandbox/observer") as socket:
        _send(client, headers, "15551230001", "hello there")
        seen = []
        for _ in range(6):
            seen.append(socket.receive_json())
            if any(e.get("event") == "message" for e in seen):
                break
    messages = [e for e in seen if e.get("event") == "message"]
    assert messages, f"no message event on the observer: {seen}"
    assert messages[0]["wa_id"] == "15551230001"


def test_observer_announces_an_auto_created_mobile(
    client: TestClient, headers: dict[str, str]
) -> None:
    """The refresh-to-see-a-new-mobile complaint, pinned.

    ensure_user() creates the row; without this event nothing tells an open page
    that the roster changed, which is exactly why a reload was needed.
    """
    with client.websocket_connect("/_sandbox/observer") as socket:
        # No pre-registration: the outbound send is what auto-creates the mobile.
        response = client.post(
            "/v25.0/PHONE_LOCAL/messages",
            headers=headers,
            json={
                "messaging_product": "whatsapp",
                "to": "15559990002",
                "type": "template",
                "template": {
                    "name": "hello_world",
                    "language": {"code": "en_US"},
                    "components": [
                        {"type": "body", "parameters": [{"type": "text", "text": "Ravi"}]}
                    ],
                },
            },
        )
        assert response.status_code == 200, response.text
        created = socket.receive_json()
    assert created["event"] == "phone_created"
    assert created["wa_id"] == "15559990002"
    assert created["user"]["auto_created"] == 1


def test_manual_phone_creation_and_deletion_are_announced(client: TestClient) -> None:
    with client.websocket_connect("/_sandbox/observer") as socket:
        client.post("/_sandbox/phones", json={"wa_id": "15557770003"})
        created = socket.receive_json()
        client.delete("/_sandbox/phones/15557770003")
        deleted = socket.receive_json()
    assert created["event"] == "phone_created"
    assert created["user"]["auto_created"] == 0
    assert deleted == {"event": "phone_deleted", "wa_id": "15557770003"}


def test_unread_counts_come_from_message_status_and_clear_on_read(
    client: TestClient, headers: dict[str, str]
) -> None:
    """Unread is derived, never separately tracked.

    Deriving it from status is what makes a badge survive a reload and stay
    consistent with the read receipt the sandbox reports to webhooks. A
    separate counter could disagree with both.
    """
    _open_window(client, "15551230004")
    _send(client, headers, "15551230004", "one")
    _send(client, headers, "15551230004", "two")

    def unread_for(wa: str) -> int:
        data = client.get("/_sandbox/unread").json()["data"]
        return sum(row["unread"] for row in data if row["wa_id"] == wa)

    assert unread_for("15551230004") == 2

    client.post("/_sandbox/phones/15551230004/read", json={"phone_number_id": "PHONE_LOCAL"})
    assert unread_for("15551230004") == 0


def test_unread_is_reported_per_mobile_not_pooled(
    client: TestClient, headers: dict[str, str]
) -> None:
    """Reading one mobile must not clear another's badge."""
    _open_window(client, "15551230005")
    _open_window(client, "15551230006")
    _send(client, headers, "15551230005", "for five")
    _send(client, headers, "15551230006", "for six")
    client.post("/_sandbox/phones/15551230005/read", json={"phone_number_id": "PHONE_LOCAL"})

    data = {row["wa_id"]: row["unread"] for row in client.get("/_sandbox/unread").json()["data"]}
    assert data.get("15551230005", 0) == 0
    assert data.get("15551230006", 0) == 1


def test_a_mobiles_own_outbound_message_is_not_counted_as_unread(
    client: TestClient, headers: dict[str, str]
) -> None:
    """Only business -> customer messages are something to read.

    The echo of what the simulated customer itself sent must never badge, or
    every reply the tester types would light up their own row.
    """
    client.post("/_sandbox/phones", json={"wa_id": "15551230007"})
    client.post(
        "/_sandbox/phones/15551230007/messages",
        json={"phone_number_id": "PHONE_LOCAL", "type": "text", "text": "typed by the customer"},
    )
    data = {row["wa_id"]: row["unread"] for row in client.get("/_sandbox/unread").json()["data"]}
    assert data.get("15551230007", 0) == 0


def test_observer_is_read_only_and_does_not_send_messages(
    client: TestClient, headers: dict[str, str]
) -> None:
    """Sending stays on the per-customer socket, which knows who it is acting as.

    The observer has no wa_id of its own, so accepting a send here would have to
    guess a sender. It ignores input instead of inventing one.
    """
    _open_window(client, "15551230008")
    before = client.get("/_sandbox/messages?wa_id=15551230008").json()["data"]
    with client.websocket_connect("/_sandbox/observer") as socket:
        socket.send_text(json.dumps({"action": "send", "type": "text", "payload": {"body": "nope"}}))
        _send(client, headers, "15551230008", "a real one")
        socket.receive_json()
    after = client.get("/_sandbox/messages?wa_id=15551230008").json()["data"]
    # Exactly the one genuine send, nothing conjured by the observer.
    assert len(after) - len(before) == 1
    assert after[0]["direction"] == "outbound"


def test_per_customer_socket_payload_is_unchanged_by_the_observer(
    client: TestClient, headers: dict[str, str]
) -> None:
    """The existing socket contract must not shift.

    broadcast() now also publishes to observers, with wa_id added on the
    observer copy only. Anything already reading /_sandbox/clients/{wa} - the
    phone page, the TUI - must see exactly what it saw before.
    """
    _open_window(client, "15551230009")
    with client.websocket_connect("/_sandbox/clients/15551230009") as socket:
        _send(client, headers, "15551230009", "unchanged")
        # The status events (sent/delivered) arrive first; the message event is
        # published once delivery lands. Scan rather than assume an ordering.
        events = [socket.receive_json() for _ in range(3)]
    messages = [e for e in events if e["event"] == "message"]
    assert messages, f"no message event on the per-customer socket: {events}"
    for event in events:
        assert "wa_id" not in event, f"observer tagging leaked into {event}"


def test_live_pushed_message_carries_a_parsed_payload(
    client: TestClient, headers: dict[str, str]
) -> None:
    """A socket message must be readable without a second fetch.

    The stored column is payload_json, so dict(row) hands out raw JSON text and
    no "payload" key. The REST endpoint parses it but the socket did not, and
    every live-pushed message rendered in the browser as "(no body)".
    """
    _open_window(client, "15551230010")
    with client.websocket_connect("/_sandbox/observer") as socket:
        _send(client, headers, "15551230010", "readable body")
        events = [socket.receive_json() for _ in range(3)]

    messages = [e for e in events if e["event"] == "message"]
    assert messages, f"no message event: {events}"
    payload = messages[0]["message"]["payload"]
    assert isinstance(payload, dict), f"payload arrived unparsed: {payload!r}"
    assert payload["text"]["body"] == "readable body"
    assert "payload_json" not in messages[0]["message"]


def test_messages_paginate_backwards_without_gaps_or_repeats(
    client: TestClient, headers: dict[str, str]
) -> None:
    """Keyset pagination, so pages stay stable while the chat is live.

    OFFSET would drift as new messages arrive mid-conversation, silently
    skipping or repeating rows, and it makes SQLite walk rows it is about to
    throw away.
    """
    _open_window(client, "15551230020")
    for index in range(12):
        _send(client, headers, "15551230020", f"body {index}")

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(6):
        url = "/_sandbox/messages?wa_id=15551230020&phone_number_id=PHONE_LOCAL&limit=5"
        if cursor:
            url += f"&before={cursor}"
        page = client.get(url).json()
        seen.extend(m["id"] for m in page["data"])
        if not page["has_more"]:
            break
        cursor = page["next_before"]

    assert len(seen) == len(set(seen)), "a message was returned on two pages"
    everything = client.get(
        "/_sandbox/messages?wa_id=15551230020&phone_number_id=PHONE_LOCAL&limit=500"
    ).json()["data"]
    assert set(seen) == {m["id"] for m in everything}, "paging lost messages"


def test_message_pages_are_newest_first_and_report_more(
    client: TestClient, headers: dict[str, str]
) -> None:
    _open_window(client, "15551230021")
    for index in range(8):
        _send(client, headers, "15551230021", f"body {index}")

    first = client.get(
        "/_sandbox/messages?wa_id=15551230021&phone_number_id=PHONE_LOCAL&limit=3"
    ).json()
    assert len(first["data"]) == 3
    assert first["has_more"] is True
    assert first["next_before"] == first["data"][-1]["id"]
    times = [m["created_at"] for m in first["data"]]
    assert times == sorted(times, reverse=True), times


def test_last_page_reports_no_more_and_no_cursor(
    client: TestClient, headers: dict[str, str]
) -> None:
    """has_more must be false on the final page, or the UI offers a dead button."""
    _open_window(client, "15551230022")
    _send(client, headers, "15551230022", "only one")
    page = client.get(
        "/_sandbox/messages?wa_id=15551230022&phone_number_id=PHONE_LOCAL&limit=50"
    ).json()
    assert page["has_more"] is False
    assert page["next_before"] is None


def test_unknown_before_cursor_is_rejected(client: TestClient) -> None:
    """Silently ignoring a bad cursor would serve page one as if it were page five."""
    response = client.get("/_sandbox/messages?before=wamid.definitely-not-real")
    assert response.status_code == 400
    assert "cursor" in response.json()["error"]


def test_pagination_survives_messages_sharing_a_timestamp(
    client: TestClient, headers: dict[str, str]
) -> None:
    """The rowid tiebreak is what makes this safe.

    The sandbox can write several messages inside one clock tick, and a
    created_at-only cursor would either drop them or replay them forever.
    """
    _open_window(client, "15551230023")
    for index in range(6):
        _send(client, headers, "15551230023", f"same tick {index}")

    stamps = [
        m["created_at"]
        for m in client.get(
            "/_sandbox/messages?wa_id=15551230023&phone_number_id=PHONE_LOCAL&limit=50"
        ).json()["data"]
    ]
    # Only meaningful if the writes really did collide; assert the walk either way.
    collided = len(stamps) != len(set(stamps))

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        url = "/_sandbox/messages?wa_id=15551230023&phone_number_id=PHONE_LOCAL&limit=2"
        if cursor:
            url += f"&before={cursor}"
        page = client.get(url).json()
        seen.extend(m["id"] for m in page["data"])
        if not page["has_more"]:
            break
        cursor = page["next_before"]
    assert len(seen) == len(set(seen)), f"duplicate across pages (collided={collided})"
    assert len(seen) == len(stamps), f"lost rows (collided={collided})"
