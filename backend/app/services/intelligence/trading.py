"""Buy/sell tax and honeypot signals from observed swaps (Transfer logs around the pool).

UniswapV2-style: a sell moves tokens seller -> pool; a taxed token also moves a fee
seller -> (token contract | fee wallet) in the same tx. A buy moves pool -> buyer, with a
fee leg pool -> fee receiver. Liquidity adds before the first buy are ignored.
"""
from collections import defaultdict
from statistics import median

from app.services.intelligence.holders import Transfer
from app.services.intelligence.rpc import ZERO

HONEYPOT_MIN_BUYS = 10


def analyze(transfers: list[Transfer], pair: str | None, token: str) -> dict:
    out = {"buy_tax": None, "sell_tax": None, "honeypot": None, "buys": 0, "sells": 0}
    if not pair:
        return out
    pair, token = pair.lower(), token.lower()
    by_tx: dict[str, list[Transfer]] = defaultdict(list)
    for t in transfers:
        by_tx[t.tx].append(t)

    # Tokens entering the pool before anyone has bought are liquidity provision, not sells.
    first_buy = min((t.block for t in transfers if t.frm == pair and t.to != ZERO), default=None)

    buy_taxes, sell_taxes = [], []
    for txs in by_tx.values():
        to_pool = [t for t in txs if t.to == pair and t.frm not in (pair, token, ZERO)]
        from_pool = [t for t in txs if t.frm == pair and t.to != ZERO]
        if to_pool and not from_pool and first_buy is not None and txs[0].block >= first_buy:  # sell
            seller = max(to_pool, key=lambda t: t.value).frm
            net = sum(t.value for t in to_pool if t.frm == seller)
            fee = sum(t.value for t in txs if t.frm == seller and t.to != pair)
            if fee >= net:
                # The "seller" forwarded at least as much as it paid the pool: it is a router / aggregator hop
                # (wrapped assets are routed through constantly), not a wallet paying a transfer fee.
                continue
            if net + fee:
                out["sells"] += 1
                sell_taxes.append(fee / (net + fee) * 100)
        elif from_pool and not to_pool:  # buy
            main = max(from_pool, key=lambda t: t.value)
            total = sum(t.value for t in from_pool)
            # fee taken on the way: buyer forwards part to token/fee wallet in same tx
            fwd = sum(t.value for t in txs if t.frm == main.to and t.to != pair)
            if fwd * 2 > main.value:
                fwd = 0  # the receiver passes most of it on: a router hop, not a fee taken from a buyer
            if total - main.value > main.value:
                continue  # several comparable payouts in one tx: separate trades, not one trade plus a fee
            if total:
                out["buys"] += 1
                buy_taxes.append((total - main.value + fwd) / total * 100)

    if buy_taxes:
        out["buy_tax"] = round(median(buy_taxes), 2)
    if sell_taxes:
        out["sell_tax"] = round(median(sell_taxes), 2)
    if out["sells"]:
        out["honeypot"] = False
    elif out["buys"] >= HONEYPOT_MIN_BUYS:
        out["honeypot"] = True  # lots of buyers, nobody managed to sell
    return out
