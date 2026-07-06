import server


def test_is_cli_invocation_true_for_subcommand():
    assert server._is_cli_invocation(["pro", "activate"]) is True
    assert server._is_cli_invocation(["scan"]) is True


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
