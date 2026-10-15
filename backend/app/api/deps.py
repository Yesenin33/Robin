from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.redis import get_redis
from app.core.security import decode_jwt
from app.models import User
from app.services.engine import get_engine
from app.services.market import MarketClient
from app.services.portfolio_service import PortfolioService
from app.services.rpc import RPCClient
from app.services.safety_service import SafetyService

bearer = HTTPBearer(auto_error=False)
_rpc: RPCClient | None = None


def get_rpc() -> RPCClient:
    global _rpc
    if _rpc is None:
        _rpc = RPCClient()
    return _rpc


async def optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: AsyncSession = Depends(get_session)
) -> User | None:
    if not creds:
        return None
    try:
        user_id = decode_jwt(creds.credentials)
    except Exception:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None
    return await db.get(User, user_id)


async def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


def safety_service(db: AsyncSession = Depends(get_session), redis: Redis = Depends(get_redis)) -> SafetyService:
    return SafetyService(db, redis, get_engine())


def portfolio_service(db: AsyncSession = Depends(get_session), redis: Redis = Depends(get_redis)) -> PortfolioService:
    return PortfolioService(db, redis, get_rpc(), MarketClient(redis))
