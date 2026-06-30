import json

import httpx


class HeyReachService:
    BASE_URL = "https://api.heyreach.io/api/public"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": api_key,
            "User-Agent": "Precise-Automator/1.0",
        }

    def url(self, endpoint: str) -> str:
        return f"{self.BASE_URL}/{endpoint}"

    def campaign_url(self, campaign_id: int) -> str:
        return f"https://app.heyreach.io/app/campaigns/{campaign_id}"

    async def post(self, endpoint: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(self.url(endpoint), json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.get(self.url(endpoint), params=params, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def get_linkedin_accounts(self, limit: int = 100, offset: int = 0) -> dict:
        # Extract unique account IDs from existing campaigns as a fallback —
        # the direct accounts endpoint is not available on all API key tiers.
        data = await self.post("campaign/GetAll", {"limit": limit, "offset": offset})
        seen: set[int] = set()
        items = []
        for c in data.get("items", []):
            for aid in c.get("campaignAccountIds") or []:
                if aid not in seen:
                    seen.add(aid)
                    items.append({"id": aid})
        return {"items": items, "totalCount": len(items)}

    async def create_empty_list(self, name: str) -> dict:
        return await self.post("list/CreateEmptyList", {"name": name, "listType": "USER_LIST"})

    async def create_campaign(
        self,
        name: str,
        list_id: int,
        account_ids: list[int],
        sequence: dict,
        schedule: dict | None = None,
    ) -> dict:
        payload: dict = {
            "name": name,
            "linkedInUserListId": list_id,
            "linkedInAccountIds": account_ids,
            "sequenceJson": json.dumps(sequence),
        }
        if schedule is not None:
            payload["schedule"] = schedule
        return await self.post("campaign/Create", payload)
