from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, IdMixin, TimestampMixin, utcnow


class Token(IdMixin, TimestampMixin, Base):
    __tablename__ = "tokens"

    address: Mapped[str] = mapped_column(String(42), unique=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(128))
    decimals: Mapped[int] = mapped_column(Integer, default=18)
    pair_address: Mapped[str | None] = mapped_column(String(42))
    price_usd: Mapped[float | None] = mapped_column(Numeric(38, 18))
    price_change_24h: Mapped[float | None] = mapped_column(Numeric(18, 6))
    liquidity_usd: Mapped[float | None] = mapped_column(Numeric(24, 2))
    last_indexed_block: Mapped[int | None] = mapped_column(BigId)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class HoldersSnapshot(IdMixin, Base):
    """Holder distribution for a token at a point in time (from intelligence scan or indexer)."""

    __tablename__ = "holders_snapshots"

    token_id: Mapped[int] = mapped_column(BigId, ForeignKey("tokens.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    holders_count: Mapped[int | None] = mapped_column(Integer)
    real_hands_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))
    top_holder_pct: Mapped[float | None] = mapped_column(Numeric(6, 2))
    # [{address, pct, operator_id, label}]
    top_holders: Mapped[list | None] = mapped_column(JSON)
    # operator clusters (linked wallets)
    operators: Mapped[list | None] = mapped_column(JSON)


class SafetyScore(IdMixin, Base):
    __tablename__ = "safety_scores"

    token_id: Mapped[int] = mapped_column(BigId, ForeignKey("tokens.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    score: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(16))  # CLEAN | RISKY | DANGER
    swarm_verdict: Mapped[str | None] = mapped_column(String(32))
    risks: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict | None] = mapped_column(JSON)
