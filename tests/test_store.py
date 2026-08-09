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

from travian.models import VillageRow
from travian.store import (
    SnapshotRecord,
    append_log,
    connect,
    get_setting,
    get_settings,
    init_schema,
    list_dates,
    load_latest,
    load_villages,
    recent_logs,
    save_snapshot,
    set_settings,
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
