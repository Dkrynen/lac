"""Deterministic Phase 0 evaluation scoring."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreResult:
    passed: bool
    score: float
    actual: str
    expected: str
    scorer: str = "exact_text"


def score_exact_text(response: str, expected: str) -> ScoreResult:
    """Compare after outer-whitespace normalization only.

    Case folding, substring matches, semantic judges, and answer extraction make
    a small baseline look better than the captured model response. Phase 0 keeps
    the contract intentionally strict and auditable.
    """

    actual = str(response).strip()
    target = str(expected).strip()
    passed = actual == target
    return ScoreResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        actual=actual,
        expected=target,
    )
