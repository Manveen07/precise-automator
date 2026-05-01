from functools import lru_cache
import os
from pathlib import Path

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import dotenv_values


SMARTLEAD_WORKSPACES = [
    {
        "key": "preciselead",
        "name": "PreciseLead",
        "api_key_env": "SMARTLEAD_PRECISELEAD_API_KEY",
        "client_id_env": "SMARTLEAD_PRECISELEAD_CLIENT_ID",
    },
    {
        "key": "belardi_wong",
        "name": "Belardi Wong",
        "api_key_env": "SMARTLEAD_BELARDI_WONG_API_KEY",
        "client_id_env": None,
    },
    {
        "key": "darlean",
        "name": "Darlean",
        "api_key_env": "SMARTLEAD_DARLEAN_API_KEY",
        "client_id_env": None,
    },
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "local"
    APP_BASE_URL: str = "http://localhost:8000"
    APP_SECRET_KEY: str = "replace_me"
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "precise_automator"
    ANTHROPIC_API_KEY: str = "replace_me"
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    SLACK_WEBHOOK_URL: AnyHttpUrl | None = None
    BLOCKED_PHRASES: list[str] = Field(default_factory=lambda: ["guaranteed", "risk-free"])

    @field_validator("SLACK_WEBHOOK_URL", mode="before")
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


def get_workspace_config(workspace_key: str) -> dict | None:
    """Return workspace config with resolved api_key and client_id, or None if unknown."""
    for workspace in SMARTLEAD_WORKSPACES:
        if workspace["key"] != workspace_key:
            continue
        api_key = get_secret_value(workspace["api_key_env"])
        client_id_env = workspace.get("client_id_env")
        client_id_raw = get_secret_value(client_id_env) if client_id_env else None
        try:
            client_id = int(client_id_raw) if client_id_raw else None
        except ValueError:
            client_id = None
        return {
            "key": workspace["key"],
            "name": workspace["name"],
            "api_key": api_key,
            "client_id": client_id,
            "client_id_required": bool(client_id_env),
        }
    return None


def list_active_workspaces() -> list[dict]:
    """Return all configured workspaces with their resolved api_key + client_id."""
    return [get_workspace_config(w["key"]) for w in SMARTLEAD_WORKSPACES]
