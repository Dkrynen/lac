import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALL = re.compile(r"subprocess\.(run|Popen|check_output|check_call|call)\b")
ALLOWED = {ROOT / "backend" / "cookbook" / "proc.py"}  # the wrapper itself

# Lines carrying this marker are a deliberately deferred exception, not a miss.
# kill_pids() in server.py is rewritten (not just re-routed) in Task A3, so its
# raw subprocess.run call is left untouched for now.
NOQA_MARKER = "# proc-noqa"


def _sources():
    files = list((ROOT / "backend").rglob("*.py")) + [ROOT / "server.py"]
    for f in files:
        if "__pycache__" in f.parts or f in ALLOWED:
            continue
        yield f


def test_no_raw_subprocess_calls_outside_proc():
    offenders = []
    for f in _sources():
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if CALL.search(line) and NOQA_MARKER not in line:
                offenders.append(f"{f.relative_to(ROOT)}:{i}")
    assert offenders == [], (
        "raw subprocess calls must route through backend.cookbook.proc: "
        + ", ".join(offenders)
    )
