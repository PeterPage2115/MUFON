"""Tests for travian.store — the sqlite snapshot store (task 4).

Covers the round-trip contract (save_snapshot → load_villages must reproduce
VillageRow exactly, incl. region None and 0/1 booleans), upsert idempotency
(re-fetch of the same day must never raise IntegrityError), settings JSON
round-trip and the job log.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from travian.models import (
    AllianceDay,
    RegionDay,
    SummaryDay,
    VillageHistoryPoint,
    VillageRow,
)
from travian.store import (
    SnapshotRecord,
    alliance_days,
    alliance_ids_by_tag,
    append_log,
    connect,
    get_setting,
    get_settings,
    init_schema,
    list_dates,
    load_latest,
    load_villages,
    recent_logs,
    region_days,
    save_snapshot,
    search_villages,
    set_settings,
    snapshot_counts,
    summary_days,
    village_history,
)


def make_row(village_id: int, **overrides: Any) -> VillageRow:
    """Fixture: one realistic village row (region NULL, mixed bools)."""
    values: dict[str, Any] = {
        "village_id": village_id,
        "x": 1,
        "y": 2,
        "tribe": 3,
        "name": f"Village {village_id}",
        "player_id": 10,
        "player_name": "Alice",
        "alliance_id": 20,
        "alliance_tag": "AAA",
        "population": 100,
        "region": None,
        "is_capital": False,
        "is_city": True,
        "is_harbor": False,
        "victory_points": 5,
    }
    values.update(overrides)
    return VillageRow(**values)


@pytest.fixture()
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A fresh, schema-initialised connection to a temp file database."""
    db = connect(tmp_path / "test.db")
    init_schema(db)
    yield db
    db.close()


# --- schema ---------------------------------------------------------------


def test_init_schema_is_idempotent_when_called_twice(conn: sqlite3.Connection) -> None:
    """When: init_schema runs a second time on the same database."""
    init_schema(conn)

    # Then: tables still exist and the file database is in WAL mode
    row = conn.execute("SELECT name FROM sqlite_master WHERE name = 'snapshots'").fetchone()
    assert row is not None
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# --- villages round-trip --------------------------------------------------


def test_save_and_load_roundtrip_when_full_row(conn: sqlite3.Connection) -> None:
    # Given: one village with a string region and special characters
    row = make_row(1, region="Regio Nord", name="MPTrav\u2019s landsby (i)")

    # When: it is saved and loaded back
    save_snapshot(conn, "2026-08-09", [row])

    # Then: the loaded row equals the original exactly
    assert load_villages(conn, "2026-08-09") == [row]


def test_save_and_load_roundtrip_when_multiple_rows(conn: sqlite3.Connection) -> None:
    # Given: three villages with distinct values
    rows = [make_row(i, x=i, population=i * 10, name=f"Village{i}") for i in range(1, 4)]

    # When: saved for one date and loaded back
    save_snapshot(conn, "2026-08-09", rows)

    # Then: the loaded list equals the original list exactly
    assert load_villages(conn, "2026-08-09") == rows


def test_load_preserves_none_region_when_region_null(conn: sqlite3.Connection) -> None:
    # Given: a village whose region is NULL
    save_snapshot(conn, "2026-08-09", [make_row(1, region=None)])

    # When/Then: loaded region is None (stored as SQL NULL, not "" or "None")
    assert load_villages(conn, "2026-08-09")[0].region is None


def test_load_preserves_bools_when_mixed_values(conn: sqlite3.Connection) -> None:
    # Given: a village with is_capital/is_city/is_harbor all set
    save_snapshot(conn, "2026-08-09", [make_row(1, is_capital=True, is_city=False, is_harbor=True)])

    # When/Then: booleans survive the 0/1 integer storage round-trip
    loaded = load_villages(conn, "2026-08-09")[0]
    assert loaded.is_capital is True
    assert loaded.is_city is False
    assert loaded.is_harbor is True


def test_load_villages_empty_when_date_not_saved(conn: sqlite3.Connection) -> None:
    assert load_villages(conn, "2026-08-09") == []


# --- upsert semantics -----------------------------------------------------


def test_save_snapshot_twice_same_date_is_idempotent(conn: sqlite3.Connection) -> None:
    # Given: one day's snapshot already stored
    save_snapshot(conn, "2026-08-09", [make_row(1, population=100), make_row(2, population=200)])

    # When: the same day is fetched again with changed values (must NOT raise)
    save_snapshot(conn, "2026-08-09", [make_row(1, population=150), make_row(2, population=250)])

    # Then: one snapshots row, one row per village, values replaced
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM villages").fetchone()[0] == 2
    assert [v.population for v in load_villages(conn, "2026-08-09")] == [150, 250]


