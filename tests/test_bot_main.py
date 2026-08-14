"""Integration tests for the bot entrypoint (task 9): startup validation,
settings merge (env + DB), the shared ``run_fetch``/``run_report`` functions,
the scheduler registration and the shared run lock.

No real Discord gateway is touched: the client is never ``.run()``'d, and the
channel surface is a fake (``FakeBot``/``FakeChannel``). async tests run via
``asyncio.run`` (pytest-asyncio is not a dependency); the run lock is bound
per event loop so multiple ``asyncio.run`` invocations get independent locks.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import discord
import pytest
from apscheduler.triggers.cron import CronTrigger  # pyright: ignore[reportMissingTypeStubs]
from fastapi.testclient import TestClient

from travian import store, strings
from travian.bot import main as bot_main
from travian.map_sql import MapSqlFetchError
from travian.models import VillageRow
from travian.strings import NO_DATA_YET

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "map_sql_sample.txt"
CHANNEL_ID = 111111111111111111
ALERT_CHANNEL_ID = 222222222222222222


# --- helpers -----------------------------------------------------------------


def _row(
    village_id: int,
    *,
    alliance_tag: str = "NOVA",
    alliance_id: int = 7,
    population: int = 100,
    region: str = "Testland",
) -> VillageRow:
    return VillageRow(
        village_id=village_id,
        x=village_id,
        y=village_id,
        tribe=1,
        name=f"Village {village_id}",
        player_id=1000 + village_id,
        player_name=f"Player {village_id}",
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
        "CHANNEL_ID": "111111111111111111",
        "ALLIANCE_TAGS": "NOVA",
        "FETCH_TZ": "Europe/London",
        "REPORT_TZ": "Europe/Warsaw",
    }
    env.update(overrides)
    return env


def _cfg(**overrides: object) -> bot_main.MergedConfig:
    values: dict[str, object] = {
        "discord_token": "test-token",
        "sqlite_path": "/tmp/bot.db",
        "channel_id": CHANNEL_ID,
        "alliance_tags": ["NOVA"],
        "fetch_hour": 0,
        "fetch_minute": 15,
        "fetch_tz": "Europe/London",
        "report_hour": 9,
        "report_minute": 0,
        "report_tz": "Europe/Warsaw",
        "admin_role_id": None,
        "report_embed_color": 0x2ECC71,
    }
    values.update(overrides)
    return bot_main.MergedConfig(**cast(Any, values))


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / "bot.db"


def _set_bot_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> None:
    env = _env(**overrides)
    env["SQLITE_PATH"] = str(_db_path(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _fetch_date() -> str:
    return datetime.now(ZoneInfo("Europe/London")).date().isoformat()


def _seed(conn: sqlite3.Connection, day: date, rows: list[VillageRow]) -> None:
    store.save_snapshot(conn, day.isoformat(), rows)


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[list[discord.Embed]] = []

    async def send(
        self,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | None = None,
        **kwargs: object,
    ) -> object:
        if embeds is not None:
            self.sent.append(embeds)
        elif embed is not None:
            self.sent.append([embed])
        else:
            self.sent.append([])
        return object()


class FakeBot:
    def __init__(self, channels: dict[int, FakeChannel]) -> None:
        self._channels = channels

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self._channels.get(channel_id)


class FailingChannel(FakeChannel):
    """``FakeChannel`` whose ``send`` always raises (alert-send failure seam)."""

    def __init__(self) -> None:
        super().__init__()
        self.send_attempts = 0

    async def send(self, embed: discord.Embed | None = None, embeds: list[discord.Embed] | None = None, **kwargs: object) -> object:
        self.send_attempts += 1
        raise RuntimeError("discord api down")


def _install_bot(channels: dict[int, FakeChannel]) -> FakeChannel:
    bot_main.current_bot = cast(bot_main.TravianBot, FakeBot(channels))
    return channels[CHANNEL_ID]


def _logs(db: Path) -> list[dict[str, str]]:
    conn = store.connect(db)
    try:
        return store.recent_logs(conn)
    finally:
        conn.close()


# --- startup validation -------------------------------------------------------


class TestValidateConfig:
    def test_valid_config_passes(self) -> None:
        bot_main.validate_config(_cfg())  # must not raise

    def test_missing_token_exits(self, caplog: pytest.LogCaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            bot_main.validate_config(_cfg(discord_token=""))
        assert exc.value.code == 1
        assert "DISCORD_TOKEN not set" in caplog.text

    def test_missing_channel_exits(self, caplog: pytest.LogCaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            bot_main.validate_config(_cfg(channel_id=None))
        assert exc.value.code == 1
        assert "CHANNEL_ID not set" in caplog.text

    def test_unknown_fetch_tz_exits(self, caplog: pytest.LogCaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            bot_main.validate_config(_cfg(fetch_tz="Mars/Olympus"))
        assert exc.value.code == 1
        assert "unknown timezone" in caplog.text

    def test_unknown_report_tz_exits(self, caplog: pytest.LogCaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            bot_main.validate_config(_cfg(report_tz="Nope/Nowhere"))
        assert exc.value.code == 1
        assert "unknown timezone" in caplog.text

    def test_empty_tz_exits(self, caplog: pytest.LogCaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            bot_main.validate_config(_cfg(fetch_tz=""))
        assert exc.value.code == 1
        assert "unknown timezone" in caplog.text

    @pytest.mark.parametrize("key", ["fetch_hour", "report_hour"])
    def test_hour_out_of_range_exits(self, key: str, caplog: pytest.LogCaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            bot_main.validate_config(_cfg(**{key: 25}))
        assert exc.value.code == 1
        assert "hour must be" in caplog.text

    def test_negative_hour_exits(self, caplog: pytest.LogCaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            bot_main.validate_config(_cfg(fetch_hour=-1))
        assert exc.value.code == 1
        assert "hour must be" in caplog.text

    @pytest.mark.parametrize("key", ["fetch_minute", "report_minute"])
    def test_minute_out_of_range_exits(self, key: str, caplog: pytest.LogCaptureFixture) -> None:
        with pytest.raises(SystemExit) as exc:
            bot_main.validate_config(_cfg(**{key: 60}))
        assert exc.value.code == 1
        assert "minute must be" in caplog.text

    def test_boundary_values_pass(self) -> None:
        bot_main.validate_config(
            _cfg(fetch_hour=0, fetch_minute=59, report_hour=23, report_minute=0)
        )  # must not raise

    def test_explicit_none_on_non_loopback_exits(self, caplog: pytest.LogCaptureFixture) -> None:
        env = {"DASHBOARD_AUTH_MODE": "none", "DASHBOARD_BIND": "0.0.0.0"}
        with pytest.raises(SystemExit) as exc:
            bot_main.validate_config(_cfg(), env)
        assert exc.value.code == 1
        assert "DASHBOARD_AUTH_MODE=none requires a loopback DASHBOARD_BIND" in caplog.text

    def test_explicit_none_on_loopback_passes(self) -> None:
        bot_main.validate_config(
            _cfg(), {"DASHBOARD_AUTH_MODE": "none", "DASHBOARD_BIND": "127.0.0.1"}
        )  # must not raise

    def test_auth_guard_skipped_without_env(self) -> None:
        # Tests call validate_config without env — the existing checks only.
        bot_main.validate_config(_cfg())  # must not raise


# --- settings merge ------------------------------------------------------------


class TestLoadMergedConfig:
    def test_defaults_when_nothing_set(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            cfg = bot_main.load_merged_config(conn, {})
        finally:
            conn.close()
        assert cfg.discord_token == ""
        assert cfg.sqlite_path == "/data/travian.db"
        assert cfg.channel_id is None
        assert cfg.alliance_tags == []
        assert (cfg.fetch_hour, cfg.fetch_minute, cfg.fetch_tz) == (0, 15, "Europe/London")
        assert (cfg.report_hour, cfg.report_minute, cfg.report_tz) == (9, 0, "Europe/Warsaw")
        assert cfg.admin_role_id is None
        assert cfg.report_embed_color == 0x2ECC71

    def test_env_values_parsed(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            cfg = bot_main.load_merged_config(
                conn,
                {"CHANNEL_ID": "12345", "ALLIANCE_TAGS": "A, B,,A", "FETCH_HOUR": "7", "ADMIN_ROLE_ID": "42"},
            )
        finally:
            conn.close()
        assert cfg.channel_id == 12345
        assert cfg.alliance_tags == ["A", "B"]
        assert cfg.fetch_hour == 7
        assert cfg.admin_role_id == 42

    def test_db_overrides_env(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        store.set_settings(
            conn,
            {"CHANNEL_ID": 999, "FETCH_HOUR": 5, "ALLIANCE_TAGS": ["DBTAG"], "REPORT_EMBED_COLOR": 12345},
        )
        try:
            cfg = bot_main.load_merged_config(
                conn, {"CHANNEL_ID": "1", "FETCH_HOUR": "2", "ALLIANCE_TAGS": "ENVTAG"}
            )
        finally:
            conn.close()
        assert cfg.channel_id == 999
        assert cfg.fetch_hour == 5
        assert cfg.alliance_tags == ["DBTAG"]
        assert cfg.report_embed_color == 12345

    def test_empty_env_values_are_unset(self) -> None:
        """Empty-string env values (e.g. `.env.example` placeholders) fall through
        to the settings table / defaults instead of failing to parse."""
        conn = store.connect(":memory:")
        store.init_schema(conn)
        store.set_settings(conn, {"CHANNEL_ID": 999})
        try:
            cfg = bot_main.load_merged_config(
                conn,
                {"CHANNEL_ID": "", "FETCH_HOUR": "", "FETCH_TZ": "", "ADMIN_ROLE_ID": "", "ALLIANCE_TAGS": ""},
            )
        finally:
            conn.close()
        assert cfg.channel_id == 999  # settings table wins over an empty env value
        assert cfg.fetch_hour == 0
        assert cfg.fetch_tz == "Europe/London"
        assert cfg.admin_role_id is None
        assert cfg.alliance_tags == []

    def test_token_never_from_db(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        store.set_settings(conn, {"DISCORD_TOKEN": "db-token"})
        try:
            cfg = bot_main.load_merged_config(conn, {"DISCORD_TOKEN": "env-token"})
        finally:
            conn.close()
        assert cfg.discord_token == "env-token"

    def test_token_empty_when_only_in_db(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        store.set_settings(conn, {"DISCORD_TOKEN": "db-token"})
        try:
            cfg = bot_main.load_merged_config(conn, {})
        finally:
            conn.close()
        assert cfg.discord_token == ""

    def test_sqlite_path_env_only(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        store.set_settings(conn, {"SQLITE_PATH": "/from/db.db"})
        try:
            cfg = bot_main.load_merged_config(conn, {"SQLITE_PATH": "/from/env.db"})
        finally:
            conn.close()
        assert cfg.sqlite_path == "/from/env.db"

    def test_color_env_hex_and_decimal(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            assert bot_main.load_merged_config(conn, {"REPORT_EMBED_COLOR": "0x2ECC71"}).report_embed_color == 0x2ECC71
            assert bot_main.load_merged_config(conn, {"REPORT_EMBED_COLOR": "65280"}).report_embed_color == 65280
        finally:
            conn.close()

    def test_color_env_out_of_range_raises(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            with pytest.raises(ValueError, match="between 0x000000 and 0xFFFFFF"):
                bot_main.load_merged_config(conn, {"REPORT_EMBED_COLOR": "0x1000000"})
            with pytest.raises(ValueError, match="between 0x000000 and 0xFFFFFF"):
                bot_main.load_merged_config(conn, {"REPORT_EMBED_COLOR": "-1"})
        finally:
            conn.close()

    def test_color_db_out_of_range_raises(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        store.set_settings(conn, {"REPORT_EMBED_COLOR": 0x1000000})
        try:
            with pytest.raises(ValueError, match="between 0x000000 and 0xFFFFFF"):
                bot_main.load_merged_config(conn, {})
        finally:
            conn.close()

    def test_bad_channel_id_raises(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            with pytest.raises(ValueError, match="CHANNEL_ID"):
                bot_main.load_merged_config(conn, {"CHANNEL_ID": "abc"})
        finally:
            conn.close()

    def test_alert_channel_id_unset_or_empty_is_none(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            assert bot_main.load_merged_config(conn, {}).alert_channel_id is None
            assert bot_main.load_merged_config(conn, {"ALERT_CHANNEL_ID": ""}).alert_channel_id is None
        finally:
            conn.close()

    def test_alert_channel_id_env_parsed(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            cfg = bot_main.load_merged_config(conn, {"ALERT_CHANNEL_ID": "222222222222222222"})
        finally:
            conn.close()
        assert cfg.alert_channel_id == 222222222222222222

    def test_alert_channel_id_invalid_raises(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            with pytest.raises(ValueError, match="ALERT_CHANNEL_ID"):
                bot_main.load_merged_config(conn, {"ALERT_CHANNEL_ID": "abc"})
        finally:
            conn.close()

    def test_alert_channel_id_never_read_from_settings_table(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        store.set_settings(conn, {"ALERT_CHANNEL_ID": 42})
        try:
            # Env-only by contract: a table value must NOT enable the alert.
            assert bot_main.load_merged_config(conn, {}).alert_channel_id is None
            # And env always wins over a table value.
            assert bot_main.load_merged_config(conn, {"ALERT_CHANNEL_ID": "7"}).alert_channel_id == 7
        finally:
            conn.close()

    def test_bad_hour_raises(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            with pytest.raises(ValueError, match="FETCH_HOUR"):
                bot_main.load_merged_config(conn, {"FETCH_HOUR": "abc"})
        finally:
            conn.close()

    def test_tags_dedupe_strip_case_sensitive(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            cfg = bot_main.load_merged_config(conn, {"ALLIANCE_TAGS": "A,,a, A, b"})
        finally:
            conn.close()
        assert cfg.alliance_tags == ["A", "a", "b"]

    def test_tracked_alliances_default_empty(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            cfg = bot_main.load_merged_config(conn, {})
        finally:
            conn.close()
        assert cfg.tracked_alliances == []

    def test_tracked_alliances_env_parsed(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            cfg = bot_main.load_merged_config(conn, {"TRACKED_ALLIANCES": "UFO, PR-U,,UFO, AAA"})
        finally:
            conn.close()
        assert cfg.tracked_alliances == ["UFO", "PR-U", "AAA"]

    def test_tracked_alliances_db_overrides_env(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        store.set_settings(conn, {"TRACKED_ALLIANCES": ["DB1", "DB2"]})
        try:
            cfg = bot_main.load_merged_config(conn, {"TRACKED_ALLIANCES": "ENV1"})
        finally:
            conn.close()
        assert cfg.tracked_alliances == ["DB1", "DB2"]

    def test_tracked_alliances_bad_type_raises(self) -> None:
        conn = store.connect(":memory:")
        store.init_schema(conn)
        try:
            with pytest.raises(TypeError, match="TRACKED_ALLIANCES"):
                bot_main.load_merged_config(conn, {"TRACKED_ALLIANCES": 5})
        finally:
            conn.close()


# --- _build_report_data ----------------------------------------------------------


class TestBuildReportData:
    def test_standings_wired_from_config(self) -> None:
        cfg = _cfg(tracked_alliances=["NOVA", "ENEMY"])
        curr = [
            _row(1, alliance_tag="NOVA", alliance_id=7, population=100),
            _row(2, alliance_tag="NOVA", alliance_id=7, population=200),
            _row(3, alliance_tag="ENEMY", alliance_id=8, population=50),
        ]
        prev = [
            _row(1, alliance_tag="NOVA", alliance_id=7, population=100),
            _row(3, alliance_tag="ENEMY", alliance_id=8, population=50),
        ]

        data = bot_main._build_report_data(cfg, "2026-08-08", curr, prev, {7})

        assert [s.tag for s in data.standings] == ["NOVA", "ENEMY"]
        assert (data.standings[0].population, data.standings[0].population_delta) == (300, 200)
        assert (data.standings[1].population, data.standings[1].population_delta) == (50, 0)

    def test_standings_empty_without_tracked(self) -> None:
        cfg = _cfg()
        curr = [_row(1)]

        data = bot_main._build_report_data(cfg, "2026-08-08", curr, None, {7})

        assert data.standings == []


# --- run_fetch ----------------------------------------------------------------


class TestRunFetch:
    def test_success_saves_snapshot_and_logs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))
        monkeypatch.setattr(bot_main, "fetch_map_sql", lambda url: FIXTURE_PATH.read_text())
        asyncio.run(bot_main.run_fetch())
        assert store.list_dates(store.connect(_db_path(tmp_path))) == [_fetch_date()]
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "fetch"
            and entry["level"] == "info"
            and entry["message"].startswith("snapshot saved for")
            for entry in logs
        )

    def test_fetch_error_logged_without_crash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))

        def fail(url: str) -> str:
            raise MapSqlFetchError("fetch failed after 4 attempts")

        monkeypatch.setattr(bot_main, "fetch_map_sql", fail)
        asyncio.run(bot_main.run_fetch())
        assert store.list_dates(store.connect(_db_path(tmp_path))) == []
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "fetch" and entry["level"] == "error" and "fetch failed after 4 attempts" in entry["message"]
            for entry in logs
        )

    def test_unexpected_error_logged_without_crash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))

        def boom(url: str) -> str:
            raise RuntimeError("unexpected")

        monkeypatch.setattr(bot_main, "fetch_map_sql", boom)
        asyncio.run(bot_main.run_fetch())
        logs = _logs(_db_path(tmp_path))
        assert any(entry["job"] == "fetch" and entry["level"] == "error" and "unexpected" in entry["message"] for entry in logs)


# --- run_report ---------------------------------------------------------------


class TestRunReport:
    def test_with_data_sends_embed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        today = date.fromisoformat(_fetch_date())
        _seed(conn, today - timedelta(days=1), [_row(1, population=100)])
        _seed(conn, today, [_row(1, population=110), _row(2, population=50)])
        conn.close()
        channel = _install_bot({CHANNEL_ID: FakeChannel()})
        asyncio.run(bot_main.run_report(CHANNEL_ID, require_today=True))
        assert len(channel.sent) == 1  # one message
        sent = channel.sent[0]
        assert 1 <= len(sent) <= 4  # up to 4 embeds in one message
        assert all(isinstance(e, discord.Embed) for e in sent)
        assert "NOVA" in (sent[0].description or "")
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "report"
            and entry["level"] == "info"
            and f"report sent to channel {CHANNEL_ID}" in entry["message"]
            for entry in logs
        )

    def test_no_data_sends_no_data_embed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))
        channel = _install_bot({CHANNEL_ID: FakeChannel()})
        asyncio.run(bot_main.run_report(CHANNEL_ID))
        assert len(channel.sent) == 1
        sent = channel.sent[0]
        assert len(sent) == 1
        embed = sent[0]
        assert isinstance(embed, discord.Embed)
        assert embed.description == NO_DATA_YET
        logs = _logs(_db_path(tmp_path))
        assert any(entry["job"] == "report" and entry["level"] == "info" and "no-data" in entry["message"] for entry in logs)

    def test_stale_skips_when_require_today(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        yesterday = date.fromisoformat(_fetch_date()) - timedelta(days=1)
        _seed(conn, yesterday, [_row(1)])
        conn.close()
        channel = _install_bot({CHANNEL_ID: FakeChannel()})
        asyncio.run(bot_main.run_report(CHANNEL_ID))
        assert channel.sent == []
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "report" and entry["level"] == "warning" and "no snapshot for today" in entry["message"]
            for entry in logs
        )

    def test_stale_sends_when_require_today_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        yesterday = date.fromisoformat(_fetch_date()) - timedelta(days=1)
        _seed(conn, yesterday, [_row(1)])
        conn.close()
        channel = _install_bot({CHANNEL_ID: FakeChannel()})
        asyncio.run(bot_main.run_report(CHANNEL_ID, require_today=False))
        assert len(channel.sent) == 1
        sent = channel.sent[0]
        assert 1 <= len(sent) <= 4
        assert all(isinstance(e, discord.Embed) for e in sent)
        assert "NOVA" in (sent[0].description or "")

    @pytest.mark.parametrize("tags", ["NOPE", ""])
    def test_unresolved_or_empty_tags_skip(
        self, tags: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS=tags)
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()
        channel = _install_bot({CHANNEL_ID: FakeChannel()})
        asyncio.run(bot_main.run_report(CHANNEL_ID))
        assert channel.sent == []
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "report" and entry["level"] == "warning" and "no alliance configured" in entry["message"]
            for entry in logs
        )

    def test_channel_not_found_logs_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()
        bot_main.current_bot = cast(bot_main.TravianBot, FakeBot({}))  # get_channel -> None
        asyncio.run(bot_main.run_report(CHANNEL_ID))
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "report" and entry["level"] == "error" and f"channel {CHANNEL_ID} not found" in entry["message"]
            for entry in logs
        )

    def test_send_failure_logged_and_lock_released(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()

        class ExplodingChannel(FakeChannel):
            async def send(self, embed: discord.Embed | None = None, **kwargs: object) -> object:
                raise RuntimeError("boom")

        _install_bot({CHANNEL_ID: ExplodingChannel()})
        asyncio.run(bot_main.run_report(CHANNEL_ID))
        logs = _logs(_db_path(tmp_path))
        assert any(entry["job"] == "report" and entry["level"] == "error" and "boom" in entry["message"] for entry in logs)

        # The lock must have been released by the failed run: a second run works.
        channel = _install_bot({CHANNEL_ID: FakeChannel()})
        asyncio.run(bot_main.run_report(CHANNEL_ID))
        assert len(channel.sent) == 1


# --- shared run lock -----------------------------------------------------------


class TestRunLock:
    def test_concurrent_run_skipped_and_logged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()

        started = asyncio.Event()
        gate = asyncio.Event()

        class BlockingChannel(FakeChannel):
            async def send(self, embed: discord.Embed | None = None, **kwargs: object) -> object:
                started.set()
                await gate.wait()
                return await super().send(embed, **kwargs)

        channel = BlockingChannel()
        _install_bot({CHANNEL_ID: channel})

        async def scenario() -> None:
            first = asyncio.create_task(bot_main.run_report(CHANNEL_ID))
            await started.wait()  # first run holds the lock inside channel.send
            status = await bot_main.run_report(CHANNEL_ID)  # must skip, not queue
            gate.set()
            await first
            assert status == bot_main.REPORT_STATUS_SKIPPED

        asyncio.run(scenario())
        assert len(channel.sent) == 1  # only the first run sent
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "report" and entry["level"] == "warning" and "already running" in entry["message"]
            for entry in logs
        )


# --- run status returns (T12, decision (a)) -----------------------------------


class TestRunStatusReturns:
    def test_run_fetch_completed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))
        monkeypatch.setattr(bot_main, "fetch_map_sql", lambda url: FIXTURE_PATH.read_text())
        assert asyncio.run(bot_main.run_fetch()) == bot_main.FETCH_STATUS_COMPLETED

    def test_run_fetch_empty_parse(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))
        monkeypatch.setattr(bot_main, "fetch_map_sql", lambda url: "")
        assert asyncio.run(bot_main.run_fetch()) == bot_main.FETCH_STATUS_EMPTY_PARSE

    def test_run_fetch_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))

        def fail(url: str) -> str:
            raise MapSqlFetchError("fetch failed after 4 attempts")

        monkeypatch.setattr(bot_main, "fetch_map_sql", fail)
        assert asyncio.run(bot_main.run_fetch()) == bot_main.FETCH_STATUS_FAILED

    def test_run_report_sent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()
        _install_bot({CHANNEL_ID: FakeChannel()})
        assert asyncio.run(bot_main.run_report(CHANNEL_ID)) == bot_main.REPORT_STATUS_SENT

    def test_run_report_no_data(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))
        _install_bot({CHANNEL_ID: FakeChannel()})
        assert asyncio.run(bot_main.run_report(CHANNEL_ID)) == bot_main.REPORT_STATUS_NO_DATA

    def test_run_report_no_snapshot_for_today(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()) - timedelta(days=1), [_row(1)])
        conn.close()
        _install_bot({CHANNEL_ID: FakeChannel()})
        assert asyncio.run(bot_main.run_report(CHANNEL_ID)) == bot_main.REPORT_STATUS_NO_SNAPSHOT_TODAY

    def test_run_report_no_alliance(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOPE")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()
        _install_bot({CHANNEL_ID: FakeChannel()})
        assert asyncio.run(bot_main.run_report(CHANNEL_ID)) == bot_main.REPORT_STATUS_NO_ALLIANCE

    def test_run_report_channel_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()
        bot_main.current_bot = cast(bot_main.TravianBot, FakeBot({}))  # get_channel -> None
        assert asyncio.run(bot_main.run_report(CHANNEL_ID)) == bot_main.REPORT_STATUS_CHANNEL_NOT_FOUND


# --- failure alerts (freshness & alerts) --------------------------------------


class TestFailureAlertEmbed:
    def test_build_failure_alert_normalizes_and_caps_reason(self) -> None:
        embed = bot_main.build_failure_alert("fetch", "line1\n\n  line2   with  spaces", "2026-08-13T10:00:00+00:00")

        assert embed.title == strings.ALERT_TITLE
        assert "fetch failed at 2026-08-13T10:00:00+00:00." in embed.description
        assert "line1 line2 with spaces" in embed.description  # one line
        assert "\nline1\nline2" not in embed.description  # no multi-line reason
        assert "See the dashboard job log for details." in embed.description
        assert embed.colour.value == 0xD47769

    def test_build_failure_alert_truncates_long_reason(self) -> None:
        embed = bot_main.build_failure_alert("report", "boom " * 500, "2026-08-13T10:00:00+00:00")

        assert len(embed.description) < 4096  # Discord description limit
        # The capped one-line reason ends with … right before the trailer.
        assert "…\n\nSee the dashboard job log for details." in embed.description
        assert "boom \nboom" not in embed.description  # reason is still a single line


class TestFailureAlerts:
    def test_alerts_disabled_send_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)  # no ALERT_CHANNEL_ID
        store.init_schema(store.connect(_db_path(tmp_path)))
        channels = {CHANNEL_ID: FakeChannel()}
        _ = _install_bot(channels)

        def fail(url: str) -> str:
            raise MapSqlFetchError("fetch failed after 4 attempts")

        monkeypatch.setattr(bot_main, "fetch_map_sql", fail)
        assert asyncio.run(bot_main.run_fetch()) == bot_main.FETCH_STATUS_FAILED

        assert channels[CHANNEL_ID].sent == []  # nothing was sent anywhere
        assert not any(e["job"] == "alert" for e in _logs(_db_path(tmp_path)))

    def test_fetch_failure_alerts_once_per_utc_day(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALERT_CHANNEL_ID=str(ALERT_CHANNEL_ID))
        store.init_schema(store.connect(_db_path(tmp_path)))
        channels = {CHANNEL_ID: FakeChannel(), ALERT_CHANNEL_ID: FakeChannel()}
        _ = _install_bot(channels)

        def fail(url: str) -> str:
            raise MapSqlFetchError("fetch failed after 4 attempts")

        monkeypatch.setattr(bot_main, "fetch_map_sql", fail)

        async def scenario() -> None:
            assert await bot_main.run_fetch() == bot_main.FETCH_STATUS_FAILED
            # Same job failing again on the same UTC day: deduped, no second send.
            assert await bot_main.run_fetch() == bot_main.FETCH_STATUS_FAILED

        asyncio.run(scenario())

        alert_channel = channels[ALERT_CHANNEL_ID]
        assert len(alert_channel.sent) == 1
        embed = alert_channel.sent[0][0]
        assert embed.title == strings.ALERT_TITLE
        assert "fetch failed at" in embed.description
        assert "fetch failed after 4 attempts" in embed.description
        assert "See the dashboard job log for details." in embed.description
        logs = _logs(_db_path(tmp_path))
        markers = [e for e in logs if e["job"] == "alert" and e["level"] == "info"]
        assert len(markers) == 1  # one marker survives both failures
        assert markers[0]["message"].startswith("failure-alert:fetch:")
        assert markers[0]["message"].endswith(f":{ALERT_CHANNEL_ID}")

    def test_empty_parse_alerts_with_fixed_reason(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALERT_CHANNEL_ID=str(ALERT_CHANNEL_ID))
        store.init_schema(store.connect(_db_path(tmp_path)))
        channels = {CHANNEL_ID: FakeChannel(), ALERT_CHANNEL_ID: FakeChannel()}
        _ = _install_bot(channels)
        monkeypatch.setattr(bot_main, "fetch_map_sql", lambda url: "")

        assert asyncio.run(bot_main.run_fetch()) == bot_main.FETCH_STATUS_EMPTY_PARSE

        embed = channels[ALERT_CHANNEL_ID].sent[0][0]
        assert "empty parse (0 villages) from map.sql, snapshot not saved" in embed.description

    def test_empty_parse_alert_holds_run_lock(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The shared run lock stays held through the alert send + marker
        write on the EMPTY-PARSE path too: a concurrent run is skipped, so
        two claims of the same job/day marker can never race."""
        _set_bot_env(monkeypatch, tmp_path, ALERT_CHANNEL_ID=str(ALERT_CHANNEL_ID))
        store.init_schema(store.connect(_db_path(tmp_path)))
        entered = asyncio.Event()
        release = asyncio.Event()

        class BlockingChannel(FakeChannel):
            async def send(
                self,
                embed: discord.Embed | None = None,
                embeds: list[discord.Embed] | None = None,
                **kwargs: object,
            ) -> object:
                entered.set()
                await release.wait()
                return await super().send(embed=embed, embeds=embeds, **kwargs)

        channels = {CHANNEL_ID: FakeChannel(), ALERT_CHANNEL_ID: BlockingChannel()}
        _ = _install_bot(channels)
        monkeypatch.setattr(bot_main, "fetch_map_sql", lambda url: "")

        async def scenario() -> None:
            first = asyncio.create_task(bot_main.run_fetch())
            await entered.wait()  # the first run is inside the alert send
            # The lock must still be held: a second run is SKIPPED, never queued.
            assert await bot_main.run_fetch() == bot_main.FETCH_STATUS_SKIPPED
            release.set()
            assert await first == bot_main.FETCH_STATUS_EMPTY_PARSE

        asyncio.run(scenario())

        assert len(channels[ALERT_CHANNEL_ID].sent) == 1  # the blocked send landed
        markers = [e for e in _logs(_db_path(tmp_path)) if e["job"] == "alert" and e["level"] == "info"]
        assert len(markers) == 1  # marker written only after the send completed

    def test_report_failure_alerts_and_fetch_alerts_independently(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA", ALERT_CHANNEL_ID=str(ALERT_CHANNEL_ID))
        store.init_schema(store.connect(_db_path(tmp_path)))
        channels = {CHANNEL_ID: FakeChannel(), ALERT_CHANNEL_ID: FakeChannel()}
        _ = _install_bot(channels)

        # Fetch failure first (its own marker + embed)…
        def fail(url: str) -> str:
            raise MapSqlFetchError("fetch failed after 4 attempts")

        monkeypatch.setattr(bot_main, "fetch_map_sql", fail)
        # …then a report compute failure (a DIFFERENT job: must alert independently).
        monkeypatch.setattr(
            bot_main,
            "_report_phase",
            lambda require_today: bot_main._ReportPhase(
                action="failed", embeds=[], failure_reason="report compute boom"
            ),
        )

        async def scenario() -> None:
            assert await bot_main.run_fetch() == bot_main.FETCH_STATUS_FAILED
            assert await bot_main.run_report(CHANNEL_ID) == bot_main.REPORT_STATUS_FAILED

        asyncio.run(scenario())

        alert_channel = channels[ALERT_CHANNEL_ID]
        assert len(alert_channel.sent) == 2
        report_embed = alert_channel.sent[1][0]
        assert "report failed at" in report_embed.description
        assert "report compute boom" in report_embed.description
        markers = [e["message"] for e in _logs(_db_path(tmp_path)) if e["job"] == "alert" and e["level"] == "info"]
        assert len(markers) == 2
        assert any(m.startswith("failure-alert:fetch:") for m in markers)
        assert any(m.startswith("failure-alert:report:") for m in markers)

    def test_report_channel_not_found_alerts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA", ALERT_CHANNEL_ID=str(ALERT_CHANNEL_ID))
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()
        # The REPORT channel is missing; only the alert channel resolves.
        channels = {ALERT_CHANNEL_ID: FakeChannel()}
        bot_main.current_bot = cast(bot_main.TravianBot, FakeBot(channels))

        assert asyncio.run(bot_main.run_report(CHANNEL_ID)) == bot_main.REPORT_STATUS_CHANNEL_NOT_FOUND

        embed = channels[ALERT_CHANNEL_ID].sent[0][0]
        assert f"channel {CHANNEL_ID} not found" in embed.description

    def test_alert_send_failure_preserves_status_and_releases_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALERT_CHANNEL_ID=str(ALERT_CHANNEL_ID))
        store.init_schema(store.connect(_db_path(tmp_path)))
        alert_channel = FailingChannel()
        bot_main.current_bot = cast(bot_main.TravianBot, FakeBot({ALERT_CHANNEL_ID: alert_channel}))
        state = {"fail": True}

        def fetch(url: str) -> str:
            if state["fail"]:
                raise MapSqlFetchError("fetch failed after 4 attempts")
            return FIXTURE_PATH.read_text()

        monkeypatch.setattr(bot_main, "fetch_map_sql", fetch)

        async def scenario() -> None:
            # Original failed status preserved despite the alert-send failure…
            assert await bot_main.run_fetch() == bot_main.FETCH_STATUS_FAILED
            state["fail"] = False
            # …and the shared run lock was released (a second run is NOT skipped).
            assert await bot_main.run_fetch() == bot_main.FETCH_STATUS_COMPLETED

        asyncio.run(scenario())

        assert alert_channel.send_attempts == 1
        logs = _logs(_db_path(tmp_path))
        assert any(
            e["job"] == "alert" and e["level"] == "error" and "failure alert for fetch failed" in e["message"]
            for e in logs
        )
        # The marker was never written (the send never succeeded) — a later
        # retry may legitimately send again.
        assert not any(e["job"] == "alert" and e["level"] == "info" for e in logs)


