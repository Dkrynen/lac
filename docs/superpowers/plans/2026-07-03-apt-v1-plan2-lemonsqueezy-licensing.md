# APT v1 — Plan 2: LemonSqueezy Licensing + Calibration Insights

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the apt-pro license stub's internals with real LemonSqueezy activation (`apt pro activate/deactivate`), online revalidation with an offline grace window — behind the frozen `check()`/`require()` contract — and ship the third Pro pillar, `apt pro insights` (calibration history + regression detection).

**Architecture:** New `apt_pro/ls.py` wraps LemonSqueezy's public License API (form-encoded POSTs to `api.lemonsqueezy.com/v1/licenses/*`, no API key) behind an injectable transport. `apt_pro/license.py` keeps its exact public contract (`Grant`, `check()`, `require()`, `GRANT_PATH`) but the grant file becomes a cached-validation record ({key, instance_id, product_id, plan, status, expires_at, last_validated_at}); `check()` revalidates online at most every REVALIDATE_DAYS, degrades to a GRACE_DAYS offline window, and hard-invalidates on an explicit `valid: false`. Insights reads core's `backend.cookbook.benchmark.history()` and flags per-model tok/s regressions.

**Tech Stack:** Python 3.10+ stdlib only (urllib, form-encoded). All LS HTTP via injectable callables (tests use fakes; no network in tests). Repo: `C:\Users\User\repos\apt-pro` (private; never gets a public remote).

## Global Constraints

- Spec: `model-hub/docs/superpowers/specs/2026-07-02-apt-v1-public-launch-design.md` §4–5. LS research reference: activate/validate/deactivate are PUBLIC endpoints, `application/x-www-form-urlencoded`, 60 req/min; success shapes carry `license_key{status: active|expired|disabled, expires_at}`, `instance{id}`, `meta{product_id, variant_name}`.
- **Frozen contract:** `Grant(key, plan, expires_at: float)` with `.valid`/`.expires_human`; `check() -> Grant | None` NEVER raises and never blocks long (2s HTTP timeout, one attempt); `require(feature)` exits 3. `APT_PRO_DEV=1` override stays. Existing tests in `tests/test_license.py` MUST keep passing unmodified except where a test asserts stub-only internals (none do — they use the public contract).
- **No-rug-pull rule (spec §4):** an expired/lapsed grant locks Pro commands but never touches the user's Ollama models or configs.
- Product identity: `APT_PRO_PRODUCT_ID` module constant (0 = not yet configured → product check skipped, warning printed; Duan sets the real id when the LS store exists). Env override `APT_PRO_PRODUCT_ID` for test mode.
- Tests: `cd C:\Users\User\repos\apt-pro && C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` — currently 23 passed; must stay green every task. No test may hit the network.
- Core repo untouched by this plan (except ledger/doc notes). Insights reads `~/.model-hub/benchmarks/results.jsonl` via core's `benchmark.history()` — READ ONLY.
- Live/test-mode E2E against a real LS store is **Duan-gated** (account + product + test-mode key) — the plan ends with a checklist, not an automated step.

---

### Task 1: `apt_pro/ls.py` — LemonSqueezy License API client

**Files:**
- Create: `apt_pro/ls.py`
- Test: `tests/test_ls.py`

**Interfaces:**
- Produces:
  - `LS_BASE = "https://api.lemonsqueezy.com/v1/licenses"`
  - `activate(license_key: str, instance_name: str, post=None) -> dict`
  - `validate(license_key: str, instance_id: str | None = None, post=None) -> dict`
  - `deactivate(license_key: str, instance_id: str, post=None) -> dict`
  - Each returns the parsed response JSON dict verbatim. `post(url: str, form: dict) -> dict` is injectable; default `_http_post` uses urllib, form-encoded, `Accept: application/json`, timeout 5s, and — key detail — **reads the JSON body even on HTTP 4xx** (LS returns meaningful JSON like `{"valid": false, "error": ...}` with error status codes; use `urllib.error.HTTPError.read()`).
  - `class LsError(Exception)` raised only for transport-level failures (network down, non-JSON body); API-level "invalid key" is NOT an exception, it's data.

- [ ] **Step 1: Write failing tests**

