"""Sync a campaign doc to Smartlead.

Runs as a FastAPI BackgroundTask after `POST /api/campaigns/{id}/sync`.

Steps:
  1. Load the campaign doc from Mongo.
  2. Re-validate the plan (defensive; route already checks).
  3. Resolve the workspace API key and infer any Smartlead agency client_id.
  4. If no smartlead_campaign_id: create the Smartlead campaign.
     Else: update the existing one in place.
  5. Apply settings, schedule, sequences. Optionally attach email accounts.
  6. Update the Mongo doc with the resulting smartlead_campaign_id and status.

On failure, mark the doc `failed` with the error text. The route returns to the
operator immediately, so this runs after the response is sent.
"""

import asyncio
import json
import re

import httpx

from app.config import get_workspace_config, infer_smartlead_client
from app.services.sequence_builder import (
    build_smartlead_sequences,
    format_email_body_for_smartlead,
    format_subject_for_smartlead,
    smartlead_html_to_text,
)
from app.services.twain_service import audit_twain_field
from app.services.smartlead_service import SmartleadService
from app.services.validation_service import validate_campaign_plan
from app import store


def sync_campaign_now(campaign_id: str) -> None:
    """Synchronous entrypoint used by FastAPI BackgroundTasks."""
    asyncio.run(_sync_campaign_async(campaign_id))


async def _sync_campaign_async(campaign_id: str) -> None:
    doc = store.get_campaign(campaign_id)
    if not doc:
        return

    try:
        plan = doc.get("current_plan") or {}
        workspace_keys = _active_workspace_keys()
        errors = validate_campaign_plan(plan, workspace_keys)
        if errors:
            store.mark_sync_failed(campaign_id, "Validation failed: " + "; ".join(errors))
            return

        workspace = get_workspace_config(doc["smartlead_workspace"])
        if not workspace or not workspace.get("api_key"):
            store.mark_sync_failed(
                campaign_id,
                f"Smartlead API key not configured for workspace '{doc['smartlead_workspace']}'",
            )
            return

        smartlead = SmartleadService(workspace["api_key"])
        existing_smartlead_id = doc.get("smartlead_campaign_id")

        if existing_smartlead_id:
            smartlead_id = existing_smartlead_id
        else:
            smartlead_client = _resolve_smartlead_client(doc, workspace)
            client_id = smartlead_client["client_id"] if smartlead_client else None
            if smartlead_client and not doc.get("smartlead_client_id"):
                store.campaigns_collection().update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "smartlead_client_id": smartlead_client["client_id"],
                            "smartlead_client_name": smartlead_client["name"],
                            "smartlead_client_match": smartlead_client["matched_alias"],
                            "updated_at": store.now_utc(),
                        }
                    },
                )
            create_response = await smartlead.create_campaign(doc["campaign_name"], client_id)
            smartlead_id = _extract_campaign_id(create_response)
            store.campaigns_collection().update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "smartlead_campaign_id": smartlead_id,
                        "status": "syncing",
                        "last_sync_error": None,
                        "updated_at": store.now_utc(),
                    }
                },
            )

        ooo_delay_days = int(plan.get("settings", {}).get("ooo_restart_delay_days", 10))
        await smartlead.apply_v1_settings(smartlead_id, ooo_delay_days)
        await smartlead.update_schedule(smartlead_id, plan["schedule"])

        sequences = build_smartlead_sequences(plan["sequence"])
        await smartlead.update_sequences(smartlead_id, sequences)
        await _verify_smartlead_sequence_sync(smartlead, smartlead_id, plan["sequence"])

        if doc.get("is_twin"):
            verify_response = await smartlead.get_sequences(smartlead_id)
            _assert_twin_join_intact(_smartlead_sequence_list(verify_response))

        email_account_ids = plan.get("inbox_selection", {}).get("email_account_ids") or []
        if email_account_ids:
            await smartlead.attach_email_accounts(smartlead_id, email_account_ids)

        store.attach_smartlead(campaign_id, smartlead_id)
    except Exception as exc:
        store.mark_sync_failed(campaign_id, _error_text(exc))


def _active_workspace_keys() -> set[str]:
    from app.config import SMARTLEAD_WORKSPACES

    return {w["key"] for w in SMARTLEAD_WORKSPACES}


def _assert_twin_join_intact(smartlead_sequences: list[dict]) -> None:
    """Hard-fail if a twin Step 1 body has reverted to a lone <br> join.

    Soft logging is insufficient — this join has silently reverted before and
    was only caught by a screenshot.
    """
    for step in smartlead_sequences:
        if int(step.get("seq_number") or step.get("step_number") or 0) != 1:
            continue
        variants = step.get("seq_variants") or step.get("sequence_variants") or [step]
        for variant in variants:
            body = variant.get("email_body") or step.get("email_body") or ""
            if "lone_br" in audit_twain_field(body):
                raise RuntimeError("Twin Step 1 join reverted to a lone <br> after sync (expected <br><br>)")