# --- bot class: on_ready + scheduler -------------------------------------------


def _field(trigger: CronTrigger, name: str) -> int:
    return next(f for f in trigger.fields if f.name == name).expressions[0].first  # type: ignore[union-attr]


class TestBot:
    def test_dashboard_readyz_reflects_runtime_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Real factory wiring: /readyz is 503 until BOTH the bot loop and
        the scheduler exist, then 200 (the deploy acceptance probe)."""
        monkeypatch.setattr(bot_main, "bot_loop", None)
        monkeypatch.setattr(bot_main, "_current_config", lambda: _cfg())
        db_path = tmp_path / "readyz.db"
        conn = store.connect(db_path)
        store.init_schema(conn)  # production: main() runs init_schema at startup
        conn.close()
        bot = bot_main.TravianBot(_cfg())
        factory = bot_main._dashboard_app_factory({"SQLITE_PATH": str(db_path)}, bot)

        # Nothing ready yet → 503.
        with TestClient(factory()) as client:
            resp = client.get("/readyz")
            assert resp.status_code == 503
            assert resp.json()["status"] == "not_ready"
            assert resp.json()["bot_ready"] is False
            assert resp.json()["scheduler_ready"] is False

            # Scheduler present, bot loop still missing → still not ready.
            bot.scheduler = FakeScheduler()  # pyright: ignore[reportAttributeAccessIssue]
            resp = client.get("/readyz")
            assert resp.status_code == 503
            assert resp.json()["scheduler_ready"] is True
            assert resp.json()["bot_ready"] is False

        # Bot loop present (module global, as set by on_ready) → ready.
        loop = asyncio.new_event_loop()
        monkeypatch.setattr(bot_main, "bot_loop", loop)
        with TestClient(factory()) as client:
            resp = client.get("/readyz")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ready"
            assert resp.json()["bot_ready"] is True
            assert resp.json()["scheduler_ready"] is True
        loop.close()

    def test_on_ready_syncs_and_starts_scheduler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def scenario() -> None:
            bot = bot_main.TravianBot(_cfg())
            # on_ready re-reads the current config; pin it to a fixed value so
            # the test stays independent of the environment's settings table.
            monkeypatch.setattr(bot_main, "_current_config", lambda: _cfg())

            async def fake_sync() -> list[object]:
                return []

            monkeypatch.setattr(bot.tree, "sync", fake_sync)
            await bot.on_ready()
            try:
                assert bot.scheduler is not None
                assert {job.id for job in bot.scheduler.get_jobs()} == {"job_fetch", "job_report"}
                assert bot_main.bot_loop is asyncio.get_running_loop()
            finally:
                bot.scheduler.shutdown(wait=False)  # type: ignore[union-attr]

        asyncio.run(scenario())

    def test_on_ready_starts_scheduler_from_saved_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Triggers must start from the CURRENT table — a save made before
        ``on_ready`` (bot still logging in) is honored without a reschedule."""
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        store.set_settings(conn, {"FETCH_HOUR": 4, "FETCH_MINUTE": 45})
        conn.close()
        monkeypatch.setenv("SQLITE_PATH", str(_db_path(tmp_path)))

        async def scenario() -> None:
            bot = bot_main.TravianBot(_cfg())  # constructor config: defaults

            async def fake_sync() -> list[object]:
                return []

            monkeypatch.setattr(bot.tree, "sync", fake_sync)
            await bot.on_ready()
            try:
                assert bot.scheduler is not None
                jobs = {job.id: job for job in bot.scheduler.get_jobs()}
                fetch_trigger = jobs["job_fetch"].trigger
                assert isinstance(fetch_trigger, CronTrigger)
                assert _field(fetch_trigger, "hour") == 4
                assert _field(fetch_trigger, "minute") == 45
                assert bot.cfg.fetch_hour == 4
                assert bot.cfg.fetch_minute == 45
            finally:
                bot.scheduler.shutdown(wait=False)  # type: ignore[union-attr]

        asyncio.run(scenario())

    def test_scheduler_triggers_match_config(self) -> None:
        cfg = _cfg(
            fetch_hour=3,
            fetch_minute=45,
            fetch_tz="Europe/London",
            report_hour=11,
            report_minute=30,
            report_tz="Europe/Warsaw",
        )

        async def scenario() -> None:
            bot = bot_main.TravianBot(cfg)
            bot._start_scheduler(cfg)
            assert bot.scheduler is not None
            try:
                jobs = {job.id: job for job in bot.scheduler.get_jobs()}
                assert jobs["job_fetch"].func is bot_main.job_fetch
                assert jobs["job_report"].func is bot_main.job_report
                fetch_trigger = jobs["job_fetch"].trigger
                assert isinstance(fetch_trigger, CronTrigger)
                assert fetch_trigger.timezone == ZoneInfo("Europe/London")
                assert _field(fetch_trigger, "hour") == 3
                assert _field(fetch_trigger, "minute") == 45
                report_trigger = jobs["job_report"].trigger
                assert isinstance(report_trigger, CronTrigger)
                assert report_trigger.timezone == ZoneInfo("Europe/Warsaw")
                assert _field(report_trigger, "hour") == 11
                assert _field(report_trigger, "minute") == 30
            finally:
                bot.scheduler.shutdown(wait=False)  # type: ignore[union-attr]

        asyncio.run(scenario())


