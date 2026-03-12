"""FastMCP adapter for the orchestrator MCP surface."""

from __future__ import annotations

import base64
import binascii
import inspect
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError
from vibrant.mcp.auth import AuthUser, AuthorizationRequest, AuthorizationServerService, OAuthError, TokenExchangeRequest
from vibrant.mcp.auth.service import normalize_scopes
from vibrant.mcp.authz import MCPPrincipal

from .server import OrchestratorMCPServer

try:  # pragma: no cover - optional dependency at runtime
    from fastmcp import FastMCP
    from fastmcp.server.auth import AccessToken, RemoteAuthProvider, TokenVerifier, require_scopes
    from mcp.server.auth.middleware.auth_context import get_access_token
    from pydantic import AnyHttpUrl
    from starlette.requests import Request
    from starlette.responses import JSONResponse, RedirectResponse
    from starlette.routing import Route
except ModuleNotFoundError:  # pragma: no cover - optional dependency at runtime
    FastMCP = Any  # type: ignore[assignment]
    AccessToken = Any  # type: ignore[assignment]
    RemoteAuthProvider = object  # type: ignore[assignment]
    TokenVerifier = object  # type: ignore[assignment]
    Route = Any  # type: ignore[assignment]
    Request = Any  # type: ignore[assignment]
    JSONResponse = Any  # type: ignore[assignment]
    RedirectResponse = Any  # type: ignore[assignment]
    AnyHttpUrl = str  # type: ignore[assignment]

    def require_scopes(*_scopes: str) -> Any:
        raise ModuleNotFoundError(
            "FastMCP is not installed. Install the optional server dependencies, for example: uv add --optional mcp 'fastmcp>=3.0'"
        )

    def get_access_token() -> Any:
        raise ModuleNotFoundError(
            "FastMCP is not installed. Install the optional server dependencies, for example: uv add --optional mcp 'fastmcp>=3.0'"
        )


CurrentUserResolver = Callable[[Any], str | AuthUser | Awaitable[str | AuthUser]]


class EmbeddedOAuthTokenVerifier(TokenVerifier):
    """FastMCP token verifier backed by Vibrant's embedded OAuth service."""

    def __init__(
        self,
        service: AuthorizationServerService,
        *,
        base_url: AnyHttpUrl | str | None = None,
        required_scopes: list[str] | None = None,
        audience: str | None = None,
    ) -> None:
        super().__init__(base_url=base_url, required_scopes=required_scopes)
        self.service = service
        self.audience = audience or service.settings.default_audience

    @property
    def scopes_supported(self) -> list[str]:
        metadata = self.service.metadata_document()
        supported = metadata.get("scopes_supported", [])
        return [str(scope) for scope in supported]

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = self.service.verify_access_token(token, audience=self.audience)
        except OAuthError:
            return None
        return access_token_from_claims(token, claims)


