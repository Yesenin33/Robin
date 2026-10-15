from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.services import alert_service
from app.services.notify import LiveSender

_sender: LiveSender | None = None


async def deliver(redis: Redis) -> dict:
    global _sender
    _sender = _sender or LiveSender()
    async with SessionLocal() as db:
        return await alert_service.deliver_pending(db, redis, _sender)


async def price_moves(redis: Redis) -> int:
    async with SessionLocal() as db:
        return await alert_service.price_moves(db, redis, get_settings().price_move_pct)