class FakeScheduler:
    """Scheduler fake for ``_reschedule``: records every reschedule call."""

    def __init__(self) -> None:
        self.reschedules: list[tuple[str, CronTrigger]] = []
        self._jobs: dict[str, CronTrigger] = {}

    def add_job(self, func: object, trigger: CronTrigger, *, id: str) -> None:
        self._jobs[id] = trigger

    def start(self) -> None: ...

    def shutdown(self, *, wait: bool = True) -> None: ...

    def get_jobs(self) -> list[object]:
        return []

    def reschedule_job(self, job_id: str, trigger: CronTrigger) -> object:
        self.reschedules.append((job_id, trigger))
        self._jobs[job_id] = trigger
        return object()


class TestReschedule:
    def test_fetch_change_reschedules_only_fetch(self) -> None:
        bot = bot_main.TravianBot(_cfg())
        fake = FakeScheduler()
        bot.scheduler = fake  # pyright: ignore[reportAttributeAccessIssue]

        changed = bot._reschedule(_cfg(fetch_hour=7))

        assert changed is True
        assert [job_id for job_id, _ in fake.reschedules] == ["job_fetch"]
        assert _field(fake.reschedules[0][1], "hour") == 7

    def test_report_change_reschedules_only_report(self) -> None:
        bot = bot_main.TravianBot(_cfg())
        fake = FakeScheduler()
        bot.scheduler = fake  # pyright: ignore[reportAttributeAccessIssue]

        changed = bot._reschedule(_cfg(report_hour=11, report_minute=30))

        assert changed is True
        assert [job_id for job_id, _ in fake.reschedules] == ["job_report"]
        assert _field(fake.reschedules[0][1], "hour") == 11
        assert _field(fake.reschedules[0][1], "minute") == 30

    def test_tz_change_reschedules_both_halves_independently(self) -> None:
        bot = bot_main.TravianBot(_cfg())
        fake = FakeScheduler()
        bot.scheduler = fake  # pyright: ignore[reportAttributeAccessIssue]

        changed = bot._reschedule(_cfg(fetch_hour=5, report_tz="Europe/London"))

        assert changed is True
        assert [job_id for job_id, _ in fake.reschedules] == ["job_fetch", "job_report"]

    def test_identical_config_no_reschedule(self) -> None:
        bot = bot_main.TravianBot(_cfg())
        fake = FakeScheduler()
        bot.scheduler = fake  # pyright: ignore[reportAttributeAccessIssue]

        assert bot._reschedule(_cfg()) is False
        assert fake.reschedules == []
        assert bot.cfg == _cfg()  # reference config updated regardless

    def test_no_scheduler_updates_cfg_without_reschedule(self) -> None:
        bot = bot_main.TravianBot(_cfg())
        assert bot.scheduler is None

        assert bot._reschedule(_cfg(fetch_hour=6)) is False
        assert bot.cfg.fetch_hour == 6


