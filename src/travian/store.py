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
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TypedDict, cast

from travian.models import (
    AllianceDay,
    PlayerHistoryPoint,
    RegionDay,
    SummaryDay,
    VillageHistoryPoint,
    VillageRow,
)

#: Full coordinate query: ``x|y`` or ``x,y`` with optional padding and signs.
_COORD_RE = re.compile(r"^(-?\d+)\s*[|,]\s*(-?\d+)$")

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
    CREATE INDEX IF NOT EXISTS idx_villages_player
        ON villages (player_id, snapshot_date)
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
    """One ``snapshots`` row: the day, its write time and which source wrote it.

    ``created_at`` doubles as the snapshot's identity for the dashboard's
    payload cache key (a re-fetch of the same day replaces the snapshot and
    bumps the timestamp)."""

    snapshot_date: str
    source: str
    created_at: str = ""


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


def _row_to_village(row: _VillageColumns) -> VillageRow:
    """Map one ``villages`` row to :class:`VillageRow` (round-trips save_snapshot)."""
    return VillageRow(
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
    return [_row_to_village(row) for row in rows]


def _parse_coords(query: str) -> tuple[int, int] | None:
    """``(x, y)`` for a full ``x|y`` / ``x,y`` coordinate query, else None."""
    match = _COORD_RE.match(query)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _escape_like(text: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so LIKE matches the text literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_villages(conn: sqlite3.Connection, snapshot_date: str, query: str, limit: int) -> list[VillageRow]:
    """Villages of ``snapshot_date`` matching ``query``, population DESC.

    A full ``x|y`` or ``x,y`` (spaces allowed, negatives allowed) is an exact
    coordinate match; anything else is a literal case-insensitive substring
    search over ``name`` and ``player_name``. Ties break on ``village_id``.
    """
    trimmed = query.strip()
    coords = _parse_coords(trimmed)
    if coords is not None:
        x, y = coords
        rows = cast(
            list[_VillageColumns],
            conn.execute(
                "SELECT * FROM villages WHERE snapshot_date = ? AND x = ? AND y = ? "
                + "ORDER BY population DESC, village_id ASC LIMIT ?",
                (snapshot_date, x, y, limit),
            ).fetchall(),
        )
    else:
        pattern = "%" + _escape_like(trimmed) + "%"
        rows = cast(
            list[_VillageColumns],
            conn.execute(
                "SELECT * FROM villages WHERE snapshot_date = ? AND ("
                + "name LIKE ? ESCAPE '\\' COLLATE NOCASE OR "
                + "player_name LIKE ? ESCAPE '\\' COLLATE NOCASE"
                + ") ORDER BY population DESC, village_id ASC LIMIT ?",
                (snapshot_date, pattern, pattern, limit),
            ).fetchall(),
        )
    return [_row_to_village(row) for row in rows]


def village_history(conn: sqlite3.Connection, village_id: int, days: int) -> list[VillageHistoryPoint]:
    """The newest ``days`` stored observations of ``village_id``, ASC by date.

    One point per snapshot date the village existed in — a village removed
    from the map simply has no row on later dates.
    """
    rows = cast(
        list[_VillageColumns],
        conn.execute(
            "SELECT * FROM villages WHERE village_id = ? ORDER BY snapshot_date DESC LIMIT ?",
            (village_id, days),
        ).fetchall(),
    )
    rows.reverse()
    return [
        VillageHistoryPoint(
            snapshot_date=row["snapshot_date"],
            name=row["name"],
            x=row["x"],
            y=row["y"],
            player_name=row["player_name"],
            alliance_tag=row["alliance_tag"],
            population=row["population"],
        )
        for row in rows
    ]


def player_history(conn: sqlite3.Connection, player_id: int, days: int) -> list[PlayerHistoryPoint]:
    """Per-snapshot aggregates for one stable ``player_id``, ASC by date.

    One point per snapshot date the player owned at least one village, over
    the newest ``days`` snapshot dates (``idx_villages_player`` serves the
    lookup). Name/alliance are the date's values (MAX over the player's rows
    — a player maps to one name per snapshot in the map data).
    """
    dates = list_dates(conn)[-days:]
    if not dates:
        return []
    placeholders = ",".join("?" * len(dates))
    rows = cast(
        list[Mapping[str, object]],
        conn.execute(
            "SELECT snapshot_date, MAX(player_name) AS player_name,"
            + " MAX(alliance_tag) AS alliance_tag, COUNT(*) AS villages,"
            + " SUM(population) AS population, SUM(victory_points) AS vp"
            + " FROM villages WHERE player_id = ? AND snapshot_date IN (" + placeholders + ")"
            + " GROUP BY snapshot_date ORDER BY snapshot_date",
            [player_id, *dates],
        ).fetchall(),
    )
    return [
        PlayerHistoryPoint(
            snapshot_date=_agg_str(row, "snapshot_date"),
            player_name=_agg_str(row, "player_name"),
            alliance_tag=(_agg_str(row, "alliance_tag") or None) if row["alliance_tag"] else None,
            villages=_agg_int(row, "villages"),
            population=_agg_int(row, "population"),
            vp=_agg_int(row, "vp"),
        )
        for row in rows
    ]


def villages_in_region(
    conn: sqlite3.Connection,
    snapshot_date: str,
    region: str,
    limit: int,
    *,
    side_ids: set[int] | None = None,
    filter_ids: set[int] | None = None,
) -> list[tuple[VillageRow, str]]:
    """Villages of one region on one date, population DESC, capped at ``limit``.

    ``side`` is ``tracked`` when the village's alliance_id is in ``side_ids``
    (the combined selection) and ``other`` otherwise; with ``filter_ids`` set
    (a single-tag selection) only those ids are returned and every row is
    ``tracked``. ``region`` NULL rows are excluded (region-less villages).
    """
    sql = "SELECT * FROM villages WHERE snapshot_date = ? AND region = ?"
    params: list[object] = [snapshot_date, region]
    if filter_ids is not None:
        if not filter_ids:
            return []
        placeholders = ",".join("?" * len(filter_ids))
        sql += " AND alliance_id IN (" + placeholders + ")"
        params.extend(sorted(filter_ids))
    sql += " ORDER BY population DESC, village_id ASC LIMIT ?"
    params.append(limit)
    rows = cast(list[_VillageColumns], conn.execute(sql, params).fetchall())
    return [
        (
            _row_to_village(row),
            "tracked" if side_ids is not None and row["alliance_id"] in side_ids else "other",
        )
        for row in rows
    ]


# --- SQL aggregation helpers (Faza 3 performance contract) ----------------------
#
# The dashboard's Regions/Overview/Compare endpoints must hold the 1.0 s p95
# bar on the 60k×7 seed. Aggregates are computed in SQL (GROUP BY) instead of
# materializing every village row into VillageRow objects; the pure merge
# functions in metrics.py keep the exact report semantics.


@dataclass(frozen=True)
class RegionAggregate:
    """Per-region aggregate of ONE date (SQL GROUP BY, no row materialization)."""

    region: str
    our_villages: int
    our_pop: int
    our_vp: int
    total_villages: int
    total_pop: int


@dataclass(frozen=True)
class PlayerAggregate:
    """Per-player aggregate of ONE date (SQL GROUP BY, no row materialization)."""

    player_id: int
    player_name: str
    villages: int
    population: int
    vp: int


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


def _region_key(row: Mapping[str, object]) -> str:
    """The region group key: NULL region groups as "" (metrics convention)."""
    value = row["region"]
    return cast(str, value) if value is not None else ""


def _agg_int(row: Mapping[str, object], key: str) -> int:
    """Typed accessor for the SQL aggregate rows (int columns)."""
    return cast(int, row[key])


def _agg_str(row: Mapping[str, object], key: str) -> str:
    """Typed accessor for the SQL aggregate rows (str columns)."""
    return cast(str, row[key])


def region_aggregates(
    conn: sqlite3.Connection, date: str, alliance_ids: set[int]
) -> dict[str, RegionAggregate]:
    """``region → RegionAggregate`` for one date (NULL region groups as "").

    ``alliance_ids`` may be empty → our_* fields are 0 (SQL ``IN ()`` is
    invalid, so an empty set short-circuits to a totals-only query).
    """
    if not alliance_ids:
        rows = cast(
            list[Mapping[str, object]],
            conn.execute(
                "SELECT region, COUNT(*) AS total_villages, SUM(population) AS total_pop" + " FROM villages WHERE snapshot_date = ? GROUP BY region",
                (date,),
            ).fetchall(),
        )
        return {
            _region_key(row): RegionAggregate(
                region=_region_key(row),
                our_villages=0,
                our_pop=0,
                our_vp=0,
                total_villages=_agg_int(row, "total_villages"),
                total_pop=_agg_int(row, "total_pop"),
            )
            for row in rows
        }
    placeholders = _placeholders(len(alliance_ids))
    ids = tuple(sorted(alliance_ids))
    rows = cast(
        list[Mapping[str, object]],
        conn.execute(
            "SELECT region," + " SUM(CASE WHEN alliance_id IN (" + placeholders + ") THEN 1 ELSE 0 END) AS our_villages," + " SUM(CASE WHEN alliance_id IN (" + placeholders + ") THEN population ELSE 0 END) AS our_pop," + " SUM(CASE WHEN alliance_id IN (" + placeholders + ") THEN victory_points ELSE 0 END) AS our_vp," + " COUNT(*) AS total_villages, SUM(population) AS total_pop" + " FROM villages WHERE snapshot_date = ? GROUP BY region",
            (*ids, *ids, *ids, date),
        ).fetchall(),
    )
    return {
        _region_key(row): RegionAggregate(
            region=_region_key(row),
            our_villages=_agg_int(row, "our_villages"),
            our_pop=_agg_int(row, "our_pop"),
            our_vp=_agg_int(row, "our_vp"),
            total_villages=_agg_int(row, "total_villages"),
            total_pop=_agg_int(row, "total_pop"),
        )
        for row in rows
    }


def player_aggregates(
    conn: sqlite3.Connection, date: str, alliance_ids: set[int]
) -> dict[int, PlayerAggregate]:
    """``player_id → PlayerAggregate`` for one date (empty ids → {})."""
    if not alliance_ids:
        return {}
    placeholders = _placeholders(len(alliance_ids))
    rows = cast(
        list[Mapping[str, object]],
        conn.execute(
            "SELECT player_id, MAX(player_name) AS player_name, COUNT(*) AS villages," + " SUM(population) AS population, SUM(victory_points) AS vp" + " FROM villages WHERE snapshot_date = ? AND alliance_id IN (" + placeholders + ")" + " GROUP BY player_id",
            (date, *sorted(alliance_ids)),
        ).fetchall(),
    )
    return {
        _agg_int(row, "player_id"): PlayerAggregate(
            player_id=_agg_int(row, "player_id"),
            player_name=_agg_str(row, "player_name"),
            villages=_agg_int(row, "villages"),
            population=_agg_int(row, "population"),
            vp=_agg_int(row, "vp"),
        )
        for row in rows
    }


def alliance_totals(conn: sqlite3.Connection, date: str, alliance_ids: set[int]) -> tuple[int, int, int, int]:
    """(villages, population, players, vp) for ``alliance_ids`` on one date."""
    if not alliance_ids:
        return (0, 0, 0, 0)
    placeholders = _placeholders(len(alliance_ids))
    row = cast(
        Mapping[str, object] | None,
        conn.execute(
            "SELECT COUNT(*) AS villages, SUM(population) AS population," + " COUNT(DISTINCT player_id) AS players, SUM(victory_points) AS vp" + " FROM villages WHERE snapshot_date = ? AND alliance_id IN (" + placeholders + ")",
            (date, *sorted(alliance_ids)),
        ).fetchone(),
    )
    if row is None or row["villages"] is None:
        return (0, 0, 0, 0)
    return (
        _agg_int(row, "villages"),
        _agg_int(row, "population"),
        _agg_int(row, "players"),
        _agg_int(row, "vp"),
    )


def village_ids(conn: sqlite3.Connection, date: str) -> set[int]:
    """All village_ids of one date — an int-only scan (no row materialization)."""
    rows = cast(list[Mapping[str, int]], conn.execute("SELECT village_id FROM villages WHERE snapshot_date = ?", (date,)).fetchall())
    return {row["village_id"] for row in rows}


def ours_village_ids(conn: sqlite3.Connection, date: str, alliance_ids: set[int]) -> set[int]:
    """Village_ids of ``alliance_ids`` on one date (int-only scan)."""
    if not alliance_ids:
        return set()
    placeholders = _placeholders(len(alliance_ids))
    rows = cast(
        list[Mapping[str, int]],
        conn.execute(
            "SELECT village_id FROM villages WHERE snapshot_date = ? AND alliance_id IN (" + placeholders + ")",
            (date, *sorted(alliance_ids)),
        ).fetchall(),
    )
    return {row["village_id"] for row in rows}


def player_village_ids(conn: sqlite3.Connection, date: str, alliance_ids: set[int]) -> dict[int, set[int]]:
    """``player_id → {village_id}`` for one date (int-only scan)."""
    if not alliance_ids:
        return {}
    placeholders = _placeholders(len(alliance_ids))
    rows = cast(
        list[Mapping[str, int]],
        conn.execute(
            "SELECT player_id, village_id FROM villages" + " WHERE snapshot_date = ? AND alliance_id IN (" + placeholders + ")",
            (date, *sorted(alliance_ids)),
        ).fetchall(),
    )
    by_player: dict[int, set[int]] = {}
    for row in rows:
        by_player.setdefault(row["player_id"], set()).add(row["village_id"])
    return by_player


def region_alliance_totals_sql(conn: sqlite3.Connection, date: str) -> dict[str, list[tuple[str, int]]]:
    """``region → top-5 [(tag, population), ...]`` — SQL GROUP BY per
    ``(region, alliance_tag)`` instead of a Python scan over every row
    (ROADMAP.md §5 performance contract; same shape as
    ``metrics.region_alliance_totals``)."""
    rows = cast(
        list[Mapping[str, object]],
        conn.execute(
            "SELECT region, alliance_tag, SUM(population) AS population" + " FROM villages WHERE snapshot_date = ? GROUP BY region, alliance_tag" + " ORDER BY region, population DESC",
            (date,),
        ).fetchall(),
    )
    by_region: dict[str, list[tuple[str, int]]] = {}
    for row in rows:
        key = _region_key(row)
        by_region.setdefault(key, []).append((_agg_str(row, "alliance_tag"), _agg_int(row, "population")))
    return {region: pairs[:5] for region, pairs in by_region.items()}


def list_dates(conn: sqlite3.Connection) -> list[str]:
    """All snapshot dates, ascending (ISO dates sort chronologically)."""
    rows = cast(
        list[Mapping[str, str]],
        conn.execute("SELECT snapshot_date FROM snapshots ORDER BY snapshot_date ASC").fetchall(),
    )
    return [row["snapshot_date"] for row in rows]


def snapshot_counts(conn: sqlite3.Connection, date: str) -> tuple[int, int, int, int]:
    """(villages, players, alliances, total_population) for one snapshot date.

    One aggregate query (``alliance_id != 0`` = has an alliance, the map.sql
    convention) — the /api/status counts, previously computed in Python over
    every village row of the date.
    """
    row = cast(
        Mapping[str, int] | None,
        conn.execute(
            """
            SELECT COUNT(*) AS villages,
                   COUNT(DISTINCT player_id) AS players,
                   COUNT(DISTINCT CASE WHEN alliance_id != 0 THEN alliance_id END) AS alliances,
                   COALESCE(SUM(population), 0) AS total_population
            FROM villages
            WHERE snapshot_date = ?
            """,
            (date,),
        ).fetchone(),
    )
    assert row is not None  # aggregate without GROUP BY always returns one row
    return row["villages"], row["players"], row["alliances"], row["total_population"]


def load_latest(conn: sqlite3.Connection) -> SnapshotRecord | None:
    """The most recent ``snapshots`` row, or None when no snapshot exists."""
    row = cast(
        Mapping[str, str] | None,
        conn.execute(
            "SELECT snapshot_date, source, created_at FROM snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone(),
    )
    if row is None:
        return None
    return SnapshotRecord(
        snapshot_date=row["snapshot_date"], source=row["source"], created_at=row["created_at"]
    )


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


def recent_logs(
    conn: sqlite3.Connection,
    n: int = 50,
    *,
    job: str | None = None,
    level: str | None = None,
) -> list[dict[str, str]]:
    """The ``n`` most recent job_log rows, newest first, as ts/job/level/message dicts.

    ``job``/``level`` filter by exact match IN SQL (never a Python-side scan
    of a 500-row window); both are validated by the API layer before they
    reach this helper.
    """
    where: list[str] = []
    params: list[object] = []
    if job is not None:
        where.append("job = ?")
        params.append(job)
    if level is not None:
        where.append("level = ?")
        params.append(level)
    sql = "SELECT ts, job, level, message FROM job_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(n)
    rows = cast(
        list[Mapping[str, str]],
        conn.execute(sql, params).fetchall(),
    )
    return [dict(row) for row in rows]


#: Success-message prefixes the bot jobs write (main.py) and the dashboard
#: status reads back (app.py) — part of the status contract: only rows
#: starting with these count as successful fetches/reports. Writers go
#: through ``record_fetch_success``/``record_report_success`` (below), so
#: the prefix literal lives in exactly ONE place per job.
FETCH_SUCCESS_PREFIX: Final = "snapshot saved for "
REPORT_SUCCESS_PREFIX: Final = "report sent to channel "


def record_fetch_success(conn: sqlite3.Connection, snapshot_date: str, village_count: int) -> None:
    """Log one successful fetch — the ONLY writer of ``FETCH_SUCCESS_PREFIX`` rows.

    The message shape is the reader's contract
    (``latest_log_timestamp(job='fetch', level='info',
    message_prefix=FETCH_SUCCESS_PREFIX)``); keep writer and reader in sync
    through this helper instead of repeating the prefix literal.
    """
    append_log(conn, "fetch", "info", f"{FETCH_SUCCESS_PREFIX}{snapshot_date} ({village_count} villages)")


def record_report_success(conn: sqlite3.Connection, channel_id: int, snapshot_date: str) -> None:
    """Log one successful report — the ONLY writer of ``REPORT_SUCCESS_PREFIX`` rows."""
    append_log(conn, "report", "info", f"{REPORT_SUCCESS_PREFIX}{channel_id} (snapshot {snapshot_date})")


def latest_log_timestamp(
    conn: sqlite3.Connection, *, job: str, level: str, message_prefix: str
) -> str | None:
    """The ``ts`` of the newest ``job_log`` row matching ``job``, ``level``
    and the literal ``message_prefix``, or None when no row matches.

    Ordered by the autoincrement ``id`` (insertion order), so two entries
    written in the same second still resolve to the newest one; the prefix is
    matched literally (LIKE-wildcards escaped). Used for the admin
    ``last_successful_fetch``/``last_successful_report`` status fields.
    """
    row = cast(
        Mapping[str, str] | None,
        conn.execute(
            "SELECT ts FROM job_log WHERE job = ? AND level = ? AND message LIKE ? ESCAPE '\\'"
            + " ORDER BY id DESC LIMIT 1",
            (job, level, _escape_like(message_prefix) + "%"),
        ).fetchone(),
    )
    return row["ts"] if row is not None else None


def latest_job_log_timestamp(conn: sqlite3.Connection, *, job: str, level: str) -> str | None:
    """The ``ts`` of the newest ``job_log`` row for ``(job, level)`` — any
    message — or None when no row matches.

    Ordered by the autoincrement ``id`` (insertion order), so two entries
    written in the same second still resolve to the newest one. Used for the
    safe ``job_health`` status signal (timestamps only, never raw messages).
    """
    row = cast(
        Mapping[str, str] | None,
        conn.execute(
            "SELECT ts FROM job_log WHERE job = ? AND level = ? ORDER BY id DESC LIMIT 1",
            (job, level),
        ).fetchone(),
    )
    return row["ts"] if row is not None else None


def has_log_marker(conn: sqlite3.Connection, marker: str) -> bool:
    """True when a ``job='alert'``, ``level='info'`` row has exactly
    ``marker`` in its ``message`` (the failure-alert dedupe lookup)."""
    row = cast(
        Mapping[str, str] | None,
        conn.execute(
            "SELECT 1 FROM job_log WHERE job = 'alert' AND level = 'info' AND message = ? LIMIT 1",
            (marker,),
        ).fetchone(),
    )
    return row is not None


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
