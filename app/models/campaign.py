import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role in ('creator','reviewer','admin')", name="users_role_check"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="creator", nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    oauth_subject: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = created_at()


class SmartleadWorkspace(Base):
    __tablename__ = "smartlead_workspaces"

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    api_key_env_name: Mapped[str] = mapped_column(String, nullable=False)
    client_id: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = created_at()

    mailbox_groups: Mapped[list["MailboxGroup"]] = relationship(back_populates="workspace")


class MailboxGroup(Base):
    __tablename__ = "mailbox_groups"

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("smartlead_workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email_account_ids_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    provider_mix_json: Mapped[dict | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    workspace: Mapped[SmartleadWorkspace] = relationship(back_populates="mailbox_groups")


class CampaignTemplate(Base):
    __tablename__ = "campaign_templates"

    id: Mapped[uuid.UUID] = uuid_pk()
    template_key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    example_block: Mapped[str] = mapped_column(Text, default="", nullable=False)
    schema_version: Mapped[str] = mapped_column(String, default="campaign_plan_v1", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CampaignRequest(Base):
    __tablename__ = "campaign_requests"
    __table_args__ = (
        CheckConstraint(
            "status in ('drafting','needs_revision','approved','syncing','synced','failed','archived')",
            name="campaign_requests_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("smartlead_workspaces.id"), nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_templates.id"), nullable=False)
    raw_input_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    lead_source_type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="drafting", nullable=False, index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workspace: Mapped[SmartleadWorkspace] = relationship()
    template: Mapped[CampaignTemplate] = relationship()
    drafts: Mapped[list["CampaignDraft"]] = relationship(back_populates="request")
    runs: Mapped[list["CampaignRun"]] = relationship(back_populates="request")


class CampaignDraft(Base):
    __tablename__ = "campaign_drafts"
    __table_args__ = (
        CheckConstraint(
            "validation_status in ('generated','invalid','valid','superseded','approved')",
            name="campaign_drafts_validation_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_requests.id"), nullable=False)
    draft_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String)
    model_name: Mapped[str | None] = mapped_column(String)
    validation_status: Mapped[str] = mapped_column(String, default="generated", nullable=False)
    validation_errors_json: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()

    request: Mapped[CampaignRequest] = relationship(back_populates="drafts")


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_requests.id"), nullable=False)
    message_log_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    latest_draft_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaign_drafts.id"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CampaignRun(Base):
    __tablename__ = "campaign_runs"
    __table_args__ = (
        CheckConstraint(
            "run_status in ('queued','running','succeeded','failed','retrying')",
            name="campaign_runs_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_requests.id"), nullable=False)
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_drafts.id"), nullable=False)
    smartlead_campaign_id: Mapped[int | None] = mapped_column(Integer)
    run_status: Mapped[str] = mapped_column(String, default="queued", nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_text: Mapped[str | None] = mapped_column(Text)

    request: Mapped[CampaignRequest] = relationship(back_populates="runs")
    steps: Mapped[list["CampaignRunStep"]] = relationship(back_populates="run")


class CampaignRunStep(Base):
    __tablename__ = "campaign_run_steps"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','running','succeeded','failed','skipped')",
            name="campaign_run_steps_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_runs.id"), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    request_json: Mapped[dict | None] = mapped_column(JSONB)
    response_json: Mapped[dict | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    run: Mapped[CampaignRun] = relationship(back_populates="steps")


class LeadUpload(Base):
    __tablename__ = "lead_uploads"

    id: Mapped[uuid.UUID] = uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_requests.id"), nullable=False)
    filename: Mapped[str | None] = mapped_column(String)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    normalized_leads_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    validation_errors_json: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("smartlead_workspaces.id"))
    smartlead_campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    event_type: Mapped[str | None] = mapped_column(String, index=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
