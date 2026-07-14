# Per-Repo Commit-Signer Trust Roots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scope commit-signer trust roots per repository so Morne's key is trusted for lac-cloud history only — never for model-hub commits, lac-pro commits, or release tags.

**Architecture:** Replace the module-global `TRUSTED_COMMIT_SIGNERS` frozenset with `TRUSTED_COMMIT_SIGNERS_BY_REPO`, resolved inside `check_repository` by repo lane name with an empty-set (fail-closed) default.

**Tech Stack:** Python 3 (`.venv/Scripts/python`), pytest. Windows host.

**Spec:** `docs/superpowers/specs/2026-07-14-per-repo-signer-trust-roots-design.md` (approved).

## Global Constraints

- model_hub / lac_pro / release-tag verification stays Duan-key-only — behavior byte-identical to before. lac_cloud = exactly {Dkrynen, Arqud}. Unknown repo name → empty allowlist → fail closed.
- Fingerprints verbatim: Dkrynen `SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c`, Arqud `SHA256:CdT6M0USfhHLOm5UqlZdwA+OdJqAtoxUGcPKtXCGKYI`.
- `TRUSTED_EVIDENCE_SIGNERS`, Authenticode allowlists, and `tests/test_release_workflow_contract.py` untouched.
- Baseline 46 green across the two gate test files; task ends at 48 green, detect-secrets exit 0.
- Repo `C:\Users\User\repos\model-hub`, master, local-only — never push, never touch git config (commits auto-sign).

---

### Task 1: Per-repo signer mapping

**Files:**
- Modify: `scripts/enterprise_launch_gate.py` (constants block ~line 126-141; `check_repository` trusted-signers comprehension ~line 623-627)
- Modify: `tests/test_enterprise_launch_gate.py` (three monkeypatches; trust-root assertion test; two new tests)
- Modify: `docs/release/enterprise-launch-gate.md` (one sentence)

**Interfaces:**
- Produces: `TRUSTED_COMMIT_SIGNERS_BY_REPO: dict[str, frozenset[str]]` (replaces `TRUSTED_COMMIT_SIGNERS`, which must no longer exist anywhere).

- [ ] **Step 1: Update existing tests + add two failing tests.**

In `tests/test_enterprise_launch_gate.py`, replace the three `monkeypatch.setattr(gate, "TRUSTED_COMMIT_SIGNERS", ...)` calls inside `test_release_tag_must_be_annotated_signed_target_head_and_use_trusted_signer`:
- `frozenset({signer})` (both occurrences) → `{"model_hub": frozenset({signer})}` with attribute name `"TRUSTED_COMMIT_SIGNERS_BY_REPO"`.
- `frozenset()` → `{"model_hub": frozenset()}` with attribute name `"TRUSTED_COMMIT_SIGNERS_BY_REPO"`.

In `test_trust_roots_are_onboarded_and_well_formed`, replace the `TRUSTED_COMMIT_SIGNERS` assertions with:

```python
    assert gate.TRUSTED_COMMIT_SIGNERS_BY_REPO == {
        "model_hub": frozenset({
            "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c",
        }),
        "lac_pro": frozenset({
            "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c",
        }),
        "lac_cloud": frozenset({
            "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c",
            "SHA256:CdT6M0USfhHLOm5UqlZdwA+OdJqAtoxUGcPKtXCGKYI",
        }),
    }
    assert all(
        gate._normalise_signer(signer)
        for signers in gate.TRUSTED_COMMIT_SIGNERS_BY_REPO.values()
        for signer in signers
    )
```

Append two new tests at the end of the file:

