"""Token-signing helpers for the authorization server scaffold."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any, Protocol


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class TokenSigner(Protocol):
    """Abstract signer interface used by the auth server service."""

    algorithm: str
    kid: str

    def sign(self, claims: Mapping[str, Any]) -> str:
        ...

    def jwks_document(self) -> dict[str, Any]:
        ...


class HMACTokenSigner:
    """Development-only HS256 token signer.

    This is useful for local scaffolding and unit tests. It does not publish a
    usable JWKS document because symmetric signing keys must not be exposed.
    """

    algorithm = "HS256"

    def __init__(self, secret: str, *, kid: str = "dev-hs256") -> None:
        self._secret = secret.encode("utf-8")
        self.kid = kid

    def sign(self, claims: Mapping[str, Any]) -> str:
        header = {
            "alg": self.algorithm,
            "typ": "JWT",
            "kid": self.kid,
        }
        header_segment = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_segment = _b64url_encode(json.dumps(dict(claims), separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        return f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"

    def jwks_document(self) -> dict[str, Any]:
        return {"keys": []}


def decode_unverified(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying the signature."""
    segments = token.split(".")
    if len(segments) != 3:
        raise ValueError("Invalid JWT format")
    return json.loads(_b64url_decode(segments[1]).decode("utf-8"))


__all__ = ["HMACTokenSigner", "TokenSigner", "decode_unverified"]
