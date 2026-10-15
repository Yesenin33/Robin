"""Chain registry. Values come from env so the package stays stdlib-only and app-agnostic."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ChainConfig:
    name: str
    chain_id: int
    rpc_url: str
    explorer_api: str  # Blockscout-compatible base URL, "" = not available
    dexscreener_slug: str
    geckoterminal_network: str
    holder_scan_blocks: int  # how far back to replay Transfer logs when no explorer
    log_chunk: int


_overrides: dict[str, str] = {}


def configure(**values: object) -> None:
    """Explicit config from the host app (takes precedence over env). Keys = env names, lower-case."""
    _overrides.update({k.upper(): str(v) for k, v in values.items() if v is not None})


def _env(key: str, default: str) -> str:
    return _overrides.get(key) or os.environ.get(key) or default


def chains() -> dict[str, ChainConfig]:
    return {
        "robinhood": ChainConfig(
            name="robinhood",
            chain_id=int(_env("CHAIN_ID", "4663")),
            rpc_url=_env("RPC_URL", "https://rpc.chain.robinhood.com"),
            explorer_api=_env("EXPLORER_API_URL", ""),
            dexscreener_slug=_env("DEXSCREENER_CHAIN", "robinhood"),
            geckoterminal_network=_env("GECKOTERMINAL_NETWORK", "robinhood"),
            holder_scan_blocks=int(_env("HOLDER_SCAN_BLOCKS", "200000")),
            log_chunk=int(_env("LOG_CHUNK_BLOCKS", "10000")),
        ),
    }


def get_chain(name: str) -> ChainConfig:
    try:
        return chains()[name]
    except KeyError:
        raise ValueError(f"unsupported chain: {name}") from None
