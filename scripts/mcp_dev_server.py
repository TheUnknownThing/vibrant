from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from vibrant.orchestrator.bootstrap import create_orchestrator
from vibrant.orchestrator.facade import OrchestratorFacade
from vibrant.orchestrator.mcp import OrchestratorMCPServer, create_orchestrator_fastmcp
from vibrant.project_init import initialize_project


def build_server(project_root: str | Path):
    root = Path(project_root).expanduser().resolve()
    initialize_project(root)
    orchestrator = create_orchestrator(root)
    registry = OrchestratorMCPServer(OrchestratorFacade(orchestrator))
    return create_orchestrator_fastmcp(registry, name=f"Vibrant MCP ({root.name})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local Vibrant MCP dev server")
    parser.add_argument("--project-root", default=".", help="Vibrant project root to serve")
    parser.add_argument("--transport", choices=("http", "stdio"), default="http", help="Server transport")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--mcp-path", default="/mcp", help="HTTP path for the MCP endpoint")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = build_server(args.project_root)
    if args.transport == "stdio":
        server.run(transport="stdio")
        return

    app = server.http_app(path=args.mcp_path, transport="http")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
