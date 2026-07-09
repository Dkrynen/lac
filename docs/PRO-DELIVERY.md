# LAC Pro delivery runbook (operator)

Public-safe summary of how the compiled **LAC Pro** plugin gets from source to a
paying customer's machine. The account-backed upload/deploy commands live in
private operator notes, not in this public repo. The intended public-launch "how
do I turn Pro on" flow is summarized in [What the customer does](#what-the-customer-does).

## Security boundary — read this first

This is **casual-piracy hardening, not DRM.** Nothing here is uncrackable, and
that's a deliberate, documented trade-off:

- **The artifact is private.** The compiled plugin lives in a **private R2
  bucket** — never in a git remote, never on the landing page. The only way to
  get the bytes is a valid license key.
- **The gate is stateless.** The Cloudflare Worker (`worker/`) validates the key
  against Polar and streams the artifact. It stores no keys, no PII, no state.
- **Licensing is enforced server-side.** The `.pyd` protects code *structure*,
  not string constants (a determined attacker can still read embedded strings) —
  so entitlement is decided by Polar at the gate, not by anything shipped in the
  client.
- **Free users get no Pro code at all.** Delivery *is* the gate: without a valid
  key the artifact never reaches disk, so there is nothing on a free machine to
  crack in the first place.

If any of those four stops being true, stop and fix it before shipping.

## The chain at a glance

| Step | What happens | Where it runs | Duan-gated? |
|---|---|---|---|
| 1 | Build the compiled artifact | `lac-pro` repo, CPython 3.11 | no |
| 2 | Upload the artifact to a private R2 bucket | Cloudflare (wrangler) | **yes** (needs the account) |
| 3 | Deploy the delivery Worker | Cloudflare (wrangler) | **yes** |
| 4 | Point the client at the deployed gate + rebuild | `model-hub` | yes (needs the live URL) |
| 5 | Real end-to-end smoke test | a clean machine + a Polar key | **yes** |

Steps 2–5 need Duan's Cloudflare + Polar accounts. This public document records
the boundary and approval checklist only; it does not publish the infrastructure
recipe or perform any account-backed action.

## Prerequisites

- The **`lac-pro`** repo checked out beside `model-hub`.
- A **CPython 3.11** build environment with **Nuitka** installed
  (`pip install nuitka` — additive). `model-hub`'s `.venv` is 3.11 and works
  once Nuitka is added. MSVC Build Tools must be present; Nuitka finds them via
  `vswhere`, so you do **not** need to run `vcvarsall` or put `cl.exe` on PATH.
- Cloudflare deploy access, used only from Duan-approved operator context.
- A **Polar** organisation and, for the smoke test, a **test-mode license key**.

---

## Step 1 — Build the artifact

From the private `lac-pro` repo, the operator builds with the ABI-correct
interpreter and records the artifact path, byte size, and SHA256 in private
release notes.

- **ABI lock.** The script **refuses to run** on any Python other than
  CPython 3.11 — the compiled `.pyd` is `cp311-win_amd64` and must match the
  minor version the shipped `lac.exe` freezes. Build-Python == ship-Python, or
  the plugin won't import on the customer's machine.
- **What ships.** A zip whose root holds the native plugin plus its
  entry-point metadata. The build asserts no readable `.py`/`.pyc` and no source
  comments leak into the bytes.
- **Determinism.** Same source → identical *layout* and *filename*, but **not**
  byte-identical (Nuitka embeds a build timestamp). The printed **sha256**
  identifies that one build's exact bytes — record it, you'll use it to confirm
  the upload landed intact.

## Step 2 — Upload the artifact to private storage

Duan-gated. The operator uploads the built zip to private Cloudflare storage
using the private deployment checklist. The object must stay private, the stored
bytes must match the recorded SHA256, and the open-source release must never
include the Pro artifact.

## Step 3 — Deploy the Worker

Duan-gated. The delivery Worker validates a submitted license key, streams the
private artifact only for an accepted key, and stores no key material or customer
state. Account-specific bindings, object names, and deploy commands stay in the
private operator checklist.

## Step 4 — Wire the client to the approved gate

The client has **exactly one** gate-URL source:
`PRO_GATE_URL` in [`../backend/pro_install.py`](../backend/pro_install.py),
which is a non-deployable placeholder in public source. After Duan approves
production Pro delivery, replace it from the private operator checklist before
building a Pro-enabled release. Until that approval and account-backed smoke test
are complete, treat Pro delivery as pending, not public-live. It is overridable
at runtime by the `LAC_PRO_GATE_URL` env var (resolution order: explicit arg →
env → constant).

Two ways to point the client at the approved gate:

- **Bake it in (for the shipped installer):** after Duan approves launch, confirm
  `PRO_GATE_URL` has been replaced with the approved production Worker URL from
  the private operator checklist, then **rebuild the app** so the frozen
  `lac.exe` embeds that gate. This is the correct path for a public Pro-enabled
  release.
- **Override at runtime (for testing / staging):** use `LAC_PRO_GATE_URL` from a
  private operator shell before running `lac unlock`. Handy for pointing a dev
  build at a test gate without a rebuild.

Keep it a single source: don't add the URL anywhere else — no second constant,
no hardcode in the CLI or web layer.

## Step 5 — Real end-to-end smoke ("run it against reality once")

**Duan-gated.** These steps need the live Worker + a real Polar key, so they are
described here, not performed. Run them once on a **clean** machine/env (ideally
the packaged `lac.exe`, so you exercise the frozen client, not the source tree):

1. **Happy path.** With a valid **test-mode** Polar license key:
   ```bash
   lac unlock <key>
   ```
   Expect: the plugin downloads, installs into `~/.model-hub/plugins/`, and the
   Pro commands mount on the next start — e.g. `lac pro status` reports active and
   `lac pro tune` / `lac pro benchmark` appear in `lac --help`. Restarting LAC is
   what mounts the freshly installed plugin.
2. **Sad path.** With an **invalid / expired** key: `lac unlock <bad-key>` must
   fail **cleanly** — the gate returns `403`, the CLI prints an honest
   "not accepted (invalid or expired)" message and a non-zero exit code, and
   **no Pro code is written to disk.**
3. **Free user.** With no key at all, confirm there is no `lac_pro` artifact in
   `~/.model-hub/plugins/` — a free install ships zero Pro code.

If all three hold, the delivery chain is live and honest.

## Rolling a new artifact

Rebuild from the private `lac-pro` repo, upload through the private deployment
checklist, record the new SHA256/byte size in private release notes, and rerun
the valid-key plus invalid-key smoke tests before public checkout is enabled.

## What the customer does

For reference — the intended buyer flow after Duan-gated public launch:

1. Buy Pro via the Polar checkout (no account needed).
2. Polar emails a **license key**.
3. Activate it: `lac unlock <key>` (CLI) **or** **Settings → Activate Pro** in
   the web UI.
4. Restart LAC — the Pro autopilot mounts on the next start.

## Push

Both repos push to their origins **only** on Duan's explicit go: `model-hub`
→ GitHub; `lac-pro` stays remote-less (its artifact lives in R2, not a git
remote).
