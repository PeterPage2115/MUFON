"""Secret-free live smoke tests for a deployed dashboard (Task 2 of the
Iteration 4 plan).

These tests hit a RUNNING deployment (e.g. http://192.168.1.164:8099) and
verify only read-only, non-mutating contracts:

- `/healthz` answers 200;
- the static UI is served and references the LOCAL vendored Chart.js
  (the offline contract, same as TestStaticAssets);
- `/api/auth/status` is public and reports the auth method;
- protected `/api/*` routes reject anonymous requests (401);
- with `DASHBOARD_TOKEN` set, protected routes answer 200.

Skipped unless `DASHBOARD_LIVE_URL` is set — local runs and CI never touch a
deployment. Never prints or logs the token, never calls `PUT /api/settings`
or `POST /api/actions/*`. The token is read from the environment ONLY.
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("DASHBOARD_LIVE_URL"),
        reason="DASHBOARD_LIVE_URL not set (live smoke is opt-in)",
    ),
]

BASE = os.environ.get("DASHBOARD_LIVE_URL", "").rstrip("/")
TOKEN = os.environ.get("DASHBOARD_TOKEN", "")


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=15.0)


def test_healthz_ok() -> None:
    with _client() as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_served_with_local_chartjs() -> None:
    with _client() as client:
        index = client.get("/")
        chart = client.get("/static/vendor/chart.umd.min.js")
    assert index.status_code == 200
    assert "/static/app.js" in index.text
    assert "/static/vendor/chart.umd.min.js" in index.text
    assert "cdn.jsdelivr.net" not in index.text
    assert "https://cdn" not in index.text
    assert chart.status_code == 200
    assert b"Chart.js" in chart.content


def test_auth_status_public() -> None:
    with _client() as client:
        resp = client.get("/api/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["method"] in {"token", "oauth", "none"}


def test_protected_routes_reject_anonymous() -> None:
    with _client() as client:
        for path in ("/api/status", "/api/analysis/dates", "/api/settings", "/api/logs"):
            resp = client.get(path)
            assert resp.status_code == 401, f"{path} should 401 without a session"


@pytest.mark.skipif(not TOKEN, reason="DASHBOARD_TOKEN not set")
def test_protected_routes_work_with_token() -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with _client() as client:
        status = client.get("/api/status", headers=headers)
        dates = client.get("/api/analysis/dates", headers=headers)
    assert status.status_code == 200
    assert status.json()["snapshot_date"] is not None
    assert dates.status_code == 200
    assert isinstance(dates.json()["dates"], list)
