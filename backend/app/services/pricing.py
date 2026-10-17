"""Value trades in USD from their quote leg (USDG = $1, WETH/native ETH = ETH price)."""
import asyncio
import json

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import Pool, Token
from app.services import chain_index
from app.services.chain_index import Trade
from app.services.intelligence import contract
from app.services.intelligence.config import get_chain
from app.services.intelligence.market import fetch_market
from app.services.intelligence.rpc import ZERO, Rpc
from app.services.market import MarketClient

log = get_logger(__name__)


def quote_kinds() -> dict[str, str]:
    """address -> "USD" | "ETH"."""
    s = get_settings()
    kinds = {ZERO: "ETH"}
    if s.weth_address:
        kinds[s.weth_address.lower()] = "ETH"
    for entry in s.quote_tokens.split(","):
        if "=" in entry:
            addr, kind = entry.split("=", 1)
            if kind.strip().upper() in ("USD", "ETH"):
                kinds[addr.strip().lower()] = kind.strip().upper()
    return kinds


async def eth_usd(redis: Redis) -> float | None:
    """Coinbase spot, else the WETH price on Robinhood Chain from DexScreener/GeckoTerminal."""
    if cached := await redis.get("px:eth"):
        return float(cached)
    price = await MarketClient(redis).native_price_usd(get_settings().native_symbol)
    if price is None and (weth := get_settings().weth_address):
        try:
            m = await asyncio.to_thread(fetch_market, get_chain("robinhood"), weth.lower())
            price = m["price_usd"] if m else None
        except Exception as e:  # noqa: BLE001
            log.warning("eth_price_unavailable", error=str(e))
    if price:
        await redis.set("px:eth", json.dumps(price), ex=60)
    return price


async def decimals_of(db: AsyncSession, rpc: Rpc, addrs: set[str]) -> dict[str, int]:
    out = {ZERO: 18}
    want = {a for a in addrs if a and a != ZERO}
    if want:
        rows = await db.execute(select(Token.address, Token.decimals).where(Token.address.in_(want)))
        out.update({a: d or 18 for a, d in rows.all()})
    for a in want - out.keys():
        try:
            out[a] = (await asyncio.to_thread(contract.read_meta, rpc, a))["decimals"] or 18
        except Exception:  # noqa: BLE001 — unknown decimals: leave the trade unvalued
            pass
    return out


def value_trades(trades: list[Trade], kinds: dict[str, str], decimals: dict[str, int], eth_price: float | None) -> None:
    for t in trades:
        kind = kinds.get(t.quote or "")
        if kind is None or t.quote_raw is None or t.quote not in decimals:
            t.usd = None  # unknown quote (e.g. a tokenized equity): excluded from ranking
            continue
        amount = t.quote_raw / 10 ** decimals[t.quote]
        t.usd = amount if kind == "USD" else amount * eth_price if eth_price else None


async def load_pools(db: AsyncSession) -> dict[str, tuple[str, str]]:
    return {p: (c0, c1) for p, c0, c1 in (await db.execute(select(Pool.pool_id, Pool.currency0, Pool.currency1))).all()}


async def save_pools(db: AsyncSession, new: dict[str, tuple[str, str]], blocks: dict[str, int] | None = None,
                     keys: dict[str, tuple[int | None, int | None, str | None]] | None = None) -> None:
    """Persist pools. `keys` = pool id -> (fee, tick spacing, hooks) so the swap never reads history logs."""
    if not new:
        return
    have = {p: row for p, row in ((r.pool_id, r) for r in (await db.execute(select(Pool).where(Pool.pool_id.in_(list(new))))).scalars())}
    for pid, (c0, c1) in new.items():
        fee, spacing, hooks = (keys or {}).get(pid, (None, None, None))
        row = have.get(pid)
        if row is None:
            db.add(Pool(pool_id=pid, currency0=c0, currency1=c1, block=(blocks or {}).get(pid), fee=fee,
                        tick_spacing=spacing, hooks=hooks))
        elif row.fee is None and fee is not None:  # backfill pools stored before the key parts were kept
            row.fee, row.tick_spacing, row.hooks = fee, spacing, hooks
    await db.commit()


async def enrich_and_value(session_factory, redis: Redis, rpc: Rpc, trades: list[Trade]) -> list[Trade]:
    """Receipts -> quote legs (pools resolved + persisted) -> USD values."""
    if not trades:
        return trades
    async with session_factory() as db:
        pools = await load_pools(db)
    known = set(pools)
    await asyncio.to_thread(chain_index.enrich, rpc, trades, get_settings().weth_address, pools)
    kinds = quote_kinds()
    async with session_factory() as db:
        await save_pools(db, {k: v for k, v in pools.items() if k not in known})
        decimals = await decimals_of(db, rpc, {t.quote for t in trades if t.quote in kinds})
    value_trades(trades, kinds, decimals, await eth_usd(redis))
    return trades
