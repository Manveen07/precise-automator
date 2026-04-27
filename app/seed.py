from sqlalchemy.orm import Session

from app.models import CampaignTemplate, SmartleadWorkspace


SYSTEM_PROMPT = """You create Smartlead campaign plans for Precise Leads.
Return only valid JSON matching the CampaignPlan schema.
Preserve merge tags and spintax exactly.
Never include API keys or secrets."""


def seed_defaults(db: Session) -> None:
    workspaces = [
        {
            "workspace_key": "smartlead_mcp",
            "display_name": "Smartlead MCP (Legacy - no client scope)",
            "api_key_env_name": "SMARTLEAD_MCP_API_KEY",
        },
        {
            "workspace_key": "smartlead_mcp_melior",
            "display_name": "Smartlead MCP - Melior",
            "api_key_env_name": "SMARTLEAD_MCP_API_KEY",
            "client_id": 12256,
        },
        {
            "workspace_key": "smartlead_mcp_avench",
            "display_name": "Smartlead MCP - Avench",
            "api_key_env_name": "SMARTLEAD_MCP_API_KEY",
            "client_id": 88657,
        },
        {
            "workspace_key": "smartlead_mcp_svsg",
            "display_name": "Smartlead MCP - SVSG",
            "api_key_env_name": "SMARTLEAD_MCP_API_KEY",
            "client_id": 145916,
        },
        {
            "workspace_key": "belardi_wong",
            "display_name": "Smartlead - Belardi Wong",
            "api_key_env_name": "SMARTLEAD_BELARDI_WONG_API_KEY",
        },
    ]
    for data in workspaces:
        exists = db.query(SmartleadWorkspace).filter_by(workspace_key=data["workspace_key"]).first()
        if not exists:
            db.add(SmartleadWorkspace(**data))
        else:
            for key in ("display_name", "api_key_env_name", "client_id"):
                if key in data:
                    setattr(exists, key, data[key])

    template = db.query(CampaignTemplate).filter_by(template_key="cold_email_standard_v1").first()
    if not template:
        db.add(
            CampaignTemplate(
                template_key="cold_email_standard_v1",
                name="Cold Email Standard V1",
                version=1,
                system_prompt=SYSTEM_PROMPT,
                example_block="",
                schema_version="campaign_plan_v1",
            )
        )
    db.commit()
