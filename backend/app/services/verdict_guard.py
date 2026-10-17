"""Read-time check of stored verdicts: a row may only claim CLEAN/RISKY while the metrics behind it exist.

Rows written before the evidence bar (or by a partial scan) can carry an optimistic verdict next to dashes or no
metrics at all. Recomputing from the stored metrics needs no chain access, so every reader can apply it.
"""
from app.schemas.safety import NO_VALUE

REQUIRED = ("holders", "real_hands", "liquidity")


def missing_metrics(metrics: dict | None) -> list[str]:
    items = {m.get("key"): m for m in (metrics or {}).get("items") or []}
    return [k for k in REQUIRED if k not in items or items[k].get("value") in (None, "", NO_VALUE)]


def effective_verdict(verdict: str, metrics: dict | None) -> str:
    if verdict in ("CLEAN", "RISKY") and missing_metrics(metrics):
        return "UNKNOWN"
    return verdict
