import asyncio
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import CampaignRun, WebhookEvent
from app.routes import webhooks


class FakeRequest:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, query_params: dict[str, str] | None = None):
        self._body = body
        self.headers = headers or {}
        self.query_params = query_params or {}

    async def body(self) -> bytes:
        return self._body


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter_by(self, **kwargs):
        return FakeQuery(
            [
                row
                for row in self.rows
                if all(getattr(row, key, None) == value for key, value in kwargs.items())
            ]
        )

    def order_by(self, *_args):
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.commits = 0

    def query(self, model):
        if model is CampaignRun:
            return FakeQuery(self.rows)
        return FakeQuery([])

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commits += 1


def test_smartlead_webhook_requires_valid_signature_when_secret_configured(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "SMARTLEAD_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(webhooks.settings, "APP_ENV", "production")
    body = json.dumps({"campaign_id": 123, "event_type": "EMAIL_REPLY"}).encode()
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    request = FakeRequest(body, headers={"X-Smartlead-Signature": signature})
    workspace_id = uuid.uuid4()
    db = FakeDb([SimpleNamespace(smartlead_campaign_id=123, request=SimpleNamespace(workspace_id=workspace_id))])

    result = asyncio.run(webhooks.smartlead_webhook(request, db=db))

    assert result == {"ok": True}
    assert db.commits == 1
    event = db.added[0]
    assert isinstance(event, WebhookEvent)
    assert event.workspace_id == workspace_id
    assert event.smartlead_campaign_id == 123


def test_smartlead_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "SMARTLEAD_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(webhooks.settings, "APP_ENV", "production")
    body = b'{"campaign_id": 123}'
    request = FakeRequest(body, headers={"X-Smartlead-Signature": "bad"})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(webhooks.smartlead_webhook(request, db=FakeDb([])))

    assert exc.value.status_code == 401


def test_smartlead_webhook_accepts_shared_secret_query_param(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "SMARTLEAD_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(webhooks.settings, "APP_ENV", "production")
    body = b'{"smartlead_campaign_id": 123, "type": "LEAD_CATEGORY_UPDATED"}'
    request = FakeRequest(body, query_params={"secret": "secret"})
    db = FakeDb([])

    assert asyncio.run(webhooks.smartlead_webhook(request, db=db)) == {"ok": True}
    assert db.added[0].event_type == "LEAD_CATEGORY_UPDATED"
