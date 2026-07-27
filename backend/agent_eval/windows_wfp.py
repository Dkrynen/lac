"""Dynamic Windows Filtering Platform policy for verified agent evaluation."""
from __future__ import annotations

import ctypes
import hashlib
import ipaddress
import os
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

from .containment import ContainmentElevationRequired, ContainmentError
from .identity import FileIdentity


FWPM_SESSION_FLAG_DYNAMIC = 0x00000001
FWP_MATCH_EQUAL = 0
FWP_EMPTY = 0
FWP_UINT16 = 2
FWP_UINT32 = 3
FWP_UINT64 = 4
FWP_BYTE_ARRAY16_TYPE = 11
FWP_BYTE_BLOB_TYPE = 12
# Compound FWP data types begin at FWP_SINGLE_DATA_TYPE_MAX (0xFF) + 1.
# IP_REMOTE_ADDRESS requires FWP_V4_ADDR_AND_MASK; a bare FWP_UINT32 is
# accepted by FwpmFilterAdd0 but never matches at classification time.
FWP_V4_ADDR_MASK = 0x100
FWP_ACTION_BLOCK = 0x00001001
FWP_ACTION_PERMIT = 0x00001002
RPC_C_AUTHN_WINNT = 10
PERMIT_WEIGHT = 0xF000000000000000
BLOCK_WEIGHT = 0x1000000000000000
SUBLAYER_WEIGHT = 0x7FFF


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> "GUID":
        raw = value.bytes
        return cls(
            int.from_bytes(raw[0:4], "big"),
            int.from_bytes(raw[4:6], "big"),
            int.from_bytes(raw[6:8], "big"),
            (ctypes.c_ubyte * 8)(*raw[8:16]),
        )

    def to_uuid(self) -> uuid.UUID:
        return uuid.UUID(
            bytes=(
                int(self.Data1).to_bytes(4, "big")
                + int(self.Data2).to_bytes(2, "big")
                + int(self.Data3).to_bytes(2, "big")
                + bytes(self.Data4)
            )
        )


class FWP_BYTE_ARRAY16(ctypes.Structure):
    _fields_ = [("byteArray16", ctypes.c_ubyte * 16)]


class FWP_V4_ADDR_AND_MASK(ctypes.Structure):
    # addr holds the IPv4 address in WFP (network) byte order; mask is
    # a prefix length, where 32 means an exact single-host match.
    _fields_ = [("addr", wintypes.DWORD), ("mask", wintypes.DWORD)]


