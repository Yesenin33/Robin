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
