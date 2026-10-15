"""Holder balances: Blockscout explorer when configured, otherwise Transfer-log replay + live balanceOf."""
from collections import Counter
from dataclasses import dataclass

from app.services.intelligence.config import ChainConfig
from app.services.intelligence.http import get_json
from app.services.intelligence.rpc import Rpc, to_int, topic_addr

MAX_CANDIDATES = 3000


@dataclass(frozen=True)
class Transfer:
    block: int
    tx: str
    frm: str
    to: str
    value: int


def parse_transfers(logs: list[dict]) -> list[Transfer]:
    out = []
    for lg in logs:
        t = lg.get("topics") or []
        if len(t) != 3:  # ERC-721 has 4 topics
            continue
        out.append(Transfer(to_int(lg["blockNumber"]), lg["transactionHash"], topic_addr(t[1]), topic_addr(t[2]),
                            to_int(lg.get("data"))))
    out.sort(key=lambda x: x.block)
    return out


@dataclass
class HolderData:
    balances: dict[str, int]  # address -> raw balance (>0)
    count: int | None
    exact: bool
    contracts: set[str]
    source: str


def from_explorer(cfg: ChainConfig, token: str) -> HolderData:
    base = cfg.explorer_api.rstrip("/")
    info = get_json(f"{base}/api/v2/tokens/{token}")
    page = get_json(f"{base}/api/v2/tokens/{token}/holders")
    balances, contracts = {}, set()
    for it in page.get("items", []):
        addr = it["address"]["hash"].lower()
        balances[addr] = int(it["value"])
        if it["address"].get("is_contract"):
            contracts.add(addr)
    count = info.get("holders_count") or info.get("holders")
    return HolderData(balances, int(count) if count is not None else None, count is not None, contracts, "explorer")


def from_transfers(rpc: Rpc, token: str, transfers: list[Transfer], window_from_genesis: bool) -> HolderData:
    activity: Counter[str] = Counter()
    for t in transfers:
        activity[t.frm] += 1
        activity[t.to] += 1
    candidates = [a for a, _ in activity.most_common(MAX_CANDIDATES)]
    balances = {a: b for a, b in rpc.balances_of(token, candidates).items() if b > 0}
    exact = window_from_genesis and len(activity) <= MAX_CANDIDATES
    return HolderData(balances, len(balances), exact, set(), "rpc-logs")
