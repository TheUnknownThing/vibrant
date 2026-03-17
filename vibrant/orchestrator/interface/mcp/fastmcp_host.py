"""FastMCP transport host for the orchestrator semantic MCP backend."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.resources import ResourceContent, ResourceResult
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from vibrant.orchestrator.types import AgentMCPBinding

from .binding_registry import BINDING_HEADER_NAME, MCPBindingRegistry, RegisteredMCPBinding
from .common import MCPResourceDefinition, MCPToolDefinition
from .server import OrchestratorMCPServer
from .transport import LoopbackHTTPTransport


_RESOURCE_URIS: dict[str, str] = {
    "vibrant.get_consensus": "vibrant://consensus",
    "vibrant.get_roadmap": "vibrant://roadmap",
    "vibrant.get_task": "vibrant://tasks/{task_id}",
    "vibrant.get_workflow_status": "vibrant://workflow-status",
    "vibrant.get_workflow_session": "vibrant://workflow-session",
    "vibrant.get_gatekeeper_session": "vibrant://gatekeeper-session",
    "vibrant.list_pending_questions": "vibrant://pending-questions",
    "vibrant.list_active_runs": "vibrant://active-runs",
    "vibrant.list_active_attempts": "vibrant://active-attempts",
    "vibrant.get_attempt_execution": "vibrant://attempts/{attempt_id}",
    "vibrant.get_conversation": "vibrant://conversations/{conversation_id}",
    "vibrant.get_review_ticket": "vibrant://review-tickets/{ticket_id}",
    "vibrant.list_pending_review_tickets": "vibrant://pending-review-tickets",
    "vibrant.list_recent_events": "vibrant://recent-events/{limit}",
}

_WILDCARD_HOSTS = {"0.0.0.0", "::"}


def _copy_callable_metadata(wrapper: Callable[..., Any], original: Callable[..., Any]) -> None:
    """Preserve signature metadata so FastMCP derives the right schemas."""

    annotations = _resolved_annotations(original)
    signature = inspect.signature(original)
    wrapper.__name__ = original.__name__
    wrapper.__qualname__ = original.__qualname__
    wrapper.__module__ = original.__module__
    wrapper.__doc__ = original.__doc__
    wrapper.__annotations__ = annotations
    wrapper.__signature__ = signature.replace(
        parameters=[
            parameter.replace(annotation=annotations.get(parameter.name, parameter.annotation))
            for parameter in signature.parameters.values()
        ],
        return_annotation=annotations.get("return", signature.return_annotation),
    )


def _resolved_annotations(original: Callable[..., Any]) -> dict[str, Any]:
    """Resolve postponed annotations before FastMCP asks Pydantic for schemas."""

    annotations = dict(getattr(original, "__annotations__", {}))
    try:
        annotations.update(get_type_hints(original, include_extras=True))
    except Exception:
        return annotations
    return annotations


def _require_binding(binding_registry: MCPBindingRegistry) -> RegisteredMCPBinding:
    """Resolve the runtime binding from the current HTTP request headers."""

    request = get_http_request()
    binding_id = request.headers.get(BINDING_HEADER_NAME) or request.headers.get(
        BINDING_HEADER_NAME.lower()
    )
    binding = binding_registry.resolve(binding_id)
    if binding is None:
        raise AuthorizationError("Missing or unknown MCP binding header")
    return binding


@dataclass(slots=True)
class FastMCPHTTPOptions:
    """Host-managed HTTP settings removed from FastMCP's constructor in v3."""

    stateless_http: bool = True
    allowed_hosts: set[str] = field(default_factory=lambda: {"127.0.0.1", "localhost"})


class _BindingMiddleware(Middleware):
    """Require a known binding header for every HTTP request."""

    def __init__(self, binding_registry: MCPBindingRegistry) -> None:
        self._binding_registry = binding_registry

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        _require_binding(self._binding_registry)
        return await call_next(context)


