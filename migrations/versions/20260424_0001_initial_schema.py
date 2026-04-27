"""initial schema

Revision ID: 20260424_0001
Revises:
Create Date: 2026-04-24 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260424_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("oauth_subject", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role in ('creator','reviewer','admin')", name="users_role_check"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=False)

    op.create_table(
        "smartlead_workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_key", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("api_key_env_name", sa.String(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_key"),
    )
    op.create_index(op.f("ix_smartlead_workspaces_workspace_key"), "smartlead_workspaces", ["workspace_key"], unique=False)

    op.create_table(
        "campaign_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("example_block", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_key"),
    )
    op.create_index(op.f("ix_campaign_templates_template_key"), "campaign_templates", ["template_key"], unique=False)

    op.create_table(
        "mailbox_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email_account_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider_mix_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["smartlead_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "campaign_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("lead_source_type", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status in ('drafting','needs_revision','approved','syncing','synced','failed','archived')",
            name="campaign_requests_status_check",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["campaign_templates.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["smartlead_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_campaign_requests_status"), "campaign_requests", ["status"], unique=False)

    op.create_table(
        "campaign_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("validation_status", sa.String(), nullable=False),
        sa.Column("validation_errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "validation_status in ('generated','invalid','valid','superseded','approved')",
            name="campaign_drafts_validation_status_check",
        ),
        sa.ForeignKeyConstraint(["request_id"], ["campaign_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "conversation_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_log_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("latest_draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["latest_draft_id"], ["campaign_drafts.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["campaign_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "campaign_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("smartlead_campaign_id", sa.Integer(), nullable=True),
        sa.Column("run_status", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.CheckConstraint("run_status in ('queued','running','succeeded','failed','retrying')", name="campaign_runs_status_check"),
        sa.ForeignKeyConstraint(["draft_id"], ["campaign_drafts.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["campaign_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(op.f("ix_campaign_runs_run_status"), "campaign_runs", ["run_status"], unique=False)

    op.create_table(
        "lead_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("normalized_leads_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation_errors_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["campaign_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("smartlead_campaign_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["smartlead_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_webhook_events_event_type"), "webhook_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_webhook_events_smartlead_campaign_id"), "webhook_events", ["smartlead_campaign_id"], unique=False)

    op.create_table(
        "campaign_run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status in ('pending','running','succeeded','failed','skipped')", name="campaign_run_steps_status_check"),
        sa.ForeignKeyConstraint(["run_id"], ["campaign_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("campaign_run_steps")
    op.drop_index(op.f("ix_webhook_events_smartlead_campaign_id"), table_name="webhook_events")
    op.drop_index(op.f("ix_webhook_events_event_type"), table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_table("lead_uploads")
    op.drop_index(op.f("ix_campaign_runs_run_status"), table_name="campaign_runs")
    op.drop_table("campaign_runs")
    op.drop_table("conversation_sessions")
    op.drop_table("campaign_drafts")
    op.drop_index(op.f("ix_campaign_requests_status"), table_name="campaign_requests")
    op.drop_table("campaign_requests")
    op.drop_table("mailbox_groups")
    op.drop_index(op.f("ix_campaign_templates_template_key"), table_name="campaign_templates")
    op.drop_table("campaign_templates")
    op.drop_index(op.f("ix_smartlead_workspaces_workspace_key"), table_name="smartlead_workspaces")
    op.drop_table("smartlead_workspaces")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
