"""User alerts: list/read, Web Push subscription, Telegram linking (code sent to the chat)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, optional_user
from app.core.config import get_settings
from app.core.db import get_session
from app.core.ratelimit import rate_limit
from app.core.redis import get_redis
from app.models import Alert, PushSubscription, User
from app.core.security import issue_jwt
from app.schemas.alerts import (
    AlertOut, AlertsOut, ChannelsOut, PushKeyOut, PushSubIn, TelegramLinkOut, TelegramVerifyIn, WebAppLinkIn,
    WebAppLinkOut,
)
from app.services import telegram_bot
from app.services.auth_service import AuthService
from app.services.notify import DeliveryError, LiveSender
from app.services.telegram_auth import InitDataError, display_name, validate_init_data

router = APIRouter(prefix="/alerts", tags=["alerts"], dependencies=[Depends(rate_limit)])

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
                       telegram_alerts=user.telegram_alerts, telegram_bot=get_settings().telegram_bot_username or None)


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

def _need_bot() -> None:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_bot_username):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Telegram alerts are not set up on this server.")


@router.post("/telegram/link", response_model=TelegramLinkOut)
async def telegram_link(user: User = Depends(current_user), redis: Redis = Depends(get_redis)):
    """Deep link (one tap in any browser) + the same link as a 6-digit code for the bot."""
    _need_bot()
    return TelegramLinkOut(**await telegram_bot.issue_link(redis, user.id))


@router.post("/telegram/link-webapp", response_model=WebAppLinkOut)
async def telegram_link_webapp(body: WebAppLinkIn, user: User | None = Depends(optional_user),
                               db: AsyncSession = Depends(get_session), sender: LiveSender = Depends(get_sender)):
    """Mini App: validated initData links this Telegram user with zero typing.

    Signed in -> link to that account. Not signed in -> sign in as the account already linked to this
    Telegram user, or create one (the wallet can be added later; linking from the wallet account moves
    the chat there)."""
    _need_bot()
    try:
        tg = validate_init_data(body.init_data, get_settings().telegram_bot_token)
    except InitDataError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Telegram sign-in check failed.") from e
    chat_id = str(tg["id"])  # private chat with the bot == the user's id
    token = None
    if user is None:
        user = await telegram_bot.user_for_chat(db, chat_id)
        if user is None:
            user = await AuthService(db, get_redis())._create_user()
        token = issue_jwt(user.id)
    await telegram_bot.link_chat(db, user, chat_id)
    try:
        await sender.telegram(chat_id, "✅ Connected. Alerts for your wallet will arrive here.")
        can_message = True
    except DeliveryError:
        can_message = False  # user hasn't allowed the bot to write yet
    return WebAppLinkOut(linked=True, chat=display_name(tg), can_message=can_message, access_token=token)


@router.post("/telegram/verify", response_model=ChannelsOut)
async def telegram_verify(body: TelegramVerifyIn, user: User = Depends(current_user),
                          db: AsyncSession = Depends(get_session), redis: Redis = Depends(get_redis)):
    """Manual fallback: the code the bot sent in reply to /start."""
    attempts = await redis.incr(f"tg:tries:{user.id}")
    await redis.expire(f"tg:tries:{user.id}", 600)
    chat_id = await redis.getdel(f"tg:chatcode:{body.code}") if attempts <= 5 else None
    if not chat_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code is wrong or expired.")
    await telegram_bot.link_chat(db, user, chat_id)
    await redis.delete(f"tg:tries:{user.id}")
    return await channels(user, db)


@router.delete("/telegram", status_code=204)
async def telegram_unlink(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    user.telegram_chat_id = None
    await db.commit()
