from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Position, SafetyScore, Token
from app.schemas.portfolio import Holding, PortfolioOut
from app.services.market import MarketClient
from app.services.rpc import RPCClient, norm
from app.services.verdict_guard import effective_verdict

NATIVE = "native"


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
            pairs = list(zip(priced, await self.rpc.erc20_balances(address, [t.address for t in priced])))
        else:
            pairs = [(by_addr[a], raw) for a, raw in held.items() if a in by_addr]

        avg_costs = await self._avg_costs(address)
        verdicts = await self._verdicts([t.id for t, _ in pairs])

        holdings: list[Holding] = []
        unrealizable = 0.0
        if native_raw:
            amt = native_raw / 1e18
            px = await self.market.native_price_usd(self.s.native_symbol)
            holdings.append(self._holding(NATIVE, self.s.native_symbol, "Ether", amt, px, None, avg_costs.get(NATIVE)))
        for t, raw in pairs:
            if not raw:
                continue
            amt = raw / 10 ** (t.decimals or 18)
            px = float(t.price_usd) if t.price_usd is not None else None
            chg = float(t.price_change_24h) if t.price_change_24h is not None else None
            h = self._holding(t.address, t.symbol or "?", t.name, amt, px, chg, avg_costs.get(t.address))
            h.verdict = verdicts.get(t.id)
            # Value you cannot take out is not value. A wallet holding 66 units of a token that calls
            # itself "ETH" in a $2 055 pool was shown a $161 609 balance it could never sell — the pool
            # itself caps what any holder can realize, and our own scan says a top-holder sell takes the
            # price to -100%. Keep the row (honest) but count it as nothing.
            liq = float(t.liquidity_usd) if t.liquidity_usd is not None else None
            if liq is not None and h.value_usd > liq:
                h.unrealizable = True
                unrealizable += h.value_usd
            holdings.append(h)

        holdings.sort(key=lambda h: h.value_usd, reverse=True)
        counted = [h for h in holdings if not h.unrealizable]
        total = sum(h.value_usd for h in counted)
        change = sum(change_from_pct(h.value_usd, h.change_24h_pct) for h in counted)
        prev = total - change
        return PortfolioOut(
            address=address, total_usd=round(total, 2), change_24h_usd=round(change, 2),
            change_24h_pct=round(change / prev * 100, 2) if prev > 0 else 0.0,
            holdings=holdings, unrealizable_usd=round(unrealizable, 2),
            updated_at=datetime.now(timezone.utc),
        )

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

    async def _verdicts(self, token_ids: list[int]) -> dict[int, str]:
        if not token_ids:
            return {}
        latest = (
            select(SafetyScore.token_id, func.max(SafetyScore.ts).label("ts"))
            .where(SafetyScore.token_id.in_(token_ids)).group_by(SafetyScore.token_id).subquery()
        )
        rows = await self.db.execute(
            select(SafetyScore.token_id, SafetyScore.verdict, SafetyScore.metrics).join(
                latest, (SafetyScore.token_id == latest.c.token_id) & (SafetyScore.ts == latest.c.ts)
            )
        )
        return {tid: effective_verdict(v, m) for tid, v, m in rows.all()}

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
