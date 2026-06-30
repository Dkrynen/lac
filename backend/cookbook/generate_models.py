"""Generate comprehensive model database with calculated VRAM requirements."""
import json
import math
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

def calc_vram(params_b, quant_bpp, context, kv_params=None):
    active = kv_params if kv_params else params_b
    weights = params_b * quant_bpp
    kv = 0.000008 * active * context
    overhead = 0.5
    return round(weights + kv + overhead, 1)

MODELS = [
    # === Qwen3 Series ===
    {"id": "qwen3:0.6b", "name": "Qwen3 0.6B", "provider": "Alibaba", "params_b": 0.6, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat"], "is_moe": True, "active_params_b": 0.1},
    {"id": "qwen3:1.7b", "name": "Qwen3 1.7B", "provider": "Alibaba", "params_b": 1.7, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat"], "is_moe": True, "active_params_b": 0.4},
    {"id": "qwen3:4b", "name": "Qwen3 4B", "provider": "Alibaba", "params_b": 4.0, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding"], "is_moe": True, "active_params_b": 0.8},
    {"id": "qwen3:7b", "name": "Qwen3 7B", "provider": "Alibaba", "params_b": 7.6, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "qwen3:14b", "name": "Qwen3 14B", "provider": "Alibaba", "params_b": 14.8, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "qwen3:32b", "name": "Qwen3 32B", "provider": "Alibaba", "params_b": 32.0, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "qwen3:72b", "name": "Qwen3 72B", "provider": "Alibaba", "params_b": 72.0, "arch": "qwen3", "context": 32768, "use_cases": ["general","coding","reasoning"], "is_moe": False},
    {"id": "qwen3:235b", "name": "Qwen3 235B", "provider": "Alibaba", "params_b": 235.0, "arch": "qwen3", "context": 262144, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 16.0},
    # === Qwen3 Coder ===
    {"id": "qwen3-coder:7b", "name": "Qwen3 Coder 7B", "provider": "Alibaba", "params_b": 7.6, "arch": "qwen3", "context": 131072, "use_cases": ["coding"], "is_moe": False},
    {"id": "qwen3-coder:14b", "name": "Qwen3 Coder 14B", "provider": "Alibaba", "params_b": 14.8, "arch": "qwen3", "context": 131072, "use_cases": ["coding"], "is_moe": False},
    {"id": "qwen3-coder:30b", "name": "Qwen3 Coder 30B", "provider": "Alibaba", "params_b": 30.0, "arch": "qwen3", "context": 131072, "use_cases": ["coding","reasoning"], "is_moe": True, "active_params_b": 3.0},

    # === Qwen2.5 Coder ===
    {"id": "qwen2.5-coder:1.5b", "name": "Qwen2.5 Coder 1.5B", "provider": "Alibaba", "params_b": 1.5, "arch": "qwen", "context": 32768, "use_cases": ["coding"], "is_moe": False},
    {"id": "qwen2.5-coder:7b", "name": "Qwen2.5 Coder 7B", "provider": "Alibaba", "params_b": 7.6, "arch": "qwen", "context": 131072, "use_cases": ["coding"], "is_moe": False},
    {"id": "qwen2.5-coder:14b", "name": "Qwen2.5 Coder 14B", "provider": "Alibaba", "params_b": 14.8, "arch": "qwen", "context": 131072, "use_cases": ["coding"], "is_moe": False},
    {"id": "qwen2.5-coder:32b", "name": "Qwen2.5 Coder 32B", "provider": "Alibaba", "params_b": 32.0, "arch": "qwen", "context": 131072, "use_cases": ["coding"], "is_moe": False},

    # === Llama 3.2 ===
    {"id": "llama3.2:1b", "name": "Llama 3.2 1B", "provider": "Meta", "params_b": 1.2, "arch": "llama", "context": 128000, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "llama3.2:3b", "name": "Llama 3.2 3B", "provider": "Meta", "params_b": 3.2, "arch": "llama", "context": 128000, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "llama3.2:11b", "name": "Llama 3.2 11B", "provider": "Meta", "params_b": 11.0, "arch": "llama", "context": 128000, "use_cases": ["general","chat","coding"], "is_moe": False},

    # === Llama 3.1 ===
    {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "provider": "Meta", "params_b": 8.0, "arch": "llama", "context": 128000, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "llama3.1:70b", "name": "Llama 3.1 70B", "provider": "Meta", "params_b": 70.0, "arch": "llama", "context": 128000, "use_cases": ["general","reasoning","coding","chat"], "is_moe": False},
    {"id": "llama3.1:405b", "name": "Llama 3.1 405B", "provider": "Meta", "params_b": 405.0, "arch": "llama", "context": 128000, "use_cases": ["general","reasoning","coding"], "is_moe": False},

    # === Llama 3.3 ===
    {"id": "llama3.3:70b", "name": "Llama 3.3 70B", "provider": "Meta", "params_b": 70.0, "arch": "llama", "context": 131072, "use_cases": ["general","reasoning","coding","chat"], "is_moe": False},

    # === Llama 4 ===
    {"id": "llama4:scout", "name": "Llama 4 Scout", "provider": "Meta", "params_b": 109.0, "arch": "llama", "context": 1048576, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 17.0},
    {"id": "llama4:maverick", "name": "Llama 4 Maverick", "provider": "Meta", "params_b": 402.0, "arch": "llama", "context": 1048576, "use_cases": ["general","reasoning","coding"], "is_moe": True, "active_params_b": 50.0},

    # === Mistral ===
    {"id": "mistral:7b", "name": "Mistral 7B", "provider": "Mistral", "params_b": 7.3, "arch": "mistral", "context": 32768, "use_cases": ["general","coding","reasoning"], "is_moe": False},
    {"id": "mistral:8x7b", "name": "Mixtral 8x7B", "provider": "Mistral", "params_b": 46.7, "arch": "mistral", "context": 32768, "use_cases": ["general","reasoning","coding"], "is_moe": True, "active_params_b": 12.9},
    {"id": "mistral:12b", "name": "Mistral 12B", "provider": "Mistral", "params_b": 12.2, "arch": "mistral", "context": 32768, "use_cases": ["general","coding","reasoning","chat"], "is_moe": False},

    # === Codestral ===
    {"id": "codestral:22b", "name": "Codestral 22B", "provider": "Mistral", "params_b": 22.0, "arch": "mistral", "context": 32768, "use_cases": ["coding"], "is_moe": False},

    # === DeepSeek ===
    {"id": "deepseek-coder-v2:16b", "name": "DeepSeek Coder V2 Lite", "provider": "DeepSeek", "params_b": 16.0, "arch": "deepseek", "context": 131072, "use_cases": ["coding","reasoning"], "is_moe": False},
    {"id": "deepseek-r1:1.5b", "name": "DeepSeek R1 1.5B", "provider": "DeepSeek", "params_b": 1.5, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning"], "is_moe": False},
    {"id": "deepseek-r1:7b", "name": "DeepSeek R1 7B", "provider": "DeepSeek", "params_b": 7.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False},
    {"id": "deepseek-r1:14b", "name": "DeepSeek R1 14B", "provider": "DeepSeek", "params_b": 14.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False},
    {"id": "deepseek-r1:32b", "name": "DeepSeek R1 32B", "provider": "DeepSeek", "params_b": 32.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False},
    {"id": "deepseek-r1:70b", "name": "DeepSeek R1 70B", "provider": "DeepSeek", "params_b": 70.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False},
    {"id": "deepseek-r1:671b", "name": "DeepSeek R1 671B", "provider": "DeepSeek", "params_b": 671.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": True, "active_params_b": 37.0},
    {"id": "deepseek-v4:flash", "name": "DeepSeek V4 Flash", "provider": "DeepSeek", "params_b": 67.0, "arch": "deepseek", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 8.0},
    {"id": "deepseek-v4:pro", "name": "DeepSeek V4 Pro", "provider": "DeepSeek", "params_b": 236.0, "arch": "deepseek", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 20.0},

    # === Phi-3 ===
    {"id": "phi3:mini", "name": "Phi-3 Mini", "provider": "Microsoft", "params_b": 3.8, "arch": "phi3", "context": 128000, "use_cases": ["general","reasoning"], "is_moe": False},
    {"id": "phi3:medium", "name": "Phi-3 Medium", "provider": "Microsoft", "params_b": 14.0, "arch": "phi3", "context": 128000, "use_cases": ["general","reasoning","coding"], "is_moe": False},

    # === Phi-4 ===
    {"id": "phi4:14b", "name": "Phi-4 14B", "provider": "Microsoft", "params_b": 14.7, "arch": "phi4", "context": 131072, "use_cases": ["reasoning","coding","general"], "is_moe": False},

    # === Gemma 3 ===
    {"id": "gemma3:2b", "name": "Gemma 3 2B", "provider": "Google", "params_b": 2.1, "arch": "gemma", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "gemma3:7b", "name": "Gemma 3 7B", "provider": "Google", "params_b": 7.2, "arch": "gemma", "context": 32768, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "gemma3:12b", "name": "Gemma 3 12B", "provider": "Google", "params_b": 12.0, "arch": "gemma", "context": 32768, "use_cases": ["general","coding","reasoning"], "is_moe": False},

    # === Gemma 4 ===
    {"id": "gemma4:2b", "name": "Gemma 4 2B", "provider": "Google", "params_b": 2.5, "arch": "gemma", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "gemma4:4b", "name": "Gemma 4 4B", "provider": "Google", "params_b": 4.0, "arch": "gemma", "context": 32768, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "gemma4:7b", "name": "Gemma 4 7B", "provider": "Google", "params_b": 7.8, "arch": "gemma", "context": 32768, "use_cases": ["general","coding","reasoning"], "is_moe": False},
    {"id": "gemma4:12b", "name": "Gemma 4 12B", "provider": "Google", "params_b": 12.0, "arch": "gemma", "context": 32768, "use_cases": ["general","coding","reasoning","chat"], "is_moe": False},
    {"id": "gemma4:24b", "name": "Gemma 4 24B", "provider": "Google", "params_b": 24.0, "arch": "gemma", "context": 262144, "use_cases": ["general","reasoning"], "is_moe": False},
    {"id": "gemma4:26b", "name": "Gemma 4 26B", "provider": "Google", "params_b": 26.0, "arch": "gemma", "context": 262144, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 4.0},
    {"id": "gemma4:31b", "name": "Gemma 4 31B", "provider": "Google", "params_b": 31.0, "arch": "gemma", "context": 262144, "use_cases": ["general","reasoning","coding"], "is_moe": False},

    # === Mellum 2 ===
    {"id": "mellum2:12b", "name": "Mellum 2 12B", "provider": "JetBrains", "params_b": 12.0, "arch": "mellum", "context": 128000, "use_cases": ["coding"], "is_moe": True, "active_params_b": 2.5},

    # === CodeGemma ===
    {"id": "codegemma:2b", "name": "CodeGemma 2B", "provider": "Google", "params_b": 2.0, "arch": "gemma", "context": 16384, "use_cases": ["coding"], "is_moe": False},
    {"id": "codegemma:7b", "name": "CodeGemma 7B", "provider": "Google", "params_b": 7.0, "arch": "gemma", "context": 16384, "use_cases": ["coding"], "is_moe": False},
    {"id": "codegemma:12b", "name": "CodeGemma 12B", "provider": "Google", "params_b": 12.0, "arch": "gemma", "context": 16384, "use_cases": ["coding"], "is_moe": False},

    # === Command R ===
    {"id": "command-r:35b", "name": "Command R 35B", "provider": "Cohere", "params_b": 35.0, "arch": "cohere", "context": 128000, "use_cases": ["general","reasoning","chat"], "is_moe": False},
    {"id": "command-r-plus:104b", "name": "Command R+ 104B", "provider": "Cohere", "params_b": 104.0, "arch": "cohere", "context": 128000, "use_cases": ["general","reasoning","coding"], "is_moe": False},

    # === Yi ===
    {"id": "yi:6b", "name": "Yi 6B", "provider": "01.AI", "params_b": 6.0, "arch": "yi", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "yi:9b", "name": "Yi 9B", "provider": "01.AI", "params_b": 9.0, "arch": "yi", "context": 32768, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "yi:34b", "name": "Yi 34B", "provider": "01.AI", "params_b": 34.0, "arch": "yi", "context": 32768, "use_cases": ["general","reasoning","coding"], "is_moe": False},

    # === Nemotron ===
    {"id": "nemotron:4b", "name": "Nemotron 4B", "provider": "NVIDIA", "params_b": 4.0, "arch": "nemotron", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "nemotron:12b", "name": "Nemotron 12B", "provider": "NVIDIA", "params_b": 12.0, "arch": "nemotron", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": False},

    # === StarCoder2 ===
    {"id": "starcoder2:3b", "name": "StarCoder2 3B", "provider": "Hugging Face", "params_b": 3.0, "arch": "starcoder", "context": 16384, "use_cases": ["coding"], "is_moe": False},
    {"id": "starcoder2:7b", "name": "StarCoder2 7B", "provider": "Hugging Face", "params_b": 7.0, "arch": "starcoder", "context": 16384, "use_cases": ["coding"], "is_moe": False},
    {"id": "starcoder2:15b", "name": "StarCoder2 15B", "provider": "Hugging Face", "params_b": 15.0, "arch": "starcoder", "context": 16384, "use_cases": ["coding"], "is_moe": False},

    # === Big Pickle (special) ===
    {"id": "big-pickle:latest", "name": "Big Pickle", "provider": "OpenCode", "params_b": 7.0, "arch": "llama", "context": 32768, "use_cases": ["general","coding"], "is_moe": False},
]

# Calculate VRAM for each model
QUANTS = [
    ("Q4_K_M", 0.58),
    ("Q8", 1.05),
    ("F16", 2.0),
]

QUANT_FIELDS = {"Q4_K_M": "vram_q4", "Q8": "vram_q8", "F16": "vram_f16"}
for m in MODELS:
    kv_params = m.get("active_params_b") or m["params_b"]
    for qname, bpp in QUANTS:
        vram = calc_vram(m["params_b"], bpp, m["context"], kv_params if m["is_moe"] else None)
        m[QUANT_FIELDS[qname]] = vram

# Remove active_params_b if not MoE
for m in MODELS:
    if not m["is_moe"]:
        m.pop("active_params_b", None)

out_path = DATA_DIR / "models.json"
with open(out_path, "w") as f:
    json.dump(MODELS, f, indent=2)

print(f"Generated {len(MODELS)} models -> {out_path}")
