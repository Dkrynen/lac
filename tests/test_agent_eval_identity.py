from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.agent_eval.identity as identity_module
from backend.agent_eval.identity import (
    IdentityError,
    _default_authenticode,
    _package_metadata,
    _wrapper_target,
    capture_model_identities,
    compare_identity_payloads,
    file_identity,
)


def _responses(*, lac_digest="b" * 64, parent="gpt-oss:20b", from_digest="c" * 64):
    return {
        "/api/tags": {
            "models": [
                {"name": "gpt-oss:20b", "digest": "a" * 64, "size": 13, "details": {"family": "gptoss"}},
                {"name": "gpt-oss:20b-agent", "digest": lac_digest, "size": 14, "details": {"family": "gptoss", "parent_model": parent}},
            ]
        },
        "gpt-oss:20b": {
            "details": {"parent_model": ""},
            "modelfile": "FROM sha256:" + from_digest,
            "parameters": "temperature 1",
            "template": "base",
            "model_info": {},
            "capabilities": ["completion", "tools"],
        },
        "gpt-oss:20b-agent": {
            "details": {"parent_model": parent},
            "modelfile": "FROM C:\\\\models\\\\sha256-" + from_digest,
            "parameters": "num_ctx 131072\\ntemperature 1",
            "template": "agent",
            "model_info": {},
            "capabilities": ["completion", "tools"],
        },
    }


def test_file_identity_hashes_exact_executable_bytes(tmp_path):
    binary = tmp_path / "opencode.exe"
    binary.write_bytes(b"opencode-1.18.4")
    identity = file_identity(binary, version="1.18.4", authenticode_fn=lambda _: "unsigned")
    assert identity.path == binary.resolve()
    assert identity.size == len(b"opencode-1.18.4")
    assert len(identity.sha256) == 64


def test_file_identity_rejects_link(tmp_path):
    target = tmp_path / "target.exe"
    target.write_bytes(b"target")
    link = tmp_path / "link.exe"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(IdentityError, match="link|reparse"):
        file_identity(link, version="1.18.4")


def test_file_identity_denies_same_metadata_replacement_during_signature_capture(
    tmp_path,
):
    binary = tmp_path / "replace.exe"
    binary.write_bytes(b"before")
    original = binary.stat()
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"after!")
    replacement.touch()
    replacement_time = original.st_mtime_ns
    import os

    os.utime(
        replacement,
        ns=(replacement_time, replacement_time),
    )
    observed = []

    def replace_during_signature(_path):
        try:
            replacement.replace(binary)
        except OSError:
            observed.append("denied")
        else:
            observed.append("replaced")
        return "unsigned"

    rejected = False
    try:
        file_identity(
            binary,
            version="1.18.4",
            authenticode_fn=replace_during_signature,
        )
    except IdentityError:
        rejected = True

    assert observed == ["denied"] or rejected


def test_file_identity_holds_lock_during_version_probe(tmp_path):
    binary = tmp_path / "opencode.exe"
    binary.write_bytes(b"before")
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"after!")
    observed = []

    def probe(_path):
        try:
            replacement.replace(binary)
        except OSError:
            observed.append("denied")
        else:
            observed.append("replaced")
        return "1.18.4"

    identity = file_identity(
        binary,
        version=None,
        version_fn=probe,
        authenticode_fn=lambda _path: "unsigned",
    )

    assert observed == ["denied"]
    assert identity.version == "1.18.4"


def test_runtime_lease_acquisition_closes_prior_files_on_failure(
    monkeypatch,
):
    snapshot = identity_module.EvaluationIdentitySnapshot.for_test()
    closed = []
    calls = 0

    class Lease:
        def close(self):
            closed.append("closed")

    def acquire(_expected):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise IdentityError("second lease failed")
        return Lease()

    monkeypatch.setattr(
        identity_module,
        "_acquire_file_lease",
        acquire,
        raising=False,
    )

    with pytest.raises(IdentityError, match="second lease failed"):
        identity_module.acquire_runtime_identity_leases(snapshot)

    assert closed == ["closed"]


def test_model_identity_requires_full_digest_and_exact_variant_parent():
    responses = _responses()
    identities = capture_model_identities(
        "gpt-oss:20b", "gpt-oss:20b-agent", fetch_fn=lambda key: responses[key]
    )
    assert identities.lac.parent_model == "gpt-oss:20b"
    assert identities.lac.from_blob_sha256 == "c" * 64


