"""Test fixtures.

Replace the real MongoClient in app.store with a mongomock client so route
tests don't need network access. Each test gets a fresh in-memory client.
"""

import mongomock
import pytest

from app import store


@pytest.fixture(autouse=True)
def fresh_mongomock(monkeypatch):
    fake_client = mongomock.MongoClient()
    monkeypatch.setattr(store, "_client", lambda: fake_client)
    monkeypatch.setenv("APP_USERNAME", "test-user")
    monkeypatch.setenv("APP_PASSWORD", "test-password")
    yield
