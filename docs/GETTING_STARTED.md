# Getting Started

LAC's shortest useful path is a private repository inspection followed by a
local coding-agent launch. Setup is evidence-driven: `lac doctor` reports what
is ready, what is uncertain, and the exact next action.

## 1. Install LAC and the prerequisites

### Windows installer

Run a published `LAC-Setup-x.x.x.exe`. If that installer offers **Add LAC to
PATH**, leave it selected. The v2.7.0 source installer is designed to add only
LAC's installation directory and record its exact value so a successful
uninstall can remove that owned entry without deleting the rest of PATH. This
is not a public release guarantee until the clean-machine install, upgrade,
and uninstall gate passes.

After setup finishes, open a **new** PowerShell or Command Prompt window, enter
the repository you want to work on, and verify the command:

```powershell
lac --help
lac doctor .
```

Installer builds containing this guide also put an offline **Getting Started**
shortcut in the LAC Start Menu folder.

### Source or pipx install

The current verified agent path requires:

- Python 3.10 or newer for a source or pipx install;
- a local [Ollama](https://ollama.com/download) runtime;
- OpenCode `1.18.9`;
- at least 5 GB free on the repository volume; and
- an explicitly installed agent-capable model that LAC recommends for the
  detected machine.

LAC does not silently install OpenCode, start Ollama, or download a model.

For a source or pipx installation:

```bash
pipx install git+https://github.com/Dkrynen/lac
```

For every installation, install the pinned OpenCode runtime:

```bash
npm install --global opencode-ai@1.18.9
```

Start Ollama through its normal platform launcher, then enter a repository.

## 2. Diagnose setup

```bash
lac doctor .
```

For automation:

```bash
lac doctor . --json
```

The doctor checks:

- repository availability;
- detected RAM and verified model-fit GPU capacity;
- free disk;
- local Ollama reachability;
- installed models against LAC's agent recommendations;
- exact OpenCode compatibility;
- whether `lac` is on PATH; and
- whether the local receipt store is writable.

Warnings remain visible but do not necessarily block launch. A failed check
makes the doctor exit nonzero.

## 3. Produce the first private receipt

```bash
lac inspect .
```

The command maps the repository without launching an LLM. It does not run
tests, project code, lifecycle scripts, package managers, or network requests.
It reports any recognized check commands as **discovered, not executed**.

Receipts are stored beneath:

```text
~/.model-hub/receipts/repository-inspections/
```

Each receipt records the repository fingerprint, detected stack, entry points,
instruction files, candidate checks, privacy guarantees, safety limits, and
findings.

The inspection excludes:

- `.env` files, credentials, tokens, private keys, and certificates;
- `.git`, dependency directories, virtual environments, caches, and build
  outputs;
- the LAC private data root; and
- symlinks, junctions, and other project-root indirection.

Machine-readable output:

```bash
lac inspect . --json
```

The existing model command remains compatible:

```bash
lac inspect qwen3:8b
```

## 4. Install a model deliberately

If the doctor reports that no compatible installed model exists:

```bash
lac recommend --use-case agent
lac pull <model-from-the-recommendation>
lac doctor .
```

Model downloads can be many gigabytes. LAC never starts one from `doctor`,
`inspect`, or `agent` without the explicit model-install command.

Reported shared integrated-GPU memory is excluded from model-fit capacity until
a bounded runtime probe verifies that the active runtime can use that exact
split on that machine.

## 5. Launch the local agent

When the doctor is ready:

```bash
lac agent .
```

LAC selects an installed compatible model, prepares its local agent variant and
fail-closed OpenCode configuration, and launches OpenCode in the repository.
The current adapter supports exactly OpenCode `1.18.9`. OpenCode permissions
are defense in depth; they are not a process sandbox. Writes, shell commands,
and network actions remain approval-sensitive, and unrestricted subagents are
disabled in the initial configuration.

## Recovery

### Ollama unavailable

Start the local Ollama application or service, confirm `ollama list` works, and
rerun `lac doctor .`.

### No compatible installed model

Run `lac recommend --use-case agent`, explicitly pull one recommendation, and
rerun the doctor. No setup command downloads for you.

### Unsupported OpenCode

Install the exact supported runtime:

```bash
npm install --global opencode-ai@1.18.9
opencode --version
```

### `lac` is not on PATH

Open a new terminal after installation. On Windows, rerun setup and leave
**Add LAC to PATH** selected if `lac` still does not resolve. Do not replace the
whole Windows PATH value. A source checkout can use its virtual environment's
console script.

### Receipt store is not writable

Correct permissions for `~/.model-hub`. Do not redirect receipts into the
repository, a credentials directory, or a shared network path.

### Low disk

Free at least 5 GB on the repository volume before agent work. Model downloads
may require substantially more; use the size shown by the chosen model.

## Current evidence boundary

Passing `lac doctor` proves setup checks for this machine at that moment.
Passing `lac inspect` proves a bounded local repository map and receipt. Neither
proves that every model can complete every coding task, that the OpenCode
permission layer is a sandbox, or that a public installer is signed and
release-ready. Source compilation does not prove PATH mutation, upgrade, or
uninstall behavior; those remain clean-machine installer checks.

### Internal agent-evidence preflight

The packaged evaluator has a read-only preflight. It creates no evaluation
workspace, consumes no model tokens, and no downloads occur:

```powershell
# Read-only preflight; no model tokens
lac eval --task python-empty-mean `
  --base-model gpt-oss:20b `
  --lac-model gpt-oss:20b-agent `
  --output-dir C:\lac-evidence `
  --run-id phase0-smoke `
  --dry-run --json
```

Verified mode needs elevated Windows PowerShell for dynamic loopback-only
network containment. Until a clean privileged build passes that dry-run, do
not treat the live verified command as ready.

For local development only:

```powershell
# Explicit non-evidence developer run
lac eval --task python-empty-mean `
  --base-model gpt-oss:20b `
  --lac-model gpt-oss:20b-agent `
  --output-dir C:\lac-evidence `
  --mode diagnostic
```

Diagnostic artifacts are invalid. The task schedules three counterbalanced
trials across three arms, so nine bounded arm runs require operator approval
before token generation. One smoke is not a competitive capability claim.
