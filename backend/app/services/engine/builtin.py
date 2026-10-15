"""Offline/dev fallback: market data only (no RPC). Same normalized schema, same scoring."""
from app.services.engine.base import ScanReport, map_report
from app.services.intelligence import assess
from app.services.intelligence.config import get_chain
from app.services.intelligence.market import fetch_market
from app.services.intelligence.schema import empty_result


class BuiltinEngine:
    name = "builtin-market-only"

    def __init__(self, chain: str = "robinhood"):
        self.chain = chain

    def scan(self, address: str) -> ScanReport:
        r = empty_result(address.lower(), self.chain)
        r["engine"] = self.name
        m = fetch_market(get_chain(self.chain), r["address"])
        if m:
            r["sources"].append(m.pop("source"))
            r.update({k: v for k, v in m.items() if k in r})
        a = assess(r)
        r["safety_score"], r["verdict"], r["risks"] = a.score, a.verdict, a.risks
        return map_report(r)
