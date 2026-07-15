from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    base_url: str
    access_token: str
    app_secret: str
    verify_token: str
    mode: str
    status_delay_seconds: float
    notify: str

    @property
    def database_path(self) -> Path:
        return self.data_dir / "whatsapp-ghost.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        mode = os.getenv("WABA_MODE", "strict").lower()
        if mode not in {"loose", "strict", "chaos"}:
            raise ValueError("WABA_MODE must be loose, strict, or chaos")
        return cls(
            data_dir=Path(os.getenv("WABA_DATA_DIR", ".whatsapp-ghost")).resolve(),
            base_url=os.getenv("WABA_BASE_URL", "http://127.0.0.1:8787").rstrip("/"),
            access_token=os.getenv("WABA_ACCESS_TOKEN", "local-dev-token"),
            app_secret=os.getenv("WABA_APP_SECRET", "local-app-secret"),
            verify_token=os.getenv("WABA_VERIFY_TOKEN", "local-verify-token"),
            mode=mode,
            status_delay_seconds=float(os.getenv("WABA_STATUS_DELAY", "0.05")),
            notify=os.getenv("WABA_NOTIFY", "bell"),
        )
