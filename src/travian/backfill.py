"""Optional backfill CLI (task 5): stream a friend's Postgres into the SQLite store.

``python -m travian.backfill`` introspects ``information_schema`` for a table
that has both a ``_date`` and a ``village_id`` column (e.g. ``village_snapshot``),
then streams every distinct date's rows (batches of 5000) through
:func:`travian.store.save_snapshot` with ``source='backfill'``.

Read-only: the source database only ever sees SELECTs. The bot must never
depend on this CLI — every expected failure (no DSN, bad connection, no
candidate table, missing required column, unwritable SQLite target) prints a
readable message and exits 0. Only unexpected bugs raise.

Source schema (friend's DB, camelCase) vs target DDL (snake_case)::

    village_id,x,y,tribe,village_name,player_id,player_name,alliance_id,
    alliance_tag,population,region,isCapital,isCity,isHarbor,victory_points,
    _date

mapped as ``village_name->name``, ``isCapital->is_capital``,
``isCity->is_city``, ``isHarbor->is_harbor``, ``_date->snapshot_date`` (the
date argument of ``save_snapshot``); everything else keeps its name. Value
coercion is pydantic's lax mode (Decimal->int, ``1/0`` or ``"TRUE"``->bool,
numeric strings->int) — verified against VillageRow; ``region`` NULL stays
``None``.

Table selection: if several tables have both columns, prefer the one whose
name contains both ``village`` and ``snapshot``; otherwise take the first in
alphabetical order. Either way the chosen table is printed.

SQLite target precedence: ``--sqlite PATH`` flag > ``SQLITE_PATH`` env >
``/data/travian.db``.

Connection seam: the module talks to Postgres through the tiny
:class:`SourceConnection` Protocol (asyncpg's ``fetch``/``close``). Tests
drive the orchestration with a fake connection; the real asyncpg connection
is created in :func:`_connect` and cast at that single boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import os
import sqlite3
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Protocol, cast

import asyncpg  # pyright: ignore[reportMissingTypeStubs]  # asyncpg ships no type stubs
from pydantic import ValidationError

from travian import store
from travian.models import VillageRow

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS: tuple[str, ...] = (
    "village_id",
    "x",
    "y",
    "tribe",
    "village_name",
    "player_id",
    "player_name",
    "alliance_id",
    "alliance_tag",
    "population",
    "region",
    "isCapital",
    "isCity",
    "isHarbor",
    "victory_points",
    "_date",
)

# Source column -> target name; absent entries keep their own name.
_RENAMES: Mapping[str, str] = {
    "village_name": "name",
    "isCapital": "is_capital",
    "isCity": "is_city",
    "isHarbor": "is_harbor",
    "_date": "snapshot_date",
}

BATCH_SIZE = 5000
DEFAULT_SQLITE_PATH = "/data/travian.db"

type SourceValue = int | float | str | bool | datetime.date | datetime.datetime | Decimal | None
type SourceRow = Mapping[str, SourceValue]


class SourceConnection(Protocol):
    """The asyncpg surface backfill needs (``fetch``/``close``)."""

    async def fetch(self, query: str, *args: object) -> Sequence[SourceRow]: ...

    async def close(self) -> None: ...


def validate_columns(columns: Sequence[str]) -> str | None:
    """Message naming every missing required source column, or None when complete."""
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if not missing:
        return None
    return f"missing required column(s): {', '.join(missing)}"


def _select_table(tables: Sequence[str]) -> str | None:
    """Prefer the table whose name contains ``village`` and ``snapshot``, else the alphabetically first."""
    if not tables:
        return None
    named = sorted(t for t in tables if "village" in t.lower() and "snapshot" in t.lower())
    return (named or sorted(tables))[0]


def _as_date(value: object) -> str:
    """Normalize a ``_date`` value to an ISO ``YYYY-MM-DD`` string."""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    raise ValueError(f"cannot interpret {value!r} as a snapshot date")


def map_row(raw: SourceRow) -> VillageRow:
    """Translate one source row (camelCase) into a snake_case :class:`VillageRow`.

    ``_date`` is the snapshot's date, carried by the caller into
    ``save_snapshot`` — it does not land in the row itself.
    """
    values: dict[str, object] = {
        _RENAMES.get(name, name): raw[name] for name in REQUIRED_COLUMNS if name != "_date"
    }
    return VillageRow.model_validate(values)


async def _discover_tables(conn: SourceConnection) -> list[str]:
    """Tables having both a ``_date`` and a ``village_id`` column, alphabetical."""
    query = (
        "SELECT table_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND column_name IN ('_date', 'village_id')"
        " GROUP BY table_name HAVING COUNT(DISTINCT column_name) = 2 ORDER BY table_name"
    )
    return [cast(str, row["table_name"]) for row in await conn.fetch(query)]


async def _table_columns(conn: SourceConnection, table: str) -> list[str]:
    query = (
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = $1"
    )
    return [cast(str, row["column_name"]) for row in await conn.fetch(query, table)]


async def _distinct_dates(conn: SourceConnection, table: str) -> list[str]:
    """Every distinct ``_date`` of ``table`` as ISO ``YYYY-MM-DD``, ascending."""
    query = (
        f"SELECT DISTINCT _date FROM public.{_quote_ident(table)}"
        " WHERE _date IS NOT NULL ORDER BY _date"
    )
    return [_as_date(row["_date"]) for row in await conn.fetch(query)]


async def _fetch_batch(
    conn: SourceConnection, table: str, date: str, offset: int, limit: int
) -> Sequence[SourceRow]:
    """One page of ``limit`` rows for ``date`` (asyncpg ``$1`` placeholders)."""
    columns = ", ".join(_quote_ident(name) for name in REQUIRED_COLUMNS)
    query = (
        f"SELECT {columns} FROM public.{_quote_ident(table)}"
        " WHERE _date = $1 ORDER BY village_id LIMIT $2 OFFSET $3"
    )
    return await conn.fetch(query, date, limit, offset)


def _quote_ident(name: str) -> str:
    """Double-quote a SQL identifier, escaping embedded quotes."""
    return '"' + name.replace('"', '""') + '"'


async def _stream_rows(conn: SourceConnection, table: str, date: str) -> list[VillageRow]:
    """All of ``date``'s rows as VillageRows, fetched in batches of :data:`BATCH_SIZE`."""
    rows: list[VillageRow] = []
    offset = 0
    while True:
        batch = await _fetch_batch(conn, table, date, offset, BATCH_SIZE)
        if not batch:
            return rows
        for raw in batch:
            try:
                rows.append(map_row(raw))
            except ValidationError as exc:
                logger.warning("backfill: skipping row village_id=%s: %s", raw.get("village_id"), exc)
        offset += BATCH_SIZE


