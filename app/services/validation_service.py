import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from app.config import settings
from app.schemas.campaign_plan import CampaignPlan

MALFORMED_MERGE_RE = re.compile(r"(?<!\{)\{[A-Za-z_][A-Za-z0-9_]*\}(?!\})")
ALLOWED_MERGE_TAGS = {"{{first_name}}", "{{company_name}}", "{{company}}", "%signature%"}


def validate_campaign_plan(plan_data: dict, active_workspace_keys: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    try:
        plan = CampaignPlan.model_validate(plan_data)
    except ValidationError as exc:
        return _format_schema_errors(exc)
    except Exception as exc:
        return [f"campaign plan validation failed: {exc}"]

    if active_workspace_keys is not None and plan.workspace_key not in active_workspace_keys:
        errors.append("workspace_key does not match an active Smartlead workspace")

    try:
        ZoneInfo(plan.schedule.timezone)
    except ZoneInfoNotFoundError:
        errors.append("schedule timezone is not valid")

    if plan.schedule.max_new_leads_per_day < 1 or plan.schedule.max_new_leads_per_day > 500:
        errors.append("max_new_leads_per_day must be between 1 and 500")

    if not plan.settings.stop_on_reply:
        errors.append("stop_on_reply must be true for V1")

    if plan.settings.track_opens or plan.settings.track_clicks:
        errors.append("tracking must remain disabled for V1")

    first_step = min(plan.sequence, key=lambda step: step.step_number)
    if first_step.step_number != 1:
        errors.append("sequence must include Step 1")
    for variant in first_step.variants:
        if not variant.subject.strip():
            errors.append("Step 1 variants must include a non-empty subject")
            break

    for step in plan.sequence:
        if len(step.variants) > 20:
            errors.append(f"Step {step.step_number} has too many variants")
        if step.step_number != first_step.step_number:
            for variant in step.variants:
                if variant.subject:
                    errors.append(f"Step {step.step_number} follow-up subjects must be empty")
                    break
        for variant in step.variants:
            errors.extend(_validate_body_copy(variant.body))

    return errors


def _format_schema_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for detail in exc.errors():
        loc = ".".join(str(part) for part in detail.get("loc", ())) or "plan"
        message = detail.get("msg", "Invalid value")
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        errors.append(f"{loc}: {message}")
    return errors or ["campaign plan schema validation failed"]


def _validate_body_copy(body: str) -> list[str]:
    errors: list[str] = []
    if MALFORMED_MERGE_RE.search(body):
        errors.append("body contains malformed merge braces")
    if body.count("{") != body.count("}"):
        errors.append("body contains unbalanced braces")
    if _has_merge_tag_inside_spintax(body):
        errors.append(
            "body contains a merge tag inside a spintax block; move the merge tag outside the {option|option} block before syncing to Smartlead"
        )
    for phrase in settings.BLOCKED_PHRASES:
        if _contains_blocked_phrase(body, phrase):
            errors.append(f"body contains blocked phrase: {phrase}")
    return errors


def _has_merge_tag_inside_spintax(body: str) -> bool:
    """Smartlead's parser rejects blocks like `{a|b for {{company_name}}}`.

    Double-brace merge tags are valid on their own, and simple single-brace
    spintax is valid on its own. The invalid shape is a merge tag nested inside
    one single-brace spintax block.
    """
    text = body or ""
    index = 0
    while index < len(text):
        if text[index] != "{" or (index + 1 < len(text) and text[index + 1] == "{"):
            index += 1
            continue

        has_pipe = False
        has_merge_tag = False
        cursor = index + 1
        while cursor < len(text):
            if text.startswith("{{", cursor):
                end = text.find("}}", cursor + 2)
                if end == -1:
                    break
                has_merge_tag = True
                cursor = end + 2
                continue
            if text[cursor] == "|":
                has_pipe = True
            if text[cursor] == "}" and not (cursor + 1 < len(text) and text[cursor + 1] == "}"):
                if has_pipe and has_merge_tag:
                    return True
                index = cursor
                break
            cursor += 1
        index += 1
    return False


def _contains_blocked_phrase(body: str, phrase: str) -> bool:
    escaped_words = [re.escape(part) for part in phrase.strip().split()]
    if not escaped_words:
        return False
    pattern = r"(?<![\w-])" + r"\s+".join(escaped_words) + r"(?![\w-])"
    return re.search(pattern, body, flags=re.IGNORECASE) is not None
