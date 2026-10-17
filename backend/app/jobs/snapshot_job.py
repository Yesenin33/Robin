"""Snapshot every linked wallet's holdings into `positions` for PnL."""
from sqlalchemy import select

from app.api.deps import get_rpc
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models import Wallet
from app.services.market import MarketClient
from app.services.portfolio_service import PortfolioService

log = get_logger(__name__)


async def snapshot_positions(redis) -> int:
    async with SessionLocal() as db:
        addresses = sorted({a for (a,) in await db.execute(select(Wallet.address))})
    rows = 0
    for addr in addresses:
        async with SessionLocal() as db:
            try:
                rows += await PortfolioService(db, redis, get_rpc(), MarketClient(redis)).snapshot(addr)
            except Exception as e:  # noqa: BLE001
                log.error("snapshot_failed", address=addr, error=str(e))
    log.info("positions_snapshot", wallets=len(addresses), rows=rows)
    return rows
