# Model Hub

Hardware scanner + model recommender + installer + chat for local LLMs.

Scans your GPU, VRAM, RAM, and CPU — then recommends the best models that fit your hardware. Installs and runs models via [Ollama](https://ollama.com).

![Dashboard](https://raw.githubusercontent.com/anomalyco/model-hub/main/screenshots/dashboard.png)

## Features

- **Hardware Scan** — detects GPU, VRAM, RAM, CPU on Windows, Linux, and macOS
- **Smart Recommendations** — 65 curated models scored by quality, speed, hardware fit, and context window
- **Model Installer** — downloads models via Ollama with live progress bars
- **Model Management** — list, run, and delete installed models
- **Built-in Chat** — streaming chat interface powered by Ollama
- **No GPU? No problem** — recommendations work for CPU-only and Apple Silicon too

## Quick Start

### Option 1: Download the installer (Windows)

Download the latest `Model-Hub-Setup-x.x.x.exe` from the [Releases page](https://github.com/anomalyco/model-hub/releases).

### Option 2: Run from source

```bash
# 1. Install Ollama (required for model downloads)
#    https://ollama.com/download

# 2. Clone and run
git clone https://github.com/anomalyco/model-hub.git
cd model-hub
pip install flask
python server.py
```

Open http://127.0.0.1:5050 in your browser.

### macOS / Linux

```bash
python3 server.py
```

## Building from Source

### Package as standalone .exe

```bash
pip install pyinstaller
pyinstaller build.spec
```

Distribution will be in `dist/model-hub.exe`.

### Create Windows installer

```bash
# Build .exe first
pyinstaller build.spec

# Then compile with InnoSetup
iscc installer.iss
```

## System Requirements

- **OS**: Windows 10+, macOS 13+, Linux (x86_64)
- **Python**: 3.10+ (when running from source)
- **Ollama**: Required for model installation and chat
- **GPU**: Optional — CPU-only mode works with system RAM

## Hardware Detection

| GPU | Method | Verified |
|-----|--------|----------|
| NVIDIA | `nvidia-smi` | All models |
| AMD (Windows) | `vulkaninfo` → registry → WMI | RX 6800 XT (16 GB) |
| AMD (Linux) | `/sys/class/drm` sysfs | ROCm/Vulkan |
| Apple Silicon | `sysctl` unified memory | M-series |
| Intel | Registry fallback | Arc, UHD, Iris |

## Project Structure

```
model-hub/
├── server.py              # Entry point
├── backend/
│   ├── api.py             # Flask API
│   ├── version.py         # Version info
│   └── cookbook/
│       ├── hardware.py    # Hardware detection
│       ├── recommend.py   # Scoring engine
│       └── data/models.json  # 65 curated models
├── frontend/
│   ├── index.html         # SPA frontend
│   ├── style.css          # Styles
│   └── script.js          # Client logic
├── build.spec             # PyInstaller config
├── installer.iss          # InnoSetup installer config
└── .github/workflows/     # CI/CD pipelines
```

## License

MIT — see [LICENSE](LICENSE).
