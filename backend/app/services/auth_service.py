from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import issue_jwt, new_token, sha256
from app.models import Subscription, User, Wallet
from app.schemas.auth import TokenOut, UserOut, WalletOut
from app.services import siwe
from app.services.mailer import send_mail


class AuthError(Exception):
    pass


class AuthService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.db, self.redis = db, redis
        self.s = get_settings()

    # ---- magic link ----
    async def send_magic_link(self, email: str) -> None:
        token = new_token()
        await self.redis.set(f"ml:{sha256(token)}", email.lower(), ex=self.s.magic_link_ttl_s)
        link = f"{self.s.frontend_url}/auth/callback?token={token}"
        await send_mail(email, "Your Chainfolio sign-in link",
                        f"Tap to sign in:\n\n{link}\n\nThe link expires in {self.s.magic_link_ttl_s // 60} minutes.")

    async def verify_magic_link(self, token: str) -> TokenOut:
        email = await self.redis.getdel(f"ml:{sha256(token)}")  # single use
        if not email:
            raise AuthError("Link is invalid or expired")
        user = (await self.db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = await self._create_user(email=email)
        return await self._login(user)

    # ---- SIWE ----
    async def siwe_nonce(self) -> str:
        nonce = new_token(12).replace("-", "").replace("_", "")[:16]
        await self.redis.set(f"siwe:{nonce}", "1", ex=self.s.siwe_nonce_ttl_s)
        return nonce

    async def siwe_login(self, message: str, signature: str, current_user: User | None = None) -> TokenOut:
        try:
            parsed = siwe.parse(message)
        except siwe.SiweError as e:
            raise AuthError(str(e)) from e
        if not await self.redis.getdel(f"siwe:{parsed.nonce}"):
            raise AuthError("Unknown or used nonce")
        try:
            siwe.verify(message, signature, domain=self.s.siwe_domain, chain_id=self.s.chain_id, nonce=parsed.nonce)
        except siwe.SiweError as e:
            raise AuthError(str(e)) from e

        address = parsed.address.lower()
        wallet = (await self.db.execute(select(Wallet).where(Wallet.address == address))).scalars().first()
        if wallet:
            user = await self.db.get(User, wallet.user_id)
        else:
            user = current_user or await self._create_user()
            self.db.add(Wallet(user_id=user.id, address=address, kind="siwe"))
            await self.db.flush()
        return await self._login(user)

    # ---- helpers ----
    async def _create_user(self, email: str | None = None) -> User:
        user = User(email=email)
        self.db.add(user)
        await self.db.flush()
        self.db.add(Subscription(user_id=user.id, plan="free"))
        await self.db.flush()
        return user

    async def _login(self, user: User) -> TokenOut:
        user.last_login_at = datetime.now(timezone.utc)
        await self.db.commit()
        return TokenOut(access_token=issue_jwt(user.id), user=await self.user_out(user))

    async def user_out(self, user: User) -> UserOut:
        sub = (await self.db.execute(select(Subscription).where(Subscription.user_id == user.id))).scalar_one_or_none()
        wallets = (await self.db.execute(select(Wallet).where(Wallet.user_id == user.id))).scalars().all()
        return UserOut(id=user.id, email=user.email, plan=sub.plan if sub else "free",
                       wallets=[WalletOut(address=w.address, kind=w.kind) for w in wallets])
