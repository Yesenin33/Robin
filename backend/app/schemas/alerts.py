from datetime import datetime

from pydantic import BaseModel, Field


class AlertOut(BaseModel):
    id: int
    kind: str
    title: str
    body: str
    url: str | None = None
    created_at: datetime
    read: bool
    delivered_push: bool
    delivered_telegram: bool


class AlertsOut(BaseModel):
    unread: int
    items: list[AlertOut]


class PushKeyOut(BaseModel):
    public_key: str


class PushSubIn(BaseModel):
    endpoint: str = Field(max_length=1024)
    keys: dict[str, str]  # {"p256dh": ..., "auth": ...}


class TelegramStartIn(BaseModel):
    chat_id: str = Field(pattern=r"^-?\d{3,20}$")


class TelegramVerifyIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class ChannelsOut(BaseModel):
    push: bool
    telegram: bool
    telegram_bot: str | None = None
