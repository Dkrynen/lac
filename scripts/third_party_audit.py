"""Validate LAC's manually curated upstream provenance ledger."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


LEDGER_PATH = Path("docs/third-party/upstream-components.json")
_COMPONENT_FIELDS = {
    "name",
    "repository",
    "commit",
    "license",
    "treatment",
    "source_paths",
    "local_paths",
    "modifications",
    "owner",
}
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_TREATMENTS = {"external-runtime", "adapted-code", "research-reference"}


def audit_ledger(repo_root: Path) -> list[str]:
    """Return deterministic findings; an empty list means the ledger is valid."""
    path = Path(repo_root) / LEDGER_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{LEDGER_PATH.as_posix()} is unreadable: {exc}"]

    findings: list[str] = []
    if set(payload) != {"schema_version", "components"}:
        findings.append("ledger fields must be exactly: components, schema_version")
    if payload.get("schema_version") != 1:
        findings.append("schema_version must be 1")
    components = payload.get("components")
    if not isinstance(components, list):
        return findings + ["components must be an array"]

    seen_names: set[str] = set()
    for index, component in enumerate(components):
        prefix = f"components[{index}]"
        if not isinstance(component, dict):
            findings.append(f"{prefix} must be an object")
            continue
        fields = set(component)
        unknown = sorted(fields - _COMPONENT_FIELDS)
        missing = sorted(_COMPONENT_FIELDS - fields)
        if unknown:
            findings.append(f"{prefix} has unknown fields: {', '.join(unknown)}")
        if missing:
            findings.append(f"{prefix} is missing fields: {', '.join(missing)}")
        if unknown or missing:
            continue

        name = component["name"]
        if not isinstance(name, str) or not name.strip():
            findings.append(f"{prefix}.name must be a non-empty string")
        elif name.casefold() in seen_names:
            findings.append(f"{prefix}.name must be unique")
        else:
            seen_names.add(name.casefold())
        if not _SHA1_RE.fullmatch(str(component["commit"])):
            findings.append(
                f"{prefix}.commit must be a 40-character lowercase SHA-1"
            )
        repository = component["repository"]
        if (
            not isinstance(repository, str)
            or not repository.startswith("https://github.com/")
        ):
            findings.append(f"{prefix}.repository must be an HTTPS GitHub URL")
        if component["treatment"] not in _TREATMENTS:
            findings.append(
                f"{prefix}.treatment must be one of: "
                f"{', '.join(sorted(_TREATMENTS))}"
            )
        for field in ("source_paths", "local_paths"):
            value = component[field]
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item for item in value
            ):
                findings.append(f"{prefix}.{field} must be an array of paths")
        for field in ("license", "modifications", "owner"):
            value = component[field]
            if not isinstance(value, str) or not value.strip():
                findings.append(f"{prefix}.{field} must be a non-empty string")
    return findings


def audit_distribution_contract(repo_root: Path) -> list[str]:
    """Require the notice artifact in both portable and installer packages."""
    root = Path(repo_root)
    findings: list[str] = []
    notices = root / "THIRD_PARTY_NOTICES.md"
    if not notices.is_file() or not notices.read_text(
        encoding="utf-8"
    ).strip():
        findings.append("THIRD_PARTY_NOTICES.md is missing or empty")
    for filename in ("build.spec", "installer.iss"):
        path = root / filename
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            findings.append(f"{filename} is unreadable")
            continue
        if "THIRD_PARTY_NOTICES.md" not in text:
            findings.append(
                f"{filename} does not package THIRD_PARTY_NOTICES.md"
            )
    return findings


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    findings = audit_ledger(repo_root) + audit_distribution_contract(repo_root)
    if findings:
        for finding in findings:
            print(f"FAIL: {finding}", file=sys.stderr)
        return 1
    print(f"OK: {LEDGER_PATH.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
