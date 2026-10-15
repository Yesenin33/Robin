from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.redis import get_redis


async def rate_limit(request: Request, redis: Redis = Depends(get_redis)) -> None:
    """Fixed-window limiter: N requests / minute per client IP per route."""
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    key = f"rl:{request.url.path.split('/')[1]}:{ip}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count > get_settings().rate_limit_per_min:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, slow down.")
