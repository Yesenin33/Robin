"""Copy-trading: follow smart wallets, read what they do.

Feed rows come from `copy_events` (written by the worker); `following` marks the rows whose wallet
the current user follows. Subscribing is per-user and idempotent.
"""
from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.core.db import get_session
from app.core.ratelimit import rate_limit
from app.core.redis import get_redis
from app.jobs.copy_job import enqueue_backfill
from app.models import CopyTarget, SmartWallet, Token, User
from app.schemas.copy import CopyItemOut, CopyTargetOut, SubscribeIn, SuggestedOut
from app.services import copy_service, smart_wallets

router = APIRouter(prefix="/copy", tags=["copy"], dependencies=[Depends(rate_limit)])


async def _followed(db: AsyncSession, user_id: int) -> set[str]:
    rows = await db.execute(select(CopyTarget.address).where(CopyTarget.user_id == user_id, CopyTarget.active.is_(True)))
    return {a.lower() for (a,) in rows.all()}


@router.get("/feed", response_model=list[CopyItemOut])
async def feed(
    limit: int = Query(30, ge=1, le=100),
    only_followed: bool = Query(False, description="only wallets I follow"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    return await copy_service.feed(db, user.id, limit, only_followed)


@router.get("/suggested", response_model=list[SuggestedOut])
async def suggested(
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    """Wallets worth following, ranked from observed trades (realised PnL x hit rate)."""
    rows = (await db.execute(select(SmartWallet).order_by(SmartWallet.score.desc()).limit(limit))).scalars().all()
    followed = await _followed(db, user.id)
    tokens = {a for w in rows for a in (w.token_list or [])}
    ids = {t.address: t.id for t in (await db.execute(select(Token).where(Token.address.in_(tokens)))).scalars()}
    safety = await copy_service.latest_safety(db, list(ids.values()))
    out = []
    for w in rows:
        verdicts = [safety[ids[a]][0] for a in (w.token_list or []) if a in ids and ids[a] in safety]
        bad = sum(v == "DANGER" for v in verdicts) / len(verdicts) if verdicts else 0
        risky = sum(v != "CLEAN" for v in verdicts) / len(verdicts) if verdicts else 0.5
        tag = "high" if bad >= 0.3 else "medium" if risky >= 0.3 else "low"
        g = {"realised_pnl_usd": float(w.realised_pnl_usd), "wins": w.wins, "losses": w.losses,
             "tokens": w.tokens, "early_buys": w.early_buys}
        out.append(SuggestedOut(address=w.address, **g, reason=smart_wallets.describe(g), risk_tag=tag,
                                following=w.address in followed))
    return out


@router.get("/targets", response_model=list[CopyTargetOut])
async def targets(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = await db.execute(select(CopyTarget).where(CopyTarget.user_id == user.id, CopyTarget.active.is_(True)))
    return [CopyTargetOut(address=t.address, active=t.active) for t in rows.scalars().all()]


@router.post("/subscribe", response_model=CopyTargetOut)
async def subscribe(body: SubscribeIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_session),
                    redis: Redis = Depends(get_redis)):
    address = body.address.strip().lower()
    existing = await db.execute(select(CopyTarget).where(CopyTarget.user_id == user.id, CopyTarget.address == address))
    row = existing.scalar_one_or_none()
    if row is None:
        if not body.follow:
            return CopyTargetOut(address=address, active=False)
        row = CopyTarget(user_id=user.id, address=address, active=True)
        db.add(row)
    else:
        row.active = body.follow
    await db.commit()
    await db.refresh(row)
    queued = False
    if row.active:  # first follow of this wallet by anyone -> pull its history now
        queued = (await redis.get(f"copy:backfilled:{address}")) is None
        await enqueue_backfill(redis, address)
    return CopyTargetOut(address=row.address, active=row.active, backfill_queued=queued)


@router.delete("/subscribe", status_code=204)
async def unsubscribe(address: str = Query(min_length=42, max_length=42), user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_session)):
    await db.execute(delete(CopyTarget).where(CopyTarget.user_id == user.id, CopyTarget.address == address.strip().lower()))
    await db.commit()
