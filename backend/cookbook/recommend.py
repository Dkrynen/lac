import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .hardware import GPUInfo, SystemInfo


@dataclass
class ModelEntry:
    id: str
    name: str
    provider: str
    params_b: float
    arch: str
    context: int
    use_cases: list[str]
    is_moe: bool
    vram_q4: float = 0
    vram_q8: float = 0
    vram_f16: float = 0
    active_params_b: Optional[float] = None


@dataclass
class Recommendation:
    model: ModelEntry
    quant: str
    vram_gb: float
    score: float
    quality_score: float
    speed_score: float
    fit_score: float
    context_score: float
    context_used: int
    run_mode: str
    ollama_cmd: str
    details: dict = field(default_factory=dict)


@dataclass
class QuantInfo:
    name: str
    bpp: float
    quality_penalty: float
    speed_mult: float
    sort_order: int


QUANTS = [
    QuantInfo("F16", 2.0, 0.0, 0.6, 0),
    QuantInfo("Q8", 1.05, 0.0, 0.8, 1),
    QuantInfo("Q6_K", 0.80, -2.0, 0.9, 2),
    QuantInfo("Q5_K_M", 0.68, -3.0, 1.0, 3),
    QuantInfo("Q4_K_M", 0.58, -5.0, 1.15, 4),
    QuantInfo("Q3_K_M", 0.48, -8.0, 1.25, 5),
    QuantInfo("Q2_K", 0.37, -12.0, 1.35, 6),
]

ARCH_SPEED_BONUS = {
    "qwen3": 1.05, "qwen": 1.0, "llama": 1.0, "mistral": 1.02,
    "gemma": 1.03, "phi3": 0.95, "phi4": 0.95, "deepseek": 0.90,
    "mellum": 1.05, "cohere": 0.85, "yi": 1.0, "nemotron": 1.0,
    "starcoder": 1.0,
}

GPU_BANDWIDTH = {
    "5090": 1792, "5080": 960, "5070 ti": 896, "5070": 640,
    "5060 ti": 507, "5060": 355,
    "4090": 1008, "4080 super": 736, "4080": 717,
    "4070 ti super": 672, "4070 ti": 672, "4070 super": 504,
    "4070": 504, "4060 ti": 288, "4060": 272,
    "3090 ti": 1008, "3090": 936, "3080 ti": 912,
    "3080": 760, "3070 ti": 608, "3070": 448,
    "3060 ti": 448, "3060": 360,
    "a100": 2039, "a100 80gb": 2039, "a6000": 768,
    "h100": 3350, "h200": 4800, "b200": 4500,
    "7900 xtx": 960, "7900 xt": 800, "7800 xt": 624,
    "7700 xt": 432, "7600 xt": 384,
    "9070 xt": 624, "9070": 488, "9060 xt": 400,
    "mi300x": 5300, "mi250": 3277, "mi210": 1638,
    "6800 xt": 512, "6800": 512, "6700 xt": 384,
    "6600 xt": 256, "6600": 224,
}

MODEL_FAMILY_QUALITY_BONUS = {
    "deepseek": 3, "qwen3": 2, "qwen": 1, "llama": 2,
    "mistral": 1, "gemma": 1, "phi3": 1, "phi4": 2,
    "mellum": 2, "cohere": 1, "yi": 0, "nemotron": 0,
    "starcoder": 0,
}

USE_CASE_WEIGHTS = {
    "general": (0.35, 0.25, 0.30, 0.10),
    "coding": (0.40, 0.15, 0.30, 0.15),
    "reasoning": (0.45, 0.10, 0.30, 0.15),
    "chat": (0.30, 0.30, 0.30, 0.10),
}

CONTEXT_TARGETS = {"general": 4096, "coding": 8192, "reasoning": 8192, "chat": 4096}

DATA_DIR = Path(__file__).parent / "data"


def load_models() -> list[ModelEntry]:
    path = DATA_DIR / "models.json"
    if not path.exists():
        raise FileNotFoundError(f"Model database not found at {path}")
    with open(path) as f:
        raw = json.load(f)
    return [ModelEntry(**m) for m in raw]


def _quality_base(params_b: float) -> float:
    if params_b < 1: return 30
    if params_b < 3: return 45
    if params_b < 7: return 60
    if params_b < 10: return 75
    if params_b < 20: return 82
    if params_b < 40: return 89
    return 95


def _estimate_bandwidth(info: SystemInfo) -> float:
    if info.is_apple_silicon and info.gpus:
        name = info.gpus[0].name.lower()
        for key, bw in sorted(GPU_BANDWIDTH.items(), key=lambda x: -len(x[0])):
            if key in name: return bw
        return 150
    for gpu in info.gpus:
        name = gpu.name.lower()
        for key, bw in sorted(GPU_BANDWIDTH.items(), key=lambda x: -len(x[0])):
            if key in name: return bw
    if info.gpus:
        return {"cuda": 220, "rocm": 180, "metal": 150, "vulkan": 120}.get(info.gpus[0].backend, 100)
    return 50


def _estimate_vram(model: ModelEntry, quant: QuantInfo, ctx: int) -> float:
    active = model.active_params_b if model.is_moe and model.active_params_b else model.params_b
    weights_gb = model.params_b * quant.bpp
    kv_gb = 0.000008 * active * ctx
    overhead = 0.5
    return round(weights_gb + kv_gb + overhead, 2)


