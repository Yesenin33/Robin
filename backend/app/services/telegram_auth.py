"""Telegram Mini App initData validation (core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    pass


def _hash(secret: bytes, fields: dict[str, str]) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def validate_init_data(init_data: str, bot_token: str, max_age: int = 300, now: float | None = None) -> dict:
    """Return the Telegram `user` object if initData is authentic and fresh; raise InitDataError otherwise.

    secret = HMAC_SHA256(key="WebAppData", msg=bot_token); hash = HMAC_SHA256(secret, data_check_string).
    """
    if not init_data or not bot_token:
        raise InitDataError("missing init data")
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError as e:
        raise InitDataError("malformed init data") from e
    given = fields.pop("hash", "")
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    candidates = [_hash(secret, fields)]
    if "signature" in fields:  # newer clients add an Ed25519 `signature`; accept either data-check variant
        candidates.append(_hash(secret, {k: v for k, v in fields.items() if k != "signature"}))
    if not given or not any(hmac.compare_digest(c, given) for c in candidates):
        raise InitDataError("bad hash")
    try:
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
    except (KeyError, ValueError) as e:
        raise InitDataError("missing auth_date/user") from e
    if (now or time.time()) - auth_date > max_age:
        raise InitDataError("init data expired")
    if not isinstance(user, dict) or "id" not in user:
        raise InitDataError("no user id")
    return user


def display_name(user: dict) -> str:
    """'@username' or first name — never the numeric id."""
    return f"@{user['username']}" if user.get("username") else (user.get("first_name") or "your Telegram")
