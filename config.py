from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    BOT_TOKEN: str
    BOT_USERNAME: str = ""
    ADMIN_IDS: str = ""
    SUPPORT_CONTACT: str = "masterly_support"

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/masterly"
    DB_ECHO: bool = False
    AUTO_CREATE_SCHEMA: bool = True

    DEFAULT_LANGUAGE: str = "uz"
    COMMISSION_PERCENT: float = 15
    MIN_WITHDRAWAL: float = 50000
    MASTER_ACCEPT_TIMEOUT_MINUTES: int = 15
    PAYMENT_TIMEOUT_MINUTES: int = 120
    TIMEZONE: str = "Asia/Tashkent"

    PORT: int = 8080
    PUBLIC_BASE_URL: str = "http://localhost:8080"
    WEBHOOK_BASE_URL: str = ""
    WEBHOOK_SECRET: str = "change-me-webhook-secret"
    PAYMENT_PROVIDER: str = "mock"
    ALLOW_MOCK_PAYMENTS: bool = True

    CLICK_SERVICE_ID: str = ""
    CLICK_MERCHANT_ID: str = ""
    CLICK_MERCHANT_USER_ID: str = ""
    CLICK_SECRET_KEY: str = ""

    PAYME_MERCHANT_ID: str = ""
    PAYME_KEY: str = ""

    SECRET_KEY: str = "change-me-to-a-long-random-string"
    CARD_ENCRYPTION_KEY: str = ""
    LOG_LEVEL: str = "INFO"
    RATE_LIMIT_SECONDS: float = 0.4

    @field_validator("DEFAULT_LANGUAGE")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        return value if value in {"uz", "ru"} else "uz"

    @field_validator("DATABASE_URL")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

    @field_validator("PAYMENT_PROVIDER")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        value = (value or "mock").lower()
        return value if value in {"mock", "click", "payme"} else "mock"

    @property
    def admin_ids(self) -> set[int]:
        result: set[int] = set()
        for raw in self.ADMIN_IDS.replace(";", ",").split(","):
            raw = raw.strip()
            if raw.isdigit():
                result.add(int(raw))
        return result

    @property
    def webhook_enabled(self) -> bool:
        return bool(self.WEBHOOK_BASE_URL.strip())

    @property
    def public_base_url(self) -> str:
        explicit = (self.PUBLIC_BASE_URL or "").strip()
        webhook = (self.WEBHOOK_BASE_URL or "").strip()
        render = os.getenv("RENDER_EXTERNAL_URL", "").strip()
        if explicit and not explicit.startswith("http://localhost"):
            base = explicit
        elif webhook:
            base = webhook
        elif render:
            base = render
        else:
            base = explicit or f"http://localhost:{self.PORT}"
        return base.rstrip("/")

    @property
    def webhook_url(self) -> str:
        return f"{self.WEBHOOK_BASE_URL.rstrip('/')}/telegram/webhook/{self.WEBHOOK_SECRET}"

    @property
    def support_contact(self) -> str:
        return self.SUPPORT_CONTACT.lstrip("@").strip()

    @property
    def commission_percent(self):
        from decimal import Decimal

        return Decimal(str(self.COMMISSION_PERCENT)).quantize(Decimal("0.01"))

    @property
    def min_withdrawal(self):
        from decimal import Decimal

        return Decimal(str(self.MIN_WITHDRAWAL)).quantize(Decimal("0.01"))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
