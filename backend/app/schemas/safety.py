from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Metric(BaseModel):
    key: str
    label: str
    emoji: str
    value: str
    tone: Literal["ok", "warn", "bad"]
    hint: str


class SafetyOut(BaseModel):
    address: str
    symbol: str | None
    name: str | None
    score: int
    verdict: Literal["CLEAN", "RISKY", "DANGER"]
    swarm_verdict: str | None = None
    summary: str
    risks: list[str]
    metrics: list[Metric]
    buy_recommended: bool
    price_usd: float | None = None
    scanned_at: datetime
    # set when holder data could not be fetched this time and the last good snapshot is shown instead
    holders_stale_at: datetime | None = None
    # set when a requested fresh scan failed and the previous result is shown
    stale: bool = False


class ScanQueued(BaseModel):
    address: str
    queued: bool = True
