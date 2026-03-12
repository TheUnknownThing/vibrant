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

- direct internal token minting for spawned agents and other internal subjects
- OAuth authorization code flow when needed
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

- mint an internal token with `service.mint_token_for_subject(...)`
- mint a spawned-agent token with `service.mint_agent_token(...)`
- approve an authorization request with `service.authorize(...)`
- exchange a code for a token with `service.exchange_authorization_code(...)`
- expose metadata with `service.metadata_document()`
- expose keys with `service.jwks_document()`

## Internal agent usage

For spawned sub-agents, you usually do **not** want the agent itself to perform the OAuth HTTP flow.

Instead, the parent process should mint a token internally and hand it to the agent.

```python
auth_service.register_user(
    AuthUser(
        user_id="agent-task-001",
        username="agent-task-001",
        roles=["editor"],
    )
)

auth_service.register_client(
    OAuthClient(
        client_id="vibrant-subagent",
        allowed_scopes=["mcp:access", "tasks:read", "tasks:write"],
        is_public=True,
    )
)

token_bundle = auth_service.mint_agent_token(
    agent_id="agent-task-001",
    client_id="vibrant-subagent",
    requested_scopes=["tasks:write"],
)
```

In this flow:

- the parent process chooses the agent identity
- the auth service resolves roles into scopes
- the auth service returns an access token bundle
- the spawned agent receives the access token as runtime input

What the agent gets:

- MCP base URL from the orchestrator or runtime config
- bearer token from `token_bundle.access_token`
- optional expiry metadata from `token_bundle.expires_in`

What the agent later sends to the MCP server:

- normal MCP-over-HTTP requests
- `Authorization: Bearer <token>` on each request

This is the recommended path for internal agent-to-MCP access.

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

Instead, the hosting app must tell it which subject is acting. In the current code the subject model is named `AuthUser`, but for agent access you can treat it as a service principal.

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

The orchestrator MCP surface now integrates this package through FastMCP's native auth layer.

The relevant adapter lives in `vibrant/orchestrator/mcp/fastmcp.py` and does two things:

- serves the embedded OAuth endpoints from `AuthorizationServerService`
- verifies bearer tokens through FastMCP before dispatching MCP tools and resources

The runtime flow is:

1. the embedded OAuth service issues access tokens
2. `EmbeddedOAuthProvider` exposes OAuth metadata, authorization, token, and JWKS routes
3. FastMCP uses `EmbeddedOAuthTokenVerifier` to validate bearer tokens on MCP requests
4. the validated token is converted into an `MCPPrincipal` and the orchestrator registry still enforces scope checks internally

This keeps one shared scope model across token issuance and MCP authorization while using FastMCP's native auth middleware for transport-level enforcement.

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

## Manual testing

For practical local MCP server and Inspector workflows, see `docs/mcp_testing.md`.