def test_save_snapshot_third_fetch_adds_new_village(conn: sqlite3.Connection) -> None:
    # Given: a day with one village, then re-fetched with an extra one
    save_snapshot(conn, "2026-08-09", [make_row(1)])
    save_snapshot(conn, "2026-08-09", [make_row(1), make_row(2)])

    # Then: both villages present, none duplicated
    assert len(load_villages(conn, "2026-08-09")) == 2


def test_save_snapshot_records_source_when_default(conn: sqlite3.Connection) -> None:
    save_snapshot(conn, "2026-08-09", [])

    assert load_latest(conn) == SnapshotRecord(snapshot_date="2026-08-09", source="map.sql")


def test_save_snapshot_records_source_when_custom(conn: sqlite3.Connection) -> None:
    save_snapshot(conn, "2026-08-09", [], source="backfill")

    assert load_latest(conn) == SnapshotRecord(snapshot_date="2026-08-09", source="backfill")


def test_save_snapshot_records_date_when_no_rows(conn: sqlite3.Connection) -> None:
    # When: a day is recorded with zero parsed villages
    save_snapshot(conn, "2026-08-09", [])

    # Then: the snapshot date exists but has no villages
    assert list_dates(conn) == ["2026-08-09"]
    assert load_villages(conn, "2026-08-09") == []


# --- dates ----------------------------------------------------------------


def test_list_dates_sorted_ascending_when_inserted_out_of_order(conn: sqlite3.Connection) -> None:
    # When: dates are saved in non-chronological order (one repeated)
    for date in ("2026-08-09", "2026-08-07", "2026-08-08", "2026-08-07"):
        save_snapshot(conn, date, [])

    # Then: dates come back ascending, deduplicated
    assert list_dates(conn) == ["2026-08-07", "2026-08-08", "2026-08-09"]


def test_list_dates_empty_when_no_snapshots(conn: sqlite3.Connection) -> None:
    assert list_dates(conn) == []


def test_load_latest_returns_newest_when_multiple_dates(conn: sqlite3.Connection) -> None:
    for date in ("2026-08-07", "2026-08-09", "2026-08-08"):
        save_snapshot(conn, date, [])

    assert load_latest(conn) == SnapshotRecord(snapshot_date="2026-08-09", source="map.sql")


def test_load_latest_none_when_no_snapshots(conn: sqlite3.Connection) -> None:
    assert load_latest(conn) is None


# --- settings -------------------------------------------------------------


def test_get_settings_empty_when_none_saved(conn: sqlite3.Connection) -> None:
    assert get_settings(conn) == {}


def test_set_and_get_settings_roundtrip_when_json_types(conn: sqlite3.Connection) -> None:
    # Given: every JSON value type
    kvs: dict[str, Any] = {
        "channel_id": 123456789,
        "guild_name": "Alliance Alpha",
        "ratio": 0.5,
        "enabled": True,
        "nothing": None,
        "tags": ["AAA", "BBB"],
        "limits": {"top": 5, "regions": 3},
    }

    # When: stored and read back
    set_settings(conn, kvs)

    # Then: values round-trip as the exact same Python objects
    assert get_settings(conn) == kvs
    assert get_setting(conn, "channel_id") == 123456789
    assert get_setting(conn, "tags") == ["AAA", "BBB"]
    assert get_setting(conn, "limits") == {"top": 5, "regions": 3}


def test_set_settings_updates_existing_key(conn: sqlite3.Connection) -> None:
    # Given: two keys stored
    set_settings(conn, {"channel_id": 1, "tag": "AAA"})

    # When: one key is overwritten
    set_settings(conn, {"channel_id": 2})

    # Then: both keys present, the overwritten one holds the new value
    assert get_settings(conn) == {"channel_id": 2, "tag": "AAA"}
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 2


def test_set_settings_noop_when_empty(conn: sqlite3.Connection) -> None:
    set_settings(conn, {})

    assert get_settings(conn) == {}


def test_set_settings_raises_valueerror_writes_nothing_when_not_serializable(
    conn: sqlite3.Connection,
) -> None:
    # Given: one serializable key and one that is not (object() has no JSON form)
    # When: set_settings is called with the bad value in the dict
    with pytest.raises(ValueError, match="not JSON-serializable"):
        set_settings(conn, {"good": 1, "bad": object()})

    # Then: the transaction was never started — nothing was written
    assert get_settings(conn) == {}


def test_get_setting_none_when_key_missing(conn: sqlite3.Connection) -> None:
    assert get_setting(conn, "missing") is None


