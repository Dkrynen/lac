import ctypes
import hashlib
import importlib.util
import inspect
import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.agent_eval.containment as containment_module
import backend.agent_eval.windows_wfp as windows_wfp_module
from backend.agent_eval.containment import (
    ContainmentError,
    select_containment_provider,
)
from backend.agent_eval.evidence import EvidenceMode, EvidenceState
from backend.agent_eval.identity import FileIdentity
from backend.agent_eval.windows_wfp import (
    BLOCK_WEIGHT,
    PERMIT_WEIGHT,
    WindowsWfpSession,
)


class FakeWfpApi:
    def __init__(self):
        self.calls = []
        self.fail = {}
        self.fail_once = {}
        self.next_filter_id = 100
        self.filters = {}
        self.override_filters = {}
        self.next_allocation = 1

    def _raise(self, name):
        error = self.fail.get(name)
        if error is not None:
            raise error
        error = self.fail_once.pop(name, None)
        if error is not None:
            raise error

    def engine_open_dynamic(self, session_key):
        self.calls.append(("engine_open", session_key))
        self._raise("engine_open")
        return 10

    def engine_close(self, engine):
        self.calls.append(("engine_close", engine))
        self._raise("engine_close")

    def sublayer_add(self, engine, sublayer_key):
        self.calls.append(("sublayer_add", engine, sublayer_key))
        self._raise("sublayer_add")

    def sublayer_delete(self, engine, sublayer_key):
        self.calls.append(("sublayer_delete", engine, sublayer_key))
        self._raise("sublayer_delete")

    def get_app_id(self, path):
        self.calls.append(("get_app_id", str(path)))
        self._raise("get_app_id")
        token = self.next_allocation
        self.next_allocation += 1
        return SimpleNamespace(
            value=("app:" + str(path).lower()).encode(),
            token=token,
        )

    def free_memory(self, token):
        self.calls.append(("free_memory", token))
        self._raise("free_memory")

    def filter_add(self, engine, spec):
        self.calls.append(("filter_add", spec))
        self._raise("filter_add")
        filter_id = self.next_filter_id
        self.next_filter_id += 1
        self.filters[filter_id] = spec
        return filter_id

    def filter_get(self, engine, filter_id):
        self.calls.append(("filter_get", filter_id))
        self._raise("filter_get")
        return self.override_filters.get(filter_id, self.filters[filter_id])

    def filter_delete(self, engine, filter_id):
        self.calls.append(("filter_delete", filter_id))
        self._raise("filter_delete")
        self.filters.pop(filter_id, None)


def identity(path: Path, payload=b"native"):
    path.write_bytes(payload)
    return FileIdentity(
        path=path.resolve(),
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        version="1.18.4",
        authenticode="unsigned",
    )


def open_session(tmp_path, api=None, endpoint="http://127.0.0.1:11434"):
    application = identity(tmp_path / "opencode.exe")
    selected = api or FakeWfpApi()
    return (
        WindowsWfpSession.open(
            endpoint,
            [application],
            api=selected,
        ),
        selected,
    )


def test_dynamic_session_installs_exact_per_app_v4_v6_policy(tmp_path):
    session, api = open_session(tmp_path)
    specs = [call[1] for call in api.calls if call[0] == "filter_add"]

    assert [spec.layer for spec in specs] == ["v4", "v4", "v6", "v6"]
    assert [spec.action for spec in specs] == [
        "permit",
        "block",
        "permit",
        "block",
    ]
    assert PERMIT_WEIGHT > BLOCK_WEIGHT
    for permit in (specs[0], specs[2]):
        assert permit.weight == PERMIT_WEIGHT
        assert permit.remote_port == 11434
        assert permit.remote_address in {"127.0.0.1", "::1"}
        assert permit.app_id
    for block in (specs[1], specs[3]):
        assert block.weight == BLOCK_WEIGHT
        assert block.remote_port is None
        assert block.remote_address is None
        assert block.app_id
    assert len(session.filter_ids) == 4
    session.close()


def test_application_paths_are_normalized_and_deduplicated(tmp_path):
    app = identity(tmp_path / "OpenCode.EXE")
    api = FakeWfpApi()
    session = WindowsWfpSession.open(
        "http://127.0.0.1:11434",
        [app, replace(app, path=Path(str(app.path).upper()))],
        api=api,
    )

    assert len([call for call in api.calls if call[0] == "get_app_id"]) == 1
    assert len(session.filter_ids) == 4
    session.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434",
        "http://192.168.1.2:11434",
        "https://127.0.0.1:11434",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:true",
    ],
)
def test_nonliteral_or_invalid_endpoint_is_refused_before_engine(
    tmp_path, endpoint
):
    api = FakeWfpApi()
    with pytest.raises(ContainmentError):
        WindowsWfpSession.open(endpoint, [identity(tmp_path / "app.exe")], api=api)
    assert api.calls == []


