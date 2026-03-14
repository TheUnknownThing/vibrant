from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn
from starlette.middleware.cors import CORSMiddleware

from vibrant.orchestrator import create_orchestrator
from vibrant.orchestrator.bootstrap import Orchestrator
from vibrant.orchestrator.mcp import BINDING_HEADER_NAME
from vibrant.orchestrator.policy.shared.capabilities import gatekeeper_binding_preset, worker_binding_preset
from vibrant.project_init import initialize_project


def build_orchestrator(project_root: str | Path) -> tuple[Orchestrator, Path]:
    root = Path(project_root).expanduser().resolve()
    initialize_project(root)
    return create_orchestrator(root), root


def register_binding(
    orchestrator: Orchestrator,
    *,
    role: str,
    session_id: str,
    conversation_id: str | None,
    worker_agent_id: str,
    worker_agent_type: str,
) -> str:
    if role == "worker":
        preset = worker_binding_preset(
            orchestrator.mcp_server,
            agent_id=worker_agent_id,
            agent_type=worker_agent_type,
        )
    else:
        preset = gatekeeper_binding_preset(orchestrator.mcp_server, session_id)

    bound = orchestrator.binding_service.bind_preset(
        preset=preset,
        session_id=session_id,
        conversation_id=conversation_id,
    )
    registered = orchestrator.mcp_host.register_binding(bound)
    return registered.binding_id


def allow_host_for_transport_security(orchestrator: Orchestrator, host: str) -> None:
    """Allow LAN access for the configured dev-server host."""

    transport_security = orchestrator.mcp_host.fastmcp.settings.transport_security
    if transport_security is None:
        return
    if host in {"0.0.0.0", "::"}:
        return

    allowed_hosts = list(transport_security.allowed_hosts or [])
    for candidate in (host, f"{host}:*"):
        if candidate not in allowed_hosts:
            allowed_hosts.append(candidate)
    transport_security.allowed_hosts = allowed_hosts

    allowed_origins = list(transport_security.allowed_origins or [])
    for candidate in (f"http://{host}", f"http://{host}:*"):
        if candidate not in allowed_origins:
            allowed_origins.append(candidate)
    transport_security.allowed_origins = allowed_origins


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local Vibrant MCP dev server")
    parser.add_argument("--project-root", default=".", help="Vibrant project root to serve")
    parser.add_argument("--transport", choices=("http", "stdio"), default="http", help="Server transport")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=9000, help="Bind port")
    parser.add_argument("--mcp-path", default="/mcp", help="HTTP path for the MCP endpoint")
    parser.add_argument("--role", choices=("gatekeeper", "worker"), default="gatekeeper", help="Binding role")
    parser.add_argument("--session-id", default="mcp-dev-session", help="Session id attached to the binding")
    parser.add_argument("--conversation-id", default=None, help="Optional conversation id attached to the binding")
    parser.add_argument("--worker-agent-id", default="dev-worker", help="Agent id for worker bindings")
    parser.add_argument("--worker-agent-type", default="code", help="Agent type for worker bindings")
    parser.add_argument(
        "--cors-allow-origin",
        action="append",
        default=[],
        help="Allowed CORS origin, repeatable. Defaults to '*' when omitted.",
    )
    parser.add_argument(
        "--stateful-http",
        action="store_true",
        help="Use stateful Streamable HTTP (requires Mcp-Session-Id on subsequent requests).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    orchestrator, root = build_orchestrator(args.project_root)

    if args.transport == "stdio":
        raise SystemExit("stdio transport is no longer supported by the binding-aware MCP host; use --transport http")

    orchestrator.mcp_host.transport.host = args.host
    orchestrator.mcp_host.transport.path = args.mcp_path
    orchestrator.mcp_host.transport.port = args.port
    orchestrator.mcp_host.fastmcp.settings.host = args.host
    orchestrator.mcp_host.fastmcp.settings.port = args.port
    orchestrator.mcp_host.fastmcp.settings.streamable_http_path = args.mcp_path
    orchestrator.mcp_host.fastmcp.settings.stateless_http = not args.stateful_http
    allow_host_for_transport_security(orchestrator, args.host)

    binding_id = register_binding(
        orchestrator,
        role=args.role,
        session_id=args.session_id,
        conversation_id=args.conversation_id,
        worker_agent_id=args.worker_agent_id,
        worker_agent_type=args.worker_agent_type,
    )

    print(f"Serving Vibrant MCP for project: {root}")
    print(f"Endpoint: http://{args.host}:{args.port}{args.mcp_path}")
    print(f"Required header: {BINDING_HEADER_NAME}: {binding_id}")
    print(f"Stateless HTTP: {orchestrator.mcp_host.fastmcp.settings.stateless_http}")

    app = orchestrator.mcp_host.fastmcp.streamable_http_app()
    cors_allow_origins = args.cors_allow_origin or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        asyncio.run(orchestrator.shutdown())


if __name__ == "__main__":
    main()
