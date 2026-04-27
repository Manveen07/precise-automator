import uuid
from datetime import date, timedelta
from json import JSONDecodeError

from anthropic import AnthropicError
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from redis import Redis
from rq import Queue
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_secret_value, settings
from app.db import get_db
from app.models import (
    CampaignDraft,
    CampaignRequest,
    CampaignRun,
    CampaignRunStep,
    CampaignTemplate,
    ConversationSession,
    SmartleadWorkspace,
)
from app.seed import seed_defaults
from app.services.anthropic_service import AnthropicCampaignService
from app.services.local_plan_service import build_campaign_plan_from_input
from app.services.parser_service import parse_messaging_file
from app.services.sequence_builder import build_smartlead_sequences
from app.services.smartlead_service import SmartleadService
from app.services.validation_service import validate_campaign_plan
from app.workers.sync_campaign import sync_campaign

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
ACTIVE_RUN_STATUSES = {"queued", "running", "retrying"}
SYNC_PROTECTED_STATUSES = {"queued", "running", "retrying", "succeeded"}


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/app", status_code=303)


@router.get("/app")
def dashboard(request: Request, db: Session = Depends(get_db)):
    campaigns = (
        db.query(CampaignRequest)
        .join(SmartleadWorkspace, CampaignRequest.workspace_id == SmartleadWorkspace.id)
        .order_by(CampaignRequest.updated_at.desc())
        .limit(25)
        .all()
    )
    rows = []
    for campaign in campaigns:
        runs = sorted(campaign.runs, key=_run_sort_key, reverse=True)
        latest_run = runs[0] if runs else None
        smartlead_id = next((run.smartlead_campaign_id for run in runs if run.smartlead_campaign_id), None)
        rows.append(
            {
                "id": campaign.id,
                "name": campaign.raw_input_json.get("campaign_name", "Untitled campaign"),
                "status": campaign.status,
                "workspace": campaign.workspace.display_name,
                "updated_at": campaign.updated_at,
                "smartlead_id": smartlead_id,
                "run_status": latest_run.run_status if latest_run else None,
            }
        )
    return templates.TemplateResponse(request, "dashboard.html", {"campaigns": rows})


@router.get("/campaigns/new")
def new_campaign(request: Request, db: Session = Depends(get_db)):
    seed_defaults(db)
    workspaces = db.query(SmartleadWorkspace).filter_by(active=True).order_by(SmartleadWorkspace.display_name).all()
    campaign_templates = db.query(CampaignTemplate).filter_by(active=True).order_by(CampaignTemplate.name).all()
    return templates.TemplateResponse(
        request,
        "campaign_new.html",
        {"workspaces": workspaces, "templates": campaign_templates},
    )


@router.post("/api/campaigns/new")
async def create_campaign_request(
    workspace_key: str = Form(...),
    template_key: str = Form(...),
    campaign_name: str = Form(...),
    max_new_leads_per_day: int = Form(100),
    messaging_text: str = Form(""),
    selected_sequence_name: str = Form(""),
    messaging_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    workspace = db.query(SmartleadWorkspace).filter_by(workspace_key=workspace_key, active=True).first()
    template = db.query(CampaignTemplate).filter_by(template_key=template_key, active=True).first()
    if not workspace or not template:
        raise HTTPException(status_code=400, detail="Invalid workspace or template")

    uploaded_text = await _read_text_upload(messaging_file)
    final_messaging_text = uploaded_text or messaging_text
    parsed_messaging = parse_messaging_file(final_messaging_text, selected_sequence_name)

    raw_input = {
        "workspace_key": workspace_key,
        "template_key": template_key,
        "campaign_name": campaign_name,
        "max_new_leads_per_day": max_new_leads_per_day,
        "messaging_filename": messaging_file.filename if uploaded_text and messaging_file else None,
        "selected_sequence_name": selected_sequence_name.strip() or parsed_messaging.get("selected_campaign"),
        "messaging_text": final_messaging_text,
        "parsed_messaging": parsed_messaging,
    }
    campaign = CampaignRequest(
        workspace_id=workspace.id,
        template_id=template.id,
        raw_input_json=raw_input,
        lead_source_type="none",
        status="drafting",
    )
    db.add(campaign)
    db.commit()
    return RedirectResponse(f"/campaigns/{campaign.id}", status_code=303)


async def _read_text_upload(file: UploadFile | None) -> str:
    if not file or not file.filename:
        return ""
    content = await file.read()
    if not content:
        return ""
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="Messaging upload must be a readable text file")


