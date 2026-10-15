"""Fallback engine when CRAWLSCAN isn't installed: DexScreener market data only.

Holder clustering / contract checks stay None -> safety service treats them as unknown.
"""
import json
import urllib.request

from app.services.engine.base import ScanReport

DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens/{ca}"


class BuiltinEngine:
    name = "builtin"

    def __init__(self, chain_slug: str = "robinhood"):
        self.chain_slug = chain_slug

    def _fetch(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "chainfolio/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return json.loads(resp.read())

    def scan(self, address: str) -> ScanReport:
        data = self._fetch(DEXSCREENER.format(ca=address))
        pairs = [p for p in data.get("pairs") or [] if p.get("chainId") == self.chain_slug] or data.get("pairs") or []
        report = ScanReport(address=address, raw={"dexscreener": data})
        if not pairs:
            return report
        p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
        base = p.get("baseToken") or {}
        report.symbol, report.name = base.get("symbol"), base.get("name")
        report.price_usd = float(p["priceUsd"]) if p.get("priceUsd") else None
        report.price_change_24h = (p.get("priceChange") or {}).get("h24")
        report.liquidity_usd = (p.get("liquidity") or {}).get("usd")
        report.fdv_usd = p.get("fdv")
        report.pair_address = p.get("pairAddress")
        info = p.get("info") or {}
        report.has_site = bool(info.get("websites"))
        report.has_twitter = any(s.get("type") == "twitter" for s in info.get("socials") or [])
        return report
