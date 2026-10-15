from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, IdMixin, utcnow


class Position(IdMixin, Base):
    """Per-wallet per-token snapshot; time-series used for PnL and 24h change."""

    __tablename__ = "positions"
    __table_args__ = (Index("ix_positions_wallet_token_ts", "wallet_address", "token_address", "ts"),)

    wallet_address: Mapped[str] = mapped_column(String(42), index=True)
    token_address: Mapped[str] = mapped_column(String(42))  # "native" for gas token
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    amount: Mapped[float] = mapped_column(Numeric(48, 18))
    price_usd: Mapped[float | None] = mapped_column(Numeric(38, 18))
    value_usd: Mapped[float | None] = mapped_column(Numeric(24, 2))
    avg_cost_usd: Mapped[float | None] = mapped_column(Numeric(38, 18))
