# lac-opencode

LAC tools for [OpenCode](https://opencode.ai): hardware scan, fit-aware local
model recommendations, and the LAC Pro tuning cockpit — callable by the agent
during a session.

LAC is the hardware brain for local AI agents: it scans your machine, picks
the agent-capable model that actually fits, and prepares it for OpenCode.
This plugin exposes that brain as agent tools.

## Tools

| Tool | Tier | What it does |
|---|---|---|
| `lac_scan` | free | Hardware report: GPU, VRAM, RAM, CPU |
| `lac_recommend` | free | Best agent-capable local model for this machine |
| `lac_status` | Pro | License + tuning-cockpit status |
| `lac_tune` | Pro | Sweep GPU-offload configs, apply the fastest |
| `lac_benchmark` | Pro | Measure a model's tok/s on this rig |
| `lac_insights` | Pro | Calibration history + regression detection |
| `lac_import` | Pro | Import a compatible model from Hugging Face |

Pro tools require the LAC Pro plugin on the machine; without a license they
report that honestly instead of failing opaquely.

## Install

Requires the `lac` CLI on PATH (install LAC from
[Releases](https://github.com/Dkrynen/lac/releases) or `pipx install
git+https://github.com/Dkrynen/lac`).

In your OpenCode config:

```json
{
  "plugin": ["lac-opencode"]
}
```

If `lac` is not on PATH, point the plugin at it explicitly:

```
LAC_CLI="C:\Program Files\LAC\lac.exe"
```

> **Note:** `lac setup` and `lac agent` write a path-resolved copy of this
> plugin (plus LAC's provider config, permissions, slash commands, and agent
> profiles) directly into your OpenCode config. Use this package when you
> want the tools without running LAC's provisioning, or in shared configs.

## Status

Scaffolded; not yet published to npm. Track
[Releases](https://github.com/Dkrynen/lac/releases) for the publish milestone.

## License

MIT — see the [LAC repository](https://github.com/Dkrynen/lac).