async def _verify_smartlead_sequence_sync(
    smartlead: SmartleadService,
    smartlead_id: int,
    plan_sequence: list[dict],
) -> None:
    last_error: RuntimeError | None = None
    for attempt in range(3):
        response = await smartlead.get_sequences(smartlead_id)
        smartlead_sequences = _smartlead_sequence_list(response)
        errors = _sequence_sync_mismatches(plan_sequence, smartlead_sequences)
        if not errors:
            return
        last_error = RuntimeError("Smartlead sequence verification failed: " + "; ".join(errors[:5]))
        if attempt < 2:
            await asyncio.sleep(1)
    if last_error:
        raise last_error


def _smartlead_sequence_list(response: object) -> list[dict]:
    if isinstance(response, dict):
        data = response.get("data", response)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("sequences"), list):
            return data["sequences"]
        if isinstance(response.get("sequences"), list):
            return response["sequences"]
    if isinstance(response, list):
        return response
    return []


def _sequence_sync_mismatches(plan_sequence: list[dict], smartlead_sequences: list[dict]) -> list[str]:
    errors: list[str] = []
    actual_by_step = {
        int(step.get("seq_number") or step.get("step_number") or 0): step
        for step in smartlead_sequences
        if step.get("seq_number") or step.get("step_number")
    }
    for expected_step in plan_sequence:
        step_number = int(expected_step.get("step_number") or 0)
        actual_step = actual_by_step.get(step_number)
        if not actual_step:
            errors.append(f"Email {step_number} missing in Smartlead")
            continue
        errors.extend(_variant_sync_mismatches(step_number, expected_step.get("variants") or [], actual_step))
    return errors


def _variant_sync_mismatches(step_number: int, expected_variants: list[dict], actual_step: dict) -> list[str]:
    errors: list[str] = []
    actual_variants = actual_step.get("sequence_variants") or actual_step.get("seq_variants") or []
    if not actual_variants and (actual_step.get("email_body") or actual_step.get("subject")):
        actual_variants = [actual_step]
    actual_by_label = {
        str(variant.get("variant_label") or _label_for_index(idx)): variant
        for idx, variant in enumerate(actual_variants)
        if not variant.get("is_deleted")
    }
    for idx, expected in enumerate(expected_variants):
        label = str(expected.get("variant_label") or _label_for_index(idx))
        actual = actual_by_label.get(label)
        if not actual:
            errors.append(f"Email {step_number} variant {label} missing in Smartlead")
            continue
        expected_subject = format_subject_for_smartlead(str(expected.get("subject") or ""))
        actual_subject = _normalize_compare_text(str(actual.get("subject") or actual_step.get("subject") or ""))
        if expected_subject != actual_subject:
            errors.append(f"Email {step_number} variant {label} subject changed")
        expected_body = _html_to_compare_text(format_email_body_for_smartlead(str(expected.get("body") or "")))
        actual_body = _html_to_compare_text(str(actual.get("email_body") or actual_step.get("email_body") or ""))
        if expected_body != actual_body:
            errors.append(f"Email {step_number} variant {label} body changed or lost spacing")
    return errors


def _html_to_compare_text(value: str) -> str:
    return smartlead_html_to_text(value)


def _normalize_compare_text(value: str) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _label_for_index(index: int) -> str:
    return chr(ord("A") + index) if index < 26 else f"V{index + 1}"


def _resolve_client_id(doc: dict, workspace: dict) -> int | None:
    smartlead_client = _resolve_smartlead_client(doc, workspace)
    return smartlead_client["client_id"] if smartlead_client else None


def _resolve_smartlead_client(doc: dict, workspace: dict) -> dict | None:
    """Return the stored or inferred Smartlead agency client for campaign create.

    None is intentional: campaigns with no matching client alias are created
    without client_id and remain under the PreciseLeads/master workspace.
    """
    client_id = doc.get("smartlead_client_id")
    if client_id:
        parsed_client_id = _parse_client_id(client_id, "stored Smartlead client")
        return {
            "client_id": parsed_client_id,
            "name": doc.get("smartlead_client_name") or f"Client {parsed_client_id}",
            "matched_alias": doc.get("smartlead_client_match"),
        }

    return infer_smartlead_client(workspace["key"], doc.get("campaign_name", ""))


def _parse_client_id(client_id: object, label: str) -> int:
    try:
        parsed_client_id = int(client_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} returned invalid id: {client_id}") from exc
    if parsed_client_id <= 0:
        raise RuntimeError(f"{label} returned invalid id: {client_id}")
    return parsed_client_id


def _extract_campaign_id(response: dict) -> int:
    """Pull the integer campaign id out of Smartlead's create response."""
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
            f"{_redact_api_key(str(response.request.url))}; body={_truncate(response.text)}"
        )
    return f"{exc.__class__.__name__}: {exc}"


def _response_snippet(response: dict) -> str:
    return _truncate(json.dumps(response, default=str, sort_keys=True))


def _truncate(value: str, limit: int = 1000) -> str:
    return value if len(value) <= limit else value[:limit] + "...[truncated]"


def _redact_api_key(value: str) -> str:
    return re.sub(r"([?&]api_key=)[^&]+", r"\1[redacted]", value)