# --- entry point ----------------------------------------------------------------


class TestMain:
    def test_main_exits_1_without_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        monkeypatch.setenv("SQLITE_PATH", str(_db_path(tmp_path)))
        monkeypatch.delenv("DISCORD_TOKEN", raising=False)
        monkeypatch.delenv("CHANNEL_ID", raising=False)
        with pytest.raises(SystemExit) as exc:
            bot_main.main()
        assert exc.value.code == 1
        assert "DISCORD_TOKEN not set" in caplog.text

    def test_main_exits_1_on_bad_tz(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        monkeypatch.setenv("SQLITE_PATH", str(_db_path(tmp_path)))
        monkeypatch.setenv("DISCORD_TOKEN", "test-token")
        monkeypatch.setenv("CHANNEL_ID", "111111111111111111")
        monkeypatch.setenv("FETCH_TZ", "Mars/Olympus")
        with pytest.raises(SystemExit) as exc:
            bot_main.main()
        assert exc.value.code == 1
        assert "unknown timezone" in caplog.text


# --- run_fetch: T10 — empty-parse guard + off-loop blocking -------------------


class TestRunFetchT10:
    @pytest.mark.parametrize("body", ["", "garbage that is not map.sql"])
    def test_empty_parse_guard_logs_error_and_skips_snapshot(
        self, body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))
        monkeypatch.setattr(bot_main, "fetch_map_sql", lambda url: body)
        asyncio.run(bot_main.run_fetch())
        # 0 parsed rows (even with HTTP 200) → the guard must NOT call save_snapshot
        assert store.list_dates(store.connect(_db_path(tmp_path))) == []
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "fetch"
            and entry["level"] == "error"
            and "empty parse" in entry["message"]
            for entry in logs
        )

    def test_blocking_fetch_does_not_freeze_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))

        def slow_fetch(url: str) -> str:
            time.sleep(0.3)  # real blocking — runs in the to_thread worker
            return FIXTURE_PATH.read_text()

        monkeypatch.setattr(bot_main, "fetch_map_sql", slow_fetch)

        async def scenario() -> None:
            fetch_task = asyncio.create_task(bot_main.run_fetch())
            await asyncio.sleep(0.05)
            # The loop must stay alive while the fetch blocks in a worker
            # thread. Against the pre-T10 code this line would only run AFTER
            # the fetch finished → fetch_task.done() → assertion fails (red).
            assert not fetch_task.done()
            await fetch_task

        asyncio.run(scenario())
        assert store.list_dates(store.connect(_db_path(tmp_path))) == [_fetch_date()]

    def test_fetch_and_sqlite_phase_run_via_to_thread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))
        fetch_mock = lambda url: FIXTURE_PATH.read_text()
        monkeypatch.setattr(bot_main, "fetch_map_sql", fetch_mock)

        real_to_thread = asyncio.to_thread
        calls: list[object] = []

        def spy(func: Any, *args: Any, **kwargs: Any) -> Any:
            calls.append(func)
            return real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        asyncio.run(bot_main.run_fetch())
        # run_fetch makes exactly two to_thread calls: the fetch and the
        # sqlite phase (own connection) — nothing blocking runs on the loop.
        assert calls == [fetch_mock, bot_main._fetch_snapshot_phase]