# --- job log --------------------------------------------------------------


def test_append_and_recent_logs_newest_first_when_multiple(conn: sqlite3.Connection) -> None:
    # Given: three log entries appended in order
    append_log(conn, "fetch", "info", "first")
    append_log(conn, "fetch", "warning", "second")
    append_log(conn, "report", "error", "third")

    # When: recent logs are read
    logs = recent_logs(conn)

    # Then: newest first, with exactly the four documented keys and UTC ISO ts
    assert [log["message"] for log in logs] == ["third", "second", "first"]
    assert all(set(log) == {"ts", "job", "level", "message"} for log in logs)
    assert logs[0]["job"] == "report"
    assert logs[0]["level"] == "error"
    assert logs[0]["ts"].endswith("+00:00")


def test_append_log_visible_from_new_connection(tmp_path: Path) -> None:
    # Given: a log entry appended, then its connection closed
    first = connect(tmp_path / "test.db")
    init_schema(first)
    append_log(first, "report", "error", "boom")
    first.close()

    # When: a fresh connection reads the log
    second = connect(tmp_path / "test.db")
    try:
        logs = recent_logs(second)
    finally:
        second.close()

    # Then: the entry is visible — append_log must commit its own transaction
    # (legacy sqlite3 isolation would otherwise roll back the INSERT on close)
    assert [log["message"] for log in logs] == ["boom"]


# --- analysis aggregators ---------------------------------------------------


class TestAllianceIdsByTag:
    def test_groups_ids_per_tag_excluding_id_zero(self, conn: sqlite3.Connection) -> None:
        save_snapshot(
            conn,
            "2026-08-08",
            [
                make_row(1, alliance_id=20, alliance_tag="AAA"),
                make_row(2, alliance_id=21, alliance_tag="AAA"),
                make_row(3, alliance_id=30, alliance_tag="BBB"),
                make_row(4, alliance_id=0, alliance_tag=""),
            ],
        )

        assert alliance_ids_by_tag(conn, "2026-08-08") == {"AAA": [20, 21], "BBB": [30]}

    def test_empty_when_date_unknown(self, conn: sqlite3.Connection) -> None:
        assert alliance_ids_by_tag(conn, "2026-08-08") == {}


class TestRegionDays:
    def test_aggregates_our_and_total_pop_per_region(self, conn: sqlite3.Connection) -> None:
        # region NULL groups as "" (COALESCE); enemy villages count only in total.
        save_snapshot(
            conn,
            "2026-08-08",
            [
                make_row(1, alliance_id=20, population=100, region="North"),
                make_row(2, alliance_id=20, population=200, region="North"),
                make_row(3, alliance_id=30, population=50, region="North"),
                make_row(4, alliance_id=20, population=10, region=None),
                make_row(5, alliance_id=30, population=30, region=None),
            ],
        )

        days = region_days(conn, "2026-08-08", "2026-08-08", {20})

        assert days == [
            RegionDay(date="2026-08-08", region="", our_pop=10, total_pop=40),
            RegionDay(date="2026-08-08", region="North", our_pop=300, total_pop=350),
        ]

    def test_window_inclusive_and_date_asc(self, conn: sqlite3.Connection) -> None:
        for date in ("2026-08-07", "2026-08-08", "2026-08-09"):
            save_snapshot(conn, date, [make_row(1, alliance_id=20, population=100, region="North")])

        days = region_days(conn, "2026-08-08", "2026-08-09", {20})

        assert [day.date for day in days] == ["2026-08-08", "2026-08-09"]

    def test_empty_alliance_ids_returns_empty(self, conn: sqlite3.Connection) -> None:
        save_snapshot(conn, "2026-08-08", [make_row(1, alliance_id=20, region="North")])

        assert region_days(conn, "2026-08-08", "2026-08-08", set()) == []


