"""Chainfolio token intelligence: holders, operators, contract checks, market data, safety score.

Stdlib-only. Public API:
    scan(address: str, chain: str = "robinhood") -> dict   # keys: schema.SCHEMA_KEYS
"""
from app.services.intelligence.config import configure
from app.services.intelligence.scan import ScanUnavailable, scan
from app.services.intelligence.schema import ENGINE_NAME, ENGINE_VERSION, SCHEMA_KEYS, ScanResult
from app.services.intelligence.scoring import assess

__all__ = ["ScanUnavailable", "configure", "ENGINE_NAME", "ENGINE_VERSION", "SCHEMA_KEYS", "ScanResult", "assess", "scan"]
