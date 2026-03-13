"""CLI entry point for Vibrant.

Usage::

    python -m vibrant [--cwd DIR] [--model MODEL]
    python -m vibrant [--dev] [--cwd DIR] [--model MODEL]
    python -m vibrant init [PATH]
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import shutil
import sys
from collections.abc import Sequence

from .config import find_project_root, load_config
from .project_init import initialize_project
from .providers.base import ProviderKind


def _enable_textual_devtools() -> None:
    """Enable Textual debug and devtools features for this process."""

    features = {
        feature.strip()
        for feature in os.environ.get("TEXTUAL", "").split(",")
        if feature.strip()
    }
    features.update({"debug", "devtools"})
    os.environ["TEXTUAL"] = ",".join(sorted(features))


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
        "--log",
        action="store_true",
        help="Enable debug logging to ~/.vibrant/debug.log",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Enable Textual devtools support for `textual console`",
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


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        vibrant_dir = initialize_project(args.path)
        print(f"Initialized Vibrant project in {vibrant_dir}")
        return

    if args.dev:
        _enable_textual_devtools()

    start_path = args.cwd or os.getcwd()
    project_root = find_project_root(start_path)
    config = load_config(start_path=start_path)
    provider_binary = config.codex_binary
    resolved_provider_binary = None
    if config.provider_kind is ProviderKind.CODEX and not config.mock_responses:
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

    if args.log:
        import pathlib

        log_dir = pathlib.Path("~/.vibrant").expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(log_dir / "debug.log"),
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        logging.getLogger("markdown_it").setLevel(logging.INFO)

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
