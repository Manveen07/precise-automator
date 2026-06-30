"""Campaign routes — Mongo-backed.

A campaign is one Mongo document. Lifecycle:
- `drafting`: plan exists, has validation errors, not synced
- `ready`: plan is valid, not yet synced
- `syncing`: background task is creating/updating the Smartlead campaign
- `synced`: Smartlead campaign exists; doc holds smartlead_campaign_id
- `failed`: last sync attempt failed; last_sync_error populated

Re-syncing a `synced` campaign updates the existing Smartlead campaign in
place (settings, schedule, sequences) — never creates a duplicate.
"""

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from json import JSONDecodeError
import logging
import re

from anthropic import AnthropicError

_log = logging.getLogger("app.routes.campaigns")
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, Response, UploadFile
import httpx
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import (
    SMARTLEAD_WORKSPACES,
    get_sheet_client_for_workspace,
    get_workspace_config,
    infer_smartlead_client,
    settings,
    static_asset_version,
)
from app.services.anthropic_service import AnthropicCampaignService
from app.services.inbox_selection_service import select_inboxes
from app.services.inbox_sheet_service import InboxSheetError, fetch_inbox_rows, fetch_last_sync
from app.services.local_plan_service import build_campaign_plan_from_input, build_twin_campaign_plan
from app.services.parser_service import parse_messaging_file
from app.services.sequence_builder import smartlead_html_to_text
from app.services.smartlead_import_service import build_campaign_plan_from_smartlead
from app.services.smartlead_service import SmartleadService
from app.services.spintax_service import apply_spintax_to_plan, count_bodies_needing_spintax
from app.services.validation_service import validate_campaign_plan
from app import store
from app.schemas.campaign_plan import linkedin_messages
from app.workers.heyreach_create import create_heyreach_campaign_now
from app.workers.sync_campaign import sync_campaign_now
from app.workers.twin_fix import run_twin_fix_now

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
# Render Smartlead email bodies (HTML with <br> + spintax) as readable plain text.
templates.env.filters["smartlead_text"] = smartlead_html_to_text
templates.env.globals["asset_version"] = static_asset_version()


# ----- Pages ----- #


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/app", status_code=303)


@router.get("/app")
def dashboard(request: Request):
    docs = store.list_recent_campaigns(limit=25)
    rows = [_dashboard_row(doc) for doc in docs]
    return templates.TemplateResponse(request, "dashboard.html", {"campaigns": rows})


@router.get("/campaigns/new")
def new_campaign(request: Request):
    return templates.TemplateResponse(
        request,
        "campaign_new.html",
        {"workspaces": [{"key": w["key"], "name": w["name"]} for w in SMARTLEAD_WORKSPACES]},
    )


@router.get("/campaigns/{campaign_id}")
def campaign_detail(campaign_id: str, request: Request):
    doc = _require_campaign(campaign_id)
    payload = _detail_payload(doc)
    return templates.TemplateResponse(request, "campaign_detail.html", {"campaign": payload})


@router.get("/api/campaigns/{campaign_id}/status")
def campaign_status(campaign_id: str) -> dict:
    doc = _require_campaign(campaign_id)
    return {
        "id": str(doc["_id"]),
        "status": doc.get("status", "drafting"),
        "status_label": _status_label(doc.get("status", "drafting")),
        "smartlead_campaign_id": doc.get("smartlead_campaign_id"),
        "last_sync_error": doc.get("last_sync_error"),
        "twin_fix_running": doc.get("twin_fix_running", False),
        "heyreach_creating": doc.get("heyreach_creating", False),
        "heyreach_campaign_url": doc.get("heyreach_campaign_url"),
        "updated_at": store.to_display_tz(doc.get("updated_at")).isoformat() if doc.get("updated_at") else None,
    }


# ----- Mutations: create, revise, spintax ----- #


