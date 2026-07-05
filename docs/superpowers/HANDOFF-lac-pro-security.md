# Session Handoff — LAC Pro Security Hardening

Paste-prompt at the bottom. This doc is the full context. **Read it, then start with
`superpowers:brainstorming` to right-size the plan against the threat model BEFORE writing any
code** — a couple of the phases below defend non-threats and should be cut or demoted, and one
goal is overstated. Brainstorming settles that; don't just execute the phase list verbatim.

## The task

Harden the LAC Pro **local** security layer (license storage, dev-override, input validation,
audit logging). Source plan drafted by Duan after a security scan — reproduced in full under
"Proposed phases" below. Your job: brainstorm/right-size it → spec → plan → build subagent-driven
with TDD. This is primarily a **`lac-pro`** effort (the license/activation/import code lives there),
with a small touch in core (`model-hub`) only if repo_id validation moves to the API entrypoint.

## The honest security boundary (read first — it governs the whole effort)

The Pro license check is **client-side code shipped in a (Nuitka-compiled) binary.** Everything
below **raises the bar against casual bypass and filesystem snooping — it is NOT DRM and NOT
"untamperable."** A determined attacker with the binary can still reverse the decrypt logic. This
is the exact boundary the delivery design already committed to in writing
(`specs/2026-07-05-lac-pro-delivery-and-hardening-design.md` §1): casual-piracy hardening, honestly
bounded. **The only structurally-un-pirateable value is the deferred server-side moat, out of scope
here.** Do not let the plan's "unreadable and untamperable even with filesystem access" phrasing set
an impossible goal — the achievable goal is: *don't leave the user's key in plaintext, detect casual
tampering, and don't ship a trivially-flippable prod backdoor.*

Also relevant: the **delivery gate already neutralizes the "free user unlocks Pro" case by
construction** — a free user never receives the plugin, so there's nothing for `LAC_PRO_DEV=1` to
unlock. That shrinks Phase 2's real scope (see notes).

## Two repos

- **`C:\Users\User\repos\model-hub`** — open-source core (GitHub `Dkrynen/lac`). Venv:
  `C:\Users\User\repos\model-hub\.venv\Scripts\python.exe`. Full suite:
  `.venv\Scripts\python.exe -m pytest -q -m "not live"` (285 passing).
- **`C:\Users\User\repos\lac-pro`** — private Pro plugin. **No git remote, and must never get one.**
  Editable-installed into model-hub's venv; run its commands with model-hub's venv from the lac-pro
  dir. Suite: `... -m pytest -q -m "not live"` from `C:\Users\User\repos\lac-pro` (118 passing + slow/live markers).

Ledgers (append per task; your recovery map after compaction): `<repo>/.superpowers/sdd/progress.md`.

## Conventions (non-negotiable)

- **Open every reply with "Duan"** (standing rule + context-health canary).
- **Subagent-driven** (`superpowers:subagent-driven-development`): fresh implementer + fresh reviewer
  per task; targeted fix subagent for Critical/Important findings, then re-review; append each task's
  outcome to the ledger. **Every subagent dispatch MUST say "work in the foreground, do NOT spawn
  agents"** (a delegation-loop bug bit this project earlier).
- **TDD per task** — failing test first (confirm it fails for the stated reason), implement, confirm
  pass. Hand each subagent its task brief via `scripts/task-brief`, each reviewer a
  `scripts/review-package BASE HEAD` diff.
- **Commits land on `master` in both repos, per task. NEVER push to origin without Duan's separate
  explicit go-ahead each time.** lac-pro never gets a remote.
- Latency: `check()` runs on Pro-command use, not a hot loop — AES-GCM decrypt cost is negligible, but
  cache the decrypted grant in-memory per process so repeated checks in one run don't re-decrypt.

## Read first (accurate anchors)

- `lac-pro/lac_pro/license.py` — `GRANT_PATH = ~/.model-hub/license.json`, `check()` (3-day
  revalidate / 14-day offline grace, hard-locks on explicit `valid:false`, honors `LAC_PRO_DEV`,
  chmod 0600 attempted, fuzz-proven never-raises), the grant read/write.
- `lac-pro/lac_pro/activate.py` — `do_activate(key, label, activate_fn=)` / deactivate; writes the grant.
- `lac-pro/lac_pro/ls.py` — Polar customer-portal client (`activate`/`validate`/`deactivate`), real
  `User-Agent` required (WAF), returns parsed JSON verbatim.
- `lac-pro/lac_pro/hf_import.py` — `repo_id` flows into HF **URLs** (`fetch_hf_model_info`,
  `fetch_hf_config`, `download_model_files`) and **filesystem paths** (scratch dir, sibling
  filenames). **No subprocess / no shell-out** (so no shell-injection surface). A sibling
  path-traversal guard already refuses `../escaped.bin`. `repo_id` itself is NOT format-validated at
  the entrypoint yet.
- `lac-pro/lac_pro/plugin.py` — CLI subcommands (`status`/`activate`/`deactivate`/`tune`/`benchmark`/
  `import`) + `register_api` (`/api/pro/*` routes incl. import-model). `LAC_PRO_DEV` messaging in
  `_cmd_status`.
- `model-hub/backend/pro_install.py` — delivery bootstrap (core, Pro-logic-unaware). Not a license
  surface, but context for how the plugin arrives.

## Proposed phases (Duan's plan) + scoping notes (settle these in brainstorming)

**Recommended priority after right-sizing:** Phase 1 → Phase 4 → Phase 2 → Phase 5 → (Phase 3: drop
or demote). Rationale in the notes.