class TestAllianceDays:
    def test_aggregates_villages_pop_vp_per_alliance(self, conn: sqlite3.Connection) -> None:
        save_snapshot(
            conn,
            "2026-08-08",
            [
                make_row(1, alliance_id=20, alliance_tag="AAA", population=100, victory_points=5),
                make_row(2, alliance_id=20, alliance_tag="AAA", population=200, victory_points=7),
                make_row(3, alliance_id=30, alliance_tag="BBB", population=50, victory_points=1),
            ],
        )

        days = alliance_days(conn, "2026-08-08", "2026-08-08", {20, 30})

        assert days == [
            AllianceDay(date="2026-08-08", alliance_id=20, alliance_tag="AAA", villages=2, population=300, vp=12),
            AllianceDay(date="2026-08-08", alliance_id=30, alliance_tag="BBB", villages=1, population=50, vp=1),
        ]

    def test_tag_is_lexicographically_greatest(self, conn: sqlite3.Connection) -> None:
        # Same alliance_id with two tags in one snapshot (tag rename) → MAX wins.
        save_snapshot(
            conn,
            "2026-08-08",
            [
                make_row(1, alliance_id=20, alliance_tag="AAA", population=100),
                make_row(2, alliance_id=20, alliance_tag="ZZZ", population=100),
            ],
        )

        days = alliance_days(conn, "2026-08-08", "2026-08-08", {20})

        assert days == [AllianceDay(date="2026-08-08", alliance_id=20, alliance_tag="ZZZ", villages=2, population=200, vp=10)]

    def test_window_inclusive(self, conn: sqlite3.Connection) -> None:
        for date in ("2026-08-07", "2026-08-08", "2026-08-09"):
            save_snapshot(conn, date, [make_row(1, alliance_id=20, population=100)])

        days = alliance_days(conn, "2026-08-08", "2026-08-09", {20})

        assert [day.date for day in days] == ["2026-08-08", "2026-08-09"]

    def test_empty_alliance_ids_returns_empty(self, conn: sqlite3.Connection) -> None:
        save_snapshot(conn, "2026-08-08", [make_row(1, alliance_id=20)])

        assert alliance_days(conn, "2026-08-08", "2026-08-08", set()) == []


class TestSummaryDays:
    def test_aggregates_villages_pop_players_vp(self, conn: sqlite3.Connection) -> None:
        save_snapshot(
            conn,
            "2026-08-08",
            [
                make_row(1, alliance_id=20, population=100, victory_points=5),
                make_row(2, alliance_id=20, population=200, victory_points=7, player_id=11),
                make_row(3, alliance_id=30, population=50, victory_points=1),
            ],
        )

        days = summary_days(conn, "2026-08-08", "2026-08-08", {20})

        assert days == [SummaryDay(date="2026-08-08", villages=2, population=300, players=2, vp=12)]

    def test_window_inclusive_and_date_asc(self, conn: sqlite3.Connection) -> None:
        for date, pop in (("2026-08-07", 100), ("2026-08-08", 200), ("2026-08-09", 300)):
            save_snapshot(conn, date, [make_row(1, alliance_id=20, population=pop)])

        days = summary_days(conn, "2026-08-08", "2026-08-09", {20})

        assert [(day.date, day.population) for day in days] == [("2026-08-08", 200), ("2026-08-09", 300)]

    def test_empty_alliance_ids_returns_empty(self, conn: sqlite3.Connection) -> None:
        save_snapshot(conn, "2026-08-08", [make_row(1, alliance_id=20)])

        assert summary_days(conn, "2026-08-08", "2026-08-08", set()) == []


class TestSnapshotCounts:
    def test_counts_and_total_population(self, conn: sqlite3.Connection) -> None:
        save_snapshot(
            conn,
            "2026-08-08",
            [
                make_row(1, alliance_id=20, population=100),
                make_row(2, alliance_id=20, population=200, player_id=11),
                # no alliance (alliance_id 0) — excluded from the alliances count
                make_row(3, alliance_id=0, population=50, player_id=12),
                # same player as row 1 — one distinct player
                make_row(4, alliance_id=30, population=150, player_id=10),
            ],
        )

        assert snapshot_counts(conn, "2026-08-08") == (4, 3, 2, 500)

    def test_unknown_date_zero_counts(self, conn: sqlite3.Connection) -> None:
        save_snapshot(conn, "2026-08-08", [make_row(1, alliance_id=20, population=100)])

        assert snapshot_counts(conn, "2026-08-07") == (0, 0, 0, 0)


def test_recent_logs_limits_when_n_smaller_than_count(conn: sqlite3.Connection) -> None:
    # Given: five log entries
    for i in range(5):
        append_log(conn, "job", "info", f"msg{i}")

    # When: only the two newest are requested
    logs = recent_logs(conn, n=2)

    # Then: exactly the two newest are returned, newest first
    assert [log["message"] for log in logs] == ["msg4", "msg3"]


def test_recent_logs_empty_when_no_logs(conn: sqlite3.Connection) -> None:
    assert recent_logs(conn) == []


# --- village explorer -------------------------------------------------------