# --- run_report: T10 — off-loop read+compute phase, gap deltas ----------------


class TestRunReportT10:
    def test_gap_between_snapshots_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        today = date.fromisoformat(_fetch_date())
        _seed(conn, today - timedelta(days=3), [_row(1, population=100)])
        _seed(conn, today, [_row(1, population=110)])
        conn.close()
        channel = _install_bot({CHANNEL_ID: FakeChannel()})
        asyncio.run(bot_main.run_report(CHANNEL_ID))
        # deltas across the gap are computed (prev = 3 days back), not None
        assert len(channel.sent) == 1
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "report"
            and entry["level"] == "info"
            and entry["message"].startswith("deltas computed across gap: prev ")
            and (today - timedelta(days=3)).isoformat() in entry["message"]
            for entry in logs
        )

    def test_consecutive_days_no_gap_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        today = date.fromisoformat(_fetch_date())
        _seed(conn, today - timedelta(days=1), [_row(1, population=100)])
        _seed(conn, today, [_row(1, population=110)])
        conn.close()
        _install_bot({CHANNEL_ID: FakeChannel()})
        asyncio.run(bot_main.run_report(CHANNEL_ID))
        logs = _logs(_db_path(tmp_path))
        assert not any("deltas computed across gap" in entry["message"] for entry in logs)

    def test_read_compute_phase_runs_via_to_thread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()
        _install_bot({CHANNEL_ID: FakeChannel()})

        real_to_thread = asyncio.to_thread
        calls: list[str] = []

        def spy(func: Any, *args: Any, **kwargs: Any) -> Any:
            calls.append(getattr(func, "__name__", str(func)))
            return real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        asyncio.run(bot_main.run_report(CHANNEL_ID))
        assert "_report_phase" in calls


