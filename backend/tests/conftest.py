import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret")

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
