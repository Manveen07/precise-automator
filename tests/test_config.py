import json

from app import config


def test_get_heyreach_account_ids_for_client_returns_ids(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    mapping = {"Mythic": [101, 102, 103], "OSC": [201]}
    monkeypatch.setenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", json.dumps(mapping))
    result = get_heyreach_account_ids_for_client("mythic", "Mythic")
    assert result == [101, 102, 103]


def test_get_heyreach_account_ids_for_client_case_insensitive(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    mapping = {"mythic": [101, 102]}
    monkeypatch.setenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", json.dumps(mapping))
    result = get_heyreach_account_ids_for_client("mythic", "MYTHIC")
    assert result == [101, 102]


def test_get_heyreach_account_ids_for_client_none_when_no_match(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    mapping = {"Mythic": [101]}
    monkeypatch.setenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", json.dumps(mapping))
    result = get_heyreach_account_ids_for_client("mythic", "Unknown Client")
    assert result is None


def test_get_heyreach_account_ids_for_client_none_when_no_env(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    monkeypatch.delenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", raising=False)
    result = get_heyreach_account_ids_for_client("mythic", "Mythic")
    assert result is None


def test_get_heyreach_account_ids_none_client_name(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    mapping = {"Mythic": [101]}
    monkeypatch.setenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", json.dumps(mapping))
    result = get_heyreach_account_ids_for_client("mythic", None)
    assert result is None


def test_workspace_config_exposes_heyreach_key(monkeypatch):
    monkeypatch.setenv("HEYREACH_PRECISELEAD_API_KEY", "hr-key-123")
    cfg = config.get_workspace_config("preciselead")
    assert cfg is not None
    assert "heyreach_api_key" in cfg
    assert cfg["heyreach_api_key"] == "hr-key-123"


def test_workspace_config_heyreach_key_none_when_unset(monkeypatch):
    monkeypatch.delenv("HEYREACH_BELARDI_WONG_API_KEY", raising=False)
    cfg = config.get_workspace_config("belardi_wong")
    assert cfg is not None
    assert cfg.get("heyreach_api_key") in (None, "")
