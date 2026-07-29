"""Reproducible local-agent evaluation primitives.

The package starts with a deliberately narrow, trusted read-only task contract.
Live runtime adapters are added behind this boundary rather than letting an
evaluation manifest become an arbitrary-code execution format.
"""

from .scoring import ScoreResult, score_exact_text
from .result import ArmResult
from .task import EvalScorer, EvalTask, EvalTaskError, load_task, snapshot_fixture

__all__ = [
    "ArmResult",
    "EvalScorer",
    "EvalTask",
    "EvalTaskError",
    "ScoreResult",
    "load_task",
    "score_exact_text",
    "snapshot_fixture",
]
