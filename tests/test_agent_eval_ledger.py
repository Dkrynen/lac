import json
import threading
from types import SimpleNamespace

import pytest

from backend.agent_eval import ledger
from backend.agent_eval.evidence import (
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
)
from backend.agent_eval.ledger import (
    ArtifactLedgerError,
    atomic_write_bytes,
    atomic_write_json,
    seal_evidence,
    verify_evidence,
)


def test_atomic_write_refuses_existing_destination(tmp_path):
    target = tmp_path / "result.json"
    atomic_write_json(target, {"first": True})

    with pytest.raises(FileExistsError):
        atomic_write_json(target, {"second": True})


def test_atomic_write_preserves_unowned_existing_temporary_sibling(
    tmp_path, monkeypatch
):
    target = tmp_path / "result.bin"
    temporary = tmp_path / ".result.bin.fixed.tmp"
    temporary.write_bytes(b"attacker-sentinel")
    monkeypatch.setattr(ledger.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))

    with pytest.raises(FileExistsError):
        atomic_write_bytes(target, b"intended")

    assert not target.exists()
    assert temporary.exists()
    assert temporary.read_bytes() == b"attacker-sentinel"


def test_atomic_write_is_create_only_when_two_writers_reach_promotion_together(
    tmp_path, monkeypatch
):
    target = tmp_path / "result.bin"
    promotion_barrier = threading.Barrier(2)
    real_replace = ledger.os.replace
    real_link = ledger.os.link
    outcomes = []
    outcomes_lock = threading.Lock()

    def synchronized_replace(source, destination):
        promotion_barrier.wait(timeout=5)
        return real_replace(source, destination)

    def synchronized_link(source, destination):
        promotion_barrier.wait(timeout=5)
        return real_link(source, destination)

    def write(payload):
        try:
            atomic_write_bytes(target, payload)
            outcome = ("created", payload)
        except FileExistsError:
            outcome = ("exists", payload)
        with outcomes_lock:
            outcomes.append(outcome)

    monkeypatch.setattr(ledger.os, "replace", synchronized_replace)
    monkeypatch.setattr(ledger.os, "link", synchronized_link)
    first = threading.Thread(target=write, args=(b"first",))
    second = threading.Thread(target=write, args=(b"second",))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert [state for state, _payload in outcomes].count("created") == 1
    assert [state for state, _payload in outcomes].count("exists") == 1
    winner = next(payload for state, payload in outcomes if state == "created")
    assert target.read_bytes() == winner


