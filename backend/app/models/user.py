from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BigId, IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    wallets: Mapped[list["Wallet"]] = relationship(back_populates="user", lazy="selectin")


class Wallet(IdMixin, TimestampMixin, Base):
    """A public address linked to a user. Non-custodial: never a private key."""

    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("user_id", "address"),)

    user_id: Mapped[int] = mapped_column(BigId, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(42), index=True)
    # siwe | embedded (Web3Auth/Privy) | watch
    kind: Mapped[str] = mapped_column(String(16), default="siwe")
    label: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[User] = relationship(back_populates="wallets")
