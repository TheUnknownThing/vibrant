"""CLI entry point for Vibrant.

Usage::

    python -m vibrant [--cwd DIR] [--model MODEL]
    python -m vibrant [--dev] [--serve] [--cwd DIR] [--model MODEL]
    python -m vibrant init [PATH]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import shutil
import shlex
import sys
from collections.abc import Sequence
from typing import Literal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .config import VibrantConfig, find_project_root, load_config
from .project_init import initialize_project
from .providers.base import ProviderKind


@dataclass(slots=True)
class CliArgs:
    """Parsed CLI arguments with paths normalized at the boundary."""

    command: Literal["init"] | None = None
    cwd: Path | None = None
    model: str | None = None
    debug: bool = False
    dev: bool = False
    serve: bool = False
    serve_host: str = "0.0.0.0"
    serve_port: int = 8000
    serve_public_url: str | None = None
    textual_client: bool = False
    init_path: Path = Path(".")


def _enable_textual_devtools() -> None:
    """Enable Textual debug and devtools features for this process."""

    features = {
        feature.strip()
        for feature in os.environ.get("TEXTUAL", "").split(",")
        if feature.strip()
    }
    features.update({"debug", "devtools"})
    os.environ["TEXTUAL"] = ",".join(sorted(features))


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
        "--dev",
        action="store_true",
        help="Enable Textual devtools support for `textual console`",
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


def _resolve_optional_cli_path(raw_path: str | None) -> Path | None:
    """Resolve an optional CLI path to an absolute ``Path``."""

    if raw_path is None or not raw_path.strip():
        return None
    return Path(raw_path).expanduser().resolve()


def _parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse CLI arguments into a typed structure."""

    parsed = _build_parser().parse_args(argv)
    raw_init_path = getattr(parsed, "path", ".")
    return CliArgs(
        command=parsed.command,
        cwd=_resolve_optional_cli_path(parsed.cwd),
        model=parsed.model,
        debug=parsed.debug,
        dev=parsed.dev,
        serve=parsed.serve,
        serve_host=parsed.serve_host,
        serve_port=parsed.serve_port,
        serve_public_url=parsed.serve_public_url,
        textual_client=parsed.textual_client,
        init_path=Path(raw_init_path).expanduser().resolve(),
    )


def _build_textual_client_command(args: CliArgs) -> str:
    command_parts = [sys.executable, "-m", "vibrant", "--textual-client"]
    if args.cwd:
        command_parts.extend(["--cwd", str(args.cwd)])
    if args.model:
        command_parts.extend(["--model", args.model])
    if args.debug:
        command_parts.append("--debug")
    return shlex.join(command_parts)


def _serve_app(args: CliArgs) -> None:
    server = _DynamicHostServer(
        _build_textual_client_command(args),
        host=args.serve_host,
        port=args.serve_port,
        title="Vibrant",
        public_url=args.serve_public_url,
    )
    server.serve(debug=args.debug)


def _resolve_provider_binary(config: VibrantConfig) -> str:
    """Return the executable path stored in app settings after validation."""

    if config.mock_responses:
        return config.codex_binary
    if config.provider_kind is ProviderKind.CODEX:
        resolved_provider_binary = _check_binary(config.codex_binary)
        if not resolved_provider_binary:
            print(
                f"❌ Error: '{config.codex_binary}' CLI not found in PATH.\n"
                "Install it: npm install -g @openai/codex\n"
                "Then run: codex auth",
                file=sys.stderr,
            )
            sys.exit(1)
        return resolved_provider_binary
    if config.claude_cli_path:
        resolved_provider_binary = _check_binary(config.claude_cli_path)
        if not resolved_provider_binary:
            print(
                f"❌ Error: Claude CLI '{config.claude_cli_path}' was configured but could not be found.",
                file=sys.stderr,
            )
            sys.exit(1)
    return config.codex_binary


def _configure_logging(debug: bool) -> None:
    """Configure process logging for the selected CLI mode."""

    if debug:
        log_dir = Path("~/.vibrant").expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(log_dir / "debug.log"),
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        logging.getLogger("markdown_it").setLevel(logging.INFO)
        return
    logging.basicConfig(level=logging.WARNING)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.command == "init":
        vibrant_dir = initialize_project(args.init_path)
        print(f"Initialized Vibrant project in {vibrant_dir}")
        return

    if args.dev:
        _enable_textual_devtools()

    if args.serve and not args.textual_client:
        _serve_app(args)
        return

    start_path = args.cwd or Path.cwd()
    project_root = find_project_root(start_path)
    config = load_config(start_path=start_path)
    provider_binary = _resolve_provider_binary(config)
    _configure_logging(args.debug)

    from .models.settings import AppSettings
    from .tui.app import VibrantApp

    default_cwd = str(args.cwd) if args.cwd is not None else None
    settings = AppSettings(
        default_model=args.model or config.model,
        default_cwd=default_cwd,
        codex_binary=provider_binary,
        history_dir=str(config.resolve_conversation_directory(project_root)),
    )

    app = VibrantApp(settings=settings, cwd=default_cwd)
    app.run()


if __name__ == "__main__":
    main()
