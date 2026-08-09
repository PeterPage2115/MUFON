"""SQLite snapshot store (task 4) — persistence for map.sql snapshots.

Connection policy: every operation receives an open ``sqlite3.Connection``;
callers open one per operation with :func:`connect` and close it afterwards.
The uvicorn worker and the bot loop therefore never share a connection
simultaneously (``check_same_thread=False`` only permits each thread to hold
its own connection). :func:`init_schema` switches a file database to WAL
(``PRAGMA journal_mode = WAL``) — ``:memory:`` databases report "memory"
instead, which is fine for tests.

Timestamps: ``created_at``, ``updated_at`` and ``ts`` are UTC ISO-8601
strings (``datetime.now(timezone.utc).isoformat()``, e.g.
``2026-08-09T14:05:00.123456+00:00``).

Ordering choices: :func:`load_villages` returns rows ordered by
``village_id`` (stable input for metrics); :func:`recent_logs` returns
newest-first (``id DESC``) because the dashboard displays the newest entries
on top.

Settings values are JSON; the ``JsonValue`` alias (a recursive JSON type)
stands in for ``Any`` in the public signatures so basedpyright stays clean
while the API stays honest: arbitrary JSON, nothing else.

allow: SIZE_OK — the DDL/upsert data tables (``_SCHEMA_STATEMENTS``,
``_VILLAGE_UPSERT``, ``_VillageColumns``) push raw LOC past 250; they are the
plan's data contract verbatim and belong with their single consumer rather
than in a split module.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

from travian.models import AllianceDay, RegionDay, SummaryDay, VillageRow

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class _VillageColumns(TypedDict):
    """The 16 columns of the ``villages`` table (mirrors the DDL)."""

    snapshot_date: str
    village_id: int
    x: int
    y: int
    tribe: int
    name: str
    player_id: int
    player_name: str
    alliance_id: int
    alliance_tag: str
    population: int
    region: str | None
    is_capital: int
    is_city: int
    is_harbor: int
    victory_points: int


_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        snapshot_date TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        source TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS villages (
        snapshot_date TEXT NOT NULL,
        village_id INTEGER NOT NULL,
        x INTEGER NOT NULL,
        y INTEGER NOT NULL,
        tribe INTEGER NOT NULL,
        name TEXT NOT NULL,
        player_id INTEGER NOT NULL,
        player_name TEXT NOT NULL,
        alliance_id INTEGER NOT NULL,
        alliance_tag TEXT NOT NULL,
        population INTEGER NOT NULL,
        region TEXT,
        is_capital INTEGER NOT NULL,
        is_city INTEGER NOT NULL,
        is_harbor INTEGER NOT NULL,
        victory_points INTEGER NOT NULL,
        PRIMARY KEY (snapshot_date, village_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_villages_snapshot_alliance
        ON villages (snapshot_date, alliance_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_villages_snapshot_region
        ON villages (snapshot_date, region)
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        job TEXT NOT NULL,
        level TEXT NOT NULL,
        message TEXT NOT NULL
    )
    """,
)

_VILLAGE_UPSERT = """
INSERT OR REPLACE INTO villages (
    snapshot_date, village_id, x, y, tribe, name, player_id, player_name,
    alliance_id, alliance_tag, population, region, is_capital, is_city,
    is_harbor, victory_points
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass(frozen=True)
class SnapshotRecord:
    """One ``snapshots`` row: the day and which source wrote it."""

    snapshot_date: str
    source: str


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a fresh connection to ``db_path`` (one per operation; see module docstring)."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if missing and switch the database to WAL.

    Idempotent — safe to call on every startup. Statements run one by one
    outside a transaction (DDL autocommits; ``journal_mode`` can only change
    outside a transaction).
    """
    for statement in _SCHEMA_STATEMENTS:
        _ = conn.execute(statement)
    _ = conn.execute("PRAGMA journal_mode = WAL")


def save_snapshot(
    conn: sqlite3.Connection, date: str, rows: list[VillageRow], source: str = "map.sql"
) -> None:
    """Store one day's villages, replacing any existing snapshot for ``date``.

    One transaction: the ``snapshots`` row is upserted on its PRIMARY KEY and
    every village on ``(snapshot_date, village_id)`` — re-fetching the same
    day never raises IntegrityError. Booleans are stored as INTEGER 0/1,
    ``region`` None as SQL NULL. An empty ``rows`` list still records the
    snapshot date (a day with zero parsed villages is a valid snapshot).
    """
    values = [
        (
            date,
            row.village_id,
            row.x,
            row.y,
            row.tribe,
            row.name,
            row.player_id,
            row.player_name,
            row.alliance_id,
            row.alliance_tag,
            row.population,
            row.region,
            int(row.is_capital),
            int(row.is_city),
            int(row.is_harbor),
            row.victory_points,
        )
        for row in rows
    ]
    with conn:
        _ = conn.execute(
            "INSERT OR REPLACE INTO snapshots (snapshot_date, created_at, source) VALUES (?, ?, ?)",
            (date, _utc_now(), source),
        )
        _ = conn.executemany(_VILLAGE_UPSERT, values)


