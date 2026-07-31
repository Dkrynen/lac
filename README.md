<img src="assets/leaf-mark.svg" width="72" alt="LAC vein-leaf mark" />

# LAC — local AI, sorted.

![tests](https://github.com/Dkrynen/lac/actions/workflows/test.yml/badge.svg)
![opencode compat](https://github.com/Dkrynen/lac/actions/workflows/opencode-compat.yml/badge.svg)

<!-- HERO: Replace with a screenshot of `lac agent .` launching — terminal
     showing the banner ("LAC picked + prepared …"), the model name, num_ctx,
     and OpenCode opening. Save as assets/hero-agent.png (1280×720). -->
<img src="assets/hero-agent.png" width="100%" alt="LAC agent launching OpenCode on a local model" />

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
your exact hardware, raises its context window to the agent-loop floor (65k),
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

## Evidence, not vibes

The 94.67 tok/s claim above comes from a bounded eval: three trials, three
arms (raw Ollama, stock OpenCode, LAC-tuned variant), scored by exact text
match on a multi-step code-reasoning task. The model was never the problem —
nine bugs in the eval harness were found and fixed before the model could
show what it could do.

`lac eval` is the internal tool that produces that evidence. It is
Windows-only, requires elevated PowerShell for network containment, and is
not a public benchmark. Diagnostic runs are explicitly non-evidence. See
[GETTING_STARTED.md](docs/GETTING_STARTED.md) for the preflight command.

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

> **Note:** The terminal agent (`lac agent .`) ships in v2.7.0, which is not
> yet published on the Releases page. The current published release (v2.6.4)
> includes `lac doctor` and `lac inspect`.

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

**Pro Cloud** (planned, **$20/month**) adds end-to-end encrypted sync and capped hosted agents. Not yet available.

**At launch,** every paid buyer signs in with Google or GitHub, checks out from that account, and activates with `lac unlock <key>` or **Settings → Activate Pro**. Free installs ship no Pro code.

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
.venv/Scripts/python -m pytest -q    # test suite
```

Full dev setup, readiness scripts, and audit tools: [CONTRIBUTING.md](CONTRIBUTING.md).
Plugins mount via the `lac.plugins` entry-point group: [docs/PLUGINS.md](docs/PLUGINS.md).

Upstream runtime and research provenance is tracked in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the machine-readable
[`upstream-components.json`](docs/third-party/upstream-components.json).

## System requirements

- **OS**: Windows 10+, macOS 13+, Linux (x86_64)
- **Python**: 3.10+ (CLI/source installs)
- **Ollama**: required for model install, chat, benchmarking, and the coding agent
- **OpenCode**: exactly `1.18.9` for the current verified agent adapter
- **GPU**: optional — CPU-only and Apple Silicon fully supported

## License

Core: MIT — see [LICENSE](LICENSE). LAC Pro is a commercial add-on.
