"""Bot update receiver: POST /telegram/webhook/{secret} (public as /api/telegram/webhook/... via nginx)."""
import secrets

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.services import telegram_bot
from app.services.market import MarketClient
from app.services.notify import LiveSender
from app.services.portfolio_service import PortfolioService
from app.services.rpc import RPCClient

router = APIRouter(prefix="/telegram", tags=["telegram"], include_in_schema=False)
log = get_logger(__name__)
_sender: LiveSender | None = None


def sender() -> LiveSender:
    global _sender
    _sender = _sender or LiveSender()
    return _sender


async def process(update: dict) -> None:
    redis = get_redis()
    uid = update.get("update_id")
    if uid is not None and not await redis.set(f"tg:upd:{uid}", "1", nx=True, ex=3600):
        return  # Telegram retry of an update we already handled
    async with SessionLocal() as db:
        async def safety(ca: str):
            return await telegram_bot.safety_lookup(db, redis, ca)

        async def portfolio(address: str):
            return await PortfolioService(db, redis, RPCClient(), MarketClient(redis)).get(address)

        ctx = telegram_bot.BotContext(db=db, redis=redis, sender=sender(), safety=safety, portfolio=portfolio)
        try:
            await telegram_bot.handle_update(update, ctx)
        except Exception:  # noqa: BLE001 — never let one bad update break the receiver
            log.exception("bot_update_failed", update_id=uid)


@router.post("/webhook/{secret}")
async def webhook(secret: str, request: Request, background: BackgroundTasks,
                  x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    expected = get_settings().telegram_webhook_secret
    if not expected or not secrets.compare_digest(secret, expected) or \
            not secrets.compare_digest(x_telegram_bot_api_secret_token or "", expected):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    update = await request.json()
    background.add_task(process, update)  # answer Telegram at once; slow commands (/safe) run after
    return {"ok": True}
