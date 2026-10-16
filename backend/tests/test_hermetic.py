"""The suite must not depend on the machine it runs on: no deployed `.env`, no leaked environment variables, no
state left by an earlier test. These tests guard conftest.py's hermetic setup."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import Settings, get_settings
from tests.conftest import PINNED_SIWE_DOMAIN

BACKEND = Path(__file__).resolve().parent.parent
HOSTILE = {
    "SIWE_DOMAIN": "deployed.example", "CHAIN_ID": "1", "RATE_LIMIT_PER_MIN": "1", "ALERTS_PER_HOUR": "1",
    "TELEGRAM_BOT_TOKEN": "999:HOSTILE", "TELEGRAM_WEBHOOK_SECRET": "x" * 40, "VAPID_PRIVATE_KEY": "x",
    "EXPLORER_BROWSER_URL": "http://nowhere:1", "EXPLORER_PROXY_PREFIX": "https://nowhere.example",
    "RPC_URL": "http://nowhere:2", "REDIS_URL": "redis://nowhere:1", "JWT_SECRET": "someone-elses-secret",
    "DATABASE_URL": "postgresql+asyncpg://nobody@nowhere/none", "SCAN_QUEUE_MAX": "1", "WARMUP_TOKENS": "0",
}


def test_settings_come_from_conftest_not_from_the_machine():
    s = get_settings()
    assert Settings.model_config["env_file"] is None  # a real backend/.env is never read
    assert (s.siwe_domain, s.chain_id, s.jwt_secret) == (PINNED_SIWE_DOMAIN, 4663, "test-secret")
    assert s.database_url.startswith("sqlite")
    assert not s.telegram_bot_token and not s.vapid_private_key and not s.explorer_browser_url
    assert not s.explorer_proxy_prefix and s.rate_limit_per_min == 60


def test_outbound_network_is_blocked():
    import socket

    from app.services.intelligence.http import HttpError, get_json

    with pytest.raises(ConnectionRefusedError, match="blocked in tests"):
        socket.create_connection(("93.184.216.34", 80), timeout=2)
    with pytest.raises(HttpError):  # the engine sees it as an unreachable source, instantly
        get_json("https://api.dexscreener.com/latest/dex/tokens/0xabc", tries=1)


def test_module_level_state_starts_clean():
    from app.services.intelligence import config as icfg
    from app.services.intelligence.endpoints import POOL

    assert icfg._overrides == {"EXPLORER_API_URL": ""} and icfg._proxy_prefix == ""
    assert POOL.snapshot("https://anything.example")["cooling_s"] == 0.0


@pytest.mark.skipif(os.environ.get("CF_HERMETIC_CHILD") == "1", reason="already the child run")
def test_auth_tests_pass_under_a_hostile_environment():
    """Run the SIWE/auth tests in a child process whose environment looks like a deployed server."""
    env = {**os.environ, **HOSTILE, "CF_HERMETIC_CHILD": "1"}
    done = subprocess.run([sys.executable, "-m", "pytest", "tests/test_auth_service.py", "-q", "-p", "no:cacheprovider"],
                          cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-500:]
