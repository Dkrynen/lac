"""Fail-closed evidence verdicts for local agent evaluation artifacts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable


class EvidenceMode(str, Enum):
    VERIFIED = "verified"
    DIAGNOSTIC = "diagnostic"


class EvidenceState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNSUPPORTED = "unsupported"


REQUIRED_CONTROLS = (
    "runtime_dependency_provenance",
    "os_loopback_only_egress",
    "immutable_ollama_model_lineage",
    "sealed_fixture_materialization",
    "windows_process_tree_containment",
    "bounded_process_and_http_capture",
    "counterbalanced_deterministic_sampling",
    "artifact_ledger_integrity",
)


@dataclass(frozen=True)
class EvidenceControlResult:
    name: str
    state: EvidenceState
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceVerdict:
    mode: EvidenceMode
    results: tuple[EvidenceControlResult, ...]
    missing: tuple[str, ...]
    artifact_valid: bool

    @classmethod
    def from_results(
        cls,
        mode: EvidenceMode,
        results: Iterable[EvidenceControlResult],
    ) -> "EvidenceVerdict":
        items = tuple(results)
        names = [item.name for item in items]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError("duplicate evidence control: " + ", ".join(duplicates))
        unknown = sorted(set(names) - set(REQUIRED_CONTROLS))
        if unknown:
            raise ValueError("unknown evidence control: " + ", ".join(unknown))
        missing = tuple(name for name in REQUIRED_CONTROLS if name not in names)
        all_pass = not missing and all(
            item.state is EvidenceState.PASS for item in items
        )
        return cls(
            mode=mode,
            results=items,
            missing=missing,
            artifact_valid=mode is EvidenceMode.VERIFIED and all_pass,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "results": [asdict(item) for item in self.results],
            "missing": list(self.missing),
            "artifact_valid": self.artifact_valid,
        }