@router.post("/api/campaigns/new")
async def create_campaign(
    workspace_key: str = Form(...),
    campaign_name: str = Form(""),
    max_new_leads_per_day: int = Form(100),
    smartlead_campaign_ref: str = Form(""),
    messaging_text: str = Form(""),
    selected_sequence_name: str = Form(""),
    is_twin: bool = Form(False),
    messaging_file: UploadFile | None = File(None),
) -> RedirectResponse:
    workspace = get_workspace_config(workspace_key)
    if not workspace:
        raise HTTPException(status_code=400, detail=f"Unknown workspace: {workspace_key}")
    smartlead_campaign_id = _parse_optional_smartlead_campaign_ref(smartlead_campaign_ref)

    uploaded_text = await _read_text_upload(messaging_file)
    final_text = uploaded_text or messaging_text
    parsed_messaging = parse_messaging_file(final_text, selected_sequence_name)
    link_only_existing_campaign = bool(smartlead_campaign_id and not final_text.strip())
    imported_plan = (
        await _try_import_existing_smartlead_plan(workspace, smartlead_campaign_id, max_new_leads_per_day)
        if link_only_existing_campaign and smartlead_campaign_id
        else None
    )
    campaign_name = _resolve_campaign_name(
        campaign_name,
        smartlead_campaign_id,
        selected_sequence_name,
        parsed_messaging,
        imported_plan.get("campaign_name") if imported_plan else None,
    )
    smartlead_client = infer_smartlead_client(workspace_key, campaign_name)

    raw_input = {
        "workspace_key": workspace_key,
        "campaign_name": campaign_name,
        "smartlead_client": smartlead_client,
        "smartlead_campaign_ref": smartlead_campaign_ref.strip(),
        "smartlead_campaign_id": smartlead_campaign_id,
        "max_new_leads_per_day": max_new_leads_per_day,
        "messaging_filename": messaging_file.filename if uploaded_text and messaging_file else None,
        "selected_sequence_name": selected_sequence_name.strip() or parsed_messaging.get("selected_campaign"),
        "messaging_text": final_text,
        "parsed_messaging": parsed_messaging,
    }
    if is_twin:
        plan = build_twin_campaign_plan(raw_input)
        errors = validate_campaign_plan(plan, _active_workspace_keys())
        status = None
    elif imported_plan:
        plan = imported_plan
        errors = validate_campaign_plan(plan, _active_workspace_keys())
        status = None
    elif link_only_existing_campaign:
        plan = {}
        errors = []
        status = "linked"
    else:
        plan = build_campaign_plan_from_input(
            raw_input,
            note="Plan generated deterministically from parsed messaging.",
        )
        errors = validate_campaign_plan(plan, _active_workspace_keys())
        status = None
    doc = store.insert_campaign(
        workspace_key=workspace_key,
        campaign_name=campaign_name,
        raw_input=raw_input,
        plan=plan,
        validation_errors=errors,
        smartlead_campaign_id=smartlead_campaign_id,
        smartlead_client_id=smartlead_client["client_id"] if smartlead_client else None,
        smartlead_client_name=smartlead_client["name"] if smartlead_client else None,
        smartlead_client_match=smartlead_client["matched_alias"] if smartlead_client else None,
        status=status,
        is_twin=is_twin,
        twin_smartlead_url=None,
    )
    return RedirectResponse(f"/campaigns/{doc['_id']}", status_code=303)


@router.post("/api/campaigns/{campaign_id}/regenerate")
def regenerate_plan(campaign_id: str, request: Request):
    doc = _require_campaign(campaign_id)
    plan = build_campaign_plan_from_input(
        doc["raw_input"],
        note="Plan regenerated from parsed messaging.",
    )
    errors = validate_campaign_plan(plan, _active_workspace_keys())
    store.update_plan(campaign_id, plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "errors": errors})


@router.post("/api/campaigns/{campaign_id}/revise")
def revise_plan(
    campaign_id: str,
    request: Request,
    revision_instruction: str = Form(...),
):
    doc = _require_campaign(campaign_id)
    if not _has_configured_anthropic_key():
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "errors": ["Anthropic API key is not configured; plan unchanged."]},
        )
    try:
        service = AnthropicCampaignService()
        plan = service.revise_campaign_plan(
            latest_plan=doc["current_plan"],
            revision_instruction=revision_instruction,
            validation_errors=doc.get("validation_errors") or [],
            template_prompt=_default_template_prompt(),
            examples=_default_template_examples(),
        )
    except JSONDecodeError as exc:
        _log.exception("Revise: Claude returned non-JSON response (campaign=%s, instruction=%r): %s",
                       campaign_id, revision_instruction, exc)
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "errors": ["Claude returned invalid JSON; plan unchanged."]},
        )
    except AnthropicError as exc:
        _log.exception("Revise: Anthropic API error (campaign=%s, instruction=%r): %s",
                       campaign_id, revision_instruction, exc)
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "errors": [f"Claude revision failed: {exc.__class__.__name__}; plan unchanged."]},
        )
    _log.info("Revise: success (campaign=%s, instruction=%r)", campaign_id, revision_instruction)
    errors = validate_campaign_plan(plan, _active_workspace_keys())
    if errors:
        _log.warning(
            "Revise: Claude returned invalid CampaignPlan; preserving current plan (campaign=%s, errors=%s)",
            campaign_id,
            errors,
        )
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "errors": ["Claude returned an invalid CampaignPlan; plan unchanged.", *errors]},
        )
    store.update_plan(campaign_id, plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "errors": errors})


