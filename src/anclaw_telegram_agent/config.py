import os
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv

load_dotenv()


class BotMode(str, Enum):
    POLLING = "polling"
    WEBHOOK = "webhook"


@dataclass
class WebhookConfig:
    url: str
    port: int
    path: str
    secret_token: str

    @property
    def full_url(self) -> str:
        """URL completo che Telegram deve chiamare (dominio + path)."""
        return f"{self.url.rstrip('/')}{self.path}"


@dataclass
class Config:
    telegram_token: str
    mode: BotMode
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    webhook: WebhookConfig | None = field(default=None)

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN non impostato nel file .env")

        mode = BotMode(os.getenv("BOT_MODE", "polling").lower())

        raw_ids = os.getenv("ALLOWED_USER_IDS", "")
        allowed_user_ids = frozenset(
            int(uid.strip()) for uid in raw_ids.split(",") if uid.strip().isdigit()
        )

        webhook = None
        if mode is BotMode.WEBHOOK:
            url = os.getenv("WEBHOOK_URL")
            if not url:
                raise ValueError("WEBHOOK_URL obbligatorio quando BOT_MODE=webhook")
            webhook = WebhookConfig(
                url=url,
                port=int(os.getenv("WEBHOOK_PORT", "8443")),
                path=os.getenv("WEBHOOK_PATH", "/webhook"),
                secret_token=os.getenv("WEBHOOK_SECRET_TOKEN", ""),
            )

        return cls(telegram_token=token, mode=mode, allowed_user_ids=allowed_user_ids, webhook=webhook)
