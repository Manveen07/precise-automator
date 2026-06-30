from app import config


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
