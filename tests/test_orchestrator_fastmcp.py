from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from vibrant.config import RoadmapExecutionMode
from vibrant.mcp.auth import (
    AuthUser,
    AuthorizationRequest,
    AuthorizationServerService,
    HMACTokenSigner,
    InMemoryAuthStore,
    OAuthClient,
)
from vibrant.mcp.config import OAuthServerSettings
from vibrant.orchestrator.state.backend import OrchestratorStateBackend
from vibrant.orchestrator.facade import OrchestratorFacade
from vibrant.orchestrator.mcp import EmbeddedOAuthProvider, OrchestratorMCPServer, create_orchestrator_fastmcp, create_orchestrator_fastmcp_app
from vibrant.orchestrator.artifacts import ConsensusService, QuestionService, RoadmapService
from vibrant.orchestrator.state import StateStore
from vibrant.project_init import initialize_project


class _StubGatekeeper:
    async def answer_question(self, question: str, answer: str):  # pragma: no cover - not exercised here
        raise NotImplementedError


def _build_facade(tmp_path: Path) -> OrchestratorFacade:
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_project(repo)

    engine = OrchestratorStateBackend.load(repo, notification_bell_enabled=False)
    state_store = StateStore(engine)
    roadmap_service = RoadmapService(repo / ".vibrant" / "roadmap.md", project_name=repo.name)
    roadmap_service.reload(project_name=repo.name, concurrency_limit=engine.state.concurrency_limit)
    consensus_service = ConsensusService(repo / ".vibrant" / "consensus.md", state_store=state_store)
    question_service = QuestionService(state_store=state_store, gatekeeper=_StubGatekeeper())
    lifecycle = SimpleNamespace(
        project_root=repo,
        engine=engine,
        state_store=state_store,
        roadmap_service=roadmap_service,
        consensus_service=consensus_service,
        question_service=question_service,
        execution_mode=RoadmapExecutionMode.MANUAL,
    )
    return OrchestratorFacade(lifecycle)


@pytest.fixture
def auth_service() -> AuthorizationServerService:
    service = AuthorizationServerService(
        settings=OAuthServerSettings(issuer_url="https://auth.example.com"),
        store=InMemoryAuthStore(),
        signer=HMACTokenSigner("development-secret"),
    )
    service.register_user(
        AuthUser(
            user_id="gatekeeper-1",
            username="gatekeeper",
            roles=["gatekeeper"],
        )
    )
    service.register_client(
        OAuthClient(
            client_id="vibrant-mcp-server",
            redirect_uris=["https://mcp.example.com/callback"],
            allowed_scopes=[
                "mcp:access",
                "tasks:read",
                "tasks:write",
                "tasks:run",
                "orchestrator:consensus:read",
                "orchestrator:consensus:write",
                "orchestrator:questions:read",
                "orchestrator:questions:write",
                "orchestrator:workflow:read",
                "orchestrator:workflow:write",
            ],
            is_public=True,
        )
    )
    return service


@pytest.mark.asyncio
async def test_embedded_oauth_provider_verifies_service_tokens(auth_service: AuthorizationServerService) -> None:
    token_bundle = auth_service.mint_token_for_subject(
        subject_id="gatekeeper-1",
        client_id="vibrant-mcp-server",
        requested_scopes=["tasks:write", "orchestrator:consensus:write"],
    )
    provider = EmbeddedOAuthProvider(
        service=auth_service,
        base_url="https://mcp.example.com",
        resolve_current_user=lambda _request: "gatekeeper-1",
    )

    token = await provider.verify_token(token_bundle.access_token)

    assert token is not None
    assert token.client_id == "vibrant-mcp-server"
    assert token.claims["sub"] == "gatekeeper-1"
    assert "mcp:access" in token.scopes
    assert "tasks:write" in token.scopes
    assert "orchestrator:consensus:write" in token.scopes


@pytest.mark.asyncio
async def test_create_orchestrator_fastmcp_registers_tools_and_resources(
    tmp_path: Path,
    auth_service: AuthorizationServerService,
) -> None:
    registry = OrchestratorMCPServer(_build_facade(tmp_path))
    provider = EmbeddedOAuthProvider(
        service=auth_service,
        base_url="https://mcp.example.com",
        resolve_current_user=lambda _request: "gatekeeper-1",
    )

    server = create_orchestrator_fastmcp(registry, auth=provider)

    assert server.auth is provider
    assert await server._local_provider.get_tool("roadmap_add_task") is not None
    assert await server._local_provider.get_tool("vibrant.update_roadmap") is not None
    assert await server._local_provider.get_resource("vibrant://consensus/current") is not None
    assert await server._local_provider.get_resource_template("vibrant://task/{task_id}") is not None


