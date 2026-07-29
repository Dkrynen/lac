import pytest

from backend.agent_eval.evidence import (
    EvidenceControlResult,
    EvidenceMode,
    EvidenceState,
    EvidenceVerdict,
    REQUIRED_CONTROLS,
)


def passed(name: str) -> EvidenceControlResult:
    return EvidenceControlResult(name, EvidenceState.PASS, "verified", {})


def test_verified_verdict_requires_every_named_control():
    results = [passed(name) for name in REQUIRED_CONTROLS]
    verdict = EvidenceVerdict.from_results(EvidenceMode.VERIFIED, results)

    assert verdict.artifact_valid is True
    assert verdict.missing == ()


def test_verified_verdict_fails_closed_for_missing_duplicate_or_failed_control():
    results = [passed(name) for name in REQUIRED_CONTROLS[:-1]]
    verdict = EvidenceVerdict.from_results(EvidenceMode.VERIFIED, results)

    assert verdict.artifact_valid is False
    assert verdict.missing == (REQUIRED_CONTROLS[-1],)

    with pytest.raises(ValueError, match="duplicate evidence control"):
        EvidenceVerdict.from_results(
            EvidenceMode.VERIFIED, [passed(REQUIRED_CONTROLS[0])] * 2
        )

    failed = [passed(name) for name in REQUIRED_CONTROLS]
    failed[0] = EvidenceControlResult(
        REQUIRED_CONTROLS[0], EvidenceState.FAIL, "measurement failed", {}
    )
    failed_verdict = EvidenceVerdict.from_results(EvidenceMode.VERIFIED, failed)
    assert failed_verdict.missing == ()
    assert failed_verdict.artifact_valid is False


def test_diagnostic_mode_can_never_be_valid():
    results = [passed(name) for name in REQUIRED_CONTROLS]
    verdict = EvidenceVerdict.from_results(EvidenceMode.DIAGNOSTIC, results)

    assert verdict.artifact_valid is False
    assert verdict.mode is EvidenceMode.DIAGNOSTIC