`tests/test_ls.py`:
```python
"""LemonSqueezy License API client: request shape + error semantics."""
import json

import pytest

from apt_pro.ls import activate, validate, deactivate, LsError, LS_BASE


def _capture(reply):
    calls = []

    def post(url, form):
        calls.append((url, dict(form)))
        return reply

    return post, calls


def test_activate_request_shape():
    post, calls = _capture({"activated": True, "instance": {"id": "i-1"}})
    out = activate("KEY-1", "duan-pc", post=post)
    assert out["activated"] is True
    url, form = calls[0]
    assert url == f"{LS_BASE}/activate"
    assert form == {"license_key": "KEY-1", "instance_name": "duan-pc"}


def test_validate_with_and_without_instance():
    post, calls = _capture({"valid": True})
    validate("KEY-1", "i-1", post=post)
    assert calls[0] == (f"{LS_BASE}/validate", {"license_key": "KEY-1", "instance_id": "i-1"})
    validate("KEY-1", post=post)
    assert calls[1] == (f"{LS_BASE}/validate", {"license_key": "KEY-1"})


def test_deactivate_request_shape():
    post, calls = _capture({"deactivated": True})
    out = deactivate("KEY-1", "i-1", post=post)
    assert out["deactivated"] is True
    assert calls[0] == (f"{LS_BASE}/deactivate", {"license_key": "KEY-1", "instance_id": "i-1"})


def test_api_level_invalid_is_data_not_exception():
    post, _ = _capture({"valid": False, "error": "License key not found or is invalid.",
                        "license_key": None})
    out = validate("BAD-KEY", post=post)
    assert out["valid"] is False
    assert "invalid" in out["error"]


def test_transport_failure_raises_lserror():
    def post(url, form):
        raise LsError("network down")

    with pytest.raises(LsError):
        validate("KEY-1", post=post)
```

