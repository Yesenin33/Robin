from app.services import intelligence
from app.services.engine.base import ScanReport, map_report


class IntelligenceEngine:
    """Default engine: in-repo intelligence package."""

    name = intelligence.ENGINE_NAME

    def __init__(self, chain: str = "robinhood", **chain_config: object):
        self.chain = chain
        intelligence.configure(**chain_config)

    def scan(self, address: str) -> ScanReport:
        return map_report(intelligence.scan(address, self.chain))
