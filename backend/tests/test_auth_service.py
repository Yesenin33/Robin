from datetime import datetime, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from app.core.security import decode_jwt
from app.services import auth_service as auth_mod
from app.services.auth_service import AuthError, AuthService


@pytest.fixture
def sent(monkeypatch):
    box = []

    async def fake_send(to, subject, body):
        box.append((to, body))

    monkeypatch.setattr(auth_mod, "send_mail", fake_send)
    return box


async def test_magic_link_flow_single_use(db, redis, sent):
    svc = AuthService(db, redis)
    await svc.send_magic_link("Alex@Email.com")
    to, body = sent[0]
    token = body.split("token=")[1].split()[0]
    out = await svc.verify_magic_link(token)
    assert out.user.email == "alex@email.com" and out.user.plan == "free"
    assert decode_jwt(out.access_token) == out.user.id
    with pytest.raises(AuthError):
        await svc.verify_magic_link(token)


async def test_magic_link_same_user_twice(db, redis, sent):
    svc = AuthService(db, redis)
    ids = []
    for _ in range(2):
        await svc.send_magic_link("a@b.co")
        ids.append((await svc.verify_magic_link(sent[-1][1].split("token=")[1].split()[0])).user.id)
    assert ids[0] == ids[1]


def siwe_message(address: str, nonce: str, domain: str | None = None, chain: int | None = None) -> str:
    from app.core.config import get_settings

    domain = domain or get_settings().siwe_domain  # whatever the app under test expects, never a literal
    chain = chain or get_settings().chain_id
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return (f"{domain} wants you to sign in with your Ethereum account:\n{address}\n\n"
            f"Sign in to Chainfolio\n\nURI: http://{domain}\nVersion: 1\nChain ID: {chain}\n"
            f"Nonce: {nonce}\nIssued At: {now}")


def sign(acct, msg: str) -> str:
    return acct.sign_message(encode_defunct(text=msg)).signature.hex()


async def test_siwe_login_and_nonce_reuse(db, redis):
    svc = AuthService(db, redis)
    acct = Account.create()
    nonce = await svc.siwe_nonce()
    msg = siwe_message(acct.address, nonce)
    out = await svc.siwe_login(msg, sign(acct, msg))
    assert out.user.wallets[0].address == acct.address.lower()
    with pytest.raises(AuthError, match="nonce"):
        await svc.siwe_login(msg, sign(acct, msg))


async def test_siwe_links_wallet_to_email_user(db, redis, sent):
    svc = AuthService(db, redis)
    await svc.send_magic_link("x@y.io")
    email_user = (await svc.verify_magic_link(sent[0][1].split("token=")[1].split()[0])).user
    acct = Account.create()
    msg = siwe_message(acct.address, await svc.siwe_nonce())
    from app.models import User
    out = await svc.siwe_login(msg, sign(acct, msg), await db.get(User, email_user.id))
    assert out.user.id == email_user.id and len(out.user.wallets) == 1


@pytest.mark.parametrize("kw,err", [({"domain": "evil.com"}, "domain"), ({"chain": 1}, "chain")])
async def test_siwe_rejects_wrong_domain_or_chain(db, redis, kw, err):
    svc = AuthService(db, redis)
    acct = Account.create()
    msg = siwe_message(acct.address, await svc.siwe_nonce(), **kw)
    with pytest.raises(AuthError, match=err):
        await svc.siwe_login(msg, sign(acct, msg))


async def test_siwe_rejects_foreign_signature(db, redis):
    svc = AuthService(db, redis)
    victim, attacker = Account.create(), Account.create()
    msg = siwe_message(victim.address, await svc.siwe_nonce())
    with pytest.raises(AuthError, match="signature"):
        await svc.siwe_login(msg, sign(attacker, msg))
