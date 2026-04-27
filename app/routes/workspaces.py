from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignTemplate, SmartleadWorkspace
from app.seed import seed_defaults

router = APIRouter(prefix="/api", tags=["workspaces"])


@router.get("/workspaces")
def list_workspaces(db: Session = Depends(get_db)) -> list[dict]:
    seed_defaults(db)
    rows = db.query(SmartleadWorkspace).filter_by(active=True).order_by(SmartleadWorkspace.display_name).all()
    return [
        {
            "workspace_key": row.workspace_key,
            "display_name": row.display_name,
            "client_id": row.client_id,
        }
        for row in rows
    ]


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)) -> list[dict]:
    seed_defaults(db)
    rows = db.query(CampaignTemplate).filter_by(active=True).order_by(CampaignTemplate.name).all()
    return [
        {
            "template_key": row.template_key,
            "name": row.name,
            "version": row.version,
            "schema_version": row.schema_version,
        }
        for row in rows
    ]
