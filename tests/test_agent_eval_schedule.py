from dataclasses import FrozenInstanceError

import pytest

from backend.agent_eval.schedule import (
    EvaluationSchedule,
    GenerationSettings,
    ScheduleError,
    TrialSpec,
    build_schedule,
)


TASK_HASH = "a" * 64
MODEL_DIGESTS = {
    "raw": "b" * 64,
    "stock": "b" * 64,
    "lac": "c" * 64,
}
GENERATION = GenerationSettings(
    temperature=1.0,
    seed_base=20260726,
    max_output_tokens=128,
)


def test_build_schedule_is_canonical_counterbalanced_and_immutable():
    schedule = build_schedule(
        TASK_HASH,
        MODEL_DIGESTS,
        GENERATION,
        3,
    )

    assert schedule == EvaluationSchedule(
        trials=(
            TrialSpec(1, 1209934845, ("raw", "stock", "lac")),
            TrialSpec(2, 656122670, ("stock", "lac", "raw")),
            TrialSpec(3, 950887272, ("lac", "raw", "stock")),
        )
    )
    assert len({trial.seed for trial in schedule.trials}) == 3
    with pytest.raises(FrozenInstanceError):
        schedule.trials = ()
    with pytest.raises(FrozenInstanceError):
        schedule.trials[0].seed = 1


def test_build_schedule_is_independent_of_model_digest_mapping_order():
    reversed_digests = {
        "stock": "b" * 64,
        "raw": "b" * 64,
        "lac": "c" * 64,
    }

    assert build_schedule(
        TASK_HASH,
        reversed_digests,
        GENERATION,
        3,
    ) == build_schedule(
        TASK_HASH,
        MODEL_DIGESTS,
        GENERATION,
        3,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", True),
        ("temperature", float("nan")),
        ("temperature", float("inf")),
        ("temperature", -0.01),
        ("temperature", 2.01),
        ("seed_base", True),
        ("seed_base", -(2**31) - 1),
        ("seed_base", 2**31),
        ("max_output_tokens", True),
        ("max_output_tokens", 0),
        ("max_output_tokens", 4097),
    ],
)
def test_generation_settings_reject_invalid_values(field, value):
    values = {
        "temperature": 1.0,
        "seed_base": 20260726,
        "max_output_tokens": 128,
    }
    values[field] = value

    with pytest.raises((ScheduleError, ValueError), match=field):
        GenerationSettings(**values)


@pytest.mark.parametrize(
    ("task_hash", "digests", "generation", "trials", "message"),
    [
        ("A" * 64, MODEL_DIGESTS, GENERATION, 3, "task"),
        ("a" * 63, MODEL_DIGESTS, GENERATION, 3, "task"),
        (
            TASK_HASH,
            {"raw": "b" * 64, "stock": "b" * 64},
            GENERATION,
            3,
            "arms",
        ),
        (
            TASK_HASH,
            {**MODEL_DIGESTS, "other": "d" * 64},
            GENERATION,
            3,
            "arms",
        ),
        (
            TASK_HASH,
            {**MODEL_DIGESTS, "lac": "D" * 64},
            GENERATION,
            3,
            "digest",
        ),
        (TASK_HASH, MODEL_DIGESTS, object(), 3, "generation"),
        (TASK_HASH, MODEL_DIGESTS, GENERATION, True, "trials"),
        (TASK_HASH, MODEL_DIGESTS, GENERATION, 2, "trials"),
    ],
)
def test_build_schedule_rejects_malformed_inputs(
    task_hash,
    digests,
    generation,
    trials,
    message,
):
    with pytest.raises((ScheduleError, ValueError, TypeError), match=message):
        build_schedule(
            task_hash,
            digests,
            generation,
            trials,
        )


@pytest.mark.parametrize(
    "arm_order",
    [
        ("raw", "raw", "lac"),
        ("raw", "stock", "other"),
        ("raw", "stock"),
    ],
)
def test_trial_spec_rejects_duplicate_unknown_or_missing_arms(arm_order):
    with pytest.raises((ScheduleError, ValueError), match="arm"):
        TrialSpec(1, 1, arm_order)


@pytest.mark.parametrize(
    ("index", "seed"),
    [(True, 1), (0, 1), (1, True), (1, -1), (1, 2**31)],
)
def test_trial_spec_rejects_bool_or_out_of_range_numbers(index, seed):
    with pytest.raises((ScheduleError, ValueError), match="index|seed"):
        TrialSpec(index, seed, ("raw", "stock", "lac"))
