"""SIWE through the HTTP API (nonce -> sign -> login -> /auth/me), plus the rejection paths."""
from eth_account import Account

from tests.test_auth_service import sign, siwe_message


async def test_siwe_full_flow(client, signed_in):
    headers, address, _ = signed_in
    me = (await client.get("/auth/me", headers=headers)).json()
    assert me["wallets"] == [{"address": address, "kind": "siwe"}] and me["plan"] == "free"


async def test_siwe_nonce_is_single_use(client):
    acct = Account.create()
    nonce = (await client.get("/auth/siwe/nonce")).json()["nonce"]
    msg = siwe_message(acct.address, nonce)
    body = {"message": msg, "signature": sign(acct, msg)}
    assert (await client.post("/auth/siwe", json=body)).status_code == 200
    r = await client.post("/auth/siwe", json=body)
    assert r.status_code == 401 and "nonce" in r.json()["detail"].lower()


async def test_siwe_wrong_signer_and_domain(client):
    victim, attacker = Account.create(), Account.create()
    nonce = (await client.get("/auth/siwe/nonce")).json()["nonce"]
    msg = siwe_message(victim.address, nonce)
    assert (await client.post("/auth/siwe", json={"message": msg, "signature": sign(attacker, msg)})).status_code == 401
    nonce = (await client.get("/auth/siwe/nonce")).json()["nonce"]
    msg = siwe_message(victim.address, nonce, domain="evil.example")
    assert (await client.post("/auth/siwe", json={"message": msg, "signature": sign(victim, msg)})).status_code == 401


async def test_protected_routes_need_auth(client):
    for path in ("/auth/me", "/copy/feed", "/copy/suggested", "/alerts"):
        assert (await client.get(path)).status_code == 401, path


async def test_per_user_rate_limit(client, signed_in, monkeypatch):
    from app.core.config import get_settings

    headers, _, _ = signed_in
    monkeypatch.setattr(get_settings(), "rate_limit_user_per_min", 3)
    monkeypatch.setattr(get_settings(), "rate_limit_per_min", 1000)
    codes = [(await client.get("/alerts", headers=headers)).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]


# ---------- wallet = identity: linking never creates a second account ----------

async def _tg_account(client, monkeypatch, chat=555):
    """A Telegram-only account (what the Mini App creates), as (headers, user id)."""
    from app.core.config import get_settings
    from tests.test_telegram import TOKEN, init_data

    s = get_settings()
    monkeypatch.setattr(s, "telegram_bot_token", TOKEN)
    monkeypatch.setattr(s, "telegram_bot_username", "chainfolio_bot")
    r = await client.post("/alerts/telegram/link-webapp", json={"init_data": init_data({"id": chat, "username": "alex"})})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return headers, (await client.get("/auth/me", headers=headers)).json()["id"]


async def _siwe(client, acct, headers=None, link_token=None):
    nonce = (await client.get("/auth/siwe/nonce")).json()["nonce"]
    msg = siwe_message(acct.address, nonce)
    body = {"message": msg, "signature": sign(acct, msg)}
    if link_token:
        body["link_token"] = link_token
    return await client.post("/auth/siwe", json=body, headers=headers or {})


async def test_wallet_links_to_the_telegram_account_with_bearer(client, monkeypatch):
    from tests.test_telegram import FakeSender
    from app.api.alerts import get_sender
    from app.main import app

    app.dependency_overrides[get_sender] = lambda: FakeSender()
    headers, uid = await _tg_account(client, monkeypatch)
    acct = Account.create()
    out = (await _siwe(client, acct, headers)).json()
    assert out["user"]["id"] == uid and out["user"]["wallets"][0]["address"] == acct.address.lower()
    assert (await client.get("/alerts/channels", headers=headers)).json()["telegram"] is True  # still linked


async def test_link_token_lets_a_browser_attach_its_wallet_once(client, monkeypatch):
    from tests.test_telegram import FakeSender
    from app.api.alerts import get_sender
    from app.main import app

    app.dependency_overrides[get_sender] = lambda: FakeSender()
    headers, uid = await _tg_account(client, monkeypatch)
    tok = (await client.post("/auth/link-token", headers=headers)).json()
    assert tok["expires_in"] == 600
    out = (await _siwe(client, Account.create(), link_token=tok["token"])).json()  # a browser with NO session
    assert out["user"]["id"] == uid  # same account, not a second one
    replay = await _siwe(client, Account.create(), link_token=tok["token"])
    assert replay.status_code == 401 and "expired" in replay.json()["detail"]
    assert (await client.post("/auth/link-token")).status_code == 401


async def test_existing_wallet_account_wins_and_telegram_moves_to_it(client, monkeypatch):
    from tests.test_telegram import FakeSender
    from app.api.alerts import get_sender
    from app.main import app

    app.dependency_overrides[get_sender] = lambda: FakeSender()
    acct = Account.create()
    first = (await _siwe(client, acct)).json()  # wallet account created earlier (e.g. on desktop)
    wallet_uid, wallet_headers = first["user"]["id"], {"Authorization": f"Bearer {first['access_token']}"}
    tg_headers, tg_uid = await _tg_account(client, monkeypatch)
    assert tg_uid != wallet_uid
    out = (await _siwe(client, acct, tg_headers)).json()  # the Telegram-only account connects that same wallet
    assert out["user"]["id"] == wallet_uid
    assert (await client.get("/alerts/channels", headers=wallet_headers)).json()["telegram"] is True
    assert (await client.get("/alerts/channels", headers=tg_headers)).json()["telegram"] is False