@router.post("/api/campaigns/{campaign_id}/spintax")
def generate_spintax(campaign_id: str, request: Request):
    doc = _require_campaign(campaign_id)
    if not _has_configured_anthropic_key():
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "errors": ["Anthropic API key is not configured; cannot generate spintax."]},
        )
    from anthropic import Anthropic, AnthropicError as _AnthropicError

    try:
        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        new_plan, stats = apply_spintax_to_plan(doc["current_plan"], client)
    except _AnthropicError as exc:
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "errors": [f"Spintax generation failed: {exc.__class__.__name__}; previous plan preserved."]},
        )
    if stats["generated"] == 0:
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": True, "stats": stats, "note": "All bodies already had spintax."},
        )
    errors = validate_campaign_plan(new_plan, _active_workspace_keys())
    if errors:
        _log.warning(
            "Spintax: Claude returned invalid CampaignPlan; preserving current plan (campaign=%s, errors=%s)",
            campaign_id,
            errors,
        )
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "stats": stats, "errors": ["Spintax result was not Smartlead-safe; previous plan preserved.", *errors]},
        )
    store.update_plan(campaign_id, new_plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "stats": stats, "errors": errors})


@router.post("/api/campaigns/{campaign_id}/delay")
def update_sequence_delay(
    campaign_id: str,
    request: Request,
    step_number: int = Form(...),
    delay_days: int = Form(...),
):
    doc = _require_campaign(campaign_id)
    plan = doc.get("current_plan") or {}
    if not plan.get("sequence"):
        raise HTTPException(status_code=400, detail="No local campaign plan to edit.")
    if step_number <= 1:
        raise HTTPException(status_code=400, detail="Email 1 sends immediately and cannot be delayed.")
    if delay_days < 1 or delay_days > 30:
        raise HTTPException(status_code=400, detail="Delay must be between 1 and 30 days.")

    updated = False
    for step in plan.get("sequence", []):
        if int(step.get("step_number", 0)) == step_number:
            step["delay_days"] = delay_days
            updated = True
            break
    if not updated:
        raise HTTPException(status_code=404, detail=f"Email {step_number} was not found in this plan.")

    errors = validate_campaign_plan(plan, _active_workspace_keys())
    store.update_plan(campaign_id, plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "errors": errors})


@router.post("/api/campaigns/{campaign_id}/sequence-edit")
def edit_sequence_variant(
    campaign_id: str,
    request: Request,
    step_number: int = Form(...),
    variant_index: int = Form(...),
    body: str = Form(...),
    subject: str = Form(""),
):
    doc = _require_campaign(campaign_id)
    plan = doc.get("current_plan") or {}
    if not plan.get("sequence"):
        raise HTTPException(status_code=400, detail="No local campaign plan to edit.")

    step = next((s for s in plan["sequence"] if int(s.get("step_number", 0)) == step_number), None)
    if step is None:
        raise HTTPException(status_code=404, detail=f"Email {step_number} was not found in this plan.")
    variants = step.get("variants") or []
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(status_code=404, detail="Variant not found for this email.")

    variant = variants[variant_index]
    variant["body"] = body.replace("\r\n", "\n").strip("\n")
    # Only the first email carries a subject; follow-ups reply in-thread.
    if step_number == 1:
        variant["subject"] = subject.strip()

    errors = validate_campaign_plan(plan, _active_workspace_keys())
    store.update_plan(campaign_id, plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "errors": errors})


@router.post("/api/campaigns/{campaign_id}/variant-distribution")
def update_variant_distribution(
    campaign_id: str,
    request: Request,
    step_number: int = Form(...),
    percentages: list[int] = Form(...),
):
    doc = _require_campaign(campaign_id)
    plan = doc.get("current_plan") or {}
    if not plan.get("sequence"):
        raise HTTPException(status_code=400, detail="No local campaign plan to edit.")

    step = next((s for s in plan["sequence"] if int(s.get("step_number", 0)) == step_number), None)
    if step is None:
        raise HTTPException(status_code=404, detail=f"Email {step_number} was not found in this plan.")
    variants = step.get("variants") or []
    if len(percentages) != len(variants):
        raise HTTPException(status_code=400, detail="One percentage per variant is required.")
    if any(p < 0 for p in percentages) or sum(percentages) != 100:
        raise HTTPException(status_code=400, detail="Variant percentages must be non-negative and sum to 100.")

    for variant, percentage in zip(variants, percentages):
        variant["distribution_percentage"] = int(percentage)

    errors = validate_campaign_plan(plan, _active_workspace_keys())
    store.update_plan(campaign_id, plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "errors": errors})


# ----- Inbox selection ----- #


def _campaign_sheet_client(doc: dict, plan: dict) -> str | None:
    workspace_key = doc.get("smartlead_workspace") or plan.get("workspace_key")
    return get_sheet_client_for_workspace(workspace_key)


def _campaign_subclient_key(doc: dict, plan: dict) -> str | None:
    workspace_key = doc.get("smartlead_workspace") or plan.get("workspace_key")
    if workspace_key == "preciselead":
        campaign_name = doc.get("campaign_name") or plan.get("campaign_name") or ""
        inferred = infer_smartlead_client("preciselead", campaign_name)
        return inferred["key"] if inferred else "internal"
    return None


