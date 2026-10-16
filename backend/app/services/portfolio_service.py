import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import Position, SafetyScore, Token
from app.schemas.portfolio import Holding, PortfolioOut
from app.services.intelligence.scoring import unsellable
from app.services.market import MarketClient
from app.services.rpc import RPCClient, norm
from app.services.verdict_guard import effective_verdict

log = get_logger(__name__)
META_TIMEOUT_S = 4.0  # symbols are nice to have: never hold the page for them
NATIVE = "native"
SMALL_SHARE_OF_POOL = 0.25  # a position above this share of its own pool cannot be sold at the shown price


def change_from_pct(value: float, pct: float | None) -> float:
    """Absolute 24h change of a value that moved by pct%."""
    if pct is None or pct <= -100:
        return 0.0
    return value - value / (1 + pct / 100)


def next_avg_cost(prev_amount: float, prev_avg: float | None, amount: float, price: float | None) -> float | None:
    """Weighted average entry price; sells keep the average, buys blend it."""
    if price is None:
        return prev_avg
    if prev_avg is None or prev_amount <= 0:
        return price
    if amount <= prev_amount:
        return prev_avg
    return (prev_amount * prev_avg + (amount - prev_amount) * price) / amount


class PortfolioService:
    def __init__(self, db: AsyncSession, redis: Redis, rpc: RPCClient, market: MarketClient):
        self.db, self.redis, self.rpc, self.market = db, redis, rpc, market
        self.s = get_settings()

    async def get(self, address: str, use_cache: bool = True) -> PortfolioOut:
        address = norm(address)
        key = f"portfolio:{address}"
        if use_cache and (cached := await self.redis.get(key)):
            return PortfolioOut.model_validate_json(cached)
        out = await self._compute(address)
        await self.redis.set(key, out.model_dump_json(), ex=self.s.portfolio_cache_ttl_s)
        return out

    async def _compute(self, address: str) -> PortfolioOut:
        tokens = (await self.db.execute(select(Token))).scalars().all()
        by_addr = {t.address.lower(): t for t in tokens}
        native_raw = await self.rpc.native_balance(address)

        # Ask the chain what this wallet actually holds, once, instead of one balanceOf per known token.
        # The all-token scan is kept only as a fallback, and there it is bounded to tokens we can price.
        held = await self.rpc.held_erc20_balances(address)
        if held is None:
            priced = [t for t in tokens if t.price_usd is not None]
            held = {t.address: raw for t, raw in zip(priced, await self.rpc.erc20_balances(address, [t.address for t in priced])) if raw}
        pairs = await self._resolve(held, by_addr)

        avg_costs = await self._avg_costs(address)
        safety = await self._safety([t.id for t, _ in pairs if t.id is not None])

        holdings: list[Holding] = []
        excluded = 0
        left_out: list[Holding] = []
        unverified: list[str] = []
        if native_raw:
            amt = native_raw / 1e18
            px = await self.market.native_price_usd(self.s.native_symbol)
            holdings.append(self._holding(NATIVE, self.s.native_symbol, "Ether", amt, px, None, avg_costs.get(NATIVE)))
        for t, raw in pairs:
            if not raw:
                continue
            amt = raw / 10 ** (t.decimals if t.decimals is not None else 18)
            px = float(t.price_usd) if t.price_usd is not None else None
            chg = float(t.price_change_24h) if t.price_change_24h is not None else None
            h = self._holding(t.address, t.symbol or "?", t.name, amt, px, chg, avg_costs.get(t.address))
            h.image_url = getattr(t, "image_url", None) or None
            row = safety.get(t.id)
            h.verdict = effective_verdict(row[0], row[2]) if row else None
            h.liquidity_usd = float(t.liquidity_usd) if t.liquidity_usd is not None else None
            # Value you cannot take out is not value: a wallet holding 66 units of a token calling itself "ETH"
            # in a $2 055 pool was shown $161 609. The pool caps what any holder can realize, so such a row is
            # kept OUT of the list and the total - but it is still reported in `excluded`, because hiding a
            # balance the wallet really has is what makes the owner distrust the whole screen.
            if h.liquidity_usd is not None and h.value_usd > h.liquidity_usd:
                excluded += 1
                h.counted = False
                h.not_counted_reason = "Left out: its pool can't pay out this price"
                left_out.append(h)
                continue
            if t.id is None or row is None:
                unverified.append(t.address)
            h.not_counted_reason = self._not_counted(h, row)
            h.counted = h.not_counted_reason is None
            holdings.append(h)
        if unverified:
            await self._queue_checks(unverified)

        # The owner asked why this is not ordered "by balance": the sort key was value_usd, the very number
        # a not-counted row hides, so unverifiable junk with a fantasy price floated to the top. Order by
        # what the row is actually worth, and put the ones we cannot stand behind at the bottom, biggest
        # first among themselves.
        holdings.sort(key=lambda h: (0 if h.counted else 1, -h.value_usd))
        counted = [h for h in holdings if h.counted]
        total = sum(h.value_usd for h in counted)
        change = sum(change_from_pct(h.value_usd, h.change_24h_pct) for h in counted)
        prev = total - change
        return PortfolioOut(
            address=address, total_usd=round(total, 2), change_24h_usd=round(change, 2),
            change_24h_pct=round(change / prev * 100, 2) if prev > 0 else 0.0,
            holdings=holdings, excluded_count=excluded, excluded=left_out,
            not_counted_count=sum(1 for h in holdings if not h.counted and h.value_usd > 0),
            updated_at=datetime.now(timezone.utc),
        )

    async def _resolve(self, held: dict[str, int], by_addr: dict[str, Token]) -> list[tuple[Any, int]]:
        """A row for EVERY non-zero balance. Tokens we never indexed get symbol/name/decimals (one batch of
        eth_calls, cached) and price + pool depth (DexScreener, batched, cached). Each lookup is isolated: if
        it fails the token still shows, without a price."""
        unknown = [a for a in held if a not in by_addr]
        metas = await self._metas(unknown)
        need_price = unknown + [a for a, t in by_addr.items() if a in held and (t.price_usd is None or t.liquidity_usd is None or not t.image_url)]
        try:
            market = await self.market.tokens_market(need_price) if need_price else {}
        except Exception as e:  # noqa: BLE001
            log.warning("portfolio_market_failed", error=str(e))
            market = {}
        out: list[tuple[Any, int]] = []
        learned = False
        # Art for the held tokens we have none for. Fetched only for those (never one call per token) and
        # remembered, because a logo we have is a logo we never fetch again.
        gecko_art: dict[str, str] = {}
        need_art = [a for a in held if not (by_addr.get(a) is not None and by_addr[a].image_url)]
        if need_art:
            try:
                gecko_art = await self.market.gecko_images(need_art)
            except Exception as e:  # noqa: BLE001 — decoration: a failed lookup must not fail the page
                log.warning("portfolio_art_failed", error=str(e))
                gecko_art = {}
            for a, u in gecko_art.items():
                t = by_addr.get(a)
                if t is not None and not t.image_url:
                    t.image_url = u
                    learned = True
        for a, raw in held.items():
            m = market.get(a) or {}
            art = gecko_art.get(a)
            if a in by_addr:
                t = by_addr[a]
                if m.get("image_url") and not t.image_url:
                    t.image_url = m["image_url"]  # remember the art: no per-render fetch from now on
                    learned = True
                if (t.price_usd is None and m.get("price_usd") is not None) or (not t.image_url and (m.get("image_url") or art)):
                    t = SimpleNamespace(id=t.id, address=a, symbol=t.symbol, name=t.name, decimals=t.decimals,
                                        image_url=t.image_url or m.get("image_url") or art,
                                        price_usd=t.price_usd if t.price_usd is not None else m.get("price_usd"),
                                        price_change_24h=t.price_change_24h if t.price_change_24h is not None else m.get("price_change_24h"),
                                        liquidity_usd=t.liquidity_usd if t.liquidity_usd is not None else m.get("liquidity_usd"))
            else:
                meta = metas.get(a) or {}
                t = SimpleNamespace(id=None, address=a, symbol=meta.get("symbol") or m.get("symbol"),
                                    name=meta.get("name") or m.get("name"), decimals=meta.get("decimals"),
                                    image_url=m.get("image_url") or art,
                                    price_usd=m.get("price_usd") if meta.get("decimals") is not None else None,
                                    price_change_24h=m.get("price_change_24h"), liquidity_usd=m.get("liquidity_usd"))
            out.append((t, raw))
        if learned:
            await self.db.commit()
        return out

    async def _metas(self, addrs: list[str]) -> dict[str, dict]:
        if not addrs:
            return {}
        out: dict[str, dict] = {}
        try:
            cached = await self.redis.mget([f"tokmeta:{a}" for a in addrs])
            missing = []
            for a, c in zip(addrs, cached):
                if c is None:
                    missing.append(a)
                else:
                    out[a] = json.loads(c)
            if missing:
                for a, meta in (await asyncio.wait_for(self.rpc.erc20_metas(missing), META_TIMEOUT_S)).items():
                    out[a] = meta
                    if meta.get("decimals") is not None:  # a failed lookup is retried next time, not cached
                        await self.redis.set(f"tokmeta:{a}", json.dumps(meta), ex=7 * 86400)
        except Exception as e:  # noqa: BLE001 — the rows still show, with "?" and no price
            log.warning("portfolio_meta_failed", error=str(e))
        return out

    @staticmethod
    def _holding(token, symbol, name, amount, price, chg, avg) -> Holding:
        value = amount * price if price is not None else 0.0
        pnl = round((price - avg) / avg * 100, 2) if price is not None and avg else None
        return Holding(token=token, symbol=symbol, name=name, amount=amount, price_usd=price,
                       value_usd=round(value, 2), change_24h_pct=chg, pnl_pct=pnl)

    async def _avg_costs(self, address: str) -> dict[str, float]:
        latest = (
            select(Position.token_address, func.max(Position.ts).label("ts"))
            .where(Position.wallet_address == address).group_by(Position.token_address).subquery()
        )
        rows = await self.db.execute(
            select(Position.token_address, Position.avg_cost_usd).join(
                latest, (Position.token_address == latest.c.token_address) & (Position.ts == latest.c.ts)
            ).where(Position.wallet_address == address)
        )
        return {a: float(c) for a, c in rows if c is not None}

    async def _safety(self, token_ids: list[int]) -> dict[int, tuple[str, list[str], dict | None, datetime]]:
        """Latest stored check per token: (verdict, risk lines, metrics, when)."""
        if not token_ids:
            return {}
        latest = (
            select(SafetyScore.token_id, func.max(SafetyScore.ts).label("ts"))
            .where(SafetyScore.token_id.in_(token_ids)).group_by(SafetyScore.token_id).subquery()
        )
        rows = await self.db.execute(
            select(SafetyScore.token_id, SafetyScore.verdict, SafetyScore.risks, SafetyScore.metrics, SafetyScore.ts).join(
                latest, (SafetyScore.token_id == latest.c.token_id) & (SafetyScore.ts == latest.c.ts)
            )
        )
        return {tid: (v, r or [], m, ts) for tid, v, r, m, ts in rows.all()}

    def _not_counted(self, h: Holding, row) -> str | None:
        """Why a holding stays out of the headline total (None = it counts). The headline must trace to value a
        person could actually sell: missing/stale/UNKNOWN checks are unverified, never clean."""
        if h.token == NATIVE or h.value_usd <= 0:
            return None
        if row is None:
            return "Not checked yet"
        verdict, risks, metrics, ts = row
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - ts > timedelta(hours=self.s.portfolio_check_max_age_h):
            return "Check is out of date"
        verdict = effective_verdict(verdict, metrics)
        if verdict == "DANGER":
            return "Failed our safety check"
        if verdict == "UNKNOWN":
            return "We couldn't check it"
        if unsellable(risks):
            return "We can't confirm it can be sold"
        if h.liquidity_usd is not None and h.value_usd > h.liquidity_usd * SMALL_SHARE_OF_POOL:
            return "Too big for its pool"
        return None

    async def _queue_checks(self, addresses: list[str]) -> None:
        """Held tokens nobody has checked get a (bulk) scan, so the next load can vouch for them."""
        try:
            from app.jobs.scan_job import enqueue_scan

            for a in addresses[:50]:
                await enqueue_scan(self.redis, a)
        except Exception as e:  # noqa: BLE001 — a queue hiccup must never fail the portfolio
            log.warning("portfolio_queue_checks_failed", error=str(e))

    async def snapshot(self, address: str) -> int:
        """Persist current holdings as positions (for PnL). Returns rows written."""
        out = await self.get(address, use_cache=False)
        prev = await self._last_positions(out.address)
        now = datetime.now(timezone.utc)
        for h in out.holdings:
            p_amt, p_avg = prev.get(h.token, (0.0, None))
            self.db.add(Position(
                wallet_address=out.address, token_address=h.token, ts=now, amount=h.amount,
                price_usd=h.price_usd, value_usd=h.value_usd,
                avg_cost_usd=next_avg_cost(p_amt, p_avg, h.amount, h.price_usd),
            ))
        await self.db.commit()
        return len(out.holdings)

    async def _last_positions(self, address: str) -> dict[str, tuple[float, float | None]]:
        latest = (
            select(Position.token_address, func.max(Position.ts).label("ts"))
            .where(Position.wallet_address == address).group_by(Position.token_address).subquery()
        )
        rows = await self.db.execute(
            select(Position.token_address, Position.amount, Position.avg_cost_usd).join(
                latest, (Position.token_address == latest.c.token_address) & (Position.ts == latest.c.ts)
            ).where(Position.wallet_address == address)
        )
        return {a: (float(amt), float(avg) if avg is not None else None) for a, amt, avg in rows}