@pytest.mark.parametrize("kind", ["missing", "python", "batch", "mutated"])
def test_unproven_or_unsafe_application_is_refused(tmp_path, kind):
    path = tmp_path / (
        "python.exe" if kind == "python" else "app.cmd" if kind == "batch" else "app.exe"
    )
    app = identity(path)
    if kind == "missing":
        path.unlink()
    elif kind == "mutated":
        path.write_bytes(b"changed")
    api = FakeWfpApi()

    with pytest.raises(ContainmentError):
        WindowsWfpSession.open("http://127.0.0.1:11434", [app], api=api)
    assert api.calls == []


def test_access_denied_names_administrator_and_elevation(tmp_path):
    api = FakeWfpApi()
    error = OSError("access denied")
    error.winerror = 5
    api.fail["engine_open"] = error

    with pytest.raises(
        ContainmentError,
        match="Administrator.*elevated PowerShell",
    ):
        WindowsWfpSession.open(
            "http://127.0.0.1:11434",
            [identity(tmp_path / "app.exe")],
            api=api,
        )


@pytest.mark.parametrize(
    "failure",
    ["engine_open", "sublayer_add", "get_app_id", "free_memory", "filter_add"],
)
def test_partial_open_failure_rolls_back_in_reverse_order(tmp_path, failure):
    api = FakeWfpApi()
    api.fail_once[failure] = OSError(f"{failure} failed")

    with pytest.raises(ContainmentError, match=failure):
        WindowsWfpSession.open(
            "http://127.0.0.1:11434",
            [identity(tmp_path / "app.exe")],
            api=api,
        )

    names = [call[0] for call in api.calls]
    if "filter_add" in names:
        added = names.count("filter_add") - 1
        assert names.count("filter_delete") == added
    if "sublayer_add" in names and failure != "sublayer_add":
        assert "sublayer_delete" in names
    if failure != "engine_open":
        assert names[-1] == "engine_close"


@pytest.mark.parametrize(
    "field",
    [
        "layer",
        "action",
        "weight",
        "app_id",
        "remote_address",
        "remote_port",
        "sublayer_key",
    ],
)
def test_verify_active_compares_complete_filter_shape(tmp_path, field):
    session, api = open_session(tmp_path)
    filter_id = session.filter_ids[0]
    original = api.filters[filter_id]
    replacement = {
        "layer": "v6",
        "action": "block",
        "weight": original.weight + 1,
        "app_id": b"other",
        "remote_address": "::2",
        "remote_port": 9,
        "sublayer_key": "other",
    }[field]
    api.override_filters[filter_id] = replace(original, **{field: replacement})

    with pytest.raises(ContainmentError, match="verification"):
        session.verify_active()
    with pytest.raises(ContainmentError, match="verification"):
        session.close()


def test_verify_failure_and_close_uncertainty_remain_fatal(tmp_path):
    session, api = open_session(tmp_path)
    api.fail_once["filter_get"] = OSError("get failed")
    with pytest.raises(ContainmentError, match="filter_get"):
        session.verify_active()

    first_filter = session.filter_ids[-1]
    api.fail_once["filter_delete"] = OSError("delete failed")
    with pytest.raises(ContainmentError, match="filter_delete"):
        session.close()
    assert first_filter in session.filter_ids
    with pytest.raises(ContainmentError):
        session.close()
    assert session.filter_ids == ()
    with pytest.raises(ContainmentError):
        session.verify_active()


def test_close_rolls_back_filters_sublayer_engine_and_is_idempotent(tmp_path):
    session, api = open_session(tmp_path)
    ids = session.filter_ids
    session.close()
    calls = list(api.calls)
    session.close()

    deleted = [call[1] for call in calls if call[0] == "filter_delete"]
    assert deleted == list(reversed(ids))
    assert [call[0] for call in calls[-6:]] == [
        "filter_delete",
        "filter_delete",
        "filter_delete",
        "filter_delete",
        "sublayer_delete",
        "engine_close",
    ]
    assert api.calls == calls


