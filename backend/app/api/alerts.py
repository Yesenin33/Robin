"""User alerts: list/read, Web Push subscription, Telegram linking (code sent to the chat)."""
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.core.config import get_settings
from app.core.db import get_session
from app.core.ratelimit import rate_limit
from app.core.redis import get_redis
from app.models import Alert, PushSubscription, User
from app.schemas.alerts import (
    AlertOut, AlertsOut, ChannelsOut, PushKeyOut, PushSubIn, TelegramStartIn, TelegramVerifyIn,
)
from app.services.notify import DeliveryError, LiveSender

router = APIRouter(prefix="/alerts", tags=["alerts"], dependencies=[Depends(rate_limit)])
TG_CODE_TTL = 600


def get_sender() -> LiveSender:
    return LiveSender()


@router.get("", response_model=AlertsOut)
async def list_alerts(limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Alert).where(Alert.user_id == user.id).order_by(Alert.id.desc()).limit(limit))).scalars()
    unread = await db.scalar(select(func.count(Alert.id)).where(Alert.user_id == user.id, Alert.read_at.is_(None)))
    return AlertsOut(unread=unread or 0, items=[AlertOut(
        id=a.id, kind=a.kind, title=a.title, body=a.body, url=(a.payload or {}).get("url"), created_at=a.created_at,
        read=a.read_at is not None, delivered_push=a.delivered_push_at is not None,
        delivered_telegram=a.delivered_tg_at is not None) for a in rows])


@router.post("/{alert_id}/read", status_code=204)
async def mark_read(alert_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    res = await db.execute(update(Alert).where(Alert.id == alert_id, Alert.user_id == user.id, Alert.read_at.is_(None))
                           .values(read_at=datetime.now(timezone.utc)))
    if res.rowcount == 0 and not await db.scalar(select(Alert.id).where(Alert.id == alert_id, Alert.user_id == user.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    await db.commit()


@router.post("/read-all", status_code=204)
async def mark_all_read(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    await db.execute(update(Alert).where(Alert.user_id == user.id, Alert.read_at.is_(None))
                     .values(read_at=datetime.now(timezone.utc)))
    await db.commit()


@router.get("/channels", response_model=ChannelsOut)
async def channels(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    has_push = bool(await db.scalar(select(func.count(PushSubscription.id)).where(PushSubscription.user_id == user.id)))
    return ChannelsOut(push=has_push and user.push_enabled, telegram=bool(user.telegram_chat_id),
                       telegram_bot=get_settings().telegram_bot_username or None)


# ---------- Web Push ----------

@router.get("/push/key", response_model=PushKeyOut)
async def push_key():
    key = get_settings().vapid_public_key
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Phone alerts are not set up on this server.")
    return PushKeyOut(public_key=key)


@router.post("/push/subscribe", status_code=204)
async def push_subscribe(body: PushSubIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    if not {"p256dh", "auth"} <= body.keys.keys():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "keys.p256dh and keys.auth are required")
    existing = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))).scalar_one_or_none()
    if existing:
        existing.user_id, existing.p256dh, existing.auth = user.id, body.keys["p256dh"], body.keys["auth"]
    else:
        db.add(PushSubscription(user_id=user.id, endpoint=body.endpoint, p256dh=body.keys["p256dh"], auth=body.keys["auth"]))
    user.push_enabled = True
    await db.commit()


@router.delete("/push/subscribe", status_code=204)
async def push_unsubscribe(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    await db.execute(delete(PushSubscription).where(PushSubscription.user_id == user.id))
    user.push_enabled = False
    await db.commit()


# ---------- Telegram ----------

@router.post("/telegram/start", status_code=202)
async def telegram_start(body: TelegramStartIn, user: User = Depends(current_user),
                         redis: Redis = Depends(get_redis), sender: LiveSender = Depends(get_sender)):
    """Send a 6-digit code to the chat; entering it proves the user owns that chat."""
    if not get_settings().telegram_bot_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram alerts are not set up on this server.")
    code = f"{secrets.randbelow(10**6):06d}"
    try:
        await sender.telegram(body.chat_id, f"Your Chainfolio code: {code}\nEnter it in the app to turn on alerts.")
    except DeliveryError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "We couldn't message that chat. Open the bot in Telegram and press Start first.") from e
    await redis.set(f"tg:code:{user.id}", f"{code}:{body.chat_id}", ex=TG_CODE_TTL)
    return {"sent": True}


@router.post("/telegram/verify", response_model=ChannelsOut)
async def telegram_verify(body: TelegramVerifyIn, user: User = Depends(current_user),
                          db: AsyncSession = Depends(get_session), redis: Redis = Depends(get_redis)):
    stored = await redis.get(f"tg:code:{user.id}")
    attempts = await redis.incr(f"tg:tries:{user.id}")
    await redis.expire(f"tg:tries:{user.id}", TG_CODE_TTL)
    if not stored or attempts > 5 or not secrets.compare_digest(stored.split(":")[0], body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code is wrong or expired.")
    user.telegram_chat_id = stored.split(":", 1)[1]
    await db.commit()
    await redis.delete(f"tg:code:{user.id}", f"tg:tries:{user.id}")
    return await channels(user, db)


@router.delete("/telegram", status_code=204)
async def telegram_unlink(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    user.telegram_chat_id = None
    await db.commit()
