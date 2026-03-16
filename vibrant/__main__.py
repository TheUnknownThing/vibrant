"""CLI entry point for Vibrant.

Usage::

    python -m vibrant [--cwd DIR] [--model MODEL]
    python -m vibrant init [PATH]
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import shutil
import shlex
import sys
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .config import find_project_root, load_config
from .project_init import initialize_project
from .providers.base import ProviderKind


class _DynamicHostServer:
    """Small wrapper around textual-serve that builds URLs from request host."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from textual_serve.server import Server

        self._server: Server = Server(*args, **kwargs)

    def serve(self, debug: bool = False) -> None:
        from aiohttp import web
        import aiohttp_jinja2

        @aiohttp_jinja2.template("app_index.html")
        async def _dynamic_handle_index(request: web.Request) -> dict[str, Any]:
            router = request.app.router
            try:
                font_size = int(request.query.get("fontsize", "16"))
            except ValueError:
                font_size = 16

            origin = self._server.public_url.rstrip("/")

            def get_url(route: str, **args: Any) -> str:
                path = router[route].url_for(**args)
                path_text = str(path)
                if not path_text.startswith("/"):
                    path_text = f"/{path_text}"
                return f"{origin}{path_text}"

            websocket_parts = urlsplit(get_url("websocket"))
            websocket_scheme = "wss" if websocket_parts.scheme == "https" else "ws"
            app_websocket_url = urlunsplit(websocket_parts._replace(scheme=websocket_scheme))

            return {
                "font_size": font_size,
                "app_websocket_url": app_websocket_url,
                "config": {
                    "static": {
                        "url": get_url("static", filename="/").rstrip("/") + "/",
                    }
                },
                "application": {"name": self._server.title},
            }

        self._server.handle_index = _dynamic_handle_index
        self._server.serve(debug=debug)


def _check_binary(binary: str | None) -> str | None:
    """Return the configured executable path, or ``None`` if it cannot be found."""

    if binary is None or not binary.strip():
        return None
    candidate = binary.strip()
    resolved = shutil.which(candidate)
    if resolved:
        return resolved
    path = Path(candidate).expanduser()
    if path.exists() and path.is_file():
        return str(path.resolve())
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibrant",
        description="Terminal UI for orchestrating Codex agent workflows",
    )
    parser.add_argument(
        "--cwd",
        help="Working directory for Codex sessions (default: current dir)",
    )
    parser.add_argument(
        "--model",
        help="Default model (default: gpt-5.3-codex)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to ~/.vibrant/debug.log",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Serve the Textual app over HTTP via textual-serve",
    )
    parser.add_argument(
        "--serve-host",
        default="0.0.0.0",
        help="Host interface for --serve (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--serve-port",
        type=int,
        default=8000,
        help="Port for --serve (default: 8000)",
    )
    parser.add_argument(
        "--serve-public-url",
        default=None,
        help="Optional externally reachable URL shown by textual-serve",
    )
    parser.add_argument(
        "--textual-client",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    subparsers = parser.add_subparsers(dest="command")
    init_parser = subparsers.add_parser("init", help="Initialize the .vibrant project directory")
    init_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Project directory to initialize (default: current dir)",
    )
    return parser


def _build_textual_client_command(args: argparse.Namespace) -> str:
    command_parts = [sys.executable, "-m", "vibrant", "--textual-client"]
    if args.cwd:
        command_parts.extend(["--cwd", args.cwd])
    if args.model:
        command_parts.extend(["--model", args.model])
    if args.debug:
        command_parts.append("--debug")
    return shlex.join(command_parts)


def _serve_app(args: argparse.Namespace) -> None:
    server = _DynamicHostServer(
        _build_textual_client_command(args),
        host=args.serve_host,
        port=args.serve_port,
        title="Vibrant",
        public_url=args.serve_public_url,
    )
    server.serve(debug=args.debug)


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        vibrant_dir = initialize_project(args.path)
        print(f"Initialized Vibrant project in {vibrant_dir}")
        return

    if args.serve and not args.textual_client:
        _serve_app(args)
        return

    start_path = args.cwd or os.getcwd()
    project_root = find_project_root(start_path)
    config = load_config(start_path=start_path)
    mock_responses_enabled = config.mock_responses
    provider_binary = config.codex_binary
    resolved_provider_binary = None
    if mock_responses_enabled:
        resolved_provider_binary = None
    elif config.provider_kind is ProviderKind.CODEX:
        resolved_provider_binary = _check_binary(config.codex_binary)
        if not resolved_provider_binary:
            print(
                f"❌ Error: '{config.codex_binary}' CLI not found in PATH.\n"
                "Install it: npm install -g @openai/codex\n"
                "Then run: codex auth",
                file=sys.stderr,
            )
            sys.exit(1)
        provider_binary = resolved_provider_binary
    elif config.claude_cli_path:
        resolved_provider_binary = _check_binary(config.claude_cli_path)
        if not resolved_provider_binary:
            print(
                f"❌ Error: Claude CLI '{config.claude_cli_path}' was configured but could not be found.",
                file=sys.stderr,
            )
            sys.exit(1)

    if args.debug:
        import pathlib

        log_dir = pathlib.Path("~/.vibrant").expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(log_dir / "debug.log"),
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    from .models.settings import AppSettings
    from .tui.app import VibrantApp

    settings = AppSettings(
        default_model=args.model or config.model,
        default_cwd=args.cwd,
        codex_binary=provider_binary,
        history_dir=str(config.resolve_conversation_directory(project_root)),
    )

    app = VibrantApp(settings=settings, cwd=args.cwd)
    app.run()


if __name__ == "__main__":
    main()