class EmbeddedOAuthProvider(RemoteAuthProvider):
    """FastMCP auth provider that serves embedded OAuth endpoints and verifies tokens."""

    def __init__(
        self,
        *,
        service: AuthorizationServerService,
        base_url: AnyHttpUrl | str,
        resolve_current_user: CurrentUserResolver | None = None,
        required_scopes: list[str] | None = None,
        audience: str | None = None,
        resource_name: str | None = "Vibrant MCP",
        resource_documentation: AnyHttpUrl | None = None,
    ) -> None:
        verifier = EmbeddedOAuthTokenVerifier(
            service,
            base_url=base_url,
            required_scopes=required_scopes or list(service.settings.baseline_scopes),
            audience=audience,
        )
        super().__init__(
            token_verifier=verifier,
            authorization_servers=[AnyHttpUrl(service.settings.issuer_url)],
            base_url=base_url,
            scopes_supported=verifier.scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
        )
        self.service = service
        self.resolve_current_user = resolve_current_user

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = list(super().get_routes(mcp_path))
        routes.extend(
            [
                Route(self.service.settings.metadata_endpoint, endpoint=self._metadata, methods=["GET"]),
                Route(self.service.settings.jwks_endpoint, endpoint=self._jwks, methods=["GET"]),
                Route(self.service.settings.authorization_endpoint, endpoint=self._authorize, methods=["GET"]),
                Route(self.service.settings.token_endpoint, endpoint=self._token, methods=["POST"]),
            ]
        )
        return routes

    async def _metadata(self, _request: Request) -> JSONResponse:
        return JSONResponse(self.service.metadata_document())

    async def _jwks(self, _request: Request) -> JSONResponse:
        return JSONResponse(self.service.jwks_document())

    async def _authorize(self, request: Request) -> RedirectResponse | JSONResponse:
        try:
            user_id = await self._resolve_current_user(request)
            auth_request = AuthorizationRequest(
                client_id=request.query_params["client_id"],
                redirect_uri=request.query_params["redirect_uri"],
                requested_scopes=request.query_params.get("scope", ""),
                state=request.query_params.get("state"),
                code_challenge=request.query_params.get("code_challenge"),
                code_challenge_method=request.query_params.get("code_challenge_method", "S256"),
                audience=request.query_params.get("audience"),
                response_type=request.query_params.get("response_type", "code"),
            )
            decision = self.service.authorize(auth_request, user_id=user_id)
        except KeyError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "error_description": f"Missing required query parameter: {exc.args[0]}",
                },
            )
        except OAuthError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

        redirect_query = {"code": decision.code}
        if decision.state is not None:
            redirect_query["state"] = decision.state
        target = _append_query_params(decision.redirect_uri, redirect_query)
        return RedirectResponse(target)

    async def _token(self, request: Request) -> JSONResponse:
        try:
            payload = await _parse_request_payload(request)
            token_request = TokenExchangeRequest.model_validate(payload)
            bundle = self.service.exchange_authorization_code(token_request)
            return JSONResponse(bundle.model_dump(exclude_none=True))
        except ValidationError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "error_description": _format_validation_error(exc),
                },
            )
        except OAuthError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    async def _resolve_current_user(self, request: Request) -> str:
        if self.resolve_current_user is None:
            raise OAuthError(
                "server_error",
                "No user resolver has been configured for the authorization endpoint",
                status_code=501,
            )
        resolved = self.resolve_current_user(request)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        if isinstance(resolved, AuthUser):
            return resolved.user_id
        if isinstance(resolved, str):
            return resolved
        raise OAuthError(
            "server_error",
            "resolve_current_user returned an unsupported value",
            status_code=500,
        )


def access_token_from_claims(token: str, claims: dict[str, Any]) -> AccessToken:
    scopes = list(normalize_scopes(claims.get("scope")))
    expires_at = claims.get("exp")
    return AccessToken(
        token=token,
        client_id=str(claims["client_id"]),
        scopes=scopes,
        expires_at=expires_at if isinstance(expires_at, int) else None,
        resource=str(claims["aud"]) if claims.get("aud") is not None else None,
        claims=dict(claims),
    )


def current_principal(*, fallback_principal: MCPPrincipal | None = None) -> MCPPrincipal:
    token = get_access_token()
    if token is None:
        if fallback_principal is not None:
            return fallback_principal
        raise PermissionError("Missing authenticated access token")
    claims = getattr(token, "claims", {}) or {}
    subject_id = claims.get("sub")
    return MCPPrincipal(scopes=tuple(token.scopes), subject_id=str(subject_id) if subject_id is not None else None)


