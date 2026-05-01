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
import re

from anthropic import AnthropicError
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile
import httpx
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import (
    SMARTLEAD_WORKSPACES,
    get_workspace_config,
    settings,
)
from app.services.anthropic_service import AnthropicCampaignService
from app.services.local_plan_service import build_campaign_plan_from_input
from app.services.parser_service import parse_messaging_file
from app.services.smartlead_service import SmartleadService
from app.services.spintax_service import apply_spintax_to_plan, count_bodies_needing_spintax
from app.services.validation_service import validate_campaign_plan
from app import store
from app.workers.sync_campaign import sync_campaign_now

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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


# ----- Mutations: create, revise, spintax ----- #


@router.post("/api/campaigns/new")
async def create_campaign(
    workspace_key: str = Form(...),
    campaign_name: str = Form(...),
    max_new_leads_per_day: int = Form(100),
    messaging_text: str = Form(""),
    selected_sequence_name: str = Form(""),
    messaging_file: UploadFile | None = File(None),
) -> RedirectResponse:
    if not get_workspace_config(workspace_key):
        raise HTTPException(status_code=400, detail=f"Unknown workspace: {workspace_key}")

    uploaded_text = await _read_text_upload(messaging_file)
    final_text = uploaded_text or messaging_text
    parsed_messaging = parse_messaging_file(final_text, selected_sequence_name)

    raw_input = {
        "workspace_key": workspace_key,
        "campaign_name": campaign_name,
        "max_new_leads_per_day": max_new_leads_per_day,
        "messaging_filename": messaging_file.filename if uploaded_text and messaging_file else None,
        "selected_sequence_name": selected_sequence_name.strip() or parsed_messaging.get("selected_campaign"),
        "messaging_text": final_text,
        "parsed_messaging": parsed_messaging,
    }
    plan = build_campaign_plan_from_input(
        raw_input,
        note="Plan generated deterministically from parsed messaging.",
    )
    errors = validate_campaign_plan(plan, _active_workspace_keys())
    doc = store.insert_campaign(
        workspace_key=workspace_key,
        campaign_name=campaign_name,
        raw_input=raw_input,
        plan=plan,
        validation_errors=errors,
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
    except JSONDecodeError:
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "errors": ["Claude returned invalid JSON; plan unchanged."]},
        )
    except AnthropicError as exc:
        return _redirect_to_detail(
            request,
            campaign_id,
            {"ok": False, "errors": [f"Claude revision failed: {exc.__class__.__name__}; plan unchanged."]},
        )
    errors = validate_campaign_plan(plan, _active_workspace_keys())
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
    store.update_plan(campaign_id, new_plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "stats": stats, "errors": errors})


# ----- Sync to Smartlead ----- #


@router.post("/api/campaigns/{campaign_id}/sync")
def sync_to_smartlead(
    campaign_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    doc = _require_campaign(campaign_id)
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
    response = await _smartlead_for_doc(doc).archive_campaign(smartlead_id)
    return _redirect_to_detail(
        request,
        campaign_id,
        {"ok": True, "mode": "archive", "smartlead_campaign_id": smartlead_id, "response": response},
    )


@router.post("/api/campaigns/{campaign_id}/smartlead/delete")
async def delete_smartlead_campaign(campaign_id: str, request: Request):
    doc = _require_campaign(campaign_id)
    smartlead_id = _require_smartlead_id(doc)
    response = await _smartlead_for_doc(doc).delete_campaign(smartlead_id)
    return _redirect_to_detail(
        request,
        campaign_id,
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
        r"(?:email-campaign|campaign)/(\d+)",
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
    return {
        "id": str(doc["_id"]),
        "name": doc.get("campaign_name", "Untitled campaign"),
        "status": doc.get("status", "drafting"),
        "status_label": _status_label(doc.get("status", "drafting")),
        "smartlead_id": doc.get("smartlead_campaign_id"),
        "smartlead_state": "Synced to Smartlead" if doc.get("smartlead_campaign_id") else "Not synced",
        "workspace": workspace["name"],
        "updated_at": doc.get("updated_at"),
        "last_sync_error": doc.get("last_sync_error"),
    }


def _detail_payload(doc: dict) -> dict:
    workspace = get_workspace_config(doc.get("smartlead_workspace", "")) or {"name": doc.get("smartlead_workspace", "?")}
    plan = doc.get("current_plan") or {}
    raw_input = doc.get("raw_input") or {}
    spintax_status = None
    if plan:
        need, total = count_bodies_needing_spintax(plan)
        spintax_status = {"need": need, "total": total, "all_have_spintax": total > 0 and need == 0}
    return {
        "id": str(doc["_id"]),
        "campaign_name": doc.get("campaign_name", "Untitled campaign"),
        "status": doc.get("status", "drafting"),
        "status_label": _status_label(doc.get("status", "drafting")),
        "workspace_key": doc.get("smartlead_workspace"),
        "workspace_name": workspace["name"],
        "raw_input": raw_input,
        "plan": plan,
        "validation_errors": doc.get("validation_errors") or [],
        "smartlead_campaign_id": doc.get("smartlead_campaign_id"),
        "last_sync_error": doc.get("last_sync_error"),
        "spintax_status": spintax_status,
        "synced_at": doc.get("synced_at"),
        "updated_at": doc.get("updated_at"),
    }


def _status_label(status: str) -> str:
    return {
        "drafting": "Drafting",
        "ready": "Ready to sync",
        "syncing": "Syncing",
        "synced": "Smartlead campaign created",
        "failed": "Failed",
        "archived": "Archived",
    }.get(status, status.replace("_", " ").title())


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
