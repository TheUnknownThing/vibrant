"""FastMCP transport host for the orchestrator semantic MCP backend."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import unquote

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools import Tool
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Resource as MCPResource
from mcp.types import ResourceTemplate as MCPResourceTemplate
from mcp.types import TextContent
from mcp.types import Tool as MCPTool

from vibrant.orchestrator.types import BoundAgentCapabilities

from .binding_registry import BINDING_HEADER_NAME, MCPBindingRegistry, RegisteredMCPBinding
from .common import MCPAuthorizationError, MCPNotFoundError
from .server import OrchestratorMCPServer
from .transport import LoopbackHTTPTransport


@dataclass(frozen=True, slots=True)
class _TransportResource:
    name: str
    description: str
    uri: str | None = None
    uri_template: str | None = None
    pattern: re.Pattern[str] | None = None
    converters: dict[str, Callable[[str], Any]] = field(default_factory=dict)

    @property
    def template(self) -> bool:
        return self.uri_template is not None

    def match(self, uri: str) -> dict[str, Any] | None:
        if self.uri is not None:
            return {} if uri == self.uri else None
        if self.pattern is None:
            return None
        match = self.pattern.fullmatch(uri)
        if match is None:
            return None
        params: dict[str, Any] = {}
        for key, value in match.groupdict().items():
            raw = unquote(value)
            converter = self.converters.get(key, str)
            params[key] = converter(raw)
        return params


class _BindingAwareFastMCP(FastMCP):
    """FastMCP server that filters the semantic MCP surface per binding."""

    def __init__(
        self,
        semantic_server: OrchestratorMCPServer,
        binding_registry: MCPBindingRegistry,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        streamable_http_path: str = "/mcp",
    ) -> None:
        super().__init__(
            name="vibrant-orchestrator",
            instructions="Loopback MCP surface for the Vibrant orchestrator.",
            host=host,
            port=port,
            streamable_http_path=streamable_http_path,
            log_level="WARNING",
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"],
                allowed_origins=["http://127.0.0.1", "http://127.0.0.1:*", "http://localhost", "http://localhost:*"],
            ),
        )
        self._semantic_server = semantic_server
        self._binding_registry = binding_registry
        self._tool_metadata = self._build_tool_metadata()
        self._resources = self._build_transport_resources()

    async def list_tools(self) -> list[MCPTool]:
        binding = self._require_binding()
        tools: list[MCPTool] = []
        for name, definition in self._semantic_server.tool_definitions.items():
            if name not in binding.visible_tools:
                continue
            if not binding.principal.allows(*definition.required_scopes):
                continue
            metadata = self._tool_metadata[name]
            tools.append(
                MCPTool(
                    name=metadata.name,
                    title=metadata.title,
                    description=metadata.description,
                    inputSchema=metadata.parameters,
                    outputSchema=metadata.output_schema,
                    annotations=metadata.annotations,
                    icons=metadata.icons,
                    _meta=metadata.meta,
                )
            )
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> list[TextContent]:
        binding = self._require_binding()
        if name not in binding.visible_tools:
            raise MCPNotFoundError(f"Unknown MCP tool: {name}")

        metadata = self._tool_metadata.get(name)
        if metadata is None:
            raise MCPNotFoundError(f"Unknown MCP tool: {name}")

        parsed = metadata.fn_metadata.arg_model.model_validate(
            metadata.fn_metadata.pre_parse_json(dict(arguments))
        )
        kwargs = parsed.model_dump_one_level()
        result = await self._semantic_server.call_tool(name, principal=binding.principal, **kwargs)
        return [_text_content_from_value(result)]

    async def list_resources(self) -> list[MCPResource]:
        binding = self._require_binding()
        resources: list[MCPResource] = []
        for resource in self._resources:
            if resource.template or resource.uri is None:
                continue
            if resource.name not in binding.visible_resources:
                continue
            resources.append(
                MCPResource(
                    uri=resource.uri,
                    name=resource.name,
                    title=None,
                    description=resource.description,
                    mimeType="application/json",
                    icons=None,
                    annotations=None,
                    _meta=None,
                )
            )
        return resources

    async def list_resource_templates(self) -> list[MCPResourceTemplate]:
        binding = self._require_binding()
        templates: list[MCPResourceTemplate] = []
        for resource in self._resources:
            if not resource.template or resource.uri_template is None:
                continue
            if resource.name not in binding.visible_resources:
                continue
            templates.append(
                MCPResourceTemplate(
                    uriTemplate=resource.uri_template,
                    name=resource.name,
                    title=None,
                    description=resource.description,
                    mimeType="application/json",
                    icons=None,
                    annotations=None,
                    _meta=None,
                )
            )
        return templates

    async def read_resource(self, uri: Any) -> list[ReadResourceContents]:
        binding = self._require_binding()
        resource, params = self._resolve_resource(str(uri))
        if resource.name not in binding.visible_resources:
            raise MCPNotFoundError(f"Unknown MCP resource: {resource.name}")

        value = await self._semantic_server.read_resource(
            resource.name,
            principal=binding.principal,
            **params,
        )
        return [
            ReadResourceContents(
                content=json.dumps(value, indent=2, ensure_ascii=True),
                mime_type="application/json",
                meta=None,
            )
        ]

    def _require_binding(self) -> RegisteredMCPBinding:
        try:
            request = self.get_context().request_context.request
        except ValueError as exc:  # pragma: no cover - direct non-request access
            raise MCPAuthorizationError("MCP binding is only available during an HTTP request") from exc

        headers = getattr(request, "headers", {})
        binding_id = headers.get(BINDING_HEADER_NAME) or headers.get(BINDING_HEADER_NAME.lower())
        binding = self._binding_registry.resolve(binding_id)
        if binding is None:
            raise MCPAuthorizationError("Missing or unknown MCP binding header")
        return binding

    def _resolve_resource(self, uri: str) -> tuple[_TransportResource, dict[str, Any]]:
        for resource in self._resources:
            params = resource.match(uri)
            if params is not None:
                return resource, params
        raise MCPNotFoundError(f"Unknown MCP resource URI: {uri}")

    def _build_tool_metadata(self) -> dict[str, Tool]:
        metadata: dict[str, Tool] = {}
        for name, definition in self._semantic_server.tool_definitions.items():
            metadata[name] = Tool.from_function(
                definition.handler,
                name=definition.name,
                description=definition.description,
            )
        return metadata

    def _build_transport_resources(self) -> list[_TransportResource]:
        descriptions = self._semantic_server.resource_definitions
        return [
            _TransportResource(
                name="vibrant.get_consensus",
                description=descriptions["vibrant.get_consensus"].description,
                uri="vibrant://consensus",
            ),
            _TransportResource(
                name="vibrant.get_roadmap",
                description=descriptions["vibrant.get_roadmap"].description,
                uri="vibrant://roadmap",
            ),
            _TransportResource(
                name="vibrant.get_workflow_status",
                description=descriptions["vibrant.get_workflow_status"].description,
                uri="vibrant://workflow-status",
            ),
            _TransportResource(
                name="vibrant.list_pending_questions",
                description=descriptions["vibrant.list_pending_questions"].description,
                uri="vibrant://pending-questions",
            ),
            _TransportResource(
                name="vibrant.list_active_agents",
                description=descriptions["vibrant.list_active_agents"].description,
                uri="vibrant://active-agents",
            ),
            _TransportResource(
                name="vibrant.list_active_attempts",
                description=descriptions["vibrant.list_active_attempts"].description,
                uri="vibrant://active-attempts",
            ),
            _TransportResource(
                name="vibrant.list_pending_review_tickets",
                description=descriptions["vibrant.list_pending_review_tickets"].description,
                uri="vibrant://pending-review-tickets",
            ),
            _TransportResource(
                name="vibrant.get_task",
                description=descriptions["vibrant.get_task"].description,
                uri_template="vibrant://tasks/{task_id}",
                pattern=re.compile(r"^vibrant://tasks/(?P<task_id>[^/]+)$"),
            ),
            _TransportResource(
                name="vibrant.get_review_ticket",
                description=descriptions["vibrant.get_review_ticket"].description,
                uri_template="vibrant://review-tickets/{ticket_id}",
                pattern=re.compile(r"^vibrant://review-tickets/(?P<ticket_id>[^/]+)$"),
            ),
            _TransportResource(
                name="vibrant.list_recent_events",
                description=descriptions["vibrant.list_recent_events"].description,
                uri_template="vibrant://recent-events/{limit}",
                pattern=re.compile(r"^vibrant://recent-events/(?P<limit>[^/]+)$"),
                converters={"limit": int},
            ),
        ]


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
        self.fastmcp = _BindingAwareFastMCP(
            semantic_server,
            self.binding_registry,
            host=host,
            port=port or 0,
            streamable_http_path=path,
        )

    @property
    def endpoint_url(self) -> str | None:
        return self.transport.endpoint_url

    @property
    def running(self) -> bool:
        return self.transport.running

    async def ensure_started(self) -> str:
        """Start the loopback MCP transport if needed and return its endpoint URL."""

        if self.transport.port is not None:
            self.fastmcp.settings.port = self.transport.port
        url = await self.transport.start(self.fastmcp.streamable_http_app())
        if self.transport.port is not None:
            self.fastmcp.settings.port = self.transport.port
        return url

    async def stop(self) -> None:
        """Stop the transport host and clear active bindings."""

        self.binding_registry.clear()
        await self.transport.stop()

    def register_binding(self, capabilities: BoundAgentCapabilities) -> RegisteredMCPBinding:
        """Register a runtime binding so incoming requests can resolve it."""

        if capabilities.access is None:
            raise ValueError("BoundAgentCapabilities.access is required for MCP transport registration")
        return self.binding_registry.register(
            principal=capabilities.principal,
            access=capabilities.access,
        )

    def unregister_binding(self, binding_id: str | None) -> None:
        self.binding_registry.discard(binding_id)


def _text_content_from_value(value: Any) -> TextContent:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, indent=2, ensure_ascii=True)
    return TextContent(type="text", text=text)
