"""Copy feed: store followed wallets' trades and describe them in plain English."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import CopyEvent, CopyTarget, SafetyScore, SmartWallet, Token
from app.services.chain_index import Trade
from app.services.intelligence import contract
from app.services.intelligence.rpc import Rpc

RISK_TAG = {"CLEAN": "low", "RISKY": "medium", "DANGER": "high", "UNKNOWN": "medium"}
VERDICT_TEXT = {
    "CLEAN": "Our safety check says it looks healthy.",
    "RISKY": "Our safety check says proceed with care",
    "DANGER": "Our safety check flags it as very likely a scam",
    "UNKNOWN": "Our safety check couldn't get enough data on this token yet",
}


def _amount(v: float) -> str:
    if v >= 1e6:
        return f"{v / 1e6:,.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:,.1f}K"
    return f"{v:,.4g}"


def describe(side: str, token_amount: float | None, symbol: str, usd: float | None, eth: float | None,
             verdict: str | None, risks: list[str] | None) -> tuple[str, str]:
    """(reason, risk_tag) — the action, then what our latest safety check says about the token."""
    verb = "Bought" if side == "buy" else "Sold"
    what = f"{_amount(token_amount)} {symbol}" if token_amount else symbol
    price = f" for ${usd:,.2f}" if usd else f" for {eth:.4g} ETH" if eth else ""
    action = f"{verb} {what}{price}."
    if verdict is None:
        return f"{action} We haven't safety-checked this token yet.", "medium"
    note = VERDICT_TEXT[verdict]
    if verdict == "UNKNOWN":
        note += "."
    elif verdict != "CLEAN":
        note += f": {risks[0][0].lower() + risks[0][1:]}" if risks else "."
    return f"{action} {note}", RISK_TAG[verdict]


async def latest_safety(db: AsyncSession, token_ids: list[int]) -> dict[int, tuple[str, list[str]]]:
    if not token_ids:
        return {}
    last = (select(SafetyScore.token_id, func.max(SafetyScore.ts).label("ts"))
            .where(SafetyScore.token_id.in_(token_ids)).group_by(SafetyScore.token_id).subquery())
    rows = await db.execute(select(SafetyScore.token_id, SafetyScore.verdict, SafetyScore.risks).join(
        last, (SafetyScore.token_id == last.c.token_id) & (SafetyScore.ts == last.c.ts)))
    return {tid: (v, r or []) for tid, v, r in rows.all()}


async def ensure_tokens(db: AsyncSession, rpc: Rpc, addresses: set[str]) -> tuple[dict[str, Token], list[str]]:
    """Token rows for these addresses; unknown ones are created from on-chain metadata. Returns (map, new)."""
    have = {t.address: t for t in (await db.execute(select(Token).where(Token.address.in_(addresses)))).scalars()}
    new = [a for a in addresses if a not in have]
    for a in new:
        try:
            meta = await asyncio.to_thread(contract.read_meta, rpc, a)
        except Exception:  # noqa: BLE001 — still record the token, metadata can come with the scan
            meta = {"symbol": None, "name": None, "decimals": 18}
        tok = Token(address=a, symbol=meta["symbol"], name=meta["name"], decimals=meta["decimals"] or 18)
        db.add(tok)
        have[a] = tok
    if new:
        await db.flush()
    return have, new


async def store_trades(db: AsyncSession, trades: list[Trade], tokens: dict[str, Token]) -> list[CopyEvent]:
    """Insert CopyEvents (dedup on tx+token+wallet+side). Returns the newly inserted rows."""
    inserted: list[CopyEvent] = []
    for t in trades:
        tok = tokens.get(t.token)
        decimals = (tok.decimals if tok else 18) or 18
        amount = t.token_raw / 10**decimals
        eth = t.eth_wei / 1e18 if t.eth_wei is not None else None
        usd = t.usd if t.usd is not None else (
            amount * float(tok.price_usd) if tok is not None and tok.price_usd is not None else None)
        ev = CopyEvent(
            address=t.wallet, tx_hash=t.tx, side=t.side, token_address=t.token, block_number=t.block,
            ts=datetime.fromtimestamp(t.ts, timezone.utc) if t.ts else datetime.now(timezone.utc),
            token_amount=amount, eth_amount=eth, amount_usd=round(usd, 2) if usd is not None else None,
            quote_token=t.quote, quote_amount=t.quote_raw,
            meta={"symbol": tok.symbol if tok else None},
        )
        try:
            async with db.begin_nested():
                db.add(ev)
            inserted.append(ev)
        except IntegrityError:
            pass  # already recorded
    await db.commit()
    return inserted


async def followers(db: AsyncSession, wallet: str) -> list[int]:
    rows = await db.execute(select(CopyTarget.user_id).where(CopyTarget.address == wallet, CopyTarget.active.is_(True)))
    return [u for (u,) in rows.all()]


async def top_suggested(db: AsyncSession, n: int) -> list[str]:
    rows = await db.execute(select(SmartWallet.address).order_by(SmartWallet.score.desc()).limit(n))
    return [a for (a,) in rows.all()]


async def feed(db: AsyncSession, user_id: int, limit: int, only_followed: bool) -> list[dict]:
    """Followed wallets' moves. A user who follows nobody gets the newest moves of the top suggested wallets,
    so the Copy screen is never empty on a fresh account (`only_followed` opts out of that)."""
    followed = {a for (a,) in (await db.execute(select(CopyTarget.address).where(
        CopyTarget.user_id == user_id, CopyTarget.active.is_(True)))).all()}
    q = select(CopyEvent, Token).join(Token, Token.address == CopyEvent.token_address, isouter=True)
    if only_followed:
        if not followed:
            return []
        q = q.where(CopyEvent.address.in_(followed))
    elif not followed:
        if top := await top_suggested(db, get_settings().spectate_wallets):
            q = q.where(CopyEvent.address.in_(top))
    rows = (await db.execute(q.order_by(CopyEvent.ts.desc()).limit(limit))).all()
    safety = await latest_safety(db, [t.id for _, t in rows if t is not None])
    out = []
    for ev, tok in rows:
        symbol = (tok.symbol if tok else None) or (ev.meta or {}).get("symbol") or ev.token_address[:8]
        verdict, risks = safety.get(tok.id, (None, [])) if tok else (None, [])
        reason, tag = describe(ev.side, float(ev.token_amount) if ev.token_amount is not None else None, symbol,
                               float(ev.amount_usd) if ev.amount_usd is not None else None,
                               float(ev.eth_amount) if ev.eth_amount is not None else None, verdict, risks)
        out.append({
            "address": ev.address, "ts": ev.ts, "side": ev.side, "token_symbol": symbol,
            "token_address": ev.token_address, "tx_hash": ev.tx_hash,
            "amount_usd": float(ev.amount_usd) if ev.amount_usd is not None else None,
            "reason": reason, "risk_tag": tag, "verdict": verdict, "following": ev.address in followed,
        })
    return out
