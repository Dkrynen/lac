# Per-Repo Commit-Signer Trust Roots — Design

**Date:** 2026-07-14
**Status:** Approved by Duan (design gate passed; "onboard Morne now" confirmed)
**Owner file:** `scripts/enterprise_launch_gate.py`
**Follows:** launch-gate scope split (2026-07-13 spec), final-review Minor 8, ledger forward trap 2026-07-14.

## Problem

`TRUSTED_COMMIT_SIGNERS` is one shared frozenset consumed by `check_repository`
for the commit-range check AND tag verification of every repo lane. Onboarding
Morne's lac-cloud signing key into it would also trust that key for model-hub
commits, lac-pro commits, and release tags. Without onboarding it, the
cloud-scope `lac_cloud_signed_commits` lane flips red on his first push.

## Decision

Replace the shared set with per-repo allowlists:

```python
_SIGNER_DKRYNEN = "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c"
_SIGNER_ARQUD = "SHA256:CdT6M0USfhHLOm5UqlZdwA+OdJqAtoxUGcPKtXCGKYI"
TRUSTED_COMMIT_SIGNERS_BY_REPO: dict[str, frozenset[str]] = {
    "model_hub": frozenset({_SIGNER_DKRYNEN}),
    "lac_pro": frozenset({_SIGNER_DKRYNEN}),
    "lac_cloud": frozenset({_SIGNER_DKRYNEN, _SIGNER_ARQUD}),
}
```

`check_repository` resolves `TRUSTED_COMMIT_SIGNERS_BY_REPO.get(name, frozenset())`
— an unknown repo lane name yields an empty allowlist and fails closed. Tag
verification keeps using the resolved set; tags are only requested on the
`model_hub` lane, so Morne's key can never authorize a release tag.

## Invariants (never weaken)

- model_hub and lac_pro commit ranges and the release tag verify against
  Duan's key only — byte-identical behavior to before this change.
- lac_cloud accepts exactly {Duan, Arqud}; this is the single behavioral delta.
- Unknown repo name → empty set → signed-commits and tag checks fail.
- Evidence-signer trust roots (`TRUSTED_EVIDENCE_SIGNERS`) unchanged.

## Testing

- Existing monkeypatches of `TRUSTED_COMMIT_SIGNERS` move to the dict shape.
- Trust-root assertion test pins the exact mapping (Arqud in lac_cloud only,
  every fingerprint normalisable).
- New behavioral test: a signer present only in the lac_cloud set is rejected
  when verifying a model_hub release tag.
- New behavioral test: a repo lane name absent from the mapping fails the
  signed-commits check even with a valid signature present.
- Suite: 46 → 48 green across the two gate test files; detect-secrets exit 0.

## Docs

`docs/release/enterprise-launch-gate.md`: state that commit-signer trust roots
are scoped per repository and tags verify against the model-hub set only.

## Out of scope

- Scope-scoped evidence signers (single reviewer today).
- Authenticode allowlists (await signing certificate).
