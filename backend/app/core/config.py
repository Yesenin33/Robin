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
    rpc_url: str = "https://rpc.chain.robinhood.com"
    native_symbol: str = "ETH"

    # CRAWLSCAN engine: path on disk + "module:function" entrypoint returning a dict report
    crawlscan_path: str = "/opt/crawlscan"
    crawlscan_entry: str = "crawlscan:scan"
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
    rate_limit_per_min: int = 60

    # Jobs
    safety_refresh_min: int = 30
    safety_stale_min: int = 60
    position_snapshot_min: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
