"""Privileged adversarial checks for the real Windows containment provider.

This module is inert in ordinary suites. It runs only when the caller selects
``-m live_containment`` and never skips a selected run merely for elevation.
"""
from __future__ import annotations

import base64
import ctypes
import ipaddress
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple

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


def _powershell_identity():
    candidate = shutil.which("powershell.exe")
    if not candidate:
        pytest.fail(
            "powershell.exe is required for the direct DNS containment probe"
        )
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


class _DnsAttempt(NamedTuple):
    transaction_id: int
    query_name: str
    packet: bytes


def _required_dns_target() -> tuple[str, int]:
    server_text = os.environ.get("LAC_WFP_LIVE_DNS_SERVER", "").strip()
    if not server_text:
        pytest.fail(
            "live containment precondition unavailable: set "
            "LAC_WFP_LIVE_DNS_SERVER to a reachable literal DNS server IP",
            pytrace=False,
        )
    try:
        server = str(ipaddress.ip_address(server_text))
    except ValueError:
        pytest.fail(
            "live containment precondition unavailable: "
            "LAC_WFP_LIVE_DNS_SERVER must be a literal IP address",
            pytrace=False,
        )
    port_text = os.environ.get("LAC_WFP_LIVE_DNS_PORT", "53").strip()
    try:
        port = int(port_text)
    except ValueError:
        port = 0
    if port != 53:
        pytest.fail(
            "live containment precondition unavailable: "
            "LAC_WFP_LIVE_DNS_PORT must be 53",
            pytrace=False,
        )
    return server, port


