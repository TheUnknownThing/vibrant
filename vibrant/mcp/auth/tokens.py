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

    def verify(self, token: str) -> dict[str, Any]:
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

    def verify(self, token: str) -> dict[str, Any]:
        segments = token.split(".")
        if len(segments) != 3:
            raise ValueError("Invalid JWT format")

        header_segment, payload_segment, signature_segment = segments
        header = json.loads(_b64url_decode(header_segment).decode("utf-8"))
        if header.get("alg") != self.algorithm:
            raise ValueError(f"Unsupported JWT algorithm: {header.get('alg')}")
        if header.get("kid") not in {None, self.kid}:
            raise ValueError(f"Unexpected JWT kid: {header.get('kid')}")

        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        expected_signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        provided_signature = _b64url_decode(signature_segment)
        if not hmac.compare_digest(expected_signature, provided_signature):
            raise ValueError("Invalid JWT signature")

        return json.loads(_b64url_decode(payload_segment).decode("utf-8"))

    def jwks_document(self) -> dict[str, Any]:
        return {"keys": []}


def decode_unverified(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying the signature."""
    segments = token.split(".")
    if len(segments) != 3:
        raise ValueError("Invalid JWT format")
    return json.loads(_b64url_decode(segments[1]).decode("utf-8"))


__all__ = ["HMACTokenSigner", "TokenSigner", "decode_unverified"]
