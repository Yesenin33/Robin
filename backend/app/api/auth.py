from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, optional_user
from app.core.db import get_session
from app.core.ratelimit import rate_limit
from app.core.redis import get_redis
from app.models import User
from app.schemas.auth import MagicLinkIn, MagicLinkOut, NonceOut, SiweIn, TokenOut, UserOut
from app.services.auth_service import AuthError, AuthService

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(rate_limit)])


def svc(db: AsyncSession = Depends(get_session), redis: Redis = Depends(get_redis)) -> AuthService:
    return AuthService(db, redis)


@router.post("/magic-link", response_model=MagicLinkOut, status_code=status.HTTP_202_ACCEPTED)
async def magic_link(body: MagicLinkIn, auth: AuthService = Depends(svc)):
    await auth.send_magic_link(body.email)
    return MagicLinkOut()


@router.get("/verify", response_model=TokenOut)
async def verify(token: str = Query(min_length=16), auth: AuthService = Depends(svc)):
    try:
        return await auth.verify_magic_link(token)
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/siwe/nonce", response_model=NonceOut)
async def siwe_nonce(auth: AuthService = Depends(svc)):
    return NonceOut(nonce=await auth.siwe_nonce())


@router.post("/siwe", response_model=TokenOut)
async def siwe_login(body: SiweIn, auth: AuthService = Depends(svc), user: User | None = Depends(optional_user)):
    """Sign in with a wallet. If already signed in (bearer), the wallet is linked to that user."""
    try:
        return await auth.siwe_login(body.message, body.signature, user)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user), auth: AuthService = Depends(svc)):
    return await auth.user_out(user)
