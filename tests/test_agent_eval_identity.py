from pathlib import Path

import pytest

from backend.agent_eval.identity import (
    IdentityError,
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


def test_file_identity_rejects_replacement_between_stat_and_hash(tmp_path, monkeypatch):
    binary = tmp_path / "replace.exe"
    binary.write_bytes(b"before")
    original_open = Path.open
    def replace_after_open(*args, **kwargs):
        handle = original_open(*args, **kwargs)
        if Path(args[0]) == binary and args[1:2] == ("rb",):
            binary.write_bytes(b"after!")
        return handle
    monkeypatch.setattr(Path, "open", replace_after_open)
    with pytest.raises(IdentityError, match="changed"):
        file_identity(binary, version="1.18.4")


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
