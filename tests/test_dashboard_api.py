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
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from travian import store
from travian.bot import main as bot_main
from travian.dashboard import app as dashboard_app
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
        env=env,
    )


def _app(
    db: Path,
    env: dict[str, str],
    *,
    run_fetch: FakeRunFetch | None = None,
    run_report: FakeRunReport | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
    get_status: Callable[[], dashboard_app.StatusData] | None = None,
) -> FastAPI:
    return create_app(
        _deps(db, env, run_fetch=run_fetch, run_report=run_report, loop=loop, get_status=get_status)
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


class TestAnalysisStandings:
    def test_series_rows_with_ours_flag(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_analysis_db(db)
        env = _env(TRACKED_ALLIANCES="NOVA,ENEMY")
        with TestClient(_app(db, env)) as client:
            payload = client.get("/api/analysis/standings?days=7").json()

        assert payload["dates"] == ["2026-08-07", "2026-08-08", "2026-08-09"]
        assert [row["tag"] for row in payload["series"]] == ["NOVA", "ENEMY"]  # latest-pop desc
        nova, enemy = payload["series"]
        assert nova["ours"] is True
        assert enemy["ours"] is False
        # Alliance-wide populations: Testland (1,2[,4/7]) + Borders row 5.
        assert [p[1] for p in nova["points"]] == [5100, 5602, 5704]
        assert len(nova["vp_points"]) == 3

    def test_unresolved_tags_skipped(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_analysis_db(db)
        env = _env(TRACKED_ALLIANCES="NOPE")
        with TestClient(_app(db, env)) as client:
            payload = client.get("/api/analysis/standings?days=7").json()

        assert payload == {"dates": ["2026-08-07", "2026-08-08", "2026-08-09"], "series": []}

    def test_empty_db_empty_payload(self, tmp_path: Path) -> None:
        db = tmp_path / "as.db"
        _seed_db(db, snapshot=False)
        with TestClient(_app(db, _env())) as client:
            assert client.get("/api/analysis/standings?days=7").json() == {"dates": [], "series": []}


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
        assert gained["x"] == 7
        assert gained["y"] == 7
        assert gained["region"] == "Testland"
        assert gained["event"] == "gained"
        assert gained["owner_tag"] is None
        assert gained["owner_player"] == "Player 7"
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
        assert nova == combined
        assert enemy == {"gained": [], "lost": []}

    def test_alliance_unknown_422(self, tmp_path: Path) -> None:
        db = tmp_path / "ae.db"
        _seed_analysis_db(db)
        with TestClient(_app(db, _env())) as client:
            res = client.get("/api/analysis/events?alliance=NOPE")

        assert res.status_code == 422
        assert "unknown alliance 'NOPE'" in res.json()["detail"]


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
