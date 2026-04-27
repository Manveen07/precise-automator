import json

from anthropic import Anthropic

from app.config import settings


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
        return json.loads(response.content[0].text)

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
        response = self.client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        return json.loads(response.content[0].text)
