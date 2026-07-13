# LAC 2.7 Enterprise Launch Gate

`scripts/enterprise_launch_gate.py` is the final fail-closed, read-only release
gate for the coordinated Local Pro and Pro Cloud launch. It does not deploy,
publish, purchase, modify a repository, or read credentials.

Run it from `model-hub`:

```powershell
python scripts/enterprise_launch_gate.py `
  --evidence C:\private\LAC-Launch-Evidence\2.7.0.json
```

Exit code `0` means every local and externally evidenced gate passed. Exit code
`1` means checkout and publication must remain closed. The JSON output names
every failing gate without including remote URLs, credential values, evidence
references, or approver names.

## Evidence manifest

The evidence file is operator supplied, limited to 1 MiB, and must stay outside
the repository. It contains references to authoritative records, not reports or
secrets themselves. Schema v2 requires an exact top-level field set and an exact
set of required gates. Every record is signed independently with an allowlisted,
gate-scoped Ed25519 review key:

```json
{
  "schema_version": 2,
  "release_version": "2.7.0",
  "gates": {
    "patent_clearance": {
      "status": "approved",
      "approver": "responsible-reviewer",
      "reference": "authoritative-record-reference",
      "recorded_at": "2026-07-13T00:00:00Z",
      "record_sha256": "64-hex-digest-of-the-authoritative-record",
      "model_hub_commit": "40-lowercase-hex-model-hub-commit",
      "lac_pro_commit": "40-lowercase-hex-lac-pro-commit",
      "lac_cloud_commit": "40-lowercase-hex-lac-cloud-commit",
      "installer_sha256": "64-lowercase-hex-installer-digest",
      "release_provenance_sha256": "64-lowercase-hex-provenance-digest",
      "signer_kid": "approved-review-key-id",
      "signature": "base64url-ed25519-signature"
    }
  }
}
```

Every required record binds the exact `model-hub`, `lac-pro`, and `lac-cloud`
HEAD commits checked by the gate plus the exact lowercase SHA-256 digests of the
installer and `release-provenance.json`. Missing artifacts or repository heads
produce empty expected bindings and make every evidence record fail closed.

`cloud_staging_smoke` additionally binds its staging API, Agent, and Runner
Worker version UUIDs. `cloud_production_dark_smoke`, `regional_latency_slo`, and
`hosted_agent_end_to_end` bind their production Worker version UUIDs, and those
three production bindings must match one another exactly. Staging is
intentionally not cross-matched to production. The latency and hosted-journey
records also require `measured_at`, which is freshness-checked independently
from the later reviewer approval time with a 24-hour limit.

The hosted journey additionally signs four lowercase SHA-256 fields for the
journey manifest, admitted signed price-card payload, provider meter record, and
infrastructure meter record. Each digest must resolve to a distinct, nonempty,
valid UTF-8 JSON object of at most 256 KiB at the fixed content-addressed path
`<evidence-dir>/objects/<lowercase-digest>.json`. Symlinked, missing, duplicate-key,
tampered, malformed, or oversized objects fail closed; the manifest cannot
supply an alternative path.

Accepted status values are `approved`, `passed`, and `verified`. Placeholder,
pending, unsigned, stale, future-dated, untrusted, wrong-version, or malformed
records fail closed. Trust roots are empty by default and must be onboarded in a
reviewed source commit; an operator-supplied file cannot add its own signer.

Freshness is evaluated against the executing machine's clock. The authoritative
publication run must therefore execute in protected CI with retained run
provenance and a trustworthy runner clock; a local invocation is only a
preflight and cannot authorize publication by itself.

The required gates are defined in `REQUIRED_EVIDENCE_GATES` inside the script
and cover patent clearance, GitHub governance, Polar readiness, Cloudflare
account ownership, Turnstile and WAF validation, staging and production smokes,
fresh exact-commit regional latency SLO evidence, a real authenticated hosted
agent journey with signed pricing and both cost meters, paid beta, penetration
and cryptographic review, remediation, incident and
recovery drills, artifact roundtrip, and clean-machine signed installation.

## Repository and artifact checks

The gate also verifies:

- `model-hub`, `lac-pro`, and `lac-cloud` are Git repositories with clean trees;
- every unpublished `model-hub` commit after the immutable public-upstream
  ancestor, plus each private launch-range commit, has a good signature from an
  allowlisted signer;
- the exact `v2.7.0` release ref is an annotated tag, peels to the gated
  `model-hub` HEAD, and has a valid signature from the same explicit signer
  allowlist;
- `lac-pro` has zero remotes;
- `lac-cloud` has the approved `Acend-co/lac-cloud` remote;
- `lac-cloud`'s strict product-readiness command reports the exact
  `hosted_agent_local_complete` state with no missing capability; its current
  fail-closed foundation state therefore remains an explicit launch blocker;
- the exact 2.7.0 installer exists and has exactly one, non-duplicated matching
  entry in `SHA256SUMS.txt`;
- both the installer and packaged `lac.exe` have an allowlisted Authenticode
  subject and thumbprint, plus a verified RFC3161 timestamp whose timestamping
  certificate has the correct EKU and was valid at the recorded signing time;
- schema-v2 `release-provenance.json` has an exact, fail-closed field set and
  binds the version, annotated tag, source commit, actual
  `requirements-release.lock` SHA-256, installer and application file sizes,
  checksums, signature states, exact RFC3161 timestamp-certificate evidence,
  and the exact byte sizes and SHA-256 hashes of both `python-sbom.json` and
  `web-sbom.json`; its `built_at_utc` must be canonical UTC RFC3339, no more
  than five minutes in the future, no more than 14 days old, and within 24
  hours after both artifact signing timestamps; and
- `gh attestation verify` separately confirms GitHub's signed SLSA provenance
  for the exact installer, packaged `lac.exe`, `SHA256SUMS.txt`,
  `release-provenance.json`, and canonical `dist` copies of both SBOMs. Every
  subject must bind the same source commit, release tag, hosted runner, and
  pinned build workflow; one missing or mismatched subject fails the gate.

The retained `signed-windows-*` CI artifact includes all six attested subjects
so an operator can re-run verification after download. The build workflow is
candidate-only: it has no `contents: write` permission and creates no draft or
published GitHub release. A future protected publication workflow must run this
enterprise gate successfully before acquiring release-write permission. The
signed installer remains the eventual public application delivery unit.

The gate records only counts and pass/fail state for Git policy checks. It does
not expose remote addresses from the machine or the contents of the evidence
manifest.
