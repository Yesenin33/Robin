from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, IdMixin, utcnow


class SmartWallet(IdMixin, Base):
    """Wallet ranked from observed on-chain trades (never hand-entered)."""

    __tablename__ = "smart_wallets"

    address: Mapped[str] = mapped_column(String(78), unique=True, index=True)
    score: Mapped[float] = mapped_column(Numeric(24, 8), default=0, index=True)
    realised_pnl_usd: Mapped[float] = mapped_column(Numeric(38, 18), default=0)
    invested_usd: Mapped[float] = mapped_column(Numeric(38, 18), default=0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    early_buys: Mapped[int] = mapped_column(Integer, default=0)
    last_block: Mapped[int | None] = mapped_column(BigId)
    token_list: Mapped[list | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
