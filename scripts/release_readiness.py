from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.update import GITHUB_API, select_release_download_url  # noqa: E402
from backend.version import __version__ as APP_VERSION  # noqa: E402


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def read_json(url: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": f"LAC-release-readiness/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def read_bytes(url: str, timeout: int = 15) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": f"LAC-release-readiness/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp.status, headers, resp.read()


def check_local_installer(path: Path) -> dict[str, Any]:
    exists = path.exists()
    out: dict[str, Any] = {"path": str(path), "exists": exists}
    if exists:
        out.update({
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return out


def parse_sha256sums(text: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest = parts[0].strip().upper()
        name = parts[-1].lstrip("*")
        if len(digest) == 64 and all(ch in "0123456789ABCDEF" for ch in digest):
            checksums[name] = digest
    return checksums


def check_running_app(base_url: str, timeout: int = 15) -> dict[str, Any]:
    out: dict[str, Any] = {"base_url": base_url, "ok": False}
    try:
        version = read_json(f"{base_url}/api/system/version", timeout)
        plugins = read_json(f"{base_url}/api/plugins", timeout)
        status, headers, body = read_bytes(f"{base_url}/api/system/debug-bundle", timeout)
        debug = json.loads(body.decode("utf-8") or "{}")
    except Exception as exc:  # noqa: BLE001 - verifier reports failures instead of masking them
        out["error"] = str(exc)
        return out

    plugin_list = plugins if isinstance(plugins, list) else plugins.get("plugins", [])
    pro = next((p for p in plugin_list if isinstance(p, dict) and p.get("name") == "pro"), None)
    out.update({
        "ok": version.get("version") == APP_VERSION and status == 200,
        "version": version.get("version"),
        "app_name": version.get("app_name"),
        "debug_bundle": {
            "status": status,
            "attachment": "attachment" in headers.get("content-disposition", "").lower(),
            "version": (debug.get("app") or {}).get("version"),
        },
        "pro_plugin": pro or None,
    })
    return out


def check_public_release(local: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
    out: dict[str, Any] = {"api_url": GITHUB_API, "ok": False}
    try:
        data = read_json(GITHUB_API, timeout)
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        return out

    download_url = select_release_download_url(data, data.get("html_url"))
    assets = data.get("assets") or []
    asset = next(
        (a for a in assets if isinstance(a, dict) and a.get("browser_download_url") == download_url),
        None,
    )
    checksum_asset = next(
        (a for a in assets if isinstance(a, dict) and a.get("name") == "SHA256SUMS.txt"),
        None,
    )
    local_size = local.get("size_bytes")
    expected_tag = f"v{APP_VERSION}"
    published_sha256 = None
    checksum_error = None
    if checksum_asset and checksum_asset.get("browser_download_url"):
        try:
            _, _, checksum_body = read_bytes(checksum_asset["browser_download_url"], timeout)
            sums = parse_sha256sums(checksum_body.decode("utf-8", errors="replace"))
            asset_name = asset.get("name") if asset else None
            published_sha256 = sums.get(str(asset_name)) if asset_name else None
        except Exception as exc:  # noqa: BLE001 - report checksum fetch failures as data
            checksum_error = str(exc)
    out.update({
        "ok": bool(download_url),
        "tag": data.get("tag_name"),
        "expected_tag": expected_tag,
        "published_matches_local_version": data.get("tag_name") == expected_tag,
        "html_url": data.get("html_url"),
        "download_url": download_url,
        "asset_name": asset.get("name") if asset else None,
        "asset_size_bytes": asset.get("size") if asset else None,
        "sha256_asset_name": checksum_asset.get("name") if checksum_asset else None,
        "published_sha256": published_sha256,
        "checksum_error": checksum_error,
        "local_matches_published_size": bool(asset and local_size and asset.get("size") == local_size),
        "local_matches_published_sha256": bool(
            published_sha256
            and local.get("sha256")
            and published_sha256 == local.get("sha256")
        ),
    })
    return out


def strict_public_match_ok(public_release: dict[str, Any]) -> bool:
    return bool(
        public_release.get("local_matches_published_size")
        and public_release.get("published_matches_local_version")
        and public_release.get("local_matches_published_sha256")
    )


def default_installer_path(version: str = APP_VERSION) -> Path:
    return ROOT / "dist" / f"LAC-Setup-{version}.exe"


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    installer = check_local_installer(Path(args.installer))
    report = {
        "version": APP_VERSION,
        "local_installer": installer,
        "installed_app": check_running_app(args.app_url, args.timeout) if not args.skip_app else {"skipped": True},
        "public_release": (
            check_public_release(installer, args.timeout)
            if not args.skip_public
            else {"skipped": True}
        ),
    }
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only LAC release readiness verifier.")
    p.add_argument("--installer", default=str(default_installer_path()), help="Local installer path to inspect.")
    p.add_argument("--app-url", default="http://127.0.0.1:5050", help="Running LAC app base URL.")
    p.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    p.add_argument("--skip-app", action="store_true", help="Do not query the running local app.")
    p.add_argument("--skip-public", action="store_true", help="Do not query GitHub releases.")
    p.add_argument(
        "--strict-public-match",
        action="store_true",
        help="Exit non-zero unless the latest public tag, installer size, and SHA256SUMS entry match this local build.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(args)
    print(json.dumps(report, indent=2, sort_keys=True))

    local_ok = bool(report["local_installer"].get("exists"))
    app_ok = args.skip_app or bool(report["installed_app"].get("ok"))
    public_ok = args.skip_public or bool(report["public_release"].get("ok"))
    public_match_ok = (
        not args.strict_public_match
        or args.skip_public
        or strict_public_match_ok(report["public_release"])
    )
    return 0 if local_ok and app_ok and public_ok and public_match_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