### Phase 1 — License file encryption at rest (REAL, highest value — keep)
Today `~/.model-hub/license.json` stores the key + grant in **plaintext**. That's a genuine hygiene
problem (a user's live license key sitting readable on disk). Encrypt at rest + detect tampering:
- Envelope encryption, AES-256-GCM. Key from the **OS keychain** (`keyring`, cross-platform) —
  preferred — with a `PBKDF2(machine_id, salt)` fallback where no keychain exists. GCM's auth tag
  already gives tamper-detection; a separate HMAC is redundant if GCM is used correctly (decide in
  brainstorming — don't bolt on both without reason).
- Read: decrypt + verify auth tag → plaintext dict; tamper/keychain-miss → treat as no grant (fail
  safe, matches `check()`'s never-raise contract).
- Tests: round-trip; hand-edited ciphertext → auth fails → no grant; Windows + POSIX.
- **Honest framing:** this stops casual snooping/hand-editing, not a determined reverser.

### Phase 2 — Dev-override hardening (right-size — smaller than it looks)
Delivery already means a free user has no plugin for `LAC_PRO_DEV=1` to unlock. Residual real risk:
someone who *already has* the plugin (a paying customer, or an artifact-pirate) flipping `DEV=1` to
skip the ongoing check. The concrete, worthwhile hardening:
- **Strip/ignore the dev override in the release (compiled) build** — the delivery spec already
  flagged this as the belt-and-suspenders step; actually implement it (a build-time flag that
  compiles `LAC_PRO_DEV` handling out, or a released-build guard). Dev path stays for Duan's source venv.
- Audit-log `DEV_OVERRIDE_ACTIVE` when it fires (feeds Phase 5).
- **Skip** the per-session `--confirm-dev` confirmation flow — friction for the developer, ~no
  security gain once the release build ignores the var. (Confirm in brainstorming.)

### Phase 3 — Client-side activation rate limiting (LOW value — recommend DROP/demote)
This defends a **non-threat**: (1) license keys are high-entropy UUIDs (~128-bit) — not
brute-forceable at all; (2) a real attacker bypasses the client and calls Polar's API directly, so a
client-side limiter adds **zero** friction to the actual threat — it only slows someone using the
official client. Polar's server-side rate limiting is the real defense (Duan's own Notes concede
this). Recommend: **drop it as a security control.** If kept at all, keep it as a tiny UX nicety
("slow down" message), explicitly **not** claimed as security, and don't spend a TDD phase on it.

### Phase 4 — `repo_id` input validation (REAL-but-modest — keep, it's cheap)
No shell-injection risk (HTTP-only, confirmed), but `repo_id` is interpolated into **HF URLs** and
**filesystem paths**, so a crafted value (`../`, `@evil.com`, a full URL, control chars) could bend
the fetch target or paths. On top of the existing sibling guard, validate `repo_id` **at the
entrypoint** (`import_custom_model` / the API route in `plugin.py`):
- Whitelist `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$` (HF `org/model`), length ≤256, reject on mismatch
  with an honest error.
- Tests: valid (`meta-llama/Llama-2-7b-hf`, and decide `gpt2`-style single-segment) pass; payloads
  (`../../../etc/passwd`, `$(whoami)`, `; rm -rf /`, `x@evil.com/y`, newlines) rejected.
- Add the "repo_id never reaches a subprocess; if that changes, validate strictly" comment.

### Phase 5 — Local audit logging (LOW-MEDIUM — keep, low priority)
Append-only `~/.model-hub/audit.jsonl` (mode 0600), events: activation success/failure, validation
success/failure, dev-override fired, import started/failed. **Truncate/hash the key** (first 8 chars
or a hash) — never log the full key. Size-based rotation (>10MB → dated file).
- **Guard the no-telemetry promise:** this is a **local** log only. It must never phone home — the
  landing page publicly claims "no telemetry," and that's verified true today. Keep it that way.
- Optional heuristic: >5 failed validations/hour → local warning line.

## Process

1. `superpowers:brainstorming` — right-size the phases against the honest boundary + threat model
   (resolve: HMAC-vs-GCM-tag, keychain-vs-PBKDF2 default, Phase 3 keep/drop, Phase 2 scope, single-
   vs-two-segment repo_id). Present options to Duan; he decides.
2. `superpowers:writing-plans` — the settled phases as a TDD, subagent-driven plan (both repos'
   ledgers).
3. `superpowers:subagent-driven-development` — build it, per-task review, final whole-branch review.
4. `superpowers:verification-before-completion` + measure `check()` latency (`timeit`, target sub-ms)
   + the manual security checks (copy encrypted license to another machine → won't decrypt; edit
   ciphertext → auth fails; forge dev override in a release build → ignored + logged).

## Notes
- Secrets: never log a full license key; hash/truncate for the audit trail (AIOS doctrine).
- No PII stored locally; audit log is local-only and immutable-ish (append + rotate).
- `keyring` adds a dependency and behaves differently per-OS (Windows Credential Locker, macOS
  Keychain, libsecret on Linux) — test on Windows + POSIX; have the PBKDF2(machine_id) fallback for
  headless/no-keychain environments. Note it must survive the Nuitka-compiled build.

---

## Paste this into the new session

```
Duan here. Read docs/superpowers/HANDOFF-lac-pro-security.md in C:\Users\User\repos\model-hub — it's
the full handoff for hardening the LAC Pro local security layer (license-file encryption, dev-override,
repo_id validation, audit logging), primarily in the private lac-pro repo. Start with
superpowers:brainstorming to right-size the phases against the honest client-side boundary (a couple
defend non-threats — the handoff flags which), then spec → writing-plans → subagent-driven TDD build.
Both repos' ledgers are at .superpowers/sdd/progress.md. Don't push anything to origin, and lac-pro
never gets a remote.
```
