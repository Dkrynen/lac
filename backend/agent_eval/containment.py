"""Network-containment provider selection for agent evaluation evidence."""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from .evidence import EvidenceControlResult, EvidenceMode, EvidenceState
from .identity import FileIdentity
from .windows_job import WindowsJobProcess


class ContainmentError(RuntimeError):
    """Network containment could not be proven or cleaned up exactly."""


class ContainmentProvider(Protocol):
    launcher: Any

    def verify_active(self) -> EvidenceControlResult: ...

    def close(self) -> None: ...


class DiagnosticContainmentProvider:
    """Explicitly unsupported provider used only for invalid diagnostics."""

    launcher = None

    def verify_active(self) -> EvidenceControlResult:
        return EvidenceControlResult(
            "os_loopback_only_egress",
            EvidenceState.UNSUPPORTED,
            "OS egress containment is unsupported in diagnostic mode",
            {"provider": "diagnostic", "active": False},
        )

    def close(self) -> None:
        return None


class _WindowsContainmentProvider:
    launcher = WindowsJobProcess.start

    def __init__(self, session: Any) -> None:
        self._session = session

    def verify_active(self) -> EvidenceControlResult:
        details = self._session.verify_active()
        return EvidenceControlResult(
            "os_loopback_only_egress",
            EvidenceState.PASS,
            "measured dynamic WFP filters restrict proven applications to loopback Ollama",
            details,
        )

    def close(self) -> None:
        self._session.close()


def select_containment_provider(
    mode: EvidenceMode,
    platform: str,
    endpoint: str,
    application_paths: Sequence[FileIdentity],
    *,
    wfp_api: object | None = None,
) -> ContainmentProvider:
    if mode is EvidenceMode.DIAGNOSTIC:
        return DiagnosticContainmentProvider()
    if mode is not EvidenceMode.VERIFIED:
        raise ContainmentError("unknown evidence mode")
    if platform != "nt":
        raise ContainmentError(
            "verified OS egress containment requires Windows"
        )
    from .windows_wfp import WindowsWfpSession

    return _WindowsContainmentProvider(
        WindowsWfpSession.open(
            endpoint,
            application_paths,
            api=wfp_api,
        )
    )
