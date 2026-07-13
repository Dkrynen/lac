# LAC Pro entitlement and artifact delivery runbook (operator)

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
- **Entitlements are explicit.** The only allowed Polar benefit identifiers are
  supplied privately as `LOCAL_PRO_BENEFIT_ID` and `PRO_CLOUD_BENEFIT_ID`.
  Their values never belong in this public runbook. A granted key for an
  unrelated product must still be rejected.
- **Artifact bytes are pinned.** The operator records the exact build digest as
  `ARTIFACT_SHA256`; the Worker returns it as `X-LAC-Artifact-SHA256`, and the
  client verifies it before any plugin file is written.
- **Licensing authority is server-signed.** The `.pyd` protects code
  *structure*, not string constants. Polar decides entitlement at the gate;
  the gate returns a 14-day Ed25519 receipt bound to the LAC product, approved
   benefit/plan, activation, license fingerprint, anonymous installation
   binding, and expiry. The client trusts
  only a baked rotating public-key ring, never locally chosen cache claims.
- **Free users get no Pro code at all.** Delivery *is* the gate: without a valid
  key the artifact never reaches disk, so there is nothing on a free machine to
  crack in the first place.

If any of those six stops being true, stop and fix it before shipping.

## The chain at a glance

| Step | What happens | Where it runs | Owner approval needed? |
|---|---|---|---|
| 1 | Build the compiled artifact | `lac-pro` repo, CPython 3.11 | no |
| 2 | Upload the artifact to a private R2 bucket | Cloudflare (wrangler) | **yes** (needs the account) |
| 3 | Deploy the delivery Worker | Cloudflare (wrangler) | **yes** |
| 4 | Point the client at the deployed gate + rebuild | `model-hub` | yes (needs the live URL) |
| 5 | Real end-to-end smoke test | a clean machine + a Polar key | **yes** |

Steps 2-5 need the owner's Cloudflare + Polar accounts. This public document records
the boundary and approval checklist only; it does not publish the infrastructure
recipe or perform any account-backed action. **Current boundary: this update does
not deploy the Worker, upload an artifact, change Polar products, or open either
checkout.** Every account-backed action still needs Duan's explicit approval.

## Prerequisites

- The **`lac-pro`** repo checked out beside `model-hub`.
- A **CPython 3.11** build environment with **Nuitka** installed
  (`pip install nuitka` — additive). `model-hub`'s `.venv` is 3.11 and works
  once Nuitka is added. MSVC Build Tools must be present; Nuitka finds them via
  `vswhere`, so you do **not** need to run `vcvarsall` or put `cl.exe` on PATH.
- Cloudflare deploy access, used only from approved operator context.
- A **Polar** organisation, distinct Local Pro and Pro Cloud benefit IDs, and,
  for the smoke test, test-mode license keys for both tiers.

---

## Step 1 — Build the artifact

From the private `lac-pro` repo, the operator builds with the ABI-correct
interpreter and records the artifact path, byte size, and SHA256 in private
release notes.

For a two-tier release, load `LAC_PRO_CLOUD_BENEFIT_ID`,
`LAC_PRO_RECEIPT_PUBLIC_KEYS_JSON`, `LAC_PRO_ENTITLEMENT_SIGNING_KID`, and
`LAC_PRO_ENTITLEMENT_GATE_URL` into the
current protected operator environment. The public-key JSON must contain at
least the current active and an overlapping successor Ed25519 public key with
issue/expiry windows; it contains no private key. The builder requires all four
values by default:

```powershell
C:\Users\User\repos\model-hub\.venv\Scripts\python.exe build\build_artifact.py
```

Do not put private deployment values in this repo or command arguments. The
command must print `cloud   : baked`; it stops before compilation if the
operator environment is incomplete, the Cloud and Local benefit IDs collide,
the receipt ring lacks two valid rotation keys, or the issuer is not an HTTPS
origin. The Ed25519 signing key is never a build input. The source tree
must be clean and committed; only Git-tracked package inputs enter the hermetic
copy. The resulting compiled artifact then
recognizes a Pro Cloud key on a clean customer machine without requiring that
machine to set an environment variable.

- **ABI lock.** The script **refuses to run** on any Python other than
  CPython 3.11 — the compiled `.pyd` is `cp311-win_amd64` and must match the
  minor version the shipped `lac.exe` freezes. Build-Python == ship-Python, or
  the plugin won't import on the customer's machine.
