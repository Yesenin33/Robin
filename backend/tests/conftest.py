"""Hermetic test setup: the suite must give the same result on a dev box, in CI and inside the production
container, whatever `.env` or environment variables are around.

Before anything imports the app we (1) stop pydantic from reading any `.env`, (2) delete every environment
variable the app or the intelligence package reads (a deployed SIWE_DOMAIN, bot tokens, rate limits, RPC
endpoints ...), and (3) pin the few values tests rely on. Tests that need another value patch the settings
object with `monkeypatch`, never the environment.
"""
import os

from app.core import config as _config

# TEST_SIWE_DOMAIN lets you prove the tests derive the domain from settings instead of hardcoding it
PINNED_SIWE_DOMAIN = os.environ.get("TEST_SIWE_DOMAIN", "localhost:3000")

PINNED_ENV = {
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "REDIS_URL": "redis://localhost:6399/0",  # never reached: tests use fakeredis
    "JWT_SECRET": "test-secret",
    "SIWE_DOMAIN": PINNED_SIWE_DOMAIN,
    "CHAIN_ID": "4663",
}


def _make_hermetic() -> None:
    _config.Settings.model_config["env_file"] = None  # a real backend/.env must never leak in
    app_vars = {name.upper() for name in _config.Settings.model_fields}
    for name in list(os.environ):
        if name.upper() in app_vars:
            del os.environ[name]
    os.environ.update(PINNED_ENV)
    _config.get_settings.cache_clear()


_make_hermetic()

import fakeredis.aioredis  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.models import Base  # noqa: E402


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


# ---------- shared DB for jobs + API (jobs open their own sessions via SessionLocal) ----------

SESSION_MODULES = ["app.core.db", "app.jobs.copy_job", "app.jobs.discovery_job", "app.jobs.rank_job",
                   "app.jobs.alert_job", "app.jobs.scan_job", "app.jobs.snapshot_job", "app.api.health",
                   "app.api.telegram"]


@pytest.fixture
async def appdb(tmp_path, monkeypatch, redis):
    """File-backed SQLite shared by every SessionLocal user; yields the sessionmaker."""
    import importlib

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    for name in SESSION_MODULES:
        mod = importlib.import_module(name)
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", maker)
    from app.services.market import MarketClient

    async def eth_price(self, symbol="ETH"):
        return 3000.0

    monkeypatch.setattr(MarketClient, "native_price_usd", eth_price)
    monkeypatch.setattr("app.core.redis._redis", redis)
    yield maker
    await engine.dispose()


@pytest.fixture
async def client(appdb, redis):
    import httpx

    from app.core.db import get_session
    from app.core.redis import get_redis
    from app.main import app

    async def session():
        async with appdb() as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_redis] = lambda: redis
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def signed_in(client):
    """A real SIWE login through the HTTP API; returns (headers, address, account)."""
    from eth_account import Account

    from tests.test_auth_service import sign, siwe_message

    acct = Account.create()
    nonce = (await client.get("/auth/siwe/nonce")).json()["nonce"]
    msg = siwe_message(acct.address, nonce)
    r = await client.post("/auth/siwe", json={"message": msg, "signature": sign(acct, msg)})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, acct.address.lower(), acct


@pytest.fixture(autouse=True)
def _reset_process_state():
    """Cooldowns, learned caps, pacing slots, config overrides, cached engine: all module-level, all reset, so the
    result of a test can't depend on which tests ran before it (or on their order)."""
    from app.services.engine.loader import get_engine
    from app.services.intelligence import config as icfg
    from app.services.intelligence import http as ihttp
    from app.services.intelligence.endpoints import POOL
    from app.services.intelligence.rpc import LOGS_GATE

    def reset():
        POOL.reset()
        LOGS_GATE.reset()
        ihttp.PACER.__init__()  # fresh lanes and clock
        ihttp.CACHE.clear()
        icfg._overrides.clear()
        icfg._overrides["EXPLORER_API_URL"] = ""  # the default is the real explorer; tests that need one set their own
        icfg._proxy_prefix = ""
        get_engine.cache_clear()

    reset()
    yield
    reset()


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch):
    """No test may touch the network: a real RPC / explorer / market call makes results depend on the host (and on
    whether it is online). Anything but loopback / unix sockets fails at once with a connection error, which the
    code under test handles like any unreachable source - and which a test would have to mock to get a result."""
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else None  # None: AF_UNIX path
        if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
            raise ConnectionRefusedError(f"network access blocked in tests: {address!r}")
        return real_connect(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded)
