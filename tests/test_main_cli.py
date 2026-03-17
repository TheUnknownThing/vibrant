"""Tests for CLI flags in ``vibrant.__main__``."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

from vibrant import __main__


class TestTextualServeCLI:
    def test_parse_args_normalizes_paths(self, tmp_path: Path) -> None:
        args = __main__._parse_args(["--cwd", str(tmp_path), "init", str(tmp_path / "demo")])

        assert args.cwd == tmp_path.resolve()
        assert args.command == "init"
        assert args.init_path == (tmp_path / "demo").resolve()

    def test_build_textual_client_command_includes_forwarded_flags(self) -> None:
        args = __main__.CliArgs(cwd=Path("/tmp/project"), model="gpt-test", debug=True)

        command = __main__._build_textual_client_command(args)

        assert "--textual-client" in command
        assert "--cwd /tmp/project" in command
        assert "--model gpt-test" in command
        assert "--debug" in command

    def test_serve_mode_invokes_server(self, monkeypatch) -> None:
        called: dict[str, object] = {}

        def fake_serve_app(args: __main__.CliArgs) -> None:
            called["args"] = args

        monkeypatch.setattr(__main__, "_serve_app", fake_serve_app)

        __main__.main(["--serve", "--serve-host", "127.0.0.1", "--serve-port", "9001"])

        args = called["args"]
        assert isinstance(args, __main__.CliArgs)
        assert args.serve is True
        assert args.serve_host == "127.0.0.1"
        assert args.serve_port == 9001

    def test_serve_app_configures_server(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        class FakeServer:
            def __init__(
                self,
                command: str,
                host: str,
                port: int,
                title: str,
                public_url: str | None,
            ) -> None:
                captured["command"] = command
                captured["host"] = host
                captured["port"] = port
                captured["title"] = title
                captured["public_url"] = public_url

            def serve(self, *, debug: bool) -> None:
                captured["debug"] = debug

        package = ModuleType("textual_serve")
        module = ModuleType("textual_serve.server")
        module.Server = FakeServer
        monkeypatch.setitem(sys.modules, "textual_serve", package)
        monkeypatch.setitem(sys.modules, "textual_serve.server", module)

        args = __main__.CliArgs(
            cwd=Path("/tmp/project"),
            model="gpt-test",
            debug=True,
            serve_host="0.0.0.0",
            serve_port=8123,
            serve_public_url="https://demo.example.com",
        )

        __main__._serve_app(args)

        assert "--textual-client" in str(captured["command"])
        assert captured["host"] == "0.0.0.0"
        assert captured["port"] == 8123
        assert captured["title"] == "Vibrant"
        assert captured["public_url"] == "https://demo.example.com"
        assert captured["debug"] is True
