from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"


def _job(text: str, name: str, next_name: str) -> str:
    return text.split(f"  {name}:\n", 1)[1].split(f"\n  {next_name}:\n", 1)[0]


def test_python_ci_installs_the_pinned_build_test_dependency():
    python_job = _job(WORKFLOW.read_text(encoding="utf-8"), "python", "web")

    assert "pyinstaller==6.21.0" in python_job


def test_worker_ci_uses_the_node_version_required_by_wrangler():
    worker_job = _job(WORKFLOW.read_text(encoding="utf-8"), "worker", "secrets")

    assert "node-version: 22.19.0" in worker_job