- [ ] **Step 2: Run — verify fail**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest tests/test_ls.py -q` (in apt-pro)
Expected: FAIL — `ModuleNotFoundError: apt_pro.ls`

- [ ] **Step 3: Implement**

`apt_pro/ls.py`:
```python
"""LemonSqueezy License API client (public endpoints, form-encoded).

activate/validate/deactivate return the parsed response JSON verbatim —
API-level rejection ({"valid": false, ...}) is DATA, not an exception.
LsError is raised only for transport failures (network, non-JSON body).
Docs: https://docs.lemonsqueezy.com/api/license-api  (60 req/min)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

LS_BASE = "https://api.lemonsqueezy.com/v1/licenses"


class LsError(Exception):
    """Transport-level failure talking to LemonSqueezy."""


def _http_post(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        # LS returns meaningful JSON bodies on 4xx (e.g. invalid key) — read them.
        try:
            body = e.read().decode()
        except Exception as exc:  # noqa: BLE001
            raise LsError(f"HTTP {e.code} with unreadable body") from exc
    except Exception as exc:  # noqa: BLE001 — DNS, timeout, refused…
        raise LsError(str(exc)) from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise LsError(f"non-JSON response: {body[:200]}") from exc


def activate(license_key: str, instance_name: str, post=None) -> dict:
    post = post or _http_post
    return post(f"{LS_BASE}/activate",
                {"license_key": license_key, "instance_name": instance_name})


def validate(license_key: str, instance_id: str | None = None, post=None) -> dict:
    post = post or _http_post
    form: dict = {"license_key": license_key}
    if instance_id:
        form["instance_id"] = instance_id
    return post(f"{LS_BASE}/validate", form)


def deactivate(license_key: str, instance_id: str, post=None) -> dict:
    post = post or _http_post
    return post(f"{LS_BASE}/deactivate",
                {"license_key": license_key, "instance_id": instance_id})
```

- [ ] **Step 4: Run — verify pass, commit**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` (in apt-pro) — 28 passed.
```bash
git add -A && git commit -m "feat: LemonSqueezy License API client (public form-encoded endpoints, injectable transport)"
```

### Task 2: Grant store v2 + revalidation/grace in `check()`

**Files:**
- Modify: `apt_pro/license.py`
- Test: `tests/test_license.py` (extend — existing 7 tests must pass UNCHANGED)

**Interfaces:**
- Consumes: `apt_pro.ls.validate`, `LsError`.
- Produces (additive to the frozen contract):
  - Grant file schema v2 (JSON at `GRANT_PATH`): `{key, instance_id, product_id, plan, status, expires_at, last_validated_at}`. `expires_at` may be `null` (perpetual/subscription-managed) → maps to far-future float internally.
  - Constants: `REVALIDATE_DAYS = 3`, `GRACE_DAYS = 14`, `FAR_FUTURE = 4102444800.0` (2100-01-01).
  - `save_grant(data: dict) -> None`, `_load_raw() -> dict | None`.
  - `check(validate_fn=None) -> Grant | None` — order:
    1. `APT_PRO_DEV=1` → dev grant (unchanged).
    2. Load grant file; missing/corrupt → None.
    3. **Legacy v1 grants** (no `last_validated_at` key — e.g. hand-written stub grants) keep working exactly as before: expiry check only, no revalidation. (Backward compat = the old tests pass unchanged.)
    4. v2 grants: if `status != "active"` → None. If `now - last_validated_at < REVALIDATE_DAYS*86400` → grant from cache. Else attempt `validate_fn(key, instance_id)`:
       - `valid: true` + `status: active` → update `last_validated_at` (+ refreshed `expires_at`/`status`), save, return grant.
       - `valid: false` (explicit rejection) → set `status` from response (`expired`/`disabled`/`invalid`), save, return None. **Hard invalidation.**
       - `LsError` (offline) → **grace**: if `now - last_validated_at <= GRACE_DAYS*86400` return grant from cache, else None.
    5. Everything wrapped so `check()` never raises.
  - `require()` unchanged.

- [ ] **Step 1: Write failing tests (append to tests/test_license.py)**

```python
# ---- v2 grants: revalidation + grace -----------------------------------

def _write_v2(path, *, status="active", last_validated_delta=0, expires_delta=None):
    body = {
        "key": "K2", "instance_id": "i-1", "product_id": 77, "plan": "pro",
        "status": status,
        "expires_at": (time.time() + expires_delta) if expires_delta is not None else None,
        "last_validated_at": time.time() + last_validated_delta,
    }
    path.write_text(json.dumps(body))


def test_v2_fresh_cache_no_network():
    _write_v2(lic.GRANT_PATH, last_validated_delta=-3600)  # validated 1h ago
    calls = []
    grant = lic.check(validate_fn=lambda *a: calls.append(a) or {"valid": True})
    assert grant is not None and grant.key == "K2"
    assert calls == []  # within REVALIDATE_DAYS -> no network attempt


def test_v2_stale_revalidates_and_updates(monkeypatch):
    _write_v2(lic.GRANT_PATH, last_validated_delta=-4 * 86400)  # stale
    def ok(key, instance_id):
        return {"valid": True,
                "license_key": {"status": "active", "expires_at": None}}
    grant = lic.check(validate_fn=ok)
    assert grant is not None
    saved = json.loads(lic.GRANT_PATH.read_text())
    assert time.time() - saved["last_validated_at"] < 60  # refreshed


def test_v2_explicit_invalid_hard_locks():
    _write_v2(lic.GRANT_PATH, last_validated_delta=-4 * 86400)
    def rejected(key, instance_id):
        return {"valid": False, "error": "License key has expired.",
                "license_key": {"status": "expired", "expires_at": None}}
    assert lic.check(validate_fn=rejected) is None
    saved = json.loads(lic.GRANT_PATH.read_text())
    assert saved["status"] == "expired"
    # and it STAYS locked even if the network now dies (no grace after hard invalid)
    from apt_pro.ls import LsError
    def offline(key, instance_id):
        raise LsError("down")
    assert lic.check(validate_fn=offline) is None


def test_v2_offline_within_grace_passes():
    from apt_pro.ls import LsError
    _write_v2(lic.GRANT_PATH, last_validated_delta=-5 * 86400)  # stale but < GRACE_DAYS
    def offline(key, instance_id):
        raise LsError("down")
    grant = lic.check(validate_fn=offline)
    assert grant is not None


def test_v2_offline_beyond_grace_locks():
    from apt_pro.ls import LsError
    _write_v2(lic.GRANT_PATH, last_validated_delta=-20 * 86400)  # > GRACE_DAYS
    def offline(key, instance_id):
        raise LsError("down")
    assert lic.check(validate_fn=offline) is None


def test_v2_validate_fn_raising_unexpected_never_escapes():
    _write_v2(lic.GRANT_PATH, last_validated_delta=-4 * 86400)
    def bug(key, instance_id):
        raise ValueError("unexpected bug")
    # never raises; treated like offline (within grace here)
    assert lic.check(validate_fn=bug) is not None
```

- [ ] **Step 2: Run — verify fail**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest tests/test_license.py -q`
Expected: new tests FAIL (`check() got an unexpected keyword argument 'validate_fn'`); the 7 original tests still pass.

- [ ] **Step 3: Implement — replace apt_pro/license.py body**

Keep the module docstring style; full new implementation:
```python
"""License gate — LemonSqueezy-backed.

Grant file (~/.model-hub/license.json) is a cached-validation record:
{key, instance_id, product_id, plan, status, expires_at, last_validated_at}.
check() revalidates online at most every REVALIDATE_DAYS, tolerates
GRACE_DAYS offline against the cache, hard-locks on an explicit
"valid: false" from LemonSqueezy. Legacy v1 grants ({key, plan,
expires_at}) keep working (expiry-only) so dev/hand-issued grants and
Plan-1 behavior are unchanged. Contract frozen: check() never raises,
require() exits 3, APT_PRO_DEV=1 overrides.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

GRANT_PATH = Path.home() / ".model-hub" / "license.json"
REVALIDATE_DAYS = 3
GRACE_DAYS = 14
FAR_FUTURE = 4102444800.0  # 2100-01-01: LS expires_at null == subscription-managed

_UPGRADE_MSG = (
    "\n  '{feature}' is an APT Pro feature.\n"
    "  Get a license: https://apt.example/pro  (dev override: set APT_PRO_DEV=1)\n"
)


