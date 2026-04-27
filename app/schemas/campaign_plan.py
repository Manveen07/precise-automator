from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class CampaignSchedule(BaseModel):
    timezone: str = "America/New_York"
    days_of_the_week: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    start_hour: str = "09:00"
    end_hour: str = "18:00"
    min_time_btw_emails: int = 17
    max_new_leads_per_day: int = 100

    @field_validator("days_of_the_week")
    @classmethod
    def active_days_required(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("schedule must include at least one active day")
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("days_of_the_week values must be 1 through 7")
        return value

    @field_validator("start_hour", "end_hour", mode="before")
    @classmethod
    def normalize_hour(cls, value: str) -> str:
        return _parse_schedule_time(value).strftime("%H:%M")

    @model_validator(mode="after")
    def start_before_end(self) -> "CampaignSchedule":
        if _parse_schedule_time(self.start_hour) >= _parse_schedule_time(self.end_hour):
            raise ValueError("schedule start_hour must be before end_hour")
        return self


class CampaignSettings(BaseModel):
    send_as_plain_text: bool = True
    track_opens: bool = False
    track_clicks: bool = False
    stop_on_reply: bool = True
    enable_ai_esp_matching: bool = True
    auto_pause_domain_leads_on_reply: bool = True
    ooo_restart_delay_days: int = 10


class SequenceVariant(BaseModel):
    variant_label: str | None = None
    subject: str = ""
    body: str


class SequenceStep(BaseModel):
    step_number: int
    delay_days: int
    variants: list[SequenceVariant]

    @field_validator("variants")
    @classmethod
    def variants_required(cls, value: list[SequenceVariant]) -> list[SequenceVariant]:
        if not value:
            raise ValueError("each sequence step needs at least one variant")
        return value


class InboxSelection(BaseModel):
    mode: Literal["manual_ids", "recommend", "skip"] = "skip"
    email_account_ids: list[int] = Field(default_factory=list)
    provider_mix: dict[str, float] = Field(default_factory=lambda: {"gmail": 0.7, "outlook": 0.3})


class LeadSource(BaseModel):
    type: Literal["csv_upload", "pasted_list", "none"] = "none"
    expected_count: int | None = None


class CampaignPlan(BaseModel):
    workspace_key: str
    client_key: str | None = None
    campaign_name: str
    template_family: str = "cold_email_standard_v1"
    goal: Literal["book_meeting", "reply", "event_meeting"] = "book_meeting"
    lead_source: LeadSource = Field(default_factory=LeadSource)
    schedule: CampaignSchedule = Field(default_factory=CampaignSchedule)
    settings: CampaignSettings = Field(default_factory=CampaignSettings)
    inbox_selection: InboxSelection = Field(default_factory=InboxSelection)
    sequence: list[SequenceStep]
    approval_required: bool = True
    notes_for_operator: list[str] = Field(default_factory=list)

    @field_validator("campaign_name")
    @classmethod
    def campaign_name_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("campaign_name is required")
        return value

    @field_validator("sequence")
    @classmethod
    def sequence_limits(cls, value: list[SequenceStep]) -> list[SequenceStep]:
        if not value:
            raise ValueError("sequence needs at least one step")
        if len(value) > 4:
            raise ValueError("V1 supports at most 4 sequence steps")
        return value


def _parse_schedule_time(value: str) -> time:
    if not isinstance(value, str):
        raise ValueError("schedule hour must be a string")
    normalized = value.strip().replace(".", "")
    candidates = [normalized, normalized.upper(), normalized.lower()]
    for candidate in candidates:
        for pattern in ("%H:%M", "%I:%M %p", "%I %p"):
            try:
                return datetime.strptime(candidate, pattern).time()
            except ValueError:
                continue
    raise ValueError("schedule hour must use HH:MM or a simple AM/PM time")
