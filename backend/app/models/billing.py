from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigId, IdMixin, TimestampMixin


class Subscription(IdMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"

    user_id: Mapped[int] = mapped_column(BigId, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    plan: Mapped[str] = mapped_column(String(16), default="free")  # free | plus
    status: Mapped[str] = mapped_column(String(16), default="active")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
