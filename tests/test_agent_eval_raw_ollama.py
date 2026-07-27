from __future__ import annotations

from pathlib import Path

import backend.agent_eval.raw_ollama as raw_module
from backend.agent_eval.raw_ollama import build_raw_prompt, run_raw
from backend.agent_eval.schedule import GenerationSettings, TrialSpec
from backend.agent_eval.task import EvalScorer, EvalTask


def _task(tmp_path: Path) -> EvalTask:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "stats_service.py").write_text(
        "def summarize(values):\n"
        "    return sum(values) / len(values)\n",
        encoding="utf-8",
    )
    return EvalTask(
        schema_version=1,
        id="python-empty-mean",
        prompt="Name the exception. Answer with only the class name.",
        fixture_root=fixture,
        timeout_seconds=180,
        scorer=EvalScorer(type="exact_text", expected="ZeroDivisionError"),
    )


def test_run_raw_sends_bounded_fixture_prompt_without_tools(tmp_path):
    captured = {}

    def request(host, body, timeout):
        captured.update(host=host, body=body, timeout=timeout)
        return {
            "message": {"role": "assistant", "content": "ZeroDivisionError"},
            "done": True,
            "prompt_eval_count": 41,
            "eval_count": 3,
            "eval_duration": 1_500_000_000,
            "total_duration": 2_000_000_000,
            "load_duration": 100_000_000,
        }

    result = run_raw(
        _task(tmp_path),
        "gpt-oss:20b",
        "http://127.0.0.1:11434",
        request_fn=request,
    )

    assert captured["host"] == "http://127.0.0.1:11434"
    assert captured["timeout"] == 180
    assert captured["body"]["model"] == "gpt-oss:20b"
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"] == {"seed": 0, "temperature": 0}
    assert set(captured["body"]) == {"model", "messages", "stream", "options"}
    assert len(captured["body"]["messages"]) == 1
    prompt = captured["body"]["messages"][0]["content"]
    assert "Name the exception" in prompt
    assert "--- FILE: stats_service.py ---" in prompt
    assert "return sum(values) / len(values)" in prompt
    assert "ZeroDivisionError" not in prompt

    assert result.arm == "raw"
    assert result.model == "gpt-oss:20b"
    assert result.runtime == "ollama"
    assert result.completed is True
    assert result.timed_out is False
    assert result.response == "ZeroDivisionError"
    assert result.errors == ()
    assert result.metrics["prompt_eval_count"] == 41
    assert result.metrics["eval_count"] == 3
    assert result.metrics["tokens_per_second"] == 2.0
    assert result.metrics["eval_duration_ms"] == 1500.0
    assert result.metrics["total_duration_ms"] == 2000.0
    assert result.metrics["load_duration_ms"] == 100.0
    assert result.request_metadata == {
        "stream": False,
        "options": {"seed": 0, "temperature": 0},
        "response_max_bytes": 8 * 1024 * 1024,
    }
    assert result.capture["response"]["overflowed"] is False


def test_run_raw_applies_identical_trial_generation_to_body_and_metadata(
    tmp_path,
):
    captured = {}
    generation = GenerationSettings(1.0, 20260726, 128)
    trial = TrialSpec(1, 1209934845, ("raw", "stock", "lac"))

    result = run_raw(
        _task(tmp_path),
        "gpt-oss:20b",
        "http://127.0.0.1:11434",
        generation=generation,
        trial=trial,
        request_fn=lambda host, body, timeout: (
            captured.update(host=host, body=body, timeout=timeout)
            or {
                "message": {"content": "ZeroDivisionError"},
                "done": True,
            }
        ),
    )

    assert captured["body"]["options"] == {
        "temperature": 1.0,
        "seed": 1209934845,
        "num_predict": 128,
    }
    assert result.request_metadata == {
        "stream": False,
        "options": {
            "temperature": 1.0,
            "seed": 1209934845,
            "num_predict": 128,
        },
        "response_max_bytes": 8 * 1024 * 1024,
        "trial_index": 1,
    }


def test_raw_prompt_builder_exposes_the_exact_auditable_input(tmp_path):
    prompt = build_raw_prompt(_task(tmp_path))

    assert prompt.startswith("Name the exception.")
    assert "--- FILE: stats_service.py ---" in prompt
    assert "ZeroDivisionError" not in prompt


def test_run_raw_records_timeout_instead_of_raising(tmp_path):
    def request(*_args):
        raise TimeoutError("generation exceeded deadline")

    result = run_raw(
        _task(tmp_path),
        "gpt-oss:20b",
        "http://localhost:11434",
        request_fn=request,
    )

    assert result.completed is False
    assert result.timed_out is True
    assert result.response == ""
    assert result.errors == ("timeout: generation exceeded deadline",)


def test_run_raw_records_transport_error_instead_of_raising(tmp_path):
    def request(*_args):
        raise RuntimeError("HTTP 500: model crashed")

    result = run_raw(
        _task(tmp_path),
        "gpt-oss:20b",
        "http://[::1]:11434",
        request_fn=request,
    )

    assert result.completed is False
    assert result.timed_out is False
    assert result.errors == ("RuntimeError: HTTP 500: model crashed",)


def test_run_raw_marks_empty_response_as_failed_evidence(tmp_path):
    result = run_raw(
        _task(tmp_path),
        "gpt-oss:20b",
        "http://localhost:11434",
        request_fn=lambda *_: {"message": {"content": ""}, "done": True},
    )

    assert result.completed is False
    assert result.timed_out is False
    assert result.errors == ("empty_response",)


def test_run_raw_refuses_non_loopback_ollama_before_request(tmp_path):
    called = False

    def request(*_args):
        nonlocal called
        called = True
        return {}

    result = run_raw(
        _task(tmp_path),
        "gpt-oss:20b",
        "http://192.168.1.50:11434",
        request_fn=request,
    )

    assert called is False
    assert result.completed is False
    assert result.errors == ("non_loopback_ollama_host",)


def test_run_raw_records_response_overflow_without_retaining_body(
    tmp_path, monkeypatch
):
    max_bytes = 8 * 1024 * 1024

    class OverflowResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self):
            return "http://127.0.0.1:11434/api/chat"

        def read(self, size):
            assert size == max_bytes + 1
            return b"x" * size

    monkeypatch.setattr(
        raw_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: OverflowResponse(),
    )

    result = run_raw(
        _task(tmp_path),
        "gpt-oss:20b",
        "http://127.0.0.1:11434",
    )

    assert result.completed is False
    assert result.errors == ("response_body_overflow",)
    assert result.response == ""
    assert result.raw_stdout == ""
    assert result.capture["response"] == {
        "allowed_bytes": max_bytes,
        "observed_bytes": max_bytes + 1,
        "overflowed": True,
    }
