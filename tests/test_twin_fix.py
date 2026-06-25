import asyncio

from app import store
from app.workers import twin_fix


class FakeSmartlead:
    def __init__(self, leads, sequences=None):
        self._leads = leads
        self._sequences = sequences or {"data": []}
        self.updated = []          # (lead_id, email, custom_fields)
        self.sequences_pushed = []

    async def get_leads(self, campaign_id, limit=100, offset=0):
        page = self._leads[offset:offset + limit]
        return {"total_leads": len(self._leads), "data": page}

    async def update_lead(self, campaign_id, lead_id, email, custom_fields):
        self.updated.append((lead_id, email, custom_fields))
        return {"ok": True}

    async def get_sequences(self, campaign_id):
        return self._sequences

    async def update_sequences(self, campaign_id, sequences):
        self.sequences_pushed.append(sequences)
        return {"ok": True}


def _lead(lead_id, email, **cf):
    return {"lead": {"id": lead_id, "email": email, "custom_fields": cf}}


def _twin_doc(**kw):
    return store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan={},
        validation_errors=[], is_twin=True, **kw,
    )


def test_fix_updates_only_changed_leads(monkeypatch):
    async def run():
        doc = _twin_doc(smartlead_campaign_id=42)
        cid = str(doc["_id"])
        fake = FakeSmartlead([
            _lead(1, "a@x.com", **{"Step 1": "A<br>B"}),          # dirty -> changes
            _lead(2, "b@x.com", **{"Step 1": "Clean<br><br>Body"}),  # clean -> no change
            _lead(3, "", **{"Step 1": "A<br>B"}),                  # no email -> skipped
        ])
        monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
        monkeypatch.setattr(twin_fix, "SmartleadService", lambda key: fake)

        summary = await twin_fix._run_twin_fix(cid, None)

        assert summary["total_leads"] == 3
        assert summary["leads_changed"] == 1
        assert [u[0] for u in fake.updated] == [1]
        assert fake.updated[0][2]["Step 1"] == "A<br><br>B"
        # email is sent in the body
        assert fake.updated[0][1] == "a@x.com"
        # persisted
        assert store.get_campaign(cid)["twin_last_fix"]["leads_changed"] == 1

    asyncio.run(run())


def test_fix_resolves_url_over_linked_id(monkeypatch):
    async def run():
        doc = _twin_doc(smartlead_campaign_id=42)
        cid = str(doc["_id"])
        seen = {}
        fake = FakeSmartlead([])

        async def spy_get_leads(campaign_id, limit=100, offset=0):
            seen["campaign_id"] = campaign_id
            return {"total_leads": 0, "data": []}

        fake.get_leads = spy_get_leads
        monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
        monkeypatch.setattr(twin_fix, "SmartleadService", lambda key: fake)

        await twin_fix._run_twin_fix(cid, "https://app.smartlead.ai/app/email-campaign/999/overview")
        assert seen["campaign_id"] == 999

    asyncio.run(run())


def test_fix_flags_greeting_issues(monkeypatch):
    async def run():
        doc = _twin_doc(smartlead_campaign_id=42)
        cid = str(doc["_id"])
        fake = FakeSmartlead([
            _lead(1, "a@x.com", **{"Step 1": "Hi Mark,<br><br>Body", "Step 3": "No greeting"}),
        ])
        monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
        monkeypatch.setattr(twin_fix, "SmartleadService", lambda key: fake)

        summary = await twin_fix._run_twin_fix(cid, None)
        kinds = {f["flag"] for f in summary["greeting_flags"]}
        assert "step1_has_greeting" in kinds
        assert "step3_missing_greeting" in kinds
        # Both fields are already clean — flagging must not trigger a write.
        assert summary["leads_changed"] == 0

    asyncio.run(run())


def test_fix_repushes_template_when_join_reverted(monkeypatch):
    async def run():
        doc = _twin_doc(smartlead_campaign_id=42)
        cid = str(doc["_id"])
        # Smartlead reports a reverted lone-<br> Step 1 join.
        reverted = {"data": [
            {"seq_number": 1, "seq_variants": [{"variant_label": "A",
                "email_body": "Hi {{first_name}},<br>{{Step 1}}<br><br>%signature%", "subject": "{{Subject 1}}"}]},
            {"seq_number": 2, "seq_variants": [{"variant_label": "A",
                "email_body": "{{Step 3}}<br><br>%signature%", "subject": ""}]},
        ]}
        fake = FakeSmartlead([], sequences=reverted)
        monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
        monkeypatch.setattr(twin_fix, "SmartleadService", lambda key: fake)

        summary = await twin_fix._run_twin_fix(cid, None)
        assert summary["template_repushed"] is True
        assert fake.sequences_pushed, "expected the corrected twin sequence to be repushed"

    asyncio.run(run())


def test_fix_no_target_records_error(monkeypatch):
    async def run():
        doc = _twin_doc()  # no smartlead_campaign_id, no url
        cid = str(doc["_id"])
        monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
        summary = await twin_fix._run_twin_fix(cid, None)
        assert summary["errors"]
        assert summary["leads_changed"] == 0

    asyncio.run(run())
