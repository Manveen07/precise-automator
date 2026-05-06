from functools import lru_cache
import os
from pathlib import Path
import re

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import dotenv_values


SMARTLEAD_WORKSPACES = [
    {
        "key": "preciselead",
        "name": "PreciseLead",
        "api_key_env": "SMARTLEAD_PRECISELEAD_API_KEY",
        "self_client_name": "PreciseLeads",
    },
    {
        "key": "belardi_wong",
        "name": "Belardi Wong",
        "api_key_env": "SMARTLEAD_BELARDI_WONG_API_KEY",
    },
    {
        "key": "darlean",
        "name": "Darlean",
        "api_key_env": "SMARTLEAD_DARLEAN_API_KEY",
    },
]


SMARTLEAD_CLIENT_RULES = {
    "preciselead": [
        {
            "key": "melior",
            "name": "Ryan Markman / Melior",
            "client_id": 12256,
            "aliases": ("melior", "ryan markman"),
        },
        {
            "key": "svsg",
            "name": "Srivatsan / SVSG",
            "client_id": 145916,
            "aliases": ("osc", "staff ai", "staffai", "svsg", "sri", "srivatsan"),
        },
        {
            "key": "avench",
            "name": "Anuroop / Avench",
            "client_id": 88657,
            "aliases": ("avench", "avenge", "anuroop"),
        },
    ]
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "local"
    APP_BASE_URL: str = "http://localhost:8000"
    APP_SECRET_KEY: str = "replace_me"
    APP_USERNAME: str = ""
    APP_PASSWORD: str = ""
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
    """Return workspace config with resolved api_key, or None if unknown."""
    for workspace in SMARTLEAD_WORKSPACES:
        if workspace["key"] != workspace_key:
            continue
        api_key = get_secret_value(workspace["api_key_env"])
        return {
            "key": workspace["key"],
            "name": workspace["name"],
            "api_key": api_key,
            "self_client_name": workspace.get("self_client_name") or workspace["name"],
        }
    return None


def list_active_workspaces() -> list[dict]:
    """Return all configured workspaces with their resolved api_key."""
    return [get_workspace_config(w["key"]) for w in SMARTLEAD_WORKSPACES]


def infer_smartlead_client(workspace_key: str, campaign_name: str) -> dict | None:
    """Infer the Smartlead agency client from a campaign name.

    Returning None intentionally means the campaign should be created without
    client_id, which keeps it under the PreciseLeads/master workspace.
    """
    for rule in SMARTLEAD_CLIENT_RULES.get(workspace_key, []):
        matched_alias = _matched_client_alias(campaign_name, rule["aliases"])
        if matched_alias:
            return {
                "key": rule["key"],
                "name": rule["name"],
                "client_id": rule["client_id"],
                "matched_alias": matched_alias,
            }
    return None


def _matched_client_alias(campaign_name: str, aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if _client_alias_matches(campaign_name, alias):
            return alias
    return None


def _client_alias_matches(campaign_name: str, alias: str) -> bool:
    alias_key = _compact_match_key(alias)
    if not alias_key:
        return False
    if len(alias_key) <= 3:
        words = {_compact_match_key(word) for word in re.findall(r"[a-z0-9]+", campaign_name.lower())}
        return alias_key in words
    return alias_key in _compact_match_key(campaign_name)


def _compact_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
