from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


NO_VALUE = "—"
NO_DATA_HINT = "We couldn't check this"


class Metric(BaseModel):
    key: str
    label: str
    emoji: str
    value: str
    tone: Literal["ok", "warn", "bad", "unknown"]  # unknown = we have no value: shown neutral, never green
    hint: str

    @model_validator(mode="after")
    def _unknown_is_neutral(self) -> "Metric":
        """A metric without a value is neutral and says we couldn't check it - whatever produced it (an older
        stored row, a cache entry, a new code path). It can never carry an ok/warn/bad tone or a reassuring hint."""
        if self.value == NO_VALUE:
            self.tone, self.hint = "unknown", NO_DATA_HINT
        return self


class SafetyOut(BaseModel):
    address: str
    symbol: str | None
    name: str | None
    image_url: str | None = None
    score: int | None  # None when verdict is UNKNOWN
    verdict: Literal["CLEAN", "RISKY", "DANGER", "UNKNOWN"]
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
    # set when this is the previous result and a fresh check is running in the background
    refreshing: bool = False


class ScanQueued(BaseModel):
    address: str
    queued: bool = True
    # queued = waiting for a worker, running = a worker is scanning it now
    state: Literal["queued", "running"] = "queued"
    retry_after_s: int = 3
    stage: str | None = None  # contract | market | transfers | holders | scoring: what the worker is doing now
    eta_s: int | None = None  # median of recent scans, None until we have measured some
