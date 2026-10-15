from datetime import datetime

from pydantic import BaseModel, Field


class CopyItemOut(BaseModel):
    """One smart-wallet action, shaped for the Copy screen."""

    address: str
    ts: datetime
    side: str  # buy | sell
    token_symbol: str
    amount_usd: float | None = None
    reason: str = ""
    risk_tag: str = "medium"  # low | medium | high
    following: bool = False


class SubscribeIn(BaseModel):
    address: str = Field(min_length=42, max_length=42)
    follow: bool = True


class CopyTargetOut(BaseModel):
    address: str
    active: bool
