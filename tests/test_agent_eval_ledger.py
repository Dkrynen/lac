import pytest

from backend.agent_eval.evidence import (
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
)
from backend.agent_eval.ledger import (
    ArtifactLedgerError,
    atomic_write_json,
    seal_evidence,
    verify_evidence,
)


def test_atomic_write_refuses_existing_destination(tmp_path):
    target = tmp_path / "result.json"
    atomic_write_json(target, {"first": True})

    with pytest.raises(FileExistsError):
        atomic_write_json(target, {"second": True})


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
