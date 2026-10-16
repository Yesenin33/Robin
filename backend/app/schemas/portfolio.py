from datetime import datetime

from pydantic import BaseModel


class Holding(BaseModel):
    token: str  # contract address or "native"
    symbol: str
    name: str | None
    amount: float
    price_usd: float | None
    value_usd: float
    change_24h_pct: float | None
    pnl_pct: float | None  # vs average entry price (from position snapshots)
    verdict: str | None = None  # last known safety verdict
    liquidity_usd: float | None = None  # depth of its pool; None = no market data found


class PortfolioOut(BaseModel):
    address: str
    total_usd: float
    change_24h_usd: float
    change_24h_pct: float
    holdings: list[Holding]
    # holdings worth more on paper than their pool can pay out: not listed, not counted - only how many
    excluded_count: int = 0
    updated_at: datetime