@router.get("/campaigns/{campaign_id}")
def campaign_detail(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    campaign = db.get(CampaignRequest, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    latest_draft = (
        db.query(CampaignDraft)
        .filter_by(request_id=campaign.id)
        .order_by(CampaignDraft.created_at.desc())
        .first()
    )
    runs = sorted(campaign.runs, key=_run_sort_key, reverse=True)
    latest_smartlead_run = _latest_smartlead_run(campaign)
    active_run = _latest_active_run(campaign)
    payload = {
        "id": campaign.id,
        "campaign_name": campaign.raw_input_json.get("campaign_name", "Untitled campaign"),
        "status": campaign.status,
        "workspace_key": campaign.workspace.workspace_key,
        "raw_input": campaign.raw_input_json,
        "latest_draft": latest_draft,
        "latest_run": runs[0] if runs else None,
        "latest_smartlead_run": latest_smartlead_run,
        "active_run": active_run,
        "runs": runs,
    }
    return templates.TemplateResponse(request, "campaign_detail.html", {"campaign": payload})


@router.post("/api/campaigns/{campaign_id}/generate-draft")
def generate_draft(
    campaign_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    campaign = db.get(CampaignRequest, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    plan = build_campaign_plan_from_input(
        campaign.raw_input_json,
        note="Draft generated deterministically from parsed messaging. Claude is not required for this step.",
    )
    draft, errors = _store_draft(db, campaign, plan, "local_parser")
    payload = {"draft_id": str(draft.id), "source": "local_parser", "validation_status": draft.validation_status, "errors": errors}
    return _api_or_campaign_redirect(request, payload, campaign_id)


@router.post("/api/campaigns/{campaign_id}/revise-draft")
def revise_draft(
    campaign_id: uuid.UUID,
    request: Request,
    revision_instruction: str = Form(...),
    db: Session = Depends(get_db),
):
    campaign = db.get(CampaignRequest, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    latest_draft = _latest_draft(db, campaign.id)
    if not latest_draft:
        raise HTTPException(status_code=400, detail="Generate a draft before revising")

    if not _has_configured_anthropic_key():
        message = "Anthropic API key is not configured; deterministic draft remains unchanged."
        _record_ai_revision_error(db, campaign, latest_draft.id, revision_instruction, message)
        return _api_or_campaign_redirect(request, {"ok": False, "draft_id": str(latest_draft.id), "errors": [message]}, campaign_id)

    try:
        service = AnthropicCampaignService()
        plan = service.revise_campaign_plan(
            latest_plan=latest_draft.draft_json,
            revision_instruction=revision_instruction,
            validation_errors=latest_draft.validation_errors_json,
            template_prompt=campaign.template.system_prompt,
            examples=campaign.template.example_block,
        )
    except JSONDecodeError:
        message = "Claude returned text that was not valid CampaignPlan JSON; deterministic draft remains unchanged."
        _record_ai_revision_error(db, campaign, latest_draft.id, revision_instruction, message)
        return _api_or_campaign_redirect(request, {"ok": False, "draft_id": str(latest_draft.id), "errors": [message]}, campaign_id)
    except AnthropicError as exc:
        message = f"Claude revision failed: {exc.__class__.__name__}; deterministic draft remains unchanged."
        _record_ai_revision_error(db, campaign, latest_draft.id, revision_instruction, message)
        return _api_or_campaign_redirect(request, {"ok": False, "draft_id": str(latest_draft.id), "errors": [message]}, campaign_id)

    draft, errors = _store_draft(db, campaign, plan, settings.ANTHROPIC_MODEL, commit=False)
    latest_draft.validation_status = "superseded"
    _upsert_conversation_session(
        db,
        campaign.id,
        draft.id,
        [
            {"role": "user", "event": "ai_revision_instruction", "content": revision_instruction},
            {"role": "assistant", "event": "ai_revised_draft", "draft_id": str(draft.id), "errors": errors},
        ],
    )
    db.commit()
    return _api_or_campaign_redirect(
        request,
        {"ok": True, "draft_id": str(draft.id), "validation_status": draft.validation_status, "errors": errors},
        campaign_id,
    )


@router.post("/api/campaigns/{campaign_id}/approve")
def approve_campaign(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    campaign = db.get(CampaignRequest, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    draft = (
        db.query(CampaignDraft)
        .filter_by(request_id=campaign.id)
        .order_by(CampaignDraft.created_at.desc())
        .first()
    )
    if not draft or draft.validation_status != "valid":
        raise HTTPException(status_code=400, detail="Latest draft must be valid before approval")
    draft.validation_status = "approved"
    campaign.status = "approved"
    db.commit()
    return _api_or_campaign_redirect(request, {"ok": True, "draft_id": str(draft.id)}, campaign_id)


@router.post("/api/campaigns/{campaign_id}/sync")
def enqueue_sync(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    campaign = db.get(CampaignRequest, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    draft = (
        db.query(CampaignDraft)
        .filter_by(request_id=campaign.id, validation_status="approved")
        .order_by(CampaignDraft.created_at.desc())
        .first()
    )
    if not draft:
        raise HTTPException(status_code=400, detail="Campaign needs an approved draft before sync")

    existing_run = _protected_sync_run(campaign, draft.id)
    if existing_run:
        _align_campaign_status_for_run(campaign, existing_run)
        db.commit()
        return _api_or_campaign_redirect(
            request,
            _sync_payload(existing_run, deduped=True),
            campaign_id,
        )

    retry_run = _retryable_failed_run(campaign, draft.id)
    if retry_run:
        db.query(CampaignRunStep).filter_by(run_id=retry_run.id).delete(synchronize_session=False)
        retry_run.run_status = "queued"
        retry_run.error_text = None
        retry_run.started_at = None
        retry_run.finished_at = None
        run = retry_run
    else:
        run = CampaignRun(
            request_id=campaign.id,
            draft_id=draft.id,
            run_status="queued",
            idempotency_key=_sync_idempotency_key(campaign.id, draft.id),
        )
        db.add(run)
    campaign.status = "syncing"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing_run = _run_by_idempotency_key(db, _sync_idempotency_key(campaign.id, draft.id))
        if not existing_run:
            raise
        _align_campaign_status_for_run(campaign, existing_run)
        db.commit()
        return _api_or_campaign_redirect(request, _sync_payload(existing_run, deduped=True), campaign_id)

    queue = Queue("campaign_sync", connection=Redis.from_url(settings.REDIS_URL))
    queue.enqueue(sync_campaign, str(run.id), job_timeout=900)
    return _api_or_campaign_redirect(request, _sync_payload(run), campaign_id)


def _sync_payload(run: CampaignRun, deduped: bool = False) -> dict:
    return {
        "run_id": str(run.id),
        "status": run.run_status,
        "smartlead_campaign_id": run.smartlead_campaign_id,
        "deduped": deduped,
    }


def _sync_idempotency_key(campaign_id: uuid.UUID, draft_id: uuid.UUID) -> str:
    return f"{campaign_id}:{draft_id}:smartlead_sync"


def _run_by_idempotency_key(db: Session, idempotency_key: str) -> CampaignRun | None:
    return db.query(CampaignRun).filter_by(idempotency_key=idempotency_key).first()


def _protected_sync_run(campaign: CampaignRequest, draft_id: uuid.UUID) -> CampaignRun | None:
    smartlead_run = _latest_smartlead_run(campaign)
    if smartlead_run:
        return smartlead_run
    runs = sorted(campaign.runs, key=_run_sort_key, reverse=True)
    return next(
        (
            run
            for run in runs
            if run.draft_id == draft_id and run.run_status in SYNC_PROTECTED_STATUSES
        ),
        None,
    )


def _retryable_failed_run(campaign: CampaignRequest, draft_id: uuid.UUID) -> CampaignRun | None:
    runs = sorted(campaign.runs, key=_run_sort_key, reverse=True)
    return next(
        (
            run
            for run in runs
            if run.draft_id == draft_id and run.run_status == "failed" and not run.smartlead_campaign_id
        ),
        None,
    )


def _latest_active_run(campaign: CampaignRequest) -> CampaignRun | None:
    runs = sorted(campaign.runs, key=_run_sort_key, reverse=True)
    return next((run for run in runs if run.run_status in ACTIVE_RUN_STATUSES), None)


def _latest_smartlead_run(campaign: CampaignRequest) -> CampaignRun | None:
    runs = sorted(campaign.runs, key=_run_sort_key, reverse=True)
    return next((run for run in runs if run.smartlead_campaign_id), None)


def _align_campaign_status_for_run(campaign: CampaignRequest, run: CampaignRun) -> None:
    if run.run_status in ACTIVE_RUN_STATUSES:
        campaign.status = "syncing"
    elif run.run_status == "succeeded":
        campaign.status = "synced"


@router.get("/api/campaigns/{campaign_id}/status")
def campaign_status(campaign_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    campaign = db.get(CampaignRequest, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    runs = (
        db.query(CampaignRun)
        .filter_by(request_id=campaign.id)
        .order_by(CampaignRun.started_at.desc().nullslast())
        .limit(5)
        .all()
    )
    return {
        "campaign_status": campaign.status,
        "runs": [
            {
                "run_id": str(run.id),
                "run_status": run.run_status,
                "smartlead_campaign_id": run.smartlead_campaign_id,
                "error_text": run.error_text,
                "steps": [
                    {
                        "step_order": step.step_order,
                        "step_name": step.step_name,
                        "status": step.status,
                        "duration_ms": step.duration_ms,
                        "error_text": step.error_text,
                    }
                    for step in sorted(run.steps, key=lambda item: item.step_order)
                ],
            }
            for run in runs
        ],
    }


@router.get("/api/campaigns/{campaign_id}/smartlead")
async def smartlead_campaign_snapshot(campaign_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    campaign = _load_campaign(db, campaign_id)
    smartlead_campaign_id = _latest_smartlead_campaign_id(campaign)
    smartlead = _smartlead_for_campaign(campaign)
    return {
        "smartlead_campaign_id": smartlead_campaign_id,
        "campaign": await smartlead.get_campaign(smartlead_campaign_id),
        "sequences": await smartlead.get_sequences(smartlead_campaign_id),
    }


@router.post("/api/campaigns/{campaign_id}/smartlead/apply")
async def apply_latest_draft_to_smartlead(
    campaign_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    campaign = _load_campaign(db, campaign_id)
    smartlead_campaign_id = _latest_smartlead_campaign_id(campaign)
    draft = _latest_syncable_draft(db, campaign.id)
    if not draft:
        raise HTTPException(status_code=400, detail="No valid or approved draft is available to apply")

    workspace_keys = {row.workspace_key for row in db.query(SmartleadWorkspace).filter_by(active=True).all()}
    errors = validate_campaign_plan(draft.draft_json, workspace_keys)
    if errors:
        raise HTTPException(status_code=400, detail={"message": "Draft validation failed", "errors": errors})

    smartlead = _smartlead_for_campaign(campaign)
    sequences = build_smartlead_sequences(draft.draft_json["sequence"])
    responses = {
        "settings": await smartlead.apply_v1_settings(
            smartlead_campaign_id,
            draft.draft_json.get("settings", {}).get("ooo_restart_delay_days", 10),
        ),
        "schedule": await smartlead.update_schedule(smartlead_campaign_id, draft.draft_json["schedule"]),
        "sequences": await smartlead.update_sequences(smartlead_campaign_id, sequences),
    }
    email_account_ids = draft.draft_json.get("inbox_selection", {}).get("email_account_ids") or []
    if email_account_ids:
        responses["email_accounts"] = await smartlead.attach_email_accounts(smartlead_campaign_id, email_account_ids)
    return _api_or_campaign_redirect(
        request,
        {"ok": True, "smartlead_campaign_id": smartlead_campaign_id, "responses": responses},
        campaign_id,
    )


@router.delete("/api/campaigns/{campaign_id}/smartlead")
async def delete_or_archive_smartlead_campaign(
    campaign_id: uuid.UUID,
    mode: str = Query("archive", pattern="^(archive|delete)$"),
    db: Session = Depends(get_db),
) -> dict:
    campaign = _load_campaign(db, campaign_id)
    smartlead_campaign_id = _latest_smartlead_campaign_id(campaign)
    smartlead = _smartlead_for_campaign(campaign)
    if mode == "archive":
        response = await smartlead.archive_campaign(smartlead_campaign_id)
    else:
        response = await smartlead.delete_campaign(smartlead_campaign_id)
    return {"ok": True, "mode": mode, "smartlead_campaign_id": smartlead_campaign_id, "response": response}


@router.post("/api/campaigns/{campaign_id}/smartlead/archive")
async def archive_smartlead_campaign(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    campaign = _load_campaign(db, campaign_id)
    smartlead_campaign_id = _latest_smartlead_campaign_id(campaign)
    response = await _smartlead_for_campaign(campaign).archive_campaign(smartlead_campaign_id)
    return _api_or_campaign_redirect(
        request,
        {"ok": True, "mode": "archive", "smartlead_campaign_id": smartlead_campaign_id, "response": response},
        campaign_id,
    )


@router.post("/api/campaigns/{campaign_id}/smartlead/delete")
async def delete_smartlead_campaign(campaign_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    campaign = _load_campaign(db, campaign_id)
    smartlead_campaign_id = _latest_smartlead_campaign_id(campaign)
    response = await _smartlead_for_campaign(campaign).delete_campaign(smartlead_campaign_id)
    return _api_or_campaign_redirect(
        request,
        {"ok": True, "mode": "delete", "smartlead_campaign_id": smartlead_campaign_id, "response": response},
        campaign_id,
    )


@router.get("/api/campaigns/{campaign_id}/analytics")
async def smartlead_campaign_analytics(
    campaign_id: uuid.UUID,
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    campaign = _load_campaign(db, campaign_id)
    smartlead_campaign_id = _latest_smartlead_campaign_id(campaign)
    smartlead = _smartlead_for_campaign(campaign)
    end_value = end_date or date.today().isoformat()
    start_value = start_date or (date.today() - timedelta(days=30)).isoformat()
    return {
        "smartlead_campaign_id": smartlead_campaign_id,
        "top_level": await smartlead.get_campaign_analytics(smartlead_campaign_id),
        "sequence_statistics": await smartlead.get_campaign_statistics(smartlead_campaign_id),
        "lead_statistics": await smartlead.get_campaign_lead_statistics(smartlead_campaign_id),
        "performance": await smartlead.get_campaign_performance(
            start_value,
            end_value,
            campaign_ids=[smartlead_campaign_id],
        ),
    }


def _latest_draft(db: Session, request_id: uuid.UUID) -> CampaignDraft | None:
    return (
        db.query(CampaignDraft)
        .filter_by(request_id=request_id)
        .order_by(CampaignDraft.created_at.desc())
        .first()
    )


def _api_or_campaign_redirect(request: Request | None, payload: dict, campaign_id: uuid.UUID):
    if request and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)
    return payload


def _load_campaign(db: Session, campaign_id: uuid.UUID) -> CampaignRequest:
    campaign = db.get(CampaignRequest, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def _latest_syncable_draft(db: Session, request_id: uuid.UUID) -> CampaignDraft | None:
    drafts = (
        db.query(CampaignDraft)
        .filter_by(request_id=request_id)
        .order_by(CampaignDraft.created_at.desc())
        .all()
    )
    return next((draft for draft in drafts if draft.validation_status in {"valid", "approved"}), None)


def _latest_smartlead_campaign_id(campaign: CampaignRequest) -> int:
    runs = sorted(campaign.runs, key=_run_sort_key, reverse=True)
    for run in runs:
        if run.smartlead_campaign_id:
            return run.smartlead_campaign_id
    raise HTTPException(status_code=400, detail="No Smartlead campaign has been created for this request yet")


def _smartlead_for_campaign(campaign: CampaignRequest) -> SmartleadService:
    api_key = get_secret_value(campaign.workspace.api_key_env_name)
    if not api_key:
        raise HTTPException(status_code=400, detail=f"Missing Smartlead API key: {campaign.workspace.api_key_env_name}")
    return SmartleadService(api_key)


def _run_sort_key(run: CampaignRun) -> str:
    timestamp = run.started_at or run.finished_at
    return timestamp.isoformat() if timestamp else run.id.hex


def _store_draft(
    db: Session,
    campaign: CampaignRequest,
    plan: dict,
    model_name: str,
    commit: bool = True,
) -> tuple[CampaignDraft, list[str]]:
    workspace_keys = {row.workspace_key for row in db.query(SmartleadWorkspace).filter_by(active=True).all()}
    errors = validate_campaign_plan(plan, workspace_keys)
    draft = CampaignDraft(
        request_id=campaign.id,
        draft_json=plan,
        prompt_version=campaign.template.schema_version,
        model_name=model_name,
        validation_status="invalid" if errors else "valid",
        validation_errors_json=errors,
    )
    db.add(draft)
    campaign.status = "needs_revision" if errors else "drafting"
    _upsert_conversation_session(
        db,
        campaign.id,
        draft.id,
        [
            {
                "role": "assistant",
                "event": "generated_draft",
                "source": model_name,
                "draft_id": str(draft.id),
                "errors": errors,
            }
        ],
    )
    if commit:
        db.commit()
    return draft, errors


def _has_configured_anthropic_key() -> bool:
    key = settings.ANTHROPIC_API_KEY.strip()
    return bool(key and key != "replace_me")


def _record_ai_revision_error(
    db: Session,
    campaign: CampaignRequest,
    latest_draft_id: uuid.UUID,
    revision_instruction: str,
    message: str,
) -> None:
    campaign.status = "needs_revision"
    _upsert_conversation_session(
        db,
        campaign.id,
        latest_draft_id,
        [
            {"role": "user", "event": "ai_revision_instruction", "content": revision_instruction},
            {"role": "assistant", "event": "ai_revision_failed", "error": message},
        ],
    )
    db.commit()


def _upsert_conversation_session(
    db: Session,
    request_id: uuid.UUID,
    latest_draft_id: uuid.UUID,
    messages: list[dict],
) -> None:
    session = db.query(ConversationSession).filter_by(request_id=request_id).first()
    if not session:
        session = ConversationSession(
            request_id=request_id,
            message_log_json=[],
            latest_draft_id=latest_draft_id,
        )
        db.add(session)
    session.message_log_json = [*session.message_log_json, *messages]
    session.latest_draft_id = latest_draft_id
