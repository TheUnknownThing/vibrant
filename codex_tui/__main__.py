"""CLI entry point for Codex TUI.

Usage::

    python -m codex_tui [--cwd DIR] [--model MODEL]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys

from .app import CodexTuiApp
from .models import AppSettings


def _check_codex() -> str | None:
    """Return the path to the codex binary, or None if not found."""
    return shutil.which("codex")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="codex-tui",
        description="Terminal UI for managing OpenAI Codex agent threads",
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
        help="Enable debug logging to ~/.codex-tui/debug.log",
    )
    args = parser.parse_args()

    # Check codex CLI
    codex_path = _check_codex()
    if not codex_path:
        print(
            "❌ Error: 'codex' CLI not found in PATH.\n"
            "Install it: npm install -g @openai/codex\n"
            "Then run: codex auth",
            file=sys.stderr,
        )
        sys.exit(1)

    # Setup logging
    if args.debug:
        import pathlib
        log_dir = pathlib.Path("~/.codex-tui").expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(log_dir / "debug.log"),
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    # Build settings
    settings = AppSettings(codex_binary=codex_path)
    if args.model:
        settings.default_model = args.model
    if args.cwd:
        settings.default_cwd = args.cwd

    # Run the app
    app = CodexTuiApp(settings=settings, cwd=args.cwd)
    app.run()


if __name__ == "__main__":
    main()
