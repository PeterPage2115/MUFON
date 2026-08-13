"""SQLite backup/restore for the MUFON database.

Uses the online ``sqlite3.Connection.backup`` API (safe against a live
WAL database — never ``shutil.copy`` of the main file), writes through a
temporary file in the destination directory and atomically renames it into
place, so a failed backup never leaves a partial ``mufon-*.sqlite3``.

CLI::

    python -m travian.backup backup --db PATH --output-dir DIR [--keep N]
    python -m travian.backup restore --source FILE --db PATH

Exit code 2 on usage/IO errors (missing source, source == destination,
invalid ``--keep``). Never prints settings, tokens or row data.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

BACKUP_PREFIX = "mufon-"
BACKUP_SUFFIX = ".sqlite3"


class BackupError(RuntimeError):
    """Reusable, message-only error (no secrets in messages)."""


def _atomic_copy(source: Path, destination: Path) -> None:
    """Copy ``source`` into ``destination`` via the SQLite online API.

    Writes to a temp file in the destination directory, then ``os.replace``
    — the destination either does not exist or is a complete backup.
    """
    if source.resolve() == destination.resolve():
        raise BackupError(f"source and destination are the same file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=BACKUP_PREFIX, suffix=".tmp", dir=str(destination.parent))
        os.close(fd)
        tmp_path = Path(tmp_name)
        # The sqlite3 connection context manager only handles transactions —
        # it never closes the connection, and an open file handle blocks
        # os.replace on Windows. Close explicitly before the rename.
        src_conn = sqlite3.connect(source)
        dst_conn = sqlite3.connect(tmp_path)
        try:
            with src_conn, dst_conn:
                _ = src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        _ = tmp_path.replace(destination)
        tmp_path = None
    except sqlite3.Error as exc:
        raise BackupError(f"backup failed: {exc}") from exc
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def backup_database(source: Path, destination: Path) -> None:
    """Create a consistent online backup of ``source`` at ``destination``."""
    if not Path(source).is_file():
        raise BackupError(f"source database not found: {source}")
    _atomic_copy(Path(source), Path(destination))


def restore_database(source: Path, destination: Path) -> None:
    """Restore ``source`` onto ``destination`` (atomic, refuses same-file)."""
    if not Path(source).is_file():
        raise BackupError(f"source backup not found: {source}")
    _atomic_copy(Path(source), Path(destination))


def prune_backups(directory: Path, keep: int) -> list[Path]:
    """Keep the newest ``keep`` ``mufon-*.sqlite3`` files, remove older ones.

    Only files matching this module's naming pattern are ever touched.
    Returns the removed paths. ``keep`` must be >= 1.
    """
    if keep < 1:
        raise BackupError(f"keep must be >= 1, got {keep}")
    directory = Path(directory)
    if not directory.is_dir():
        raise BackupError(f"backup directory not found: {directory}")
    backups = sorted(
        (p for p in directory.iterdir() if p.name.startswith(BACKUP_PREFIX) and p.name.endswith(BACKUP_SUFFIX)),
        key=lambda p: p.name,
        reverse=True,
    )
    removed: list[Path] = []
    for old in backups[keep:]:
        old.unlink(missing_ok=True)
        removed.append(old)
    return removed


def _default_backup_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"


def _cli_backup(args: argparse.Namespace) -> int:
    db = Path(cast(str, args.db))
    output_dir = Path(cast(str, args.output_dir))
    keep = cast(int, args.keep)
    if not db.is_file():
        print(f"ERROR: source database not found: {db}", file=sys.stderr)
        return 2
    if keep < 1:
        print(f"ERROR: --keep must be >= 1, got {keep}", file=sys.stderr)
        return 2
    destination = output_dir / _default_backup_name()
    try:
        backup_database(db, destination)
        _ = prune_backups(output_dir, keep)
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(destination)
    return 0


def _cli_restore(args: argparse.Namespace) -> int:
    source = Path(cast(str, args.source))
    db = Path(cast(str, args.db))
    if not source.is_file():
        print(f"ERROR: source backup not found: {source}", file=sys.stderr)
        return 2
    if source.resolve() == db.resolve():
        print(f"ERROR: source and destination are the same file: {source}", file=sys.stderr)
        return 2
    try:
        restore_database(source, db)
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(db)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m travian.backup", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="backup SQLITE_PATH into --output-dir (keeps newest N)")
    _ = backup.add_argument("--db", required=True, help="source SQLite database path")
    _ = backup.add_argument("--output-dir", required=True, help="directory for mufon-*.sqlite3 backups")
    _ = backup.add_argument("--keep", type=int, default=7, help="backups to keep (default: 7)")
    backup.set_defaults(func=_cli_backup)

    restore = sub.add_parser("restore", help="restore a mufon-*.sqlite3 backup onto --db")
    _ = restore.add_argument("--source", required=True, help="backup file to restore")
    _ = restore.add_argument("--db", required=True, help="destination database path")
    restore.set_defaults(func=_cli_restore)

    args = parser.parse_args(argv)
    func = cast(Callable[[argparse.Namespace], int], args.func)
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