async def _backfill(args: argparse.Namespace, conn: SourceConnection) -> int:
    """Introspect, validate, then stream every date into the SQLite store. Always 0."""
    tables = await _discover_tables(conn)
    if not tables:
        print("backfill: no table with both '_date' and 'village_id' columns found, skipping")
        return 0
    table = _select_table(tables)
    assert table is not None  # guarded by the empty check above
    if len(tables) > 1:
        print(f"backfill: using table {table!r} (candidates: {', '.join(tables)})")
    else:
        print(f"backfill: using table {table!r}")
    missing = validate_columns(await _table_columns(conn, table))
    if missing is not None:
        print(f"backfill: table {table!r} {missing} - skipping")
        return 0
    dates = await _distinct_dates(conn, table)
    print(f"backfill: {len(dates)} snapshot date(s): {', '.join(dates)}")
    if cast(bool, args.dry_run):
        print("backfill: dry-run - nothing written")
        return 0
    sqlite_path = cast(str | None, args.sqlite) or os.environ.get("SQLITE_PATH", DEFAULT_SQLITE_PATH)
    try:
        sqlite_conn = store.connect(sqlite_path)
    except sqlite3.OperationalError as exc:
        print(f"backfill: cannot open SQLite database {sqlite_path!r}: {exc} - skipping")
        return 0
    store.init_schema(sqlite_conn)
    try:
        for date in dates:
            rows = await _stream_rows(conn, table, date)
            store.save_snapshot(sqlite_conn, date, rows, source="backfill")
            print(f"backfill: saved {date} ({len(rows)} villages)")
    finally:
        sqlite_conn.close()
    return 0


async def _connect(dsn: str) -> SourceConnection | None:
    """Open the asyncpg connection, or print a readable error and return None."""
    try:
        # asyncpg is untyped; the cast pins its result to our typed seam.
        return cast(SourceConnection, await asyncpg.connect(dsn))  # pyright: ignore[reportUnknownMemberType]
    except (OSError, asyncpg.PostgresError, ValueError) as exc:
        print(f"backfill: cannot connect to source database: {exc}")
        return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="travian.backfill")
    _ = parser.add_argument("--dry-run", action="store_true", help="print found tables and dates, write nothing")
    _ = parser.add_argument(
        "--sqlite",
        default=None,
        help="target SQLite path (overrides SQLITE_PATH env; default /data/travian.db)",
    )
    return parser.parse_args(argv)


async def _run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dsn = os.environ.get("BACKFILL_DSN")
    if dsn is None:
        print("BACKFILL_DSN not set, skipping")
        return 0
    conn = await _connect(dsn)
    if conn is None:
        return 0
    try:
        return await _backfill(args, conn)
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``python -m travian.backfill [--dry-run] [--sqlite PATH]``."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    return asyncio.run(_run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
