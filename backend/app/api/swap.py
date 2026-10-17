from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_rpc
from app.core.config import get_settings
from app.core.db import get_session
from app.core.logging import get_logger
from app.core.ratelimit import rate_limit
from app.core.redis import get_redis
from app.models import Token
from app.services import swap as swapsvc
from app.services.market import MarketClient
from app.services.rpc import ADDRESS_RE

log = get_logger(__name__)
router = APIRouter(prefix="/swap", tags=["swap"], dependencies=[Depends(rate_limit)])


class QuoteIn(BaseModel):
    token: str = Field(pattern=ADDRESS_RE.pattern)
    side: str = Field(pattern="^(buy|sell)$")
    amount: str = Field(max_length=40, description="Human units of what you pay, e.g. '0.05'")
    slippage_bps: int | None = None
    wallet: str = Field(pattern=ADDRESS_RE.pattern)
    pay_with: str = Field(default="ETH", pattern="^(ETH|USDG)$")


@router.get("/config")
async def swap_config():
    """What the app needs to decide whether to offer the in-app flow. Public addresses only - never a key."""
    s = get_settings()
    ready = s.swap_enabled and not swapsvc.addresses_missing(s)
    return {"enabled": ready, "chain_id": s.chain_id, "default_slippage_bps": s.swap_default_slippage_bps,
            "max_slippage_bps": s.swap_max_slippage_bps}


@router.post("/quote")
async def swap_quote(body: QuoteIn, db: AsyncSession = Depends(get_session), redis: Redis = Depends(get_redis)):
    s, rpc = get_settings(), get_rpc()
    token = body.token.lower()
    if not (s.swap_enabled and not swapsvc.addresses_missing(s)):  # decide before touching any network
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "In-app swap isn't available yet.")
    try:
        row = (await db.execute(select(Token).where(Token.address == token))).scalar_one_or_none()
        market = dict((await MarketClient(redis).tokens_market([token])).get(token) or {})
        if row is not None and row.pair_address and not market.get("pool_id"):
            market["pool_id"] = row.pair_address.lower()  # our own record when DexScreener has none
        if row and row.decimals is not None:
            meta = {"symbol": row.symbol, "decimals": row.decimals}
        else:
            meta = (await rpc.erc20_metas([token]))[token]
            if meta.get("decimals") is None:
                raise swapsvc.SwapError("We couldn't read this token's contract, so we won't build a trade for it.")
        eth_usd = await MarketClient(redis).native_price_usd(s.native_symbol)
        return await swapsvc.quote(rpc, redis, s, swapsvc.QuoteRequest(**body.model_dump()), meta, market, eth_usd, db,
                                 row.pool_init_block if row else None)
    except swapsvc.NoRoute as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "We couldn't find a pool to quote this trade.") from e
    except swapsvc.SwapError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except Exception as e:  # noqa: BLE001 — RPC trouble: no quote, never a guessed one
        log.warning("swap_quote_failed", token=token, error=str(e))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "We couldn't get a live quote right now.") from e
