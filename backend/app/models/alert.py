from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, IdMixin, TimestampMixin


class Alert(IdMixin, TimestampMixin, Base):
    __tablename__ = "alerts"

    user_id: Mapped[int] = mapped_column(BigId, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # safety_drop | copy_event | price
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(String(1024))
    payload: Mapped[dict | None] = mapped_column(JSON)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_tg_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
