"""Backup/restore contract for the SQLite database (Task 6).

Covers the online-backup API, atomicity (no partial destination files),
WAL-mode sources, retention pruning and the ``python -m travian.backup``
CLI. Never touches the production ``/data/travian.db`` — everything runs
on ``tmp_path`` databases.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from travian import backup, store
from travian.models import VillageRow

SNAPSHOT_DATE = "2026-08-01"


def _row(village_id: int, *, population: int) -> VillageRow:
    return VillageRow(
        village_id=village_id,
        x=village_id,
        y=village_id,
        tribe=1,
        name=f"Village {village_id}",
        player_id=1000 + village_id,
        player_name=f"Player {village_id}",
        alliance_id=7,
        alliance_tag="NOVA",
        population=population,
        region="Testland",
        is_capital=False,
        is_city=False,
        is_harbor=False,
        victory_points=10,
    )


def _seed_source(db: Path) -> None:
    conn = store.connect(db)
    store.init_schema(conn)
    store.save_snapshot(conn, SNAPSHOT_DATE, [_row(1, population=100), _row(2, population=110)])
    store.set_settings(conn, {"ALLIANCE_TAGS": ["NOVA"], "FETCH_HOUR": 0})
    store.append_log(conn, "fetch", "info", "snapshot stored")
    conn.close()


def _counts(db: Path) -> tuple[int, int, int, int]:
    conn = sqlite3.connect(db)
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM villages").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM job_log").fetchone()[0],
        )
    finally:
        conn.close()


def test_backup_and_restore_preserve_records(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    _seed_source(src)
    dest = tmp_path / "backups" / "mufon-20260801T000000Z.sqlite3"

    backup.backup_database(src, dest)
    assert dest.is_file()
    assert _counts(src) == _counts(dest) == (1, 2, 2, 1)

    restored = tmp_path / "restored.db"
    backup.restore_database(dest, restored)
    assert _counts(restored) == (1, 2, 2, 1)
    conn = sqlite3.connect(restored)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM villages").fetchall()}
        assert names == {"Village 1", "Village 2"}
        assert conn.execute("SELECT COUNT(*) FROM snapshots WHERE snapshot_date = ?", (SNAPSHOT_DATE,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_backup_covers_wal_data(tmp_path: Path) -> None:
    """A WAL-mode source with uncheckpointed data still backs up completely."""
    src = tmp_path / "wal.db"
    _seed_source(src)
    assert sqlite3.connect(src).execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    # Leave the WAL uncheckpointed: open a transaction and write, keep conn open.
    conn = store.connect(src)
    store.save_snapshot(conn, "2026-08-02", [_row(3, population=130)])
    dest = tmp_path / "mufon-wal-backup.sqlite3"
    backup.backup_database(src, dest)
    conn.close()
    assert _counts(dest)[0] == 2  # both snapshots present


def test_failed_backup_leaves_no_partial_file_and_no_temp(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(backup.BackupError, match="source database not found"):
        backup.backup_database(tmp_path / "missing.db", out / "mufon-x.sqlite3")
    assert list(out.iterdir()) == []

    # A corrupt/unopenable source must not leave a partial destination or a
    # stray temp file either.
    bogus = tmp_path / "bogus.db"
    bogus.write_text("not a database", encoding="utf-8")
    dest = out / "mufon-bogus.sqlite3"
    with pytest.raises(backup.BackupError):
        backup.backup_database(bogus, dest)
    assert not dest.exists()
    assert [p.name for p in out.iterdir()] == []


def test_restore_refuses_same_file(tmp_path: Path) -> None:
    db = tmp_path / "same.db"
    _seed_source(db)
    with pytest.raises(backup.BackupError, match="same file"):
        backup.restore_database(db, db)


def test_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError, match="not found"):
        backup.restore_database(tmp_path / "nope.sqlite3", tmp_path / "out.db")


def test_prune_keeps_exactly_n_newest(tmp_path: Path) -> None:
    out = tmp_path / "backups"
    out.mkdir()
    names = [f"mufon-2026080{i}T000000Z.sqlite3" for i in range(1, 6)]  # 01..05, ascending
    for name in names:
        (out / name).write_text("x", encoding="utf-8")
    (out / "other-file.sqlite3").write_text("x", encoding="utf-8")  # must never be touched

    removed = backup.prune_backups(out, keep=3)
    remaining = sorted(p.name for p in out.iterdir())
    assert remaining == names[2:] + ["other-file.sqlite3"]
    assert sorted(p.name for p in removed) == names[:2]

    with pytest.raises(backup.BackupError, match="keep"):
        backup.prune_backups(out, keep=0)


def test_cli_backup_and_restore(tmp_path: Path) -> None:
    src = tmp_path / "cli.db"
    _seed_source(src)
    out = tmp_path / "backups"

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "travian.backup", "backup", "--db", str(src), "--output-dir", str(out), "--keep", "2"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    backup_file = Path(proc.stdout.strip())
    assert backup_file.is_file()

    restored = tmp_path / "cli-restored.db"
    proc = subprocess.run(
        [sys.executable, "-m", "travian.backup", "restore", "--source", str(backup_file), "--db", str(restored)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert _counts(restored) == (1, 2, 2, 1)


def test_cli_error_codes(tmp_path: Path) -> None:
    db = tmp_path / "e.db"
    _seed_source(db)
    out = tmp_path / "out"
    out.mkdir()

    proc = subprocess.run(
        [sys.executable, "-m", "travian.backup", "backup", "--db", str(tmp_path / "missing.db"), "--output-dir", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "not found" in proc.stderr

    proc = subprocess.run(
        [sys.executable, "-m", "travian.backup", "backup", "--db", str(db), "--output-dir", str(out), "--keep", "0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "keep" in proc.stderr

    proc = subprocess.run(
        [sys.executable, "-m", "travian.backup", "restore", "--source", str(db), "--db", str(db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "same file" in proc.stderr
