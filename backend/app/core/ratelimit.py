from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import decode_jwt

log = get_logger(__name__)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


async def _hit(redis: Redis, key: str, limit: int) -> bool:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    return count <= limit


async def rate_limit(request: Request, redis: Redis = Depends(get_redis)) -> None:
    """Fixed 60s windows: per IP per route group, and per signed-in user across routes.

    Fails open if Redis is unavailable — a cache outage must not take the API down.
    """
    s = get_settings()
    group = request.url.path.split("/")[1]
    try:
        if not await _hit(redis, f"rl:{group}:{client_ip(request)}", s.rate_limit_per_min):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, slow down.",
                                headers={"Retry-After": "60"})
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                uid = decode_jwt(auth[7:])
            except Exception:  # noqa: BLE001 — invalid tokens are rejected later by auth deps
                return
            if not await _hit(redis, f"rl:user:{uid}", s.rate_limit_user_per_min):
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, slow down.",
                                    headers={"Retry-After": "60"})
    except RedisError as e:
        log.warning("rate_limit_redis_unavailable", error=str(e))