class OrchestratorFastMCPHost:
    """Real loopback MCP host backed by the semantic orchestrator registry."""

    def __init__(
        self,
        semantic_server: OrchestratorMCPServer,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        path: str = "/mcp",
    ) -> None:
        self.semantic_server = semantic_server
        self.binding_registry = MCPBindingRegistry()
        self.transport = LoopbackHTTPTransport(host=host, port=port, path=path)
        self.http_options = FastMCPHTTPOptions()
        self.fastmcp = FastMCP(
            name="vibrant-orchestrator",
            instructions="Loopback MCP surface for the Vibrant orchestrator.",
            middleware=[_BindingMiddleware(self.binding_registry)],
        )
        self._register_tools()
        self._register_resources()

    @property
    def endpoint_url(self) -> str | None:
        return self.transport.endpoint_url

    @property
    def running(self) -> bool:
        return self.transport.running

    @property
    def stateless_http(self) -> bool:
        return self.http_options.stateless_http

    @stateless_http.setter
    def stateless_http(self, value: bool) -> None:
        self.http_options.stateless_http = value

    def allow_host(self, host: str) -> None:
        """Allow the given host through TrustedHost protection."""

        if host not in _WILDCARD_HOSTS:
            self.http_options.allowed_hosts.add(host)

    def http_app(self, middleware: list[ASGIMiddleware] | None = None):
        """Build the FastMCP ASGI app with the host's HTTP transport settings."""

        app_middleware = list(middleware or [])
        trusted_hosts = self._trusted_hosts()
        if trusted_hosts is not None:
            app_middleware.insert(
                0,
                ASGIMiddleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts),
            )
        return self.fastmcp.http_app(
            path=self.transport.path,
            transport="streamable-http",
            stateless_http=self.stateless_http,
            middleware=app_middleware,
        )

    async def ensure_started(self) -> str:
        """Start the loopback MCP transport if needed and return its endpoint URL."""

        return await self.transport.start(self.http_app())

    async def stop(self) -> None:
        """Stop the transport host and clear active bindings."""

        self.binding_registry.clear()
        await self.transport.stop()

    def register_binding(self, binding: AgentMCPBinding) -> RegisteredMCPBinding:
        """Register a runtime binding so incoming requests can resolve it."""

        return self.binding_registry.register(
            principal=binding.principal,
            access=binding.access,
        )

    def unregister_binding(self, binding_id: str | None) -> None:
        self.binding_registry.discard(binding_id)

    def _trusted_hosts(self) -> list[str] | None:
        if self.transport.host in _WILDCARD_HOSTS:
            return None
        return sorted(self.http_options.allowed_hosts)

    def _register_tools(self) -> None:
        for definition in self.semantic_server.tool_definitions.values():
            tool_callable = self._build_tool_callable(definition)
            self.fastmcp.tool(
                tool_callable,
                name=definition.name,
                description=definition.description,
                auth=self._tool_auth(definition),
            )

    def _register_resources(self) -> None:
        for definition in self.semantic_server.resource_definitions.values():
            uri = _RESOURCE_URIS[definition.name]
            resource_callable = self._build_resource_callable(definition)
            self.fastmcp.resource(
                uri,
                name=definition.name,
                description=definition.description,
                mime_type="application/json",
                auth=self._resource_auth(definition),
            )(resource_callable)

    def _tool_auth(self, definition: MCPToolDefinition) -> Callable[[Any], bool]:
        def check(_ctx: Any) -> bool:
            binding = _require_binding(self.binding_registry)
            return definition.name in binding.visible_tools and binding.principal.allows(
                *definition.required_scopes
            )

        return check

    def _resource_auth(self, definition: MCPResourceDefinition) -> Callable[[Any], bool]:
        def check(_ctx: Any) -> bool:
            binding = _require_binding(self.binding_registry)
            return definition.name in binding.visible_resources and binding.principal.allows(
                *definition.required_scopes
            )

        return check

    def _build_tool_callable(self, definition: MCPToolDefinition) -> Callable[..., Any]:
        async def wrapper(**kwargs: Any) -> Any:
            binding = _require_binding(self.binding_registry)
            return await self.semantic_server.call_tool(
                definition.name,
                principal=binding.principal,
                **kwargs,
            )

        _copy_callable_metadata(wrapper, definition.handler)
        return wrapper

    def _build_resource_callable(self, definition: MCPResourceDefinition) -> Callable[..., Any]:
        async def wrapper(**kwargs: Any) -> Any:
            binding = _require_binding(self.binding_registry)
            value = await self.semantic_server.read_resource(
                definition.name,
                principal=binding.principal,
                **kwargs,
            )
            if isinstance(value, (str, bytes)):
                return ResourceResult(value)
            return ResourceResult(
                [ResourceContent(value, mime_type="application/json")]
            )

        _copy_callable_metadata(wrapper, definition.handler)
        return wrapper
