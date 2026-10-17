"""Seed `tokens` from Robinhood Chain and queue safety scans.

Usage:
  python -m scripts.bootstrap_indexer --tokens 0xabc... 0xdef...
  python -m scripts.bootstrap_indexer --discover-blocks 5000 --top 50
"""
import argparse
import asyncio
from collections import Counter

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.core.redis import get_redis
from app.jobs.scan_job import drain_queue, enqueue_scan
from app.models import Token
from app.services.rpc import RPCClient, norm

log = get_logger("bootstrap")


async def discover(rpc: RPCClient, blocks: int, top: int, step: int = 500) -> list[str]:
    """Most active ERC-20s by Transfer count over the last `blocks` blocks."""
    from app.services.rpc import TRANSFER_TOPIC

    head = await rpc.block_number()
    counts: Counter[str] = Counter()
    for start in range(max(0, head - blocks), head + 1, step):
        logs = await rpc.call("eth_getLogs", [{"topics": [TRANSFER_TOPIC], "fromBlock": hex(start),
                                               "toBlock": hex(min(start + step - 1, head))}]) or []
        counts.update(lg["address"].lower() for lg in logs if len(lg.get("topics", [])) == 3)  # ERC-20 only
    return [a for a, _ in counts.most_common(top)]


async def main(tokens: list[str], discover_blocks: int, top: int, scan: bool) -> None:
    setup_logging()
    rpc = RPCClient()
    addrs = [norm(t) for t in tokens]
    if discover_blocks:
        addrs += await discover(rpc, discover_blocks, top)
    addrs = list(dict.fromkeys(addrs))
    redis = get_redis()

    async with SessionLocal() as db:
        for a in addrs:
            meta = await rpc.erc20_meta(a)
            token = (await db.execute(select(Token).where(Token.address == a))).scalar_one_or_none()
            if token is None:
                token = Token(address=a)
                db.add(token)
            token.symbol, token.name, token.decimals = meta["symbol"], meta["name"], meta["decimals"]
            log.info("token_seeded", address=a, symbol=meta["symbol"])
        await db.commit()

    for a in addrs:
        await enqueue_scan(redis, a)
    if scan:
        await drain_queue(redis, max_items=len(addrs))
    await rpc.aclose()
    log.info("bootstrap_done", tokens=len(addrs))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tokens", nargs="*", default=[])
    p.add_argument("--discover-blocks", type=int, default=0)
    p.add_argument("--top", type=int, default=50)
    p.add_argument("--scan", action="store_true", help="run scans now instead of leaving them to the worker")
    a = p.parse_args()
    asyncio.run(main(a.tokens, a.discover_blocks, a.top, a.scan))
