from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def _expected(value: str) -> str:
    match = SEMVER.fullmatch(str(value).strip())
    if match is None:
        raise ValueError("release version must be exact vX.Y.Z or X.Y.Z")
    return match.group(1)


def _backend_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise ValueError("backend/version.py has no literal __version__")


def _literal_version_assignment(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(values) != 1:
        raise ValueError(f"{path.name} must have one literal __version__ fallback")
    return values[0]


def _web_version(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("version") if isinstance(data, dict) else None
    if not isinstance(value, str):
        raise ValueError("web/package.json has no string version")
    return value


def _installer_version(path: Path) -> str:
    match = re.search(
        r'^#define\s+MyAppVersion\s+"([^"]+)"\s*$',
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if match is None:
        raise ValueError("installer.iss has no literal MyAppVersion")
    return match.group(1)


def _installed_audit_version(path: Path) -> str:
    match = re.search(
        r'/api/system/check-update",\s*current="([^"]+)"',
        path.read_text(encoding="utf-8"),
    )
    if match is None:
        raise ValueError("installed_app_audit.py has no literal update-check version")
    return match.group(1)


def check_versions(repo_root: Path, expected: str) -> dict:
    root = Path(repo_root)
    wanted = _expected(expected)
    versions = {
        "backend/version.py": _backend_version(root / "backend" / "version.py"),
        "backend/tui/app.py": _literal_version_assignment(root / "backend" / "tui" / "app.py"),
        "web/package.json": _web_version(root / "web" / "package.json"),
        "web/package-lock.json": _web_version(root / "web" / "package-lock.json"),
        "web/package-lock.json#root": _web_version(root / "web" / "package-lock.json").strip()
        if json.loads((root / "web" / "package-lock.json").read_text(encoding="utf-8")).get("packages", {}).get("", {}).get("version") == _web_version(root / "web" / "package-lock.json")
        else "package-lock-root-mismatch",
        "installer.iss": _installer_version(root / "installer.iss"),
        "scripts/installed_app_audit.py": _installed_audit_version(root / "scripts" / "installed_app_audit.py"),
    }
    mismatches = [name for name, value in versions.items() if value != wanted]
    return {
        "ok": not mismatches,
        "expected": wanted,
        "versions": versions,
        "mismatches": mismatches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Require source, web, and installer release versions to match exactly."
    )
    parser.add_argument("--expected", required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        report = check_versions(args.repo_root, args.expected)
    except (OSError, ValueError, json.JSONDecodeError, SyntaxError) as exc:
        report = {"ok": False, "error": str(exc)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
