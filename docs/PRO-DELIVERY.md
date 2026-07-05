# LAC Pro delivery runbook (operator)

How the compiled **LAC Pro** plugin gets from source to a paying customer's
machine. This is the end-to-end chain the operator runs at deploy; the
customer-facing "how do I turn Pro on" answer lives in the README and the
landing page (see [What the customer does](#what-the-customer-does)).

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

Steps 2–5 need Duan's Cloudflare + Polar accounts. This runbook **describes**
them so they're followable; it does not perform them.

## Prerequisites

- The **`lac-pro`** repo checked out beside `model-hub` (they're sibling repos;
  the Worker's upload example assumes `../../lac-pro/` from `model-hub/worker/`).
- A **CPython 3.11** build environment with **Nuitka** installed
  (`pip install nuitka` — additive). `model-hub`'s `.venv` is 3.11 and works
  once Nuitka is added. MSVC Build Tools must be present; Nuitka finds them via
  `vswhere`, so you do **not** need to run `vcvarsall` or put `cl.exe` on PATH.
- **`wrangler`** — used on demand via `npx`, nothing extra is committed.
- A **Polar** organisation and, for the smoke test, a **test-mode license key**.

---

## Step 1 — Build the artifact

From the `lac-pro` repo, with the ABI-correct interpreter:

```bash
cd ../lac-pro          # sibling of model-hub
# build with a CPython 3.11 venv that has Nuitka installed
python build/build_artifact.py
# -> build/dist/lac-pro-0.1.0-cp311-win_amd64.zip
#    sha256 : <printed>
#    bytes  : <printed>
```

- **ABI lock.** The script **refuses to run** on any Python other than
  CPython 3.11 — the compiled `.pyd` is `cp311-win_amd64` and must match the
  minor version the shipped `lac.exe` freezes. Build-Python == ship-Python, or
  the plugin won't import on the customer's machine.
- **What ships.** A zip whose root holds the native `lac_pro.cp311-win_amd64.pyd`
  plus its `lac_pro-0.1.0.dist-info/` (entry-point discovery keys on
  `dist-info/entry_points.txt`, so both must be present). The build asserts no
  readable `.py`/`.pyc` and no source comments leak into the bytes.
- **Determinism.** Same source → identical *layout* and *filename*, but **not**
  byte-identical (Nuitka embeds a build timestamp). The printed **sha256**
  identifies that one build's exact bytes — record it, you'll use it to confirm
  the upload landed intact.

## Step 2 — Upload the artifact to R2

Create a **private** R2 bucket (once) and upload the built zip under the object
key the Worker serves. The Worker's `ARTIFACT_KEY` var defaults to
`lac-pro-latest.zip`; the R2 object key is yours to choose but must match it.

```bash
cd ../model-hub/worker
npx wrangler login                                   # first time only
npx wrangler r2 bucket create lac-pro-artifacts      # name must match wrangler.toml
npx wrangler r2 object put lac-pro-artifacts/lac-pro-latest.zip \
  --file ../../lac-pro/build/dist/lac-pro-0.1.0-cp311-win_amd64.zip
```

The bucket stays private — the Worker reads it through its R2 binding, so the
bytes are never publicly addressable. The bucket name must match `bucket_name`
in `worker/wrangler.toml` (or edit it to taste). See the Worker's own README for
the full var table.

## Step 3 — Deploy the Worker

The Worker is documented end-to-end in **[`../worker/README.md`](../worker/README.md)** —
follow its "Deploy" section rather than re-reading it here. In short:

1. In `worker/wrangler.toml`, point the `[[r2_buckets]]` binding at the real
   bucket and set the `[vars]` — `POLAR_ORG_ID` (public Polar org UUID),
   `ARTIFACT_KEY` (the object key from Step 2), and `ARTIFACT_FILENAME`. None are
   secret, so no `wrangler secret` is needed.
2. Deploy:
   ```bash
   cd ../model-hub/worker
   npx wrangler deploy       # or: npm run deploy
   ```
3. **Note the deployed URL** — `https://<worker-subdomain>.workers.dev`. The
   client calls `POST <that URL>/pro/download`. You need it for Step 4.

## Step 4 — Wire the client to the live gate

The client has **exactly one** gate-URL source:
`PRO_GATE_URL` in [`../backend/pro_install.py`](../backend/pro_install.py)
(line 44), which ships as a placeholder and is overridable at runtime by the
`LAC_PRO_GATE_URL` env var (resolution order: explicit arg → env → constant).

Two ways to point the client at the Worker from Step 3:

- **Bake it in (for the shipped installer):** set `PRO_GATE_URL` to the real
  `https://<worker-subdomain>.workers.dev/pro/download`, then **rebuild the app**
  so the frozen `lac.exe` embeds the live gate. This is the correct path for a
  public release.
- **Override at runtime (for testing / staging):** leave the constant alone and
  export `LAC_PRO_GATE_URL=https://<worker-subdomain>.workers.dev/pro/download`
  before running `lac unlock`. Handy for pointing a dev build at a test Worker
  without a rebuild.

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

Rebuild (Step 1) and re-upload to the **same** object key — no Worker redeploy
needed, it streams whatever is at `ARTIFACT_KEY`:

```bash
npx wrangler r2 object put lac-pro-artifacts/lac-pro-latest.zip \
  --file ../../lac-pro/build/dist/lac-pro-<new-ver>-cp311-win_amd64.zip
```

(To keep old versions around instead, upload under a new key and bump
`ARTIFACT_KEY` + redeploy.)

## What the customer does

For reference — the buyer's side, documented on the README and the landing page:

1. Buy Pro via the Polar checkout (no account needed).
2. Polar emails a **license key**.
3. Activate it: `lac unlock <key>` (CLI) **or** **Settings → Activate Pro** in
   the web UI.
4. Restart LAC — the Pro autopilot mounts on the next start.

## Push

Both repos push to their origins **only** on Duan's explicit go: `model-hub`
→ GitHub; `lac-pro` stays remote-less (its artifact lives in R2, not a git
remote).
