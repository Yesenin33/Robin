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
    # True when the holding is worth more than its pool can pay out: it counts towards nothing.
    unrealizable: bool = False


class PortfolioOut(BaseModel):
    address: str
    total_usd: float
    change_24h_usd: float
    change_24h_pct: float
    holdings: list[Holding]
    # paper value we deliberately kept OUT of total_usd because it can't be sold (see Holding.unrealizable)
    unrealizable_usd: float = 0.0
    updated_at: datetime