def recommend(info: SystemInfo, use_case: str = "coding",
              min_context: int = 0, top_k: int = 5) -> list[Recommendation]:
    models = load_models()
    bw = _estimate_bandwidth(info)
    avail_vram = max(info.total_vram_gb, info.ram_gb * 0.25)

    w_quality, w_speed, w_fit, w_context = USE_CASE_WEIGHTS.get(use_case, (0.35, 0.25, 0.30, 0.10))
    ctx_target = max(CONTEXT_TARGETS.get(use_case, 4096), min_context)

    all_recs: list[Recommendation] = []

    for model in models:
        if use_case not in model.use_cases and "general" not in model.use_cases:
            continue

        best_rec: Optional[Recommendation] = None
        model_vram_q4 = model.vram_q4 or _estimate_vram(model, QUANTS[4], model.context)

        for quant in QUANTS:
            if best_rec and quant.sort_order >= QUANTS.index(
                [q for q in QUANTS if q.name == best_rec.quant][0]
            ):
                if best_rec.run_mode == "gpu":
                    break

            for ctx in [model.context, 65536, 32768, 16384, 8192, 4096, 2048]:
                if ctx > model.context: continue
                if ctx < ctx_target and best_rec: continue

                vram_needed = _estimate_vram(model, quant, ctx)
                run_mode = "gpu"
                if vram_needed > avail_vram:
                    if vram_needed >= avail_vram * 3: continue
                    run_mode = "cpu_offload"

                quality = max(0, min(100, _quality_base(model.params_b)
                    + MODEL_FAMILY_QUALITY_BONUS.get(model.arch, 0) + quant.quality_penalty))

                speed = _estimate_speed(model, quant, bw, vram_needed, avail_vram)
                target_speed = 20 if use_case == "reasoning" else 30
                speed_score = min(100, (speed / target_speed) * 100)

                vram_ratio = vram_needed / max(avail_vram, 0.1)
                if run_mode == "cpu_offload":
                    fit_score = max(0, 50 - (vram_ratio - 1.0) * 50)
                elif vram_ratio <= 0.5: fit_score = 80 + (vram_ratio / 0.5) * 20
                elif vram_ratio <= 0.75: fit_score = 100
                elif vram_ratio <= 0.9: fit_score = 85
                elif vram_ratio <= 1.0: fit_score = 65
                else: fit_score = 40

                cscore = 100 if ctx >= ctx_target else (70 if ctx >= ctx_target // 2 else 30)
                composite = quality * w_quality + speed_score * w_speed + fit_score * w_fit + cscore * w_context

                quant_tag = quant.name.lower().replace("_", "-")
                ollama_tag = f"{model.id}:{quant_tag}" if quant_tag != "q4-k-m" else model.id

                rec = Recommendation(
                    model=model, quant=quant.name, vram_gb=vram_needed,
                    score=round(composite, 1), quality_score=round(quality, 1),
                    speed_score=round(speed_score, 1), fit_score=round(fit_score, 1),
                    context_score=round(cscore, 1), context_used=ctx,
                    run_mode=run_mode, ollama_cmd=f"ollama run {ollama_tag}",
                    details={"vram_q4": model_vram_q4, "params_b": model.params_b, "provider": model.provider}
                )

                if best_rec is None or _better_than(rec, best_rec):
                    best_rec = rec
                if run_mode == "gpu": break

        if best_rec:
            all_recs.append(best_rec)

    all_recs.sort(key=lambda r: r.score, reverse=True)
    return all_recs[:top_k]


def _better_than(new: Recommendation, old: Recommendation) -> bool:
    if new.run_mode == "gpu" and old.run_mode != "gpu": return True
    if new.run_mode != "gpu" and old.run_mode == "gpu": return False
    return new.score > old.score


def _estimate_speed(model: ModelEntry, quant: QuantInfo, bw: float, vram_gb: float, avail_gb: float) -> float:
    offload_penalty = 1.0
    if vram_gb > avail_gb:
        gpu_frac = avail_gb / vram_gb
        bw_eff = bw * gpu_frac + 50 * (1 - gpu_frac)
        offload_penalty = 0.3
    else:
        bw_eff = bw

    active = model.active_params_b if model.is_moe and model.active_params_b else model.params_b
    model_gb = active * quant.bpp
    moe_bonus = 1.2 if model.is_moe else 1.0
    tps = (bw_eff / max(model_gb, 0.5)) * 0.55 * quant.speed_mult * moe_bonus * ARCH_SPEED_BONUS.get(model.arch, 1.0) * offload_penalty
    return round(tps, 1)


def print_recommendations(recs: list[Recommendation], info: SystemInfo, use_case: str) -> None:
    if not recs:
        print("No models found that fit your hardware.")
        return
    print(f"Top {len(recs)} recommendations for '{use_case}' on your hardware:\n")
    print(f"{'#':<3} {'Model':<35} {'Quant':<7} {'Score':<7} {'VRAM':<7} {'Ctx':<7} {'Mode':<13} {'Command'}")
    print("-" * 120)
    for i, rec in enumerate(recs, 1):
        mode = "GPU" if rec.run_mode == "gpu" else "Offload"
        print(f"{i:<3} {rec.model.name:<35} {rec.quant:<7} {rec.score:<7} {rec.vram_gb:<7} {rec.context_used:<7} {mode:<13} {rec.ollama_cmd}")
    print()
    for i, rec in enumerate(recs[:3], 1):
        print(f"  {i}. {rec.model.name} ({rec.quant}) — Quality: {rec.quality_score:.0f} Speed: {rec.speed_score:.0f} Fit: {rec.fit_score:.0f} Ctx: {rec.context_score:.0f} = {rec.score}")
