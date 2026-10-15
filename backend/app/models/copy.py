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

    address: Mapped[str] = mapped_column(String(42), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    tx_hash: Mapped[str] = mapped_column(String(66), unique=True)
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    token_address: Mapped[str] = mapped_column(String(42))
    amount_usd: Mapped[float | None] = mapped_column(Numeric(24, 2))
    reason: Mapped[str | None] = mapped_column(String(512))
    risk_tag: Mapped[str | None] = mapped_column(String(16))  # low | medium | high
    meta: Mapped[dict | None] = mapped_column(JSON)