def _dns_tcp_query(transaction_id: int, query_name: str) -> bytes:
    labels = query_name.rstrip(".").split(".")
    if (
        not labels
        or any(
            not label
            or len(label.encode("ascii")) > 63
            for label in labels
        )
    ):
        raise ValueError("DNS query name has an invalid label")
    question = b"".join(
        bytes((len(label.encode("ascii")),)) + label.encode("ascii")
        for label in labels
    )
    question += b"\0" + struct.pack("!HH", 1, 1)
    message = (
        struct.pack("!HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
        + question
    )
    return message


def _new_dns_attempt(
    _scratch: Path,
    previous: _DnsAttempt | None = None,
) -> _DnsAttempt:
    while True:
        token = uuid.uuid4()
        transaction_id = (
            (token.bytes[0] % 255) << 8
        ) | (token.bytes[1] % 255)
        query_name = f"{token.hex}.lac-wfp-probe.invalid"
        if previous is None or (
            transaction_id != previous.transaction_id
            and query_name != previous.query_name
        ):
            break
    return _DnsAttempt(
        transaction_id,
        query_name,
        _dns_tcp_query(transaction_id, query_name),
    )


def _validate_dns_response(response: bytes, transaction_id: int) -> int:
    if len(response) < 12:
        raise ValueError("DNS response is shorter than its header")
    response_id, flags = struct.unpack("!HH", response[:4])
    if response_id != transaction_id:
        raise ValueError("DNS response transaction ID did not match")
    if not flags & 0x8000:
        raise ValueError("DNS response did not set the response bit")
    rcode = flags & 0x000F
    if rcode not in {0, 2, 3, 5}:
        raise ValueError(f"DNS response returned unacceptable rcode {rcode}")
    return rcode


def _run_direct_dns_attempt(
    executable: Path,
    server: str,
    port: int,
    attempt: _DnsAttempt,
) -> tuple[int, int | None, bytes]:
    packet = base64.b64encode(attempt.packet).decode("ascii")
    script = f"""
$ErrorActionPreference = "Stop"
$server = [System.Net.IPAddress]::Parse("{server}")
$port = {port}
$packet = [Convert]::FromBase64String("{packet}")
$client = [System.Net.Sockets.UdpClient]::new($server.AddressFamily)
try {{
    $client.Client.SendTimeout = 2000
    $client.Client.ReceiveTimeout = 4000
    $client.Connect($server, $port)
    [void]$client.Send($packet, $packet.Length)
    if ($server.AddressFamily -eq
        [System.Net.Sockets.AddressFamily]::InterNetworkV6) {{
        $anyAddress = [System.Net.IPAddress]::IPv6Any
    }} else {{
        $anyAddress = [System.Net.IPAddress]::Any
    }}
    $remote = [System.Net.IPEndPoint]::new($anyAddress, 0)
    $response = $client.Receive([ref]$remote)
    [Console]::Out.WriteLine(
        [Convert]::ToBase64String($response)
    )
}} catch [System.Net.Sockets.SocketException] {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 28
}} finally {{
    $client.Dispose()
}}
"""
    encoded_script = base64.b64encode(
        script.encode("utf-16-le")
    ).decode("ascii")
    process = WindowsJobProcess.start(
        [
            str(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded_script,
        ],
        cwd=Path.cwd(),
        env=dict(os.environ),
    )
    try:
        try:
            returncode = process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.terminate_tree()
            returncode = 124
        stdout = process.stdout.read()
        stderr = process.stderr.read()
    finally:
        process.close()
    if stdout:
        try:
            response = base64.b64decode(stdout.strip(), validate=True)
        except ValueError as exc:
            raise ValueError(
                "direct DNS probe returned invalid base64"
            ) from exc
        rcode = _validate_dns_response(
            response,
            attempt.transaction_id,
        )
    else:
        rcode = None
    return returncode, rcode, stderr


def _prove_direct_dns_baseline(
    executable: Path,
    server: str,
    port: int,
    scratch: Path,
) -> _DnsAttempt:
    attempt = _new_dns_attempt(scratch)
    try:
        returncode, rcode, stderr = _run_direct_dns_attempt(
            executable,
            server,
            port,
            attempt,
        )
    except ValueError as exc:
        pytest.fail(
            "live containment precondition unavailable: "
            f"direct DNS response was invalid: {exc}",
            pytrace=False,
        )
    if rcode is None:
        detail = stderr.decode("utf-8", errors="replace").strip()
        pytest.fail(
            "live containment precondition unavailable: direct DNS probe "
            f"to {server}:{port} returned no validated response "
            f"(exit {returncode})"
            + (f": {detail}" if detail else ""),
            pytrace=False,
        )
    return attempt


def _prove_direct_dns_blocked(
    executable: Path,
    server: str,
    port: int,
    scratch: Path,
    baseline: _DnsAttempt,
) -> _DnsAttempt:
    attempt = _new_dns_attempt(scratch, baseline)
    try:
        returncode, rcode, stderr = _run_direct_dns_attempt(
            executable,
            server,
            port,
            attempt,
        )
    except ValueError as exc:
        pytest.fail(
            "live containment DNS proof unavailable: "
            f"contained response was invalid: {exc}",
            pytrace=False,
        )
    if rcode is not None:
        pytest.fail(
            "direct DNS remained reachable under WFP containment: "
            f"{server}:{port} returned rcode {rcode}",
            pytrace=False,
        )
    if returncode != 28:
        detail = stderr.decode("utf-8", errors="replace").strip()
        pytest.fail(
            "live containment DNS proof unavailable: direct DNS probe "
            f"returned unexpected exit {returncode} without a validated "
            "response"
            + (f": {detail}" if detail else ""),
            pytrace=False,
        )
    return attempt


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
    dns_identity = _powershell_identity()
    dns_server, dns_port = _required_dns_target()
    dns_baseline = _prove_direct_dns_baseline(
        dns_identity.path,
        dns_server,
        dns_port,
        tmp_path,
    )

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
    proxy_target_url = os.environ.get(
        "LAC_WFP_LIVE_PROXY_TARGET_URL",
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
        (
            "configured proxy",
            proxy_target_url,
            proxy_environment,
            True,
        ),
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
    session = WindowsWfpSession.open(
        endpoint,
        [identity, dns_identity],
        api=api,
    )
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
        _prove_direct_dns_blocked(
            dns_identity.path,
            dns_server,
            dns_port,
            tmp_path,
            dns_baseline,
        )
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
