from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "chainfolio"
    env: str = "dev"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000"]

    database_url: str = "postgresql+asyncpg://chainfolio:chainfolio@postgres:5432/chainfolio"
    redis_url: str = "redis://redis:6379/0"

    # Robinhood Chain
    chain_id: int = 4663
    rpc_url: str = "https://rpc.mainnet.chain.robinhood.com"
    native_symbol: str = "ETH"

    chain_name: str = "robinhood"
    # Blockscout-compatible explorer API; "" = holders come from Transfer-log replay (slow, needs a node)
    explorer_api_url: str = "https://robinhoodchain.blockscout.com"
    # The Robinhood Blockscout sits behind a Cloudflare challenge; explorer reads go through this
    # read proxy, which returns the JSON payload. Set "" to call the explorer directly.
    explorer_proxy_prefix: str = "https://r.jina.ai"
    dexscreener_chain: str = "robinhood"
    geckoterminal_network: str = "robinhood"
    holder_scan_blocks: int = 60_000
    log_chunk_blocks: int = 9_999

    # Token analytics: "intelligence" (in-repo, default) | "builtin" (market-only, offline dev)
    scan_engine: str = "intelligence"
    scan_timeout_s: int = 60

    # Auth
    jwt_secret: str = "change-me"
    jwt_ttl_s: int = 60 * 60 * 24 * 30
    magic_link_ttl_s: int = 15 * 60
    siwe_domain: str = "localhost:3000"
    siwe_nonce_ttl_s: int = 10 * 60

    # Mail (empty smtp_host = log link instead of sending)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = "Chainfolio <no-reply@chainfolio.app>"

    # Cache / rate limit
    safety_cache_ttl_s: int = 600
    portfolio_cache_ttl_s: int = 30
    rate_limit_per_min: int = 60  # per IP per route group
    rate_limit_user_per_min: int = 120  # per signed-in user, all routes
    warmup_tokens: int = 20  # most-requested tokens kept scanned before their cache expires

    # Extra data-source settings (production)
    rpc_fallback_urls: str = ""  # comma-separated, tried in order when RPC_URL fails (e.g. Alchemy first)
    explorer_proxy_key: str = ""  # bearer key for the explorer read proxy (r.jina.ai API key)

    # Jobs
    safety_refresh_min: int = 30
    safety_stale_min: int = 60
    position_snapshot_min: int = 5

    # Indexer / copy feed
    weth_address: str = ""  # WETH on Robinhood Chain, if pools use it instead of native ETH
    discovery_lookback_blocks: int = 2_000_000  # first run: how far back to look for V4 pools
    copy_lookback_blocks: int = 20_000  # first run of the live copy collector
    backfill_blocks: int = 1_000_000  # RPC backfill depth on first follow when the explorer is down
    backfill_pages: int = 4  # explorer pages (50 transfers each) per first-follow backfill
    rank_tokens: int = 15  # tokens analysed per smart-wallet ranking run
    rank_swaps_per_token: int = 300
    rank_window_blocks: int = 500_000
    suggested_min_closed: int = 2  # closed positions a wallet needs before we suggest it

    # Alerts
    alerts_per_hour: int = 10  # push/Telegram deliveries per user per hour (in-app list is unlimited)
    price_move_pct: float = 20.0
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:support@chainfolio.app"


@lru_cache
def get_settings() -> Settings:
    return Settings()
