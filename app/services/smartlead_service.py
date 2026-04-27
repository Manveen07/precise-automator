from urllib.parse import urlencode

import httpx


class SmartleadService:
    BASE_URL = "https://server.smartlead.ai/api/v1"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    def url(self, endpoint: str, params: dict | None = None) -> str:
        query = {"api_key": self.api_key}
        if params:
            query.update({key: value for key, value in params.items() if value is not None})
        return f"{self.BASE_URL}/{endpoint}?{urlencode(query)}"

    def campaign_url(self, campaign_id: int) -> str:
        return f"https://app.smartlead.ai/app/campaign/{campaign_id}"

    async def post(self, endpoint: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(self.url(endpoint), json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def patch(self, endpoint: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.patch(self.url(endpoint), json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def delete(self, endpoint: str) -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.delete(self.url(endpoint), headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.get(self.url(endpoint, params), headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def create_campaign(self, campaign_name: str, client_id: int | None = None) -> dict:
        payload: dict[str, str | int] = {"name": campaign_name}
        if client_id is not None:
            payload["client_id"] = client_id
        return await self.post("campaigns/create", payload)

    async def apply_v1_settings(self, campaign_id: int, ooo_delay_days: int = 10) -> list[dict]:
        responses = []
        responses.append(
            await self.post(
                f"campaigns/{campaign_id}/settings",
                {
                    "stop_lead_settings": "REPLY_TO_AN_EMAIL",
                    "follow_up_percentage": 50,
                    "track_settings": ["DONT_TRACK_EMAIL_OPEN", "DONT_TRACK_LINK_CLICK"],
                    "add_unsubscribe_tag": False,
                    "unsubscribe_text": "",
                    "auto_pause_domain_leads_on_reply": True,
                    "enable_ai_esp_matching": True,
                    "bounce_autopause_threshold": "3",
                    "domain_level_rate_limit": False,
                    "ai_categorisation_options": [1, 2, 3, 4, 5, 6, 7, 8, 9],
                    "out_of_office_detection_settings": {
                        "ignoreOOOasReply": True,
                        "autoCategorizeOOO": False,
                        "autoReactivateOOO": True,
                        "reactivateOOOwithDelay": 0,
                    },
                },
            )
        )
        responses.append(
            await self.post(
                f"campaigns/{campaign_id}/settings",
                {"out_of_office_detection_settings": {"reactivateOOOwithDelay": ooo_delay_days}},
            )
        )
        responses.append(
            await self.post(
                f"campaigns/{campaign_id}/settings",
                {"send_as_plain_text": True, "force_plain_text": True},
            )
        )
        return responses

    async def update_schedule(self, campaign_id: int, schedule: dict) -> dict:
        return await self.post(f"campaigns/{campaign_id}/schedule", schedule)

    async def update_sequences(self, campaign_id: int, sequences: list[dict]) -> dict:
        return await self.post(f"campaigns/{campaign_id}/sequences", {"sequences": sequences})

    async def attach_email_accounts(self, campaign_id: int, email_account_ids: list[int]) -> dict:
        return await self.post(f"campaigns/{campaign_id}/email-accounts", {"email_account_ids": email_account_ids})

    async def add_leads(self, campaign_id: int, leads: list[dict]) -> dict:
        return await self.post(f"campaigns/{campaign_id}/leads", {"leads": leads})

    async def create_webhook(self, campaign_id: int, webhook_url: str) -> dict:
        return await self.post(
            f"campaigns/{campaign_id}/webhooks",
            {
                "name": "Precise Automator Reply Webhook",
                "webhook_url": webhook_url,
                "event_types": ["EMAIL_REPLY", "LEAD_CATEGORY_UPDATED"],
            },
        )

    async def update_status(self, campaign_id: int, status: str) -> dict:
        return await self.patch(f"campaigns/{campaign_id}/status", {"status": status})

    async def delete_campaign(self, campaign_id: int) -> dict:
        return await self.delete(f"campaigns/{campaign_id}")

    async def archive_campaign(self, campaign_id: int) -> dict:
        return await self.update_status(campaign_id, "ARCHIVED")

    async def get_campaign(self, campaign_id: int) -> dict:
        return await self.get(f"campaigns/{campaign_id}")

    async def get_sequences(self, campaign_id: int) -> dict:
        return await self.get(f"campaigns/{campaign_id}/sequences")

    async def get_campaign_analytics(self, campaign_id: int) -> dict:
        return await self.get(f"campaigns/{campaign_id}/analytics")

    async def get_campaign_statistics(self, campaign_id: int, limit: int = 100, offset: int = 0) -> dict:
        return await self.get(f"campaigns/{campaign_id}/statistics", {"limit": limit, "offset": offset})

    async def get_campaign_lead_statistics(self, campaign_id: int, limit: int = 100, offset: int = 0) -> dict:
        return await self.get(f"campaigns/{campaign_id}/leads-statistics", {"limit": limit, "offset": offset})

    async def get_campaign_performance(
        self,
        start_date: str,
        end_date: str,
        timezone: str = "America/New_York",
        campaign_ids: list[int] | None = None,
    ) -> dict:
        return await self.get(
            "analytics/campaign/overall-stats",
            {
                "start_date": start_date,
                "end_date": end_date,
                "timezone": timezone,
                "campaign_ids": ",".join(str(item) for item in campaign_ids) if campaign_ids else None,
            },
        )
