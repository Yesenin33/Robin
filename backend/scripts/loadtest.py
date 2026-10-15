"""Tiny async load test: python -m scripts.loadtest http://localhost:8000/health -c 50 -n 2000 [--spoof-ips]

Prints throughput, latency percentiles and the status-code mix (429s show the rate limiter working).
"""
import argparse
import asyncio
import random
import statistics
import time
from collections import Counter

import httpx


async def run(url: str, concurrency: int, total: int, spoof: bool, headers: dict) -> None:
    lat: list[float] = []
    codes: Counter[int | str] = Counter()
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(total):
        queue.put_nowait(i)

    async def worker(client: httpx.AsyncClient) -> None:
        while not queue.empty():
            queue.get_nowait()
            h = dict(headers)
            if spoof:  # many distinct clients (as Caddy would forward them)
                h["x-forwarded-for"] = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
            t0 = time.perf_counter()
            try:
                r = await client.get(url, headers=h)
                codes[r.status_code] += 1
            except httpx.HTTPError as e:
                codes[type(e).__name__] += 1
            lat.append((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=concurrency)) as client:
        await asyncio.gather(*(worker(client) for _ in range(concurrency)))
    wall = time.perf_counter() - t0
    q = statistics.quantiles(lat, n=100)
    print(f"{url}  c={concurrency} n={total}")
    print(f"  {total / wall:,.0f} req/s   p50 {q[49]:.1f} ms   p95 {q[94]:.1f} ms   p99 {q[98]:.1f} ms   max {max(lat):.1f} ms")
    print(f"  status: {dict(sorted(codes.items(), key=str))}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("-c", type=int, default=50)
    p.add_argument("-n", type=int, default=2000)
    p.add_argument("--spoof-ips", action="store_true")
    p.add_argument("-H", action="append", default=[], help="header 'Name: value'")
    a = p.parse_args()
    hdrs = dict(h.split(": ", 1) for h in a.H)
    asyncio.run(run(a.url, a.c, a.n, a.spoof_ips, hdrs))
