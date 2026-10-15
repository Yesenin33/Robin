"""Chain detection: which configured chain has contract code at this address."""
import re

from app.services.intelligence.config import chains
from app.services.intelligence.rpc import Rpc, RpcError

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def is_address(v: str) -> bool:
    return bool(ADDRESS_RE.match(v or ""))


def detect_chain(address: str, rpc_factory=Rpc) -> str | None:
    for name, cfg in chains().items():
        try:
            if rpc_factory(cfg.rpc_url).get_code(address) not in ("0x", "0x0", None):
                return name
        except (RpcError, RuntimeError):
            continue
    return None