def load_villages(conn: sqlite3.Connection, date: str) -> list[VillageRow]:
    """All villages of ``date`` as ``VillageRow``, ordered by village_id.

    Round-trips save_snapshot exactly: region NULL → None, INTEGER 0/1 → bool.
    """
    rows = cast(
        list[_VillageColumns],
        conn.execute(
            "SELECT * FROM villages WHERE snapshot_date = ? ORDER BY village_id",
            (date,),
        ).fetchall(),
    )
    return [
        VillageRow(
            village_id=row["village_id"],
            x=row["x"],
            y=row["y"],
            tribe=row["tribe"],
            name=row["name"],
            player_id=row["player_id"],
            player_name=row["player_name"],
            alliance_id=row["alliance_id"],
            alliance_tag=row["alliance_tag"],
            population=row["population"],
            region=row["region"],
            is_capital=bool(row["is_capital"]),
            is_city=bool(row["is_city"]),
            is_harbor=bool(row["is_harbor"]),
            victory_points=row["victory_points"],
        )
        for row in rows
    ]


def list_dates(conn: sqlite3.Connection) -> list[str]:
    """All snapshot dates, ascending (ISO dates sort chronologically)."""
    rows = cast(
        list[Mapping[str, str]],
        conn.execute("SELECT snapshot_date FROM snapshots ORDER BY snapshot_date ASC").fetchall(),
    )
    return [row["snapshot_date"] for row in rows]


def load_latest(conn: sqlite3.Connection) -> SnapshotRecord | None:
    """The most recent ``snapshots`` row, or None when no snapshot exists."""
    row = cast(
        Mapping[str, str] | None,
        conn.execute(
            "SELECT snapshot_date, source FROM snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone(),
    )
    if row is None:
        return None
    return SnapshotRecord(snapshot_date=row["snapshot_date"], source=row["source"])


def get_settings(conn: sqlite3.Connection) -> dict[str, JsonValue]:
    """All settings as parsed JSON values (empty dict when none are stored)."""
    rows = cast(
        list[Mapping[str, str]],
        conn.execute("SELECT key, value FROM settings").fetchall(),
    )
    return {row["key"]: _load_json(row["value"]) for row in rows}


def get_setting(conn: sqlite3.Connection, key: str) -> JsonValue | None:
    """One setting's parsed JSON value, or None when the key is absent."""
    row = cast(
        Mapping[str, str] | None,
        conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone(),
    )
    return _load_json(row["value"]) if row is not None else None


def set_settings(conn: sqlite3.Connection, kvs: dict[str, JsonValue]) -> None:
    """Upsert settings keys in one transaction; values are stored as JSON.

    Every value must be JSON-serializable — a non-serializable value raises
    ValueError before anything is written (validation happens up front, so a
    bad key never leaves a partial write behind). An empty dict is a no-op.
    """
    encoded: list[tuple[str, str, str]] = []
    for key, value in kvs.items():
        try:
            text = json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"setting {key!r} is not JSON-serializable: {exc}") from exc
        encoded.append((key, text, _utc_now()))
    with conn:
        _ = conn.executemany(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            encoded,
        )


def append_log(conn: sqlite3.Connection, job: str, level: str, message: str) -> None:
    """Append one job_log row with a UTC ISO timestamp (commits its own transaction).

    The ``with conn:`` wrapper is REQUIRED: under Python's legacy sqlite3
    isolation (``isolation_level=""``) an INSERT opens an implicit transaction
    that is ROLLED BACK when the connection closes without a commit — an entry
    written on one connection would be invisible to later connections. The
    cross-connection test in test_store.py locks this behavior.
    """
    with conn:
        _ = conn.execute(
            "INSERT INTO job_log (ts, job, level, message) VALUES (?, ?, ?, ?)",
            (_utc_now(), job, level, message),
        )


