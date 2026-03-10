"""Unit tests for the OAuth authorization-server scaffold."""

from __future__ import annotations

import pytest

from vibrant.mcp.auth import (
    AuthUser,
    AuthorizationRequest,
    AuthorizationServerService,
    HMACTokenSigner,
    InMemoryAuthStore,
    OAuthClient,
    OAuthError,
    TokenExchangeRequest,
    build_s256_code_challenge,
)
from vibrant.mcp.auth.tokens import decode_unverified
from vibrant.mcp.config import OAuthServerSettings


@pytest.fixture
def auth_service() -> AuthorizationServerService:
    service = AuthorizationServerService(
        settings=OAuthServerSettings(issuer_url="https://auth.example.com"),
        store=InMemoryAuthStore(),
        signer=HMACTokenSigner("development-secret"),
    )
    service.register_user(
        AuthUser(
            user_id="user-123",
            username="alice",
            roles=["editor"],
            claims={"email": "alice@example.com"},
        )
    )
    service.register_client(
        OAuthClient(
            client_id="vibrant-mcp-server",
            redirect_uris=["https://mcp.example.com/callback"],
            allowed_scopes=["mcp:access", "tasks:read", "tasks:write"],
            is_public=True,
        )
    )
    return service


class TestAuthorizationServerService:
    def test_authorize_and_exchange_code(self, auth_service: AuthorizationServerService):
        code_verifier = "correct horse battery staple"
        decision = auth_service.authorize(
            AuthorizationRequest(
                client_id="vibrant-mcp-server",
                redirect_uri="https://mcp.example.com/callback",
                requested_scopes=["tasks:write"],
                state="opaque-state",
                code_challenge=build_s256_code_challenge(code_verifier),
            ),
            user_id="user-123",
        )

        token_bundle = auth_service.exchange_authorization_code(
            TokenExchangeRequest(
                code=decision.code,
                client_id="vibrant-mcp-server",
                redirect_uri="https://mcp.example.com/callback",
                code_verifier=code_verifier,
            )
        )
        claims = decode_unverified(token_bundle.access_token)

        assert token_bundle.scope == "mcp:access tasks:write"
        assert claims["iss"] == "https://auth.example.com"
        assert claims["aud"] == "vibrant-mcp"
        assert claims["sub"] == "user-123"
        assert claims["scope"] == "mcp:access tasks:write"
        assert claims["roles"] == ["editor"]
        assert claims["email"] == "alice@example.com"

    def test_rejects_unknown_redirect_uri(self, auth_service: AuthorizationServerService):
        with pytest.raises(OAuthError, match="redirect_uri does not match"):
            auth_service.authorize(
                AuthorizationRequest(
                    client_id="vibrant-mcp-server",
                    redirect_uri="https://evil.example.com/callback",
                    requested_scopes=["tasks:read"],
                    code_challenge="challenge",
                ),
                user_id="user-123",
            )

    def test_rejects_invalid_pkce_verifier(self, auth_service: AuthorizationServerService):
        decision = auth_service.authorize(
            AuthorizationRequest(
                client_id="vibrant-mcp-server",
                redirect_uri="https://mcp.example.com/callback",
                requested_scopes=["tasks:read"],
                code_challenge=build_s256_code_challenge("expected"),
            ),
            user_id="user-123",
        )

        with pytest.raises(OAuthError, match="code_verifier does not match"):
            auth_service.exchange_authorization_code(
                TokenExchangeRequest(
                    code=decision.code,
                    client_id="vibrant-mcp-server",
                    redirect_uri="https://mcp.example.com/callback",
                    code_verifier="unexpected",
                )
            )

    def test_metadata_document_lists_standard_endpoints(self, auth_service: AuthorizationServerService):
        metadata = auth_service.metadata_document()

        assert metadata["issuer"] == "https://auth.example.com"
        assert metadata["authorization_endpoint"] == "https://auth.example.com/authorize"
        assert metadata["token_endpoint"] == "https://auth.example.com/token"
        assert metadata["jwks_uri"] == "https://auth.example.com/.well-known/jwks.json"
        assert metadata["response_types_supported"] == ["code"]
        assert "mcp:access" in metadata["scopes_supported"]
