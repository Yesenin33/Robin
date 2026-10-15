"""Copy feed: turn followed wallets' on-chain trades into CopyEvent rows.

A followed wallet's transfer is a *trade* only when the counterparty is the pool (Uniswap V4
PoolManager on Robinhood Chain) — plain wallet-to-wallet moves are ignored. Rows are deduped by tx.
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models import CopyEvent, CopyTarget, SafetyScore, Token
from app.services.intelligence.rpc import TRANSFER_TOPIC, Rpc, to_int

log = get_logger(__name__)

# Uniswap V4 PoolManager on Robinhood Chain — the counterparty of every swap on a Pons/V4 pool
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"
LOOKBACK_BLOCKS = 3000
RISK = {"DANGER": "high", "RISKY": "medium", "CLEAN": "low"}


def _addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def _collect_sync(rpc_url: str, targets: list[dict]) -> list[dict]:
    """Read recent transfers for the given tokens and keep the followed wallets' swaps."""
    rpc = Rpc(rpc_url)
    head = rpc.block_number()
    start = max(0, head - LOOKBACK_BLOCKS)
    out: list[dict] = []
    for t in targets:
        logs = rpc.transfer_logs(t["address"], start, head, 9999)
        for lg in logs:
            topics = lg.get("topics") or []
            if len(topics) != 3:
                continue
            frm, to = _addr(topics[1]), _addr(topics[2])
            wallet = frm if frm in t["wallets"] else to if to in t["wallets"] else None
            if wallet is None:
                continue
            if frm == POOL_MANAGER and to == wallet:
                side = "buy"
            elif to == POOL_MANAGER and frm == wallet:
                side = "sell"
            else:
                continue  # wallet-to-wallet move, not a trade
            raw = to_int(lg.get("data"))
            out.append({"wallet": wallet, "side": side, "raw": raw, "tx": lg.get("transactionHash"),
                        "token": t, "ts": datetime.now(timezone.utc)})
    return out


async def collect(redis=None, batch_tokens: int = 15) -> int:
    """Scan recently seen tokens for the followed wallets' swaps; insert new CopyEvents."""
    s = get_settings()
    async with SessionLocal() as db:
        wallets = {a.lower() for (a,) in (await db.execute(
            select(CopyTarget.address).where(CopyTarget.active.is_(True)).distinct())).all()}
        if not wallets:
            return 0
        tokens = (await db.execute(select(Token).order_by(Token.updated_at.desc()).limit(batch_tokens))).scalars().all()
        targets, risk = [], {}
        for t in tokens:
            row = (await db.execute(select(SafetyScore.verdict)
                                    .where(SafetyScore.token_id == t.id)
                                    .order_by(SafetyScore.ts.desc()).limit(1))).first()
            risk[t.address] = row[0] if row else None
            targets.append({"address": t.address, "symbol": t.symbol or t.address[:8], "decimals": t.decimals,
                            "price": float(t.price_usd) if t.price_usd is not None else None, "wallets": wallets})

    if not targets:
        return 0

    try:
        found = await asyncio.to_thread(_collect_sync, s.rpc_url, targets)
    except Exception as e:  # noqa: BLE001
        log.error("copy_collect_failed", error=str(e))
        return 0

    inserted = 0
    async with SessionLocal() as db:
        for f in found:
            t = f["token"]
            amount = f["raw"] / 10 ** t["decimals"] if t["decimals"] else 0.0
            usd = round(amount * t["price"], 2) if t["price"] else None
            verdict = risk.get(t["address"])
            reason = (f"{'Bought' if f['side'] == 'buy' else 'Sold'} {amount:,.0f} ${t['symbol']}"
                      + (f" for ${usd:,.0f}" if usd else "")
                      + (" · flagged risky" if verdict == "DANGER" else ""))
            db.add(CopyEvent(address=f["wallet"], ts=f["ts"], tx_hash=f["tx"], side=f["side"],
                             token_address=t["address"], amount_usd=usd, reason=reason,
                             risk_tag=RISK.get(verdict, "medium"), meta={"symbol": t["symbol"]}))
            try:
                await db.commit()
                inserted += 1
            except IntegrityError:  # same tx already recorded
                await db.rollback()
    log.info("copy_collected", events=inserted)
    return inserted
