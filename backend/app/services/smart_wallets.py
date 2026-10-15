"""Rank wallets by what they actually did on-chain: realised PnL in USD over closed positions.

Inputs are enriched + valued Trades (`usd` known). Robinhood Chain pools quote mostly in USDG,
then WETH, so USD is the common unit. Trades without a USD value are skipped. Nothing is hand-curated.
"""
from collections import defaultdict
from dataclasses import dataclass, field

from app.services.chain_index import Trade

EARLY_BLOCKS = 300  # "early buyer" = bought within this many blocks of pool creation


@dataclass
class WalletStats:
    address: str
    realised_pnl_usd: float = 0.0
    invested_usd: float = 0.0
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
        return self.realised_pnl_usd * (self.wins / self.closed if self.closed else 0)


def rank(trades: list[Trade], pool_init: dict[str, int] | None = None, min_closed: int = 2,
         exclude: set[str] | None = None) -> list[WalletStats]:
    pool_init = pool_init or {}
    exclude = exclude or set()
    pos: dict[tuple[str, str], dict] = defaultdict(lambda: {"b": 0, "s": 0, "in": 0, "out": 0, "first_buy": None})
    stats: dict[str, WalletStats] = {}
    for t in trades:
        if t.wallet in exclude or t.usd is None:
            continue
        p = pos[(t.wallet, t.token)]
        st = stats.setdefault(t.wallet, WalletStats(t.wallet))
        st.trades += 1
        st.last_block = max(st.last_block, t.block)
        if t.side == "buy":
            p["b"] += t.token_raw
            p["in"] += t.usd
            if p["first_buy"] is None:
                p["first_buy"] = t.block
        else:
            p["s"] += t.token_raw
            p["out"] += t.usd

    for (wallet, token), p in pos.items():
        st = stats[wallet]
        if token not in st.tokens:
            st.tokens.append(token)
        st.invested_usd += p["in"]
        init = pool_init.get(token)
        if init is not None and p["first_buy"] is not None and p["first_buy"] - init <= EARLY_BLOCKS:
            st.early_buys += 1
        if p["b"] and p["s"]:  # closed (fully or partly): realise the sold share's cost basis
            cost = p["in"] * min(1.0, p["s"] / p["b"])
            pnl = p["out"] - cost
            st.realised_pnl_usd += pnl
            if pnl > 0:
                st.wins += 1
            else:
                st.losses += 1

    ranked = [s for s in stats.values() if s.closed >= min_closed and s.realised_pnl_usd > 0]
    return sorted(ranked, key=lambda s: s.score, reverse=True)


def rank_tiers(trades: list[Trade], pool_init: dict[str, int] | None, strong_min: int, fill_to: int,
               exclude: set[str] | None = None) -> list[WalletStats]:
    """Wallets with >= strong_min closed trades first; if that leaves fewer than `fill_to`, top up with
    wallets that have a single closed winning trade (the reason text says "1 of 1", so nothing is oversold)."""
    strong = rank(trades, pool_init, strong_min, exclude)
    if len(strong) >= fill_to:
        return strong
    seen = {w.address for w in strong}
    weak = [w for w in rank(trades, pool_init, 1, exclude) if w.address not in seen]
    return strong + weak[: fill_to - len(strong)]


def describe(s: WalletStats | dict) -> str:
    g = s if isinstance(s, dict) else {
        "realised_pnl_usd": s.realised_pnl_usd, "wins": s.wins, "losses": s.losses,
        "tokens": len(s.tokens), "early_buys": s.early_buys,
    }
    closed = g["wins"] + g["losses"]
    pnl = g["realised_pnl_usd"]
    text = (f"Made {'+' if pnl >= 0 else '-'}${abs(pnl):,.2f} in realised profit; "
            f"{g['wins']} of {closed} closed trades were wins across {g['tokens']} "
            f"token{'s' if g['tokens'] != 1 else ''}.")
    if g["early_buys"]:
        text += f" Bought early in {g['early_buys']} new token{'s' if g['early_buys'] != 1 else ''}."
    return text
