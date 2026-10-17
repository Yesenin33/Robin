from datetime import datetime, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from app.core.security import decode_jwt
from app.services.auth_service import AuthError, AuthService


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
    assert out.user.plan == "free"
    assert decode_jwt(out.access_token) == out.user.id
    with pytest.raises(AuthError, match="nonce"):
        await svc.siwe_login(msg, sign(acct, msg))


async def test_siwe_same_wallet_is_same_user(db, redis):
    """The wallet is the identity: signing in again must not mint a second account."""
    svc = AuthService(db, redis)
    acct = Account.create()
    ids = []
    for _ in range(2):
        msg = siwe_message(acct.address, await svc.siwe_nonce())
        ids.append((await svc.siwe_login(msg, sign(acct, msg))).user.id)
    assert ids[0] == ids[1]


async def test_siwe_links_wallet_to_existing_session(db, redis):
    """A signed-in session (bearer) attaches a new wallet to THAT account, not a fresh one."""
    from app.models import User

    svc = AuthService(db, redis)
    first = Account.create()
    msg = siwe_message(first.address, await svc.siwe_nonce())
    user = (await svc.siwe_login(msg, sign(first, msg))).user

    second = Account.create()
    msg2 = siwe_message(second.address, await svc.siwe_nonce())
    out = await svc.siwe_login(msg2, sign(second, msg2), await db.get(User, user.id))
    assert out.user.id == user.id and len(out.user.wallets) == 2


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