class TestSearchVillages:
    def test_matches_name_and_player_literally(self, conn: sqlite3.Connection) -> None:
        save_snapshot(
            conn,
            "2026-08-09",
            [make_row(1, name="Alpha Keep", player_name="Skipper"), make_row(2, name="Beta Camp", player_name="Ranger")],
        )

        by_name = search_villages(conn, "2026-08-09", "alpha", 50)
        by_player = search_villages(conn, "2026-08-09", "Ranger", 50)

        assert [row.village_id for row in by_name] == [1]  # NOCASE
        assert [row.village_id for row in by_player] == [2]

    def test_positive_and_negative_coords_both_separators(self, conn: sqlite3.Connection) -> None:
        save_snapshot(
            conn,
            "2026-08-09",
            [make_row(1, x=7, y=7), make_row(2, x=-3, y=-2), make_row(3, x=9, y=9)],
        )

        assert [row.village_id for row in search_villages(conn, "2026-08-09", "7|7", 50)] == [1]
        assert [row.village_id for row in search_villages(conn, "2026-08-09", "7,7", 50)] == [1]
        assert [row.village_id for row in search_villages(conn, "2026-08-09", " 7 | 7 ", 50)] == [1]
        assert [row.village_id for row in search_villages(conn, "2026-08-09", "-3,-2", 50)] == [2]
        assert [row.village_id for row in search_villages(conn, "2026-08-09", "-3 | -2", 50)] == [2]

    def test_like_metacharacters_are_escaped(self, conn: sqlite3.Connection) -> None:
        save_snapshot(
            conn,
            "2026-08-09",
            [make_row(1, name="100%_win"), make_row(2, name="1000_win"), make_row(3, name="back\\slash")],
        )

        # "100%_win" must match only the literal row, not the "1000_win" row.
        assert [row.village_id for row in search_villages(conn, "2026-08-09", "100%_win", 50)] == [1]
        assert [row.village_id for row in search_villages(conn, "2026-08-09", "back\\slash", 50)] == [3]

    def test_results_sorted_by_population_then_limited(self, conn: sqlite3.Connection) -> None:
        save_snapshot(
            conn,
            "2026-08-09",
            [
                make_row(1, name="Keep A", population=100),
                make_row(2, name="Keep B", population=300),
                make_row(3, name="Keep C", population=200),
            ],
        )

        rows = search_villages(conn, "2026-08-09", "Keep", 2)

        assert [row.village_id for row in rows] == [2, 3]  # pop DESC, then limit

    def test_search_scoped_to_snapshot_date(self, conn: sqlite3.Connection) -> None:
        save_snapshot(conn, "2026-08-08", [make_row(1, name="Old Keep")])
        save_snapshot(conn, "2026-08-09", [make_row(2, name="New Keep")])

        assert [row.village_id for row in search_villages(conn, "2026-08-09", "Keep", 50)] == [2]


class TestVillageHistory:
    def test_observations_chronologically_ascending(self, conn: sqlite3.Connection) -> None:
        save_snapshot(conn, "2026-08-08", [make_row(7, population=100, player_name="Old Owner", alliance_tag="OLD")])
        save_snapshot(conn, "2026-08-09", [make_row(7, population=150, player_name="New Owner", alliance_tag="NEW")])
        save_snapshot(conn, "2026-08-10", [make_row(7, population=180, player_name="New Owner", alliance_tag="NEW")])

        points = village_history(conn, 7, 30)

        assert [p.snapshot_date for p in points] == ["2026-08-08", "2026-08-09", "2026-08-10"]
        assert points[-1].population == 180
        assert points[0].player_name == "Old Owner"
        assert points[-1] == VillageHistoryPoint(
            snapshot_date="2026-08-10",
            name=points[-1].name,
            x=points[-1].x,
            y=points[-1].y,
            player_name="New Owner",
            alliance_tag="NEW",
            population=180,
        )

    def test_days_caps_newest_observations(self, conn: sqlite3.Connection) -> None:
        for day in ("2026-08-06", "2026-08-07", "2026-08-08", "2026-08-09", "2026-08-10"):
            save_snapshot(conn, day, [make_row(7, population=100)])

        points = village_history(conn, 7, days=2)

        assert [p.snapshot_date for p in points] == ["2026-08-09", "2026-08-10"]

    def test_deleted_village_keeps_history_without_latest_observation(self, conn: sqlite3.Connection) -> None:
        save_snapshot(conn, "2026-08-08", [make_row(4)])
        save_snapshot(conn, "2026-08-09", [])  # village gone from the map

        points = village_history(conn, 4, 30)

        assert [p.snapshot_date for p in points] == ["2026-08-08"]  # still has history

    def test_unknown_id_returns_empty(self, conn: sqlite3.Connection) -> None:
        save_snapshot(conn, "2026-08-09", [make_row(1)])

        assert village_history(conn, 999, 30) == []
