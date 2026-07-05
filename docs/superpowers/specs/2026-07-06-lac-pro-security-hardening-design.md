# LAC Pro Security Hardening — Design

**Date:** 2026-07-06
**Repos:** `lac-pro` (primary) + `model-hub` (one dependency add, build)
**Status:** Approved (Duan, 2026-07-06) — ready for `writing-plans`

## The honest security boundary (governs everything below)

The Pro license check is **client-side code shipped in a Nuitka-compiled binary.** Everything here
**raises the bar against casual bypass and filesystem snooping — it is NOT DRM and NOT
"untamperable."** A determined attacker with the binary can still reverse the decrypt logic; the KEK
is derived from a locally-readable `machine_id`, so a determined *local* user can re-derive it. This
matches the boundary the delivery design already committed to in writing
(`specs/2026-07-05-lac-pro-delivery-and-hardening-design.md` §1): **casual-piracy hardening, honestly
bounded.** The only structurally-un-pirateable value is the deferred server-side moat (out of scope).

The achievable, honest goals:
1. Don't leave the user's license key in **plaintext** on disk.
2. Detect **casual tampering** of the grant (e.g. hand-editing `expires_at` to coast the offline grace).
3. Don't ship a **trivially-flippable prod backdoor** (`LAC_PRO_DEV=1` in a release build).

The delivery gate already neutralizes the "free user unlocks Pro" case by construction — a free user
never receives the plugin, so there is nothing for `LAC_PRO_DEV` to unlock. That is *not* what Phase 2
defends; Phase 2 defends the party who *already has* the plugin (paying customer or artifact-pirate)
flipping the override to skip the ongoing check.

## Scope

**In scope, in order:** Phase 1 (encryption at rest) → Phase 4 (repo_id validation) → Phase 2
(dev-override compile-out).

**Cut:** Phase 3 (client-side activation rate-limiting) — **dropped as a security control.** License
keys are 128-bit UUIDs (not brute-forceable) and a real attacker calls Polar's API directly, so a
client-side limiter adds zero friction to the actual threat while only slowing the official client.
Polar's server-side rate limiting is the real defense. Not built, not as a "UX nicety" either.

**Deferred:** Phase 5 (local audit logging) — **deferred entirely from this effort.** Lowest value
for a single-user local app, and it adds surface to the publicly-claimed "no telemetry" promise. When
resurrected later it is to be built as the **full** version (all events, key hashed/truncated never
logged raw, size-based rotation, optional failed-validation heuristic), and it **must remain
local-only — it must never phone home.** That no-telemetry guarantee is a standing constraint on any
future audit-log work.

## Phase 1 — License encryption at rest

### Current state
`~/.model-hub/license.json` stores the grant as **plaintext JSON**:
`{key, activation_id, organization_id, plan, status, expires_at, last_validated_at}` (v2 Polar grant),
with legacy v1 grants `{key, plan, expires_at}` still supported (expiry-only). Written by
`lac_pro/activate.py::do_activate` and the grant-writer in `lac_pro/license.py`; read by `check()`.
`check()` is fuzz-proven to **never raise**; `require()` exits 3; `LAC_PRO_DEV=1` overrides.

