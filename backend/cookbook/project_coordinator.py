"""In-process serialization for workspace metadata and registered projects."""
from __future__ import annotations

import threading


WORKSPACE_PROJECT_LOCK = threading.RLock()


__all__ = ["WORKSPACE_PROJECT_LOCK"]