def _provider_mix_from_ratio(gmail_ratio: float) -> dict[str, float]:
    g = max(0.0, min(1.0, gmail_ratio))
    return {"gmail": round(g, 4), "outlook": round(1.0 - g, 4)}


@router.get("/api/campaigns/{campaign_id}/inboxes")
def recommend_campaign_inboxes(campaign_id: str, gmail_ratio: float | None = Query(None)) -> dict:
    doc = _require_campaign(campaign_id)
    plan = doc.get("current_plan") or {}
    client = _campaign_sheet_client(doc, plan)
    if not client:
        workspace_key = doc.get("smartlead_workspace") or plan.get("workspace_key")
        return {"ok": False, "error": f"No inbox-sheet client mapped for workspace '{workspace_key}'."}

    needed = int((plan.get("schedule") or {}).get("max_new_leads_per_day") or 100)
    try:
        rows = fetch_inbox_rows()
    except InboxSheetError as exc:
        return {"ok": False, "error": str(exc)}

    subclient_key = _campaign_subclient_key(doc, plan)
    # The operator can override the gmail/outlook split per request; otherwise use the plan's.
    if gmail_ratio is not None:
        provider_mix = _provider_mix_from_ratio(gmail_ratio)
    else:
        provider_mix = (plan.get("inbox_selection") or {}).get("provider_mix")
    result = select_inboxes(
        rows,
        client=client,
        needed_daily_volume=needed,
        subclient_key=subclient_key,
        provider_mix=provider_mix,
    )
    result["ok"] = True
    result["provider_mix"] = provider_mix or {"gmail": 0.7, "outlook": 0.3}
    result["last_sync"] = fetch_last_sync()
    result["selected_account_ids"] = (plan.get("inbox_selection") or {}).get("email_account_ids") or []
    return result


@router.post("/api/campaigns/{campaign_id}/inbox-selection")
def update_inbox_selection(
    campaign_id: str,
    request: Request,
    account_ids: list[int] = Form(default=[]),
    gmail_ratio: float | None = Form(None),
):
    doc = _require_campaign(campaign_id)
    plan = doc.get("current_plan") or {}
    if not plan.get("sequence"):
        raise HTTPException(status_code=400, detail="No local campaign plan to edit.")

    client = _campaign_sheet_client(doc, plan)
    # Cross-client guard: only attach inboxes that are eligible (FREE) for this client.
    if account_ids and client:
        try:
            rows = fetch_inbox_rows()
            subclient_key = _campaign_subclient_key(doc, plan)
            eligible_ids = {row["account_id"] for row in select_inboxes(rows, client=client, needed_daily_volume=1, subclient_key=subclient_key)["free_pool"]}
        except InboxSheetError:
            eligible_ids = None  # sheet unavailable: cannot validate, accept as submitted
        if eligible_ids is not None:
            rejected = [account_id for account_id in account_ids if account_id not in eligible_ids]
            if rejected:
                raise HTTPException(status_code=400, detail=f"Inboxes not eligible for this client: {rejected}")

    selection = plan.get("inbox_selection") or {}
    selection["mode"] = "manual_ids" if account_ids else "skip"
    selection["email_account_ids"] = account_ids
    if gmail_ratio is not None:
        selection["provider_mix"] = _provider_mix_from_ratio(gmail_ratio)
    plan["inbox_selection"] = selection

    errors = validate_campaign_plan(plan, _active_workspace_keys())
    store.update_plan(campaign_id, plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "selected": account_ids, "errors": errors})


# ----- Sync to Smartlead ----- #


@router.post("/api/campaigns/{campaign_id}/sync")
def sync_to_smartlead(
    campaign_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    doc = _require_campaign(campaign_id)
    plan = doc.get("current_plan") or {}
    if not plan.get("sequence"):
        raise HTTPException(status_code=400, detail="No local campaign plan to sync yet.")
    if doc.get("validation_errors"):
        raise HTTPException(status_code=400, detail="Plan has validation errors; revise before syncing.")
    if doc.get("status") == "syncing":
        return _redirect_to_detail(request, campaign_id, {"ok": True, "status": "syncing"})

    store.campaigns_collection().update_one(
        {"_id": store.to_object_id(campaign_id)},
        {"$set": {"status": "syncing", "last_sync_error": None, "updated_at": store.now_utc()}},
    )
    background_tasks.add_task(sync_campaign_now, campaign_id)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "status": "syncing"})


