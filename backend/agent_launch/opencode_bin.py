"""Locate the stock OpenCode binary that LAC wraps. P1 requires it on PATH;
bundling / auto-fetch is a P3 packaging concern."""
import shutil
from pathlib import Path


class OpenCodeNotFound(RuntimeError):
    pass


_INSTALL_HINT = (
    "OpenCode is not installed or not on PATH. LAC's agent wraps it.\n"
    "Install it (see https://opencode.ai/docs), e.g.:\n"
    "  npm i -g opencode-ai   (or)   curl -fsSL https://opencode.ai/install | bash\n"
    "then re-run `lac agent`."
)


def resolve_opencode_binary() -> Path:
    found = shutil.which("opencode")
    if not found:
        raise OpenCodeNotFound(_INSTALL_HINT)
    return Path(found)
