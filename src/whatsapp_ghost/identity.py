"""Deterministic display names and colors for simulated customers.

Autocreated recipients need a human-readable identity the moment they are
first messaged. Both name and color derive from a hash of the wa_id, so the
same number always yields the same identity across restarts and resets.
"""

from __future__ import annotations

import hashlib

ADJECTIVES = (
    "Amber", "Azure", "Bright", "Calm", "Clever", "Coral", "Crimson", "Dusky",
    "Eager", "Emerald", "Gentle", "Golden", "Hidden", "Indigo", "Jolly", "Keen",
    "Lively", "Lucky", "Merry", "Misty", "Noble", "Olive", "Patient", "Quiet",
    "Rapid", "Rustic", "Scarlet", "Silent", "Silver", "Solar", "Swift", "Teal",
    "Tidy", "Vivid", "Warm", "Wise",
)

ANIMALS = (
    "Otter", "Falcon", "Heron", "Badger", "Marten", "Ibex", "Lynx", "Osprey",
    "Puffin", "Raven", "Sable", "Tapir", "Vole", "Wren", "Yak", "Zebra",
    "Bison", "Crane", "Dingo", "Egret", "Ferret", "Gecko", "Hare", "Jackal",
    "Kite", "Lemur", "Moose", "Newt", "Ocelot", "Panda", "Quail", "Robin",
    "Stoat", "Turtle", "Urchin", "Weasel",
)

# Distinct hues, readable against both the light console and the phone bubbles.
PALETTE = (
    "#25D366", "#128C7E", "#075E54", "#34B7F1", "#1A73E8", "#4051B5",
    "#7E57C2", "#9C27B0", "#D81B60", "#E53935", "#F4511E", "#FB8C00",
    "#F9A825", "#C0CA33", "#43A047", "#00897B", "#00ACC1", "#5C6BC0",
)


def _digest(wa_id: str) -> int:
    return int.from_bytes(hashlib.sha256(wa_id.encode()).digest()[:8], "big")


def generated_name(wa_id: str) -> str:
    """A stable, readable two-word name derived from the number."""
    value = _digest(wa_id)
    return f"{ADJECTIVES[value % len(ADJECTIVES)]} {ANIMALS[(value // len(ADJECTIVES)) % len(ANIMALS)]}"


def generated_color(wa_id: str) -> str:
    """A stable palette color derived from the number."""
    return PALETTE[(_digest(wa_id) // (len(ADJECTIVES) * len(ANIMALS))) % len(PALETTE)]