@router.post("/api/campaigns/{campaign_id}/smartlead/link")
def link_existing_smartlead_campaign(
    campaign_id: str,
    request: Request,
    smartlead_campaign_ref: str = Form(...),
):
    _require_campaign(campaign_id)
    smartlead_id = _extract_smartlead_campaign_id(smartlead_campaign_ref)
    if not smartlead_id:
        raise HTTPException(status_code=400, detail="Paste a valid Smartlead campaign URL or numeric ID")
    store.attach_smartlead(campaign_id, smartlead_id)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "smartlead_campaign_id": smartlead_id})


@router.post("/api/campaigns/{campaign_id}/twin")
def mark_twin(
    campaign_id: str,
    request: Request,
    is_twin: bool = Form(False),
    twin_smartlead_url: str = Form(""),
) -> dict:
    _require_campaign(campaign_id)
    url = twin_smartlead_url.strip() or None
    if url and _extract_smartlead_campaign_id(url) is None:
        raise HTTPException(status_code=400, detail="Paste a valid Smartlead campaign URL or numeric ID")
    store.set_twin(campaign_id, is_twin, url)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "is_twin": is_twin})


@router.post("/api/campaigns/{campaign_id}/linkedin-messages")
async def save_linkedin_messages(
    campaign_id: str, request: Request
) -> Response:
    doc = _require_campaign(campaign_id)
    form = await request.form()
    raw_messages = form.getlist("messages")
    plan = doc.get("current_plan") or {}
    bodies = [m.strip() for m in raw_messages if m and m.strip()][:3]
    email_steps = [s for s in (plan.get("sequence") or []) if s.get("channel") != "linkedin"]
    base = max([s.get("step_number", 0) for s in email_steps], default=0)
    linkedin_steps = [
        {
            "step_number": base + i + 1,
            "delay_days": 0,
            "channel": "linkedin",
            "variants": [{"variant_label": "A", "subject": "", "body": body}],
        }
        for i, body in enumerate(bodies)
    ]
    plan["sequence"] = email_steps + linkedin_steps
    errors = validate_campaign_plan(plan, _active_workspace_keys())
    store.update_plan(campaign_id, plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True})


@router.post("/api/campaigns/{campaign_id}/heyreach-create")
def heyreach_create_route(
    campaign_id: str, request: Request, background_tasks: BackgroundTasks
) -> Response:
    doc = _require_campaign(campaign_id)
    if not linkedin_messages(doc.get("current_plan") or {}):
        raise HTTPException(status_code=400, detail="No LinkedIn steps. Add LinkedIn messages first.")
    store.set_heyreach_creating(campaign_id, True)
    background_tasks.add_task(create_heyreach_campaign_now, campaign_id)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "queued": True})


@router.post("/api/campaigns/{campaign_id}/twin-fix")
def twin_fix(
    campaign_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    twin_smartlead_url: str = Form(""),
) -> dict:
    doc = _require_campaign(campaign_id)
    if not doc.get("is_twin"):
        raise HTTPException(status_code=400, detail="Not a twin campaign. Mark it as twin first.")
    url = twin_smartlead_url.strip() or None
    store.set_twin_fix_running(campaign_id, True)
    background_tasks.add_task(run_twin_fix_now, campaign_id, url)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "queued": True})


@router.post("/api/campaigns/{campaign_id}/local-delete")
def delete_local_campaign(campaign_id: str, request: Request):
    _require_campaign(campaign_id)
    deleted = store.delete_campaign(campaign_id)
    payload = {"ok": deleted, "mode": "local_delete", "campaign_id": campaign_id}
    return _redirect_after_delete(request, payload)


# ----- Smartlead inspect/analytics/lifecycle ----- #


@router.get("/api/campaigns/{campaign_id}/smartlead")
async def smartlead_snapshot(
    campaign_id: str,
    request: Request,
    smartlead_campaign_ref: str | None = Query(None),
):
    doc = _require_campaign(campaign_id)
    smartlead_id = _target_smartlead_id(doc, smartlead_campaign_ref)
    smartlead = _smartlead_for_doc(doc)
    payload = await _smartlead_snapshot_payload(smartlead, smartlead_id)
    if _wants_html(request):
        return templates.TemplateResponse(
            request,
            "smartlead_report.html",
            {
                "campaign": _detail_payload(doc),
                "title": "Inspect Smartlead Campaign",
                "payload": payload,
                "sections": _report_sections(payload, ["campaign", "sequences"]),
            },
        )
    return payload


@router.get("/api/campaigns/{campaign_id}/analytics")
async def smartlead_analytics(
    campaign_id: str,
    request: Request,
    smartlead_campaign_ref: str | None = Query(None),
    start_date: str | None = None,
    end_date: str | None = None,
):
    doc = _require_campaign(campaign_id)
    smartlead_id = _target_smartlead_id(doc, smartlead_campaign_ref)
    smartlead = _smartlead_for_doc(doc)
    end_value = end_date or date.today().isoformat()
    start_value = start_date or (date.today() - timedelta(days=30)).isoformat()
    payload = await _smartlead_analytics_payload(smartlead, smartlead_id, start_value, end_value)
    if _wants_html(request):
        return templates.TemplateResponse(
            request,
            "smartlead_report.html",
            {
                "campaign": _detail_payload(doc),
                "title": "Smartlead Analytics",
                "payload": payload,
                "sections": _report_sections(
                    payload,
                    ["top_level", "sequence_statistics", "lead_statistics", "performance"],
                ),
            },
        )
    return payload


