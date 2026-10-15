from functools import lru_cache

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.engine.base import ScanEngine
from app.services.engine.builtin import BuiltinEngine
from app.services.engine.crawlscan import CrawlscanEngine

log = get_logger(__name__)


@lru_cache
def get_engine() -> ScanEngine:
    s = get_settings()
    try:
        engine = CrawlscanEngine(s.crawlscan_path, s.crawlscan_entry)
        log.info("engine_loaded", engine="crawlscan", path=s.crawlscan_path)
        return engine
    except (ImportError, AttributeError, ValueError) as e:
        log.warning("crawlscan_unavailable_fallback_builtin", error=str(e))
        return BuiltinEngine()
