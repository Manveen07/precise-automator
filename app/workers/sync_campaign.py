import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import get_secret_value, settings
from app.db import SessionLocal
from app.models import CampaignDraft, CampaignRun, CampaignRunStep, SmartleadWorkspace
from app.services.sequence_builder import build_smartlead_sequences
from app.services.smartlead_service import SmartleadService
from app.services.validation_service import validate_campaign_plan


def sync_campaign(run_id: str) -> None:
    asyncio.run(_sync_campaign(run_id))


async def _sync_campaign(run_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.get(CampaignRun, run_id)
        if not run:
            raise RuntimeError(f"Campaign run not found: {run_id}")
        run.run_status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        draft = db.get(CampaignDraft, run.draft_id)
        if not draft:
            raise RuntimeError("Campaign draft not found")

        workspace_keys = {row.workspace_key for row in db.query(SmartleadWorkspace).filter_by(active=True).all()}
        errors = validate_campaign_plan(draft.draft_json, workspace_keys)
        if errors:
            _mark_failed(db, run, "Validation failed before sync: " + "; ".join(errors))
            return

        workspace = db.query(SmartleadWorkspace).filter_by(workspace_key=draft.draft_json["workspace_key"]).one()
        api_key = get_secret_value(workspace.api_key_env_name)
        if not api_key:
            raise RuntimeError(f"Missing Smartlead API key: {workspace.api_key_env_name}")
        smartlead = SmartleadService(api_key)

        campaign = await _log_step(
            db,
            run,
            1,
            "create_campaign",
            {"name": draft.draft_json["campaign_name"], "client_id": workspace.client_id},
            smartlead.create_campaign(draft.draft_json["campaign_name"], workspace.client_id),
        )
        campaign_id = _extract_campaign_id(campaign)
        run.smartlead_campaign_id = campaign_id
        db.commit()

        ooo_delay_days = int(draft.draft_json.get("settings", {}).get("ooo_restart_delay_days", 10))
        await _log_step(
            db,
            run,
            2,
            "apply_settings",
            {"ooo_delay_days": ooo_delay_days},
            smartlead.apply_v1_settings(campaign_id, ooo_delay_days),
        )
        await _log_step(
            db,
            run,
            3,
            "apply_schedule",
            draft.draft_json["schedule"],
            smartlead.post(f"campaigns/{campaign_id}/schedule", draft.draft_json["schedule"]),
        )
        sequences = build_smartlead_sequences(draft.draft_json["sequence"])
        await _log_step(
            db,
            run,
            4,
            "push_sequences",
            {"sequences": sequences},
            smartlead.post(f"campaigns/{campaign_id}/sequences", {"sequences": sequences}),
        )

        step_order = 5
        email_account_ids = draft.draft_json.get("inbox_selection", {}).get("email_account_ids") or []
        if email_account_ids:
            await _log_step(
                db,
                run,
                step_order,
                "attach_email_accounts",
                {"email_account_ids": email_account_ids},
                smartlead.attach_email_accounts(campaign_id, email_account_ids),
            )
            step_order += 1

        webhook_url = f"{settings.APP_BASE_URL}/api/webhooks/smartlead"
        logged_webhook_url = webhook_url
        if settings.SMARTLEAD_WEBHOOK_SECRET:
            webhook_url = f"{webhook_url}?{urlencode({'secret': settings.SMARTLEAD_WEBHOOK_SECRET})}"
            logged_webhook_url = f"{logged_webhook_url}?secret=[redacted]"
        if settings.APP_BASE_URL.startswith("https://"):
            await _log_step(
                db,
                run,
                step_order,
                "create_webhook",
                {"webhook_url": logged_webhook_url, "event_types": ["EMAIL_REPLY", "LEAD_CATEGORY_UPDATED"]},
                smartlead.create_webhook(campaign_id, webhook_url),
            )
            step_order += 1
        else:
            _log_skipped_step(
                db,
                run,
                step_order,
                "create_webhook",
                {"webhook_url": logged_webhook_url},
                "APP_BASE_URL is not https; webhook creation skipped for local safety.",
            )
            step_order += 1

        verification = {
            "campaign": await smartlead.get_campaign(campaign_id),
            "sequences": await smartlead.get_sequences(campaign_id),
        }
        await _log_step(db, run, step_order, "verify_campaign", {}, _already_done(verification))

        run.run_status = "succeeded"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        run = db.get(CampaignRun, run_id)
        if run:
            _mark_failed(db, run, _error_text(exc))
        raise
    finally:
        db.close()


async def _log_step(
    db: Session,
    run: CampaignRun,
    order: int,
    name: str,
    request_json: dict,
    awaitable,
) -> dict:
    step = CampaignRunStep(run_id=run.id, step_order=order, step_name=name, status="running", request_json=request_json)
    db.add(step)
    db.commit()
    started = datetime.now(timezone.utc)
    try:
        response = await awaitable
        step.status = "succeeded"
        step.response_json = response if isinstance(response, dict) else {"ok": True}
        step.duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        db.commit()
        return step.response_json
    except Exception as exc:
        step.status = "failed"
        step.error_text = _error_text(exc)
        step.duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        db.commit()
        raise


def _log_skipped_step(
    db: Session,
    run: CampaignRun,
    order: int,
    name: str,
    request_json: dict,
    message: str,
) -> None:
    step = CampaignRunStep(
        run_id=run.id,
        step_order=order,
        step_name=name,
        status="skipped",
        request_json=request_json,
        response_json={"skipped": True, "reason": message},
        error_text=message,
        duration_ms=0,
    )
    db.add(step)
    db.commit()


def _mark_failed(db: Session, run: CampaignRun, message: str) -> None:
    run.run_status = "failed"
    run.error_text = message
    run.finished_at = datetime.now(timezone.utc)
    db.commit()


def _extract_campaign_id(response: dict) -> int:
    for candidate in (response, response.get("data") if isinstance(response.get("data"), dict) else None):
        if not candidate:
            continue
        for key in ("id", "campaign_id"):
            if key in candidate and candidate[key] is not None:
                try:
                    campaign_id = int(candidate[key])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"Smartlead create_campaign returned non-numeric {key}: {_response_snippet(response)}"
                    ) from exc
                if campaign_id <= 0:
                    raise RuntimeError(
                        f"Smartlead create_campaign returned invalid {key}: {_response_snippet(response)}"
                    )
                return campaign_id
    raise RuntimeError(f"Smartlead create_campaign response missing id/campaign_id: {_response_snippet(response)}")


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        return (
            f"{exc.__class__.__name__}: HTTP {response.status_code} for {response.request.method} "
            f"{response.request.url}; body={_truncate(response.text)}"
        )
    return str(exc)


def _response_snippet(response: dict) -> str:
    return _truncate(json.dumps(response, default=str, sort_keys=True))


def _truncate(value: str, limit: int = 1000) -> str:
    return value if len(value) <= limit else value[:limit] + "...[truncated]"


async def _already_done(value: dict) -> dict:
    return value
