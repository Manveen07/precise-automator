import httpx

from app.config import settings


async def send_slack_summary(text: str) -> None:
    if not settings.SLACK_WEBHOOK_URL:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(str(settings.SLACK_WEBHOOK_URL), json={"text": text})