@router.post("/api/campaigns/{campaign_id}/smartlead/archive")
async def archive_smartlead_campaign(campaign_id: str, request: Request):
    doc = _require_campaign(campaign_id)
    smartlead_id = _require_smartlead_id(doc)
    try:
        response = await _smartlead_for_doc(doc).archive_campaign(smartlead_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            store.delete_campaign(campaign_id)
            return _redirect_after_delete(
                request,
                {
                    "ok": True,
                    "mode": "archive",
                    "smartlead_campaign_id": smartlead_id,
                    "note": "Smartlead campaign was already missing; local record removed.",
                },
            )
        store.mark_sync_failed(campaign_id, _smartlead_http_error("Archive campaign", exc))
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "mode": "archive", "smartlead_campaign_id": smartlead_id, "error": _smartlead_http_error("Archive campaign", exc)},
        )
    store.campaigns_collection().update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": "archived", "updated_at": store.now_utc()}},
    )
    return _redirect_to_detail(
        request,
        campaign_id,
        {"ok": True, "mode": "archive", "smartlead_campaign_id": smartlead_id, "response": response},
    )


@router.post("/api/campaigns/{campaign_id}/smartlead/delete")
async def delete_smartlead_campaign(campaign_id: str, request: Request):
    doc = _require_campaign(campaign_id)
    smartlead_id = _require_smartlead_id(doc)
    try:
        response = await _smartlead_for_doc(doc).delete_campaign(smartlead_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            store.delete_campaign(campaign_id)
            return _redirect_after_delete(
                request,
                {
                    "ok": True,
                    "mode": "delete",
                    "smartlead_campaign_id": smartlead_id,
                    "note": "Smartlead campaign was already missing; local record removed.",
                },
            )
        store.mark_sync_failed(campaign_id, _smartlead_http_error("Delete campaign", exc))
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "mode": "delete", "smartlead_campaign_id": smartlead_id, "error": _smartlead_http_error("Delete campaign", exc)},
        )
    store.delete_campaign(campaign_id)
    return _redirect_after_delete(
        request,
        {"ok": True, "mode": "delete", "smartlead_campaign_id": smartlead_id, "response": response},
    )


# ----- Helpers ----- #


