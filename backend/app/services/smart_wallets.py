"""Rank wallets by what they actually did on-chain: realised PnL in ETH over closed positions.

Inputs are enriched Trades (eth_wei known). Nothing here is hand-curated.
"""
from collections import defaultdict
from dataclasses import dataclass, field

from app.services.chain_index import Trade

EARLY_BLOCKS = 300  # "early buyer" = bought within this many blocks of pool creation
WEI = 10**18


@dataclass
class WalletStats:
    address: str
    realised_pnl_eth: float = 0.0
    invested_eth: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    tokens: list[str] = field(default_factory=list)
    early_buys: int = 0
    last_block: int = 0

    @property
    def closed(self) -> int:
        return self.wins + self.losses

    @property
    def score(self) -> float:
        """PnL weighted by hit rate, so one lucky trade doesn't top the list."""
        return self.realised_pnl_eth * (self.wins / self.closed if self.closed else 0)


def rank(trades: list[Trade], pool_init: dict[str, int] | None = None, min_closed: int = 2,
         exclude: set[str] | None = None) -> list[WalletStats]:
    pool_init = pool_init or {}
    exclude = exclude or set()
    pos: dict[tuple[str, str], dict] = defaultdict(lambda: {"b": 0, "s": 0, "in": 0, "out": 0, "first_buy": None})
    stats: dict[str, WalletStats] = {}
    for t in trades:
        if t.wallet in exclude or t.eth_wei is None:
            continue
        p = pos[(t.wallet, t.token)]
        st = stats.setdefault(t.wallet, WalletStats(t.wallet))
        st.trades += 1
        st.last_block = max(st.last_block, t.block)
        if t.side == "buy":
            p["b"] += t.token_raw
            p["in"] += t.eth_wei
            if p["first_buy"] is None:
                p["first_buy"] = t.block
        else:
            p["s"] += t.token_raw
            p["out"] += t.eth_wei

    for (wallet, token), p in pos.items():
        st = stats[wallet]
        if token not in st.tokens:
            st.tokens.append(token)
        st.invested_eth += p["in"] / WEI
        init = pool_init.get(token)
        if init is not None and p["first_buy"] is not None and p["first_buy"] - init <= EARLY_BLOCKS:
            st.early_buys += 1
        if p["b"] and p["s"]:  # closed (fully or partly): realise the sold share's cost basis
            cost = p["in"] * min(1.0, p["s"] / p["b"])
            pnl = (p["out"] - cost) / WEI
            st.realised_pnl_eth += pnl
            if pnl > 0:
                st.wins += 1
            else:
                st.losses += 1

    ranked = [s for s in stats.values() if s.closed >= min_closed and s.realised_pnl_eth > 0]
    return sorted(ranked, key=lambda s: s.score, reverse=True)


def describe(s: WalletStats | dict) -> str:
    g = s if isinstance(s, dict) else {
        "realised_pnl_eth": s.realised_pnl_eth, "wins": s.wins, "losses": s.losses,
        "tokens": len(s.tokens), "early_buys": s.early_buys,
    }
    closed = g["wins"] + g["losses"]
    text = (f"Made {g['realised_pnl_eth']:+.2f} ETH in realised profit; "
            f"{g['wins']} of {closed} closed trades were wins across {g['tokens']} "
            f"token{'s' if g['tokens'] != 1 else ''}.")
    if g["early_buys"]:
        text += f" Bought early in {g['early_buys']} new token{'s' if g['early_buys'] != 1 else ''}."
    return text