def recent_logs(conn: sqlite3.Connection, n: int = 50) -> list[dict[str, str]]:
    """The ``n`` most recent job_log rows, newest first, as ts/job/level/message dicts."""
    rows = cast(
        list[Mapping[str, str]],
        conn.execute(
            "SELECT ts, job, level, message FROM job_log ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall(),
    )
    return [dict(row) for row in rows]


# --- analysis aggregators (dashboard) -------------------------------------------
#
# One GROUP BY query per series — no row explosion: the dashboard's
# /api/analysis/* endpoints hand these day lists to the pure functions in
# travian.analysis. ``from_date``/``to_date`` are inclusive on
# ``snapshot_date``; empty ``alliance_ids`` returns [] (SQL ``IN ()`` is
# invalid).


def alliance_ids_by_tag(conn: sqlite3.Connection, date: str) -> dict[str, list[int]]:
    """``tag → [alliance_id]`` for ``date`` (``alliance_id != 0``).

    One tag may union several alliance ids (the metrics resolution
    semantics); the id lists are sorted ascending for determinism.
    """
    rows = cast(
        list[Mapping[str, object]],
        conn.execute(
            """
            SELECT DISTINCT alliance_tag, alliance_id FROM villages
            WHERE snapshot_date = ? AND alliance_id != 0
            ORDER BY alliance_tag, alliance_id
            """,
            (date,),
        ).fetchall(),
    )
    by_tag: dict[str, list[int]] = {}
    for row in rows:
        tag = cast(str, row["alliance_tag"])
        by_tag.setdefault(tag, []).append(cast(int, row["alliance_id"]))
    return by_tag


def region_days(
    conn: sqlite3.Connection,
    from_date: str,
    to_date: str,
    alliance_ids: set[int],
) -> list[RegionDay]:
    """Per-day per-region aggregates over the inclusive date range.

    ``our_pop`` sums the population of the given alliance ids, ``total_pop``
    of ALL villages in the region that day; ``region`` NULL groups as ``""``
    (COALESCE — same semantics as the region metrics). Ordered by date ASC,
    region ASC.
    """
    if not alliance_ids:
        return []
    placeholders = ",".join("?" * len(alliance_ids))
    rows = cast(
        list[Mapping[str, object]],
        conn.execute(
            f"""
            SELECT snapshot_date, COALESCE(region, '') AS region,
                   SUM(population) AS total_pop,
                   SUM(CASE WHEN alliance_id IN ({placeholders})
                            THEN population ELSE 0 END) AS our_pop
            FROM villages
            WHERE snapshot_date BETWEEN ? AND ?
            GROUP BY snapshot_date, COALESCE(region, '')
            ORDER BY snapshot_date ASC, region ASC
            """,
            (*alliance_ids, from_date, to_date),
        ).fetchall(),
    )
    return [
        RegionDay(
            date=cast(str, row["snapshot_date"]),
            region=cast(str, row["region"]),
            our_pop=cast(int, row["our_pop"]),
            total_pop=cast(int, row["total_pop"]),
        )
        for row in rows
    ]


def alliance_days(
    conn: sqlite3.Connection,
    from_date: str,
    to_date: str,
    alliance_ids: set[int],
) -> list[AllianceDay]:
    """Per-day per-alliance aggregates over the inclusive date range.

    ``alliance_tag`` = ``MAX(alliance_tag)`` (lexicographically greatest —
    map.sql is tag-consistent per alliance_id within a snapshot, so this is
    the tag); ``villages`` = COUNT(*), ``vp`` = SUM(victory_points). Ordered
    by date ASC, alliance_id ASC.
    """
    if not alliance_ids:
        return []
    placeholders = ",".join("?" * len(alliance_ids))
    rows = cast(
        list[Mapping[str, object]],
        conn.execute(
            f"""
            SELECT snapshot_date, alliance_id, MAX(alliance_tag) AS alliance_tag,
                   COUNT(*) AS villages, SUM(population) AS population,
                   SUM(victory_points) AS vp
            FROM villages
            WHERE snapshot_date BETWEEN ? AND ? AND alliance_id IN ({placeholders})
            GROUP BY snapshot_date, alliance_id
            ORDER BY snapshot_date ASC, alliance_id ASC
            """,
            (from_date, to_date, *alliance_ids),
        ).fetchall(),
    )
    return [
        AllianceDay(
            date=cast(str, row["snapshot_date"]),
            alliance_id=cast(int, row["alliance_id"]),
            alliance_tag=cast(str, row["alliance_tag"]),
            villages=cast(int, row["villages"]),
            population=cast(int, row["population"]),
            vp=cast(int, row["vp"]),
        )
        for row in rows
    ]


def summary_days(
    conn: sqlite3.Connection,
    from_date: str,
    to_date: str,
    alliance_ids: set[int],
) -> list[SummaryDay]:
    """Per-day headline aggregates for ``alliance_ids`` over the date range.

    ``villages`` = COUNT(*), ``players`` = COUNT(DISTINCT player_id),
    ``vp`` = SUM(victory_points). Ordered by date ASC.
    """
    if not alliance_ids:
        return []
    placeholders = ",".join("?" * len(alliance_ids))
    rows = cast(
        list[Mapping[str, object]],
        conn.execute(
            f"""
            SELECT snapshot_date, COUNT(*) AS villages, SUM(population) AS population,
                   COUNT(DISTINCT player_id) AS players, SUM(victory_points) AS vp
            FROM villages
            WHERE snapshot_date BETWEEN ? AND ? AND alliance_id IN ({placeholders})
            GROUP BY snapshot_date
            ORDER BY snapshot_date ASC
            """,
            (from_date, to_date, *alliance_ids),
        ).fetchall(),
    )
    return [
        SummaryDay(
            date=cast(str, row["snapshot_date"]),
            villages=cast(int, row["villages"]),
            population=cast(int, row["population"]),
            players=cast(int, row["players"]),
            vp=cast(int, row["vp"]),
        )
        for row in rows
    ]


def _load_json(text: str) -> JsonValue:
    return cast(JsonValue, json.loads(text))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
