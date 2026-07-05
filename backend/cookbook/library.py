"""Shared library-browse enrichment: cross-reference scraped Ollama
library entries against the curated catalog to populate real VRAM/params
and a hardware-fit verdict. Used by both the web API's
/api/library/browse route and the CLI's `lac browse` command so there is
exactly one implementation of "what does this library entry look like on
my hardware" (the CLI path previously duplicated nothing -- it just
skipped enrichment entirely, which is why every result got filtered out)."""
from __future__ import annotations

import re
from typing import Optional

from .recommend import load_models


def enrich_library_models(models: list[dict], system_vram: Optional[float]) -> list[dict]:
    """Mutate + return models in place, adding vram_q4/params_b/fit fields
    from the curated catalog (or a rough estimate from advertised sizes
    when there's no catalog match)."""
    catalog_by_family: dict[str, list] = {}
    try:
        for cm in load_models():
            catalog_by_family.setdefault(cm.id.split(":")[0], []).append(cm)
    except Exception:
        pass

    sv = system_vram or 0
    for m in models:
        fam = m.get("name", "")
        variants = catalog_by_family.get(fam)
        if variants:
            variants = sorted(variants, key=lambda v: v.vram_q4 or 0)
            fitting = [v for v in variants if (v.vram_q4 or 0) <= sv * 0.9]
            if fitting:
                rep = fitting[-1]
                m["fit"] = "gpu"
            else:
                rep = variants[0]
                m["fit"] = "offload" if (rep.vram_q4 or 0) <= sv * 2 else "too_big"
            m["vram_q4"] = rep.vram_q4
            m["params_b"] = rep.params_b
        elif m.get("sizes"):
            # No catalog match — rough estimate from advertised sizes (e.g. "3B").
            try:
                pb = float(re.sub(r"[^0-9.]", "", str(m["sizes"][0])) or 0)
                if pb:
                    vq4 = round(pb * 0.6, 1)
                    m["params_b"] = pb
                    m["vram_q4"] = vq4
                    if sv:
                        m["fit"] = "gpu" if vq4 <= sv * 0.9 else ("offload" if vq4 <= sv * 2 else "too_big")
                    else:
                        m["fit"] = "unknown"
                else:
                    m["fit"] = "unknown"
            except Exception:
                m["fit"] = "unknown"
        else:
            m["fit"] = "unknown"
    return models
