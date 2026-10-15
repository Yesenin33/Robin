from pydantic import BaseModel, EmailStr


class MagicLinkIn(BaseModel):
    email: EmailStr


class MagicLinkOut(BaseModel):
    sent: bool = True


class NonceOut(BaseModel):
    nonce: str


class SiweIn(BaseModel):
    message: str
    signature: str


class WalletOut(BaseModel):
    address: str
    kind: str


class UserOut(BaseModel):
    id: int
    email: str | None
    plan: str
    wallets: list[WalletOut]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