- **What ships.** A zip whose root holds the native plugin plus its
  entry-point metadata. The build asserts no readable `.py`/`.pyc` and no source
  comments leak into the bytes.
- **Determinism.** Same source → identical *layout* and *filename*, but **not**
  byte-identical (Nuitka embeds a build timestamp). The printed **sha256**
  identifies that one build's exact bytes. Record it privately as the
  `ARTIFACT_SHA256` Worker configuration value, then use it to confirm the upload
  landed intact.
- **Provenance.** The builder writes a non-secret `.provenance.json` sidecar
  containing the exact source commit, UTC build time, ABI, filename, byte size,
  SHA-256, benefit IDs, gate origin, canonical public-ring digest, key IDs and
  windows, and active signing key ID. Keep it with private release evidence; do not upload it as the
  customer artifact.

## Step 2 — Upload the artifact to private storage

Owner-approved. The operator uploads the built zip to private Cloudflare storage
using the private deployment checklist. The object key must be immutable and
hash-bearing (`product/version/sha256/filename`), the object must stay private,
the downloaded bytes must match the recorded SHA256, and the open-source release
must never include the Pro artifact.

## Step 3 — Deploy the Worker

Owner-approved. The delivery Worker validates a submitted license key, streams the
private artifact only for an accepted key, and stores no key material or customer
state. Account-specific bindings, object names, and deploy commands stay in the
private operator checklist.

Before an approved deployment, the Worker configuration must contain concrete,
private values for `POLAR_ORG_ID`, `LOCAL_PRO_BENEFIT_ID`,
`PRO_CLOUD_BENEFIT_ID`, `ENTITLEMENT_SIGNING_KID`,
`ENTITLEMENT_SIGNING_PUBLIC_KEY`, `ARTIFACT_KEY`, and the raw 64-hex
`ARTIFACT_SHA256`, plus the private `R2_BUCKET` binding. The two benefit IDs are
an allowlist, not descriptive labels: a key is accepted only when Polar reports
`granted` and its benefit matches one of those exact configured IDs. The artifact
digest must describe the exact R2 object bytes served by that deployment.
`ARTIFACT_FILENAME` must be the same safe ASCII `.zip` name returned in the
successful response's exact `Content-Disposition` header; paths, control/header
characters, and Windows-reserved device names are rejected.

Generate the active Ed25519 key in an approved secret-management context. Put
only its base64url PKCS#8 representation into the protected Wrangler secret
`ENTITLEMENT_SIGNING_PRIVATE_KEY`; never place it in TOML, source, shell
arguments, logs, or chat. Bake its raw public key and a pre-generated next
public key into the private plugin. Rotate by shipping the overlapping public
ring first, switching the Worker `kid` and private secret second, and retaining
the retired public key until every receipt it issued has expired.

Deploy only through `.github/workflows/pro-gate-deploy.yml`. It is manual,
targets protected `pro-gate-staging` or `pro-gate-production` environments,
checks the signed annotated release tag and every reachable commit through the
GitHub verification API, renders protected config outside the repository,
requires an exact remote secret inventory, and deploys with Wrangler strict
mode. Production requires `PRO_GATE_STAGING_TESTED_COMMIT` to equal the exact
approved commit. The deployed Worker version tag is that commit—not merely the
release label—so every smoke can require `X-LAC-Deployment-Commit` equality.
Any post-deploy failure triggers an immediate Wrangler rollback; Cloudflare
notes that rollback restores the Worker version but does not recreate or revert
external resources, so R2/WAF changes remain separately operator-controlled.

Before the public endpoint is deployed, protect it with **both** the configured
[Workers Rate Limiting binding](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/)
and a Duan-approved custom-domain WAF rate-limit rule. The Worker hashes the
license key before using it as a limiter key and fails closed when the binding
is unavailable. The WAF handles broader anonymous/IP abuse before the Worker can
amplify requests to Polar. Worker counters are eventually consistent abuse
controls, never entitlement or quota accounting. The concrete WAF rule and
deployment evidence belong in the private checklist.

## Step 4 — Wire the client to the approved gate

The client has **exactly one** gate-URL source:
`PRO_GATE_URL` in [`../backend/pro_install.py`](../backend/pro_install.py),
which is a non-deployable placeholder in public source. After approval for
production Pro delivery, replace it from the private operator checklist before
building a Pro-enabled release. Until that approval and account-backed smoke test
are complete, treat Pro delivery as pending, not public-live. Source/development
runs can override it with `LAC_PRO_GATE_URL` (resolution order: explicit arg ->
env -> constant). Frozen release builds ignore both override paths and use only
the audited baked constant.

