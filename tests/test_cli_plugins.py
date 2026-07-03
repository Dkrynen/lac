"""CLI plugin mounting: plugins add subcommands; apt plugins lists them."""
from types import SimpleNamespace

import backend.plugins as plugins_mod
from backend.plugins import LoadedPlugin


def _fake_discover(monkeypatch, plugins):
    monkeypatch.setattr(plugins_mod, "discover", lambda: plugins)


def test_plugin_subcommand_is_mounted(monkeypatch):
    calls = {}

    def register_cli(sub):
        p = sub.add_parser("prototest", help="plugin-added command")
        p.set_defaults(func=lambda args: calls.setdefault("ran", True))

    plug = SimpleNamespace(name="fake", version="9.9", register_cli=register_cli)
    _fake_discover(monkeypatch, [LoadedPlugin("fake", "9.9", plug)])

    import cli
    parser = cli.build_parser()
    args = parser.parse_args(["prototest"])
    args.func(args)
    assert calls["ran"] is True


def test_broken_register_cli_does_not_crash(monkeypatch):
    def register_cli(sub):
        raise RuntimeError("plugin exploded")

    plug = SimpleNamespace(name="bad", version="0.0", register_cli=register_cli)
    _fake_discover(monkeypatch, [LoadedPlugin("bad", "0.0", plug)])

    import cli
    parser = cli.build_parser()  # must not raise
    args = parser.parse_args(["list"])
    assert args is not None


def test_cmd_plugins_lists(monkeypatch, capsys):
    plug = SimpleNamespace(name="fake", version="9.9")
    _fake_discover(monkeypatch, [
        LoadedPlugin("fake", "9.9", plug),
        LoadedPlugin("broken", "?", None, error="ImportError: nope"),
    ])
    import cli
    cli.cmd_plugins(SimpleNamespace())
    out = capsys.readouterr().out
    assert "fake" in out and "9.9" in out
    assert "broken" in out and "error" in out.lower()


def test_discover_failure_does_not_kill_cli(monkeypatch, capsys):
    """If discovery itself raises, build_parser() must still return a working
    parser (warning on stderr), so every CLI invocation keeps functioning."""
    def boom():
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr(plugins_mod, "discover", boom)

    import cli
    parser = cli.build_parser()  # must not raise
    args = parser.parse_args(["list"])
    assert args is not None
    assert "discovery failed" in capsys.readouterr().err