@pytest.mark.asyncio
async def test_create_orchestrator_fastmcp_uses_local_principal_without_http_auth_context(
    tmp_path: Path,
    auth_service: AuthorizationServerService,
) -> None:
    registry = OrchestratorMCPServer(_build_facade(tmp_path))
    provider = EmbeddedOAuthProvider(
        service=auth_service,
        base_url="https://mcp.example.com",
        resolve_current_user=lambda _request: "gatekeeper-1",
    )

    server = create_orchestrator_fastmcp(registry, auth=provider)
    tool = await server._local_provider.get_tool("roadmap_get")

    result = await tool.run({})

    assert result is not None


def test_create_orchestrator_fastmcp_app_exposes_auth_and_resource_routes(
    tmp_path: Path,
    auth_service: AuthorizationServerService,
) -> None:
    registry = OrchestratorMCPServer(_build_facade(tmp_path))
    provider = EmbeddedOAuthProvider(
        service=auth_service,
        base_url="https://mcp.example.com",
        resolve_current_user=lambda _request: "gatekeeper-1",
    )
    app = create_orchestrator_fastmcp_app(registry, auth=provider, mcp_path="/mcp")

    route_paths = {getattr(route, "path", None) for route in app.routes}
    assert auth_service.settings.metadata_endpoint in route_paths
    assert auth_service.settings.jwks_endpoint in route_paths
    assert auth_service.settings.authorization_endpoint in route_paths
    assert auth_service.settings.token_endpoint in route_paths
    assert "/mcp" in route_paths
    assert any(path and "oauth-protected-resource" in path for path in route_paths)

    with TestClient(app) as client:
        metadata = client.get(auth_service.settings.metadata_endpoint)

    assert metadata.status_code == 200
    assert metadata.json()["issuer"] == "https://auth.example.com"


def test_authorize_preserves_existing_redirect_query_params(
    tmp_path: Path,
    auth_service: AuthorizationServerService,
) -> None:
    redirect_uri = "https://client.example.com/callback?foo=bar"
    auth_service.register_client(
        OAuthClient(
            client_id="query-client",
            redirect_uris=[redirect_uri],
            allowed_scopes=["mcp:access", "tasks:read"],
            is_public=True,
            require_pkce=False,
        )
    )
    registry = OrchestratorMCPServer(_build_facade(tmp_path))
    provider = EmbeddedOAuthProvider(
        service=auth_service,
        base_url="https://mcp.example.com",
        resolve_current_user=lambda _request: "gatekeeper-1",
    )
    app = create_orchestrator_fastmcp_app(registry, auth=provider, mcp_path="/mcp")

    with TestClient(app) as client:
        response = client.get(
            auth_service.settings.authorization_endpoint,
            params={
                "client_id": "query-client",
                "redirect_uri": redirect_uri,
                "scope": "tasks:read",
                "state": "opaque-state",
            },
            follow_redirects=False,
        )

    assert response.status_code in {302, 307}
    location = response.headers["location"]
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://client.example.com/callback"
    assert query == {
        "foo": ["bar"],
        "code": [query["code"][0]],
        "state": ["opaque-state"],
    }


def test_token_accepts_client_secret_basic(
    tmp_path: Path,
    auth_service: AuthorizationServerService,
) -> None:
    redirect_uri = "https://client.example.com/callback"
    auth_service.register_client(
        OAuthClient(
            client_id="confidential-client",
            client_secret="top-secret",
            redirect_uris=[redirect_uri],
            allowed_scopes=["mcp:access", "tasks:read"],
            is_public=False,
            require_pkce=False,
            token_endpoint_auth_method="client_secret_basic",
        )
    )
    decision = auth_service.authorize(
        AuthorizationRequest(
            client_id="confidential-client",
            redirect_uri=redirect_uri,
            requested_scopes="tasks:read",
        ),
        user_id="gatekeeper-1",
    )
    registry = OrchestratorMCPServer(_build_facade(tmp_path))
    provider = EmbeddedOAuthProvider(
        service=auth_service,
        base_url="https://mcp.example.com",
        resolve_current_user=lambda _request: "gatekeeper-1",
    )
    app = create_orchestrator_fastmcp_app(registry, auth=provider, mcp_path="/mcp")
    basic = base64.b64encode(b"confidential-client:top-secret").decode("ascii")

    with TestClient(app) as client:
        response = client.post(
            auth_service.settings.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": decision.code,
                "redirect_uri": redirect_uri,
            },
            headers={"Authorization": f"Basic {basic}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
