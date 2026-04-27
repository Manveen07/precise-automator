from functools import lru_cache
import os
from pathlib import Path

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import dotenv_values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "local"
    APP_BASE_URL: str = "http://localhost:8000"
    APP_SECRET_KEY: str = "replace_me"
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:55432/precise_automator"
    REDIS_URL: str = "redis://localhost:6379/0"
    ANTHROPIC_API_KEY: str = "replace_me"
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    SMARTLEAD_WEBHOOK_SECRET: str | None = None
    SLACK_WEBHOOK_URL: AnyHttpUrl | None = None
    INBOX_SHEET_SCRIPT_URL: AnyHttpUrl | None = None
    BLOCKED_PHRASES: list[str] = Field(default_factory=lambda: ["guaranteed", "risk-free"])

    @field_validator("SLACK_WEBHOOK_URL", "INBOX_SHEET_SCRIPT_URL", mode="before")
    @classmethod
    def blank_url_is_none(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def get_secret_value(env_name: str) -> str | None:
    value = os.environ.get(env_name)
    if value:
        return value
    env_values = _dotenv_values()
    value = env_values.get(env_name)
    return value if value else None


@lru_cache
def _dotenv_values() -> dict[str, str | None]:
    env_path = Path(".env")
    if not env_path.exists():
        return {}
    return dict(dotenv_values(env_path))