```python
def test_cloud_only_signer_cannot_authorize_a_model_hub_release_tag(tmp_path, monkeypatch):
    gate = _load_gate()
    repo = _repo(tmp_path, "tagged")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "-a", "v2.7.0", "-m", "release 2.7.0")
    real_git = gate._git
    signer = "B" * 40

    def signed_tag(repo_path, *args):
        if args == ("verify-tag", "--raw", "refs/tags/v2.7.0"):
            return subprocess.CompletedProcess(
                args,
                0,
                "",
                f"[GNUPG:] NEWSIG\n[GNUPG:] VALIDSIG {signer} 2026-07-14 0 4 0 1 10 00 {signer}\n",
            )
        return real_git(repo_path, *args)

    monkeypatch.setattr(gate, "_git", signed_tag)
    monkeypatch.setattr(gate, "TRUSTED_COMMIT_SIGNERS_BY_REPO", {
        "model_hub": frozenset(),
        "lac_cloud": frozenset({signer}),
    })

    rows = gate.check_repository(
        "model_hub",
        repo,
        release_tag="v2.7.0",
        expected_tag_target=head,
    )

    assert next(
        row for row in rows if row["name"] == "model_hub_signed_release_tag"
    )["ok"] is False


def test_unknown_repo_name_resolves_to_an_empty_signer_allowlist(tmp_path, monkeypatch):
    gate = _load_gate()
    repo = _repo(tmp_path, "mystery")
    signer = "C" * 40
    real_git = gate._git

    def signed_log(repo_path, *args):
        if args and args[0] == "log":
            return subprocess.CompletedProcess(args, 0, f"G\x00{signer}\n", "")
        return real_git(repo_path, *args)

    monkeypatch.setattr(gate, "_git", signed_log)

    rows = gate.check_repository("mystery_repo", repo)

    assert next(
        row for row in rows if row["name"] == "mystery_repo_signed_commits"
    )["ok"] is False
```

- [ ] **Step 2: Run to verify the changed/new tests fail** (AttributeError on the missing dict / assertion failures):

Run: `cd C:\Users\User\repos\model-hub; .venv/Scripts/python -m pytest tests/test_enterprise_launch_gate.py -q -k "trust_roots or release_tag or cloud_only or unknown_repo"`
Expected: FAIL

- [ ] **Step 3: Implement in `scripts/enterprise_launch_gate.py`.**

Replace the block:

```python
TRUSTED_COMMIT_SIGNERS: frozenset[str] = frozenset({
    # Duan Krynen - SSH Ed25519 release signing key (~/.ssh/lac_git_signing)
    "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c",
})
```

with:

```python
# Commit-signer trust roots are scoped per repository. A repository name
# without an entry resolves to an empty allowlist and fails closed. Release
# tags verify against the model_hub set only, because only that lane
# requests tag checks.
_SIGNER_DKRYNEN = "SHA256:1e+lhgtrePHcjsvpPTQLLYRqwgwgBp07HCi2mdo+Q8c"
_SIGNER_ARQUD = "SHA256:CdT6M0USfhHLOm5UqlZdwA+OdJqAtoxUGcPKtXCGKYI"
TRUSTED_COMMIT_SIGNERS_BY_REPO: dict[str, frozenset[str]] = {
    "model_hub": frozenset({_SIGNER_DKRYNEN}),
    "lac_pro": frozenset({_SIGNER_DKRYNEN}),
    "lac_cloud": frozenset({_SIGNER_DKRYNEN, _SIGNER_ARQUD}),
}
```

In `check_repository`, change:

```python
    trusted_signers = {
        normalised
        for signer in TRUSTED_COMMIT_SIGNERS
        if (normalised := _normalise_signer(signer))
    }
```

to:

```python
    trusted_signers = {
        normalised
        for signer in TRUSTED_COMMIT_SIGNERS_BY_REPO.get(name, frozenset())
        if (normalised := _normalise_signer(signer))
    }
```

Verify no other reference to the old name remains: `grep -n "TRUSTED_COMMIT_SIGNERS\b" scripts/ tests/` must show only `TRUSTED_COMMIT_SIGNERS_BY_REPO`.

- [ ] **Step 4: Doc sentence.** In `docs/release/enterprise-launch-gate.md`, in the sentence added 2026-07-14 ("Commit and evidence-review trust roots were onboarded..."), append after "on 2026-07-14": ", with commit-signer allowlists scoped per repository (the lac-cloud contributor key is trusted for lac-cloud history only; release tags verify against the model-hub allowlist)".

- [ ] **Step 5: Full green + secrets scan.**

Run: `cd C:\Users\User\repos\model-hub; .venv/Scripts/python -m pytest tests/test_enterprise_launch_gate.py tests/test_release_workflow_contract.py -q`
Expected: 48 passed
Run: `.venv/Scripts/python -m detect_secrets.pre_commit_hook --baseline .secrets.baseline scripts/enterprise_launch_gate.py tests/test_enterprise_launch_gate.py`
Expected: exit 0 (if the Arqud fingerprint trips the scanner, add the repo's standard inline `# pragma: allowlist secret -- SSH public-key fingerprint, not a secret` comment on that line and re-run)

- [ ] **Step 6: Commit** (auto-signed):

```powershell
git add scripts/enterprise_launch_gate.py tests/test_enterprise_launch_gate.py docs/release/enterprise-launch-gate.md
git commit -m "feat(release): per-repo commit-signer trust roots - onboard lac-cloud contributor key"
```