Two ways to point the client at the approved gate:

- **Bake it in (for the shipped installer):** after approval for launch, confirm
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

**Owner-approved.** These steps need the live Worker + a real Polar key, so they are
described here, not performed. Run them once on a **clean** machine/env (ideally
the packaged `lac.exe`, so you exercise the frozen client, not the source tree):

Before checkout opens, run the commerce gate from `model-hub`:

```bash
.venv/Scripts/python scripts/pro_commerce_readiness.py --require-baked-gate --live-gate
```

Set `LAC_PRO_TEST_KEY` only in the local shell that runs the check. The report
names the env var but never prints the key value.

1. **Local Pro happy path.** With a valid **test-mode Local Pro** Polar license key:
   ```bash
   lac unlock <key>
   ```
   Expect: the plugin downloads, installs into `~/.model-hub/plugins/`, and the
   Pro commands mount on the next start — e.g. `lac pro status` reports active and
   `lac pro tune` / `lac pro benchmark` appear in `lac --help`. Restarting LAC is
   what mounts the freshly installed plugin.
2. **Pro Cloud entitlement path.** With a separate test-mode key for the benefit
   configured as `PRO_CLOUD_BENEFIT_ID`, confirm the gate recognizes that exact
   entitlement and `lac unlock <key>` activates the clean machine as
   `Pro Cloud`. This also proves the same ID was baked into the private artifact.
   Pro Cloud checkout and hosted usage remain not yet available.
3. **Unrelated-product path.** A granted Polar key from the same organization but
   for an unrelated product/benefit must return `403` and write no plugin bytes.
4. **Sad path.** With an **invalid / expired** key: `lac unlock <bad-key>` must
   fail **cleanly** — the gate returns `403`, the CLI prints an honest
   "not accepted (invalid or expired)" message and a non-zero exit code, and
   **no Pro code is written to disk.**
5. **Integrity path.** Confirm the successful response's
   `X-LAC-Artifact-SHA256` is raw 64-hex and equals the downloaded bytes. A
   malformed or mismatched value must fail before `~/.model-hub/plugins/` changes.
   The invalid-key response and both Local/Cloud success responses must also
   contain one `X-LAC-Deployment-Commit` equal to the approved 40-character
   source commit; a missing, duplicate, malformed, stale, or mismatched value
   fails the smoke and triggers rollback.
6. **Receipt path.** Confirm activation returns a valid Ed25519 receipt. A
   modified claim/signature, wrong product, wrong benefit/plan, wrong license
   fingerprint, wrong installation binding, unknown/retired key, or expired receipt must fail closed, and
   revalidation must renew the same Polar activation.
7. **Free user.** With no key at all, confirm there is no `lac_pro` artifact in
   `~/.model-hub/plugins/` — a free install ships zero Pro code.

If all seven hold, the approved delivery chain is live and honest. Until an operator
runs them against an explicitly approved deployment, they are a checklist, not a
live-state claim.

## Rolling a new artifact

Rebuild from the private `lac-pro` repo, upload through the private deployment
checklist, record the new SHA256/byte size in private release notes, and rerun
the valid-key plus invalid-key smoke tests before public checkout is enabled.

## What the customer does

For reference: the intended **Local Pro** buyer flow after approved public launch:

1. Sign in to the LAC account with Google or GitHub.
2. Start the Local Pro checkout from that authenticated account. The API creates
   the Polar checkout with immutable account metadata; the redirect never grants
   access by itself.
3. After the signed Polar webhook grants the entitlement, Polar exposes the
   **license key** to the buyer.
4. Activate it: `lac unlock <key>` (CLI) or **Settings > Activate Pro** in the
   web UI.
5. Restart LAC so the Pro autopilot mounts on the next start. After activation,
   Local Pro runtime remains key-based and local.

Pro Cloud is the planned $20/month higher tier. It includes everything in Local
Pro, plus encrypted sync and capped hosted agents. Both products require an
account and both checkouts remain closed until they launch together. Nothing in
this runbook claims checkout or hosted usage is live.

## Push

Both repos push to their origins **only** on explicit owner approval: `model-hub`
to GitHub; `lac-pro` stays remote-less (its artifact lives in R2, not a git
remote).
