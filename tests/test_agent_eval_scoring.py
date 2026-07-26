from dataclasses import FrozenInstanceError

import pytest

from backend.agent_eval.scoring import score_exact_text


def test_exact_text_score_normalizes_only_outer_whitespace():
    result = score_exact_text("\n  ZeroDivisionError \t", "ZeroDivisionError")

    assert result.passed is True
    assert result.score == 1.0
    assert result.actual == "ZeroDivisionError"
    assert result.expected == "ZeroDivisionError"


@pytest.mark.parametrize(
    "response",
    [
        "zerodivisionerror",
        "The answer is ZeroDivisionError",
        "ZeroDivisionError\nbecause the list is empty",
        "Zero Division Error",
    ],
)
def test_exact_text_score_does_not_apply_fuzzy_or_llm_judgment(response):
    result = score_exact_text(response, "ZeroDivisionError")

    assert result.passed is False
    assert result.score == 0.0


def test_exact_text_result_is_immutable():
    result = score_exact_text("x", "x")

    with pytest.raises(FrozenInstanceError):
        result.score = 0.0
