"""Tests for the dashboard API (task 12): factory, endpoints, validation,
auth middleware and the uvicorn bootstrap.

No real bot is involved: ``create_app`` gets injected fakes for the run
functions and the bot loop (a real event loop running in a thread for the
cross-loop dispatch tests). Sqlite is seeded per test via the store helpers.
The uvicorn bootstrap smoke test runs a real ``uvicorn.Server`` thread on a
free port and polls ``GET /api/status``.

allow: SIZE_OK — declarative test file (one tiny Given/When/Then test per
route/validation case), same precedent as test_bot_main/test_metrics.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal, cast
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from travian import store
from travian.bot import main as bot_main
from travian.dashboard import app as dashboard_app
from travian.dashboard import auth as dashboard_auth
from travian.dashboard.app import DashboardDeps, create_app, make_status_provider
from travian.models import VillageRow

CHANNEL_ID = 111111111111111111
SNAPSHOT_DATE = "2026-08-09"


# --- fakes and helpers ---------------------------------------------------------


class FakeRunFetch:
    """Records calls; returns a configurable status string (default: completed)."""

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.result = bot_main.FETCH_STATUS_COMPLETED

    async def __call__(self) -> str:
        self.calls.append(1)
        return self.result


class FakeRunReport:
    """Records (channel_id, require_today); returns a configurable status string."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []
        self.result = bot_main.REPORT_STATUS_SENT

    async def __call__(self, channel_id: int, require_today: bool = True) -> str:
        self.calls.append((channel_id, require_today))
        return self.result


class FakeSyncScheduler:
    """Async fake of the settings→scheduler callback; records the loop each
    call ran on (must be the bot loop, never the TestClient loop)."""

    def __init__(self, result: Literal["applied", "unchanged", "pending"] = "applied") -> None:
        self.result: Literal["applied", "unchanged", "pending"] = result
        self.calls: list[asyncio.AbstractEventLoop] = []

    async def __call__(self) -> Literal["applied", "unchanged", "pending"]:
        self.calls.append(asyncio.get_running_loop())
        return self.result


class RaisingSyncScheduler:
    async def __call__(self) -> Literal["applied", "unchanged", "pending"]:
        raise RuntimeError("boom")


