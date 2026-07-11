from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.project_security import (
    MAX_PROJECT_FILE_BYTES,
    SensitiveProjectPathError,
    is_sensitive_project_path,
    list_project_directory,
    read_project_text,
    resolve_project_path,
)


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        ".env.local",
        ".env-production",
        ".envrc",
        ".envrc-local",
        ".env.local/nested.txt",
        ".envrc-production/nested.txt",
        ".git/config",
        ".hg/hgrc",
        ".svn/entries",
        ".ssh/id_ed25519",
        ".aws/credentials",
        ".azure/accessTokens.json",
        ".gcloud/application_default_credentials.json",
        ".kube/config",
        ".docker/config.json",
        ".terraform/terraform.tfstate",
        ".pulumi/stacks.json",
        ".secrets/client.json",
        "secrets/client.json",
        "credentials/local.json",
        "tokens/access.json",
        ".apt/apt.jsonc",
        ".model-hub/cookbook.db",
        ".cloudflared/cert.pem",
        ".oci/config",
        "credentials.json",
        "credentials.json.bak",
        "token.json",
        "token.json.backup",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".git-credentials",
        "id_rsa",
        "id_ed25519_work",
        "client_secret.json",
        "database_credentials.json",
        "oauth-token.json",
        "passwords.prod.json",
        "secret.env",
        "credentials.env",
        "tokens.csv",
        "passwords.tsv",
        "client_secret.properties",
        "secrets.tfvars",
        "private_key.asc",
        "secrets.py",
        "src/secrets.ts",
        "credentials.js",
        "private_key.py",
        "token.sh",
        "service-account.json",
        "private.pem",
        "private.pem.old",
        "signing.p12",
        "terraform.tfstate",
        "terraform.tfstate.backup",
        "folder/file.txt:stream",
        "../outside.txt",
        "/absolute.txt",
        "C:/absolute.txt",
        "folder\\windows.txt",
    ],
)
def test_sensitive_project_path_policy_fails_closed(relative):
    assert is_sensitive_project_path(relative)


@pytest.mark.parametrize(
    "relative",
    [
        "src/tokenizer.py",
        "docs/secretary.md",
        "src/credential_helper.py",
        "src/api_key_manager.py",
        "src/password_reset.py",
        "src/tokenizer.csv",
        "docs/secretary.tsv",
        "node_modules/pkg/index.js",
        "dist/app.js",
        "models/tiny.gguf",
        "certificates/readme.md",
        ".github/workflows/ci.yml",
    ],
)
def test_sensitive_project_path_policy_does_not_use_generic_substrings(relative):
    assert not is_sensitive_project_path(relative)


