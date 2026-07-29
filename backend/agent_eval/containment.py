"""Network-containment provider selection for agent evaluation evidence."""
from __future__ import annotations

import uuid
from typing import Any, Protocol, Sequence

from .evidence import EvidenceControlResult, EvidenceMode, EvidenceState
from .identity import FileIdentity
from .windows_job import WindowsJobProcess


class ContainmentError(RuntimeError):
    """Network containment could not be proven or cleaned up exactly."""


class ContainmentElevationRequired(ContainmentError):
    """The requested dynamic WFP session requires an elevated terminal."""


class ContainmentProvider(Protocol):
    launcher: Any

    def close(self) -> None: ...


class DiagnosticContainmentProvider:
    """Explicitly unsupported provider used only for invalid diagnostics."""

    launcher = None

    def close(self) -> None:
        return None


class WindowsContainmentProvider:
    launcher = WindowsJobProcess.start

    def __init__(self, session: Any) -> None:
        from .windows_wfp import WindowsWfpSession

        if type(session) is not WindowsWfpSession:
            raise ContainmentError(
                "verified containment requires the production Windows "
                "WFP session"
            )
        self._session = session

    @property
    def session(self) -> Any:
        return self._session

    def close(self) -> None:
        self._session.close()


def derive_containment_result(
    mode: EvidenceMode,
    provider: ContainmentProvider,
    endpoint: str,
    application_paths: Sequence[FileIdentity],
) -> EvidenceControlResult:
    """Derive evidence from the owned production session, never provider claims."""
    if mode is EvidenceMode.DIAGNOSTIC:
        if type(provider) is not DiagnosticContainmentProvider:
            raise ContainmentError(
                "diagnostic containment requires the diagnostic provider"
            )
        return EvidenceControlResult(
            "os_loopback_only_egress",
            EvidenceState.UNSUPPORTED,
            "OS egress containment is unsupported in diagnostic mode",
            {"provider": "diagnostic", "active": False},
        )
    if mode is not EvidenceMode.VERIFIED:
        raise ContainmentError("unknown evidence mode")

    from .windows_wfp import WindowsWfpSession

    if type(provider) is not WindowsContainmentProvider:
        raise ContainmentError(
            "verified containment requires the production Windows provider"
        )
    session = provider.session
    if type(session) is not WindowsWfpSession:
        raise ContainmentError(
            "verified containment requires the production Windows WFP session"
        )
    applications = tuple(application_paths)
    if (
        session.endpoint != endpoint
        or session.applications != applications
    ):
        raise ContainmentError(
            "production Windows WFP session identity does not match the plan"
        )
    details = session.verify_active()
    expected_keys = {
        "provider",
        "dynamic_session",
        "session_key",
        "sublayer_key",
        "endpoint",
        "applications",
        "filter_ids",
        "filter_count",
        "active",
        "verified_complete_shape",
    }
    expected_paths = [str(identity.path) for identity in applications]
    filter_ids = details.get("filter_ids") if isinstance(details, dict) else None
    valid_ids = (
        isinstance(filter_ids, list)
        and len(filter_ids) == 4 * len(applications)
        and len(set(filter_ids)) == len(filter_ids)
        and all(type(item) is int and item > 0 for item in filter_ids)
        and tuple(filter_ids) == session.filter_ids
    )
    try:
        uuid.UUID(details["session_key"])
        uuid.UUID(details["sublayer_key"])
        valid_uuids = True
    except (KeyError, TypeError, ValueError, AttributeError):
        valid_uuids = False
    if (
        not isinstance(details, dict)
        or set(details) != expected_keys
        or details.get("provider") != "windows_wfp"
        or details.get("dynamic_session") is not True
        or details.get("endpoint") != endpoint
        or details.get("applications") != expected_paths
        or type(details.get("filter_count")) is not int
        or details.get("filter_count") != 4 * len(applications)
        or details.get("active") is not True
        or details.get("verified_complete_shape") is not True
        or not valid_ids
        or not valid_uuids
    ):
        raise ContainmentError(
            "production Windows WFP measurement is incomplete or mismatched"
        )
    return EvidenceControlResult(
        "os_loopback_only_egress",
        EvidenceState.PASS,
        "measured dynamic WFP filters restrict proven applications to loopback Ollama",
        details,
    )


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

    return WindowsContainmentProvider(
        WindowsWfpSession.open(
            endpoint,
            application_paths,
            api=wfp_api,
        )
    )
