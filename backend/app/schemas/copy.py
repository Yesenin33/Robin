from datetime import datetime

from pydantic import BaseModel, Field


class CopyItemOut(BaseModel):
    """One smart-wallet action, shaped for the Copy screen."""

    address: str
    ts: datetime
    side: str  # buy | sell
    token_symbol: str
    token_address: str | None = None
    tx_hash: str | None = None
    amount_usd: float | None = None
    reason: str = ""
    risk_tag: str = "medium"  # low | medium | high
    verdict: str | None = None  # latest safety verdict of the token
    following: bool = False


class SubscribeIn(BaseModel):
    address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    follow: bool = True


class CopyTargetOut(BaseModel):
    address: str
    active: bool
    backfill_queued: bool = False


class SuggestedOut(BaseModel):
    address: str
    realised_pnl_usd: float
    wins: int
    losses: int
    tokens: int
    early_buys: int
    reason: str
    risk_tag: str
    following: bool
