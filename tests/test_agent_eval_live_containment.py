"""Privileged adversarial checks for the real Windows containment provider.

This module is inert in ordinary suites. It runs only when the caller selects
``-m live_containment`` and never skips a selected run merely for elevation.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.agent_eval.identity import file_identity
from backend.agent_eval.windows_job import WindowsJobProcess
from backend.agent_eval.windows_wfp import (
    WindowsWfpSession,
    _FwpuclntApi,
)


pytestmark = [
    pytest.mark.live,
    pytest.mark.live_containment,
    pytest.mark.skipif(
        os.name != "nt",
        reason="Windows WFP reference provider",
    ),
]


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler contract
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"contained-ok")

    def log_message(self, _format, *_args):
        return None


def _explicitly_selected(request) -> None:
    if "live_containment" not in request.config.option.markexpr:
        pytest.skip("select explicitly with -m live_containment")


def _require_elevation() -> None:
    if not bool(ctypes.windll.shell32.IsUserAnAdmin()):
        pytest.fail(
            "Verified Windows network containment requires an elevated "
            "terminal. Reopen PowerShell as Administrator and rerun the "
            "exact live_containment command.",
            pytrace=False,
        )


def _curl_identity():
    candidate = shutil.which("curl.exe")
    if not candidate:
        pytest.fail("curl.exe is required for the live containment probe")
    return file_identity(
        Path(candidate).resolve(),
        version=None,
        authenticode_fn=lambda _path: "live-test-measured",
    )


def _curl(
    executable: Path,
    url: str,
    *,
    environment: dict[str, str] | None = None,
    use_proxy: bool = False,
) -> tuple[int, bytes, bytes]:
    process = WindowsJobProcess.start(
        [
            str(executable),
            "--noproxy",
            "" if use_proxy else "*",
            "--connect-timeout",
            "2",
            "--max-time",
            "4",
            "--silent",
            "--show-error",
            url,
        ],
        cwd=Path.cwd(),
        env=environment,
    )
    try:
        returncode = process.wait(timeout=6)
        stdout = process.stdout.read()
        stderr = process.stderr.read()
        return returncode, stdout, stderr
    finally:
        process.close()


def _assert_filters_absent(api: _FwpuclntApi, filter_ids: tuple[int, ...]) -> None:
    engine = api.engine_open_dynamic(uuid.uuid4())
    try:
        for filter_id in filter_ids:
            with pytest.raises(
                Exception,
                match="FwpmFilterGetById0.*returned",
            ):
                api.filter_get(engine, filter_id)
    finally:
        api.engine_close(engine)


def _prove_uncontained_baseline(
    executable: Path,
    probes: list[tuple[str, str, dict[str, str], bool]],
) -> None:
    for label, target, environment, use_proxy in probes:
        returncode, _stdout, stderr = _curl(
            executable,
            target,
            environment=environment,
            use_proxy=use_proxy,
        )
        if returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            pytest.fail(
                "live containment precondition unavailable: "
                f"{label} did not succeed before WFP installation"
                + (f": {detail}" if detail else ""),
                pytrace=False,
            )


def _forced_session_worker() -> None:
    executable = Path(os.environ["LAC_WFP_LIVE_EXECUTABLE"])
    endpoint = os.environ["LAC_WFP_LIVE_ENDPOINT"]
    ready = Path(os.environ["LAC_WFP_LIVE_READY"])
    identity = file_identity(
        executable,
        version=None,
        authenticode_fn=lambda _path: "live-test-measured",
    )
    session = WindowsWfpSession.open(endpoint, [identity])
    ready.write_text(json.dumps(list(session.filter_ids)), encoding="utf-8")
    while True:
        time.sleep(1)


def test_real_wfp_allows_only_exact_loopback_endpoint_and_cleans_up(
    request,
    tmp_path,
):
    _explicitly_selected(request)
    _require_elevation()
    identity = _curl_identity()

    allowed = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    denied = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (allowed, denied)
    ]
    for thread in threads:
        thread.start()

    endpoint = f"http://127.0.0.1:{allowed.server_port}"
    direct_environment = dict(os.environ)
    proxy_environment = dict(os.environ)
    proxy_url = f"http://127.0.0.1:{denied.server_port}"
    proxy_environment.update(
        HTTP_PROXY=proxy_url,
        HTTPS_PROXY=proxy_url,
        ALL_PROXY=proxy_url,
        NO_PROXY="",
    )
    public_ipv4_url = os.environ.get(
        "LAC_WFP_LIVE_PUBLIC_IPV4_URL",
        "http://1.1.1.1/",
    )
    dns_url = os.environ.get(
        "LAC_WFP_LIVE_DNS_URL",
        "http://example.com/",
    )
    denial_probes = [
        (
            "other loopback port",
            f"http://127.0.0.1:{denied.server_port}",
            direct_environment,
            False,
        ),
        ("public IPv4", public_ipv4_url, direct_environment, False),
        ("DNS hostname", dns_url, direct_environment, False),
        ("configured proxy", dns_url, proxy_environment, True),
    ]
    if socket.has_ipv6:
        denial_probes.append(
            (
                "public IPv6",
                os.environ.get(
                    "LAC_WFP_LIVE_PUBLIC_IPV6_URL",
                    "http://[2606:4700:4700::1111]/",
                ),
                direct_environment,
                False,
            )
        )
    _prove_uncontained_baseline(identity.path, denial_probes)

    api = _FwpuclntApi()
    session = WindowsWfpSession.open(endpoint, [identity], api=api)
    normal_filter_ids = session.filter_ids
    try:
        assert session.verify_active()["verified_complete_shape"] is True

        ok, body, _error = _curl(
            identity.path,
            endpoint,
            environment=dict(os.environ),
        )
        assert ok == 0
        assert body == b"contained-ok"

        for label, target, environment, use_proxy in denial_probes:
            returncode, _stdout, _stderr = _curl(
                identity.path,
                target,
                environment=environment,
                use_proxy=use_proxy,
            )
            assert returncode != 0, f"{label}: {target}"
    finally:
        try:
            session.close()
        finally:
            allowed.shutdown()
            denied.shutdown()
            allowed.server_close()
            denied.server_close()
    assert session.filter_ids == ()
    _assert_filters_absent(api, normal_filter_ids)

    ready = tmp_path / "forced-session.json"
    worker_environment = dict(os.environ)
    worker_environment.update(
        LAC_WFP_LIVE_EXECUTABLE=str(identity.path),
        LAC_WFP_LIVE_ENDPOINT=endpoint,
        LAC_WFP_LIVE_READY=str(ready),
    )
    worker = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--wfp-worker"],
        cwd=Path.cwd(),
        env=worker_environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and worker.poll() is None:
        if time.monotonic() >= deadline:
            worker.kill()
            worker.wait(timeout=5)
            pytest.fail("forced-session worker did not become ready")
        time.sleep(0.05)
    if not ready.exists():
        error = worker.stderr.read().decode("utf-8", errors="replace")
        pytest.fail(f"forced-session worker failed before readiness: {error}")
    forced_filter_ids = tuple(json.loads(ready.read_text(encoding="utf-8")))
    worker.kill()
    worker.wait(timeout=5)
    _assert_filters_absent(_FwpuclntApi(), forced_filter_ids)


if __name__ == "__main__" and "--wfp-worker" in sys.argv:
    _forced_session_worker()
