"""Alert generation (copy events, safety drops, price moves) and delivery (Telegram, Web Push)."""
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import Alert, CopyEvent, Position, PushSubscription, Token, User, Wallet
from app.services import copy_service
from app.services.notify import DeliveryError, Sender

log = get_logger(__name__)
SEVERITY = {"CLEAN": 0, "RISKY": 1, "DANGER": 2}
DELIVERY_WINDOW = timedelta(hours=1)


def _short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


async def create(db: AsyncSession, redis: Redis, user_id: int, kind: str, title: str, body: str,
                 payload: dict | None = None, dedupe: str | None = None) -> Alert | None:
    """Add an in-app alert (caller commits). `dedupe` suppresses repeats for 24h."""
    if dedupe and not await redis.set(f"alert:dedupe:{user_id}:{dedupe}", "1", nx=True, ex=86400):
        return None
    alert = Alert(user_id=user_id, kind=kind, title=title[:160], body=body[:1024], payload=payload or {})
    db.add(alert)
    return alert


# ---------- generation ----------

async def on_copy_events(db: AsyncSession, redis: Redis, events: list[CopyEvent]) -> int:
    n = 0
    for ev in events:
        symbol = (ev.meta or {}).get("symbol") or ev.token_address[:8]
        usd = f" (${float(ev.amount_usd):,.0f})" if ev.amount_usd is not None else ""
        for uid in await copy_service.followers(db, ev.address):
            title = f"{_short(ev.address)} {'bought' if ev.side == 'buy' else 'sold'} {symbol}{usd}"
            if await create(db, redis, uid, "copy_event", title,
                            "A wallet you follow just traded. Open Chainfolio to see the safety check.",
                            {"address": ev.address, "token": ev.token_address, "tx": ev.tx_hash, "url": "/app/copy"},
                            dedupe=f"copy:{ev.tx_hash}:{ev.token_address}:{ev.address}"):
                n += 1
    await db.commit()
    return n


async def holders_of(db: AsyncSession, token: str) -> list[int]:
    """Users whose wallets held the token in a recent position snapshot."""
    since = datetime.now(timezone.utc) - timedelta(days=1)
    rows = await db.execute(
        select(Wallet.user_id).join(Position, Position.wallet_address == Wallet.address)
        .where(Position.token_address == token, Position.amount > 0, Position.ts >= since).distinct()
    )
    return [u for (u,) in rows.all()]


async def on_verdict(db: AsyncSession, redis: Redis, token: Token, old: str | None, new: str, risks: list[str]) -> int:
    if old is None or SEVERITY[new] <= SEVERITY[old]:
        return 0
    sym = token.symbol or _short(token.address)
    body = f"Our safety check moved {sym} from {old} to {new}." + (f" {risks[0]}" if risks else "")
    n = 0
    for uid in await holders_of(db, token.address):
        if await create(db, redis, uid, "safety_drop", f"{sym} is now {new}", body,
                        {"token": token.address, "old": old, "new": new, "url": f"/app/safety/{token.address}"},
                        dedupe=f"safety:{token.address}:{new}"):
            n += 1
    await db.commit()
    return n


async def price_moves(db: AsyncSession, redis: Redis, pct: float) -> int:
    """Alert holders when a token moved >= pct% since the last baseline (baseline resets after alerting)."""
    since = datetime.now(timezone.utc) - timedelta(days=1)
    held = {a for (a,) in (await db.execute(
        select(Position.token_address).where(Position.amount > 0, Position.ts >= since).distinct())).all()}
    if not held:
        return 0
    tokens = (await db.execute(select(Token).where(Token.address.in_(held), Token.price_usd.is_not(None)))).scalars()
    n = 0
    for t in tokens:
        price, key = float(t.price_usd), f"px:base:{t.address}"
        base = await redis.get(key)
        if base is None:
            await redis.set(key, price, ex=86400)
            continue
        move = (price - float(base)) / float(base) * 100 if float(base) else 0
        if abs(move) < pct:
            continue
        await redis.set(key, price, ex=86400)
        sym = t.symbol or _short(t.address)
        for uid in await holders_of(db, t.address):
            if await create(db, redis, uid, "price", f"{sym} {'up' if move > 0 else 'down'} {abs(move):.0f}%",
                            f"{sym} is now ${price:,.6g}.", {"token": t.address, "move_pct": round(move, 1),
                                                             "url": "/app/portfolio"}):
                n += 1
    await db.commit()
    return n


# ---------- delivery ----------

async def _quota_ok(redis: Redis, user_id: int, limit: int) -> bool:
    key = f"alerts:q:{user_id}:{datetime.now(timezone.utc):%Y%m%d%H}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, 3700)
    return n <= limit


def _text(a: Alert) -> str:
    return f"{a.title}\n{a.body}"


async def deliver_pending(db: AsyncSession, redis: Redis, sender: Sender) -> dict:
    s = get_settings()
    since = datetime.now(timezone.utc) - DELIVERY_WINDOW
    alerts = (await db.execute(
        select(Alert).where(Alert.created_at >= since,
                            (Alert.delivered_tg_at.is_(None)) | (Alert.delivered_push_at.is_(None)))
        .order_by(Alert.id)
    )).scalars().all()
    stats = {"telegram": 0, "push": 0, "throttled": 0, "failed": 0}
    for a in alerts:
        payload = dict(a.payload or {})
        if payload.get("throttled") or payload.get("done"):
            continue
        user = await db.get(User, a.user_id)
        subs = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == a.user_id))).scalars().all()
        want_tg = bool(user and user.telegram_chat_id and s.telegram_bot_token) and a.delivered_tg_at is None
        want_push = bool(subs and s.vapid_private_key) and a.delivered_push_at is None and user and user.push_enabled
        if not (want_tg or want_push):
            payload["done"] = True  # no channel: in-app only
            a.payload = payload
            continue
        if not payload.get("counted"):
            if not await _quota_ok(redis, a.user_id, s.alerts_per_hour):
                payload["throttled"] = True
                a.payload = payload
                stats["throttled"] += 1
                continue
            payload["counted"] = True
        now = datetime.now(timezone.utc)
        if want_tg:
            try:
                payload["tg_message_id"] = await sender.telegram(user.telegram_chat_id, _text(a))
                a.delivered_tg_at = now
                stats["telegram"] += 1
            except DeliveryError as e:
                stats["failed"] += 1
                log.warning("alert_tg_failed", alert=a.id, error=str(e))
                if e.gone:
                    user.telegram_chat_id = None
        if want_push:
            data = {"title": a.title, "body": a.body, "url": payload.get("url", "/app/portfolio")}
            sent = False
            for sub in subs:
                try:
                    await sender.webpush({"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}, data)
                    sent = True
                except DeliveryError as e:
                    log.warning("alert_push_failed", alert=a.id, error=str(e))
                    if e.gone:
                        await db.execute(delete(PushSubscription).where(PushSubscription.id == sub.id))
            if sent:
                a.delivered_push_at = now
                stats["push"] += 1
            else:
                stats["failed"] += 1
        a.payload = payload
    await db.commit()
    if any(stats.values()):
        log.info("alerts_delivered", **stats)
    return stats
