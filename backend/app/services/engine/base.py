from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ScanReport:
    """Normalized CRAWLSCAN output. Everything the API needs, nothing engine-specific."""

    address: str
    symbol: str | None = None
    name: str | None = None
    decimals: int = 18
    score: int | None = None  # 0-100, higher = safer
    swarm_verdict: str | None = None
    # holders
    holders_count: int | None = None
    real_hands_pct: float | None = None  # share of supply NOT held by bots / linked operators
    top_holder_pct: float | None = None  # largest independent operator, % of supply
    top_holders: list[dict] = field(default_factory=list)
    operators: list[dict] = field(default_factory=list)
    # market
    price_usd: float | None = None
    price_change_24h: float | None = None
    liquidity_usd: float | None = None
    fdv_usd: float | None = None
    pair_address: str | None = None
    # contract security
    honeypot: bool | None = None
    can_mint: bool | None = None
    owner_renounced: bool | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None
    blacklist: bool | None = None
    proxy: bool | None = None
    # off-chain
    has_site: bool | None = None
    has_twitter: bool | None = None
    whale_labels: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class ScanEngine(Protocol):
    name: str

    def scan(self, address: str) -> ScanReport:
        """Blocking scan (run in a thread)."""
        ...
