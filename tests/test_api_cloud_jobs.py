from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest

from backend.cloud_session import CloudSessionError


JOB_ID = "00000000-0000-4000-8000-000000000001"
WORKSPACE_ID = "00000000-0000-4000-8000-000000000002"


def job_list_payload():
    return {
        "jobs": [
            {
                "id": JOB_ID,
                "workspace_id": WORKSPACE_ID,
                "model_alias": "fast",
                "status": "running",
                "reserved_credits": 25,
                "actual_credits": None,
                "failure_code": None,
                "created_at": 1_700_000_000,
                "updated_at": 1_700_000_001,
                "started_at": 1_700_000_001,
                "finished_at": None,
            }
        ]
    }


def job_events_payload():
    event = {
        "event_id": "event_01",
        "sequence": 1,
        "phase": "running",
        "message": "Hosted job started",
        "percent": 10,
        "occurred_at": 1_700_000_001_000,
    }
    return {
        "job": {
            "id": JOB_ID,
            "revision": 1,
            "phase": "running",
            "latest_sequence": 1,
            "latest_progress": event,
            "pending_approval": None,
            "last_approval": None,
        },
        "events": [event],
    }


class FakeCloudJobs:
    def __init__(self):
        self.calls = []

    def list_jobs(self):
        self.calls.append(("list",))
        return job_list_payload()

    def job_events(self, job_id, after_sequence):
        self.calls.append(("events", job_id, after_sequence))
        return job_events_payload()

    def cancel_job(self, job_id):
        self.calls.append(("cancel", job_id))
        return {"job": {"id": job_id, "status": "cancelling"}}


def test_cloud_job_proxy_routes_are_loopback_only_exact_and_no_store(monkeypatch, flask_app):
    from backend import api as api_mod

    cloud = FakeCloudJobs()
    monkeypatch.setattr(api_mod, "_cloud_session", cloud)
    client = flask_app.test_client()

    listed = client.get("/api/cloud/jobs")
    events = client.get(f"/api/cloud/jobs/{JOB_ID}/events?after_sequence=0")
    cancelled = client.post(f"/api/cloud/jobs/{JOB_ID}/cancel")

    assert listed.status_code == 200
    assert listed.get_json() == job_list_payload()
    assert events.status_code == 200
    assert events.get_json() == job_events_payload()
    assert cancelled.status_code == 202
    assert cancelled.get_json() == {"job": {"id": JOB_ID, "status": "cancelling"}}
    assert all(response.headers["Cache-Control"] == "no-store" for response in (listed, events, cancelled))
    assert cloud.calls == [("list",), ("events", JOB_ID, 0), ("cancel", JOB_ID)]


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/cloud/jobs/not-a-uuid/events?after_sequence=0", "GET"),
        (f"/api/cloud/jobs/{JOB_ID}/events?after_sequence=-2", "GET"),
        (f"/api/cloud/jobs/{JOB_ID}/events?after_sequence=1&after_sequence=2", "GET"),
        (f"/api/cloud/jobs/{JOB_ID}/events?after_sequence=0&extra=1", "GET"),
        ("/api/cloud/jobs/not-a-uuid/cancel", "POST"),
    ],
)
def test_cloud_job_proxy_rejects_ambiguous_or_invalid_identifiers(
    monkeypatch, flask_app, path, method
):
    from backend import api as api_mod

    cloud = FakeCloudJobs()
    monkeypatch.setattr(api_mod, "_cloud_session", cloud)

    response = flask_app.test_client().open(path, method=method)

    assert response.status_code == 400
    assert response.get_json() == {"error": {"code": "invalid_request"}}
    assert response.headers["Cache-Control"] == "no-store"
    assert cloud.calls == []


@pytest.mark.parametrize("transfer_encoding", [None, "chunked"])
def test_cloud_cancel_rejects_unframed_bodies_after_reading_at_most_one_byte(
    monkeypatch, flask_app, transfer_encoding
):
    from backend import api as api_mod

    class OneByteInput(BytesIO):
        def read(self, size=-1):
            if size < 0 or size > 1:
                raise AssertionError(f"cancel route attempted an unbounded read: {size}")
            return super().read(size)

        def readinto(self, buffer):
            if len(buffer) > 1:
                raise AssertionError(
                    f"cancel route attempted an oversized read: {len(buffer)}"
                )
            return super().readinto(buffer)

    cloud = FakeCloudJobs()
    monkeypatch.setattr(api_mod, "_cloud_session", cloud)
    body = OneByteInput(b"x" * 1024)
    environ = {
        "CONTENT_LENGTH": "",
        "wsgi.input": body,
        "wsgi.input_terminated": True,
    }
    if transfer_encoding is not None:
        environ["HTTP_TRANSFER_ENCODING"] = transfer_encoding

    response = flask_app.test_client().post(
        f"/api/cloud/jobs/{JOB_ID}/cancel",
        environ_overrides=environ,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": {"code": "invalid_request"}}
    assert response.headers["Cache-Control"] == "no-store"
    assert body.tell() == 1
    assert cloud.calls == []


@pytest.mark.parametrize(
    "code,status",
    [
        ("auth_required", 401),
        ("quota_exhausted", 402),
        ("entitlement_required", 403),
        ("conflict_or_concurrency", 409),
        ("abuse_rate_limited", 429),
        ("provider_unavailable", 503),
        ("invalid_response", 502),
    ],
)
def test_cloud_job_proxy_maps_stable_cloud_failures(monkeypatch, flask_app, code, status):
    from backend import api as api_mod

    monkeypatch.setattr(
        api_mod,
        "_cloud_session",
        SimpleNamespace(
            list_jobs=lambda: (_ for _ in ()).throw(CloudSessionError(code))
        ),
    )

    response = flask_app.test_client().get("/api/cloud/jobs")

    assert response.status_code == status
    assert response.get_json() == {"error": {"code": code}}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "path,method,json_body",
    [
        ("/api/product/state", "GET", None),
        ("/api/cloud/auth/start", "POST", {"provider": "google"}),
        (
            "/api/cloud/auth/callback",
            "POST",
            {"callback_uri": "lac://oauth/callback?code=" + "c" * 43},
        ),
        ("/api/cloud/logout", "POST", None),
        ("/api/cloud/jobs", "GET", None),
        (f"/api/cloud/jobs/{JOB_ID}/events?after_sequence=0", "GET", None),
        (f"/api/cloud/jobs/{JOB_ID}/cancel", "POST", None),
    ],
)
def test_every_cloud_bearing_route_rejects_a_non_loopback_client(
    monkeypatch, flask_app, path, method, json_body
):
    from backend import api as api_mod

    class NeverCalled:
        def __getattr__(self, name):
            raise AssertionError(f"Cloud session method reached over LAN: {name}")

    monkeypatch.setattr(api_mod, "_cloud_session", NeverCalled())
    response = flask_app.test_client().open(
        path,
        method=method,
        json=json_body,
        environ_overrides={"REMOTE_ADDR": "192.0.2.25"},
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": {"code": "local_request_required"}}
    assert response.headers["Cache-Control"] == "no-store"
