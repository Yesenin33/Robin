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
_proxy_prefix: str = ""


def configure(**values: object) -> None:
    """Explicit config from the host app (takes precedence over env). Keys = env names, lower-case."""
    _overrides.update({k.upper(): str(v) for k, v in values.items() if v is not None})
    if "EXPLORER_PROXY_PREFIX" in _overrides:
        set_proxy_prefix(_overrides["EXPLORER_PROXY_PREFIX"])


def set_proxy_prefix(value: str | None) -> None:
    """Read proxy for explorer calls (some explorers sit behind a JS challenge)."""
    global _proxy_prefix
    _proxy_prefix = (value or "").strip()


def proxy_key() -> str:
    return _overrides.get("EXPLORER_PROXY_KEY") or os.environ.get("EXPLORER_PROXY_KEY", "").strip()


def proxy_prefix() -> str:
    return _proxy_prefix or os.environ.get("EXPLORER_PROXY_PREFIX", "").strip()


def _env(key: str, default: str) -> str:
    # "set" must be distinguishable from "empty": an explicit "" disables explorer reads.
    if key in _overrides:
        return _overrides[key]
    return os.environ.get(key, default)


def chains() -> dict[str, ChainConfig]:
    return {
        "robinhood": ChainConfig(
            name="robinhood",
            chain_id=int(_env("CHAIN_ID", "4663")),
            # The official public node answers eth_getLogs; third-party mirrors cap the range or
            # demand an archive token. Keep the chunk under 10k so free plans accept it.
            rpc_url=_env("RPC_URL", "https://rpc.mainnet.chain.robinhood.com"),
            explorer_api=_env("EXPLORER_API_URL", "https://robinhoodchain.blockscout.com"),
            dexscreener_slug=_env("DEXSCREENER_CHAIN", "robinhood"),
            geckoterminal_network=_env("GECKOTERMINAL_NETWORK", "robinhood"),
            holder_scan_blocks=int(_env("HOLDER_SCAN_BLOCKS", "60000")),
            log_chunk=int(_env("LOG_CHUNK_BLOCKS", "9999")),
        ),
    }


def get_chain(name: str) -> ChainConfig:
    try:
        return chains()[name]
    except KeyError:
        raise ValueError(f"unsupported chain: {name}") from None
