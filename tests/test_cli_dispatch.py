import server


def test_is_cli_invocation_true_for_subcommand():
    assert server._is_cli_invocation(["pro", "activate"]) is True
    assert server._is_cli_invocation(["scan"]) is True
    assert server._is_cli_invocation(["eval", "--dry-run"]) is True


def test_is_cli_invocation_routes_global_host_eval_to_cli():
    host = "http://127.0.0.1:11434"
    assert server._is_cli_invocation(["--host", host, "eval", "--dry-run"]) is True
    assert server._is_cli_invocation([f"--host={host}", "eval", "--dry-run"]) is True
    assert server._is_cli_invocation(["--hos", host, "eval", "--dry-run"]) is True
    assert server._is_cli_invocation([f"--ho={host}", "eval", "--dry-run"]) is True


def test_is_cli_invocation_routes_top_level_help_to_cli():
    assert server._is_cli_invocation(["--help"]) is True
    assert server._is_cli_invocation(["-h"]) is True


def test_is_cli_invocation_false_for_server_flags_and_empty():
    assert server._is_cli_invocation([]) is False
    assert server._is_cli_invocation(["--window"]) is False
    assert server._is_cli_invocation(["--host", "localhost"]) is False


def test_main_delegates_cli(monkeypatch):
    monkeypatch.setattr(server.sys, "argv", ["lac", "pro", "activate"])
    called = {}
    import cli
    monkeypatch.setattr(cli, "main", lambda: called.setdefault("ran", True))
    with __import__("pytest").raises(SystemExit):
        server.main()
    assert called.get("ran") is True


def test_main_delegates_global_host_eval_to_cli(monkeypatch):
    monkeypatch.setattr(
        server.sys,
        "argv",
        [
            "lac",
            "--host",
            "http://127.0.0.1:11434",
            "eval",
            "--dry-run",
        ],
    )
    called = {}
    import cli

    monkeypatch.setattr(cli, "main", lambda: called.setdefault("ran", True))
    with __import__("pytest").raises(SystemExit):
        server.main()
    assert called.get("ran") is True
