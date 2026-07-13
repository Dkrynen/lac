from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_release_version.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_release_version", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path: Path, *, backend="2.7.0", web="2.7.0", installer="2.7.0") -> Path:
    root = tmp_path / "repo"
    (root / "backend" / "tui").mkdir(parents=True, exist_ok=True)
    (root / "web").mkdir(exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "backend" / "version.py").write_text(
        f'__version__ = "{backend}"\n', encoding="utf-8"
    )
    (root / "web" / "package.json").write_text(
        json.dumps({"name": "lac-web", "version": web}), encoding="utf-8"
    )
    (root / "web" / "package-lock.json").write_text(
        json.dumps({"name": "lac-web", "version": web, "packages": {"": {"version": web}}}),
        encoding="utf-8",
    )
    (root / "backend" / "tui" / "app.py").write_text(
        f'try:\n    from backend.version import __version__\nexcept Exception:\n    __version__ = "{backend}"\n',
        encoding="utf-8",
    )
    (root / "scripts" / "installed_app_audit.py").write_text(
        f'q("/api/system/check-update", current="{backend}")\n', encoding="utf-8"
    )
    (root / "installer.iss").write_text(
        f'#define MyAppVersion "{installer}"\n', encoding="utf-8"
    )
    return root


def test_release_version_contract_accepts_one_exact_semver(tmp_path):
    module = _load()
    report = module.check_versions(_tree(tmp_path), "v2.7.0")

    assert report == {
        "ok": True,
        "expected": "2.7.0",
        "versions": {
            "backend/version.py": "2.7.0",
            "backend/tui/app.py": "2.7.0",
            "web/package.json": "2.7.0",
            "web/package-lock.json": "2.7.0",
            "web/package-lock.json#root": "2.7.0",
            "installer.iss": "2.7.0",
            "scripts/installed_app_audit.py": "2.7.0",
        },
        "mismatches": [],
    }


def test_release_version_contract_rejects_label_drift(tmp_path):
    module = _load()
    report = module.check_versions(
        _tree(tmp_path, backend="2.6.4", web="2.7.0", installer="2.7.1"),
        "2.7.0",
    )

    assert report["ok"] is False
    assert report["mismatches"] == [
        "backend/version.py",
        "backend/tui/app.py",
        "installer.iss",
        "scripts/installed_app_audit.py",
    ]


def test_release_version_contract_rejects_non_exact_semver(tmp_path):
    module = _load()
    for value in ("2.7", "v2.7.0-rc1", "2.7.0 trailing", "../../2.7.0"):
        try:
            module.check_versions(_tree(tmp_path), value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid version to fail: {value!r}")
