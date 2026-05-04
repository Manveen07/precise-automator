import json
import logging
import os
from datetime import datetime

from anthropic import Anthropic

from app.config import settings

_log = logging.getLogger("app.anthropic")


def _strip_markdown_fence(text: str) -> str:
    """Strip ```json / ``` fences Claude often wraps JSON responses in.

    Returns the inner content if the text is wrapped in a single fenced
    code block, otherwise returns the original text unchanged.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the opening fence line (```json, ```JSON, ``` etc.).
    first_newline = stripped.find("\n")
    if first_newline == -1:
        return stripped
    body = stripped[first_newline + 1 :]
    # Drop the closing fence (last ```).
    closing = body.rfind("```")
    if closing != -1:
        body = body[:closing]
    return body.strip()


def _debug_dump(label: str, payload: dict | str) -> None:
    """Drop a one-shot diagnostic file so we can inspect what Claude returned."""
    if os.environ.get("APP_DEBUG_ANTHROPIC") != "1":
        return
    try:
        os.makedirs("debug_anthropic", exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        path = f"debug_anthropic/{ts}_{label}.txt"
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(payload, str):
                fh.write(payload)
            else:
                fh.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        _log.warning("Anthropic debug dump: %s", path)
    except Exception as exc:
        _log.warning("Failed to write Anthropic debug dump: %s", exc)


class AnthropicCampaignService:
    def __init__(self) -> None:
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def generate_campaign_plan(self, operator_input: dict, template_prompt: str, examples: str = "") -> dict:
        system_prompt = f"""You are a campaign planning assistant for Precise Leads.
Return only valid JSON that matches the CampaignPlan schema.
Do not call Smartlead. Do not invent API responses. Do not expose secrets.
Preserve merge tags and spintax exactly.
Template instructions:
{template_prompt}
Approved examples:
{examples}"""
        response = self.client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": json.dumps(operator_input, ensure_ascii=False)}],
        )
        return json.loads(_strip_markdown_fence(response.content[0].text))

    def revise_campaign_plan(
        self,
        latest_plan: dict,
        revision_instruction: str,
        validation_errors: list[str] | None,
        template_prompt: str,
        examples: str = "",
    ) -> dict:
        system_prompt = f"""You revise Smartlead campaign plans for Precise Leads.
Return only valid JSON matching the CampaignPlan schema.
Do not call Smartlead. Do not invent API responses. Do not expose secrets.
Preserve merge tags and spintax exactly.
Step 1 subjects must be non-empty. Follow-up subjects must be empty strings.
Template instructions:
{template_prompt}
Approved examples:
{examples}"""
        payload = {
            "latest_plan": latest_plan,
            "revision_instruction": revision_instruction,
            "validation_errors": validation_errors or [],
        }
        _debug_dump("revise_request", {"system": system_prompt, "user": payload})
        response = self.client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        raw_text = response.content[0].text
        _debug_dump("revise_response_raw", raw_text)
        try:
            return json.loads(_strip_markdown_fence(raw_text))
        except json.JSONDecodeError:
            _debug_dump("revise_response_decode_failed", raw_text)
            raise
