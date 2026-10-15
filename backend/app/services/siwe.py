"""EIP-4361 (Sign-In With Ethereum) parsing + signature verification."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from eth_account import Account
from eth_account.messages import encode_defunct

HEADER = re.compile(r"^(?P<domain>\S+) wants you to sign in with your Ethereum account:$")


class SiweError(ValueError):
    pass


@dataclass
class SiweMessage:
    domain: str
    address: str
    uri: str
    version: str
    chain_id: int
    nonce: str
    issued_at: datetime
    expiration_time: datetime | None = None


def _dt(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def parse(message: str) -> SiweMessage:
    lines = message.split("\n")
    if len(lines) < 2 or not (m := HEADER.match(lines[0].strip())):
        raise SiweError("malformed SIWE header")
    fields = {}
    for line in lines[2:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            fields[k.strip()] = v.strip()
    try:
        return SiweMessage(
            domain=m["domain"], address=lines[1].strip(), uri=fields["URI"], version=fields["Version"],
            chain_id=int(fields["Chain ID"]), nonce=fields["Nonce"], issued_at=_dt(fields["Issued At"]),
            expiration_time=_dt(fields["Expiration Time"]) if "Expiration Time" in fields else None,
        )
    except (KeyError, ValueError) as e:
        raise SiweError(f"missing/invalid field: {e}") from e


def verify(message: str, signature: str, *, domain: str, chain_id: int, nonce: str) -> SiweMessage:
    msg = parse(message)
    if msg.domain != domain:
        raise SiweError("domain mismatch")
    if msg.chain_id != chain_id:
        raise SiweError("wrong chain")
    if msg.nonce != nonce:
        raise SiweError("nonce mismatch")
    if msg.expiration_time and msg.expiration_time < datetime.now(timezone.utc):
        raise SiweError("message expired")
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception as e:  # noqa: BLE001
        raise SiweError("bad signature") from e
    if recovered.lower() != msg.address.lower():
        raise SiweError("signature does not match address")
    return msg
