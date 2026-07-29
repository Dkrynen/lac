import json

from scripts.third_party_audit import audit_distribution_contract, audit_ledger


def _component(**overrides):
    component = {
        "name": "OpenCode",
        "repository": "https://github.com/anomalyco/opencode",
        "commit": "7534d23551f665e65080809975b4ca5c7d63807b",
        "license": "MIT",
        "treatment": "external-runtime",
        "source_paths": ["sdks/vscode/src/extension.ts"],
        "local_paths": [],
        "modifications": "None; invoked through the LAC adapter.",
        "owner": "LAC runtime compatibility",
    }
    component.update(overrides)
    return component


def _write_ledger(tmp_path, component):
    path = tmp_path / "docs" / "third-party" / "upstream-components.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"schema_version": 1, "components": [component]}),
        encoding="utf-8",
    )
    return path


def test_audit_rejects_component_without_pinned_commit(tmp_path):
    _write_ledger(tmp_path, _component(commit="dev"))

    assert audit_ledger(tmp_path) == [
        "components[0].commit must be a 40-character lowercase SHA-1"
    ]


def test_audit_accepts_complete_pinned_component(tmp_path):
    _write_ledger(tmp_path, _component())

    assert audit_ledger(tmp_path) == []


def test_audit_rejects_unknown_or_missing_fields(tmp_path):
    component = _component()
    component.pop("owner")
    component["surprise"] = True
    _write_ledger(tmp_path, component)

    assert audit_ledger(tmp_path) == [
        "components[0] has unknown fields: surprise",
        "components[0] is missing fields: owner",
    ]


def test_distribution_contract_requires_notices_in_both_packages(tmp_path):
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("notices\n", encoding="utf-8")
    (tmp_path / "build.spec").write_text(
        'for extra in ["LICENSE"]:\n    pass\n',
        encoding="utf-8",
    )
    (tmp_path / "installer.iss").write_text(
        'Source: "LICENSE"; DestDir: "{app}"\n',
        encoding="utf-8",
    )

    assert audit_distribution_contract(tmp_path) == [
        "build.spec does not package THIRD_PARTY_NOTICES.md",
        "installer.iss does not package THIRD_PARTY_NOTICES.md",
    ]


def test_distribution_contract_accepts_packaged_notices(tmp_path):
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("notices\n", encoding="utf-8")
    (tmp_path / "build.spec").write_text(
        'for extra in ["LICENSE", "THIRD_PARTY_NOTICES.md"]:\n    pass\n',
        encoding="utf-8",
    )
    (tmp_path / "installer.iss").write_text(
        'Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"\n',
        encoding="utf-8",
    )

    assert audit_distribution_contract(tmp_path) == []
