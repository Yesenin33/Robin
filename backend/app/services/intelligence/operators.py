"""Group holder wallets into "operators" (one actor, many wallets) and find snipers.

Links (union-find):
  1. wallet -> wallet token transfers (neither side is the pool)
  2. one sender fanning tokens out to >=3 holders in a single tx (split / airdrop)
  3. snipers buying from the pool in the same block during the first SNIPE_BLOCKS blocks
"""
from collections import defaultdict

from app.services.intelligence.holders import Transfer
from app.services.intelligence.rpc import DEAD, ZERO

SNIPE_BLOCKS = 3
FANOUT_MIN = 3


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.p[self.find(a)] = self.find(b)


def analyze(balances: dict[str, int], supply: int, transfers: list[Transfer], token: str,
            pair: str | None, contracts: set[str]) -> dict:
    excluded = {ZERO, DEAD, token.lower()} | ({pair.lower()} if pair else set())
    eligible = {a for a, b in balances.items() if b > 0 and a not in excluded and a not in contracts}
    uf = _UF()
    for a in eligible:
        uf.find(a)

    # 1. wallet-to-wallet
    by_tx: dict[str, list[Transfer]] = defaultdict(list)
    for t in transfers:
        by_tx[t.tx].append(t)
        if t.frm in eligible and t.to in eligible and t.frm != t.to:
            uf.union(t.frm, t.to)
    # 2. fan-out in one tx
    for txs in by_tx.values():
        fan: dict[str, set[str]] = defaultdict(set)
        for t in txs:
            if t.frm not in excluded and t.to in eligible:
                fan[t.frm].add(t.to)
        for sender, rcpts in fan.items():
            if len(rcpts) >= FANOUT_MIN:
                first = next(iter(rcpts))
                for r in rcpts:
                    uf.union(first, r)
                if sender in eligible:
                    uf.union(sender, first)
    # 3. snipers
    snipers: set[str] = set()
    if pair:
        pair = pair.lower()
        pool_blocks = [t.block for t in transfers if pair in (t.frm, t.to)]
        if pool_blocks:
            b0 = min(pool_blocks)
            per_block: dict[int, list[str]] = defaultdict(list)
            for t in transfers:
                if t.frm == pair and b0 <= t.block < b0 + SNIPE_BLOCKS and t.to in eligible:
                    snipers.add(t.to)
                    per_block[t.block].append(t.to)
            for wallets in per_block.values():
                for w in wallets[1:]:
                    uf.union(wallets[0], w)

    groups: dict[str, list[str]] = defaultdict(list)
    for a in eligible:
        groups[uf.find(a)].append(a)

    pct = lambda raw: raw / supply * 100 if supply else 0.0  # noqa: E731
    operators = []
    for wallets in groups.values():
        wallets.sort(key=lambda w: -balances[w])
        label = "sniper" if any(w in snipers for w in wallets) else "cluster" if len(wallets) > 1 else "single"
        operators.append({"wallets": wallets, "pct": round(pct(sum(balances[w] for w in wallets)), 4), "label": label})
    operators.sort(key=lambda o: -o["pct"])
    for i, o in enumerate(operators):
        o["id"] = i

    excluded_raw = sum(balances.get(a, 0) for a in excluded | contracts)  # pool is often both: count once
    circulating = max(supply - excluded_raw, 0)
    linked_raw = sum(sum(balances[w] for w in o["wallets"]) for o in operators if o["label"] != "single")
    real_hands = round((circulating - linked_raw) / circulating * 100, 2) if circulating else None

    op_of = {w: o["id"] for o in operators for w in o["wallets"]}
    op_label = {o["id"]: o["label"] for o in operators}
    top = []
    for a, b in sorted(balances.items(), key=lambda x: -x[1])[:10]:
        label = ("pool" if pair and a == pair else "burn" if a in (ZERO, DEAD) else
                 "contract" if a in contracts or a == token.lower() else
                 op_label.get(op_of.get(a, -1)) if op_label.get(op_of.get(a, -1)) != "single" else None)
        top.append({"address": a, "pct": round(pct(b), 4), "operator_id": op_of.get(a), "label": label,
                    "is_contract": a in contracts if contracts else None})

    labels = []
    for o in operators[:5]:
        if o["pct"] < 1:
            break
        lead = o["wallets"][0]
        desc = {"sniper": f"sniper group of {len(o['wallets'])}" if len(o["wallets"]) > 1 else "sniper",
                "cluster": f"{len(o['wallets'])} linked wallets", "single": "whale"}[o["label"]]
        labels.append(f"{lead[:6]}…{lead[-4:]}: {desc} · {o['pct']:.1f}%")

    return {
        "operators": operators,
        "top_holder_pct": operators[0]["pct"] if operators else None,
        "real_hands_pct": real_hands,
        "top_holders": top,
        "snipers": sorted(snipers),
        "whale_labels": labels,
    }
