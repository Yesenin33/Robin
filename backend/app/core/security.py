import hashlib
import secrets
import time

import jwt

from app.core.config import get_settings


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_jwt(user_id: int) -> str:
    s = get_settings()
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + s.jwt_ttl_s}, s.jwt_secret, "HS256")


def decode_jwt(token: str) -> int:
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    return int(payload["sub"])