def create_orchestrator_fastmcp(
    registry: OrchestratorMCPServer,
    *,
    auth: EmbeddedOAuthProvider | None = None,
    local_principal: MCPPrincipal | None = None,
    name: str = "Vibrant Orchestrator",
    instructions: str | None = None,
) -> FastMCP:
    if FastMCP is Any:  # pragma: no cover - optional dependency at runtime
        raise ModuleNotFoundError(
            "FastMCP is not installed. Install the optional server dependencies, for example: uv add --optional mcp 'fastmcp>=3.0'"
        )

    server = FastMCP(name=name, instructions=instructions, auth=auth)
    baseline_scopes = tuple(auth.required_scopes) if auth is not None else ()
    fallback_principal = local_principal or _trusted_local_principal(registry)

    def component_auth(required_scopes: tuple[str, ...]) -> Any:
        extra_scopes = [scope for scope in required_scopes if scope not in baseline_scopes]
        if not extra_scopes:
            return None
        return require_scopes(*extra_scopes)

    def principal() -> MCPPrincipal:
        return current_principal(fallback_principal=fallback_principal)

    @server.resource(
        "vibrant://consensus/current",
        name="consensus.current",
        description=registry.get_resource_definition("consensus.current").description,
        auth=component_auth(registry.get_resource_definition("consensus.current").required_scopes),
    )
    async def consensus_current() -> dict[str, Any] | None:
        return await registry.read_resource("consensus.current", principal=principal())

    @server.resource(
        "vibrant://roadmap/current",
        name="roadmap.current",
        description=registry.get_resource_definition("roadmap.current").description,
        auth=component_auth(registry.get_resource_definition("roadmap.current").required_scopes),
    )
    async def roadmap_current() -> dict[str, Any] | None:
        return await registry.read_resource("roadmap.current", principal=principal())

    @server.resource(
        "vibrant://workflow/status",
        name="workflow.status",
        description=registry.get_resource_definition("workflow.status").description,
        auth=component_auth(registry.get_resource_definition("workflow.status").required_scopes),
    )
    async def workflow_status() -> dict[str, Any]:
        return await registry.read_resource("workflow.status", principal=principal())

    @server.resource(
        "vibrant://questions/pending",
        name="questions.pending",
        description=registry.get_resource_definition("questions.pending").description,
        auth=component_auth(registry.get_resource_definition("questions.pending").required_scopes),
    )
    async def questions_pending() -> list[dict[str, Any]]:
        return await registry.read_resource("questions.pending", principal=principal())

    @server.resource(
        "vibrant://task/{task_id}",
        name="task.by_id",
        description=registry.get_resource_definition("task.by_id").description,
        auth=component_auth(registry.get_resource_definition("task.by_id").required_scopes),
    )
    async def task_by_id(task_id: str) -> dict[str, Any]:
        return await registry.read_resource("task.by_id", principal=principal(), task_id=task_id)

    @server.resource(
        "vibrant://task/{task_id}/assigned",
        name="task.assigned",
        description=registry.get_resource_definition("task.assigned").description,
        auth=component_auth(registry.get_resource_definition("task.assigned").required_scopes),
    )
    async def task_assigned(task_id: str) -> dict[str, Any]:
        return await registry.read_resource("task.assigned", principal=principal(), task_id=task_id)

    @server.resource(
        "vibrant://agent/{agent_id}/status",
        name="agent.status",
        description=registry.get_resource_definition("agent.status").description,
        auth=component_auth(registry.get_resource_definition("agent.status").required_scopes),
    )
    async def agent_status(agent_id: str) -> dict[str, Any] | list[dict[str, Any]]:
        return await registry.read_resource("agent.status", principal=principal(), agent_id=agent_id)

    @server.resource(
        "vibrant://events/recent/{task_id}{?limit}",
        name="events.recent",
        description=registry.get_resource_definition("events.recent").description,
        auth=component_auth(registry.get_resource_definition("events.recent").required_scopes),
    )
    async def events_recent(task_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return await registry.read_resource(
            "events.recent",
            principal=principal(),
            task_id=task_id,
            limit=limit,
        )

    @server.tool(
        name="agent_get",
        description=registry.get_tool_definition("agent_get").description,
        auth=component_auth(registry.get_tool_definition("agent_get").required_scopes),
    )
    async def agent_get(agent_id: str) -> dict[str, Any]:
        return await registry.call_tool("agent_get", principal=principal(), agent_id=agent_id)

    @server.tool(
        name="agent_list",
        description=registry.get_tool_definition("agent_list").description,
        auth=component_auth(registry.get_tool_definition("agent_list").required_scopes),
    )
    async def agent_list(
        task_id: str | None = None,
        include_completed: bool = True,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        return await registry.call_tool(
            "agent_list",
            principal=principal(),
            task_id=task_id,
            include_completed=include_completed,
            active_only=active_only,
        )

    @server.tool(
        name="agent_result_get",
        description=registry.get_tool_definition("agent_result_get").description,
        auth=component_auth(registry.get_tool_definition("agent_result_get").required_scopes),
    )
    async def agent_result_get(agent_id: str) -> dict[str, Any]:
        return await registry.call_tool("agent_result_get", principal=principal(), agent_id=agent_id)

    @server.tool(
        name="agent_respond_to_request",
        description=registry.get_tool_definition("agent_respond_to_request").description,
        auth=component_auth(registry.get_tool_definition("agent_respond_to_request").required_scopes),
    )
    async def agent_respond_to_request(
        agent_id: str,
        request_id: int | str,
        result: Any | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "agent_respond_to_request",
            principal=principal(),
            agent_id=agent_id,
            request_id=request_id,
            result=result,
            error=error,
        )

    @server.tool(
        name="agent_wait",
        description=registry.get_tool_definition("agent_wait").description,
        auth=component_auth(registry.get_tool_definition("agent_wait").required_scopes),
    )
    async def agent_wait(agent_id: str, release_terminal: bool = True) -> dict[str, Any]:
        return await registry.call_tool(
            "agent_wait",
            principal=principal(),
            agent_id=agent_id,
            release_terminal=release_terminal,
        )

    @server.tool(
        name="consensus_get",
        description=registry.get_tool_definition("consensus_get").description,
        auth=component_auth(registry.get_tool_definition("consensus_get").required_scopes),
    )
    async def consensus_get() -> dict[str, Any] | None:
        return await registry.call_tool("consensus_get", principal=principal())

    @server.tool(
        name="consensus_update",
        description=registry.get_tool_definition("consensus_update").description,
        auth=component_auth(registry.get_tool_definition("consensus_update").required_scopes),
    )
    async def consensus_update(
        status: str | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "consensus_update",
            principal=principal(),
            status=status,
            context=context,
        )

    @server.tool(
        name="question_ask_user",
        description=registry.get_tool_definition("question_ask_user").description,
        auth=component_auth(registry.get_tool_definition("question_ask_user").required_scopes),
    )
    async def question_ask_user(
        text: str,
        source_agent_id: str | None = None,
        priority: str = "blocking",
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "question_ask_user",
            principal=principal(),
            text=text,
            source_agent_id=source_agent_id,
            priority=priority,
        )

    @server.tool(
        name="question_resolve",
        description=registry.get_tool_definition("question_resolve").description,
        auth=component_auth(registry.get_tool_definition("question_resolve").required_scopes),
    )
    async def question_resolve(question_id: str, answer: str | None = None) -> dict[str, Any]:
        return await registry.call_tool(
            "question_resolve",
            principal=principal(),
            question_id=question_id,
            answer=answer,
        )

    @server.tool(
        name="roadmap_add_task",
        description=registry.get_tool_definition("roadmap_add_task").description,
        auth=component_auth(registry.get_tool_definition("roadmap_add_task").required_scopes),
    )
    async def roadmap_add_task(task: dict[str, Any], index: int | None = None) -> dict[str, Any]:
        return await registry.call_tool(
            "roadmap_add_task",
            principal=principal(),
            task=task,
            index=index,
        )

    @server.tool(
        name="roadmap_get",
        description=registry.get_tool_definition("roadmap_get").description,
        auth=component_auth(registry.get_tool_definition("roadmap_get").required_scopes),
    )
    async def roadmap_get() -> dict[str, Any] | None:
        return await registry.call_tool("roadmap_get", principal=principal())

    @server.tool(
        name="roadmap_reorder_tasks",
        description=registry.get_tool_definition("roadmap_reorder_tasks").description,
        auth=component_auth(registry.get_tool_definition("roadmap_reorder_tasks").required_scopes),
    )
    async def roadmap_reorder_tasks(ordered_task_ids: list[str]) -> dict[str, Any]:
        return await registry.call_tool(
            "roadmap_reorder_tasks",
            principal=principal(),
            ordered_task_ids=ordered_task_ids,
        )

    @server.tool(
        name="roadmap_update_task",
        description=registry.get_tool_definition("roadmap_update_task").description,
        auth=component_auth(registry.get_tool_definition("roadmap_update_task").required_scopes),
    )
    async def roadmap_update_task(task_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        return await registry.call_tool(
            "roadmap_update_task",
            principal=principal(),
            task_id=task_id,
            updates=updates,
        )

    @server.tool(
        name="task_get",
        description=registry.get_tool_definition("task_get").description,
        auth=component_auth(registry.get_tool_definition("task_get").required_scopes),
    )
    async def task_get(task_id: str) -> dict[str, Any]:
        return await registry.call_tool("task_get", principal=principal(), task_id=task_id)

    @server.tool(
        name="workflow_execute_next_task",
        description=registry.get_tool_definition("workflow_execute_next_task").description,
        auth=component_auth(registry.get_tool_definition("workflow_execute_next_task").required_scopes),
    )
    async def workflow_execute_next_task() -> dict[str, Any] | None:
        return await registry.call_tool("workflow_execute_next_task", principal=principal())

    @server.tool(
        name="workflow_pause",
        description=registry.get_tool_definition("workflow_pause").description,
        auth=component_auth(registry.get_tool_definition("workflow_pause").required_scopes),
    )
    async def workflow_pause() -> dict[str, Any]:
        return await registry.call_tool("workflow_pause", principal=principal())

    @server.tool(
        name="workflow_resume",
        description=registry.get_tool_definition("workflow_resume").description,
        auth=component_auth(registry.get_tool_definition("workflow_resume").required_scopes),
    )
    async def workflow_resume() -> dict[str, Any]:
        return await registry.call_tool("workflow_resume", principal=principal())

    @server.tool(
        name="vibrant.end_planning_phase",
        description=registry.get_tool_definition("vibrant.end_planning_phase").description,
        auth=component_auth(registry.get_tool_definition("vibrant.end_planning_phase").required_scopes),
    )
    async def end_planning_phase() -> dict[str, Any]:
        return await registry.call_tool("vibrant.end_planning_phase", principal=principal())

    @server.tool(
        name="vibrant.request_user_decision",
        description=registry.get_tool_definition("vibrant.request_user_decision").description,
        auth=component_auth(registry.get_tool_definition("vibrant.request_user_decision").required_scopes),
    )
    async def request_user_decision(
        question: str,
        source_agent_id: str | None = None,
        priority: str = "blocking",
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.request_user_decision",
            principal=principal(),
            question=question,
            source_agent_id=source_agent_id,
            priority=priority,
        )

    @server.tool(
        name="vibrant.set_pending_questions",
        description=registry.get_tool_definition("vibrant.set_pending_questions").description,
        auth=component_auth(registry.get_tool_definition("vibrant.set_pending_questions").required_scopes),
    )
    async def set_pending_questions(
        questions: list[str],
        source_agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await registry.call_tool(
            "vibrant.set_pending_questions",
            principal=principal(),
            questions=questions,
            source_agent_id=source_agent_id,
        )

    @server.tool(
        name="vibrant.review_task_outcome",
        description=registry.get_tool_definition("vibrant.review_task_outcome").description,
        auth=component_auth(registry.get_tool_definition("vibrant.review_task_outcome").required_scopes),
    )
    async def review_task_outcome(
        task_id: str,
        decision: str,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.review_task_outcome",
            principal=principal(),
            task_id=task_id,
            decision=decision,
            failure_reason=failure_reason,
        )

    @server.tool(
        name="vibrant.mark_task_for_retry",
        description=registry.get_tool_definition("vibrant.mark_task_for_retry").description,
        auth=component_auth(registry.get_tool_definition("vibrant.mark_task_for_retry").required_scopes),
    )
    async def mark_task_for_retry(
        task_id: str,
        failure_reason: str,
        prompt: str | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.mark_task_for_retry",
            principal=principal(),
            task_id=task_id,
            failure_reason=failure_reason,
            prompt=prompt,
            acceptance_criteria=acceptance_criteria,
        )

    @server.tool(
        name="vibrant.update_consensus",
        description=registry.get_tool_definition("vibrant.update_consensus").description,
        auth=component_auth(registry.get_tool_definition("vibrant.update_consensus").required_scopes),
    )
    async def update_consensus(
        status: str | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.update_consensus",
            principal=principal(),
            status=status,
            context=context,
        )

    @server.tool(
        name="vibrant.update_roadmap",
        description=registry.get_tool_definition("vibrant.update_roadmap").description,
        auth=component_auth(registry.get_tool_definition("vibrant.update_roadmap").required_scopes),
    )
    async def update_roadmap(tasks: list[dict[str, Any]], project: str | None = None) -> dict[str, Any]:
        return await registry.call_tool(
            "vibrant.update_roadmap",
            principal=principal(),
            tasks=tasks,
            project=project,
        )

    return server


def create_orchestrator_fastmcp_app(
    registry: OrchestratorMCPServer,
    *,
    auth: EmbeddedOAuthProvider,
    mcp_path: str = "/mcp",
    name: str = "Vibrant Orchestrator",
    instructions: str | None = None,
    transport: str = "http",
) -> Any:
    server = create_orchestrator_fastmcp(
        registry,
        auth=auth,
        name=name,
        instructions=instructions,
    )
    return server.http_app(path=mcp_path, transport=transport)


async def _parse_request_payload(request: Any) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = dict(await request.json())
    else:
        raw_body = (await request.body()).decode("utf-8")
        parsed = parse_qs(raw_body, keep_blank_values=True)
        payload = {key: values[-1] for key, values in parsed.items()}

    client_id, client_secret = _parse_basic_client_credentials(request.headers.get("authorization"))
    if client_id is None:
        return payload
    if payload.get("client_secret") not in {None, ""}:
        raise OAuthError(
            "invalid_request",
            "Multiple client authentication methods are not allowed",
            status_code=400,
        )
    payload_client_id = payload.get("client_id")
    if payload_client_id not in {None, "", client_id}:
        raise OAuthError(
            "invalid_request",
            "client_id does not match HTTP Basic credentials",
            status_code=400,
        )
    payload["client_id"] = client_id
    payload["client_secret"] = client_secret
    payload["client_auth_method"] = "client_secret_basic"
    return payload


def _append_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urlsplit(url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query_pairs.extend(params.items())
    return urlunsplit(parsed._replace(query=urlencode(query_pairs)))


def _format_validation_error(exc: ValidationError) -> str:
    errors: list[str] = []
    for issue in exc.errors():
        location = ".".join(str(part) for part in issue.get("loc", ())) or "request"
        message = issue.get("msg", "invalid value")
        errors.append(f"{location}: {message}")
    return "; ".join(errors) or "Token request payload is invalid"


def _parse_basic_client_credentials(header_value: str | None) -> tuple[str | None, str | None]:
    if not header_value:
        return None, None
    scheme, _, encoded_credentials = header_value.partition(" ")
    if scheme.lower() != "basic" or not encoded_credentials:
        return None, None
    try:
        decoded_credentials = base64.b64decode(encoded_credentials, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise OAuthError("invalid_client", "Invalid HTTP Basic credentials", status_code=401) from exc
    if ":" not in decoded_credentials:
        raise OAuthError("invalid_client", "Invalid HTTP Basic credentials", status_code=401)
    client_id, client_secret = decoded_credentials.split(":", 1)
    return unquote(client_id), unquote(client_secret)


def _trusted_local_principal(registry: OrchestratorMCPServer) -> MCPPrincipal:
    scopes: list[str] = []
    for definition in registry.resource_definitions():
        for scope in definition.required_scopes:
            if scope not in scopes:
                scopes.append(scope)
    for definition in registry.tool_definitions():
        for scope in definition.required_scopes:
            if scope not in scopes:
                scopes.append(scope)
    return MCPPrincipal(scopes=tuple(scopes), subject_id="local")


__all__ = [
    "EmbeddedOAuthProvider",
    "EmbeddedOAuthTokenVerifier",
    "access_token_from_claims",
    "create_orchestrator_fastmcp",
    "create_orchestrator_fastmcp_app",
    "current_principal",
]
