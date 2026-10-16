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
    # The Robinhood Blockscout sits behind a Cloudflare JS challenge that only a real browser passes. Explorer
    # reads go through the Chromium sidecar (`browser/`, POST {url}/internal/fetch). The paid read proxy
    # (e.g. https://r.jina.ai) is an optional FALLBACK, used only if EXPLORER_PROXY_PREFIX is set and the
    # browser failed. Both empty = call the explorer directly (works only when it is not challenged).
    explorer_browser_url: str = ""  # e.g. http://browser:8765
    explorer_browser_token: str = ""  # shared secret for the sidecar (optional, internal network anyway)
    explorer_proxy_prefix: str = ""
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
    # comma-separated, tried in order when RPC_URL fails or is cooling down (429 / 5xx). Do NOT list nodes that
    # never answer here (robinhood.drpc.org returns 500 on eth_getLogs): they only add latency.
    rpc_fallback_urls: str = ""
    # endpoints tried FIRST for eth_getLogs bursts (the official node throttles those first); the general
    # RPC_URL / fallbacks follow. Example: https://robinhood-rpc.publicnode.com
    rpc_logs_urls: str = ""
    rpc_logs_concurrency: int = 2  # eth_getLogs in flight across the whole process (scans, indexer, copy, rank)
    rpc_logs_min_interval_ms: int = 100  # minimum spacing between eth_getLogs calls
    rpc_batch_size: int = 50  # JSON-RPC calls per HTTP request (portfolio / snapshots)
    explorer_proxy_key: str = ""  # bearer key for the explorer read proxy (r.jina.ai API key)

    # Scan queues
    scan_queue_max: int = 300  # bulk queue length cap; overflow is dropped and picked up by refresh_stale later
    scan_retry_max: int = 3  # automatic retries per token after a failed/incomplete scan, then a cool-off
    scan_retry_delays: str = "30,120,480"  # seconds before retry 1, 2, 3 (jittered +-20%)
    scan_give_up_cooldown_s: int = 3600  # no automatic re-queueing for this long after the retries ran out
    scan_concurrency: int = 4  # scans run at once per drain
    scan_bulk_per_tick: int = 8  # bulk scans started per 5 s tick
    refresh_batch: int = 40  # stale tokens enqueued per refresh_stale run
    refresh_marker_ttl_s: int = 180  # "user asked for a fresh scan" marker (skips cache + DB shortcut)
    explorer_rpm: int = 0  # explorer reads per minute, all threads; 0 = auto (60 browser, 20 anonymous proxy, 200 with key)

    # Jobs
    safety_refresh_min: int = 30
    safety_stale_min: int = 60
    position_snapshot_min: int = 5

    # Indexer / copy feed
    weth_address: str = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"  # WETH on Robinhood Chain
    # Quote currencies used to value trades: "address=USD" (pegged $1) or "address=ETH" (ETH price).
    # Native ETH and WETH_ADDRESS are always ETH. Trades in any other quote are not valued/ranked.
    quote_tokens: str = ("0x5fc5360d0400a0fd4f2af552add042d716f1d168=USD,"  # USDG "Global Dollar"
                         "0x0bd7d308f8e1639fab988df18a8011f41eacad73=ETH")  # WETH
    discovery_lookback_blocks: int = 2_000_000  # first run: how far back to look for V4 pools
    copy_lookback_blocks: int = 20_000  # first run of the live copy collector
    backfill_blocks: int = 1_000_000  # RPC backfill depth on first follow when the explorer is down
    backfill_pages: int = 4  # explorer pages (50 transfers each) per first-follow backfill
    rank_tokens: int = 40  # tokens analysed per smart-wallet ranking run
    rank_swaps_per_token: int = 800
    rank_window_blocks: int = 600_000
    suggested_min_closed: int = 2  # closed positions for a "strong" suggestion
    suggested_fill_to: int = 20  # below this many, top up with wallets that have only 1 closed (winning) trade
    spectate_wallets: int = 10  # top suggested wallets whose trades are collected for every user

    # Alerts
    alerts_per_hour: int = 10  # push/Telegram deliveries per user per hour (in-app list is unlimited)
    price_move_pct: float = 20.0
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    # Path + header secret for the bot webhook ([A-Za-z0-9_-], 32+ chars). Empty = webhook disabled.
    telegram_webhook_secret: str = ""
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:support@chainfolio.app"


@lru_cache
def get_settings() -> Settings:
    return Settings()