# --- /wioski + /regiony section runners --------------------------------------------


class TestSectionRunners:
    def test_run_villages_returns_villages_embed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        today = date.fromisoformat(_fetch_date())
        _seed(conn, today - timedelta(days=1), [_row(1, population=100)])
        _seed(conn, today, [_row(1, population=110), _row(2, population=50)])  # row 2 is gained
        conn.close()
        embeds = asyncio.run(bot_main.run_villages())
        assert [e.title for e in embeds] == [strings.EMBED_TITLE_VILLAGES]
        assert "# New Villages" in (embeds[0].description or "")

    def test_run_regions_returns_full_regions_embed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        today = date.fromisoformat(_fetch_date())
        _seed(conn, today - timedelta(days=1), [_row(1, population=100)])
        _seed(conn, today, [_row(1, population=110)])
        conn.close()
        embeds = asyncio.run(bot_main.run_regions())
        assert [e.title for e in embeds] == [strings.EMBED_TITLE_REGIONS]
        description = embeds[0].description or ""
        # The on-demand command carries the FULL table (no region_limit) —
        # all rows render, no more-line inside the fence.
        assert "Testland" in description
        assert "…and " not in description
        assert strings.REGION_LEGEND in description

    def test_run_villages_empty_without_snapshot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))
        assert asyncio.run(bot_main.run_villages()) == []

    def test_run_villages_empty_without_alliance(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOPE")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()
        assert asyncio.run(bot_main.run_villages()) == []

    def test_run_villages_empty_when_no_events(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        today = date.fromisoformat(_fetch_date())
        _seed(conn, today - timedelta(days=1), [_row(1)])
        _seed(conn, today, [_row(1, population=110)])  # no new/lost villages
        conn.close()
        assert asyncio.run(bot_main.run_villages()) == []


# --- job_report: T10 — resolved-subset pre-check --------------------------------


class TestJobReport:
    @staticmethod
    def _spy_run_report(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, bool]]:
        calls: list[tuple[int, bool]] = []

        async def spy(channel_id: int, require_today: bool = True, run_id: str | None = None) -> None:
            calls.append((channel_id, require_today))

        monkeypatch.setattr(bot_main, "run_report", spy)
        return calls

    def test_no_channel_skips(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))
        monkeypatch.delenv("CHANNEL_ID")
        calls = self._spy_run_report(monkeypatch)
        asyncio.run(bot_main.job_report())
        assert calls == []
        logs = _logs(_db_path(tmp_path))
        assert any("CHANNEL_ID not set" in entry["message"] for entry in logs)

    def test_empty_tags_skip_before_run_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="")
        store.init_schema(store.connect(_db_path(tmp_path)))
        calls = self._spy_run_report(monkeypatch)
        asyncio.run(bot_main.job_report())
        assert calls == []
        logs = _logs(_db_path(tmp_path))
        assert any(
            entry["job"] == "report"
            and entry["level"] == "warning"
            and "no alliance configured, skipping daily report" in entry["message"]
            for entry in logs
        )

    def test_no_snapshot_yet_skips(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        store.init_schema(store.connect(_db_path(tmp_path)))
        calls = self._spy_run_report(monkeypatch)
        asyncio.run(bot_main.job_report())
        assert calls == []
        logs = _logs(_db_path(tmp_path))
        assert any("no snapshot yet, skipping daily report" in entry["message"] for entry in logs)

    def test_unresolvable_tags_skip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1, alliance_tag="OTHER")])
        conn.close()
        calls = self._spy_run_report(monkeypatch)
        asyncio.run(bot_main.job_report())
        assert calls == []
        logs = _logs(_db_path(tmp_path))
        assert any("unresolved alliance tags, skipping daily report" in entry["message"] for entry in logs)

    def test_resolvable_tags_call_run_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(_db_path(tmp_path))
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1)])
        conn.close()
        calls = self._spy_run_report(monkeypatch)
        asyncio.run(bot_main.job_report())
        assert calls == [(CHANNEL_ID, True)]


