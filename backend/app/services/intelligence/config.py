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


def logs_urls() -> list[str]:
    """Endpoints preferred for eth_getLogs (before the general list)."""
    return [u.strip() for u in _env("RPC_LOGS_URLS", "").split(",") if u.strip()]


def logs_concurrency() -> int:
    return max(int(_env("RPC_LOGS_CONCURRENCY", "2") or 2), 1)


def logs_min_interval() -> float:
    return max(int(_env("RPC_LOGS_MIN_INTERVAL_MS", "100") or 0), 0) / 1000


def browser_url() -> str:
    """Base URL of the Chromium sidecar (`browser/`). The explorer sits behind a Cloudflare JS challenge that
    only a real browser passes; the sidecar fetches explorer URLs for us. Empty = no sidecar."""
    return _env("EXPLORER_BROWSER_URL", "").strip().rstrip("/")


def browser_token() -> str:
    return _env("EXPLORER_BROWSER_TOKEN", "").strip()


def explorer_base() -> str:
    return _env("EXPLORER_API_URL", "https://robinhoodchain.blockscout.com").rstrip("/")


def explorer_rpm() -> int:
    """Explorer reads per minute, shared by every thread. Browser sidecar ~60; free r.jina.ai ~20, with a key ~200."""
    configured = int(_env("EXPLORER_RPM", "0") or 0)
    if configured > 0:
        return configured
    if browser_url():
        return 60  # local browser: no third-party quota, just be polite to Blockscout
    return 200 if proxy_key() else 20


def proxy_prefix() -> str:
    return _proxy_prefix or os.environ.get("EXPLORER_PROXY_PREFIX", "").strip()


def _env(key: str, default: str) -> str:
    # "set" must be distinguishable from "empty": an explicit "" disables explorer reads.
    if key in _overrides:
        return _overrides[key]
    return os.environ.get(key, default)


def rpc_urls() -> str:
    """`RPC_URL` plus `RPC_FALLBACK_URLS` as one comma-separated list (first = preferred for plain calls).

    The engine's endpoint pool picks per request: it drops an endpoint whose eth_getLogs range cap is
    smaller than the span asked for. That is exactly the split this chain needs — a keyed Alchemy first
    (fast eth_call/balances, but a 10-block log cap) with the official node behind it for wide getLogs.
    Reading only `RPC_URL` here was a real defect: with the keyed endpoint as RPC_URL, every scan that ran
    inside the API process lost its Transfer logs and reported UNKNOWN.
    """
    urls = []
    for chunk in (_env("RPC_URL", "https://rpc.mainnet.chain.robinhood.com"), _env("RPC_FALLBACK_URLS", "")):
        urls += [u.strip() for u in chunk.split(",") if u.strip()]
    seen: set[str] = set()
    ordered = [u for u in urls if not (u in seen or seen.add(u))]
    return ",".join(ordered)


def chains() -> dict[str, ChainConfig]:
    return {
        "robinhood": ChainConfig(
            name="robinhood",
            chain_id=int(_env("CHAIN_ID", "4663")),
            # The official public node answers wide eth_getLogs; third-party mirrors cap the range or
            # demand an archive token. Keep the chunk under 10k so free plans accept it.
            rpc_url=rpc_urls(),
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
