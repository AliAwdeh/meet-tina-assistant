import logging
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class OpenWAClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send_text(self, chat_id: str, text: str) -> dict[str, Any]:
        if not self.settings.openwa_api_base_url:
            logger.info("openwa_send_skipped", extra={"chat_id": chat_id})
            return {"status": "skipped", "reason": "OPENWA_API_BASE_URL not configured"}
        url = f"{self.settings.openwa_api_base_url.rstrip('/')}/sendText"
        headers = {"Authorization": f"Bearer {self.settings.openwa_api_token}"} if self.settings.openwa_api_token else {}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json={"args": {"to": chat_id, "content": text}}, headers=headers)
            response.raise_for_status()
            return response.json()

    async def download_media(self, media_url: str) -> bytes:
        if not media_url.startswith(self.settings.openwa_api_base_url.rstrip("/")) and self.settings.openwa_api_base_url:
            raise ValueError("media URL is outside the configured OpenWA API host")
        headers = {"Authorization": f"Bearer {self.settings.openwa_api_token}"} if self.settings.openwa_api_token else {}
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            response = await client.get(media_url, headers=headers)
            response.raise_for_status()
            return response.content