def test_each_session_uses_a_unique_sublayer(tmp_path):
    app = identity(tmp_path / "app.exe")
    first_api = FakeWfpApi()
    second_api = FakeWfpApi()
    first = WindowsWfpSession.open(
        "http://127.0.0.1:11434", [app], api=first_api
    )
    second = WindowsWfpSession.open(
        "http://127.0.0.1:11434", [app], api=second_api
    )
    assert first.sublayer_key != second.sublayer_key
    first.close()
    second.close()


def test_no_permanent_firewall_registry_or_shell_path_exists():
    source = inspect.getsource(
        __import__(
            "backend.agent_eval.windows_wfp",
            fromlist=["WindowsWfpSession"],
        )
    ).lower()
    for forbidden in ("netsh", "new-netfirewallrule", "registry", "reg.exe"):
        assert forbidden not in source


def test_native_port_condition_uses_wfp_host_byte_order():
    spec = windows_wfp_module.FilterSpec(
        "v4",
        "permit",
        PERMIT_WEIGHT,
        b"app-id",
        "127.0.0.1",
        11434,
        "12345678-1234-5678-9abc-123456789abc",
    )

    native, references = windows_wfp_module._native_filter(spec)

    assert references
    port = next(
        condition
        for condition in native.filterCondition[: native.numFilterConditions]
        if condition.fieldKey.to_uuid()
        == windows_wfp_module.FWPM_CONDITION_IP_REMOTE_PORT
    )
    assert port.conditionValue.uint16 == 11434
    assert windows_wfp_module._parse_native_filter(native, spec) == spec


@pytest.mark.skipif(
    sys.byteorder != "little",
    reason="Windows WFP reference ABI is little-endian",
)
def test_native_ipv4_condition_uses_independent_wfp_host_order_value():
    spec = windows_wfp_module.FilterSpec(
        "v4",
        "permit",
        PERMIT_WEIGHT,
        b"app-id",
        "127.0.0.1",
        11434,
        "12345678-1234-5678-9abc-123456789abc",
    )

    native, references = windows_wfp_module._native_filter(spec)

    assert references
    address = next(
        condition
        for condition in native.filterCondition[: native.numFilterConditions]
        if condition.fieldKey.to_uuid()
        == windows_wfp_module.FWPM_CONDITION_IP_REMOTE_ADDRESS
    )
    assert address.conditionValue.uint32 == 0x0100007F
    assert windows_wfp_module._parse_native_filter(native, spec) == spec


@pytest.mark.parametrize(
    "mutation",
    ["match_type", "extra", "duplicate", "reordered"],
)
def test_native_parser_rejects_incomplete_condition_shape(mutation):
    spec = windows_wfp_module.FilterSpec(
        "v4",
        "permit",
        PERMIT_WEIGHT,
        b"app-id",
        "127.0.0.1",
        11434,
        "12345678-1234-5678-9abc-123456789abc",
    )
    native, references = windows_wfp_module._native_filter(spec)
    conditions = [
        native.filterCondition[index]
        for index in range(native.numFilterConditions)
    ]
    if mutation == "match_type":
        conditions[0].matchType = 99
    elif mutation == "extra":
        extra = windows_wfp_module.FWPM_FILTER_CONDITION0()
        extra.fieldKey = windows_wfp_module.GUID.from_uuid(uuid.uuid4())
        extra.matchType = windows_wfp_module.FWP_MATCH_EQUAL
        extra.conditionValue.type = windows_wfp_module.FWP_UINT16
        extra.conditionValue.uint16 = 1
        conditions.append(extra)
    elif mutation == "duplicate":
        conditions.append(conditions[0])
    else:
        conditions.reverse()
    condition_array = (
        windows_wfp_module.FWPM_FILTER_CONDITION0 * len(conditions)
    )(*conditions)
    references.append(condition_array)
    native.numFilterConditions = len(conditions)
    native.filterCondition = condition_array

    with pytest.raises(ContainmentError, match="condition shape"):
        windows_wfp_module._parse_native_filter(native, spec)


