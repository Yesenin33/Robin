"""Adapter around the existing CRAWLSCAN engine (stdlib-only, sync).

CRAWLSCAN is imported as-is from CRAWLSCAN_PATH and called via CRAWLSCAN_ENTRY
("module:function", function(address) -> dict). The dict is mapped to ScanReport
here — this is the ONLY place that knows CRAWLSCAN's output keys.
"""
import importlib
import sys
from collections.abc import Callable
from typing import Any

from app.services.engine.base import ScanReport


def _get(d: Any, *paths: str, default: Any = None) -> Any:
    """First non-None value among dotted paths."""
    for path in paths:
        cur = d
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur is not None:
            return cur
    return default


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _pct(v: Any) -> float | None:
    """Accepts 0..1 or 0..100."""
    x = _f(v)
    if x is None:
        return None
    return x * 100 if 0 <= x <= 1 else x


def map_report(address: str, r: dict) -> ScanReport:
    operators = _get(r, "operators", "holders.operators", "clusters", default=[]) or []
    top_holders = _get(r, "top_holders", "holders.top", default=[]) or []
    top_pct = _pct(_get(r, "holders.top_operator_pct", "top_operator_pct", "top_holder_pct"))
    if top_pct is None and operators:
        top_pct = max((_pct(o.get("pct") or o.get("share")) or 0) for o in operators)
    return ScanReport(
        address=address,
        symbol=_get(r, "token.symbol", "symbol"),
        name=_get(r, "token.name", "name"),
        decimals=int(_get(r, "token.decimals", "decimals", default=18)),
        score=(int(s) if (s := _f(_get(r, "safety_score", "score", "safety.score"))) is not None else None),
        swarm_verdict=_get(r, "swarm.verdict", "swarm_verdict", "verdict"),
        holders_count=_get(r, "holders.count", "holders_count", "holder_count"),
        real_hands_pct=_pct(_get(r, "holders.real_hands", "real_hands_pct", "real_hands")),
        top_holder_pct=top_pct,
        top_holders=top_holders,
        operators=operators,
        price_usd=_f(_get(r, "market.price_usd", "market.priceUsd", "price_usd")),
        price_change_24h=_f(_get(r, "market.price_change_24h", "market.priceChange.h24", "price_change_24h")),
        liquidity_usd=_f(_get(r, "market.liquidity_usd", "market.liquidity.usd", "liquidity_usd")),
        fdv_usd=_f(_get(r, "market.fdv", "market.fdv_usd", "fdv")),
        pair_address=_get(r, "market.pair_address", "market.pairAddress", "pair_address"),
        honeypot=_get(r, "security.honeypot", "contract.honeypot", "is_honeypot"),
        can_mint=_get(r, "security.mintable", "contract.mintable", "can_mint"),
        owner_renounced=_get(r, "security.owner_renounced", "contract.owner_renounced"),
        buy_tax=_pct(_get(r, "security.buy_tax", "contract.buy_tax")),
        sell_tax=_pct(_get(r, "security.sell_tax", "contract.sell_tax")),
        blacklist=_get(r, "security.blacklist", "contract.blacklist"),
        proxy=_get(r, "security.proxy", "contract.proxy"),
        has_site=_get(r, "site.ok", "socials.site_ok", "site_ok"),
        has_twitter=_get(r, "twitter.ok", "socials.twitter_ok", "twitter_ok"),
        whale_labels=_get(r, "gmgn.labels", "whale_labels", default=[]) or [],
        raw=r,
    )


class CrawlscanEngine:
    name = "crawlscan"

    def __init__(self, path: str, entry: str):
        if path and path not in sys.path:
            sys.path.insert(0, path)
        module_name, func_name = entry.split(":")
        self._fn: Callable[[str], dict] = getattr(importlib.import_module(module_name), func_name)

    def scan(self, address: str) -> ScanReport:
        return map_report(address, self._fn(address))
