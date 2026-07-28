<img src="assets/leaf-mark.svg" width="72" alt="LAC vein-leaf mark" />

# LAC — local AI, sorted.

**Give LAC a repository. It maps the project privately, proves what your
machine can run, and prepares a local coding agent with explicit safety
boundaries.**

LAC is a local-model control plane and coding-agent launcher. It scans your
hardware, recommends models that actually fit, configures a pinned
[OpenCode](https://github.com/anomalyco/opencode) runtime, and records truthful
local evidence. Ollama remains the inference runtime; LAC supplies the hardware
intelligence, model profile, containment policy, and receipts.

## Local AI first

The default assumption is that serious coding agents need cloud models. LAC
challenges that directly.

On a mid-range AMD rig (RX 6800 XT 16 GB, Ryzen 5 7600, 32 GB RAM), a local
20B model answered a multi-step code-reasoning task correctly through the full
OpenCode agent loop — reading a file, reasoning about it, and returning the
exact answer — at **94.67 tok/s**, with no cloud calls and no API key.

The gap was never the model. It was the tooling around it: context window too
small for the agent loop, no hardware-aware model selection, no tuned variant.
LAC closes that gap.

```bash
lac agent .   # scan → recommend → tune → launch. One command, local, private.
```

Your code never leaves your machine. No API bills. No rate limits. No
third-party logging your prompts. LAC picks the best agent-capable model for
your exact hardware, raises its context window to the agent-loop floor (32k),
and hands it to OpenCode already configured.

The free tier does all of this. **Pro** adds the tuning cockpit: GPU offload
sweeps, RAM spill, iGPU offload — the difference between "runs" and "runs
well on your rig."

Read the full case: [Local AI First](docs/LOCAL_AI_FIRST.md).

## First private win

From a repository:

```bash
lac doctor .       # readiness evidence; never installs or downloads
lac inspect .      # local-only repository map + durable JSON receipt
lac agent .        # launch the supported local-model agent when ready
```

`lac inspect .` does not execute project code, package scripts, tests, or
network requests. It discovers supported stacks, entry points, repository
instructions, and candidate checks while excluding secret-shaped files,
dependency trees, VCS internals, build outputs, and symlinks. Candidate checks
are reported as **discovered, not executed**.

See [Getting Started](docs/GETTING_STARTED.md) for setup and recovery.

## Internal agent-evidence command

`lac eval` is a bounded internal evaluator, not a public benchmark claim. Its
read-only preflight creates no output, consumes no model tokens, and no
downloads occur:

```powershell
# Read-only preflight; no model tokens
lac eval --task python-empty-mean `
  --base-model gpt-oss:20b `
  --lac-model gpt-oss:20b-agent `
  --output-dir C:\lac-evidence `
  --run-id phase0-smoke `
  --dry-run --json

# Explicit non-evidence developer run
lac eval --task python-empty-mean `
  --base-model gpt-oss:20b `
  --lac-model gpt-oss:20b-agent `
  --output-dir C:\lac-evidence `
  --mode diagnostic
```

Verified mode needs elevated Windows PowerShell so dynamic OS-level loopback
containment can be opened, checked, and torn down. Diagnostic artifacts are
invalid and cannot be promoted into verified evidence. A live task performs
nine bounded arm runs; those nine bounded arm runs require operator approval
after a clean privileged dry-run displays the maximum runtime. One smoke is
not a competitive capability claim. The live verified command is not
documented as ready until that privileged clean-build gate passes.

## Features

- **Hardware scan** — Windows-first GPU, VRAM, RAM, CPU detection, with source/CLI probes for Linux and macOS (NVIDIA, AMD, Apple Silicon, Intel)
- **Fit-aware recommendations** — 91-model curated catalog scored by quality, speed, hardware fit, and context; multi-GPU tiering (dGPU → iGPU → RAM) with per-model split plans
- **Real-speed calibration** — recs are tagged `measured` / `calibrated` / `estimated` with confidence bands; LAC Pro can feed the `measured` tier for supported installs after activation
- **What-if controls** — toggle GPUs on/off, allow/deny RAM spill, and watch the recommendations recompute live in the web UI
- **Model management + chat** — install, run, delete; streaming chat with session persistence; full TUI
- **Local-agent doctor** — structured hardware, disk, Ollama, installed-model,
  OpenCode, PATH, and receipt-store evidence with exact remediation
- **Private repository receipt** — bounded read-only mapping with no project
  execution or network access
- **Terminal agent** — LAC selects an installed model that fits the machine,
  prepares its local agent profile, and launches the pinned OpenCode runtime

## Install

### Windows (recommended)

Download the latest published `LAC-Setup-x.x.x.exe` from
[Releases](https://github.com/Dkrynen/lac/releases). Installer builds that
offer **Add LAC to PATH** should be run with that task selected. Open a new
terminal, enter your repository, and run:

```powershell
lac doctor .
lac inspect .
```

The v2.7.0 source installer is designed to add only its own directory to PATH
and remove that exact installer-owned entry after a successful uninstall. That
lifecycle remains a clean-machine release gate; do not assume an older
published installer contains it. Local development builds may be ahead of the
public Releases page.

### Any platform (CLI via pipx)

```bash
# Requires Python 3.10+ and Ollama (https://ollama.com/download)
pipx install git+https://github.com/Dkrynen/lac
lac doctor .      # what is missing before useful local-agent work?
lac inspect .     # map this repository privately
lac recommend --use-case agent
lac pull <the-model-you-explicitly-chose>
lac agent .
```

### macOS & Linux apps

Coming soon — **[join the waitlist](https://dkrynen.github.io/lac/)** and each platform release lands in your inbox.

## Local Pro - the Tuning Cockpit

The free tier is complete and stays free. **Local Pro** adds the paid power tools:

- **Autopilot** - after Pro is installed and licensed, supported model installs can be benchmarked, GPU-offload swept, and tuned to your exact rig
- **`lac pro tune <model>` / `lac pro benchmark <model>`** - manual on-demand re-runs of the same sweep and benchmark steps autopilot uses
- **Private Hugging Face imports** - GGUF-first import for compatible repos, with local token storage for gated/private models
- **Local coding cockpit** - readiness checks and launch guidance for stronger coding models and agent workflows
- **Insights** - calibration history and regression detection ("your tok/s dropped 12% since that driver update")

Local Pro is planned at **$36/year** (the equivalent of $3/month). Checkout is **not open yet**; the [waitlist](https://dkrynen.github.io/lac/) hears first.

**Pro Cloud** is the planned **$20/month** higher tier. It includes everything in Local Pro, plus end-to-end encrypted sync and capped hosted agents. Encrypted sync is designed so LAC cannot read the ciphertext. Hosted processing is a separate, explicit path: only selected job inputs are decrypted for execution and may be sent to approved model providers. It is **not yet available**: checkout, hosted usage, quotas, and cloud entitlements must not be treated as live.

**At launch,** every paid buyer first signs in to a LAC account with Google or GitHub. Checkout starts from that authenticated account, and access follows the signed Polar webhook rather than the browser redirect. Polar then provides the Local Pro license key. Run `lac unlock <key>` or use **Settings -> Activate Pro** in the web UI; after activation the Local Pro runtime remains key-based and local. Restart LAC so the Pro cockpit mounts cleanly. Free installs ship no Pro code.

## Hardware detection

| GPU | Method | Verified |
|-----|--------|----------|
| NVIDIA | `nvidia-smi` | NVIDIA driver/CLI path |
| AMD (Windows) | `vulkaninfo` → registry → WMI | RX 6800 XT (16 GB) |
| AMD (Linux) | `/sys/class/drm` sysfs | ROCm/Vulkan |
| Apple Silicon | `sysctl` unified memory | M-series |
| Intel | Registry fallback | Arc, UHD, Iris |

## Development

```bash
git clone https://github.com/Dkrynen/lac && cd lac
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt  # or bin/ on POSIX
.venv/Scripts/python server.py        # Flask + web UI on :5050
cd web && npm ci && npm run dev       # Vite dev server (proxies /api)
.venv/Scripts/python -m pytest -q    # test suite
.venv/Scripts/python scripts/installed_launch_smoke.py  # clean installed-exe launch/audit/shutdown proof
.venv/Scripts/python scripts/public_readiness_gate.py --include-live-import --include-launch-smoke --allow-existing-launch  # full gate against an already-running app
.venv/Scripts/python scripts/release_readiness.py  # read-only local/public release check
.venv/Scripts/python scripts/pro_commerce_readiness.py  # read-only Pro checkout/delivery readiness check
.venv/Scripts/python scripts/installed_app_audit.py  # installed app page/API audit
.venv/Scripts/python scripts/installed_launch_smoke.py --allow-existing  # audit an already-running installed app
.venv/Scripts/python scripts/runtime_smoke.py --model qwen2.5:0.5b  # live installed-app chat/session smoke test
.venv/Scripts/python scripts/live_import_stress.py --preflight-only  # cheap HF/Pro resolver + disk preflight smoke
.venv/Scripts/python scripts/live_import_stress.py  # live HF import + disposable delete stress test
```

Plugins mount via the `lac.plugins` entry-point group — see [docs/PLUGINS.md](docs/PLUGINS.md). Contributions welcome: [CONTRIBUTING.md](CONTRIBUTING.md).

Upstream runtime and research provenance is tracked in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the machine-readable
[`upstream-components.json`](docs/third-party/upstream-components.json).

## System requirements

- **OS**: Windows 10+, macOS 13+, Linux (x86_64)
- **Python**: 3.10+ (CLI/source installs)
- **Ollama**: required for model install, chat, benchmarking, and the coding agent
- **OpenCode**: exactly `1.18.7` for the current verified agent adapter
- **GPU**: optional — CPU-only and Apple Silicon fully supported

## License

Core: MIT — see [LICENSE](LICENSE). LAC Pro is a commercial add-on.
