# Embedded Auth for Vibrant

> **Status**: implementation guide
> **Date**: 2026-03-10

## What it is

Vibrant's embedded auth layer is a small OAuth 2.1 authorization server that lives inside the same Python codebase as the MCP server.

It is built from:

- **Authlib primitives** for OAuth protocol behavior
- **our own policy layer** for role-to-scope mapping
- **our own storage layer** for users, clients, authorization codes, and issued tokens
- a thin **FastAPI adapter** so the embedded service can be exposed as HTTP endpoints

This is **not** a full identity platform. It is a focused embedded authorization server for MCP.

## What it does

The embedded auth service handles:

- OAuth authorization code flow
- PKCE validation through Authlib
- token issuance
- authorization server metadata
- JWKS exposure through the configured signer
- role-based scope calculation before tokens are issued

The main entry point is `AuthorizationServerService` in `vibrant/mcp/auth/service.py`.

## File layout

The simplified auth package is:

- `vibrant/mcp/auth/service.py` — high-level embedded auth API, powered by Authlib
- `vibrant/mcp/auth/models.py` — user, client, code, and request models
- `vibrant/mcp/auth/store.py` — storage interface plus in-memory dev store
- `vibrant/mcp/auth/tokens.py` — token signer interface and dev signer
- `vibrant/mcp/auth/fastapi_app.py` — optional FastAPI wrapper
- `vibrant/mcp/authz.py` — auth-related scope constants and default role mapping
- `vibrant/mcp/config.py` — auth server configuration model
- `vibrant/mcp/auth/__init__.py` — convenience exports

## How it fits together

```text
FastAPI route layer
        |
        v
create_auth_app(...)
        |
        v
AuthorizationServerService
        |
        +--> Authlib AuthorizationServer + AuthorizationCodeGrant
        +--> AuthStore
        +--> TokenSigner
        +--> role -> scope policy
```

## Minimal usage

```python
from vibrant.mcp.auth import (
    AuthUser,
    AuthorizationServerService,
    HMACTokenSigner,
    InMemoryAuthStore,
    OAuthClient,
)
from vibrant.mcp.config import OAuthServerSettings

store = InMemoryAuthStore()
signer = HMACTokenSigner("dev-secret")
settings = OAuthServerSettings(issuer_url="https://auth.example.com")

service = AuthorizationServerService(
    settings=settings,
    store=store,
    signer=signer,
)

service.register_user(
    AuthUser(
        user_id="user-123",
        username="alice",
        roles=["editor"],
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
```

After that, the service can:

- approve an authorization request with `service.authorize(...)`
- exchange a code for a token with `service.exchange_authorization_code(...)`
- expose metadata with `service.metadata_document()`
- expose keys with `service.jwks_document()`

## FastAPI usage

Use `create_auth_app(...)` when you want real HTTP endpoints.

```python
from vibrant.mcp.auth import create_auth_app


def resolve_current_user(request):
    return "user-123"

app = create_auth_app(
    service,
    resolve_current_user=resolve_current_user,
    title="Vibrant Auth",
)
```

This exposes:

- `GET /.well-known/oauth-authorization-server`
- `GET /.well-known/jwks.json`
- `GET /authorize`
- `POST /token`

## How login works

The embedded auth service does **not** implement a login UI.

Instead, the hosting app must tell it who the current user is.

That is why `create_auth_app(...)` takes `resolve_current_user`. The expected flow is:

1. your app authenticates the user using your own session logic
2. `resolve_current_user` returns the current user or user ID
3. the embedded auth service uses that identity to authorize the OAuth request

## How RBAC works

OAuth protocol handling comes from Authlib.

RBAC policy is still ours.

The service expands roles into scopes and computes:

```text
final_scopes = requested_scopes ∩ client_allowed_scopes ∩ user_allowed_scopes
```

Then it enforces any baseline scopes such as `mcp:access`.

## MCP integration

This package intentionally stops at the auth boundary.

It provides an embedded OAuth authorization server that a future MCP layer can integrate with later.

That future MCP layer should consume:

- the issuer URL
- the authorization endpoint
- the token endpoint
- the JWKS endpoint
- the scope model defined here

The actual MCP server wiring is intentionally deferred.

## Development vs production

Current dev-friendly pieces:

- `InMemoryAuthStore` is only for local development and tests
- `HMACTokenSigner` is only a dev signer

Before production use, replace them with:

- persistent storage, such as SQLite or Postgres
- an asymmetric signer with a real JWKS document

## When to touch which file

- change OAuth flow behavior: `vibrant/mcp/auth/service.py`
- change users/clients/code persistence: `vibrant/mcp/auth/store.py`
- change client or code shape: `vibrant/mcp/auth/models.py`
- change token signing: `vibrant/mcp/auth/tokens.py`
- change HTTP exposure: `vibrant/mcp/auth/fastapi_app.py`

## Summary

The embedded auth package is intentionally small:

- one high-level auth service
- one optional HTTP wrapper
- one storage boundary
- one signer boundary
- one auth-specific config model
- one place for role-to-scope policy

That keeps OAuth protocol complexity inside Authlib while keeping Vibrant's authorization rules inside Vibrant, without bundling premature MCP server integration.
