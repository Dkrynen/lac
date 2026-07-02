"""Generate comprehensive model database with calculated VRAM requirements."""
import json
import math
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

def calc_vram(params_b, quant_bpp, context, kv_params=None):
    # Catalog VRAM fields represent the model FILE size (weights + small compute
    # overhead), NOT weights + full-context KV. KV scales with the context the
    # user actually runs, and the recommendation engine (_estimate_vram in
    # recommend.py) adds it per chosen context. File-size here is what the Browse
    # fit verdict and "does it fit in VRAM" checks actually need.
    # MoE note: ALL experts must reside in memory, so weights use TOTAL params.
    weights = params_b * quant_bpp
    overhead = 0.3
    return round(weights + overhead, 1)

MODELS = [
    # === Qwen3 (dense: 0.6/1.7/4/8/14/32; MoE: 30B-A3B, 235B-A22B) ===
    {"id": "qwen3:0.6b", "name": "Qwen3 0.6B", "provider": "Alibaba", "params_b": 0.6, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "qwen3:1.7b", "name": "Qwen3 1.7B", "provider": "Alibaba", "params_b": 1.7, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "qwen3:4b", "name": "Qwen3 4B", "provider": "Alibaba", "params_b": 4.0, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "qwen3:8b", "name": "Qwen3 8B", "provider": "Alibaba", "params_b": 8.2, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "qwen3:14b", "name": "Qwen3 14B", "provider": "Alibaba", "params_b": 14.8, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "qwen3:32b", "name": "Qwen3 32B", "provider": "Alibaba", "params_b": 32.8, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "qwen3:30b-a3b", "name": "Qwen3 30B A3B", "provider": "Alibaba", "params_b": 30.0, "arch": "qwen3", "context": 131072, "use_cases": ["general","chat","coding","reasoning"], "is_moe": True, "active_params_b": 3.0},
    {"id": "qwen3:235b", "name": "Qwen3 235B A22B", "provider": "Alibaba", "params_b": 235.0, "arch": "qwen3", "context": 262144, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 22.0},

    # === Qwen3.5 (successor; dense 0.8/2/4/9/27; MoE 35B/122B — active params estimated) ===
    {"id": "qwen3.5:0.8b", "name": "Qwen3.5 0.8B", "provider": "Alibaba", "params_b": 0.8, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "qwen3.5:2b", "name": "Qwen3.5 2B", "provider": "Alibaba", "params_b": 2.0, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "qwen3.5:4b", "name": "Qwen3.5 4B", "provider": "Alibaba", "params_b": 4.0, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "qwen3.5:9b", "name": "Qwen3.5 9B", "provider": "Alibaba", "params_b": 9.0, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "qwen3.5:27b", "name": "Qwen3.5 27B", "provider": "Alibaba", "params_b": 27.0, "arch": "qwen3", "context": 40960, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "qwen3.5:35b", "name": "Qwen3.5 35B A4B", "provider": "Alibaba", "params_b": 35.0, "arch": "qwen3", "context": 131072, "use_cases": ["general","chat","coding","reasoning"], "is_moe": True, "active_params_b": 4.0},
    {"id": "qwen3.5:122b", "name": "Qwen3.5 122B A8B", "provider": "Alibaba", "params_b": 122.0, "arch": "qwen3", "context": 262144, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 8.0},

    # === Qwen3 Coder (30b MoE, 480b MoE — 480b omitted: unusable on consumer HW) ===
    {"id": "qwen3-coder:30b", "name": "Qwen3 Coder 30B", "provider": "Alibaba", "params_b": 30.0, "arch": "qwen3", "context": 131072, "use_cases": ["coding","reasoning"], "is_moe": True, "active_params_b": 3.0},

    # === Qwen2.5 (non-coder dense) ===
    {"id": "qwen2.5:0.5b", "name": "Qwen2.5 0.5B", "provider": "Alibaba", "params_b": 0.5, "arch": "qwen", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "qwen2.5:1.5b", "name": "Qwen2.5 1.5B", "provider": "Alibaba", "params_b": 1.5, "arch": "qwen", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "qwen2.5:3b", "name": "Qwen2.5 3B", "provider": "Alibaba", "params_b": 3.0, "arch": "qwen", "context": 32768, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "qwen2.5:7b", "name": "Qwen2.5 7B", "provider": "Alibaba", "params_b": 7.6, "arch": "qwen", "context": 131072, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "qwen2.5:14b", "name": "Qwen2.5 14B", "provider": "Alibaba", "params_b": 14.8, "arch": "qwen", "context": 131072, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "qwen2.5:32b", "name": "Qwen2.5 32B", "provider": "Alibaba", "params_b": 32.8, "arch": "qwen", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": False},
    {"id": "qwen2.5:72b", "name": "Qwen2.5 72B", "provider": "Alibaba", "params_b": 72.8, "arch": "qwen", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": False},

    # === Qwen2.5 Coder ===
    {"id": "qwen2.5-coder:1.5b", "name": "Qwen2.5 Coder 1.5B", "provider": "Alibaba", "params_b": 1.5, "arch": "qwen", "context": 32768, "use_cases": ["coding"], "is_moe": False},
    {"id": "qwen2.5-coder:7b", "name": "Qwen2.5 Coder 7B", "provider": "Alibaba", "params_b": 7.6, "arch": "qwen", "context": 131072, "use_cases": ["coding"], "is_moe": False},
    {"id": "qwen2.5-coder:14b", "name": "Qwen2.5 Coder 14B", "provider": "Alibaba", "params_b": 14.8, "arch": "qwen", "context": 131072, "use_cases": ["coding"], "is_moe": False},
    {"id": "qwen2.5-coder:32b", "name": "Qwen2.5 Coder 32B", "provider": "Alibaba", "params_b": 32.8, "arch": "qwen", "context": 131072, "use_cases": ["coding"], "is_moe": False},

    # === Llama 3.2 (1b, 3b only — 11b vision is a separate family) ===
    {"id": "llama3.2:1b", "name": "Llama 3.2 1B", "provider": "Meta", "params_b": 1.2, "arch": "llama", "context": 128000, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "llama3.2:3b", "name": "Llama 3.2 3B", "provider": "Meta", "params_b": 3.2, "arch": "llama", "context": 128000, "use_cases": ["general","chat"], "is_moe": False},

    # === Llama 3.1 ===
    {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "provider": "Meta", "params_b": 8.0, "arch": "llama", "context": 128000, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "llama3.1:70b", "name": "Llama 3.1 70B", "provider": "Meta", "params_b": 70.0, "arch": "llama", "context": 128000, "use_cases": ["general","reasoning","coding","chat"], "is_moe": False},
    {"id": "llama3.1:405b", "name": "Llama 3.1 405B", "provider": "Meta", "params_b": 405.0, "arch": "llama", "context": 128000, "use_cases": ["general","reasoning","coding"], "is_moe": False},

    # === Llama 3.3 ===
    {"id": "llama3.3:70b", "name": "Llama 3.3 70B", "provider": "Meta", "params_b": 70.0, "arch": "llama", "context": 131072, "use_cases": ["general","reasoning","coding","chat"], "is_moe": False},

    # === Llama 4 (Scout 16x17b=109B/17B active; Maverick 128x17b=402B/17B active) ===
    {"id": "llama4:scout", "name": "Llama 4 Scout", "provider": "Meta", "params_b": 109.0, "arch": "llama", "context": 1048576, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 17.0},
    {"id": "llama4:maverick", "name": "Llama 4 Maverick", "provider": "Meta", "params_b": 402.0, "arch": "llama", "context": 1048576, "use_cases": ["general","reasoning","coding"], "is_moe": True, "active_params_b": 17.0},

    # === Mistral ===
    {"id": "mistral:7b", "name": "Mistral 7B", "provider": "Mistral", "params_b": 7.3, "arch": "mistral", "context": 32768, "use_cases": ["general","coding","reasoning"], "is_moe": False},
    {"id": "mistral-nemo:12b", "name": "Mistral Nemo 12B", "provider": "Mistral", "params_b": 12.2, "arch": "mistral", "context": 131072, "use_cases": ["general","coding","reasoning","chat"], "is_moe": False},

    # === Mixtral (MoE) ===
    {"id": "mixtral:8x7b", "name": "Mixtral 8x7B", "provider": "Mistral", "params_b": 46.7, "arch": "mistral", "context": 32768, "use_cases": ["general","reasoning","coding"], "is_moe": True, "active_params_b": 12.9},
    {"id": "mixtral:8x22b", "name": "Mixtral 8x22B", "provider": "Mistral", "params_b": 141.0, "arch": "mistral", "context": 65536, "use_cases": ["general","reasoning","coding"], "is_moe": True, "active_params_b": 39.0},

    # === Codestral ===
    {"id": "codestral:22b", "name": "Codestral 22B", "provider": "Mistral", "params_b": 22.0, "arch": "mistral", "context": 32768, "use_cases": ["coding"], "is_moe": False},

    # === Mistral Small 3.2 (dense 24B) ===
    {"id": "mistral-small3.2:24b", "name": "Mistral Small 3.2 24B", "provider": "Mistral", "params_b": 24.0, "arch": "mistral", "context": 131072, "use_cases": ["general","coding","reasoning","chat"], "is_moe": False},

    # === DeepSeek R1 (distills: 1.5b=Qwen2.5-1.5B, 7b=Qwen2.5-7B, 8b=Llama-3.1-8B, 14b=Qwen2.5-14B, 32b=Qwen2.5-32B, 70b=Llama-3.3-70B; 671b MoE) ===
    {"id": "deepseek-r1:1.5b", "name": "DeepSeek R1 1.5B", "provider": "DeepSeek", "params_b": 1.5, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning"], "is_moe": False},
    {"id": "deepseek-r1:7b", "name": "DeepSeek R1 7B", "provider": "DeepSeek", "params_b": 7.6, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False},
    {"id": "deepseek-r1:8b", "name": "DeepSeek R1 8B", "provider": "DeepSeek", "params_b": 8.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False},
    {"id": "deepseek-r1:14b", "name": "DeepSeek R1 14B", "provider": "DeepSeek", "params_b": 14.8, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False},
    {"id": "deepseek-r1:32b", "name": "DeepSeek R1 32B", "provider": "DeepSeek", "params_b": 32.8, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False},
    {"id": "deepseek-r1:70b", "name": "DeepSeek R1 70B", "provider": "DeepSeek", "params_b": 70.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": False},
    {"id": "deepseek-r1:671b", "name": "DeepSeek R1 671B", "provider": "DeepSeek", "params_b": 671.0, "arch": "deepseek", "context": 131072, "use_cases": ["reasoning","coding"], "is_moe": True, "active_params_b": 37.0},

    # === DeepSeek V3 (671B MoE, 37B active) ===
    {"id": "deepseek-v3:671b", "name": "DeepSeek V3 671B", "provider": "DeepSeek", "params_b": 671.0, "arch": "deepseek", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 37.0},

    # === DeepSeek V4 (speculative families on Ollama; active params estimated) ===
    {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "provider": "DeepSeek", "params_b": 67.0, "arch": "deepseek", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 8.0},
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "provider": "DeepSeek", "params_b": 236.0, "arch": "deepseek", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 20.0},

    # === DeepSeek Coder V2 Lite (16B MoE, 2.4B active) ===
    {"id": "deepseek-coder-v2:16b", "name": "DeepSeek Coder V2 Lite", "provider": "DeepSeek", "params_b": 16.0, "arch": "deepseek", "context": 131072, "use_cases": ["coding","reasoning"], "is_moe": True, "active_params_b": 2.4},

    # === Phi-3 ===
    {"id": "phi3:mini", "name": "Phi-3 Mini", "provider": "Microsoft", "params_b": 3.8, "arch": "phi3", "context": 128000, "use_cases": ["general","reasoning"], "is_moe": False},
    {"id": "phi3:medium", "name": "Phi-3 Medium", "provider": "Microsoft", "params_b": 14.0, "arch": "phi3", "context": 128000, "use_cases": ["general","reasoning","coding"], "is_moe": False},

    # === Phi-4 ===
    {"id": "phi4:14b", "name": "Phi-4 14B", "provider": "Microsoft", "params_b": 14.7, "arch": "phi4", "context": 131072, "use_cases": ["reasoning","coding","general"], "is_moe": False},
    {"id": "phi4-mini:3.8b", "name": "Phi-4 Mini", "provider": "Microsoft", "params_b": 3.8, "arch": "phi4", "context": 131072, "use_cases": ["general","reasoning","coding","chat"], "is_moe": False},

    # === Gemma 3 (real sizes: 1b/4b/12b/27b) ===
    {"id": "gemma3:1b", "name": "Gemma 3 1B", "provider": "Google", "params_b": 1.0, "arch": "gemma", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "gemma3:4b", "name": "Gemma 3 4B", "provider": "Google", "params_b": 4.0, "arch": "gemma", "context": 32768, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "gemma3:12b", "name": "Gemma 3 12B", "provider": "Google", "params_b": 12.0, "arch": "gemma", "context": 32768, "use_cases": ["general","coding","reasoning"], "is_moe": False},
    {"id": "gemma3:27b", "name": "Gemma 3 27B", "provider": "Google", "params_b": 27.0, "arch": "gemma", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": False},

    # === Gemma 4 (e2b/e4b/12b/26b/31b — dense; internals estimated) ===
    {"id": "gemma4:2b", "name": "Gemma 4 2B", "provider": "Google", "params_b": 2.6, "arch": "gemma", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "gemma4:4b", "name": "Gemma 4 4B", "provider": "Google", "params_b": 4.0, "arch": "gemma", "context": 32768, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "gemma4:12b", "name": "Gemma 4 12B", "provider": "Google", "params_b": 12.0, "arch": "gemma", "context": 32768, "use_cases": ["general","coding","reasoning","chat"], "is_moe": False},
    {"id": "gemma4:26b", "name": "Gemma 4 26B", "provider": "Google", "params_b": 26.0, "arch": "gemma", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": False},
    {"id": "gemma4:31b", "name": "Gemma 4 31B", "provider": "Google", "params_b": 31.0, "arch": "gemma", "context": 131072, "use_cases": ["general","reasoning","coding"], "is_moe": False},

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

    # === Nemotron (real families: nemotron=70b, nemotron-mini=4b) ===
    {"id": "nemotron:70b", "name": "Nemotron 70B", "provider": "NVIDIA", "params_b": 70.0, "arch": "nemotron", "context": 131072, "use_cases": ["general","reasoning","coding","chat"], "is_moe": False},
    {"id": "nemotron-mini:4b", "name": "Nemotron Mini 4B", "provider": "NVIDIA", "params_b": 4.0, "arch": "nemotron", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},

    # === StarCoder2 ===
    {"id": "starcoder2:3b", "name": "StarCoder2 3B", "provider": "Hugging Face", "params_b": 3.0, "arch": "starcoder", "context": 16384, "use_cases": ["coding"], "is_moe": False},
    {"id": "starcoder2:7b", "name": "StarCoder2 7B", "provider": "Hugging Face", "params_b": 7.0, "arch": "starcoder", "context": 16384, "use_cases": ["coding"], "is_moe": False},
    {"id": "starcoder2:15b", "name": "StarCoder2 15B", "provider": "Hugging Face", "params_b": 15.0, "arch": "starcoder", "context": 16384, "use_cases": ["coding"], "is_moe": False},

    # === DBRX (132B MoE, 36B active) ===
    {"id": "dbrx:132b", "name": "DBRX 132B", "provider": "Databricks", "params_b": 132.0, "arch": "dbrx", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 36.0},

    # === GPT-OSS (OpenAI MoE: 20B/3.6B active fits 16GB at Q4; 120B/5.1B active) ===
    {"id": "gpt-oss:20b", "name": "GPT-OSS 20B", "provider": "OpenAI", "params_b": 20.0, "arch": "gpt-oss", "context": 131072, "use_cases": ["general","chat","coding","reasoning"], "is_moe": True, "active_params_b": 3.6},
    {"id": "gpt-oss:120b", "name": "GPT-OSS 120B", "provider": "OpenAI", "params_b": 120.0, "arch": "gpt-oss", "context": 131072, "use_cases": ["general","coding","reasoning"], "is_moe": True, "active_params_b": 5.1},

    # === SmolLM2 (efficient small models) ===
    {"id": "smollm2:360m", "name": "SmolLM2 360M", "provider": "Hugging Face", "params_b": 0.36, "arch": "smollm", "context": 8192, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "smollm2:1.7b", "name": "SmolLM2 1.7B", "provider": "Hugging Face", "params_b": 1.7, "arch": "smollm", "context": 8192, "use_cases": ["general","chat","coding"], "is_moe": False},

    # === Falcon 3 ===
    {"id": "falcon3:1b", "name": "Falcon 3 1B", "provider": "TII", "params_b": 1.0, "arch": "falcon", "context": 32768, "use_cases": ["general","chat"], "is_moe": False},
    {"id": "falcon3:3b", "name": "Falcon 3 3B", "provider": "TII", "params_b": 3.0, "arch": "falcon", "context": 32768, "use_cases": ["general","chat","coding"], "is_moe": False},
    {"id": "falcon3:7b", "name": "Falcon 3 7B", "provider": "TII", "params_b": 7.0, "arch": "falcon", "context": 32768, "use_cases": ["general","chat","coding","reasoning"], "is_moe": False},
    {"id": "falcon3:10b", "name": "Falcon 3 10B", "provider": "TII", "params_b": 10.0, "arch": "falcon", "context": 32768, "use_cases": ["general","coding","reasoning"], "is_moe": False},

    # === Sub-4-bit / 1.58-bit (BitNet family) — pulled via hf.co GGUF ===
    {"id": "hf.co/tiiuae/Falcon3-3B-Instruct-1.58bit", "name": "Falcon 3 3B 1.58-bit", "provider": "TII", "params_b": 3.0, "arch": "falcon", "context": 32768, "use_cases": ["general","chat","coding"], "is_moe": False, "sub4bit": True},
    {"id": "hf.co/microsoft/BitNet-b1.58-2B-4T", "name": "BitNet b1.58 2B", "provider": "Microsoft", "params_b": 2.0, "arch": "bitnet", "context": 4096, "use_cases": ["general","chat"], "is_moe": False, "sub4bit": True},

    # === OLMoE (7B total MoE, 1B active) — pulled via hf.co GGUF ===
    {"id": "hf.co/allenai/OLMoE-1B-7B-0924-Instruct", "name": "OLMoE 1B-7B", "provider": "AI2", "params_b": 7.0, "arch": "olmoe", "context": 4096, "use_cases": ["general","chat","coding"], "is_moe": True, "active_params_b": 1.0},

    # === Big Pickle (special) ===
    {"id": "big-pickle:latest", "name": "Big Pickle", "provider": "OpenCode", "params_b": 7.0, "arch": "llama", "context": 32768, "use_cases": ["general","coding"], "is_moe": False},
]

# Calculate VRAM for each model
QUANTS = [
    ("Q4_K_M", 0.58),
    ("Q8", 1.05),
    ("F16", 2.0),
]

# 1.58-bit (BitNet) models: ~0.2 bytes/param. Stored in vram_q4 as the smallest
# available quant, since these ship at 1.58-bit rather than Q4.
SUB4BIT_BPP = 0.2

QUANT_FIELDS = {"Q4_K_M": "vram_q4", "Q8": "vram_q8", "F16": "vram_f16"}
for m in MODELS:
    for qname, bpp in QUANTS:
        if m.get("sub4bit") and qname == "Q4_K_M":
            m[QUANT_FIELDS[qname]] = calc_vram(m["params_b"], SUB4BIT_BPP, m["context"])
        else:
            m[QUANT_FIELDS[qname]] = calc_vram(m["params_b"], bpp, m["context"])

# Remove active_params_b if not MoE; sub4bit flag is kept (consumed by recommend.py)
for m in MODELS:
    if not m["is_moe"]:
        m.pop("active_params_b", None)

out_path = DATA_DIR / "models.json"
with open(out_path, "w") as f:
    json.dump(MODELS, f, indent=2)

print(f"Generated {len(MODELS)} models -> {out_path}")