@dataclass
class Grant:
    key: str
    plan: str
    expires_at: float

    @property
    def valid(self) -> bool:
        return self.expires_at > time.time()

    @property
    def expires_human(self) -> str:
        if self.expires_at >= FAR_FUTURE:
            return "while subscribed"
        return datetime.fromtimestamp(self.expires_at).strftime("%Y-%m-%d")


def _load_raw() -> dict | None:
    try:
        return json.loads(GRANT_PATH.read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt == unlicensed
        return None


def save_grant(data: dict) -> None:
    GRANT_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRANT_PATH.write_text(json.dumps(data, indent=2))


def _expiry_float(expires_at) -> float:
    """LS ISO string | epoch float | None -> epoch float."""
    if expires_at is None:
        return FAR_FUTURE
    if isinstance(expires_at, (int, float)):
        return float(expires_at)
    try:
        return datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _grant_from(data: dict) -> Grant:
    return Grant(key=str(data["key"]), plan=str(data.get("plan", "pro")),
                 expires_at=_expiry_float(data.get("expires_at")))


def check(validate_fn=None) -> Grant | None:
    """Return the active grant, or None. Never raises, never blocks long."""
    if os.environ.get("APT_PRO_DEV") == "1":
        return Grant(key="dev", plan="dev", expires_at=time.time() + 86400)

    data = _load_raw()
    if data is None or "key" not in data:
        return None

    try:
        # Legacy v1 grants (stub-era / hand-issued): expiry-only, no revalidation.
        if "last_validated_at" not in data:
            grant = _grant_from(data)
            return grant if grant.valid else None

        if data.get("status") != "active":
            return None

        now = time.time()
        age = now - float(data.get("last_validated_at", 0))
        if age < REVALIDATE_DAYS * 86400:
            grant = _grant_from(data)
            return grant if grant.valid else None

        # Stale — try online revalidation.
        if validate_fn is None:
            from apt_pro.ls import validate as validate_fn  # noqa: PLC0415
        from apt_pro.ls import LsError

        try:
            resp = validate_fn(data["key"], data.get("instance_id"))
        except LsError:
            resp = None          # offline -> grace below
        except Exception:        # noqa: BLE001 — bug in transport = same as offline
            resp = None

        if resp is not None:
            lk = resp.get("license_key") or {}
            if resp.get("valid") and lk.get("status") == "active":
                data["last_validated_at"] = now
                data["status"] = "active"
                data["expires_at"] = lk.get("expires_at", data.get("expires_at"))
                save_grant(data)
                grant = _grant_from(data)
                return grant if grant.valid else None
            # Explicit rejection -> hard lock.
            data["status"] = lk.get("status") or "invalid"
            save_grant(data)
            return None

        # Offline: grace window against the cache.
        if age <= GRACE_DAYS * 86400:
            grant = _grant_from(data)
            return grant if grant.valid else None
        return None
    except Exception:  # noqa: BLE001 — the gate must never raise
        return None


def require(feature: str) -> Grant:
    """Return the grant or exit(3) with an upgrade message naming the feature."""
    grant = check()
    if grant is None:
        print(_UPGRADE_MSG.format(feature=feature))
        raise SystemExit(3)
    return grant
```

- [ ] **Step 4: Run — verify pass (old 7 + new 6), full pro suite, commit**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` — expect 34 passed.
Sanity: `test_valid_grant_file`/`test_expired_grant_is_none` (v1 grants) pass UNCHANGED — proves backward compat.
```bash
git add -A && git commit -m "feat: grant store v2 — LS revalidation cadence, offline grace, hard lock on explicit invalid (contract frozen)"
```

### Task 3: `apt pro activate <key>` + `apt pro deactivate`

**Files:**
- Create: `apt_pro/activate.py`
- Modify: `apt_pro/plugin.py` (register both via `_SUBCOMMANDS`)
- Test: `tests/test_activate.py`

**Interfaces:**
- Consumes: `apt_pro.ls.{activate, deactivate, LsError}`, `apt_pro.license.{save_grant, _load_raw, GRANT_PATH}`.
- Produces:
  - `APT_PRO_PRODUCT_ID = 0` module constant (0 = unset → product check skipped with a printed warning; env `APT_PRO_PRODUCT_ID` overrides — set at store-creation time / test mode).
  - `do_activate(key: str, instance_name: str, activate_fn=None) -> tuple[bool, str]` — calls LS activate; on `activated: true`: verify `meta.product_id` matches (when configured), build + `save_grant` the v2 record (status/expires from `license_key`, plan from `meta.variant_name` lowercased, `last_validated_at=now`), return `(True, msg)`. On rejection return `(False, error-from-response)`. On `LsError` return `(False, "network: ...")`. Never raises.
  - `do_deactivate(deactivate_fn=None) -> tuple[bool, str]` — reads grant; no grant → `(False, ...)`; calls LS deactivate with stored key+instance_id; on success DELETES the grant file; LS failure still deletes locally (seat may leak — message says so). Never raises.
  - `configure_activate(parser)` / `configure_deactivate(parser)` for `_SUBCOMMANDS` (`activate` takes positional `key`; both set `func=`). NOT license-gated (you must be able to activate while unlicensed).

- [ ] **Step 1: Write failing tests**

`tests/test_activate.py`:
```python
"""Activation flow: LS response -> saved v2 grant; deactivate frees + deletes."""
import json

import pytest

import apt_pro.license as lic
import apt_pro.activate as act


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("APT_PRO_DEV", raising=False)
    monkeypatch.delenv("APT_PRO_PRODUCT_ID", raising=False)
    monkeypatch.setattr(lic, "GRANT_PATH", tmp_path / "license.json")


def _ls_ok(product_id=77):
    return {
        "activated": True, "error": None,
        "license_key": {"status": "active", "key": "K-9",
                        "expires_at": "2027-01-01T00:00:00.000000Z"},
        "instance": {"id": "inst-42", "name": "duan-pc"},
        "meta": {"product_id": product_id, "variant_name": "Pro"},
    }


def test_activate_saves_v2_grant():
    ok, msg = act.do_activate("K-9", "duan-pc", activate_fn=lambda k, n: _ls_ok())
    assert ok, msg
    saved = json.loads(lic.GRANT_PATH.read_text())
    assert saved["key"] == "K-9"
    assert saved["instance_id"] == "inst-42"
    assert saved["status"] == "active"
    assert saved["plan"] == "pro"
    assert saved["last_validated_at"] > 0
    # and check() now grants without network
    assert lic.check(validate_fn=lambda *a: (_ for _ in ()).throw(AssertionError)) is not None


def test_activate_rejection_bubbles_error():
    rej = {"activated": False, "error": "This license key has reached the activation limit.",
           "license_key": {"status": "active"}, "meta": {}}
    ok, msg = act.do_activate("K-9", "pc", activate_fn=lambda k, n: rej)
    assert not ok
    assert "activation limit" in msg
    assert not lic.GRANT_PATH.exists()


def test_activate_wrong_product_rejected(monkeypatch):
    monkeypatch.setenv("APT_PRO_PRODUCT_ID", "77")
    ok, msg = act.do_activate("K-9", "pc", activate_fn=lambda k, n: _ls_ok(product_id=999))
    assert not ok
    assert "different product" in msg
    assert not lic.GRANT_PATH.exists()


def test_activate_network_failure():
    from apt_pro.ls import LsError

    def down(k, n):
        raise LsError("no dns")

    ok, msg = act.do_activate("K-9", "pc", activate_fn=down)
    assert not ok and "network" in msg


def test_deactivate_deletes_grant():
    act.do_activate("K-9", "pc", activate_fn=lambda k, n: _ls_ok())
    calls = []
    ok, msg = act.do_deactivate(deactivate_fn=lambda k, i: calls.append((k, i)) or {"deactivated": True})
    assert ok
    assert calls == [("K-9", "inst-42")]
    assert not lic.GRANT_PATH.exists()


def test_deactivate_without_grant():
    ok, msg = act.do_deactivate(deactivate_fn=lambda k, i: {"deactivated": True})
    assert not ok


def test_cli_commands_registered():
    import argparse
    from apt_pro.plugin import PLUGIN
    parser = argparse.ArgumentParser(prog="apt")
    sub = parser.add_subparsers(dest="command")
    PLUGIN.register_cli(sub)
    a = parser.parse_args(["pro", "activate", "SOME-KEY"])
    assert a.key == "SOME-KEY" and callable(a.func)
    d = parser.parse_args(["pro", "deactivate"])
    assert callable(d.func)
```

- [ ] **Step 2: Run — verify fail**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest tests/test_activate.py -q`
Expected: FAIL — `apt_pro.activate` missing.

- [ ] **Step 3: Implement**

`apt_pro/activate.py`:
```python
"""`apt pro activate <key>` / `apt pro deactivate` — seat management."""
from __future__ import annotations

import os
import socket
import time

from apt_pro import ls
from apt_pro.license import GRANT_PATH, _load_raw, save_grant

APT_PRO_PRODUCT_ID = 0  # set when the LemonSqueezy store/product exists; 0 = skip check


def _configured_product_id() -> int:
    env = os.environ.get("APT_PRO_PRODUCT_ID")
    if env and env.isdigit():
        return int(env)
    return APT_PRO_PRODUCT_ID


def do_activate(key: str, instance_name: str, activate_fn=None) -> tuple[bool, str]:
    activate_fn = activate_fn or ls.activate
    try:
        resp = activate_fn(key, instance_name)
    except ls.LsError as e:
        return False, f"network: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"unexpected: {e}"

    if not resp.get("activated"):
        return False, str(resp.get("error") or "activation rejected")

    meta = resp.get("meta") or {}
    want = _configured_product_id()
    if want == 0:
        print("  [warn] APT_PRO_PRODUCT_ID not configured — product check skipped")
    elif meta.get("product_id") != want:
        return False, "this key belongs to a different product"

    lk = resp.get("license_key") or {}
    inst = resp.get("instance") or {}
    save_grant({
        "key": key,
        "instance_id": inst.get("id"),
        "product_id": meta.get("product_id"),
        "plan": str(meta.get("variant_name") or "pro").lower(),
        "status": lk.get("status", "active"),
        "expires_at": lk.get("expires_at"),
        "last_validated_at": time.time(),
    })
    return True, "activated — APT Pro unlocked on this machine"


def do_deactivate(deactivate_fn=None) -> tuple[bool, str]:
    deactivate_fn = deactivate_fn or ls.deactivate
    data = _load_raw()
    if not data or "key" not in data:
        return False, "no license is active on this machine"
    msg = "deactivated — seat freed"
    try:
        resp = deactivate_fn(data["key"], data.get("instance_id") or "")
        if not resp.get("deactivated"):
            msg = f"local license removed (LemonSqueezy said: {resp.get('error')})"
    except Exception as e:  # noqa: BLE001
        msg = f"local license removed (couldn't reach LemonSqueezy: {e} — the seat may still be held)"
    try:
        GRANT_PATH.unlink()
    except OSError:
        pass
    return True, msg


# --- CLI --------------------------------------------------------------------

def _cmd_activate(args) -> None:
    ok, msg = do_activate(args.key, socket.gethostname())
    print(f"  {msg}")
    if not ok:
        raise SystemExit(1)


def _cmd_deactivate(args) -> None:
    ok, msg = do_deactivate()
    print(f"  {msg}")
    if not ok:
        raise SystemExit(1)


def configure_activate(parser) -> None:
    parser.add_argument("key", help="License key from your purchase email")
    parser.set_defaults(func=_cmd_activate)


def configure_deactivate(parser) -> None:
    parser.set_defaults(func=_cmd_deactivate)
```
Register in `apt_pro/plugin.py` (after the tune registration):
```python
from apt_pro import activate as _activate

_SUBCOMMANDS.append(("activate", "Activate an APT Pro license key on this machine", _activate.configure_activate))
_SUBCOMMANDS.append(("deactivate", "Deactivate this machine's license seat", _activate.configure_deactivate))
```

- [ ] **Step 4: Run — verify pass, e2e spot-check, commit**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` — expect 41 passed.
Spot-check through core (from model-hub): `.venv\Scripts\python.exe cli.py pro activate FAKE-KEY` → prints a network/rejection error and exits 1 (no crash; no grant written).
```bash
git add -A && git commit -m "feat: apt pro activate/deactivate — LS seat management writing v2 grants"
```

### Task 4: `apt pro status` enrichment

**Files:**
- Modify: `apt_pro/plugin.py` (`_cmd_status`)
- Test: `tests/test_plugin.py` (extend)

**Interfaces:**
- Consumes: `license._load_raw()`, `license.check()`.
- Produces: status output gains — when a v2 grant exists — `plan`, `status`, `expires` (via `Grant.expires_human`), `last validated` (YYYY-MM-DD), `instance` id. Unlicensed and dev-mode outputs unchanged.

- [ ] **Step 1: Write failing test (append to tests/test_plugin.py)**

```python
def test_pro_status_shows_v2_grant_details(tmp_path, monkeypatch, capsys):
    import json
    import time
    import apt_pro.license as lic

    monkeypatch.delenv("APT_PRO_DEV", raising=False)
    monkeypatch.setattr(lic, "GRANT_PATH", tmp_path / "license.json")
    lic.GRANT_PATH.write_text(json.dumps({
        "key": "K-9", "instance_id": "inst-42", "product_id": 77,
        "plan": "pro", "status": "active", "expires_at": None,
        "last_validated_at": time.time(),
    }))

    parser, sub = _build_sub()
    PLUGIN.register_cli(sub)
    args = parser.parse_args(["pro", "status"])
    args.func(args)
    out = capsys.readouterr().out
    assert "pro" in out
    assert "while subscribed" in out
    assert "inst-42" in out
```

- [ ] **Step 2: Run — verify fail** (`inst-42` not in output)

- [ ] **Step 3: Implement — replace `_cmd_status` in apt_pro/plugin.py**

```python
def _cmd_status(args) -> None:
    from datetime import datetime

    from apt_pro.license import check, _load_raw
    grant = check()
    print("APT Pro — Tuning Cockpit")
    print(f"  version : {__version__}")
    if grant is None:
        print("  license : none — set APT_PRO_DEV=1 (dev) or run: apt pro activate <key>")
        return
    print(f"  license : {grant.plan} ({grant.key if grant.plan == 'dev' else 'active'})")
    print(f"  expires : {grant.expires_human}")
    raw = _load_raw() or {}
    if raw.get("last_validated_at"):
        seen = datetime.fromtimestamp(raw["last_validated_at"]).strftime("%Y-%m-%d")
        print(f"  checked : {seen} (revalidates every 3 days; 14-day offline grace)")
    if raw.get("instance_id"):
        print(f"  machine : {raw['instance_id']}")
```

- [ ] **Step 4: Run — verify pass (all plugin tests incl. original 3), commit**

```bash
git add -A && git commit -m "feat: pro status shows plan/expiry/last-validated/instance for v2 grants"
```

### Task 5: `apt pro insights` — calibration history + regression detection

**Files:**
- Create: `apt_pro/insights.py`
- Modify: `apt_pro/plugin.py` (register)
- Test: `tests/test_insights.py`

**Interfaces:**
- Consumes: core `backend.cookbook.benchmark.history()` (list of dicts with `model`, `tokens_per_second`, `timestamp`; injectable as `history_fn`); `license.require("insights")`.
- Produces:
  - `analyze(rows: list[dict], window: int = 5, threshold: float = 0.15) -> list[dict]` — per model (rows with a `model` and numeric `tokens_per_second`, sorted by timestamp): `baseline` = median of all-but-last-`window` runs, `recent` = median of last `window` (if fewer than `2*window` runs, split half/half; needs ≥4 runs total else skipped). Emits `{model, runs, baseline_tps, recent_tps, delta_pct, regression: bool}` where `regression = delta_pct <= -threshold*100`.
  - CLI `apt pro insights [--threshold PCT]` — gated by `require("insights")`; prints a table (model, runs, baseline, recent, Δ%, flag) and a summary line; "no benchmark history yet" when empty.
  - `configure_parser(parser)` registered as `("insights", "Calibration history + regression detection", ...)`.

- [ ] **Step 1: Write failing tests**

`tests/test_insights.py`:
```python
"""Insights: per-model baseline vs recent medians, regression flags."""
from apt_pro.insights import analyze


def _rows(model, values, t0=1000.0):
    return [{"model": model, "tokens_per_second": v, "timestamp": t0 + i}
            for i, v in enumerate(values)]


def test_regression_flagged():
    rows = _rows("m", [100, 102, 98, 101, 100, 80, 79, 81, 80, 78])
    out = analyze(rows, window=5, threshold=0.15)
    assert len(out) == 1
    r = out[0]
    assert r["model"] == "m"
    assert r["regression"] is True
    assert r["delta_pct"] < -15


def test_stable_not_flagged():
    rows = _rows("m", [100, 101, 99, 100, 100, 102, 99, 101, 100, 100])
    out = analyze(rows, window=5)
    assert out[0]["regression"] is False


def test_too_few_runs_skipped():
    assert analyze(_rows("m", [100, 99, 101])) == []


def test_models_separated_and_sorted_by_time():
    rows = _rows("a", [50, 50, 50, 50, 40, 40, 40, 40]) + _rows("b", [10, 10, 10, 10])
    out = analyze(rows, window=4)
    assert {r["model"] for r in out} == {"a", "b"}
    a = next(r for r in out if r["model"] == "a")
    assert a["regression"] is True  # 50 -> 40 = -20%


def test_rows_without_tps_ignored():
    rows = _rows("m", [100, 100, 100, 100]) + [{"model": "m", "timestamp": 2000}]
    out = analyze(rows, window=2)
    assert out[0]["runs"] == 4
```

- [ ] **Step 2: Run — verify fail** (module missing)

- [ ] **Step 3: Implement**

`apt_pro/insights.py`:
```python
"""`apt pro insights` — calibration history + tok/s regression detection.

Reads core's benchmark history (results.jsonl) READ-ONLY.
"""
from __future__ import annotations

import statistics

from apt_pro.license import require


def analyze(rows: list[dict], window: int = 5, threshold: float = 0.15) -> list[dict]:
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        model = r.get("model")
        tps = r.get("tokens_per_second")
        if not model or not isinstance(tps, (int, float)) or tps <= 0:
            continue
        by_model.setdefault(model, []).append(r)

    out: list[dict] = []
    for model, mrows in sorted(by_model.items()):
        mrows.sort(key=lambda r: r.get("timestamp", 0))
        vals = [float(r["tokens_per_second"]) for r in mrows]
        if len(vals) < 4:
            continue
        w = min(window, len(vals) // 2)
        baseline = statistics.median(vals[:-w])
        recent = statistics.median(vals[-w:])
        delta_pct = (recent - baseline) / baseline * 100 if baseline > 0 else 0.0
        out.append({
            "model": model,
            "runs": len(vals),
            "baseline_tps": round(baseline, 1),
            "recent_tps": round(recent, 1),
            "delta_pct": round(delta_pct, 1),
            "regression": delta_pct <= -threshold * 100,
        })
    return out


# --- CLI --------------------------------------------------------------------

def cmd_insights(args) -> None:
    require("insights")
    from backend.cookbook.benchmark import history  # core; loaded us at runtime
    rows = history()
    results = analyze(rows, threshold=args.threshold / 100.0)
    if not results:
        print("No benchmark history yet — run `apt benchmark <model>` a few times first.")
        return
    print(f"  {'model':<28} {'runs':>5} {'baseline':>9} {'recent':>8} {'Δ%':>7}")
    for r in results:
        flag = "  ◀ REGRESSION" if r["regression"] else ""
        print(f"  {r['model']:<28} {r['runs']:>5} {r['baseline_tps']:>9.1f} "
              f"{r['recent_tps']:>8.1f} {r['delta_pct']:>+7.1f}{flag}")
    regs = [r for r in results if r["regression"]]
    if regs:
        print(f"\n{len(regs)} model(s) slower than baseline — driver update, background load, "
              f"or a stack change (re-run `apt benchmark` to recalibrate).")
    else:
        print("\nAll models at or above baseline.")


def configure_parser(parser) -> None:
    parser.add_argument("--threshold", type=float, default=15.0,
                        help="Regression threshold in percent (default 15)")
    parser.set_defaults(func=cmd_insights)
```
Register in `apt_pro/plugin.py`:
```python
from apt_pro import insights as _insights

_SUBCOMMANDS.append(("insights", "Calibration history + regression detection", _insights.configure_parser))
```

- [ ] **Step 4: Run — verify pass; live spot-check; commit**

Run: `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe -m pytest -q` — expect 47 passed.
Live spot-check (from model-hub): `set APT_PRO_DEV=1` then `.venv\Scripts\python.exe cli.py pro insights` → renders the table off the real 9-row results.jsonl (few models will have ≥4 runs; "no history" fallback also acceptable output).
```bash
git add -A && git commit -m "feat: apt pro insights — per-model baseline/recent medians with regression flags"
```

### Task 6: Docs + ledger + Duan-gated live checklist

**Files:**
- Modify: `C:\Users\User\repos\apt-pro\README.md` (commands section)
- Modify: `C:\Users\User\repos\model-hub\HANDOFF.md` (Remaining section)
- Modify: `.superpowers/sdd/progress.md` (ledger)

**Interfaces:** none (docs).

- [ ] **Step 1: Update README commands**

Add to the Commands list in apt-pro README:
```markdown
- `apt pro activate <key>` / `apt pro deactivate` — LemonSqueezy seat management
  (revalidates every 3 days; 14-day offline grace; hard-locks on explicit invalid)
- `apt pro insights [--threshold PCT]` — calibration history + tok/s regression flags
```

- [ ] **Step 2: Update HANDOFF Remaining + write the Duan-gated checklist**

Replace the "Plan 2" line in HANDOFF's Remaining with:
```markdown
- **Plan 2 code DONE** (LS activation/grace/insights). Duan-gated to finish licensing:
  1. Create LemonSqueezy account + store + "APT Pro" product (subscription variant,
     license keys ENABLED, activation limit e.g. 3).
  2. Put the product id into `apt_pro/activate.py::APT_PRO_PRODUCT_ID`.
  3. Test mode: generate a test license key -> `apt pro activate <key>` ->
     `apt pro status` -> `apt pro tune` unlocked -> `apt pro deactivate`.
  4. Replace the placeholder upgrade URL in `apt_pro/license.py::_UPGRADE_MSG`
     when the landing page exists (Plan 3).
- **Plan 3** — release engineering (CI matrix, installers, rebrand, secrets sweep,
  landing page + waitlist).
```

- [ ] **Step 3: Commit both repos + ledger**

```bash
cd C:\Users\User\repos\apt-pro && git add README.md && git commit -m "docs: activate/deactivate + insights commands"
cd C:\Users\User\repos\model-hub && git add HANDOFF.md && git commit -m "docs: HANDOFF — Plan 2 done, Duan-gated LS store checklist"
```

---

## Final verification (whole plan)

- [ ] apt-pro suite green (≈47 tests), zero network calls in tests (grep tests/ for `api.lemonsqueezy.com` — only ls.py may contain it).
- [ ] Original 7 stub-era license tests pass UNCHANGED (backward compat proven).
- [ ] `apt pro activate FAKE-KEY` through core CLI: clean error + exit 1, no grant file.
- [ ] `APT_PRO_DEV=1 apt pro insights` renders against real results.jsonl.
- [ ] Core suite still green (no core files changed).
- [ ] `git -C C:\Users\User\repos\apt-pro remote -v` still empty.

## Deferred (explicit)

- Real LS store + test-mode E2E → Duan-gated checklist in HANDOFF (Task 6).
- Landing-page URL in `_UPGRADE_MSG` → Plan 3.
- HWID binding hardening (beyond LS instance ids) → post-launch, only if abuse shows up.