def test_model_identity_payload_is_deeply_immutable():
    identities = capture_model_identities(
        "gpt-oss:20b", "gpt-oss:20b-agent", fetch_fn=lambda key: _responses()[key]
    )
    with pytest.raises(TypeError):
        identities.lac.details["parent_model"] = "mutated"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda data: data["/api/tags"]["models"].pop(), "missing"),
        (lambda data: data["/api/tags"]["models"].append(dict(data["/api/tags"]["models"][0])), "duplicate"),
        (lambda data: data["/api/tags"]["models"].__setitem__(0, {**data["/api/tags"]["models"][0], "digest": "a" * 63}), "digest"),
        (lambda data: data["gpt-oss:20b-agent"]["details"].__setitem__("parent_model", "wrong:model"), "parent"),
        (lambda data: data["gpt-oss:20b-agent"].__setitem__("modelfile", "FROM local-name"), "FROM"),
    ],
)
def test_model_identity_rejects_unpinned_or_wrong_lineage(mutate, message):
    responses = _responses()
    mutate(responses)
    with pytest.raises(IdentityError, match=message):
        capture_model_identities("gpt-oss:20b", "gpt-oss:20b-agent", fetch_fn=lambda key: responses[key])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("modelfile", []),
        ("parameters", {}),
        ("template", None),
        ("details", []),
        ("model_info", []),
        ("capabilities", {}),
    ],
)
def test_model_identity_rejects_wrong_show_field_types(field, value):
    responses = _responses()
    responses["gpt-oss:20b-agent"][field] = value

    with pytest.raises(IdentityError, match="invalid"):
        capture_model_identities(
            "gpt-oss:20b",
            "gpt-oss:20b-agent",
            fetch_fn=lambda key: responses[key],
        )


def test_model_identity_rejects_lac_from_digest_that_differs_from_base():
    responses = _responses()
    responses["gpt-oss:20b-agent"]["modelfile"] = "FROM sha256:" + "d" * 64

    with pytest.raises(IdentityError, match="FROM digest"):
        capture_model_identities(
            "gpt-oss:20b",
            "gpt-oss:20b-agent",
            fetch_fn=lambda key: responses[key],
        )


def test_wrapper_target_rejects_expected_path_hidden_in_dead_comment(tmp_path):
    wrapper = tmp_path / "opencode.cmd"
    target = tmp_path / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"expected")
    (tmp_path / "evil.exe").write_bytes(b"evil")
    wrapper.write_text(
        'REM %~dp0\\node_modules\\opencode-ai\\bin\\opencode.exe\n'
        '"%~dp0\\evil.exe" %*\n',
        encoding="utf-8",
    )

    with pytest.raises(IdentityError, match="supported executable target"):
        _wrapper_target(wrapper)


@pytest.mark.parametrize("dp0", ["%~dp0", "%dp0%"])
def test_wrapper_target_accepts_only_direct_supported_command(tmp_path, dp0):
    wrapper = tmp_path / "opencode.cmd"
    target = tmp_path / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"expected")
    wrapper.write_text(
        f'@ECHO off\n"{dp0}\\node_modules\\opencode-ai\\bin\\opencode.exe" %*\n',
        encoding="utf-8",
    )

    assert _wrapper_target(wrapper) == target


def test_package_metadata_is_found_at_npm_package_root(tmp_path):
    binary = (
        tmp_path
        / "node_modules"
        / "opencode-ai"
        / "bin"
        / "opencode.exe"
    )
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"opencode")
    package = binary.parent.parent / "package.json"
    package.write_text('{"name":"opencode-ai","version":"1.18.4"}', encoding="utf-8")

    assert _package_metadata(binary) == package


@pytest.mark.parametrize(
    ("status", "returncode", "expected"),
    [
        ("Valid", 0, "valid"),
        ("NotSigned", 0, "unsigned"),
        ("HashMismatch", 0, "invalid"),
        ("", 1, "unavailable"),
        ("Valid", 1, "unavailable"),
        ("NotSigned", 1, "unavailable"),
    ],
)
def test_authenticode_status_mapping_is_truthful(
    tmp_path, monkeypatch, status, returncode, expected
):
    monkeypatch.setattr(
        identity_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=status + ("\n" if status else ""),
            returncode=returncode,
        ),
    )

    assert _default_authenticode(tmp_path / "runtime.exe") == expected


def test_postflight_model_or_binary_drift_fails():
    before = {"runtime": {"opencode": {"sha256": "a" * 64}}, "models": {"base": {"digest": "b" * 64}}}
    after = {"runtime": {"opencode": {"sha256": "c" * 64}}, "models": {"base": {"digest": "b" * 64}}}
    result = compare_identity_payloads(before, after)
    assert result.state.value == "fail"
    assert "opencode" in result.reason


def test_postflight_show_hash_version_and_config_drift_fail():
    before = {"runtime": {"ollama_version": "0.9", "config_sha256": {"stock": "a" * 64}}, "models": {"base": {"show_sha256": "b" * 64}}}
    after = {"runtime": {"ollama_version": "1.0", "config_sha256": {"stock": "c" * 64}}, "models": {"base": {"show_sha256": "d" * 64}}}
    assert compare_identity_payloads(before, after).state.value == "fail"