def test_resolve_project_path_rejects_escape_and_link_traversal(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

    target, relative = resolve_project_path(root, "src/app.py")
    assert target == root / "src" / "app.py"
    assert relative == "src/app.py"

    for unsafe in ("../outside.txt", str(tmp_path / "outside.txt"), "src/app.py:ads"):
        with pytest.raises(SensitiveProjectPathError):
            resolve_project_path(root, unsafe)

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not read", encoding="utf-8")
    link = root / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation unavailable")

    with pytest.raises(SensitiveProjectPathError):
        resolve_project_path(root, "linked/secret.txt")
    with pytest.raises(SensitiveProjectPathError):
        resolve_project_path(root, "linked/new.txt")


def test_shared_read_is_bounded_regular_strict_utf8(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "ok.txt").write_text("hello", encoding="utf-8")
    (root / "binary.dat").write_bytes(b"\xff\xfe")
    (root / "large.txt").write_bytes(b"x" * (MAX_PROJECT_FILE_BYTES + 1))

    relative, content = read_project_text(root, "ok.txt")
    assert (relative, content) == ("ok.txt", "hello")
    with pytest.raises(SensitiveProjectPathError):
        read_project_text(root, "binary.dat")
    with pytest.raises(SensitiveProjectPathError):
        read_project_text(root, "large.txt")
    with pytest.raises(SensitiveProjectPathError):
        read_project_text(root, ".env")


def test_shared_listing_omits_denied_links_and_is_bounded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for name in ("a.txt", "b.txt", "c.txt", ".env", "credentials.json"):
        (root / name).write_text(name, encoding="utf-8")
    (root / ".aws").mkdir()
    (root / ".aws" / "credentials").write_text("secret", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "linked.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        link = None

    relative, entries, truncated = list_project_directory(root, ".", max_entries=2)

    assert relative == "."
    assert [entry.name for entry in entries] == ["a.txt", "b.txt"]
    assert truncated
    assert all(entry.name not in {".env", ".aws", "credentials.json"} for entry in entries)
    if link is not None:
        assert "linked.txt" not in [entry.name for entry in entries]


def test_shared_read_and_listing_reject_in_root_hardlinks(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("credential outside the project", encoding="utf-8")
    linked = root / "innocent-notes.txt"
    try:
        os.link(outside, linked)
    except OSError as exc:
        pytest.skip(f"hardlink creation unavailable: {exc}")

    with pytest.raises(SensitiveProjectPathError, match="hard link|unsafe"):
        read_project_text(root, linked.name)
    _relative, entries, _truncated = list_project_directory(root)
    assert linked.name not in [entry.name for entry in entries]


def _staged_handlers(session_id: str):
    from backend.agent.staging import build_staged_handlers
    from backend.plugin.builtins.tools import TOOL_HANDLERS

    return build_staged_handlers(TOOL_HANDLERS, session_id=session_id, run_id="run-1")


def test_builtin_read_and_list_enforce_sensitive_boundary(tmp_path):
    from backend.plugin.builtins.tools import TOOL_HANDLERS

    root = tmp_path / "project"
    root.mkdir()
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    (root / ".env.local").write_text("SECRET=x", encoding="utf-8")
    (root / "tokenizer.py").write_text("class Tokenizer: ...\n", encoding="utf-8")
    (root / ".aws").mkdir()
    (root / ".aws" / "credentials").write_text("secret", encoding="utf-8")
    ctx = {"cwd": str(root)}

    assert TOOL_HANDLERS["read_file"]({"path": "safe.txt"}, ctx) == "safe"
    assert TOOL_HANDLERS["read_file"]({"path": ".env.local"}, ctx).startswith("error:")
    assert TOOL_HANDLERS["list_files"]({"path": ".aws"}, ctx).startswith("error:")
    listing = TOOL_HANDLERS["list_files"]({"path": "."}, ctx)
    assert "safe.txt" in listing
    assert "tokenizer.py" in listing
    assert ".env.local" not in listing
    assert ".aws" not in listing


def test_builtin_direct_write_denies_sensitive_and_linked_paths(tmp_path):
    from backend.plugin.builtins.tools import TOOL_HANDLERS

    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = root / "linked"
    try:
        os.symlink(outside, linked, target_is_directory=True)
    except OSError:
        linked = None
    ctx = {"cwd": str(root)}

    safe = TOOL_HANDLERS["write_file"](
        {"path": "src/app.py", "content": "print('safe')\n"}, ctx
    )
    denied_secret = TOOL_HANDLERS["write_file"](
        {"path": ".env.local", "content": "SECRET=no"}, ctx
    )

    assert safe.startswith("wrote ")
    assert (root / "src" / "app.py").read_text(encoding="utf-8") == "print('safe')\n"
    assert denied_secret.startswith("error:")
    assert not (root / ".env.local").exists()

    outside_file = tmp_path / "outside-file.txt"
    outside_file.write_text("outside original", encoding="utf-8")
    hardlinked = root / "hardlinked.txt"
    try:
        os.link(outside_file, hardlinked)
    except OSError:
        hardlinked = None
    if hardlinked is not None:
        denied_hardlink = TOOL_HANDLERS["write_file"](
            {"path": hardlinked.name, "content": "must not follow"}, ctx
        )
        assert denied_hardlink.startswith("error:")
        assert outside_file.read_text(encoding="utf-8") == "outside original"

    symlinked = root / "symlinked.txt"
    try:
        os.symlink(outside_file, symlinked)
    except OSError:
        symlinked = None
    if symlinked is not None:
        denied_symlink = TOOL_HANDLERS["write_file"](
            {"path": symlinked.name, "content": "must not follow"}, ctx
        )
        assert denied_symlink.startswith("error:")
        assert outside_file.read_text(encoding="utf-8") == "outside original"

    if linked is not None:
        denied_link = TOOL_HANDLERS["write_file"](
            {"path": "linked/escape.txt", "content": "outside"}, ctx
        )
        assert denied_link.startswith("error:")
        assert not (outside / "escape.txt").exists()


def test_staged_handlers_deny_secret_reads_writes_and_list_overlays(
    isolated_home, tmp_path
):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    session_id = persistence.create_session(name="security", model="mock:1b")
    handlers = _staged_handlers(session_id)
    ctx = {"cwd": str(root)}

    denied = handlers["write_file"](
        {"path": ".env.local", "content": "SECRET=staged"}, ctx
    )
    assert denied.startswith("error:")
    assert persistence.list_staged_changes(session_id, status="pending") == []

    assert handlers["write_file"](
        {"path": "safe.txt", "content": "safe staged"}, ctx
    ).startswith("staged ")
    persistence.stage_change(
        session_id, "tampered", str(root), "secrets/token.txt", "leaked staged"
    )
    persistence.stage_change(
        session_id, "tampered", str(root), ".env.staged", "leaked staged"
    )

    assert handlers["read_file"]({"path": ".env.staged"}, ctx).startswith("error:")
    assert handlers["list_files"]({"path": "secrets"}, ctx).startswith("error:")
    listing = handlers["list_files"]({"path": "."}, ctx)
    assert "safe.txt (staged)" in listing
    assert "secrets" not in listing
    assert ".env.staged" not in listing


def test_staged_handlers_reject_linked_parent(isolated_home, tmp_path):
    from backend.cookbook import persistence

    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation unavailable")

    session_id = persistence.create_session(name="security", model="mock:1b")
    handlers = _staged_handlers(session_id)
    result = handlers["write_file"](
        {"path": "linked/new.txt", "content": "must not stage"},
        {"cwd": str(root)},
    )

    assert result.startswith("error:")
    assert persistence.list_staged_changes(session_id, status="pending") == []
    assert not (outside / "new.txt").exists()


def test_sandbox_composes_shared_policy_with_broader_snapshot_exclusions():
    from backend.agent.sandbox import _is_sensitive_rel

    assert not is_sensitive_project_path("node_modules/pkg/index.js")
    assert not is_sensitive_project_path("models/tiny.gguf")
    assert _is_sensitive_rel("node_modules/pkg/index.js")
    assert _is_sensitive_rel("models/tiny.gguf")
    assert _is_sensitive_rel(".envrc-production")
    assert not _is_sensitive_rel("src/tokenizer.py")
