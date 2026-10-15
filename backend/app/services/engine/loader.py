from functools import lru_cache

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.engine.base import ScanEngine
from app.services.engine.builtin import BuiltinEngine
from app.services.engine.local import IntelligenceEngine

log = get_logger(__name__)


def rpc_urls() -> str:
    """Primary + fallbacks, comma-separated (intelligence.rpc.Rpc fails over in order)."""
    s = get_settings()
    return ",".join(u.strip() for u in [s.rpc_url, *s.rpc_fallback_urls.split(",")] if u.strip())


def apply_rpc_config() -> None:
    """Hand the RPC pacing settings to the intelligence package (jobs call this before they build an Rpc)."""
    from app.services import intelligence

    s = get_settings()
    intelligence.configure(rpc_logs_urls=s.rpc_logs_urls, rpc_logs_concurrency=s.rpc_logs_concurrency,
                           rpc_logs_min_interval_ms=s.rpc_logs_min_interval_ms)


@lru_cache
def get_engine() -> ScanEngine:
    s = get_settings()
    if s.scan_engine == "builtin":
        log.warning("engine_builtin_dev_only", detail="market data only; holders/contract checks disabled")
        from app.services import intelligence

        intelligence.configure(dexscreener_chain=s.dexscreener_chain, geckoterminal_network=s.geckoterminal_network)
        return BuiltinEngine(s.chain_name)
    apply_rpc_config()
    engine = IntelligenceEngine(
        s.chain_name, chain_id=s.chain_id, rpc_url=rpc_urls(), explorer_api_url=s.explorer_api_url,
        explorer_proxy_prefix=s.explorer_proxy_prefix, explorer_proxy_key=s.explorer_proxy_key or None,
        explorer_rpm=s.explorer_rpm,
        dexscreener_chain=s.dexscreener_chain, geckoterminal_network=s.geckoterminal_network,
        holder_scan_blocks=s.holder_scan_blocks, log_chunk_blocks=s.log_chunk_blocks,
    )
    log.info("engine_loaded", engine=engine.name, chain=s.chain_name, explorer=bool(s.explorer_api_url))
    return engine
