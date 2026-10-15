from functools import lru_cache

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.engine.base import ScanEngine
from app.services.engine.builtin import BuiltinEngine
from app.services.engine.local import IntelligenceEngine

log = get_logger(__name__)


@lru_cache
def get_engine() -> ScanEngine:
    s = get_settings()
    if s.scan_engine == "builtin":
        log.warning("engine_builtin_dev_only", detail="market data only; holders/contract checks disabled")
        from app.services import intelligence

        intelligence.configure(dexscreener_chain=s.dexscreener_chain, geckoterminal_network=s.geckoterminal_network)
        return BuiltinEngine(s.chain_name)
    engine = IntelligenceEngine(
        s.chain_name, chain_id=s.chain_id, rpc_url=s.rpc_url, explorer_api_url=s.explorer_api_url,
        explorer_proxy_prefix=s.explorer_proxy_prefix,
        dexscreener_chain=s.dexscreener_chain, geckoterminal_network=s.geckoterminal_network,
        holder_scan_blocks=s.holder_scan_blocks, log_chunk_blocks=s.log_chunk_blocks,
    )
    log.info("engine_loaded", engine=engine.name, chain=s.chain_name, explorer=bool(s.explorer_api_url))
    return engine
