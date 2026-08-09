"""Tests for travian.backfill — the optional supabase backfill CLI (task 5).

The CLI reads a friend's Postgres via asyncpg and streams snapshots into the
SQLite store. No real Postgres is needed: the connection surface is a thin
``fetch``/``close`` Protocol, and the orchestration tests drive it with
FakeConnection serving canned introspection and batch rows. The only real
network-free process test is the no-DSN subprocess check (exit 0, message).
"""

from __future__ import annotations

import asyncio
import datetime
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from asyncpg.exceptions import InvalidPasswordError

from travian import backfill, store
from travian.models import VillageRow

REQUIRED = backfill.REQUIRED_COLUMNS


def source_row(village_id: int, date: str = "2026-08-09") -> dict[str, backfill.SourceValue]:
    """One canned source row in the friend's camelCase schema."""
    return {
        "village_id": village_id,
        "x": village_id,
        "y": -village_id,
        "tribe": 3,
        "village_name": f"Village {village_id}",
        "player_id": 10,
        "player_name": "Alice",
        "alliance_id": 20,
        "alliance_tag": "AAA",
        "population": 100 + village_id,
        "region": None,
        "isCapital": village_id % 2 == 0,
        "isCity": True,
        "isHarbor": False,
        "victory_points": village_id,
        "_date": date,
    }


def expected_row(village_id: int) -> VillageRow:
    """The VillageRow source_row(village_id) must map to."""
    return VillageRow(
        village_id=village_id,
        x=village_id,
        y=-village_id,
        tribe=3,
        name=f"Village {village_id}",
        player_id=10,
        player_name="Alice",
        alliance_id=20,
        alliance_tag="AAA",
        population=100 + village_id,
        region=None,
        is_capital=village_id % 2 == 0,
        is_city=True,
        is_harbor=False,
        victory_points=village_id,
    )


class FakeConnection:
    """asyncpg-style fake: serves canned introspection + batch rows, records calls.

    Dispatches on query markers: ``table_name`` -> candidate tables,
    ``column_name`` -> the table's columns, ``DISTINCT`` -> snapshot dates,
    anything else -> batch rows sliced by (date, limit, offset).
    """

    def __init__(
        self,
        *,
        tables: list[str],
        columns: list[str],
        dates: list[str],
        rows: list[dict[str, backfill.SourceValue]],
    ) -> None:
        self.tables = tables
        self.columns = columns
        self.dates = dates
        self.rows = rows
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    async def fetch(self, query: str, *args: object) -> list[dict[str, backfill.SourceValue]]:
        self.calls.append((query, args))
        if "GROUP BY" in query:
            return [{"table_name": t} for t in self.tables]
        if "table_name = $1" in query:
            return [{"column_name": c} for c in self.columns]
        if "DISTINCT" in query:
            return [{"_date": d} for d in self.dates]
        date, limit, offset = args
        start, stop = int(cast(int, offset)), int(cast(int, offset)) + int(cast(int, limit))
        return [r for r in self.rows if r["_date"] == date][start:stop]

    async def close(self) -> None:
        self.closed = True


# --- validate_columns -------------------------------------------------------


def test_validate_columns_none_when_all_required_present() -> None:
    """Given: every required source column plus unrelated extras."""
    assert backfill.validate_columns([*REQUIRED, "id", "extra"]) is None


def test_validate_columns_names_single_missing_column() -> None:
    """When: one required column is absent, the message names exactly it."""
    cols = [c for c in REQUIRED if c != "isCapital"]
    msg = backfill.validate_columns(cols)
    assert msg is not None
    assert "isCapital" in msg
    assert "victory_points" not in msg


def test_validate_columns_names_every_missing_column() -> None:
    """When: several required columns are absent, the message names all."""
    cols = [c for c in REQUIRED if c not in ("_date", "victory_points")]
    msg = backfill.validate_columns(cols)
    assert msg is not None
    assert "_date" in msg and "victory_points" in msg


# --- map_row ----------------------------------------------------------------


def test_map_row_translates_camel_case_to_snake_case() -> None:
    """When: a full source row, every target field keeps its exact value."""
    row = backfill.map_row(source_row(7))
    assert row == expected_row(7)
    assert row.name == "Village 7"  # village_name -> name
    assert row.is_capital is False and row.is_city is True and row.is_harbor is False


def test_map_row_coerces_int_bools_and_string_numerics() -> None:
    """When: bools are stored as ints and numerics as strings/Decimal."""
    raw = source_row(3)
    raw.update(
        {
            "isCapital": 1,
            "isCity": 0,
            "isHarbor": "TRUE",
            "victory_points": Decimal(42),
            "population": "150",
        }
    )
    row = backfill.map_row(raw)
    assert row.is_capital is True and row.is_city is False and row.is_harbor is True
    assert row.victory_points == 42 and row.population == 150


def test_map_row_keeps_region_none_and_ignores_extra_keys() -> None:
    """Given: region NULL and extra source columns, extras never leak through."""
    raw = source_row(1)
    raw["id"] = 999
    row = backfill.map_row(raw)
    assert row.region is None
    assert row.model_dump().keys() == expected_row(1).model_dump().keys()


# --- _as_date ---------------------------------------------------------------


def test_as_date_formats_datetime_to_date_string() -> None:
    """When: the source column is a timestamp, only the date part is kept."""
    assert backfill._as_date(datetime.datetime(2026, 8, 9, 12, 30, tzinfo=datetime.UTC)) == "2026-08-09"


def test_as_date_passes_dates_and_strings_through() -> None:
    assert backfill._as_date(datetime.date(2026, 8, 9)) == "2026-08-09"
    assert backfill._as_date("2026-08-09") == "2026-08-09"


