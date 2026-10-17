"""Fill tokens.image_url from GeckoTerminal — the source that actually HAS art for this chain.

DexScreener has none for most Robinhood-chain tokens (measured: 0 images across the owner's 47 holdings,
WETH included). GeckoTerminal indexes the network (`/networks/robinhood/tokens/...`), carries a real
`image_url` per token — branded art even for the junk airdrops — and answers up to 30 addresses per
request via `tokens/multi`, so the whole catalogue is ~135 calls instead of one per token.

Existing art is only replaced when GeckoTerminal has something: a token we already have a logo for is
not downgraded to nothing.
"""
import asyncio

import httpx
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.models import Token

MULTI = "https://api.geckoterminal.com/api/v2/networks/robinhood/tokens/multi/{addrs}"
BATCH = 30
PACE = 1.8  # stay inside GeckoTerminal's free rate limit (~30 calls/min)


async def main() -> None:
    async with SessionLocal() as db:
        rows = (await db.execute(select(Token.id, Token.address, Token.symbol, Token.image_url))).all()
    total = len(rows)
    print(f"tokens: {total} | already with art: {sum(1 for r in rows if r[3])}", flush=True)

    # the owner's own wallet first, so his screen fills before the bulk pass finishes
    owner = "0x26a1a7c87f72fe37ca57ab92f24353ff12b1110a"
    held: set[str] = set()
    try:
        import json as _json
        import urllib.request
        d = _json.load(urllib.request.urlopen(f"http://127.0.0.1:8000/portfolio/{owner}", timeout=120))
        held = {h["token"].lower() for h in d["holdings"]}
        held |= {h["token"].lower() for h in (d.get("excluded") or [])}
    except Exception as e:  # noqa: BLE001
        print("  (could not read the wallet first; running the plain order)", type(e).__name__, flush=True)

    rows.sort(key=lambda r: 0 if r[1].lower() in held else 1)
    filled = 0
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "Mozilla/5.0"}) as c:
        for i in range(0, total, BATCH):
            chunk = rows[i:i + BATCH]
            addrs = ",".join(r[1] for r in chunk)
            try:
                r = await c.get(MULTI.format(addrs=addrs))
                if r.status_code == 429:
                    print(f"  429 at {i}; waiting 30 s", flush=True)
                    await asyncio.sleep(30)
                    continue
                data = (r.json().get("data") or []) if r.status_code == 200 else []
            except Exception as e:  # noqa: BLE001
                print(f"  batch {i // BATCH}: {type(e).__name__} {str(e)[:70]}", flush=True)
                data = []
            got = {}
            for d in data:
                a = (d.get("attributes") or {}).get("address", "").lower()
                img = (d.get("attributes") or {}).get("image_url")
                if a and img:
                    got[a] = img
            if got:
                async with SessionLocal() as db:
                    for tid, addr, _sym, _img in chunk:
                        if addr.lower() in got:
                            await db.execute(update(Token).where(Token.id == tid).values(image_url=got[addr.lower()]))
                    await db.commit()
                filled += sum(1 for a in got if a in {x[1].lower() for x in chunk})
            if (i // BATCH) % 10 == 0:
                print(f"  {min(i + BATCH, total)}/{total} asked, {filled} filled", flush=True)
            await asyncio.sleep(PACE)
    print(f"DONE filled={filled}", flush=True)


asyncio.run(main())