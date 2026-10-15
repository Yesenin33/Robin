from dataclasses import dataclass, field, fields
from typing import Protocol

from app.services.intelligence import SCHEMA_KEYS


@dataclass
class ScanReport:
    """Typed mirror of intelligence.schema.ScanResult (same keys, same meaning)."""

    address: str
    chain: str = "robinhood"
    symbol: str | None = None
    name: str | None = None
    decimals: int = 18
    total_supply: float | None = None
    safety_score: int = 0
    verdict: str = "DANGER"
    risks: list[str] = field(default_factory=list)
    holders_count: int | None = None
    holders_count_exact: bool = False
    real_hands_pct: float | None = None
    top_holder_pct: float | None = None
    top_holders: list[dict] = field(default_factory=list)
    operators: list[dict] = field(default_factory=list)
    price_usd: float | None = None
    price_change_24h: float | None = None
    liquidity_usd: float | None = None
    fdv: float | None = None
    pair_address: str | None = None
    honeypot: bool | None = None
    can_mint: bool | None = None
    owner_renounced: bool | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None
    blacklist: bool | None = None
    proxy: bool | None = None
    has_site: bool | None = None
    has_twitter: bool | None = None
    whale_labels: list[str] = field(default_factory=list)
    engine: str = ""
    engine_version: str = ""
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


REPORT_KEYS = tuple(f.name for f in fields(ScanReport) if f.name != "raw")
assert REPORT_KEYS == SCHEMA_KEYS, "ScanReport drifted from intelligence schema"


def map_report(result: dict) -> ScanReport:
    """The only place that knows engine keys: exact 1:1 copy of the normalized schema."""
    missing = [k for k in SCHEMA_KEYS if k not in result]
    if missing:
        raise KeyError(f"scan result missing keys: {missing}")
    return ScanReport(**{k: result[k] for k in SCHEMA_KEYS}, raw=result)


class ScanEngine(Protocol):
    name: str

    def scan(self, address: str) -> ScanReport:
        """Blocking scan (run in a thread)."""
        ...
