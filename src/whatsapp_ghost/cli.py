from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from .config import Settings


app = typer.Typer(no_args_is_help=True, help="Run and control a local WhatsApp Cloud API sandbox.")
clock_app = typer.Typer(no_args_is_help=True, help="Control the sandbox virtual clock.")
phone_app = typer.Typer(no_args_is_help=True, help="Create and open simulated WhatsApp phones.")
webhook_app = typer.Typer(no_args_is_help=True, help="Inspect webhook deliveries.")
app.add_typer(clock_app, name="clock")
app.add_typer(phone_app, name="phone")
app.add_typer(webhook_app, name="webhooks")
console = Console()


def api(method: str, path: str, *, json: dict | None = None) -> dict:
    settings = Settings.from_env()
    try:
        response = httpx.request(method, settings.base_url + path, json=json, timeout=10)
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        console.print(f"[red]Sandbox is not running at {settings.base_url}.[/] Start it with: [bold]waba start[/]")
        raise typer.Exit(1)
    except httpx.HTTPStatusError as exc:
        console.print(f"[red]{exc.response.status_code}[/] {exc.response.text}")
        raise typer.Exit(1)


@app.command()
def init(force: Annotated[bool, typer.Option("--force", help="Replace an existing .env file.")] = False) -> None:
    """Create novice-friendly local configuration without starting a server."""
    target = Path(".env")
    if target.exists() and not force:
        console.print(f"[yellow]{target} already exists.[/] Use --force to replace it.")
        return
    target.write_text(
        "WABA_BASE_URL=http://127.0.0.1:8787\nWABA_ACCESS_TOKEN=local-dev-token\nWABA_APP_SECRET=local-app-secret\nWABA_VERIFY_TOKEN=local-verify-token\nWABA_MODE=strict\nWABA_NOTIFY=bell\n",
        encoding="utf-8",
    )
    console.print("[green]Configuration example created.[/] Defaults already work; run [bold]waba start[/].")


@app.command()
def start(host: str = "127.0.0.1", port: int = 8787, reload: bool = False, mode: str | None = None) -> None:
    """Start the API, seeded database, webhook worker, and client gateway."""
    import os
    import uvicorn
    if mode:
        os.environ["WABA_MODE"] = mode
    console.print(f"[green]WhatsApp Ghost[/] → http://{host}:{port}")
    console.print("Console → [link=http://127.0.0.1:8787/console]http://127.0.0.1:8787/console[/]")
    console.print("Docs → [link=http://127.0.0.1:8787/docs]http://127.0.0.1:8787/docs[/]")
    uvicorn.run("whatsapp_ghost.api:app", host=host, port=port, reload=reload)


@app.command()
def doctor() -> None:
    """Check Python, configuration, storage, and a running server."""
    settings = Settings.from_env()
    table = Table("Check", "Result")
    table.add_row("Python", sys.version.split()[0])
    table.add_row("Data directory", str(settings.data_dir))
    table.add_row("Mode", settings.mode)
    try:
        result = api("GET", "/_sandbox/health")
        table.add_row("Server", f"[green]{result['status']}[/] at {settings.base_url}")
    except typer.Exit:
        table.add_row("Server", "[red]not reachable[/]")
    console.print(table)


@app.command()
def docs() -> None:
    """Open the local interactive API documentation."""
    webbrowser.open(Settings.from_env().base_url + "/docs")


@app.command("console")
def open_console() -> None:
    """Open the visual local developer console."""
    webbrowser.open(Settings.from_env().base_url + "/console")


@app.command()
def reset(yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
    """Reset all sandbox state and restore the demo data."""
    if not yes and not typer.confirm("Delete all local sandbox messages, templates, and media?"):
        raise typer.Abort()
    api("POST", "/_sandbox/reset")
    console.print("[green]Sandbox reset.[/]")


@clock_app.command("show")
def clock_show() -> None:
    result = api("GET", "/_sandbox/clock")
    console.print(result["now"] + (" [yellow](frozen)[/]" if result["frozen"] else " [green](system clock)[/]"))


@clock_app.command("advance")
def clock_advance(duration: str) -> None:
    result = api("POST", "/_sandbox/clock", json={"action": "advance", "value": duration})
    console.print(result["now"] + " [yellow](frozen)[/]")


@clock_app.command("set")
def clock_set(value: str) -> None:
    result = api("POST", "/_sandbox/clock", json={"action": "set", "value": value})
    console.print(result["now"] + " [yellow](frozen)[/]")


@clock_app.command("reset")
def clock_reset() -> None:
    result = api("POST", "/_sandbox/clock", json={"action": "reset"})
    console.print(result["now"] + " [green](system clock)[/]")


@phone_app.command("create")
def phone_create(wa_id: str, name: str = "Local Customer") -> None:
    result = api("POST", "/_sandbox/phones", json={"wa_id": wa_id, "display_name": name})
    console.print(f"[green]Created[/] {result['display_name']} ({result['wa_id']})")


@phone_app.command("open")
def phone_open(wa_id: str = "15550002001") -> None:
    """Open a Textual WhatsApp-like client for one simulated number."""
    from .tui import PhoneApp
    settings = Settings.from_env()
    PhoneApp(wa_id, settings.base_url, settings.access_token, settings.notify).run()


@phone_app.command("spawn")
def phone_spawn(wa_id: str = "15550002001") -> None:
    """Open a simulated phone in a separate Windows terminal."""
    command = [sys.executable, "-m", "whatsapp_ghost.cli", "phone", "open", wa_id]
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "WhatsApp Ghost", *command])
    else:
        console.print("Automatic terminal spawning is platform-specific; opening in this terminal.")
        phone_open(wa_id)


@webhook_app.command("list")
def webhook_list() -> None:
    result = api("GET", "/_sandbox/webhooks")
    table = Table("ID", "Event", "Status", "Attempts", "Destination")
    for item in result["data"]:
        table.add_row(item["id"], item["event_type"], item["status"], str(item["attempt_count"]), item["destination_url"] or "—")
    console.print(table)


if __name__ == "__main__":
    app()
