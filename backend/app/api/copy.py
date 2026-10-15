"""Copy-trading: follow smart wallets, read what they do.

Feed rows come from `copy_events` (written by the worker); `following` marks the rows whose wallet
the current user follows. Subscribing is per-user and idempotent.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.core.db import get_session
from app.core.ratelimit import rate_limit
from app.models import CopyEvent, CopyTarget, Token, User
from app.schemas.copy import CopyItemOut, CopyTargetOut, SubscribeIn

router = APIRouter(prefix="/copy", tags=["copy"], dependencies=[Depends(rate_limit)])


async def _followed(db: AsyncSession, user_id: int) -> set[str]:
    rows = await db.execute(select(CopyTarget.address).where(CopyTarget.user_id == user_id, CopyTarget.active.is_(True)))
    return {a.lower() for (a,) in rows.all()}


@router.get("/feed", response_model=list[CopyItemOut])
async def feed(
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_session),
):
    rows = await db.execute(
        select(CopyEvent, Token.symbol)
        .join(Token, Token.address == CopyEvent.token_address, isouter=True)
        .order_by(CopyEvent.ts.desc())
        .limit(limit)
    )
    followed = await _followed(db, user.id)
    out: list[CopyItemOut] = []
    for ev, symbol in rows.all():
        out.append(CopyItemOut(
            address=ev.address,
            ts=ev.ts,
            side=ev.side,
            token_symbol=symbol or ev.token_address[:8],
            amount_usd=float(ev.amount_usd) if ev.amount_usd is not None else None,
            reason=ev.reason or "",
            risk_tag=ev.risk_tag or "medium",
            following=ev.address.lower() in followed,
        ))
    return out


@router.get("/targets", response_model=list[CopyTargetOut])
async def targets(user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
    rows = await db.execute(select(CopyTarget).where(CopyTarget.user_id == user.id, CopyTarget.active.is_(True)))
    return [CopyTargetOut(address=t.address, active=t.active) for t in rows.scalars().all()]


@router.post("/subscribe", response_model=CopyTargetOut)
async def subscribe(body: SubscribeIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_session)):
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
    return CopyTargetOut(address=row.address, active=row.active)


@router.delete("/subscribe", status_code=204)
async def unsubscribe(address: str = Query(min_length=42, max_length=42), user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_session)):
    await db.execute(delete(CopyTarget).where(CopyTarget.user_id == user.id, CopyTarget.address == address.strip().lower()))
    await db.commit()
