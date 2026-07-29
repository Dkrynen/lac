"""Canonical deterministic counterbalancing for agent evaluation trials."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass


ARMS = ("raw", "stock", "lac")
_ARM_SET = frozenset(ARMS)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MIN_SEED_BASE = -(2**31)
_MAX_SEED = 2**31 - 1


class ScheduleError(ValueError):
    """A schedule input or derived schedule is malformed."""


@dataclass(frozen=True)
class GenerationSettings:
    temperature: float
    seed_base: int
    max_output_tokens: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not math.isfinite(float(self.temperature))
            or not 0 <= float(self.temperature) <= 2
        ):
            raise ScheduleError(
                "temperature must be finite and between 0 and 2"
            )
        if (
            type(self.seed_base) is not int
            or not _MIN_SEED_BASE <= self.seed_base <= _MAX_SEED
        ):
            raise ScheduleError(
                "seed_base must be an integer in the signed 31-bit range"
            )
        if (
            type(self.max_output_tokens) is not int
            or not 1 <= self.max_output_tokens <= 4096
        ):
            raise ScheduleError(
                "max_output_tokens must be an integer between 1 and 4096"
            )
        object.__setattr__(self, "temperature", float(self.temperature))

    def to_dict(self) -> dict[str, int | float]:
        return {
            "temperature": self.temperature,
            "seed_base": self.seed_base,
            "max_output_tokens": self.max_output_tokens,
        }


@dataclass(frozen=True)
class TrialSpec:
    index: int
    seed: int
    arm_order: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.index) is not int or not 1 <= self.index <= 3:
            raise ScheduleError("trial index must be an integer from 1 to 3")
        if type(self.seed) is not int or not 0 <= self.seed <= _MAX_SEED:
            raise ScheduleError(
                "trial seed must be an integer in the 31-bit range"
            )
        if (
            not isinstance(self.arm_order, tuple)
            or len(self.arm_order) != len(ARMS)
            or frozenset(self.arm_order) != _ARM_SET
            or len(set(self.arm_order)) != len(ARMS)
        ):
            raise ScheduleError(
                "trial arm_order must contain each known arm exactly once"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "seed": self.seed,
            "arm_order": list(self.arm_order),
        }


@dataclass(frozen=True)
class EvaluationSchedule:
    trials: tuple[TrialSpec, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.trials, tuple)
            or len(self.trials) != 3
            or tuple(trial.index for trial in self.trials) != (1, 2, 3)
            or len({trial.seed for trial in self.trials}) != 3
        ):
            raise ScheduleError(
                "schedule must contain three indexed trials with distinct seeds"
            )
        expected_orders = tuple(
            ARMS[offset:] + ARMS[:offset]
            for offset in range(3)
        )
        if tuple(trial.arm_order for trial in self.trials) != expected_orders:
            raise ScheduleError(
                "schedule arm orders must use the canonical cyclic balance"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "trials": [trial.to_dict() for trial in self.trials],
        }


def _validated_digests(
    model_digests: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(model_digests, Mapping):
        raise ScheduleError("model digests must be a mapping for all arms")
    if set(model_digests) != _ARM_SET:
        raise ScheduleError(
            "model digest arms must be exactly raw, stock, and lac"
        )
    selected: dict[str, str] = {}
    for arm in sorted(ARMS):
        digest = model_digests[arm]
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ScheduleError(f"model digest for {arm} is invalid")
        selected[arm] = digest
    return selected


def build_schedule(
    task_contract_sha256: str,
    model_digests: Mapping[str, str],
    generation: GenerationSettings,
    trials: int,
) -> EvaluationSchedule:
    if (
        not isinstance(task_contract_sha256, str)
        or not _DIGEST.fullmatch(task_contract_sha256)
    ):
        raise ScheduleError("task contract hash must be lowercase sha256")
    digests = _validated_digests(model_digests)
    if not isinstance(generation, GenerationSettings):
        raise ScheduleError("generation must be GenerationSettings")
    if type(trials) is not int or trials != 3:
        raise ScheduleError("trials must be exactly 3")

    specs = []
    for index in range(1, trials + 1):
        payload = {
            "task_contract_sha256": task_contract_sha256,
            "model_digests": digests,
            "generation": generation.to_dict(),
            "trial_index": index,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        seed = int.from_bytes(
            hashlib.sha256(encoded).digest()[:4],
            "big",
        ) >> 1
        offset = index - 1
        specs.append(
            TrialSpec(
                index,
                seed,
                ARMS[offset:] + ARMS[:offset],
            )
        )
    return EvaluationSchedule(tuple(specs))
