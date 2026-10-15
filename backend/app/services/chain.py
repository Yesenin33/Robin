from app.services.engine.loader import rpc_urls
from app.services.intelligence.rpc import Rpc


def make_rpc() -> Rpc:
    """Sync RPC for jobs: primary + fallbacks, same failover as the scan engine."""
    return Rpc(rpc_urls())