# --- _select_table ----------------------------------------------------------


def test_select_table_prefers_village_snapshot() -> None:
    """When: several tables match, the village+snapshot one wins."""
    assert backfill._select_table(["z_other", "village_snapshot", "a_first"]) == "village_snapshot"


def test_select_table_falls_back_to_alphabetical_first() -> None:
    """When: no village+snapshot table, the alphabetically first is chosen."""
    assert backfill._select_table(["b_table", "a_table"]) == "a_table"


def test_select_table_returns_none_for_no_candidates() -> None:
    assert backfill._select_table([]) is None


# --- CLI orchestration (FakeConnection) ------------------------------------


def test_dry_run_lists_tables_and_dates_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given: --dry-run, tables + dates are printed and nothing is written."""
    fake = FakeConnection(
        tables=["village_snapshot", "z_other"],
        columns=list(REQUIRED),
        dates=["2026-08-08", "2026-08-09"],
        rows=[source_row(i) for i in range(3)],
    )
    db = tmp_path / "t.db"
    args = backfill._parse_args(["--dry-run", "--sqlite", str(db)])

    rc = asyncio.run(backfill._backfill(args, fake))

    assert rc == 0
    out = capsys.readouterr().out
    assert "village_snapshot" in out and "z_other" in out
    assert "2026-08-08" in out and "2026-08-09" in out
    assert not db.exists()
    assert not any("LIMIT" in query for query, _ in fake.calls)


def test_missing_required_column_skips_with_readable_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given: the table lacks a required column, the message names it and skips."""
    cols = [c for c in REQUIRED if c != "victory_points"]
    fake = FakeConnection(tables=["village_snapshot"], columns=cols, dates=["2026-08-09"], rows=[])
    db = tmp_path / "t.db"
    args = backfill._parse_args(["--sqlite", str(db)])

    rc = asyncio.run(backfill._backfill(args, fake))

    assert rc == 0
    out = capsys.readouterr().out
    assert "victory_points" in out and "skipping" in out
    assert not db.exists()


def test_no_candidate_tables_prints_message_and_skips(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given: no table with both _date and village_id, run skips cleanly."""
    fake = FakeConnection(tables=[], columns=[], dates=[], rows=[])
    args = backfill._parse_args(["--dry-run"])

    rc = asyncio.run(backfill._backfill(args, fake))

    assert rc == 0
    assert "no table" in capsys.readouterr().out


def test_real_run_streams_rows_in_batches_and_saves_to_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given: 12 villages per date with batch size 5, every row lands in SQLite."""
    monkeypatch.setattr(backfill, "BATCH_SIZE", 5)
    dates = ["2026-08-08", "2026-08-09"]
    rows = [source_row(i, dates[i % 2]) for i in range(24)]
    fake = FakeConnection(tables=["village_snapshot"], columns=list(REQUIRED), dates=dates, rows=rows)
    db = tmp_path / "t.db"
    args = backfill._parse_args(["--sqlite", str(db)])

    rc = asyncio.run(backfill._backfill(args, fake))

    assert rc == 0
    conn = store.connect(db)
    try:
        assert store.list_dates(conn) == dates
        latest = store.load_latest(conn)
        assert latest is not None and latest.source == "backfill"
        assert store.load_villages(conn, "2026-08-08") == [expected_row(i) for i in range(0, 24, 2)]
        assert store.load_villages(conn, "2026-08-09") == [expected_row(i) for i in range(1, 24, 2)]
    finally:
        conn.close()
    batch_calls = [query for query, _ in fake.calls if "LIMIT" in query]
    assert len(batch_calls) == 8  # 2 dates x (3 data batches + terminating empty batch)
    assert "saved 2026-08-08 (12 villages)" in capsys.readouterr().out


def test_real_run_records_a_date_with_zero_villages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given: a date with no rows, the snapshot date is still recorded."""
    fake = FakeConnection(tables=["village_snapshot"], columns=list(REQUIRED), dates=["2026-08-09"], rows=[])
    db = tmp_path / "t.db"
    args = backfill._parse_args(["--sqlite", str(db)])

    rc = asyncio.run(backfill._backfill(args, fake))

    assert rc == 0
    conn = store.connect(db)
    try:
        assert store.list_dates(conn) == ["2026-08-09"]
        assert store.load_villages(conn, "2026-08-09") == []
    finally:
        conn.close()


def test_real_run_uses_sqlite_path_env_when_flag_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When: --sqlite is absent, SQLITE_PATH env decides the target database."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "env.db"))
    fake = FakeConnection(tables=["village_snapshot"], columns=list(REQUIRED), dates=["2026-08-09"], rows=[source_row(1)])
    args = backfill._parse_args([])

    rc = asyncio.run(backfill._backfill(args, fake))

    assert rc == 0
    assert (tmp_path / "env.db").exists()


def test_connection_error_prints_message_and_returns_none(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When: connecting fails, a readable message is printed, no traceback."""
    def boom(dsn: str) -> object:
        raise InvalidPasswordError("password authentication failed")

    monkeypatch.setattr(backfill.asyncpg, "connect", boom)

    result = asyncio.run(backfill._connect("postgres://user:pass@host/db"))

    assert result is None
    assert "cannot connect" in capsys.readouterr().out


# --- process-level entry ----------------------------------------------------


def test_no_dsn_prints_message_and_exits_zero() -> None:
    """When: BACKFILL_DSN is unset, `python -m travian.backfill` exits 0."""
    env = {k: v for k, v in os.environ.items() if k != "BACKFILL_DSN"}
    result = subprocess.run(
        [sys.executable, "-m", "travian.backfill"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0
    assert "BACKFILL_DSN not set, skipping" in result.stdout
