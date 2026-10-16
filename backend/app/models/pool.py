from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, IdMixin, TimestampMixin


class Pool(IdMixin, TimestampMixin, Base):
    """Uniswap V4 pool (PoolManager Initialize): id -> its two currencies (ZERO = native ETH)."""

    __tablename__ = "pools"

    pool_id: Mapped[str] = mapped_column(String(78), unique=True, index=True)
    currency0: Mapped[str] = mapped_column(String(78))
    currency1: Mapped[str] = mapped_column(String(78))
    block: Mapped[int | None] = mapped_column(BigId)
    # the rest of the v4 PoolKey, from the same Initialize event (None until discovered)
    fee: Mapped[int | None] = mapped_column(Integer)
    tick_spacing: Mapped[int | None] = mapped_column(Integer)
    hooks: Mapped[str | None] = mapped_column(String(42))