### Cryptography
- **Cipher:** AES-256-GCM via the `cryptography` package (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`).
  GCM is AEAD — its authentication tag *is* the tamper-detector. **No separate HMAC** (redundant with a
  correctly-used GCM tag; a redundant primitive is only a place to get it wrong).
- **Key derivation:** **HKDF-SHA256**, not PBKDF2.
  - Rationale: PBKDF2 is a *password* stretcher whose iteration count buys resistance to brute-forcing a
    low-entropy secret. Here the input key material (`machine_id`) is **not a secret password** — it is
    locally readable — so iteration count would add only latency, never security. HKDF is the correct
    primitive for deriving a key from existing (non-password) key material.
  - `KEK = HKDF(algorithm=SHA256, length=32, salt=<per-file-random-16-bytes>, info=b"lac-pro-license-v2")`
    over `machine_id` bytes.
- **Machine binding:** because the KEK depends on `machine_id`, an encrypted `license.json` copied to a
  **different machine will not decrypt** (different `machine_id` → different KEK). This is the intended
  copy-resistance and is a required verification check.

### machine_id (stability is the real scale risk)
A flaky or run-to-run-unstable `machine_id` would silently invalidate every grant → mass re-activations.
So `machine_id` must be **stable and deterministic**:
- Windows: registry `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`.
- macOS: `IOPlatformUUID` (via `ioreg`).
- Linux: `/etc/machine-id` (fallback `/var/lib/dbus/machine-id`).
- Deterministic last-resort fallback if none readable (e.g. a stable hash of hostname+username), so the
  code always yields *some* stable value rather than raising.
- Cached per process. **Stability across repeated calls is a first-class test.**

### On-disk envelope
JSON (matches the repo's file convention, debuggable):
```json
{ "v": 2, "salt": "<b64>", "nonce": "<b64 12 bytes>", "ct": "<b64 ciphertext||gcm-tag>" }
```
File written chmod 0600 (preserve the existing attempt).

### Read path (backward compatible — no mass logout)
1. Read file bytes; if absent → no grant (unchanged).
2. If it parses as the **encrypted envelope** (`v==2` with `salt`/`nonce`/`ct`): derive KEK, decrypt,
   verify GCM tag → plaintext grant dict.
3. Else if it parses as a **legacy plaintext grant** (existing v2-plaintext or v1 shape): accept as
   today, and mark for transparent **re-encryption on the next write**.
4. Any failure — malformed envelope, GCM auth-tag mismatch, `machine_id` unreadable, `cryptography`
   import failure, decrypt exception → **treated as no grant. Never raises.** (Preserves `check()`'s
   contract; a tampered ciphertext therefore fails safe to "unlicensed", not to a crash or a bypass.)

### Write path
- All grant writes go through a single `_write_grant(dict)` in `license.py` that **always encrypts**
  (v2 envelope). `activate.py::do_activate` and any other writer call it — consolidate scattered writes
  into this one path (legit "improve the code you're working in").
- A read of a legacy plaintext grant followed by any normal write silently upgrades the file to encrypted.

### Performance
- Decrypted grant **cached in-memory per process**, keyed on the grant file's mtime; re-decrypt only when
  mtime changes. Warm `check()` calls do no crypto (well under the sub-ms target). Cold decrypt (HKDF +
  AES-GCM) is a one-time sub-millisecond-to-low-ms cost per process.

### Dependency consequence (explicit)
- `cryptography` is added to **model-hub's `requirements.txt`**. The Pro artifact is a Nuitka `.pyd` +
  `dist-info` zip that pip-installs nothing, so any dependency the compiled `lac_pro` imports at runtime
  must already live in the **core app's PyInstaller bundle**. Net: the open-core free app bundles
  `cryptography` for the proprietary plugin's benefit. Accepted trade-off.
- Dev + tests pass once `cryptography` is in model-hub's venv. **The shipped-exe actually bundling
  `cryptography` (PyInstaller hidden-import/hook survival) is a Duan-gated build + smoke step**, same
  class as every prior build gate — verified, not assumed, but not a blocker for the code tasks.

## Phase 4 — repo_id validation

### Decision: model HF's real grammar, not a convenience regex
At scale a **two-segment-only** rule would false-reject legitimate legacy ids (`gpt2`, redirected by HF)
and generate support tickets. Enterprise-robust = maximally compatible with real HF inputs while still
rejecting every injection/traversal payload. Security comes from the **per-segment character grammar**,
not the slash count.

### Rules (validated at the entrypoint)
- Split on `/`: accept **exactly 1 or 2 segments** (bare legacy id *or* `org/model`). More than one
  slash → reject.
- Each segment matches `^[A-Za-z0-9]+([._-][A-Za-z0-9]+)*$` — alphanumeric runs joined by single
  `.`/`_`/`-`, no leading/trailing/consecutive separators (HF's actual component rule).
- Length: ≤96 chars per segment, ≤200 total.
- On mismatch: honest error, `error_type="invalid_repo_id"`, message naming the expected form
  (e.g. `Qwen/Qwen2.5-0.5B-Instruct`).

### Placement
- Validate at the top of `import_custom_model` (`lac_pro/hf_import.py`) — the single choke point both the
  CLI (`import_cli.py`) and the API route (`plugin.py::register_api` import-model) flow through — and
  return the honest `{state:"failed", error_type:"invalid_repo_id", message:...}` dict, consistent with
  the existing never-raise / four-plus-honest-states pattern. Both surfaces render it through their
  existing uniform message path (no special-casing).
- This is defense-in-depth on top of the existing sibling path-traversal guard in `download_model_files`;
  it rejects garbage **at the door** before any network/filesystem work.

### Tests
- **Valid pass:** `Qwen/Qwen2.5-0.5B-Instruct`, `meta-llama/Llama-2-7b-hf`, `TheBloke/Llama-2-7B-GGUF`,
  single-segment `gpt2`.
- **Payloads rejected:** `../../../etc/passwd`, `$(whoami)`, `; rm -rf /`, `x@evil.com/y`,
  `https://evil.com/x`, embedded newline/control chars, `a/b/c` (>2 segments), empty, leading/trailing/
  consecutive separators, over-length.

## Phase 2 — Dev-override compile-out

### Mechanism
- Commit `lac_pro/_build.py` with `IS_RELEASE = False` (source default).
- `build/build_artifact.py` regenerates `_build.py` with `IS_RELEASE = True` immediately before the
  Nuitka `--module` compile, so the shipped `.pyd` bakes `IS_RELEASE = True`.
- `license.py::check()` honors `LAC_PRO_DEV=1` **only when `not IS_RELEASE`**. A release build ignores
  the variable entirely; Duan's source venv is unchanged.
- **Backstop:** additionally treat the plugin as release when it is running compiled — detect via
  `__loader__` (the spike's proven compiled-vs-source signal; Nuitka fakes `__file__`/`__spec__.origin`
  but not the loader). So a forgotten bake still fails safe to "override ignored".
- `plugin.py::_cmd_status` stops advertising the `LAC_PRO_DEV` hint when running in a release build.
- **Skip** the `--confirm-dev` per-session flow — pure developer friction, ~no security gain once release
  builds ignore the var.

### Tests
- Source build (`IS_RELEASE=False`, not compiled): `LAC_PRO_DEV=1` still grants (unchanged dev path).
- Simulated release (`IS_RELEASE=True`): `LAC_PRO_DEV=1` is ignored → unlicensed.
- `_cmd_status` copy: hint present in source build, absent in release build.

## Cross-cutting

### Never-raise contract
Every new failure mode in Phases 1 and 4 resolves to a safe, honest outcome (no grant / honest failed
state), never an exception out of `check()` or `import_custom_model`. Existing fuzz/never-raise tests
must still pass; new negative-path tests assert the fail-safe explicitly.

### Secrets
Never log a full (or any) license key. No new logging of key material is introduced by this effort
(audit logging is deferred). The encrypted envelope contains no plaintext key.

### Process (non-negotiable)
- **Open every reply with "Duan"** (standing rule + context-health canary).
- **Subagent-driven** (`superpowers:subagent-driven-development`): fresh implementer + fresh reviewer per
  task; targeted fix subagent for Critical/Important findings, then re-review. **Every subagent dispatch
  MUST say "work in the foreground, do NOT spawn agents"** (prior delegation-loop bug).
- **TDD per task:** failing test first (confirm it fails for the stated reason), implement, confirm pass.
- Append each task's outcome to `<repo>/.superpowers/sdd/progress.md` in **both** repos.
- Commits land on `master` in both repos, per task. **NEVER push to origin without Duan's separate
  explicit go-ahead each time. lac-pro never gets a remote.**

### Verification before completion
- Full suites green: model-hub (`-m "not live"`, 285 baseline) + lac-pro (`-m "not live"`, 118 baseline),
  plus the new tests.
- `check()` warm-call latency measured (`timeit`) — sub-ms warm, one-time modest cold decrypt.
- Manual security checks: (a) copy an encrypted `license.json` to another machine/HOME → won't decrypt →
  unlicensed; (b) hand-edit ciphertext → GCM auth fails → unlicensed; (c) forge `LAC_PRO_DEV=1` under a
  simulated release build → ignored; (d) legacy plaintext grant still loads then upgrades to encrypted on
  next write.
- Shipped-exe `cryptography`-bundling smoke: **Duan-gated build step**, flagged not blocking.

## Anchors (accurate as of 2026-07-06)
- `lac-pro/lac_pro/license.py` — `GRANT_PATH`, `check()` (3-day revalidate / 14-day grace, hard-locks on
  explicit non-granted, honors `LAC_PRO_DEV`, chmod 0600, never-raises), grant read/write.
- `lac-pro/lac_pro/activate.py` — `do_activate(key, label, activate_fn=)` / deactivate; writes the grant.
- `lac-pro/lac_pro/hf_import.py` — `import_custom_model` (the choke point), `download_model_files`
  (existing sibling path-traversal guard). `repo_id` flows into HF URLs + filesystem paths; no subprocess.
- `lac-pro/lac_pro/plugin.py` — CLI subcommands + `register_api` (`/api/pro/*`); `_cmd_status` dev messaging.
- `lac-pro/build/build_artifact.py` — Nuitka `--module` build (ABI-locked cp311-win-amd64); where
  `IS_RELEASE=True` gets baked.
- `model-hub/requirements.txt` — where `cryptography` is added.

## Out of scope
- Phase 3 (dropped), Phase 5 (deferred — full version later, must stay no-telemetry).
- Server-side moat / any change to the Cloudflare Worker + Polar delivery gate.
- Any push to origin; any repo remote for lac-pro.