class FWP_BYTE_BLOB(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _FWP_VALUE_UNION(ctypes.Union):
    _fields_ = [
        ("uint8", ctypes.c_ubyte),
        ("uint16", wintypes.WORD),
        ("uint32", wintypes.DWORD),
        ("uint64", ctypes.POINTER(ctypes.c_ulonglong)),
        ("byteArray16", ctypes.POINTER(FWP_BYTE_ARRAY16)),
        ("byteBlob", ctypes.POINTER(FWP_BYTE_BLOB)),
        ("unicodeString", wintypes.LPWSTR),
        ("v4AddrMask", ctypes.POINTER(FWP_V4_ADDR_AND_MASK)),
    ]


class FWP_VALUE0(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", ctypes.c_int), ("value", _FWP_VALUE_UNION)]


class _FWP_CONDITION_UNION(ctypes.Union):
    _fields_ = _FWP_VALUE_UNION._fields_


class FWP_CONDITION_VALUE0(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("type", ctypes.c_int),
        ("value", _FWP_CONDITION_UNION),
    ]


class FWPM_DISPLAY_DATA0(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("description", wintypes.LPWSTR)]


class FWPM_SESSION0(ctypes.Structure):
    _fields_ = [
        ("sessionKey", GUID),
        ("displayData", FWPM_DISPLAY_DATA0),
        ("flags", wintypes.DWORD),
        ("txnWaitTimeoutInMSec", wintypes.DWORD),
        ("processId", wintypes.DWORD),
        ("sid", ctypes.c_void_p),
        ("username", wintypes.LPWSTR),
        ("kernelMode", wintypes.BOOL),
    ]


class FWPM_SUBLAYER0(ctypes.Structure):
    _fields_ = [
        ("subLayerKey", GUID),
        ("displayData", FWPM_DISPLAY_DATA0),
        ("flags", wintypes.DWORD),
        ("providerKey", ctypes.POINTER(GUID)),
        ("providerData", FWP_BYTE_BLOB),
        ("weight", wintypes.WORD),
    ]


class FWPM_ACTION0(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("filterType", GUID)]


class FWPM_FILTER_CONDITION0(ctypes.Structure):
    _fields_ = [
        ("fieldKey", GUID),
        ("matchType", ctypes.c_int),
        ("conditionValue", FWP_CONDITION_VALUE0),
    ]


class _FWPM_CONTEXT_UNION(ctypes.Union):
    _fields_ = [
        ("rawContext", ctypes.c_ulonglong),
        ("providerContextKey", GUID),
    ]


class FWPM_FILTER0(ctypes.Structure):
    _anonymous_ = ("context",)
    _fields_ = [
        ("filterKey", GUID),
        ("displayData", FWPM_DISPLAY_DATA0),
        ("flags", wintypes.DWORD),
        ("providerKey", ctypes.POINTER(GUID)),
        ("providerData", FWP_BYTE_BLOB),
        ("layerKey", GUID),
        ("subLayerKey", GUID),
        ("weight", FWP_VALUE0),
        ("numFilterConditions", wintypes.DWORD),
        ("filterCondition", ctypes.POINTER(FWPM_FILTER_CONDITION0)),
        ("action", FWPM_ACTION0),
        ("context", _FWPM_CONTEXT_UNION),
        ("reserved", ctypes.POINTER(GUID)),
        ("filterId", ctypes.c_ulonglong),
        ("effectiveWeight", FWP_VALUE0),
    ]


FWPM_LAYER_ALE_AUTH_CONNECT_V4 = uuid.UUID(
    "c38d57d1-05a7-4c33-904f-7fbceee60e82"
)
FWPM_LAYER_ALE_AUTH_CONNECT_V6 = uuid.UUID(
    "4a72393b-319f-44bc-84c3-ba54dcb3b6b4"
)
FWPM_CONDITION_ALE_APP_ID = uuid.UUID(
    "d78e1e87-8644-4ea5-9437-d809ecefc971"
)
FWPM_CONDITION_IP_REMOTE_ADDRESS = uuid.UUID(
    "b235ae9a-1d64-49b8-a44c-5ff3d9095045"
)
FWPM_CONDITION_IP_REMOTE_PORT = uuid.UUID(
    "c35a604d-d22b-4e1a-91b4-68f674ee674b"
)


@dataclass(frozen=True)
class FilterSpec:
    layer: str
    action: str
    weight: int
    app_id: bytes
    remote_address: str | None
    remote_port: int | None
    sublayer_key: str


@dataclass(frozen=True)
class _AppAllocation:
    value: bytes
    token: object


def _operation(name: str, function):
    try:
        return function()
    except ContainmentError:
        raise
    except BaseException as exc:
        if getattr(exc, "winerror", None) == 5:
            raise ContainmentElevationRequired(
                f"{name} denied: run the exact command from an "
                "Administrator elevated PowerShell"
            ) from exc
        raise ContainmentError(f"{name} failed: {exc}") from exc


def _validate_endpoint(endpoint: str) -> int:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ContainmentError("containment endpoint is invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or type(port) is not int
        or not 1 <= port <= 65535
    ):
        raise ContainmentError(
            "containment endpoint must be literal loopback with an exact port"
        )
    return port


def _validated_applications(
    applications: Sequence[FileIdentity],
) -> tuple[FileIdentity, ...]:
    if not applications:
        raise ContainmentError("application identity set must not be empty")
    unique: dict[str, FileIdentity] = {}
    for identity in applications:
        if not isinstance(identity, FileIdentity):
            raise ContainmentError(
                "application path must come from a Task 2 FileIdentity"
            )
        try:
            resolved = identity.path.resolve(strict=True)
        except OSError as exc:
            raise ContainmentError("proven application is missing") from exc
        if (
            not resolved.is_file()
            or resolved.suffix.lower() != ".exe"
            or resolved.name.lower().startswith("python")
        ):
            raise ContainmentError(
                "application must be a dedicated native executable"
            )
        raw = resolved.read_bytes()
        if (
            len(raw) != identity.size
            or hashlib.sha256(raw).hexdigest() != identity.sha256
        ):
            raise ContainmentError("application identity has mutated")
        key = str(resolved).casefold()
        unique.setdefault(
            key,
            FileIdentity(
                resolved,
                identity.size,
                identity.sha256,
                identity.version,
                identity.authenticode,
            ),
        )
    return tuple(unique.values())


class WindowsWfpSession:
    """Owns one non-persistent dynamic WFP session and its exact filters."""

    def __init__(
        self,
        *,
        api: object,
        endpoint: str,
        applications: tuple[FileIdentity, ...],
        engine: object,
        session_key: uuid.UUID,
        sublayer_key: uuid.UUID,
        filter_ids: list[int],
        expected: dict[int, FilterSpec],
    ) -> None:
        self._api = api
        self.endpoint = endpoint
        self.applications = applications
        self._engine = engine
        self.session_key = session_key
        self.sublayer_key = sublayer_key
        self._sublayer_owned = True
        self._filter_ids = filter_ids
        self._expected = expected
        self._closed = False
        self._uncertainty: ContainmentError | None = None

    @property
    def filter_ids(self) -> tuple[int, ...]:
        return tuple(self._filter_ids)

    @classmethod
    def open(
        cls,
        endpoint: str,
        application_paths: Sequence[FileIdentity],
        *,
        api: object | None = None,
    ) -> "WindowsWfpSession":
        port = _validate_endpoint(endpoint)
        applications = _validated_applications(application_paths)
        selected_api = api if api is not None else _FwpuclntApi()
        session_key = uuid.uuid4()
        sublayer_key = uuid.uuid4()
        engine = None
        sublayer_owned = False
        allocations: list[object] = []
        filter_ids: list[int] = []
        expected: dict[int, FilterSpec] = {}
        operation = "engine_open"
        try:
            engine = _operation(
                operation,
                lambda: selected_api.engine_open_dynamic(session_key),
            )
            operation = "sublayer_add"
            _operation(
                operation,
                lambda: selected_api.sublayer_add(engine, sublayer_key),
            )
            sublayer_owned = True
            for application in applications:
                operation = "get_app_id"
                allocation = _operation(
                    operation,
                    lambda application=application: selected_api.get_app_id(
                        application.path
                    ),
                )
                allocations.append(allocation.token)
                app_id = bytes(allocation.value)
                operation = "free_memory"
                _operation(
                    operation,
                    lambda token=allocation.token: selected_api.free_memory(
                        token
                    ),
                )
                allocations.remove(allocation.token)
                specs = (
                    FilterSpec(
                        "v4",
                        "permit",
                        PERMIT_WEIGHT,
                        app_id,
                        "127.0.0.1",
                        port,
                        str(sublayer_key),
                    ),
                    FilterSpec(
                        "v4",
                        "block",
                        BLOCK_WEIGHT,
                        app_id,
                        None,
                        None,
                        str(sublayer_key),
                    ),
                    FilterSpec(
                        "v6",
                        "permit",
                        PERMIT_WEIGHT,
                        app_id,
                        "::1",
                        port,
                        str(sublayer_key),
                    ),
                    FilterSpec(
                        "v6",
                        "block",
                        BLOCK_WEIGHT,
                        app_id,
                        None,
                        None,
                        str(sublayer_key),
                    ),
                )
                for spec in specs:
                    operation = "filter_add"
                    filter_id = _operation(
                        operation,
                        lambda spec=spec: selected_api.filter_add(
                            engine,
                            spec,
                        ),
                    )
                    if type(filter_id) is not int or filter_id <= 0:
                        raise ContainmentError(
                            "filter_add returned an invalid filter ID"
                        )
                    filter_ids.append(filter_id)
                    expected[filter_id] = spec
            session = cls(
                api=selected_api,
                endpoint=endpoint,
                applications=applications,
                engine=engine,
                session_key=session_key,
                sublayer_key=sublayer_key,
                filter_ids=filter_ids,
                expected=expected,
            )
            operation = "filter_get"
            session.verify_active()
            return session
        except BaseException as exc:
            rollback_errors: list[str] = []

            def rollback(name: str, function) -> None:
                try:
                    function()
                except BaseException as cleanup_exc:
                    rollback_errors.append(f"{name}: {cleanup_exc}")

            for filter_id in reversed(filter_ids):
                rollback(
                    f"filter_delete[{filter_id}]",
                    lambda filter_id=filter_id: selected_api.filter_delete(
                        engine, filter_id
                    ),
                )
            for token in reversed(allocations):
                rollback(
                    "free_memory",
                    lambda token=token: selected_api.free_memory(token),
                )
            if sublayer_owned:
                rollback(
                    "sublayer_delete",
                    lambda: selected_api.sublayer_delete(
                        engine, sublayer_key
                    ),
                )
            if engine is not None:
                rollback(
                    "engine_close",
                    lambda: selected_api.engine_close(engine),
                )
            message = str(exc)
            if not isinstance(exc, ContainmentError):
                message = f"{operation} failed: {exc}"
            if rollback_errors:
                message += "; rollback uncertain: " + "; ".join(
                    rollback_errors
                )
            error_type = (
                ContainmentElevationRequired
                if isinstance(exc, ContainmentElevationRequired)
                and not rollback_errors
                else ContainmentError
            )
            raise error_type(message) from exc

    def verify_active(self) -> dict[str, object]:
        if self._uncertainty is not None:
            raise self._uncertainty
        if self._closed or self._engine is None:
            raise ContainmentError("WFP session is not active")
        for filter_id in self._filter_ids:
            measured = _operation(
                "filter_get",
                lambda filter_id=filter_id: self._api.filter_get(
                    self._engine,
                    filter_id,
                ),
            )
            if measured != self._expected[filter_id]:
                error = ContainmentError(
                    f"filter verification failed for ID {filter_id}"
                )
                self._uncertainty = error
                raise error
        return {
            "provider": "windows_wfp",
            "dynamic_session": True,
            "session_key": str(self.session_key),
            "sublayer_key": str(self.sublayer_key),
            "endpoint": self.endpoint,
            "applications": [
                str(identity.path) for identity in self.applications
            ],
            "filter_ids": list(self._filter_ids),
            "filter_count": len(self._filter_ids),
            "active": True,
            "verified_complete_shape": True,
        }

    def close(self) -> None:
        if self._closed:
            if self._uncertainty is not None:
                raise self._uncertainty
            return
        errors: list[ContainmentError] = []
        for filter_id in tuple(reversed(self._filter_ids)):
            try:
                _operation(
                    f"filter_delete[{filter_id}]",
                    lambda filter_id=filter_id: self._api.filter_delete(
                        self._engine,
                        filter_id,
                    ),
                )
            except ContainmentError as exc:
                errors.append(exc)
            else:
                self._filter_ids.remove(filter_id)
                self._expected.pop(filter_id, None)
        if not self._filter_ids and self._sublayer_owned:
            try:
                _operation(
                    "sublayer_delete",
                    lambda: self._api.sublayer_delete(
                        self._engine,
                        self.sublayer_key,
                    ),
                )
            except ContainmentError as exc:
                errors.append(exc)
            else:
                self._sublayer_owned = False
        if not self._filter_ids and not self._sublayer_owned and self._engine is not None:
            engine = self._engine
            try:
                _operation(
                    "engine_close",
                    lambda: self._api.engine_close(engine),
                )
            except ContainmentError as exc:
                errors.append(exc)
            else:
                self._engine = None
        self._closed = (
            not self._filter_ids
            and not self._sublayer_owned
            and self._engine is None
        )
        if errors:
            if self._uncertainty is None:
                self._uncertainty = ContainmentError(
                    "; ".join(str(error) for error in errors)
                )
            raise self._uncertainty
        if self._uncertainty is not None:
            raise self._uncertainty


class _FwpuclntApi:
    """Minimal fwpuclnt binding. Pure tests inject FakeWfpApi instead."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise ContainmentError("WFP requires Windows")
        library = ctypes.WinDLL("fwpuclnt.dll", use_last_error=True)
        self._engine_open = library.FwpmEngineOpen0
        self._engine_open.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(FWPM_SESSION0),
            ctypes.POINTER(wintypes.HANDLE),
        )
        self._engine_open.restype = wintypes.DWORD
        self._engine_close = library.FwpmEngineClose0
        self._engine_close.argtypes = (wintypes.HANDLE,)
        self._engine_close.restype = wintypes.DWORD
        self._sublayer_add = library.FwpmSubLayerAdd0
        self._sublayer_add.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(FWPM_SUBLAYER0),
            ctypes.c_void_p,
        )
        self._sublayer_add.restype = wintypes.DWORD
        self._sublayer_delete = library.FwpmSubLayerDeleteByKey0
        self._sublayer_delete.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(GUID),
        )
        self._sublayer_delete.restype = wintypes.DWORD
        self._filter_add = library.FwpmFilterAdd0
        self._filter_add.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(FWPM_FILTER0),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulonglong),
        )
        self._filter_add.restype = wintypes.DWORD
        self._filter_delete = library.FwpmFilterDeleteById0
        self._filter_delete.argtypes = (
            wintypes.HANDLE,
            ctypes.c_ulonglong,
        )
        self._filter_delete.restype = wintypes.DWORD
        self._filter_get = library.FwpmFilterGetById0
        self._filter_get.argtypes = (
            wintypes.HANDLE,
            ctypes.c_ulonglong,
            ctypes.POINTER(ctypes.POINTER(FWPM_FILTER0)),
        )
        self._filter_get.restype = wintypes.DWORD
        self._get_app_id = library.FwpmGetAppIdFromFileName0
        self._get_app_id.argtypes = (
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.POINTER(FWP_BYTE_BLOB)),
        )
        self._get_app_id.restype = wintypes.DWORD
        self._free_memory = library.FwpmFreeMemory0
        self._free_memory.argtypes = (ctypes.POINTER(ctypes.c_void_p),)
        self._free_memory.restype = None
        self._native_expected: dict[int, FilterSpec] = {}

    @staticmethod
    def _check(code: int, operation: str) -> None:
        if code == 5:
            error = OSError("access denied")
            error.winerror = 5
            raise error
        if code:
            raise OSError(code, f"{operation} returned {code}")

    def engine_open_dynamic(self, session_key: uuid.UUID) -> int:
        session = FWPM_SESSION0()
        session.sessionKey = GUID.from_uuid(session_key)
        session.displayData = FWPM_DISPLAY_DATA0(
            "LAC agent evaluation",
            "Dynamic loopback-only evaluation containment",
        )
        session.flags = FWPM_SESSION_FLAG_DYNAMIC
        handle = wintypes.HANDLE()
        self._check(
            self._engine_open(
                None,
                RPC_C_AUTHN_WINNT,
                None,
                ctypes.byref(session),
                ctypes.byref(handle),
            ),
            "FwpmEngineOpen0",
        )
        if not handle.value:
            raise OSError("FwpmEngineOpen0 returned a null engine")
        return int(handle.value)

    def engine_close(self, engine: int) -> None:
        self._check(self._engine_close(engine), "FwpmEngineClose0")

    def sublayer_add(self, engine: int, key: uuid.UUID) -> None:
        sublayer = FWPM_SUBLAYER0()
        sublayer.subLayerKey = GUID.from_uuid(key)
        sublayer.displayData = FWPM_DISPLAY_DATA0(
            "LAC agent evaluation",
            "Unique dynamic loopback-only sublayer",
        )
        sublayer.weight = SUBLAYER_WEIGHT
        self._check(
            self._sublayer_add(engine, ctypes.byref(sublayer), None),
            "FwpmSubLayerAdd0",
        )

    def sublayer_delete(self, engine: int, key: uuid.UUID) -> None:
        native = GUID.from_uuid(key)
        self._check(
            self._sublayer_delete(engine, ctypes.byref(native)),
            "FwpmSubLayerDeleteByKey0",
        )

    def get_app_id(self, path: Path) -> _AppAllocation:
        pointer = ctypes.POINTER(FWP_BYTE_BLOB)()
        self._check(
            self._get_app_id(str(path), ctypes.byref(pointer)),
            "FwpmGetAppIdFromFileName0",
        )
        if not pointer:
            raise OSError("FwpmGetAppIdFromFileName0 returned null")
        value = ctypes.string_at(pointer.contents.data, pointer.contents.size)
        return _AppAllocation(value, pointer)

    def free_memory(self, token: object) -> None:
        pointer = ctypes.cast(token, ctypes.c_void_p)
        self._free_memory(ctypes.byref(pointer))

    def filter_add(self, engine: int, spec: FilterSpec) -> int:
        native, references = _native_filter(spec)
        filter_id = ctypes.c_ulonglong()
        self._check(
            self._filter_add(
                engine,
                ctypes.byref(native),
                None,
                ctypes.byref(filter_id),
            ),
            "FwpmFilterAdd0",
        )
        self._native_expected[int(filter_id.value)] = spec
        return int(filter_id.value)

    def filter_delete(self, engine: int, filter_id: int) -> None:
        self._check(
            self._filter_delete(engine, filter_id),
            "FwpmFilterDeleteById0",
        )
        self._native_expected.pop(filter_id, None)

    def filter_get(self, engine: int, filter_id: int) -> FilterSpec:
        pointer = ctypes.POINTER(FWPM_FILTER0)()
        self._check(
            self._filter_get(engine, filter_id, ctypes.byref(pointer)),
            "FwpmFilterGetById0",
        )
        if not pointer:
            raise OSError("FwpmFilterGetById0 returned null")
        token = ctypes.cast(pointer, ctypes.c_void_p)
        try:
            expected = self._native_expected.get(filter_id)
            if expected is None:
                raise OSError("filter ID is not owned by this session")
            return _parse_native_filter(pointer.contents, expected)
        finally:
            self._free_memory(ctypes.byref(token))


def _native_filter(spec: FilterSpec) -> tuple[FWPM_FILTER0, list[object]]:
    references: list[object] = []
    native = FWPM_FILTER0()
    native.filterKey = GUID.from_uuid(uuid.uuid4())
    native.displayData = FWPM_DISPLAY_DATA0(
        f"LAC {spec.layer} {spec.action}",
        "Dynamic per-application evaluation containment",
    )
    native.layerKey = GUID.from_uuid(
        FWPM_LAYER_ALE_AUTH_CONNECT_V4
        if spec.layer == "v4"
        else FWPM_LAYER_ALE_AUTH_CONNECT_V6
    )
    native.subLayerKey = GUID.from_uuid(uuid.UUID(spec.sublayer_key))
    weight = ctypes.c_ulonglong(spec.weight)
    references.append(weight)
    native.weight.type = FWP_UINT64
    native.weight.uint64 = ctypes.pointer(weight)
    native.action.type = (
        FWP_ACTION_PERMIT if spec.action == "permit" else FWP_ACTION_BLOCK
    )
    conditions: list[FWPM_FILTER_CONDITION0] = []

    app_buffer = (ctypes.c_ubyte * len(spec.app_id)).from_buffer_copy(spec.app_id)
    app_blob = FWP_BYTE_BLOB(
        len(spec.app_id),
        ctypes.cast(app_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    references.extend((app_buffer, app_blob))
    app_condition = FWPM_FILTER_CONDITION0()
    app_condition.fieldKey = GUID.from_uuid(FWPM_CONDITION_ALE_APP_ID)
    app_condition.matchType = FWP_MATCH_EQUAL
    app_condition.conditionValue.type = FWP_BYTE_BLOB_TYPE
    app_condition.conditionValue.byteBlob = ctypes.pointer(app_blob)
    conditions.append(app_condition)

    if spec.remote_address is not None:
        address_condition = FWPM_FILTER_CONDITION0()
        address_condition.fieldKey = GUID.from_uuid(
            FWPM_CONDITION_IP_REMOTE_ADDRESS
        )
        address_condition.matchType = FWP_MATCH_EQUAL
        if spec.layer == "v4":
            v4_addr_mask = FWP_V4_ADDR_AND_MASK(
                int.from_bytes(
                    ipaddress.IPv4Address(spec.remote_address).packed,
                    sys.byteorder,
                ),
                32,
            )
            references.append(v4_addr_mask)
            address_condition.conditionValue.type = FWP_V4_ADDR_MASK
            address_condition.conditionValue.v4AddrMask = ctypes.pointer(
                v4_addr_mask
            )
        else:
            address = FWP_BYTE_ARRAY16(
                (ctypes.c_ubyte * 16)(
                    *ipaddress.IPv6Address(spec.remote_address).packed
                )
            )
            references.append(address)
            address_condition.conditionValue.type = FWP_BYTE_ARRAY16_TYPE
            address_condition.conditionValue.byteArray16 = ctypes.pointer(
                address
            )
        conditions.append(address_condition)
    if spec.remote_port is not None:
        port_condition = FWPM_FILTER_CONDITION0()
        port_condition.fieldKey = GUID.from_uuid(
            FWPM_CONDITION_IP_REMOTE_PORT
        )
        port_condition.matchType = FWP_MATCH_EQUAL
        port_condition.conditionValue.type = FWP_UINT16
        port_condition.conditionValue.uint16 = spec.remote_port
        conditions.append(port_condition)
    condition_array = (FWPM_FILTER_CONDITION0 * len(conditions))(*conditions)
    references.append(condition_array)
    native.numFilterConditions = len(conditions)
    native.filterCondition = condition_array
    return native, references


def _parse_native_filter(
    native: FWPM_FILTER0,
    expected: FilterSpec,
) -> FilterSpec:
    layer = (
        "v4"
        if native.layerKey.to_uuid() == FWPM_LAYER_ALE_AUTH_CONNECT_V4
        else "v6"
        if native.layerKey.to_uuid() == FWPM_LAYER_ALE_AUTH_CONNECT_V6
        else "unknown"
    )
    action = (
        "permit"
        if native.action.type == FWP_ACTION_PERMIT
        else "block"
        if native.action.type == FWP_ACTION_BLOCK
        else "unknown"
    )
    weight = (
        int(native.weight.uint64.contents.value)
        if native.weight.type == FWP_UINT64 and native.weight.uint64
        else -1
    )
    app_id = b""
    remote_address = None
    remote_port = None
    expected_conditions = [
        (FWPM_CONDITION_ALE_APP_ID, FWP_BYTE_BLOB_TYPE),
    ]
    if (expected.remote_address is None) != (expected.remote_port is None):
        raise ContainmentError(
            "native filter condition shape has an incomplete permit"
        )
    if expected.remote_address is not None:
        expected_conditions.extend(
            (
                (
                    FWPM_CONDITION_IP_REMOTE_ADDRESS,
                    FWP_V4_ADDR_MASK
                    if expected.layer == "v4"
                    else FWP_BYTE_ARRAY16_TYPE,
                ),
                (FWPM_CONDITION_IP_REMOTE_PORT, FWP_UINT16),
            )
        )
    if (
        int(native.numFilterConditions) != len(expected_conditions)
        or not native.filterCondition
    ):
        raise ContainmentError(
            "native filter condition shape has the wrong condition count"
        )
    for index, (expected_field, expected_type) in enumerate(
        expected_conditions
    ):
        condition = native.filterCondition[index]
        field = condition.fieldKey.to_uuid()
        value = condition.conditionValue
        if (
            field != expected_field
            or int(condition.matchType) != FWP_MATCH_EQUAL
            or int(value.type) != expected_type
        ):
            raise ContainmentError(
                f"native filter condition shape mismatch at index {index}"
            )
        if (
            field == FWPM_CONDITION_ALE_APP_ID
            and value.byteBlob
        ):
            app_id = ctypes.string_at(
                value.byteBlob.contents.data,
                value.byteBlob.contents.size,
            )
        elif field == FWPM_CONDITION_IP_REMOTE_ADDRESS:
            if expected_type == FWP_V4_ADDR_MASK:
                if not value.v4AddrMask:
                    raise ContainmentError(
                        f"native filter condition shape has a null value at index {index}"
                    )
                v4_addr_mask = value.v4AddrMask.contents
                if int(v4_addr_mask.mask) != 32:
                    raise ContainmentError(
                        f"native filter condition shape has a non-exact prefix at index {index}"
                    )
                remote_address = str(
                    ipaddress.IPv4Address(
                        int(v4_addr_mask.addr).to_bytes(4, sys.byteorder)
                    )
                )
            elif value.byteArray16:
                remote_address = str(
                    ipaddress.IPv6Address(
                        bytes(value.byteArray16.contents.byteArray16)
                    )
                )
        elif field == FWPM_CONDITION_IP_REMOTE_PORT:
            remote_port = int(value.uint16)
        if (
            field == FWPM_CONDITION_ALE_APP_ID
            and not value.byteBlob
        ) or (
            field == FWPM_CONDITION_IP_REMOTE_ADDRESS
            and expected_type == FWP_BYTE_ARRAY16_TYPE
            and not value.byteArray16
        ):
            raise ContainmentError(
                f"native filter condition shape has a null value at index {index}"
            )
    return FilterSpec(
        layer,
        action,
        weight,
        app_id,
        remote_address,
        remote_port,
        str(native.subLayerKey.to_uuid()),
    )