def test_live_design_proves_reachability_before_installing_wfp():
    path = (
        Path(__file__).resolve().parent
        / "test_agent_eval_live_containment.py"
    )
    spec = importlib.util.spec_from_file_location(
        "agent_eval_live_containment_design",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = inspect.getsource(
        module.test_real_wfp_allows_only_exact_loopback_endpoint_and_cleans_up
    )

    assert source.index("_prove_uncontained_baseline(") < source.index(
        "WindowsWfpSession.open("
    )
    assert "198.51.100.1" not in source
    assert "2001:db8::1" not in source
    assert ".invalid" not in source


def test_diagnostic_provider_is_explicitly_unsupported():
    provider = select_containment_provider(
        EvidenceMode.DIAGNOSTIC,
        "nt",
        "http://127.0.0.1:11434",
        [],
    )
    result = containment_module.derive_containment_result(
        EvidenceMode.DIAGNOSTIC,
        provider,
        "http://127.0.0.1:11434",
        [],
    )
    assert result.state is EvidenceState.UNSUPPORTED
    assert result.details["active"] is False
    provider.close()


def test_verified_non_windows_provider_fails_closed():
    with pytest.raises(ContainmentError, match="requires Windows"):
        select_containment_provider(
            EvidenceMode.VERIFIED,
            "posix",
            "http://127.0.0.1:11434",
            [],
        )


def test_verified_evidence_rejects_nonproduction_provider():
    forged = SimpleNamespace(
        launcher=lambda *_args, **_kwargs: None,
        verify_active=lambda: EvidenceControlResult(
            "os_loopback_only_egress",
            EvidenceState.PASS,
            "forged",
            {"active": True, "verified_complete_shape": True},
        ),
        close=lambda: None,
    )

    with pytest.raises(ContainmentError, match="production Windows"):
        containment_module.derive_containment_result(
            EvidenceMode.VERIFIED,
            forged,
            "http://127.0.0.1:11434",
            [],
        )


def test_verified_windows_provider_owns_wfp_and_job_launcher(tmp_path):
    api = FakeWfpApi()
    provider = select_containment_provider(
        EvidenceMode.VERIFIED,
        "nt",
        "http://127.0.0.1:11434",
        [identity(tmp_path / "app.exe")],
        wfp_api=api,
    )
    application = provider.session.applications[0]
    result = containment_module.derive_containment_result(
        EvidenceMode.VERIFIED,
        provider,
        "http://127.0.0.1:11434",
        [application],
    )
    assert result.state is EvidenceState.PASS
    assert result.details["verified_complete_shape"] is True
    assert getattr(provider.launcher, "_windows_job_launcher") is True
    provider.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows WFP ABI contract")
def test_fwpuclnt_bindings_are_exact_and_explicit():
    api = windows_wfp_module._FwpuclntApi()
    w = windows_wfp_module.wintypes
    expected = {
        "_engine_open": 5,
        "_engine_close": 1,
        "_sublayer_add": 3,
        "_sublayer_delete": 2,
        "_filter_add": 4,
        "_filter_delete": 2,
        "_filter_get": 3,
        "_get_app_id": 2,
        "_free_memory": 1,
    }
    assert set(vars(api)) == {*expected, "_native_expected"}
    for name, count in expected.items():
        function = getattr(api, name)
        assert function.argtypes is not None
        assert len(function.argtypes) == count
        assert function.restype in {w.DWORD, None}
    assert {
        name: ctypes.sizeof(getattr(windows_wfp_module, name))
        for name in (
            "GUID",
            "FWP_BYTE_BLOB",
            "FWP_VALUE0",
            "FWP_CONDITION_VALUE0",
            "FWPM_DISPLAY_DATA0",
            "FWPM_SESSION0",
            "FWPM_SUBLAYER0",
            "FWPM_ACTION0",
            "FWPM_FILTER_CONDITION0",
            "FWPM_FILTER0",
        )
    } == {
        "GUID": 16,
        "FWP_BYTE_BLOB": 16,
        "FWP_VALUE0": 16,
        "FWP_CONDITION_VALUE0": 16,
        "FWPM_DISPLAY_DATA0": 16,
        "FWPM_SESSION0": 72,
        "FWPM_SUBLAYER0": 72,
        "FWPM_ACTION0": 20,
        "FWPM_FILTER_CONDITION0": 40,
        "FWPM_FILTER0": 200,
    }
    assert {
        name: getattr(windows_wfp_module.FWPM_FILTER0, name).offset
        for name in (
            "filterKey",
            "displayData",
            "flags",
            "providerKey",
            "providerData",
            "layerKey",
            "subLayerKey",
            "weight",
            "numFilterConditions",
            "filterCondition",
            "action",
            "context",
            "reserved",
            "filterId",
            "effectiveWeight",
        )
    } == {
        "filterKey": 0,
        "displayData": 16,
        "flags": 32,
        "providerKey": 40,
        "providerData": 48,
        "layerKey": 64,
        "subLayerKey": 80,
        "weight": 96,
        "numFilterConditions": 112,
        "filterCondition": 120,
        "action": 128,
        "context": 152,
        "reserved": 168,
        "filterId": 176,
        "effectiveWeight": 184,
    }