def test_seal_hashes_every_non_workspace_artifact_and_detects_tampering(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    atomic_write_json(run / "manifest.json", {"schema_version": 2})
    (run / "workspaces").mkdir()
    (run / "workspaces" / "fixture.py").write_text("ignored workspace")

    evidence = seal_evidence(
        run,
        EvidenceMode.DIAGNOSTIC,
        preliminary_results=[],
    )

    assert evidence["artifact_valid"] is False
    assert "manifest.json" in evidence["artifacts"]
    assert "workspaces/fixture.py" not in evidence["artifacts"]
    assert evidence["controls"]["results"][-1]["name"] == "artifact_ledger_integrity"
    assert evidence["controls"]["results"][-1]["state"] == "pass"
    assert verify_evidence(run).ok is True

    (run / "manifest.json").write_text('{"tampered": true}')
    assert verify_evidence(run).ok is False


def test_verify_rejects_tampered_diagnostic_validity_metadata(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    atomic_write_json(run / "manifest.json", {"schema_version": 2})
    seal_evidence(run, EvidenceMode.DIAGNOSTIC, preliminary_results=[])

    evidence_path = run / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["artifact_valid"] = True
    evidence["controls"]["artifact_valid"] = True
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    verification = verify_evidence(run)
    assert verification.ok is False
    assert "artifact_valid" in (verification.reason or "")


@pytest.mark.parametrize("bad_count", [999, True])
def test_verify_rejects_tampered_or_boolean_ledger_artifact_count(
    tmp_path, bad_count
):
    run = tmp_path / "run"
    run.mkdir()
    atomic_write_json(run / "manifest.json", {"schema_version": 2})
    seal_evidence(run, EvidenceMode.DIAGNOSTIC, preliminary_results=[])

    evidence_path = run / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["controls"]["results"][-1]["details"]["artifact_count"] = bad_count
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    verification = verify_evidence(run)
    assert verification.ok is False
    assert "artifact_count" in (verification.reason or "")


def test_verify_rejects_duplicate_artifact_ledger_control(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    atomic_write_json(run / "manifest.json", {"schema_version": 2})
    seal_evidence(run, EvidenceMode.DIAGNOSTIC, preliminary_results=[])

    evidence_path = run / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    ledger_result = evidence["controls"]["results"][-1]
    evidence["controls"]["results"].append(ledger_result)
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    verification = verify_evidence(run)
    assert verification.ok is False
    assert "artifact_ledger_integrity" in (verification.reason or "")


def test_verify_rejects_duplicate_top_level_json_key(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    atomic_write_json(run / "manifest.json", {"schema_version": 2})
    seal_evidence(run, EvidenceMode.DIAGNOSTIC, preliminary_results=[])

    evidence_path = run / "evidence.json"
    serialized = evidence_path.read_text(encoding="utf-8")
    evidence_path.write_text(
        serialized.replace(
            '  "artifact_valid": false,',
            '  "artifact_valid": true,\n  "artifact_valid": false,',
            1,
        ),
        encoding="utf-8",
    )

    verification = verify_evidence(run)
    assert verification.ok is False
    assert "duplicate JSON key" in (verification.reason or "")


def test_seal_refuses_unknown_temporary_or_partial_files(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / ".manifest.json.tmp").write_text("partial")

    with pytest.raises(ArtifactLedgerError, match="temporary artifact"):
        seal_evidence(
            run,
            EvidenceMode.DIAGNOSTIC,
            preliminary_results=[],
        )


def test_seal_rejects_regular_file_at_excluded_root(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "workspaces").write_text("not a directory")

    with pytest.raises(ArtifactLedgerError, match="excluded root must be a directory"):
        seal_evidence(run, EvidenceMode.DIAGNOSTIC, preliminary_results=[])


def test_seal_rejects_symlink_at_excluded_root_when_supported(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    target = tmp_path / "workspace-target"
    target.mkdir()
    excluded = run / "workspaces"
    try:
        excluded.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ArtifactLedgerError, match="link artifact"):
        seal_evidence(run, EvidenceMode.DIAGNOSTIC, preliminary_results=[])


def test_seal_rejects_reparse_excluded_root(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    (run / "workspaces").mkdir()
    monkeypatch.setattr(
        ledger,
        "_is_reparse_point",
        lambda path: path.name == "workspaces",
    )

    with pytest.raises(ArtifactLedgerError, match="reparse-point artifact"):
        seal_evidence(run, EvidenceMode.DIAGNOSTIC, preliminary_results=[])


def test_seal_rejects_temporary_excluded_root_name(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "workspaces.tmp").mkdir()

    with pytest.raises(ArtifactLedgerError, match="temporary artifact"):
        seal_evidence(
            run,
            EvidenceMode.DIAGNOSTIC,
            preliminary_results=[],
            excluded_roots=("workspaces.tmp",),
        )


def test_seal_rejects_reserved_absent_evidence_output_exclusion(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    atomic_write_json(run / "manifest.json", {"schema_version": 2})
    evidence_path = run / "evidence.json"
    assert not evidence_path.exists()

    with pytest.raises(ArtifactLedgerError, match="reserved evidence output"):
        seal_evidence(
            run,
            EvidenceMode.DIAGNOSTIC,
            preliminary_results=[],
            excluded_roots=("evidence.json",),
        )

    assert not evidence_path.exists()


def test_evidence_root_hash_is_independent_of_artifact_creation_order(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    (first / "b.json").write_text("second")
    (first / "a.json").write_text("first")
    (second / "a.json").write_text("first")
    (second / "b.json").write_text("second")

    first_evidence = seal_evidence(
        first, EvidenceMode.DIAGNOSTIC, preliminary_results=[]
    )
    second_evidence = seal_evidence(
        second, EvidenceMode.DIAGNOSTIC, preliminary_results=[]
    )

    assert first_evidence["artifacts"] == second_evidence["artifacts"]
    assert first_evidence["evidence_root_sha256"] == second_evidence[
        "evidence_root_sha256"
    ]


def test_caller_cannot_preclaim_ledger_integrity(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    preclaimed = EvidenceControlResult(
        "artifact_ledger_integrity",
        EvidenceState.PASS,
        "not measured",
        {},
    )

    with pytest.raises(ArtifactLedgerError, match="computed only while sealing"):
        seal_evidence(
            run,
            EvidenceMode.VERIFIED,
            preliminary_results=[preclaimed],
        )
