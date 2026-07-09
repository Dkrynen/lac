# LAC Pro delivery gate (Cloudflare Worker)

A tiny, **stateless** Cloudflare Worker that gates downloads of the compiled
LAC **Pro** plugin. It validates a [Polar](https://polar.sh) license key, and
on success streams the Pro artifact straight from a private R2 bucket.

- **No state.** No KV, no D1, no Durable Objects, no database.
- **No PII / no secrets in this repo.** Account-specific IDs, bucket names,
  artifact keys, and deploy commands stay in private operator notes. The license
  key is passed to the provider and **never logged**.
- **Fails closed.** Any doubt — non-`granted` status, a 4xx body, a non-JSON WAF
  wall, or a network error — returns `403`.
- **Free-tier compatible.** Nothing here uses a paid Cloudflare feature.

## Endpoint

```
POST /pro/download
Content-Type: application/json

{ "license_key": "<polar license key>" }
```

| Condition | Response |
|---|---|
| Polar returns `status: "granted"` | `200`, artifact bytes streamed from R2 (`Content-Type: application/octet-stream`, `Content-Disposition: attachment; filename="…"`) |
| Any other Polar outcome (other status, 4xx body, non-JSON, network error) | `403 {"error":"invalid_or_expired"}` |
| Missing / non-string `license_key` | `400 {"error":"invalid_request"}` |
| Non-POST method | `405 {"error":"method_not_allowed"}` (with `Allow: POST`) |
| Key valid but artifact missing from R2 | `503 {"error":"artifact_unavailable"}` |

The provider validation call requires a real `User-Agent`; absent/default
User-Agents can be blocked before the request reaches the API. Account-specific
provider URL fields and organization values stay in private operator notes.

## Configuration (`wrangler.toml`)

| Key | Meaning |
|---|---|
| `[vars] POLAR_ORG_ID` | Provider organization identifier, filled from private operator notes before deploy. |
| `[vars] ARTIFACT_KEY` | Private artifact object key, filled from private operator notes before deploy. |
| `[vars] ARTIFACT_FILENAME` | Filename offered to the client in `Content-Disposition`. |
| `[[r2_buckets]] binding = "R2_BUCKET"` | The private artifact bucket. Emulated locally in tests. |

## Testing (local, no Cloudflare account needed)

```bash
cd worker
npm install
npm test
```

Tests run under plain **Vitest** with a thin handler harness: the Worker's
exported `fetch` is invoked directly with a **mocked global `fetch`** (Polar)
and a **faked R2 binding** (a `Map`-backed store returning a real
`ReadableStream` body). This exercises the real request → validate → stream
logic without needing `workerd`.

> **Toolchain note.** The plan preferred `@cloudflare/vitest-pool-workers`
> (real `workerd` + Miniflare-emulated R2). Its current npm release
> (`0.18.0`) ships a broken package `exports` map — the documented
> `@cloudflare/vitest-pool-workers/config` entrypoint isn't exported, so the
> vitest config can't load under `vitest@4`. The thin-harness fallback above
> meets the same bar (mocked Polar + emulated R2, real streaming) with zero
> native/`workerd` dependencies, which is also friendlier for an open-source
> repo and Windows contributors.

## Deploy (Duan-gated — needs the Cloudflare account)

Not part of the build task; run only from Duan-approved operator context.

> Deploying this Worker is one link in the chain. For the whole pipeline —
> build the artifact → upload to R2 → deploy this Worker → wire the client →
> end-to-end smoke — see [`../docs/PRO-DELIVERY.md`](../docs/PRO-DELIVERY.md).

1. Build the private `lac-pro` artifact and record its SHA256/byte size in
   private release notes.
2. Upload the artifact to private Cloudflare storage using the private operator
   checklist.
3. Fill `POLAR_ORG_ID`, `ARTIFACT_KEY`, `ARTIFACT_FILENAME`, and the R2 binding
   from private operator notes.
4. Deploy from the approved Cloudflare account.
5. Smoke test with a real/test license key without printing the key in logs.

To roll a new artifact, repeat the private upload checklist, update the private
release notes, and rerun the valid-key plus invalid-key smoke tests before
public checkout is enabled.
