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


class PortfolioOut(BaseModel):
    address: str
    total_usd: float
    change_24h_usd: float
    change_24h_pct: float
    holdings: list[Holding]
    updated_at: datetime