# --- Faza 4: identifiable runs (job_runs lifecycle) -----------------------------


class TestRunRows:
    def test_run_fetch_with_run_id_tracks_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        db = _db_path(tmp_path)
        store.init_schema(store.connect(db))
        monkeypatch.setattr(bot_main, "fetch_map_sql", lambda url: FIXTURE_PATH.read_text())

        conn = store.connect(db)
        store.create_run(conn, run_id="run-fetch-1", job="fetch", source="test")
        conn.close()
        asyncio.run(bot_main.run_fetch(run_id="run-fetch-1"))
        conn = store.connect(db)
        try:
            row = store.get_run(conn, "run-fetch-1")
        finally:
            conn.close()
        assert row is not None
        assert row["status"] == "succeeded"
        assert row["job"] == "fetch"
        assert row["result"] == "completed"
        assert row["snapshot_date"] == _fetch_date()

    def test_run_fetch_failed_maps_to_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        store.init_schema(store.connect(_db_path(tmp_path)))

        def fail(url: str) -> str:
            raise MapSqlFetchError("fetch failed after 4 attempts")

        monkeypatch.setattr(bot_main, "fetch_map_sql", fail)
        conn = store.connect(_db_path(tmp_path))
        store.create_run(conn, run_id="run-fetch-2", job="fetch", source="test")
        conn.close()
        asyncio.run(bot_main.run_fetch(run_id="run-fetch-2"))
        conn = store.connect(_db_path(tmp_path))
        try:
            row = store.get_run(conn, "run-fetch-2")
        finally:
            conn.close()
        assert row["status"] == "failed"
        assert row["result"] == "failed"

    def test_run_fetch_skipped_when_lock_held(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path)
        db = _db_path(tmp_path)
        store.init_schema(store.connect(db))
        monkeypatch.setattr(bot_main, "fetch_map_sql", lambda url: FIXTURE_PATH.read_text())

        conn = store.connect(db)
        store.create_run(conn, run_id="run-fetch-3", job="fetch", source="test")
        conn.close()

        async def scenario() -> None:
            lock = bot_main._get_run_lock()
            await lock.acquire()
            try:
                result = await bot_main.run_fetch(run_id="run-fetch-3")
                assert result == bot_main.FETCH_STATUS_SKIPPED
            finally:
                lock.release()

        asyncio.run(scenario())
        conn = store.connect(db)
        try:
            row = store.get_run(conn, "run-fetch-3")
        finally:
            conn.close()
        assert row["status"] == "skipped"

    def test_run_report_with_run_id_sent_maps_succeeded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        db = _db_path(tmp_path)
        conn = store.connect(db)
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1, population=100), _row(2, population=100)])
        conn.close()
        conn = store.connect(db)
        store.create_run(conn, run_id="run-report-1", job="report", source="test")
        conn.close()
        channel = _install_bot({CHANNEL_ID: FakeChannel()})

        asyncio.run(bot_main.run_report(CHANNEL_ID, require_today=False, run_id="run-report-1"))
        assert len(channel.sent) == 1
        conn = store.connect(db)
        try:
            row = store.get_run(conn, "run-report-1")
        finally:
            conn.close()
        assert row["status"] == "succeeded"
        assert row["result"] == "sent"
        assert row["snapshot_date"] == _fetch_date()

    def test_scheduler_and_discord_sources(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """job_fetch creates a scheduler run; /raport creates a discord run."""
        _set_bot_env(monkeypatch, tmp_path)
        db = _db_path(tmp_path)
        store.init_schema(store.connect(db))
        monkeypatch.setattr(bot_main, "fetch_map_sql", lambda url: FIXTURE_PATH.read_text())

        asyncio.run(bot_main.job_fetch())
        conn = store.connect(db)
        try:
            rows = store.list_runs(conn, job="fetch")
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["source"] == "scheduler"
        assert rows[0]["status"] == "succeeded"

        # /raport surface: discord source, succeeded — a NOVA snapshot makes
        # the report build (the fixture map has no NOVA villages).
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        conn = store.connect(db)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1, population=100)])
        conn.close()
        channel = _install_bot({CHANNEL_ID: FakeChannel()})
        asyncio.run(bot_main._discord_run_report(CHANNEL_ID, require_today=False))
        assert len(channel.sent) == 1
        conn = store.connect(db)
        try:
            rows = store.list_runs(conn, job="report")
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["source"] == "discord"
        assert rows[0]["status"] == "succeeded"

    def test_job_report_scheduler_source(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_bot_env(monkeypatch, tmp_path, ALLIANCE_TAGS="NOVA")
        db = _db_path(tmp_path)
        conn = store.connect(db)
        store.init_schema(conn)
        _seed(conn, date.fromisoformat(_fetch_date()), [_row(1, population=100)])
        conn.close()
        _install_bot({CHANNEL_ID: FakeChannel()})

        asyncio.run(bot_main.job_report())
        conn = store.connect(db)
        try:
            rows = store.list_runs(conn, job="report")
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]["source"] == "scheduler"
        assert rows[0]["status"] == "succeeded"
