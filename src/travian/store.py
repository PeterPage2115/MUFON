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

from travian.models import VillageRow

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
    """Append one job_log row with a UTC ISO timestamp (single statement, autocommits)."""
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


def _load_json(text: str) -> JsonValue:
    return cast(JsonValue, json.loads(text))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