def _require_campaign(campaign_id: str) -> dict:
    doc = store.get_campaign(campaign_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return doc


def _require_smartlead_id(doc: dict) -> int:
    smartlead_id = doc.get("smartlead_campaign_id")
    if not smartlead_id:
        raise HTTPException(status_code=400, detail="No Smartlead campaign linked to this request yet")
    return smartlead_id


def _target_smartlead_id(doc: dict, ref: str | None) -> int:
    if ref and ref.strip():
        parsed = _extract_smartlead_campaign_id(ref)
        if not parsed:
            raise HTTPException(status_code=400, detail="Paste a valid Smartlead campaign URL or numeric ID")
        return parsed
    return _require_smartlead_id(doc)


def _extract_smartlead_campaign_id(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    patterns = (
        r"(?:email-campaigns-v2|email-campaigns|email-campaign|campaigns?)/(\d+)",
        r"(?:campaign_id|smartlead_campaign_id)=(\d+)",
        r"\b(\d+)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            campaign_id = int(match.group(1))
        except ValueError:
            continue
        if campaign_id > 0:
            return campaign_id
    return None


def _parse_optional_smartlead_campaign_ref(value: str) -> int | None:
    if not value or not value.strip():
        return None
    smartlead_id = _extract_smartlead_campaign_id(value)
    if not smartlead_id:
        raise HTTPException(status_code=400, detail="Paste a valid Smartlead campaign URL or numeric ID")
    return smartlead_id


async def _try_import_existing_smartlead_plan(workspace: dict, smartlead_campaign_id: int, max_new_leads_per_day: int) -> dict | None:
    api_key = workspace.get("api_key")
    if not api_key:
        return None
    smartlead = SmartleadService(api_key)
    try:
        campaign = await smartlead.get_campaign(smartlead_campaign_id)
        sequences_response = await smartlead.get_sequences(smartlead_campaign_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=_smartlead_http_error("Import existing Smartlead campaign", exc)) from exc
    sequences = sequences_response.get("data", sequences_response) if isinstance(sequences_response, dict) else sequences_response
    if not isinstance(sequences, list):
        return None
    plan = build_campaign_plan_from_smartlead(
        workspace_key=workspace["key"],
        campaign=campaign,
        sequences=sequences,
        max_new_leads_per_day=max_new_leads_per_day,
    )
    return plan if plan.get("sequence") else None


def _resolve_campaign_name(
    campaign_name: str,
    smartlead_campaign_id: int | None,
    selected_sequence_name: str,
    parsed_messaging: dict,
    imported_campaign_name: str | None = None,
) -> str:
    explicit_name = (campaign_name or "").strip()
    if explicit_name:
        return explicit_name
    if imported_campaign_name:
        return imported_campaign_name
    if smartlead_campaign_id:
        return f"Smartlead Campaign {smartlead_campaign_id}"
    sequence_name = (selected_sequence_name or "").strip() or (parsed_messaging.get("selected_campaign") or "").strip()
    if sequence_name:
        return sequence_name
    return "Untitled Campaign"


def _smartlead_for_doc(doc: dict) -> SmartleadService:
    workspace = get_workspace_config(doc["smartlead_workspace"])
    if not workspace or not workspace.get("api_key"):
        raise HTTPException(
            status_code=500,
            detail=f"Smartlead API key not configured for workspace '{doc['smartlead_workspace']}'",
        )
    return SmartleadService(workspace["api_key"])


def _active_workspace_keys() -> set[str]:
    return {w["key"] for w in SMARTLEAD_WORKSPACES}


def _has_configured_anthropic_key() -> bool:
    key = settings.ANTHROPIC_API_KEY
    return bool(key) and key != "replace_me"


def _default_template_prompt() -> str:
    return (
        "You are a precise editor that revises a CampaignPlan JSON. "
        "Apply only the requested change. Preserve schema, merge tags, sequence structure, and all other fields."
    )


def _default_template_examples() -> str:
    return ""


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _redirect_to_detail(request: Request, campaign_id: str, payload: dict):
    if _wants_html(request):
        return RedirectResponse(f"/campaigns/{campaign_id}", status_code=303)
    return payload


def _redirect_after_delete(request: Request, payload: dict):
    if _wants_html(request):
        return RedirectResponse("/app", status_code=303)
    return payload


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


def _dashboard_row(doc: dict) -> dict:
    workspace = get_workspace_config(doc.get("smartlead_workspace", "")) or {"name": doc.get("smartlead_workspace", "?")}
    smartlead_id = doc.get("smartlead_campaign_id")
    smartlead_state = "Not linked"
    if smartlead_id:
        smartlead_state = "Synced" if doc.get("status") == "synced" else "Linked"
    return {
        "id": str(doc["_id"]),
        "name": doc.get("campaign_name", "Untitled campaign"),
        "status": doc.get("status", "drafting"),
        "status_label": _dashboard_status_label(doc.get("status", "drafting")),
        "smartlead_id": smartlead_id,
        "smartlead_state": smartlead_state,
        "workspace": workspace["name"],
        "smartlead_client": _smartlead_client_payload(doc, workspace)["label"],
        "updated_at": store.to_display_tz(doc.get("updated_at")),
        "last_sync_error": doc.get("last_sync_error"),
    }


def _detail_payload(doc: dict) -> dict:
    workspace = get_workspace_config(doc.get("smartlead_workspace", "")) or {"name": doc.get("smartlead_workspace", "?")}
    plan = doc.get("current_plan") or {}
    raw_input = doc.get("raw_input") or {}
    spintax_status = None
    has_local_plan = bool(plan.get("sequence"))
    if has_local_plan:
        need, total = count_bodies_needing_spintax(plan)
        spintax_status = {"need": need, "total": total, "all_have_spintax": total > 0 and need == 0}
    return {
        "id": str(doc["_id"]),
        "campaign_name": doc.get("campaign_name", "Untitled campaign"),
        "status": doc.get("status", "drafting"),
        "status_label": _status_label(doc.get("status", "drafting")),
        "workspace_key": doc.get("smartlead_workspace"),
        "workspace_name": workspace["name"],
        "smartlead_client": _smartlead_client_payload(doc, workspace),
        "raw_input": raw_input,
        "plan": plan,
        "has_local_plan": has_local_plan,
        "validation_errors": doc.get("validation_errors") or [],
        "smartlead_campaign_id": doc.get("smartlead_campaign_id"),
        "is_twin": doc.get("is_twin", False),
        "twin_smartlead_url": doc.get("twin_smartlead_url"),
        "twin_last_fix": doc.get("twin_last_fix"),
        "twin_fix_running": doc.get("twin_fix_running", False),
        "heyreach_campaign_id": doc.get("heyreach_campaign_id"),
        "heyreach_campaign_url": doc.get("heyreach_campaign_url"),
        "heyreach_status": doc.get("heyreach_status"),
        "heyreach_creating": doc.get("heyreach_creating", False),
        "heyreach_last_error": doc.get("heyreach_last_error"),
        "linkedin_messages": linkedin_messages(doc.get("current_plan") or {}),
        "last_sync_error": doc.get("last_sync_error"),
        "spintax_status": spintax_status,
        "synced_at": store.to_display_tz(doc.get("synced_at")),
        "updated_at": store.to_display_tz(doc.get("updated_at")),
    }


def _status_label(status: str) -> str:
    return {
        "drafting": "Drafting",
        "ready": "Ready to sync",
        "syncing": "Syncing",
        "synced": "Smartlead campaign created",
        "linked": "Linked to Smartlead",
        "failed": "Failed",
        "archived": "Archived",
    }.get(status, status.replace("_", " ").title())


def _dashboard_status_label(status: str) -> str:
    return {
        "drafting": "Draft",
        "ready": "Ready",
        "syncing": "Syncing",
        "synced": "Synced",
        "linked": "Linked",
        "failed": "Failed",
        "archived": "Archived",
    }.get(status, status.replace("_", " ").title())


def _smartlead_client_payload(doc: dict, workspace: dict) -> dict:
    client_id = doc.get("smartlead_client_id")
    client_name = doc.get("smartlead_client_name")
    if client_id:
        label = f"{client_name or 'Client'} (ID {client_id})"
        return {
            "id": client_id,
            "name": client_name or "Client",
            "match": doc.get("smartlead_client_match"),
            "label": label,
        }
    self_client_name = workspace.get("self_client_name") or workspace.get("name", "Workspace")
    return {
        "id": None,
        "name": self_client_name,
        "match": None,
        "label": self_client_name,
    }


# ----- Smartlead snapshot/analytics payloads ----- #


async def _smartlead_snapshot_payload(smartlead: SmartleadService, smartlead_campaign_id: int) -> dict:
    sections = {
        "campaign": await _safe_smartlead_call("Campaign", lambda: smartlead.get_campaign(smartlead_campaign_id)),
        "sequences": await _safe_smartlead_call("Sequences", lambda: smartlead.get_sequences(smartlead_campaign_id)),
    }
    return _smartlead_payload(smartlead, smartlead_campaign_id, sections)


async def _smartlead_analytics_payload(
    smartlead: SmartleadService,
    smartlead_campaign_id: int,
    start_date: str,
    end_date: str,
) -> dict:
    sections = {
        "top_level": await _safe_smartlead_call(
            "Top-level analytics",
            lambda: smartlead.get_campaign_analytics(smartlead_campaign_id),
        ),
        "sequence_statistics": await _safe_smartlead_call(
            "Sequence statistics",
            lambda: smartlead.get_campaign_statistics(smartlead_campaign_id),
        ),
        "lead_statistics": await _safe_smartlead_call(
            "Lead statistics",
            lambda: smartlead.get_campaign_lead_statistics(smartlead_campaign_id),
        ),
        "performance": await _safe_smartlead_call(
            "Performance",
            lambda: smartlead.get_campaign_performance(start_date, end_date, campaign_ids=[smartlead_campaign_id]),
        ),
    }
    payload = _smartlead_payload(smartlead, smartlead_campaign_id, sections)
    payload["date_range"] = {"start_date": start_date, "end_date": end_date}
    return payload


async def _safe_smartlead_call(label: str, call: Callable[[], Awaitable[dict]]) -> dict:
    try:
        return {"ok": True, "data": await call()}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": _smartlead_http_error(label, exc)}
    except httpx.RequestError as exc:
        return {"ok": False, "error": f"{label} request failed: {exc.__class__.__name__}: {exc}"}
    except ValueError as exc:
        return {"ok": False, "error": f"{label} returned an invalid JSON response: {exc}"}


def _smartlead_http_error(label: str, exc: httpx.HTTPStatusError) -> str:
    body = (exc.response.text or "").strip()
    if len(body) > 500:
        body = f"{body[:500]}..."
    suffix = f" - {body}" if body else ""
    return f"{label} failed with HTTP {exc.response.status_code}{suffix}"


def _smartlead_payload(smartlead: SmartleadService, smartlead_campaign_id: int, sections: dict) -> dict:
    errors = [
        {"section": section, "error": result["error"]}
        for section, result in sections.items()
        if not result.get("ok")
    ]
    return {
        "ok": not errors,
        "smartlead_campaign_id": smartlead_campaign_id,
        "smartlead_url": smartlead.campaign_url(smartlead_campaign_id),
        "errors": errors,
        **sections,
    }


def _report_sections(payload: dict, section_names: list[str]) -> list[dict]:
    return [
        {
            "key": section_name,
            "label": section_name.replace("_", " ").title(),
            **payload.get(section_name, {"ok": False, "error": "No response captured"}),
        }
        for section_name in section_names
    ]
