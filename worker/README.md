# LAC Pro artifact-delivery gate

A small, stateless Cloudflare Worker that validates a Polar license key and
streams the compiled LAC Pro artifact from a private R2 bucket. Both the Local
Pro and Pro Cloud benefits are eligible for the same artifact.

- **No application state.** No KV, D1, Durable Objects, or license-key logs.
- **No secrets in this repository.** Public configuration contains deliberate
  placeholders; account-specific values stay in private operator notes.
- **Explicit entitlement boundary.** Polar must return `status: "granted"`
  and a `benefit_id` matching `LOCAL_PRO_BENEFIT_ID` or
  `PRO_CLOUD_BENEFIT_ID`.
- **Bounded input.** Request bodies are capped at 16 KiB and license keys at
  512 characters before Polar is called.
- **Streaming delivery.** The R2 body is passed directly to `Response`; the
  artifact is never buffered in Worker memory.
- **Layered abuse control.** The Worker uses an in-code Rate Limiting binding
  keyed by a SHA-256 license fingerprint and fails closed if it is unavailable.
  Production additionally requires a custom-domain WAF rate-limit rule. Neither
  mechanism is quota or billing accounting.

## Endpoint

```http
POST /pro/download
Content-Type: application/json

{ "license_key": "<polar license key>" }
```

Only the exact `/pro/download` pathname is routed. A query string is allowed;
a trailing slash or any other pathname returns `404`.

| Condition | Response |
|---|---|
| Granted Local Pro or Pro Cloud benefit | `200`, streamed artifact with `Cache-Control: no-store` and `X-LAC-Artifact-SHA256` |
| Explicit invalid, expired, revoked, unrelated, or missing benefit | `403 {"error":"invalid_or_expired"}` |
| Polar timeout, network failure, malformed response, rate limit, or 5xx | `503 {"error":"validation_unavailable"}` |
| Public placeholder or malformed runtime configuration | `503 {"error":"configuration_unavailable"}`; the key is not sent to Polar |
| In-code limiter unavailable | `503 {"error":"abuse_protection_unavailable"}` |
| In-code abuse limit exceeded | `429 {"error":"abuse_rate_limited"}` |
| Missing, malformed, oversized body, or invalid key shape | `400 {"error":"invalid_request"}` |
| Non-POST method on the exact endpoint | `405 {"error":"method_not_allowed"}` with `Allow: POST` |
| Granted key but missing/unavailable R2 artifact | `503 {"error":"artifact_unavailable"}` |

Polar validation has an eight-second upstream timeout. Every response is marked
`Cache-Control: no-store`. The provider call requires a real `User-Agent`;
absent/default agents can be rejected by the upstream WAF.

## Public configuration

[`wrangler.toml`](./wrangler.toml) intentionally keeps account-specific values
as `replace-from-private-operator-notes`. The Worker refuses to forward a key
while those placeholders remain.

| Key | Meaning |
|---|---|
| `POLAR_ORG_ID` | Polar organization identifier |
| `LOCAL_PRO_BENEFIT_ID` | Polar benefit eligible through Local Pro |
| `PRO_CLOUD_BENEFIT_ID` | Polar benefit eligible through Pro Cloud |
| `ARTIFACT_KEY` | Immutable private R2 key containing version, exact SHA-256 segment, and response filename |
| `ARTIFACT_FILENAME` | Safe ASCII `.zip` filename for `Content-Disposition`; no paths, header characters, or Windows device names |
| `ARTIFACT_SHA256` | 64-character SHA-256 digest recorded at build/upload time |
| `R2_BUCKET` | Private artifact bucket binding |

The two benefit IDs must be non-empty and different. `ARTIFACT_SHA256` is sent
to the client as `X-LAC-Artifact-SHA256` so the downloaded stream can be checked
against the release digest.

Wrangler-generated binding types live in
[`worker-configuration.d.ts`](./worker-configuration.d.ts). Regenerate and
verify them after any binding change:

```powershell
npx.cmd wrangler types --include-runtime false --strict-vars false
npm.cmd run types:check
```

## Local verification

No Cloudflare account or remote binding is used by these checks:

```powershell
cd worker
npm.cmd install
npm.cmd test
npm.cmd run typecheck
npm.cmd run types:check
```

The tests execute inside Cloudflare's `workerd` runtime through
`@cloudflare/vitest-pool-workers`. They use a locally emulated R2 binding and a
mocked Polar request, covering both plans, exact routing, limits, failure
classification, cache/integrity headers, and the no-license-key-logging rule.

## Deploy (Duan-gated)

Deployment is not part of local implementation. For the complete private
artifact pipeline, see [`../docs/PRO-DELIVERY.md`](../docs/PRO-DELIVERY.md).

1. Build the private `lac-pro` artifact and record its byte size and SHA-256 in
   private release notes.
2. Upload the artifact to the approved private R2 bucket.
3. Replace all public placeholders from private operator notes, including both
   Polar benefit IDs and the matching artifact SHA-256.
4. Verify the account-unique in-code Rate Limiting binding **and** the approved
   custom-domain WAF rule so anonymous traffic cannot amplify Polar validation
   requests. Record WAF evidence privately.
5. Run tests, typecheck, generated-type check, and the commerce readiness gate.
6. Deploy only from Duan-approved Cloudflare account context.
7. Smoke-test valid Local Pro, valid Pro Cloud, invalid-key, and revoked-key
   paths without printing the keys.
