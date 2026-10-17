"""Embedded (social-login) wallet providers — interface only, Phase 2.

Non-custodial by design: provider (Web3Auth / Privy) holds key shares client-side;
we only ever receive and store the resulting public address.
"""
from typing import Protocol


class EmbeddedWalletProvider(Protocol):
    name: str

    async def verify_id_token(self, id_token: str) -> dict:
        """Validate provider JWT, return {'sub', 'email', 'address'}."""
        ...


class NotConfiguredProvider:
    name = "none"

    async def verify_id_token(self, id_token: str) -> dict:
        raise NotImplementedError("Embedded wallets (Web3Auth/Privy) are planned for Phase 2")
