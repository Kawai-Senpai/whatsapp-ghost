from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

from .notifications import notify


class PhoneApp(App[None]):
    TITLE = "WhatsApp Ghost Phone"
    SUB_TITLE = "Local simulated client"
    CSS = """
    Screen { background: #0b141a; color: #e9edef; }
    #sidebar { width: 28; background: #111b21; border-right: solid #2a3942; }
    #identity { height: 5; padding: 1 2; background: #202c33; color: #00a884; text-style: bold; }
    #contacts { height: 1fr; }
    #chat { width: 1fr; }
    #chat-title { height: 3; padding: 1 2; background: #202c33; text-style: bold; }
    #messages { height: 1fr; padding: 1 2; scrollbar-size: 0 0; }
    #composer { dock: bottom; height: 3; margin: 0 1 1 1; border: tall #2a3942; background: #202c33; }
    ListItem { padding: 1; }
    ListItem.--highlight { background: #202c33; }
    """

    def __init__(
        self,
        wa_id: str,
        base_url: str,
        token: str,
        notify_adapter: str = "bell",
        phone_number_id: str | None = None,
    ):
        super().__init__()
        self.wa_id = wa_id
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.notify_adapter = notify_adapter
        self.active_phone_id = phone_number_id
        self.senders: dict[str, dict[str, Any]] = {}
        self.seen: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static(f"PHONE\n{self.wa_id}", id="identity")
                yield ListView(ListItem(Label("Loading business senders…"), id="sender-loading"), id="contacts")
            with Vertical(id="chat"):
                yield Static("Ghost Demo Business  •  local sandbox", id="chat-title")
                yield RichLog(id="messages", wrap=True, markup=True)
                yield Input(placeholder="Type a message and press Enter…", id="composer")
        yield Footer()

    async def on_mount(self) -> None:
        await self.load_senders()
        self.set_interval(0.75, self.refresh_messages)
        await self.refresh_messages()
        self.query_one("#composer", Input).focus()

    async def load_senders(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self.base_url}/_sandbox/businesses")
                response.raise_for_status()
            senders = [
                {**phone, "business_name": business["name"]}
                for business in response.json()["data"]
                for phone in business["phone_numbers"]
            ]
        except Exception as exc:
            self.sub_title = f"Disconnected: {exc}"
            return
        contacts = self.query_one("#contacts", ListView)
        await contacts.clear()
        self.senders = {f"sender-{index}": sender for index, sender in enumerate(senders)}
        if not senders:
            await contacts.append(ListItem(Label("No configured senders"), id="sender-empty"))
            self.active_phone_id = None
            return
        if not self.active_phone_id or not any(sender["id"] == self.active_phone_id for sender in senders):
            self.active_phone_id = senders[0]["id"]
        await contacts.extend([
            ListItem(Label(f"{sender['verified_name']}\n+{sender['display_phone_number']}"), id=item_id)
            for item_id, sender in self.senders.items()
        ])
        selected = next(index for index, sender in enumerate(senders) if sender["id"] == self.active_phone_id)
        contacts.index = selected
        self.update_sender_title()

    def update_sender_title(self) -> None:
        sender = next((item for item in self.senders.values() if item["id"] == self.active_phone_id), None)
        if sender:
            self.query_one("#chat-title", Static).update(
                f"{sender['verified_name']}  •  +{sender['display_phone_number']}  •  {sender['business_name']}"
            )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        sender = self.senders.get(event.item.id or "")
        if not sender or sender["id"] == self.active_phone_id:
            return
        self.active_phone_id = sender["id"]
        self.seen.clear()
        self.query_one("#messages", RichLog).clear()
        self.update_sender_title()
        await self.refresh_messages()

    async def refresh_messages(self) -> None:
        if not self.active_phone_id:
            return
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(
                    f"{self.base_url}/_sandbox/messages",
                    params={"wa_id": self.wa_id, "phone_number_id": self.active_phone_id, "limit": 200},
                )
                response.raise_for_status()
            items = list(reversed(response.json()["data"]))
        except Exception as exc:
            self.sub_title = f"Disconnected: {exc}"
            return
        self.sub_title = "Connected to local sandbox"
        log = self.query_one("#messages", RichLog)
        for item in items:
            if item["id"] in self.seen:
                continue
            self.seen.add(item["id"])
            inbound_to_phone = item["direction"] == "outbound"
            active_sender = next((item for item in self.senders.values() if item["id"] == self.active_phone_id), None)
            sender = (active_sender or {}).get("verified_name", "Business") if inbound_to_phone else "You"
            color = "#d9fdd3" if inbound_to_phone else "#53bdeb"
            payload = item["payload"]
            kind = item["message_type"]
            content: Any = payload.get(kind, payload)
            if kind == "text":
                text = content.get("body", "")
            elif kind == "template":
                text = "[template] " + content.get("name", "unknown")
            elif kind in {"image", "video", "audio", "document", "sticker"}:
                text = f"[{kind}] " + (content.get("caption") or content.get("filename") or content.get("id") or content.get("link") or "attachment")
            else:
                text = f"[{kind}] {json.dumps(content, ensure_ascii=False)}"
            stamp = datetime.fromisoformat(item["created_at"]).strftime("%H:%M")
            log.write(f"[{color}][b]{escape(sender)}[/b]  {stamp}[/]\n{escape(str(text))}  [dim]{escape(item['status'])}[/]\n")
            if inbound_to_phone:
                notify("WhatsApp Ghost", f"{sender}: {text}", self.notify_adapter)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(
                    f"{self.base_url}/_sandbox/phones/{self.wa_id}/messages",
                    json={"type": "text", "text": text, "phone_number_id": self.active_phone_id},
                )
                response.raise_for_status()
            await asyncio.sleep(0.05)
            await self.refresh_messages()
        except Exception as exc:
            self.notify(f"Could not send: {exc}", severity="error")
