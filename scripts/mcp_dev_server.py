from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from vibrant.mcp import DEFAULT_BEARER_TOKEN_ENV_VAR, MCPServerSettings
from vibrant.orchestrator.bootstrap import create_orchestrator
from vibrant.orchestrator.facade import OrchestratorFacade
from vibrant.orchestrator.mcp import OrchestratorMCPServer, create_orchestrator_fastmcp, create_orchestrator_fastmcp_app
from vibrant.project_init import initialize_project


def build_registry(project_root: str | Path) -> tuple[OrchestratorMCPServer, Path]:
    root = Path(project_root).expanduser().resolve()
    initialize_project(root)
    orchestrator = create_orchestrator(root)
    return OrchestratorMCPServer(OrchestratorFacade(orchestrator)), root


def build_http_settings(
    *,
    host: str,
    port: int,
    mcp_path: str,
    bearer_token_env_var: str,
) -> MCPServerSettings:
    public_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return MCPServerSettings(
        url=f"http://{public_host}:{port}{mcp_path}",
        bearer_token_env_var=bearer_token_env_var,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local Vibrant MCP dev server")
    parser.add_argument("--project-root", default=".", help="Vibrant project root to serve")
    parser.add_argument("--transport", choices=("http", "stdio"), default="http", help="Server transport")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=9000, help="Bind port")
    parser.add_argument("--mcp-path", default="/mcp", help="HTTP path for the MCP endpoint")
    parser.add_argument(
        "--bearer-token-env-var",
        default=DEFAULT_BEARER_TOKEN_ENV_VAR,
        help="Environment variable that stores the MCP bearer token for HTTP transport",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    registry, root = build_registry(args.project_root)
    if args.transport == "stdio":
        server = create_orchestrator_fastmcp(registry, name=f"Vibrant MCP ({root.name})")
        server.run(transport="stdio")
        return

    settings = build_http_settings(
        host=args.host,
        port=args.port,
        mcp_path=args.mcp_path,
        bearer_token_env_var=args.bearer_token_env_var,
    )
    app = create_orchestrator_fastmcp_app(
        registry,
        settings=settings,
        mcp_path=args.mcp_path,
        name=f"Vibrant MCP ({root.name})",
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
