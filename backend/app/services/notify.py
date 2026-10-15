"""Outbound delivery channels. Both are injectable so tests never hit the network."""
import asyncio
import json
from typing import Protocol

import httpx

from app.core.config import get_settings


class DeliveryError(Exception):
    def __init__(self, msg: str, gone: bool = False):
        super().__init__(msg)
        self.gone = gone  # subscription/chat no longer valid -> drop it


class Sender(Protocol):
    async def telegram(self, chat_id: str, text: str, reply_markup: dict | None = None) -> int: ...
    async def webpush(self, sub: dict, data: dict) -> None: ...


class LiveSender:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.s = get_settings()
        self._client = client or httpx.AsyncClient(timeout=10)

    async def telegram(self, chat_id: str, text: str, reply_markup: dict | None = None) -> int:
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        body, status = await self.bot_api("sendMessage", payload)
        r_status = status
        if r_status == 403 or (r_status == 400 and "chat not found" in str(body.get("description", ""))):
            raise DeliveryError(body.get("description", "forbidden"), gone=True)
        if not body.get("ok"):
            raise DeliveryError(f"telegram {r_status}: {body.get('description')}")
        return int(body["result"]["message_id"])

    async def bot_api(self, method: str, payload: dict) -> tuple[dict, int]:
        """Any Bot API call. Errors never carry the URL (it contains the bot token)."""
        if not self.s.telegram_bot_token:
            raise DeliveryError("telegram not configured")
        try:
            r = await self._client.post(f"https://api.telegram.org/bot{self.s.telegram_bot_token}/{method}", json=payload)
        except httpx.HTTPError as e:
            raise DeliveryError(f"telegram network error: {type(e).__name__}") from None
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return body, r.status_code

    async def webpush(self, sub: dict, data: dict) -> None:
        if not (self.s.vapid_private_key and self.s.vapid_public_key):
            raise DeliveryError("web push not configured")
        from pywebpush import WebPushException, webpush

        def send() -> None:
            webpush(subscription_info=sub, data=json.dumps(data), vapid_private_key=self.s.vapid_private_key,
                    vapid_claims={"sub": self.s.vapid_subject}, ttl=3600)

        try:
            await asyncio.to_thread(send)
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            raise DeliveryError(str(e), gone=code in (404, 410)) from e