class LoopThread:
    """A real asyncio event loop running in a background thread (the "bot loop")."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)

def _row(
    village_id: int,
    *,
    population: int = 100,
    player_id: int | None = None,
    player_name: str | None = None,
    alliance_id: int = 7,
    alliance_tag: str = "NOVA",
    region: str = "Testland",
) -> VillageRow:
    return VillageRow(
        village_id=village_id,
        x=village_id,
        y=village_id,
        tribe=1,
        name=f"Village {village_id}",
        player_id=1000 + village_id if player_id is None else player_id,
        player_name=f"Player {village_id}" if player_name is None else player_name,
        alliance_id=alliance_id,
        alliance_tag=alliance_tag,
        population=population,
        region=region,
        is_capital=False,
        is_city=False,
        is_harbor=False,
        victory_points=10,
    )


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "DISCORD_TOKEN": "test-token",
        "CHANNEL_ID": str(CHANNEL_ID),
        "ALLIANCE_TAGS": "NOVA",
        "FETCH_HOUR": "0",
        "FETCH_MINUTE": "15",
        "FETCH_TZ": "Europe/London",
        "REPORT_HOUR": "9",
        "REPORT_MINUTE": "0",
        "REPORT_TZ": "Europe/Warsaw",
        "SQLITE_PATH": "/tmp/unused.db",
        "DASHBOARD_BIND": "127.0.0.1",
        "DASHBOARD_PORT": "8090",
        "DASHBOARD_LOOPBACK_ONLY": "false",
        "DASHBOARD_TOKEN": "",
    }
    env.update(overrides)
    return env


def _config_getter(db: Path, env: dict[str, str]) -> Callable[[], bot_main.MergedConfig]:
    """The real merged-config getter pattern (same shape as main._current_config)."""

    def get_config() -> bot_main.MergedConfig:
        conn = store.connect(db)
        try:
            return bot_main.load_merged_config(conn, env)
        finally:
            conn.close()

    return get_config


def _deps(
    db: Path,
    env: dict[str, str],
    *,
    run_fetch: FakeRunFetch | None = None,
    run_report: FakeRunReport | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
    get_status: Callable[[], dashboard_app.StatusData] | None = None,
    sync_scheduler_fn: dashboard_app.SyncSchedulerFn | None = None,
    get_runtime_state: Callable[[], dashboard_app.RuntimeState] | None = None,
) -> DashboardDeps:
    env = dict(env)
    env["SQLITE_PATH"] = str(db)  # app endpoints open the db via env, like main()
    get_config = _config_getter(db, env)
    return DashboardDeps(
        get_status=get_status or make_status_provider(str(db), get_config),
        run_fetch_fn=run_fetch or FakeRunFetch(),
        run_report_fn=run_report or FakeRunReport(),
        bot_loop_getter=lambda: loop,
        get_config=get_config,
        get_runtime_state=get_runtime_state or (lambda: dashboard_app.RuntimeState(True, True)),
        env=env,
        sync_scheduler_fn=sync_scheduler_fn,
    )


def _app(
    db: Path,
    env: dict[str, str],
    *,
    run_fetch: FakeRunFetch | None = None,
    run_report: FakeRunReport | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
    get_status: Callable[[], dashboard_app.StatusData] | None = None,
    sync_scheduler_fn: dashboard_app.SyncSchedulerFn | None = None,
    get_runtime_state: Callable[[], dashboard_app.RuntimeState] | None = None,
) -> FastAPI:
    return create_app(
        _deps(
            db,
            env,
            run_fetch=run_fetch,
            run_report=run_report,
            loop=loop,
            get_status=get_status,
            sync_scheduler_fn=sync_scheduler_fn,
            get_runtime_state=get_runtime_state,
        )
    )


def _seed_db(db: Path, *, snapshot: bool = True) -> None:
    conn = store.connect(db)
    store.init_schema(conn)
    if snapshot:
        store.save_snapshot(
            conn,
            SNAPSHOT_DATE,
            [
                _row(1, population=100),
                _row(2, population=110),
                _row(3, population=50, player_id=1001, player_name="Player 1"),
            ],
        )
    conn.close()


def _seed_analysis_db(db: Path) -> None:
    """Three snapshot dates: our NOVA (id 7) + enemy (id 8) across two
    regions, plus one gained (id 7) and one lost (id 4) village on the last
    day. Region "Enemyland" is enemy-only (dropped from the series filter)."""
    conn = store.connect(db)
    store.init_schema(conn)

    def base(day_offset: int) -> list[VillageRow]:
        return [
            _row(1, population=2500 + day_offset),  # NOVA Testland
            _row(2, population=2500 + day_offset, player_id=1001, player_name="Player 1"),
            _row(3, population=2000, alliance_id=8, alliance_tag="ENEMY"),  # enemy Testland
            _row(5, population=100, region="Borders"),  # NOVA Borders
            _row(6, population=50, region="Borders", alliance_id=8, alliance_tag="ENEMY"),
            _row(8, population=900, region="Enemyland", alliance_id=8, alliance_tag="ENEMY"),
        ]

    store.save_snapshot(conn, "2026-08-07", base(0))
    store.save_snapshot(conn, "2026-08-08", base(1) + [_row(4, population=500)])  # lost on 08-09
    store.save_snapshot(conn, "2026-08-09", base(2) + [_row(7, population=600)])  # gained on 08-09
    conn.close()


def _seed_logs(db: Path) -> None:
    conn = store.connect(db)
    for message in ["err0", "warn1", "info2", "err3", "err4"]:
        level = "error" if message.startswith("err") else ("warning" if message.startswith("warn") else "info")
        store.append_log(conn, "fetch", level, message)
    conn.close()


def _db_settings(db: Path) -> dict[str, store.JsonValue]:
    conn = store.connect(db)
    try:
        return store.get_settings(conn)
    finally:
        conn.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --- GET / ---------------------------------------------------------------------


class TestIndex:
    def test_serves_static_index_html(self, tmp_path: Path, monkeypatch) -> None:
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")
        monkeypatch.setattr(dashboard_app, "STATIC_DIR", static)
        db = tmp_path / "i.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.get("/")
        assert resp.status_code == 200
        assert resp.text == "<html>dashboard</html>"
        assert resp.headers["content-type"].startswith("text/html")

    def test_404_when_static_not_built_yet(self, tmp_path: Path, monkeypatch) -> None:
        # Task 13 builds static/ — until then GET / must 404, never crash.
        monkeypatch.setattr(dashboard_app, "STATIC_DIR", tmp_path / "static")
        db = tmp_path / "i.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.get("/")
        assert resp.status_code == 404


class TestStaticAssets:
    def test_chartjs_served_locally_without_cdn(self, tmp_path: Path) -> None:
        """Offline contract: the index references the vendored Chart.js and no
        CDN host, and the vendored file is served with 200."""
        db = tmp_path / "s.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            index = client.get("/")
            chart = client.get("/static/vendor/chart.umd.min.js")
        assert index.status_code == 200
        assert "/static/vendor/chart.umd.min.js" in index.text
        assert "cdn.jsdelivr.net" not in index.text
        assert "https://cdn" not in index.text
        assert chart.status_code == 200
        assert chart.headers["content-type"].startswith(("application/javascript", "text/javascript"))
        assert b"Chart.js v4" in chart.content


# --- GET /api/status -----------------------------------------------------------


class TestStatus:
    def test_status_with_snapshot(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        _seed_db(db, snapshot=True)
        _seed_logs(db)
        with TestClient(_app(db, _env())) as client:
            body = client.get("/api/status").json()
        assert body["snapshot_date"] == SNAPSHOT_DATE
        assert body["snapshot_source"] == "map.sql"
        assert body["villages"] == 3
        assert body["players"] == 2  # rows 1+3 share player 1001
        assert body["alliances"] == 1  # all alliance_id 7
        assert body["total_population"] == 260
        assert (body["fetch_hour"], body["fetch_minute"], body["fetch_tz"]) == (0, 15, "Europe/London")
        assert (body["report_hour"], body["report_minute"], body["report_tz"]) == (9, 0, "Europe/Warsaw")
        next_fetch = datetime.fromisoformat(body["next_fetch"])
        assert (next_fetch.hour, next_fetch.minute) == (0, 15)
        assert next_fetch.timestamp() > time.time()  # the NEXT occurrence
        next_report = datetime.fromisoformat(body["next_report"])
        assert (next_report.hour, next_report.minute) == (9, 0)
        # errors: newest-first, only level == 'error', from the seeded 5 entries
        assert [e["message"] for e in body["errors"]] == ["err4", "err3", "err0"]
        assert all(e["level"] == "error" for e in body["errors"])
        assert body["alliance_tags"] == ["NOVA"]
        # No seeded row matches the success prefixes (the "info2" row is an
        # unrelated info message) → both last-success fields are null.
        assert body["last_successful_fetch"] is None
        assert body["last_successful_report"] is None

    def test_status_last_success_timestamps(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        _seed_db(db)
        conn = store.connect(db)
        store.append_log(conn, "fetch", "info", "snapshot saved for 2026-08-07 (3 villages)")
        store.append_log(conn, "report", "info", "report sent to channel 111111111111111111 (snapshot 2026-08-07)")
        store.append_log(conn, "fetch", "info", "snapshot saved for 2026-08-09 (3 villages)")  # newest fetch
        store.append_log(conn, "fetch", "error", "snapshot saved for 2026-08-10 (0 villages)")  # error: not a success
        conn.close()
        with TestClient(_app(db, _env())) as client:
            body = client.get("/api/status").json()

        # The persisted UTC ISO ts of the NEWEST matching success row wins;
        # unrelated levels never count.
        conn = store.connect(db)
        try:
            by_message = {e["message"]: e["ts"] for e in store.recent_logs(conn)}
        finally:
            conn.close()
        assert body["last_successful_fetch"] == by_message["snapshot saved for 2026-08-09 (3 villages)"]
        assert body["last_successful_fetch"].endswith("+00:00")
        assert body["last_successful_report"] == by_message[
            "report sent to channel 111111111111111111 (snapshot 2026-08-07)"
        ]
        # The existing freshness + error contract is preserved alongside the
        # new fields: the seeded error row still surfaces in errors, but it
        # never counts as a successful fetch.
        assert body["freshness"]["state"] == "stale"
        assert body["freshness"]["snapshot_date"] == SNAPSHOT_DATE
        assert [e["message"] for e in body["errors"]] == ["snapshot saved for 2026-08-10 (0 villages)"]

    def test_status_without_snapshot(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            body = client.get("/api/status").json()
        assert body["snapshot_date"] is None
        assert body["snapshot_source"] is None
        assert body["villages"] == 0
        assert body["players"] == 0
        assert body["alliances"] == 0
        assert body["total_population"] == 0
        assert body["errors"] == []
        assert body["alliance_tags"] == ["NOVA"]
        assert body["last_successful_fetch"] is None
        assert body["last_successful_report"] is None


# --- GET /api/settings ---------------------------------------------------------


class TestGetSettings:
    def test_merged_env_and_db(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        _seed_db(db)
        conn = store.connect(db)
        store.set_settings(conn, {"FETCH_HOUR": 5, "ALLIANCE_TAGS": ["DB"]})
        conn.close()
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/settings").json()
        assert payload["FETCH_HOUR"] == 5  # db overrides env
        assert payload["FETCH_MINUTE"] == 15  # env fallback
        assert payload["ALLIANCE_TAGS"] == ["DB"]
        assert payload["CHANNEL_ID"] == CHANNEL_ID
        assert payload["FETCH_TZ"] == "Europe/London"
        assert payload["REPORT_HOUR"] == 9
        assert payload["REPORT_MINUTE"] == 0
        assert payload["REPORT_TZ"] == "Europe/Warsaw"
        assert payload["ADMIN_ROLE_ID"] is None
        assert payload["REPORT_EMBED_COLOR"] == 0x2ECC71
        assert payload["TRACKED_ALLIANCES"] == []  # env fallback empty
        assert "DISCORD_TOKEN" not in payload  # secrets never leave the API

    def test_tracked_alliances_in_payload(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        _seed_db(db)
        conn = store.connect(db)
        store.set_settings(conn, {"TRACKED_ALLIANCES": ["UFO", "PR-U"]})
        conn.close()
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/settings").json()
        assert payload["TRACKED_ALLIANCES"] == ["UFO", "PR-U"]


# --- PUT /api/settings ---------------------------------------------------------


class TestPutSettings:
    def test_valid_subset_saved_and_returned(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"FETCH_HOUR": 5, "REPORT_EMBED_COLOR": 0x2ECC71})
        assert resp.status_code == 200
        body = resp.json()
        assert body["FETCH_HOUR"] == 5
        assert body["REPORT_EMBED_COLOR"] == 0x2ECC71
        assert body["FETCH_MINUTE"] == 15  # untouched env fallback
        assert _db_settings(db)["FETCH_HOUR"] == 5  # persisted (fresh connection)

    def test_discord_token_rejected_and_table_unchanged(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        conn = store.connect(db)
        store.set_settings(conn, {"FETCH_HOUR": 3})
        conn.close()
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"DISCORD_TOKEN": "sekret"})
        assert resp.status_code == 422
        assert "DISCORD_TOKEN" in resp.json()["detail"]
        assert _db_settings(db) == {"FETCH_HOUR": 3}  # unchanged, nothing written

    def test_backfill_dsn_and_sqlite_path_rejected(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.put("/api/settings", json={"BACKFILL_DSN": "postgres://x"}).status_code == 422
            assert client.put("/api/settings", json={"SQLITE_PATH": "/evil.db"}).status_code == 422
        assert _db_settings(db) == {}

    def test_alert_channel_id_env_only_rejected(self, tmp_path: Path) -> None:
        """ALERT_CHANNEL_ID is environment-only: the dashboard must refuse to
        store it (unknown setting), so operators configure the alert route
        only through the environment."""
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"ALERT_CHANNEL_ID": 12345})
        assert resp.status_code == 422
        assert "ALERT_CHANNEL_ID" in resp.json()["detail"]
        assert _db_settings(db) == {}  # nothing written

    def test_bad_hour_422_and_unchanged(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"FETCH_HOUR": 25})
        assert resp.status_code == 422
        assert "0 and 23" in resp.json()["detail"]
        assert _db_settings(db) == {}

    def test_bad_minute_422(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"REPORT_MINUTE": 60})
        assert resp.status_code == 422
        assert "0 and 59" in resp.json()["detail"]

    def test_bad_tz_422(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"FETCH_TZ": "Mars/Olympus"})
        assert resp.status_code == 422
        assert "Mars/Olympus" in resp.json()["detail"]

    def test_empty_tags_422(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"ALLIANCE_TAGS": []})
        assert resp.status_code == 422
        assert "env-only" in resp.json()["detail"]

    def test_whitespace_only_tags_422(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"ALLIANCE_TAGS": ["  ", ""]})
        assert resp.status_code == 422

    def test_tags_stripped_and_deduped(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"ALLIANCE_TAGS": [" A ", "A", "a", " b ", "A"]})
        assert resp.status_code == 200
        assert resp.json()["ALLIANCE_TAGS"] == ["A", "a", "b"]  # case-sensitive dedupe
        assert _db_settings(db)["ALLIANCE_TAGS"] == ["A", "a", "b"]

    def test_tags_must_be_list_of_strings(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.put("/api/settings", json={"ALLIANCE_TAGS": "NOVA"}).status_code == 422
            assert client.put("/api/settings", json={"ALLIANCE_TAGS": [1, 2]}).status_code == 422

    def test_tracked_alliances_saved_stripped_deduped(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"TRACKED_ALLIANCES": [" UFO ", "UFO", "AAA", "AAA"]})
        assert resp.status_code == 200
        assert resp.json()["TRACKED_ALLIANCES"] == ["UFO", "AAA"]
        assert _db_settings(db)["TRACKED_ALLIANCES"] == ["UFO", "AAA"]

    def test_tracked_alliances_empty_allowed(self, tmp_path: Path) -> None:
        """Empty TRACKED_ALLIANCES is legal (hides Standings) — unlike ALLIANCE_TAGS."""
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"TRACKED_ALLIANCES": []})
        assert resp.status_code == 200
        assert resp.json()["TRACKED_ALLIANCES"] == []

    def test_tracked_alliances_must_be_list_of_strings(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.put("/api/settings", json={"TRACKED_ALLIANCES": "UFO"}).status_code == 422
            assert client.put("/api/settings", json={"TRACKED_ALLIANCES": [1, 2]}).status_code == 422

    def test_bad_color_422(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"REPORT_EMBED_COLOR": 0x1FFFFFFF})
        assert resp.status_code == 422
        assert "0xFFFFFF" in resp.json()["detail"]

    def test_channel_id_must_be_int(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.put("/api/settings", json={"CHANNEL_ID": "12345"}).status_code == 422
            assert client.put("/api/settings", json={"CHANNEL_ID": 12.5}).status_code == 422
            assert client.put("/api/settings", json={"CHANNEL_ID": 12345}).status_code == 200

    def test_admin_role_id_null_and_int(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.put("/api/settings", json={"ADMIN_ROLE_ID": 42}).status_code == 200
            assert _db_settings(db)["ADMIN_ROLE_ID"] == 42
            assert client.put("/api/settings", json={"ADMIN_ROLE_ID": None}).status_code == 200
            assert _db_settings(db)["ADMIN_ROLE_ID"] is None  # null removes the override
            assert client.put("/api/settings", json={"ADMIN_ROLE_ID": "42"}).status_code == 422

    def test_invalid_json_body_422(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", content="not-json", headers={"content-type": "application/json"})
        assert resp.status_code == 422

    def test_atomic_no_partial_write(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"FETCH_HOUR": 5, "DISCORD_TOKEN": "x"})
        assert resp.status_code == 422
        assert _db_settings(db) == {}  # the valid key was NOT written

    def test_non_schedule_write_returns_not_needed(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.put("/api/settings", json={"REPORT_EMBED_COLOR": 0x2ECC71})
        assert resp.status_code == 200
        assert resp.json()["schedule_sync"] == "not_needed"

    def test_schedule_write_without_loop_returns_pending(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:  # no loop, no sync fn
            resp = client.put("/api/settings", json={"FETCH_HOUR": 5})
        assert resp.status_code == 200
        assert resp.json()["schedule_sync"] == "pending"
        assert resp.json()["FETCH_HOUR"] == 5  # settings payload intact

    def test_schedule_sync_runs_on_bot_loop(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        loop_thread = LoopThread()
        fake = FakeSyncScheduler()
        try:
            with TestClient(_app(db, _env(), loop=loop_thread.loop, sync_scheduler_fn=fake)) as client:
                resp = client.put("/api/settings", json={"FETCH_HOUR": 5})
            assert resp.status_code == 200
            assert resp.json()["schedule_sync"] == "applied"
            assert fake.calls == [loop_thread.loop]  # bot loop, NOT the TestClient loop
        finally:
            loop_thread.stop()

    def test_schedule_sync_unchanged_result_passthrough(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        loop_thread = LoopThread()
        fake = FakeSyncScheduler(result="unchanged")
        try:
            with TestClient(_app(db, _env(), loop=loop_thread.loop, sync_scheduler_fn=fake)) as client:
                resp = client.put("/api/settings", json={"FETCH_MINUTE": 30})
            assert resp.json()["schedule_sync"] == "unchanged"
        finally:
            loop_thread.stop()

    def test_schedule_sync_failure_keeps_setting_and_logs_error(self, tmp_path: Path) -> None:
        db = tmp_path / "p.db"
        _seed_db(db)
        loop_thread = LoopThread()
        try:
            with TestClient(_app(db, _env(), loop=loop_thread.loop, sync_scheduler_fn=RaisingSyncScheduler())) as client:
                resp = client.put("/api/settings", json={"FETCH_HOUR": 6})
            assert resp.status_code == 200
            assert resp.json()["schedule_sync"] == "failed"
            assert _db_settings(db)["FETCH_HOUR"] == 6  # saved config NOT rolled back
            conn = store.connect(db)
            try:
                logs = store.recent_logs(conn, 10)
            finally:
                conn.close()
            assert any(
                entry["job"] == "config"
                and entry["level"] == "error"
                and entry["message"].startswith("scheduler sync failed: ")
                for entry in logs
            )
        finally:
            loop_thread.stop()



# --- POST /api/actions/* -------------------------------------------------------


class TestActions:
    def test_fetch_without_bot_loop_409(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.post("/api/actions/fetch")
        assert resp.status_code == 409
        assert resp.json() == {"error": "bot not ready"}

    def test_fetch_dispatches_to_bot_loop(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed_db(db)
        run_fetch = FakeRunFetch()
        loop_thread = LoopThread()
        try:
            with TestClient(_app(db, _env(), run_fetch=run_fetch, loop=loop_thread.loop)) as client:
                resp = client.post("/api/actions/fetch")
        finally:
            loop_thread.stop()
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "message": bot_main.FETCH_STATUS_COMPLETED}
        assert run_fetch.calls == [1]

    def test_fetch_status_string_passthrough(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed_db(db)
        run_fetch = FakeRunFetch()
        run_fetch.result = bot_main.FETCH_STATUS_SKIPPED
        loop_thread = LoopThread()
        try:
            with TestClient(_app(db, _env(), run_fetch=run_fetch, loop=loop_thread.loop)) as client:
                resp = client.post("/api/actions/fetch")
        finally:
            loop_thread.stop()
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "message": bot_main.FETCH_STATUS_SKIPPED}

    def test_report_without_channel_409(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed_db(db)
        env = _env()
        del env["CHANNEL_ID"]
        loop_thread = LoopThread()  # loop present → the channel check is reached
        try:
            with TestClient(_app(db, env, loop=loop_thread.loop)) as client:
                resp = client.post("/api/actions/report")
        finally:
            loop_thread.stop()
        assert resp.status_code == 409
        assert resp.json() == {"error": "CHANNEL_ID not configured"}

    def test_report_dispatches_merged_channel_require_today_false(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed_db(db)
        run_report = FakeRunReport()
        loop_thread = LoopThread()
        try:
            with TestClient(_app(db, _env(), run_report=run_report, loop=loop_thread.loop)) as client:
                resp = client.post("/api/actions/report")
        finally:
            loop_thread.stop()
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "message": bot_main.REPORT_STATUS_SENT}
        assert run_report.calls == [(CHANNEL_ID, False)]  # require_today=False like /raport

    def test_report_channel_from_db_settings(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed_db(db)
        env = _env()
        del env["CHANNEL_ID"]  # channel comes from the settings table instead
        conn = store.connect(db)
        store.set_settings(conn, {"CHANNEL_ID": 999})
        conn.close()
        run_report = FakeRunReport()
        loop_thread = LoopThread()
        try:
            with TestClient(_app(db, env, run_report=run_report, loop=loop_thread.loop)) as client:
                resp = client.post("/api/actions/report")
        finally:
            loop_thread.stop()
        assert resp.status_code == 200
        assert run_report.calls == [(999, False)]


# --- GET /api/logs -------------------------------------------------------------


class TestLogs:
    def test_n_newest_first(self, tmp_path: Path) -> None:
        db = tmp_path / "l.db"
        _seed_db(db, snapshot=False)
        _seed_logs(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.get("/api/logs", params={"n": 3})
        assert resp.status_code == 200
        assert [row["message"] for row in resp.json()] == ["err4", "err3", "info2"]
        assert set(resp.json()[0]) == {"ts", "job", "level", "message"}

    def test_default_n_50(self, tmp_path: Path) -> None:
        db = tmp_path / "l.db"
        _seed_db(db, snapshot=False)
        _seed_logs(db)
        with TestClient(_app(db, _env())) as client:
            assert len(client.get("/api/logs").json()) == 5

    def test_n_out_of_range_422(self, tmp_path: Path) -> None:
        db = tmp_path / "l.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/logs", params={"n": 0}).status_code == 422
            assert client.get("/api/logs", params={"n": -1}).status_code == 422


# --- auth middleware -----------------------------------------------------------


# --- analysis endpoints (report trim) ----------------------------------------


class TestAnalysisRegions:
    def test_payload_shape_with_current_control_fields(self, tmp_path: Path) -> None:
        db = tmp_path / "ar.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/regions?days=7").json()

        assert payload["dates"] == ["2026-08-07", "2026-08-08", "2026-08-09"]
        # Enemy-only region dropped from the series; our regions keep every point.
        assert set(payload["series"]) == {"Testland", "Borders"}
        assert payload["series"]["Testland"][0] == {
            "date": "2026-08-07",
            "share": 5000 / 7000,
            "our_pop": 5000,
            "total_pop": 7000,
        }
        # current = latest-pair region_stats (report semantics) + derived fields.
        current = payload["current"]
        assert [row["region"] for row in current] == ["Testland", "Borders"]
        testland = current[0]
        assert testland["active"] is True
        assert testland["controlled"] is True
        assert testland["to50_needed"] == (7604 // 2) + 1 - 5604
        borders = current[1]
        assert borders["active"] is False
        assert borders["controlled"] is False
        assert borders["to50_needed"] is None

    def test_days_window_trims_series(self, tmp_path: Path) -> None:
        db = tmp_path / "ar.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/regions?days=2").json()

        assert payload["dates"] == ["2026-08-08", "2026-08-09"]
        assert [p["date"] for p in payload["series"]["Testland"]] == ["2026-08-08", "2026-08-09"]

    def test_days_out_of_range_422(self, tmp_path: Path) -> None:
        db = tmp_path / "ar.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/regions?days=1").status_code == 422
            assert client.get("/api/analysis/regions?days=61").status_code == 422

    def test_empty_db_empty_payload(self, tmp_path: Path) -> None:
        db = tmp_path / "ar.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/regions?days=7").json() == {
                "dates": [],
                "series": {},
                "current": [],
                "top_alliances": {},
            }

    def test_alliance_filter_per_tag(self, tmp_path: Path) -> None:
        db = tmp_path / "ar.db"
        _seed_analysis_db(db)
        env = _env(ALLIANCE_TAGS="NOVA,ENEMY")
        with TestClient(_app(db, env)) as client:
            combined = client.get("/api/analysis/regions?days=7").json()
            nova = client.get("/api/analysis/regions?days=7&alliance=NOVA").json()
            enemy = client.get("/api/analysis/regions?days=7&alliance=ENEMY").json()

        # Combined = union of both tags: every region with ANY of our
        # presence. Combined covers ALL villages of each region (share 1.0),
        # so rows sort by region name asc.
        assert [row["region"] for row in combined["current"]] == ["Borders", "Enemyland", "Testland"]
        assert set(combined["series"]) == {"Testland", "Borders", "Enemyland"}
        testland = next(row for row in combined["current"] if row["region"] == "Testland")
        # Latest day (08-09) Testland: NOVA rows 1,2,7 (row 4 exists only on
        # 08-08) + ENEMY row 3.
        assert testland["our_pop"] == 2502 + 2502 + 600 + 2000

        # NOVA-only: Enemyland disappears (no NOVA presence), numbers drop.
        assert [row["region"] for row in nova["current"]] == ["Testland", "Borders"]
        assert set(nova["series"]) == {"Testland", "Borders"}
        nova_testland = next(row for row in nova["current"] if row["region"] == "Testland")
        assert nova_testland["our_pop"] == 2502 + 2502 + 600
        assert nova_testland["region_total_pop"] == 2502 + 2502 + 600 + 2000  # all villages, both alliances

        # ENEMY-only: shares computed from the enemy villages only (the
        # fixture seeds ENEMY in all three regions; Enemyland is fully ours).
        assert [row["region"] for row in enemy["current"]] == ["Enemyland", "Borders", "Testland"]
        assert set(enemy["series"]) == {"Testland", "Borders", "Enemyland"}
        enemyland = next(row for row in enemy["current"] if row["region"] == "Enemyland")
        assert enemyland["our_pop"] == 900
        enemy_testland = next(row for row in enemy["current"] if row["region"] == "Testland")
        assert enemy_testland["our_pop"] == 2000

    def test_alliance_unknown_422_lists_valid(self, tmp_path: Path) -> None:
        db = tmp_path / "ar.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/regions?days=7&alliance=NOPE")

        assert res.status_code == 422
        detail = res.json()["detail"]
        assert "unknown alliance 'NOPE'" in detail
        assert "NOVA" in detail  # the valid tags are listed

    def test_alliance_combined_explicit_equals_default(self, tmp_path: Path) -> None:
        db = tmp_path / "ar.db"
        _seed_analysis_db(db)
        env = _env(ALLIANCE_TAGS="NOVA,ENEMY")
        with TestClient(_app(db, env)) as client:
            default = client.get("/api/analysis/regions?days=7").json()
            explicit = client.get("/api/analysis/regions?days=7&alliance=combined").json()
        assert default == explicit

    def test_top_alliances_per_region(self, tmp_path: Path) -> None:
        db = tmp_path / "ar.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/regions?days=7").json()

        top = payload["top_alliances"]
        assert set(top) == {"Testland", "Borders", "Enemyland"}
        # Latest day (08-09) Testland: NOVA rows 1,2,7 (5604) + ENEMY row 3.
        assert top["Testland"] == [
            {"tag": "NOVA", "population": 2502 + 2502 + 600},
            {"tag": "ENEMY", "population": 2000},
        ]
        assert top["Borders"] == [{"tag": "NOVA", "population": 100}, {"tag": "ENEMY", "population": 50}]
        assert top["Enemyland"] == [{"tag": "ENEMY", "population": 900}]


class TestAnalysisStandings:
    def test_series_rows_with_ours_flag(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_analysis_db(db)
        env = _env(TRACKED_ALLIANCES="NOVA,ENEMY")
        with TestClient(_app(db, env)) as client:
            payload = client.get("/api/analysis/standings?days=7").json()

        assert payload["dates"] == ["2026-08-07", "2026-08-08", "2026-08-09"]
        assert payload["available_tags"] == ["ENEMY", "NOVA"]  # alphabetical, alliance_id != 0
        assert payload["default_tags"] == ["NOVA", "ENEMY"]  # config order
        assert [row["tag"] for row in payload["series"]] == ["NOVA", "ENEMY"]  # latest-pop desc
        nova, enemy = payload["series"]
        assert nova["ours"] is True
        assert enemy["ours"] is False
        # Alliance-wide populations: Testland (1,2[,4/7]) + Borders row 5.
        assert [p[1] for p in nova["points"]] == [5100, 5602, 5704]
        assert len(nova["vp_points"]) == 3

    def test_tag_filter_returns_exactly_requested(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_analysis_db(db)
        env = _env(TRACKED_ALLIANCES="NOVA,ENEMY")
        with TestClient(_app(db, env)) as client:
            payload = client.get("/api/analysis/standings?days=7&tag=NOVA").json()

        assert [row["tag"] for row in payload["series"]] == ["NOVA"]
        assert payload["available_tags"] == ["ENEMY", "NOVA"]  # catalog stays whole-map
        assert payload["default_tags"] == ["NOVA", "ENEMY"]

    def test_tag_dedupe_preserves_first_occurrence(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_analysis_db(db)
        env = _env(TRACKED_ALLIANCES="NOVA,ENEMY")
        with TestClient(_app(db, env)) as client:
            payload = client.get("/api/analysis/standings?days=7&tag=ENEMY&tag=NOVA&tag=ENEMY").json()

        # Deduped to ENEMY, NOVA in request order (ENEMY latest-pop 2950 < NOVA
        # 5704, so series order stays NOVA-first by the standings sort).
        assert [row["tag"] for row in payload["series"]] == ["NOVA", "ENEMY"]

    def test_nine_tags_422(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_analysis_db(db)
        params = [("tag", f"TAG{i}") for i in range(9)]
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/standings", params=params)

        assert res.status_code == 422
        assert "at most 8 tags" in res.json()["detail"]

    def test_unknown_tag_422(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/standings?days=7&tag=NOPE")

        assert res.status_code == 422
        assert "unknown standings tag 'NOPE'" in res.json()["detail"]
        assert "ENEMY" in res.json()["detail"]

    def test_unresolved_tags_skipped(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_analysis_db(db)
        env = _env(TRACKED_ALLIANCES="NOPE")
        with TestClient(_app(db, env)) as client:
            payload = client.get("/api/analysis/standings?days=7").json()

        assert payload == {
            "dates": ["2026-08-07", "2026-08-08", "2026-08-09"],
            "series": [],
            "available_tags": ["ENEMY", "NOVA"],
            "default_tags": [],
        }

    def test_empty_db_empty_payload(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/standings?days=7").json() == {
                "dates": [],
                "series": [],
                "available_tags": [],
                "default_tags": [],
            }


class TestAnalysisDates:
    def test_dates_ascending(self, tmp_path: Path) -> None:
        db = tmp_path / "ad.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/dates").json() == {
                "dates": ["2026-08-07", "2026-08-08", "2026-08-09"]
            }

    def test_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "ad.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/dates").json() == {"dates": []}


class TestAnalysisEvents:
    def test_default_pair_is_latest_two_dates(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/events").json()

        assert [e["village_name"] for e in payload["gained"]] == ["Village 7"]
        assert [e["village_name"] for e in payload["lost"]] == ["Village 4"]
        gained = payload["gained"][0]
        assert gained["village_id"] == 7  # additive field for history lookup
        assert gained["x"] == 7
        assert gained["y"] == 7
        assert gained["region"] == "Testland"
        assert gained["event"] == "gained"
        assert gained["owner_tag"] is None
        assert gained["owner_player"] == "Player 7"
        assert payload["lost"][0]["village_id"] == 4
        assert payload["lost"][0]["event"] == "lost_deleted"
        assert payload["lost"][0]["owner_tag"] is None
        assert payload["lost"][0]["owner_player"] is None

    def test_explicit_range(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/events?from=2026-08-07&to=2026-08-08").json()

        assert [e["village_name"] for e in payload["gained"]] == ["Village 4"]
        assert payload["lost"] == []

    def test_from_equals_to_422(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/events?from=2026-08-09&to=2026-08-09")

        assert res.status_code == 422
        assert "must be earlier than" in res.json()["detail"]

    def test_unknown_date_422_lists_valid_dates(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/events?from=2026-01-01")

        assert res.status_code == 422
        detail = res.json()["detail"]
        assert "2026-08-07" in detail and "2026-08-09" in detail

    def test_single_snapshot_without_params_422(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/events").status_code == 422

    def test_empty_db_empty_payload(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/events").json() == {"gained": [], "lost": []}

    def test_alliance_filter(self, tmp_path: Path) -> None:
        # The fixture's only events belong to NOVA (ids 7/4); an ENEMY-only
        # filter must return no events, NOVA must return the same pair as
        # combined when ENEMY has no events.
        db = tmp_path / "ae.db"
        _seed_analysis_db(db)
        env = _env(ALLIANCE_TAGS="NOVA,ENEMY")
        with TestClient(_app(db, env)) as client:
            combined = client.get("/api/analysis/events?from=2026-08-07&to=2026-08-09").json()
            nova = client.get("/api/analysis/events?from=2026-08-07&to=2026-08-09&alliance=NOVA").json()
            enemy = client.get("/api/analysis/events?from=2026-08-07&to=2026-08-09&alliance=ENEMY").json()

        # 07→09 window: village 7 appears on 08-09 (gained); village 4
        # exists only inside the window (08-08) and is not an event of the
        # window itself; nothing disappeared.
        assert [e["village_name"] for e in combined["gained"]] == ["Village 7"]
        assert combined["lost"] == []
        assert combined["gained_total"] == 1
        assert combined["lost_total"] == 0
        assert combined["limit"] == 200
        assert nova == combined
        assert enemy == {"gained": [], "lost": [], "gained_total": 0, "lost_total": 0, "limit": 200}

    def test_alliance_unknown_422(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/events?alliance=NOPE")

        assert res.status_code == 422
        assert "unknown alliance 'NOPE'" in res.json()["detail"]

    def test_limit_slices_each_list_with_full_totals(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        conn = store.connect(db)
        store.init_schema(conn)
        store.save_snapshot(
            conn,
            "2026-08-07",
            [_row(1, population=100), _row(2, population=100), _row(3, population=100), _row(4, population=100)],
        )
        store.save_snapshot(
            conn,
            "2026-08-08",
            [_row(1, population=100), _row(5, population=100), _row(6, population=100), _row(7, population=100)],
        )
        conn.close()
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/events?limit=2").json()

        # Full (already sorted) lists counted before the per-list slice: 3
        # gained (5,6,7) and 3 lost (2,3,4) exist; only 2 of each returned.
        assert payload["gained_total"] == 3
        assert payload["lost_total"] == 3
        assert payload["limit"] == 2
        assert [e["village_name"] for e in payload["gained"]] == ["Village 5", "Village 6"]
        assert [e["village_name"] for e in payload["lost"]] == ["Village 2", "Village 3"]

    def test_limit_bounds_422(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/events?limit=0").status_code == 422
            assert client.get("/api/analysis/events?limit=1001").status_code == 422


class TestAnalysisWars:
    """Wars scoreboard: TRACKED_ALLIANCES universe, latest-pair defaults.

    Scenario (two dates): NOVA(7) ↔ ENEMY(8) transfers on villages 2/3,
    ENEMY village 4 deleted, GHOST(999) untracked and stable, NOVA village 7
    new on the second day. Transfers involving an untracked alliance never
    appear; new villages never appear.
    """

    def _seed(self, db: Path) -> None:
        conn = store.connect(db)
        store.init_schema(conn)
        store.save_snapshot(
            conn,
            "2026-08-07",
            [
                _row(1, population=100),  # NOVA stable
                _row(2, population=200, alliance_id=8, alliance_tag="ENEMY"),  # ENEMY -> NOVA
                _row(3, population=300),  # NOVA -> ENEMY
                _row(4, population=400, alliance_id=8, alliance_tag="ENEMY"),  # ENEMY deleted
                _row(6, population=600, alliance_id=999, alliance_tag="GHOST"),  # untracked stable
            ],
        )
        store.save_snapshot(
            conn,
            "2026-08-08",
            [
                _row(1, population=110),
                _row(2, population=210, alliance_id=7, alliance_tag="NOVA"),
                _row(3, population=310, alliance_id=8, alliance_tag="ENEMY"),
                _row(6, population=610, alliance_id=999, alliance_tag="GHOST"),
                _row(7, population=700),  # new NOVA village (ignored)
            ],
        )
        conn.close()

    def test_default_pair_is_latest_two_dates(self, tmp_path: Path) -> None:
        db = tmp_path / "wars.db"
        self._seed(db)
        with TestClient(_app(db, _env(TRACKED_ALLIANCES="NOVA,ENEMY"))) as client:
            payload = client.get("/api/analysis/wars").json()

        assert payload["from"] == "2026-08-07"
        assert payload["to"] == "2026-08-08"
        assert payload["tracked_tags"] == ["NOVA", "ENEMY"]
        assert [(p["from_tag"], p["to_tag"]) for p in payload["pairs"]] == [
            ("ENEMY", "NOVA"),
            ("NOVA", "ENEMY"),
        ]
        enemy_to_nova = payload["pairs"][0]
        assert enemy_to_nova["villages"] == 1
        assert enemy_to_nova["population"] == 210
        entry = enemy_to_nova["entries"][0]
        assert entry["village_name"] == "Village 2"
        assert entry["from_tag"] == "ENEMY"
        assert entry["to_tag"] == "NOVA"
        assert entry["from_player"] == "Player 2"
        assert entry["to_player"] == "Player 2"  # same player id keeps owner
        assert payload["pairs"][1]["entries"][0]["village_name"] == "Village 3"
        assert [d["village_name"] for d in payload["deleted"]] == ["Village 4"]
        assert payload["deleted"][0]["from_tag"] == "ENEMY"

    def test_explicit_dates_and_validation(self, tmp_path: Path) -> None:
        db = tmp_path / "wars.db"
        self._seed(db)
        with TestClient(_app(db, _env(TRACKED_ALLIANCES="NOVA,ENEMY"))) as client:
            explicit = client.get("/api/analysis/wars?from=2026-08-07&to=2026-08-08").json()
            same = client.get("/api/analysis/wars?from=2026-08-08&to=2026-08-08")
            unknown = client.get("/api/analysis/wars?from=2026-01-01")
            reversed_order = client.get("/api/analysis/wars?from=2026-08-08&to=2026-08-07")

        assert explicit["from"] == "2026-08-07"
        assert same.status_code == 422
        assert "must be earlier than" in same.json()["detail"]
        assert unknown.status_code == 422
        assert "valid dates" in unknown.json()["detail"]
        assert reversed_order.status_code == 422

    def test_fewer_than_two_dates_empty_payload(self, tmp_path: Path) -> None:
        db = tmp_path / "wars.db"
        conn = store.connect(db)
        store.init_schema(conn)
        store.save_snapshot(conn, "2026-08-07", [_row(1, population=100)])
        conn.close()
        with TestClient(_app(db, _env(TRACKED_ALLIANCES="NOVA,ENEMY"))) as client:
            payload = client.get("/api/analysis/wars").json()

        assert payload == {"from": None, "to": None, "tracked_tags": [], "pairs": [], "deleted": []}

    def test_empty_db_empty_payload(self, tmp_path: Path) -> None:
        db = tmp_path / "wars.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env(TRACKED_ALLIANCES="NOVA,ENEMY"))) as client:
            payload = client.get("/api/analysis/wars").json()

        assert payload == {"from": None, "to": None, "tracked_tags": [], "pairs": [], "deleted": []}

    def test_unresolved_tracked_tags_dropped(self, tmp_path: Path) -> None:
        db = tmp_path / "wars.db"
        self._seed(db)
        with TestClient(_app(db, _env(TRACKED_ALLIANCES="NOVA,MISSING"))) as client:
            payload = client.get("/api/analysis/wars").json()

        assert payload["tracked_tags"] == ["NOVA"]
        # A conquest needs BOTH sides tracked: with ENEMY untracked, every
        # transfer and the ENEMY deletion drop out — empty results, 200.
        assert payload["pairs"] == []
        assert payload["deleted"] == []

    def test_tracked_only_universe(self, tmp_path: Path) -> None:
        db = tmp_path / "wars.db"
        self._seed(db)
        with TestClient(_app(db, _env(TRACKED_ALLIANCES="NOVA"))) as client:
            payload = client.get("/api/analysis/wars").json()

        assert payload["tracked_tags"] == ["NOVA"]
        # Both sides of a conquest must be tracked: the ENEMY→NOVA transfer
        # (village 2) and the ENEMY deletion (village 4) are not in the
        # NOVA-only universe.
        assert payload["pairs"] == []
        assert payload["deleted"] == []


class TestAnalysisDeltas:
    def test_rows_with_deltas_none_on_oldest(self, tmp_path: Path) -> None:
        db = tmp_path / "dl.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/deltas?days=7").json()

        assert payload["dates"] == ["2026-08-07", "2026-08-08", "2026-08-09"]
        rows = payload["rows"]
        assert [row["date"] for row in rows] == payload["dates"]
        assert rows[0]["villages_delta"] is None
        assert rows[1]["villages_delta"] == 1  # 2 villages on 08-08 (incl. id 4)
        assert rows[2]["villages_delta"] == 0  # 2 villages on 08-09 (id 7 replaces id 4)
        assert rows[2]["population_delta"] == 5604 - 5502
        assert rows[2]["players_delta"] == 0

    def test_days_window(self, tmp_path: Path) -> None:
        db = tmp_path / "dl.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/deltas?days=2").json()

        assert payload["dates"] == ["2026-08-08", "2026-08-09"]
        assert len(payload["rows"]) == 2

    def test_empty_db_empty_payload(self, tmp_path: Path) -> None:
        db = tmp_path / "dl.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/deltas?days=7").json() == {"dates": [], "rows": []}

    def test_alliance_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "dl.db"
        _seed_analysis_db(db)
        env = _env(ALLIANCE_TAGS="NOVA,ENEMY")
        with TestClient(_app(db, env)) as client:
            combined = client.get("/api/analysis/deltas?days=7").json()
            nova = client.get("/api/analysis/deltas?days=7&alliance=NOVA").json()
            enemy = client.get("/api/analysis/deltas?days=7&alliance=ENEMY").json()

        # Latest-day rows: NOVA swaps village 4 (08-08 only) for village 7
        # (08-09), so village/pop deltas are 0/+102; ENEMY is static
        # (3 villages, 2950 pop both days); combined = NOVA + ENEMY rows.
        assert combined["rows"][2]["villages"] == 7
        assert combined["rows"][2]["population"] == 8654
        assert combined["rows"][2]["villages_delta"] == 0
        assert combined["rows"][2]["population_delta"] == 102
        assert nova["rows"][2]["villages"] == 4
        assert nova["rows"][2]["population"] == 5704
        assert nova["rows"][2]["villages_delta"] == 0
        assert nova["rows"][2]["population_delta"] == 102
        assert enemy["rows"][2]["villages"] == 3
        assert enemy["rows"][2]["population"] == 2950
        assert enemy["rows"][2]["villages_delta"] == 0
        assert enemy["rows"][2]["population_delta"] == 0
        # 08-08 row: NOVA gained village 4 (+502 pop) while ENEMY stayed flat.
        assert nova["rows"][1]["villages_delta"] == 1
        assert nova["rows"][1]["population_delta"] == 502
        assert enemy["rows"][1]["villages_delta"] == 0

    def test_alliance_unknown_422(self, tmp_path: Path) -> None:
        db = tmp_path / "dl.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/deltas?days=7&alliance=NOPE")

        assert res.status_code == 422
        assert "unknown alliance 'NOPE'" in res.json()["detail"]


class TestAuthMiddleware:
    def test_non_loopback_bind_requires_token(self, tmp_path: Path) -> None:
        db = tmp_path / "m.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_TOKEN="sekret")
        with TestClient(_app(db, env)) as client:
            assert client.get("/api/status").status_code == 401
            assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert client.post("/api/actions/fetch").status_code == 401
            resp = client.get("/api/status", headers={"Authorization": "Bearer sekret"})
            assert resp.status_code == 200

    def test_loopback_bind_no_token_needed(self, tmp_path: Path) -> None:
        db = tmp_path / "m.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:  # DASHBOARD_BIND=127.0.0.1
            assert client.get("/api/status").status_code == 200

    def test_non_loopback_with_loopback_only_true_no_token(self, tmp_path: Path) -> None:
        # compose publishes loopback-only on the host and sets
        # DASHBOARD_LOOPBACK_ONLY=true — the token is not required.
        db = tmp_path / "m.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_LOOPBACK_ONLY="true")
        with TestClient(_app(db, env)) as client:
            assert client.get("/api/status").status_code == 200

    def test_middleware_active_but_token_empty_401_always(self, tmp_path: Path) -> None:
        db = tmp_path / "m.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="0.0.0.0")  # DASHBOARD_TOKEN=""
        with TestClient(_app(db, env)) as client:
            assert client.get("/api/status", headers={"Authorization": "Bearer "}).status_code == 401
            assert client.get("/api/status").status_code == 401

    def test_healthz_public_under_auth(self, tmp_path: Path) -> None:
        # The container HEALTHCHECK probes /healthz — it must never need the
        # token (an authenticated probe would mark the container unhealthy).
        db = tmp_path / "m.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_TOKEN="sekret")
        with TestClient(_app(db, env)) as client:
            assert client.get("/healthz").status_code == 200
            assert client.get("/healthz").json() == {"status": "ok"}
            assert client.get("/api/status").status_code == 401

    def test_static_ui_public_under_auth(self, tmp_path: Path, monkeypatch) -> None:
        # The browser must be able to load the page without a token; the UI
        # then authenticates the API calls (app.js sends the Bearer header
        # from the login dialog).
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")
        monkeypatch.setattr(dashboard_app, "STATIC_DIR", static)
        db = tmp_path / "m.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_TOKEN="sekret")
        with TestClient(_app(db, env)) as client:
            assert client.get("/").status_code == 200
            assert client.get("/api/status").status_code == 401
            assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert client.get("/api/status", headers={"Authorization": "Bearer sekret"}).status_code == 200


class TestBuildMeta:
    def test_meta_public_with_injected_sha(self, tmp_path: Path) -> None:
        """Anonymous GET /api/meta in token mode: exact two-key payload, no secrets."""
        db = tmp_path / "m.db"
        _seed_db(db)
        env = _env(
            DASHBOARD_BIND="0.0.0.0",
            DASHBOARD_TOKEN="sekret",
            TRAVIAN_BUILD_SHA="abc123",
            BACKFILL_DSN="postgres://user:pass@example/db",
        )
        with TestClient(_app(db, env)) as client:
            resp = client.get("/api/meta")
            assert resp.status_code == 200
            assert resp.json() == {"version": "0.1.0", "build_sha": "abc123"}
            # No env, tokens or settings leak into the public payload.
            text = resp.text
            assert "sekret" not in text
            assert "test-token" not in text
            assert "postgres://" not in text
            assert "Authorization" not in text

    def test_meta_defaults_to_dev_without_sha(self, tmp_path: Path) -> None:
        db = tmp_path / "m.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            resp = client.get("/api/meta")
            assert resp.status_code == 200
            assert resp.json() == {"version": "0.1.0", "build_sha": "dev"}

    def test_meta_normalizes_blank_sha_to_dev(self, tmp_path: Path) -> None:
        db = tmp_path / "m.db"
        _seed_db(db)
        env = _env(TRAVIAN_BUILD_SHA="   ")
        with TestClient(_app(db, env)) as client:
            assert client.get("/api/meta").json()["build_sha"] == "dev"
class TestAnalysisPlayers:
    def test_payload_shape_and_rankings(self, tmp_path: Path) -> None:
        db = tmp_path / "pl.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/players").json()

        assert set(payload) == {"population", "growth", "new_villages", "vp"}
        # Latest day (08-09): NOVA players 1001 (rows 1,2), 1007, 1005 — plus
        # 1004, whose village exists only on 08-08 and who left the alliance
        # (the universe is curr-ours ∪ prev-ours; ranks with population 0).
        assert len(payload["population"]) == 4
        top = payload["population"][0]
        assert set(top) == {"player_id", "player_name", "population", "villages", "growth", "vp", "gains"}
        assert top["player_id"] == 1001
        assert top["population"] == 2502 + 2502
        assert top["villages"] == 2
        assert [row["player_id"] for row in payload["population"]] == [1001, 1007, 1005, 1004]
        # Growth vs 08-08: 1007 +600, 1001 +2, 1005 0, 1004 −500 (left).
        assert [row["player_id"] for row in payload["growth"]] == [1007, 1001, 1005, 1004]
        assert payload["growth"][0]["growth"] == 600
        assert payload["growth"][3]["growth"] == -500
        # New villages vs 08-08: only village 7 (1007) is a strict gain.
        assert [row["player_id"] for row in payload["new_villages"]] == [1007, 1001, 1005, 1004]
        assert payload["new_villages"][0]["gains"] == 1
        assert payload["new_villages"][1]["gains"] == 0
        # VP: 1001 sums two villages (20), 1007/1005 tie at 10 → population
        # desc; 1004 left the alliance → vp 0.
        assert [row["player_id"] for row in payload["vp"]] == [1001, 1007, 1005, 1004]
        assert [row["vp"] for row in payload["vp"]] == [20, 10, 10, 0]

    def test_alliance_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "pl.db"
        _seed_analysis_db(db)
        env = _env(ALLIANCE_TAGS="NOVA,ENEMY")
        with TestClient(_app(db, env)) as client:
            enemy = client.get("/api/analysis/players?alliance=ENEMY").json()

        # ENEMY players: 1003 (2000), 1008 (900), 1006 (50) — no growth/gains;
        # all vp 10 → vp ranking degrades to population order.
        assert [row["player_id"] for row in enemy["population"]] == [1003, 1008, 1006]
        assert all(row["growth"] == 0 for row in enemy["growth"])
        assert all(row["gains"] == 0 for row in enemy["new_villages"])
        assert [row["player_id"] for row in enemy["vp"]] == [1003, 1008, 1006]
        assert [row["vp"] for row in enemy["vp"]] == [10, 10, 10]

    def test_unknown_alliance_422(self, tmp_path: Path) -> None:
        db = tmp_path / "pl.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/players?alliance=NOPE")

        assert res.status_code == 422
        assert "unknown alliance 'NOPE'" in res.json()["detail"]

    def test_empty_db_empty_rankings(self, tmp_path: Path) -> None:
        db = tmp_path / "pl.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/players").json() == {
                "population": [],
                "growth": [],
                "new_villages": [],
                "vp": [],
            }


class TestAnalysisVillages:
    def test_empty_db_returns_null_snapshot_and_no_results(self, tmp_path: Path) -> None:
        db = tmp_path / "v.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/villages", params={"q": "anything"}).json()

        assert payload == {"snapshot_date": None, "results": []}

    def test_empty_query_returns_snapshot_without_scan(self, tmp_path: Path) -> None:
        db = tmp_path / "v.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/villages", params={"q": "   "}).json()

        assert payload["snapshot_date"] == SNAPSHOT_DATE
        assert payload["results"] == []

    def test_search_by_name_and_coordinates(self, tmp_path: Path) -> None:
        db = tmp_path / "v.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            by_name = client.get("/api/analysis/villages", params={"q": "Village 1"}).json()
            by_coords = client.get("/api/analysis/villages", params={"q": "1|1"}).json()

        assert by_name["snapshot_date"] == SNAPSHOT_DATE
        assert [row["village_id"] for row in by_name["results"]] == [1]
        assert [row["village_id"] for row in by_coords["results"]] == [1]
        fields = {
            "village_id",
            "name",
            "x",
            "y",
            "region",
            "population",
            "player_name",
            "alliance_tag",
            "is_capital",
            "is_city",
            "is_harbor",
        }
        assert set(by_name["results"][0]) == fields
        assert by_name["results"][0]["name"] == "Village 1"
        assert by_name["results"][0]["population"] == 100
        assert by_name["results"][0]["is_capital"] is False

    def test_limit_clamped_and_no_match_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "v.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/villages", params={"q": "x", "limit": 0}).status_code == 422
            assert client.get("/api/analysis/villages", params={"q": "x", "limit": 51}).status_code == 422
            no_match = client.get("/api/analysis/villages", params={"q": "zzz-no-match"}).json()

        assert no_match["snapshot_date"] == SNAPSHOT_DATE
        assert no_match["results"] == []

    def test_not_filtered_by_alliance_tags(self, tmp_path: Path) -> None:
        """The explorer covers the whole map; ENEMY villages are searchable
        even though the analysis alliance filter would exclude them."""
        db = tmp_path / "v.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/villages", params={"q": "8|8"}).json()

        assert [row["village_id"] for row in payload["results"]] == [8]
        assert payload["results"][0]["alliance_tag"] == "ENEMY"


class TestAnalysisVillageHistory:
    def test_history_chronological_and_present(self, tmp_path: Path) -> None:
        db = tmp_path / "vh.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/villages/7/history", params={"days": 60}).json()

        assert payload["village_id"] == 7
        assert payload["latest_snapshot_date"] == "2026-08-09"
        assert payload["present_in_latest"] is True
        assert [point["snapshot_date"] for point in payload["history"]] == ["2026-08-09"]
        point = payload["history"][0]
        assert set(point) == {"snapshot_date", "name", "x", "y", "player_name", "alliance_tag", "population"}
        assert point["name"] == "Village 7"
        assert point["player_name"] == "Player 7"

    def test_deleted_village_history_present_in_latest_false(self, tmp_path: Path) -> None:
        db = tmp_path / "vh.db"
        _seed_analysis_db(db)  # village 4 exists 08-08 only
        with TestClient(_app(db, _env())) as client:
            payload = client.get("/api/analysis/villages/4/history").json()

        assert payload["present_in_latest"] is False
        assert [point["snapshot_date"] for point in payload["history"]] == ["2026-08-08"]

    def test_unknown_village_404(self, tmp_path: Path) -> None:
        db = tmp_path / "vh.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/villages/999/history")

        assert res.status_code == 404
        assert res.json()["detail"] == "unknown village id 999"

    def test_days_clamped(self, tmp_path: Path) -> None:
        db = tmp_path / "vh.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/villages/1/history", params={"days": 0}).status_code == 422
            assert client.get("/api/analysis/villages/1/history", params={"days": 61}).status_code == 422



class TestAuthModes:
    def test_explicit_none_overrides_non_loopback_bind(self, tmp_path: Path) -> None:
        db = tmp_path / "am.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_AUTH_MODE="none")
        with TestClient(_app(db, env)) as client:
            assert client.get("/api/status").status_code == 200

    def test_oauth_without_oauth_env_falls_back_to_token(self, tmp_path: Path) -> None:
        # Loopback bind + DASHBOARD_AUTH_MODE=oauth but no OAUTH_* keys: the
        # safe default is token mode (the token is required even on loopback
        # then — never silent openness).
        db = tmp_path / "am.db"
        _seed_db(db)
        env = _env(DASHBOARD_AUTH_MODE="oauth", DASHBOARD_TOKEN="sekret")
        with TestClient(_app(db, env)) as client:
            assert client.get("/api/status").status_code == 401
            assert client.get("/api/status", headers={"Authorization": "Bearer sekret"}).status_code == 200

    def test_oauth_complete_requires_session(self, tmp_path: Path) -> None:
        db = tmp_path / "am.db"
        _seed_db(db)
        env = _env(
            DASHBOARD_AUTH_MODE="oauth",
            OAUTH_CLIENT_ID="cid",
            OAUTH_CLIENT_SECRET="csec",
            OAUTH_GUILD_ID="100",
        )
        with TestClient(_app(db, env)) as client:
            assert client.get("/api/status").status_code == 401
            assert client.get("/api/status", headers={"Authorization": "Bearer bogus"}).status_code == 401

    def test_legacy_loopback_no_token(self, tmp_path: Path) -> None:
        # Unset DASHBOARD_AUTH_MODE + loopback bind → legacy "none".
        db = tmp_path / "am.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:  # bind 127.0.0.1
            assert client.get("/api/status").status_code == 200


class TestReadinessAndFreshness:
    def test_compute_freshness_states(self) -> None:
        """Pure-function contract: precedence no_data → gap → stale → current."""
        fixed_now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
        assert dashboard_app.compute_freshness([], fixed_now, "Europe/Warsaw") == {
            "state": "no_data",
            "snapshot_date": None,
            "previous_snapshot_date": None,
            "age_days": None,
            "gap_days": None,
        }
        gap = dashboard_app.compute_freshness(
            ["2026-08-01", "2026-08-08"], fixed_now, "Europe/Warsaw"
        )
        assert gap == {
            "state": "gap",
            "snapshot_date": "2026-08-08",
            "previous_snapshot_date": "2026-08-01",
            "age_days": 5,
            "gap_days": 6,
        }
        assert dashboard_app.compute_freshness(["2026-08-13"], fixed_now, "Europe/Warsaw") == {
            "state": "current",
            "snapshot_date": "2026-08-13",
            "previous_snapshot_date": None,
            "age_days": 0,
            "gap_days": None,
        }
        stale = dashboard_app.compute_freshness(["2026-08-01"], fixed_now, "Europe/Warsaw")
        assert stale["state"] == "stale"
        assert stale["age_days"] == 12
        assert stale["gap_days"] is None

    def test_readyz_503_before_runtime_ready(self, tmp_path: Path) -> None:
        db = tmp_path / "rz.db"
        _seed_db(db)
        def not_ready() -> dashboard_app.RuntimeState:
            return dashboard_app.RuntimeState(bot_ready=False, scheduler_ready=False)
        with TestClient(_app(db, _env(), get_runtime_state=not_ready)) as client:
            resp = client.get("/readyz")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["bot_ready"] is False
        assert body["scheduler_ready"] is False
        # Freshness is still reported on a not-ready process (public probe).
        assert body["freshness"]["state"] in {"current", "stale", "gap"}

    def test_readyz_200_when_runtime_ready(self, tmp_path: Path) -> None:
        db = tmp_path / "rz.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:  # fixture default: ready
            resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_readyz_public_under_token_auth(self, tmp_path: Path) -> None:
        """/readyz must never need the token (same reasoning as /healthz)."""
        db = tmp_path / "rz.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_TOKEN="sekret")
        with TestClient(_app(db, env)) as client:
            assert client.get("/api/status").status_code == 401
            assert client.get("/readyz").status_code == 200
            assert client.get("/readyz").json()["status"] == "ready"

    def test_readyz_gap_days_and_empty_db(self, tmp_path: Path) -> None:
        gap_db = tmp_path / "gap.db"
        _seed_db(gap_db, snapshot=False)
        conn = store.connect(gap_db)
        store.save_snapshot(conn, "2026-08-01", [_row(1, population=100)])
        store.save_snapshot(conn, "2026-08-08", [_row(1, population=110)])
        conn.close()
        with TestClient(_app(gap_db, _env())) as client:
            freshness = client.get("/readyz").json()["freshness"]
        assert freshness["state"] == "gap"
        assert freshness["gap_days"] == 6
        assert freshness["previous_snapshot_date"] == "2026-08-01"

        empty_db = tmp_path / "empty.db"
        _seed_db(empty_db, snapshot=False)
        with TestClient(_app(empty_db, _env())) as client:
            freshness = client.get("/readyz").json()["freshness"]
        assert freshness["state"] == "no_data"
        assert freshness["snapshot_date"] is None
        assert freshness["age_days"] is None

    def test_status_includes_freshness_for_admins(self, tmp_path: Path) -> None:
        db = tmp_path / "st.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:
            body = client.get("/api/status").json()
        assert body["freshness"]["snapshot_date"] == SNAPSHOT_DATE
        assert "state" in body["freshness"]


class TestOAuthFlow:
    OAUTH_ENV: ClassVar[dict[str, str]] = {
        "DASHBOARD_AUTH_MODE": "oauth",
        "OAUTH_CLIENT_ID": "cid",
        "OAUTH_CLIENT_SECRET": "csec",
        "OAUTH_GUILD_ID": "100",
        "OAUTH_PUBLIC_ORIGIN": "http://testserver",
        "ADMIN_ROLE_ID": "555",
    }

    #: Distinguishes "not given" from an explicit None (member endpoint 404).
    _MEMBER_MISSING = object()

    def _client(
        self,
        tmp_path: Path,
        monkeypatch,
        *,
        member: object = _MEMBER_MISSING,
        guilds: list[dict[str, object]] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> TestClient:
        db = tmp_path / "oa.db"
        _seed_db(db)
        env = _env(**self.OAUTH_ENV)
        if member is self._MEMBER_MISSING:
            member = {"roles": []}
        member_obj = cast(dict[str, object] | None, member)
        guilds_list = guilds if guilds is not None else []
        self.exchange_calls: list[tuple[str, str, str, str]] = []

        def fake_exchange(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict[str, object]:
            self.exchange_calls.append((client_id, client_secret, code, redirect_uri))
            return {"access_token": "at-fake"}

        monkeypatch.setattr(dashboard_auth, "exchange_code", fake_exchange)
        monkeypatch.setattr(dashboard_auth, "fetch_user", lambda token: {"id": "u1", "username": "Tester"})
        monkeypatch.setattr(dashboard_auth, "fetch_guild_member", lambda token, guild_id: member_obj)
        monkeypatch.setattr(dashboard_auth, "fetch_guilds", lambda token: guilds_list)
        return TestClient(_app(db, env, loop=loop), follow_redirects=False)

    def test_login_redirects_to_discord_with_state(self, tmp_path: Path, monkeypatch) -> None:
        client = self._client(tmp_path, monkeypatch)
        with client:
            res = client.get("/api/auth/login")

        assert res.status_code == 302
        location = res.headers["location"]
        assert location.startswith("https://discord.com/oauth2/authorize?")
        qs = parse_qs(urlparse(location).query)
        assert qs["client_id"] == ["cid"]
        assert qs["state"]
        assert qs["redirect_uri"] == ["http://testserver/api/auth/callback"]
        assert qs["scope"] == ["identify guilds guilds.members.read"]

    def test_login_not_enabled_in_token_mode(self, tmp_path: Path) -> None:
        db = tmp_path / "oa.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:  # token-mode env
            res = client.get("/api/auth/login")

        assert res.status_code == 409
        assert res.json() == {"error": "oauth not enabled"}

    def test_callback_invalid_state(self, tmp_path: Path, monkeypatch) -> None:
        client = self._client(tmp_path, monkeypatch)
        with client:
            res = client.get("/api/auth/callback?code=x&state=bogus")

        assert res.status_code == 302
        assert res.headers["location"] == "/?#auth_error=invalid_state"

    def test_member_gets_intel_and_freshness_without_errors_or_logs(self, tmp_path: Path, monkeypatch) -> None:
        client = self._client(tmp_path, monkeypatch)  # member without admin role
        with client:
            _seed_logs(tmp_path / "oa.db")  # operator errors must never leak to members
            self._login(client)

            # Status: identical freshness payload, sanitized errors.
            status = client.get("/api/status")
            assert status.status_code == 200
            body = status.json()
            assert body["errors"] == []
            assert body["snapshot_date"] == SNAPSHOT_DATE
            assert body["alliance_tags"] == ["NOVA"]
            # Freshness is part of the member payload (never sanitized away).
            assert body["freshness"]["snapshot_date"] == SNAPSHOT_DATE
            assert body["freshness"]["state"] in {"current", "stale", "gap"}

            # Intelligence and the village explorer stay open for members.
            assert client.get("/api/analysis/players").status_code == 200
            assert client.get("/api/analysis/villages", params={"q": "Village 1"}).status_code == 200

            # Raw logs are admin-only.
            assert client.get("/api/logs").status_code == 403
            assert client.get("/api/logs").json() == {"error": "admin required"}

    def test_admin_sees_logs_and_status_errors(self, tmp_path: Path, monkeypatch) -> None:
        client = self._client(tmp_path, monkeypatch, member={"roles": ["555"]})
        with client:
            _seed_logs(tmp_path / "oa.db")
            self._login(client)

            assert client.get("/api/logs").status_code == 200
            assert len(client.get("/api/logs").json()) == 5
            status = client.get("/api/status").json()
            assert [e["message"] for e in status["errors"]] == ["err4", "err3", "err0"]

    def test_token_mode_bearer_keeps_admin_access(self, tmp_path: Path) -> None:
        db = tmp_path / "tok.db"
        _seed_db(db)
        _seed_logs(db)
        env = _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_TOKEN="sekret")
        with TestClient(_app(db, env)) as client:
            headers = {"Authorization": "Bearer sekret"}
            assert client.get("/api/logs", headers=headers).status_code == 200
            status = client.get("/api/status", headers=headers).json()
            assert [e["message"] for e in status["errors"]] == ["err4", "err3", "err0"]

    def test_callback_missing_params(self, tmp_path: Path, monkeypatch) -> None:
        client = self._client(tmp_path, monkeypatch)
        with client:
            assert client.get("/api/auth/callback").status_code == 302
            assert client.get("/api/auth/callback?code=x").status_code == 302

    def test_callback_exchange_error(self, tmp_path: Path, monkeypatch) -> None:
        client = self._client(tmp_path, monkeypatch)
        with client:
            state = self._login_state(client)

            def boom(*args: object, **kwargs: object) -> dict[str, object]:
                raise httpx.HTTPError("boom")

            monkeypatch.setattr(dashboard_auth, "exchange_code", boom)
            res = client.get(f"/api/auth/callback?code=x&state={state}")

        assert res.status_code == 302
        assert res.headers["location"] == "/?#auth_error=login_failed"

    def test_full_flow_member_readonly(self, tmp_path: Path, monkeypatch) -> None:
        client = self._client(tmp_path, monkeypatch)  # member without admin role
        with client:
            # OAuth mode has no Bearer transport: a bare token header is 401.
            assert client.get("/api/status", headers={"Authorization": "Bearer whatever"}).status_code == 401
            state = self._login_state(client)
            res = client.get(f"/api/auth/callback?code=thecode&state={state}")
            assert res.status_code == 302
            location = res.headers["location"]
            assert location == "/?auth=success"
            assert "session=" not in location  # the token never rides in the URL

            # The exchange saw the right arguments (trusted-origin redirect).
            assert self.exchange_calls == [("cid", "csec", "thecode", "http://testserver/api/auth/callback")]

            # The session cookie is HttpOnly + SameSite=Lax; oauth mode has
            # no Bearer transport anymore.
            set_cookie = res.headers["set-cookie"]
            assert set_cookie.startswith("dashboard_session=")
            assert "HttpOnly" in set_cookie
            assert "SameSite=Lax" in set_cookie
            assert "Path=/" in set_cookie
            assert "Max-Age=604800" in set_cookie
            assert "Secure" not in set_cookie  # http origin
            assert client.get("/api/status").status_code == 200  # cookie session works

            # Session works for data endpoints via the cookie jar.
            assert client.get("/api/status").status_code == 200
            status = client.get("/api/auth/status").json()
            assert status == {"method": "oauth", "user": {"name": "Tester", "admin": False}}

            # Member: read-only — settings and actions are 403.
            assert client.get("/api/settings").status_code == 403
            assert client.get("/api/settings").json() == {"error": "admin required"}
            assert client.post("/api/actions/fetch").status_code == 403

            # Logout kills the session (deletion cookie + in-memory removal).
            assert client.post("/api/auth/logout").status_code == 204
            assert client.get("/api/status").status_code == 401

    def test_admin_gets_settings_and_actions(self, tmp_path: Path, monkeypatch) -> None:
        loop = LoopThread()
        try:
            client = self._client(tmp_path, monkeypatch, member={"roles": ["555"]}, loop=loop.loop)
            with client:
                self._login(client)

                assert client.get("/api/settings").status_code == 200
                assert client.get("/api/auth/status").json()["user"] == {
                    "name": "Tester",
                    "admin": True,
                }
                # fetch dispatches to the fake run function on the bot loop.
                res = client.post("/api/actions/fetch")
                assert res.status_code == 200
                assert res.json() == {"status": "ok", "message": "completed"}
        finally:
            loop.stop()

    def test_manage_guild_permission_grants_admin_with_member_response(self, tmp_path: Path, monkeypatch) -> None:
        # The member endpoint succeeds (no roles), but the guilds list entry
        # carries Manage Server (32) — that alone must grant admin.
        client = self._client(
            tmp_path, monkeypatch, member={"roles": []}, guilds=[{"id": "100", "permissions": "32"}]
        )
        with client:
            self._login(client)
            assert client.get("/api/auth/status").json()["user"] == {
                "name": "Tester",
                "admin": True,
            }
            assert client.get("/api/settings").status_code == 200

    def test_login_uses_configured_origin_not_request_host(self, tmp_path: Path, monkeypatch) -> None:
        """Host-header injection: the redirect URI never follows the Host."""
        env = dict(self.OAUTH_ENV)
        env["OAUTH_PUBLIC_ORIGIN"] = "http://dashboard.lan:8099/"
        db = tmp_path / "oa.db"
        _seed_db(db)
        client = TestClient(_app(db, _env(**env)), follow_redirects=False)
        with client:
            res = client.get("/api/auth/login", headers={"host": "attacker.example"})
        assert res.status_code == 302
        qs = parse_qs(urlparse(res.headers["location"]).query)
        assert qs["redirect_uri"] == ["http://dashboard.lan:8099/api/auth/callback"]

    def test_callback_exchange_uses_configured_origin(self, tmp_path: Path, monkeypatch) -> None:
        env = dict(self.OAUTH_ENV)
        env["OAUTH_PUBLIC_ORIGIN"] = "http://dashboard.lan:8099"
        db = tmp_path / "oa.db"
        _seed_db(db)
        client = TestClient(_app(db, _env(**env)), follow_redirects=False)
        self.exchange_calls: list[tuple[str, str, str, str]] = []
        monkeypatch.setattr(
            dashboard_auth, "exchange_code",
            lambda *a, **k: self.exchange_calls.append(a) or {"access_token": "at"},
        )
        monkeypatch.setattr(dashboard_auth, "fetch_user", lambda token: {"id": "u1", "username": "T"})
        monkeypatch.setattr(dashboard_auth, "fetch_guild_member", lambda token, guild_id: {"roles": []})
        monkeypatch.setattr(dashboard_auth, "fetch_guilds", lambda token: [])
        with client:
            res = client.get("/api/auth/login", headers={"host": "attacker.example"})
            state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
            res = client.get(f"/api/auth/callback?code=thecode&state={state}", headers={"host": "attacker.example"})
        assert res.status_code == 302
        assert self.exchange_calls[0][3] == "http://dashboard.lan:8099/api/auth/callback"

    def test_callback_secure_cookie_for_https_origin(self, tmp_path: Path, monkeypatch) -> None:
        env = dict(self.OAUTH_ENV)
        env["OAUTH_PUBLIC_ORIGIN"] = "https://dashboard.example.com"
        db = tmp_path / "oa.db"
        _seed_db(db)
        client = TestClient(_app(db, _env(**env)), follow_redirects=False)
        self.exchange_calls = []
        monkeypatch.setattr(dashboard_auth, "exchange_code", lambda *a, **k: {"access_token": "at"})
        monkeypatch.setattr(dashboard_auth, "fetch_user", lambda token: {"id": "u1", "username": "T"})
        monkeypatch.setattr(dashboard_auth, "fetch_guild_member", lambda token, guild_id: {"roles": []})
        monkeypatch.setattr(dashboard_auth, "fetch_guilds", lambda token: [])
        with client:
            state = parse_qs(urlparse(client.get("/api/auth/login").headers["location"]).query)["state"][0]
            res = client.get(f"/api/auth/callback?code=thecode&state={state}")
        assert "; Secure" in res.headers["set-cookie"]

    def test_oauth_responses_no_store_and_no_referrer(self, tmp_path: Path, monkeypatch) -> None:
        client = self._client(tmp_path, monkeypatch)
        with client:
            login = client.get("/api/auth/login")
            assert login.headers["cache-control"] == "no-store"
            assert login.headers["referrer-policy"] == "no-referrer"
            res = client.get(f"/api/auth/callback?code=thecode&state={self._login_state(client)}")
            assert res.headers["cache-control"] == "no-store"
            assert res.headers["referrer-policy"] == "no-referrer"
            assert client.post("/api/auth/logout").headers["cache-control"] == "no-store"

    def test_oauth_falls_back_to_token_without_public_origin(self, tmp_path: Path) -> None:
        env = dict(self.OAUTH_ENV)
        del env["OAUTH_PUBLIC_ORIGIN"]
        db = tmp_path / "oa.db"
        _seed_db(db)
        with TestClient(_app(db, _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_TOKEN="sekret", **env))) as client:
            assert client.get("/api/auth/status").json()["method"] == "token"
            assert client.get("/api/auth/login").status_code == 409

    def test_client_admin_query_does_not_escalate(self, tmp_path: Path, monkeypatch) -> None:
        """Role comes from Discord data only — client-supplied flags are inert."""
        client = self._client(tmp_path, monkeypatch)  # plain member (no admin role)
        with client:
            state = self._login_state(client)
            res = client.get(f"/api/auth/callback?code=thecode&state={state}&admin=true&role=555")
            assert res.status_code == 302
            assert client.get("/api/auth/status").json()["user"] == {"name": "Tester", "admin": False}
            assert client.get("/api/settings").status_code == 403

    def test_callback_not_a_member(self, tmp_path: Path, monkeypatch) -> None:
        client = self._client(tmp_path, monkeypatch, member=None)  # member endpoint 404s, guilds empty
        with client:
            state = self._login_state(client)
            res = client.get(f"/api/auth/callback?code=x&state={state}")

        assert res.status_code == 302
        assert res.headers["location"] == "/?#auth_error=not_a_member"

    @staticmethod
    def _login_state(client: TestClient) -> str:
        res = client.get("/api/auth/login")
        assert res.status_code == 302
        return parse_qs(urlparse(res.headers["location"]).query)["state"][0]

    def _login(self, client: TestClient) -> None:
        """Full login: state → callback; the cookie lands in the client jar."""
        state = self._login_state(client)
        res = client.get(f"/api/auth/callback?code=thecode&state={state}")
        assert res.status_code == 302
        assert res.headers["location"] == "/?auth=success"


class TestRateLimit:
    def test_actions_limited_per_window(self, tmp_path: Path) -> None:
        db = tmp_path / "rl.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_TOKEN="sekret")
        with TestClient(_app(db, env)) as client:
            headers = {"Authorization": "Bearer sekret"}
            statuses = [client.post("/api/actions/fetch", headers=headers).status_code for _ in range(7)]

        # 6 allowed (409 = bot not ready, past the middleware), then 429.
        assert statuses[:6] == [409] * 6
        assert statuses[6] == 429
        limited = client.post("/api/actions/fetch", headers=headers)
        assert limited.status_code == 429
        assert limited.json() == {"error": "rate limited"}
        assert int(limited.headers["Retry-After"]) > 0


class TestAuthStatus:
    def test_public_in_token_mode(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="0.0.0.0", DASHBOARD_TOKEN="sekret")
        with TestClient(_app(db, env)) as client:
            res = client.get("/api/auth/status")

        assert res.status_code == 200
        assert res.json() == {"method": "token", "user": None}

    def test_public_in_none_mode(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_db(db)
        with TestClient(_app(db, _env())) as client:  # loopback → none
            assert client.get("/api/auth/status").json() == {"method": "none", "user": None}

    def test_public_in_oauth_mode_without_session(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_db(db)
        env = _env(
            DASHBOARD_AUTH_MODE="oauth",
            OAUTH_CLIENT_ID="cid",
            OAUTH_CLIENT_SECRET="csec",
            OAUTH_GUILD_ID="100",
            OAUTH_PUBLIC_ORIGIN="http://testserver",
        )
        with TestClient(_app(db, env)) as client:
            assert client.get("/api/auth/status").json() == {"method": "oauth", "user": None}


# --- uvicorn bootstrap (bot.main.start_dashboard) ------------------------------


class TestBootstrap:
    def test_start_dashboard_serves_status_then_stops(self, tmp_path: Path) -> None:
        db = tmp_path / "smoke.db"
        _seed_db(db)
        env = _env(DASHBOARD_BIND="127.0.0.1", DASHBOARD_PORT=str(_free_port()))
        env["SQLITE_PATH"] = str(db)
        thread = bot_main.start_dashboard(lambda: _app(db, env), env)
        assert isinstance(thread, threading.Thread)
        port = int(env["DASHBOARD_PORT"])
        try:
            # Poll the real uvicorn server until it answers (bounded, no sleep-only).
            deadline = time.monotonic() + 10
            body: dict[str, object] = {}
            while time.monotonic() < deadline:
                try:
                    resp = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=1)
                    if resp.status_code == 200:
                        body = resp.json()
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            assert body.get("snapshot_date") == SNAPSHOT_DATE
        finally:
            thread.server.should_exit = True  # type: ignore[attr-defined]  # uvicorn stop hook (test-only)
            thread.join(timeout=5)
        assert not thread.is_alive()
