from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import issue_jwt, new_token
from app.models import Subscription, User, Wallet
from app.schemas.auth import TokenOut, UserOut, WalletOut
from app.services import siwe


class AuthError(Exception):
    pass


class AuthService:
    """Wallet-only auth: the wallet IS the account. There is no e-mail path."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db, self.redis = db, redis
        self.s = get_settings()

    # ---- SIWE ----
    async def siwe_nonce(self) -> str:
        nonce = new_token(12).replace("-", "").replace("_", "")[:16]
        await self.redis.set(f"siwe:{nonce}", "1", ex=self.s.siwe_nonce_ttl_s)
        return nonce

    LINK_TTL = 600

    async def issue_link_token(self, user: User) -> str:
        token = new_token(24)
        await self.redis.set(f"link:{token}", str(user.id), ex=self.LINK_TTL)
        return token

    async def resolve_link_token(self, token: str) -> User:
        uid = await self.redis.getdel(f"link:{token}")  # single use
        user = await self.db.get(User, int(uid)) if uid else None
        if user is None:
            raise AuthError("This link has expired. Open Chainfolio in Telegram and try again.")
        return user

    async def siwe_login(self, message: str, signature: str, current_user: User | None = None,
                         link_token: str | None = None) -> TokenOut:
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

        if link_token:
            current_user = await self.resolve_link_token(link_token)
        address = parsed.address.lower()
        wallet = (await self.db.execute(select(Wallet).where(Wallet.address == address))).scalars().first()
        if wallet:
            user = await self.db.get(User, wallet.user_id)
            if current_user and current_user.id != user.id:
                # The wallet is the identity: its account wins. A Telegram chat that was linked to the
                # wallet-less account moves over, so alerts keep working and nothing is duplicated.
                if current_user.telegram_chat_id and not user.telegram_chat_id:
                    user.telegram_chat_id = current_user.telegram_chat_id
                    user.telegram_alerts = current_user.telegram_alerts
                    current_user.telegram_chat_id = None
        else:
            user = current_user or await self._create_user()
            self.db.add(Wallet(user_id=user.id, address=address, kind="siwe"))
            await self.db.flush()
        return await self._login(user)

    # ---- helpers ----
    async def _create_user(self) -> User:
        user = User()
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
        return UserOut(id=user.id, plan=sub.plan if sub else "free",
                       wallets=[WalletOut(address=w.address, kind=w.kind) for w in wallets])
