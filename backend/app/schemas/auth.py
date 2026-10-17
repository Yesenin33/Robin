from pydantic import BaseModel


class NonceOut(BaseModel):
    nonce: str


class SiweIn(BaseModel):
    message: str
    signature: str
    # one-time token from POST /auth/link-token: lets a browser session attach its wallet to the account
    # that issued it (Telegram Mini App -> "open in browser") without sharing a session
    link_token: str | None = None


class LinkTokenOut(BaseModel):
    token: str
    expires_in: int


class WalletOut(BaseModel):
    address: str
    kind: str


class UserOut(BaseModel):
    id: int
    plan: str
    wallets: list[WalletOut]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
