"""The ONE definition of the scan() output. Every consumer maps from these keys."""
from typing import Any, TypedDict

ENGINE_NAME = "chainfolio-intelligence"
ENGINE_VERSION = "1.0"


class HolderEntry(TypedDict):
    address: str
    pct: float  # % of total supply
    operator_id: int | None
    label: str | None  # pool | burn | contract | sniper | cluster | None
    is_contract: bool | None


class Operator(TypedDict):
    id: int
    wallets: list[str]
    pct: float  # % of total supply held by all wallets of this operator
    label: str  # single | cluster | sniper


class ScanResult(TypedDict):
    # identity
    address: str
    chain: str
    symbol: str | None
    name: str | None
    decimals: int
    total_supply: float | None
    # assessment (filled by scoring.assess)
    safety_score: int | None  # None when verdict is UNKNOWN
    verdict: str  # CLEAN | RISKY | DANGER | UNKNOWN
    risks: list[str]
    # holders
    holders_count: int | None
    holders_count_exact: bool
    real_hands_pct: float | None
    top_holder_pct: float | None
    top_holders: list[HolderEntry]
    operators: list[Operator]
    # market
    price_usd: float | None
    price_change_24h: float | None
    liquidity_usd: float | None
    fdv: float | None
    pair_address: str | None
    # contract security
    honeypot: bool | None
    can_mint: bool | None
    owner_renounced: bool | None
    buy_tax: float | None
    sell_tax: float | None
    blacklist: bool | None
    proxy: bool | None
    # off-chain
    has_site: bool | None
    has_twitter: bool | None
    whale_labels: list[str]
    # provenance
    engine: str
    engine_version: str
    sources: list[str]
    errors: list[str]


SCHEMA_KEYS: tuple[str, ...] = tuple(ScanResult.__annotations__)


def empty_result(address: str, chain: str) -> dict[str, Any]:
    return {
        "address": address, "chain": chain, "symbol": None, "name": None, "decimals": 18, "total_supply": None,
        "safety_score": None, "verdict": "UNKNOWN", "risks": [],
        "holders_count": None, "holders_count_exact": False, "real_hands_pct": None, "top_holder_pct": None,
        "top_holders": [], "operators": [],
        "price_usd": None, "price_change_24h": None, "liquidity_usd": None, "fdv": None, "pair_address": None,
        "honeypot": None, "can_mint": None, "owner_renounced": None, "buy_tax": None, "sell_tax": None,
        "blacklist": None, "proxy": None,
        "has_site": None, "has_twitter": None, "whale_labels": [],
        "engine": ENGINE_NAME, "engine_version": ENGINE_VERSION, "sources": [], "errors": [],
    }
