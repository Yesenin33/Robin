from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, IdMixin, TimestampMixin, utcnow


class CopyTarget(IdMixin, TimestampMixin, Base):
    """A user following a smart wallet."""

    __tablename__ = "copy_targets"
    __table_args__ = (UniqueConstraint("user_id", "address"),)

    user_id: Mapped[int] = mapped_column(BigId, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(42), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CopyEvent(IdMixin, Base):
    __tablename__ = "copy_events"
    __table_args__ = (UniqueConstraint("tx_hash", "token_address", "address", "side"),)

    address: Mapped[str] = mapped_column(String(42), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # one tx can hold several swaps (multi-hop); unique per (tx, token, wallet)
    tx_hash: Mapped[str] = mapped_column(String(78))
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    token_address: Mapped[str] = mapped_column(String(42))
    amount_usd: Mapped[float | None] = mapped_column(Numeric(24, 2))
    token_amount: Mapped[float | None] = mapped_column(Numeric(48, 18))
    eth_amount: Mapped[float | None] = mapped_column(Numeric(38, 18))
    block_number: Mapped[int | None] = mapped_column(BigId)
    reason: Mapped[str | None] = mapped_column(String(512))
    risk_tag: Mapped[str | None] = mapped_column(String(16))  # low | medium | high
    meta: Mapped[dict | None] = mapped_column(JSON)
