"""Bot entrypoint (task 9): discord client, scheduler, shared run functions.

Startup order (``main()``): ``init_schema`` → merged config (env + ``settings``
table) → startup validation (exit 1 on any problem) → ``TravianBot`` →
``bot.run(token)``. ``on_ready`` syncs the command tree and starts the
``AsyncIOScheduler``.

DECISIONS (documented for the plan):

- **Scheduler starts in ``on_ready``**, not before login: discord.py's loop
  IS the asyncio loop, and ``AsyncIOScheduler`` must be started from inside
  it. ``on_ready`` runs on that loop after login; reconnects re-fire
  ``on_ready``, so the scheduler is created exactly once (guarded by
  ``self.scheduler is None``).
- **Merged config**: the ``settings`` table overrides env for the allowed
  keys (``ALLIANCE_TAGS, CHANNEL_ID, FETCH_HOUR, FETCH_MINUTE, FETCH_TZ,
  REPORT_HOUR, REPORT_MINUTE, REPORT_TZ, ADMIN_ROLE_ID,
  REPORT_EMBED_COLOR``); ``DISCORD_TOKEN`` is ONLY read from env (never from
  the table), same for ``SQLITE_PATH`` and the ``DASHBOARD_*`` keys. Stored
  values are JSON (store policy): ints for hours/minutes/channel, list[str]
  for tags, int color.
- **``SERVER`` constant**: the plan defines no ``SERVER_NAME`` env var, so the
  server label in the embed is this constant (``build_report_embed`` takes
  ``data.server`` from it).
- **Empty resolved subset**: ``run_report`` always builds the report from the
  RESOLVED subset; an empty subset (no tags configured, or none resolvable)
  makes the report meaningless → ``append_log`` warning + return without
  sending (``job_report`` pre-checks first (T10) and skips without calling
  ``run_report``; ``/raport`` keeps ``run_report``'s own no-data path).
- **Shared run lock**: ``run_fetch``/``run_report`` are guarded by one
  ``asyncio.Lock`` (module state). A call while another run is in progress is
  SKIPPED and logged — never queued. The lock is bound per event loop
  (``_get_run_lock``): production runs everything on the bot's single loop
  (one lock), while tests drive the functions across separate
  ``asyncio.run`` invocations and must not share lock state.
- **``bot_loop`` module state**: set at runtime by ``on_ready``; task 12's
  dashboard dispatches ``run_fetch``/``run_report`` onto this loop with
  ``asyncio.run_coroutine_threadsafe(coro, bot_loop)``.
- **T9/T10 split**: T9 implemented both run functions fully; T10 finalized
  them — the empty-parse guard, off-loop blocking and the ``job_report``
  pre-checks below.
- **Empty-parse guard (T10)**: a 0-row ``parse_map_sql`` result (empty or
  truncated body with HTTP 200) is treated as a fetch failure:
  ``append_log('fetch', 'error', 'empty parse (0 villages) from map.sql,
  snapshot not saved')`` and NO ``save_snapshot`` — ``run_report``'s date
  guard then skips the day instead of reporting a misleading "0 villages".
- **Blocking off the bot loop (T10)**: ``fetch_map_sql`` (sync httpx, up to
  ~190 s with retries) runs in its own ``asyncio.to_thread``; the sqlite
  phase — connect + config + save + log — runs in a second
  ``asyncio.to_thread`` whose helper opens its own connection. A sqlite
  connection is never shared across threads and the loop never blocks
  (heartbeat ~41 s ≪ fetch worst case). ``channel.send`` and
  ``bot.get_channel`` stay ON the loop (discord.py is not thread-safe); the
  log entries they trigger go through ``_log_entry`` in a worker thread.
- **Gap deltas (T10)**: deltas across day gaps are computed normally
  (``prev`` = max date strictly older than ``latest``, however far back);
  when ``prev`` is more than one calendar day before ``latest`` an info
  entry ``deltas computed across gap: prev <date>`` is logged.
- **job_report pre-checks (T10)**: before calling ``run_report`` the daily
  job verifies: merged config's channel_id present → ``ALLIANCE_TAGS``
  non-empty → a snapshot exists (else ``no snapshot yet, skipping daily
  report``) → tags resolve against the latest snapshot. Any failure =
  ``append_log`` warning + return WITHOUT calling ``run_report``;
  ``run_report`` itself always builds from the resolved subset.
- **Command registration (T11 + report trim)**: ``register_commands``
  (commands.py) is called in ``TravianBot.__init__`` with this module's
  ``run_report``, ``_current_config``, ``run_villages`` and ``run_regions``
  (/raport, /wioski, /regiony). The admin role id is read from a FRESH merged
  config per command invocation, so dashboard changes apply immediately. The
  module graph is single-direction (commands.py imports nothing from this
  module — its config getter is typed against a minimal protocol), so no
  import cycle exists.
- **Logging**: ``main()`` configures the root logger at INFO
  (``logging.basicConfig``); module loggers inherit. All job errors are also
  recorded in the ``job_log`` table via ``append_log`` (job names
  ``fetch``/``report``/``config``).
- **append_log persistence (fixed at the root, T4-fix)**: ``store.append_log``
  commits its own transaction (``with conn:``) — the T9 workaround (explicit
  commits in the run functions) was removed.
- **Run status returns (T12, decision (a))**: ``run_fetch``/``run_report``
  RETURN a short status string (see the ``*_STATUS_*`` constants below) in
  addition to their existing logging — the dashboard's ``POST /api/actions/*``
  surfaces it in the response body for the UI toast. Nothing about logging or
  check order changed; ``job_*`` and ``/raport`` ignore the return value.
- **Dashboard bootstrap (T12)**: ``main()`` starts ``start_dashboard`` — a
  daemon thread running ``uvicorn.Server`` in factory mode: the app is created
  at serve start INSIDE the thread (a factory failure kills only the
  dashboard, never the bot). The app dispatches ``run_fetch``/``run_report``
  onto ``bot_loop`` (set by ``on_ready``) via
  ``asyncio.run_coroutine_threadsafe``; actions before ``on_ready`` → 409
  "bot not ready".

allow: SIZE_OK — the plan pins ``bot/main.py`` as the single bot module
(tasks 9-11 reference only this file plus ``commands.py``), so config merge,
run functions, jobs, the bot class and the entry point share this file by
plan contract — same rationale as the ``metrics.py``/``store.py`` markers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final, Literal, Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
import uvicorn
from apscheduler.schedulers.asyncio import (  # pyright: ignore[reportMissingTypeStubs]  # apscheduler 3.x ships no stubs
    AsyncIOScheduler,
)
from apscheduler.triggers.cron import CronTrigger  # pyright: ignore[reportMissingTypeStubs]
from discord import app_commands
from discord.abc import Messageable
from fastapi import FastAPI

from travian import store
from travian.bot.commands import register_commands
from travian.bot.report_embed import DAILY_SECTIONS, build_report_embed
from travian.dashboard.app import DashboardDeps, create_app, make_status_provider
from travian.map_sql import fetch_map_sql, parse_map_sql
from travian.metrics import (
    alliance_standings,
    compute_deltas,
    region_stats,
    resolve_alliance_ids,
    village_events,
)
from travian.models import ReportData, VillageRow
from travian.strings import NO_DATA_YET

logger = logging.getLogger(__name__)

DEFAULT_SQLITE_PATH = "/data/travian.db"
DEFAULT_FETCH_HOUR = 0
DEFAULT_FETCH_MINUTE = 15
DEFAULT_FETCH_TZ = "Europe/London"
DEFAULT_REPORT_HOUR = 9
DEFAULT_REPORT_MINUTE = 0
DEFAULT_REPORT_TZ = "Europe/Warsaw"
DEFAULT_EMBED_COLOR = 0x2ECC71
SERVER: Final = "cw.x2.international.travian.com"
MAP_SQL_URL: Final = "https://cw.x2.international.travian.com/map.sql"

# --- run status strings (T12, decision (a)) ------------------------------------
#
# Short outcome strings RETURNED by run_fetch/run_report (their logging is
# unchanged) — surfaced by the dashboard's POST /api/actions/* responses.
# The Literal aliases let basedpyright verify every return site; the Final
# constants give callers/tests stable names.

type RunFetchStatus = Literal["completed", "skipped (already running)", "empty parse", "failed"]
type RunReportStatus = Literal[
    "sent",
    "skipped (already running)",
    "no snapshot for today",
    "no data yet",
    "no alliance",
    "channel not found",
    "failed",
]

FETCH_STATUS_SKIPPED: Final[RunFetchStatus] = "skipped (already running)"
FETCH_STATUS_COMPLETED: Final[RunFetchStatus] = "completed"
FETCH_STATUS_EMPTY_PARSE: Final[RunFetchStatus] = "empty parse"
FETCH_STATUS_FAILED: Final[RunFetchStatus] = "failed"
REPORT_STATUS_SKIPPED: Final[RunReportStatus] = "skipped (already running)"
REPORT_STATUS_SENT: Final[RunReportStatus] = "sent"
REPORT_STATUS_NO_SNAPSHOT_TODAY: Final[RunReportStatus] = "no snapshot for today"
REPORT_STATUS_NO_DATA: Final[RunReportStatus] = "no data yet"
REPORT_STATUS_NO_ALLIANCE: Final[RunReportStatus] = "no alliance"
REPORT_STATUS_CHANNEL_NOT_FOUND: Final[RunReportStatus] = "channel not found"
REPORT_STATUS_FAILED: Final[RunReportStatus] = "failed"

#: The bot's running event loop, set by ``on_ready`` — task 12 dispatches the
#: dashboard's actions onto it via ``asyncio.run_coroutine_threadsafe``.
bot_loop: asyncio.AbstractEventLoop | None = None

#: The bot instance, set by ``main()`` — ``run_report`` resolves channels
#: through it. Assigned by tests to a fake with the same ``get_channel``.
current_bot: TravianBot | None = None

_run_lock: asyncio.Lock | None = None
_run_lock_loop: asyncio.AbstractEventLoop | None = None


@dataclass(frozen=True)
class MergedConfig:
    """Startup configuration: env merged with ``settings``-table overrides.

    ``discord_token`` and ``sqlite_path`` are env-only (never from the
    settings table); the remaining fields are env defaults overridable by the
    table. Stored settings values are JSON, coerced to these typed fields.
    """

    discord_token: str = ""
    sqlite_path: str = DEFAULT_SQLITE_PATH
    channel_id: int | None = None
    alliance_tags: list[str] = field(default_factory=list)
    tracked_alliances: list[str] = field(default_factory=list)
    fetch_hour: int = DEFAULT_FETCH_HOUR
    fetch_minute: int = DEFAULT_FETCH_MINUTE
    fetch_tz: str = DEFAULT_FETCH_TZ
    report_hour: int = DEFAULT_REPORT_HOUR
    report_minute: int = DEFAULT_REPORT_MINUTE
    report_tz: str = DEFAULT_REPORT_TZ
    admin_role_id: int | None = None
    report_embed_color: int = DEFAULT_EMBED_COLOR


# --- config: merge + validation -------------------------------------------------


def load_merged_config(conn: sqlite3.Connection, env: Mapping[str, str]) -> MergedConfig:
    """Build :class:`MergedConfig` from ``env`` with ``settings``-table overrides.

    Only the allowed keys can be overridden by the table; ``DISCORD_TOKEN``
    and ``SQLITE_PATH`` are read from env exclusively. Unparseable values
    raise ``ValueError`` with the offending key in the message (``main()``
    turns that into exit 1).
    """
    db = store.get_settings(conn)
    channel_raw = _pick(env, db, "CHANNEL_ID")
    return MergedConfig(
        discord_token=env.get("DISCORD_TOKEN", ""),
        sqlite_path=env.get("SQLITE_PATH", DEFAULT_SQLITE_PATH),
        channel_id=None if channel_raw is None else _as_int("CHANNEL_ID", channel_raw),
        alliance_tags=_as_tags("ALLIANCE_TAGS", _pick(env, db, "ALLIANCE_TAGS")),
        tracked_alliances=_as_tags("TRACKED_ALLIANCES", _pick(env, db, "TRACKED_ALLIANCES")),
        fetch_hour=_as_int("FETCH_HOUR", _pick(env, db, "FETCH_HOUR", DEFAULT_FETCH_HOUR)),
        fetch_minute=_as_int("FETCH_MINUTE", _pick(env, db, "FETCH_MINUTE", DEFAULT_FETCH_MINUTE)),
        fetch_tz=_as_str("FETCH_TZ", _pick(env, db, "FETCH_TZ", DEFAULT_FETCH_TZ)),
        report_hour=_as_int("REPORT_HOUR", _pick(env, db, "REPORT_HOUR", DEFAULT_REPORT_HOUR)),
        report_minute=_as_int("REPORT_MINUTE", _pick(env, db, "REPORT_MINUTE", DEFAULT_REPORT_MINUTE)),
        report_tz=_as_str("REPORT_TZ", _pick(env, db, "REPORT_TZ", DEFAULT_REPORT_TZ)),
        admin_role_id=(
            None if _pick(env, db, "ADMIN_ROLE_ID") is None else _as_int("ADMIN_ROLE_ID", _pick(env, db, "ADMIN_ROLE_ID"))
        ),
        report_embed_color=_as_color("REPORT_EMBED_COLOR", _pick(env, db, "REPORT_EMBED_COLOR", DEFAULT_EMBED_COLOR)),
    )


def validate_config(cfg: MergedConfig) -> None:
    """Startup validation of the merged config; logs a readable error and
    raises ``SystemExit(1)`` on the first problem.

    Checks: token present (env-only), channel present, both timezones
    constructible via ``ZoneInfo``, hours 0-23, minutes 0-59. ``ALLIANCE_TAGS``
    is deliberately NOT required (empty/unresolvable → runtime warning + daily
    skip in ``run_report``).
    """
    if not cfg.discord_token:
        logger.error("DISCORD_TOKEN not set (env only)")
        raise SystemExit(1)
    if cfg.channel_id is None:
        logger.error("CHANNEL_ID not set (env or settings)")
        raise SystemExit(1)
    for key, tz_name in (("FETCH_TZ", cfg.fetch_tz), ("REPORT_TZ", cfg.report_tz)):
        try:
            _ = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.error("invalid %s %r: unknown timezone", key, tz_name)
            raise SystemExit(1)
    for key, hour in (("FETCH_HOUR", cfg.fetch_hour), ("REPORT_HOUR", cfg.report_hour)):
        if not 0 <= hour <= 23:
            logger.error("invalid %s %d: hour must be between 0 and 23", key, hour)
            raise SystemExit(1)
    for key, minute in (("FETCH_MINUTE", cfg.fetch_minute), ("REPORT_MINUTE", cfg.report_minute)):
        if not 0 <= minute <= 59:
            logger.error("invalid %s %d: minute must be between 0 and 59", key, minute)
            raise SystemExit(1)


def _pick(env: Mapping[str, str], db: dict[str, store.JsonValue], key: str, default: object = None) -> object:
    """Settings-table value for ``key``, falling back to env, then ``default``.

    An EMPTY-string env value counts as unset (e.g. ``CHANNEL_ID=`` in
    ``.env.example``): it falls through to the settings table / default
    instead of failing to parse — so a container configured via the dashboard
    starts even when the env placeholder is empty.
    """
    if key in db:
        return db[key]
    value = env.get(key)
    if value is not None and value != "":
        return value
    return default


def _as_int(key: str, raw: object) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"setting {key}: expected an integer, got {raw!r}") from None
    raise ValueError(f"setting {key}: expected an integer, got {raw!r}")


def _as_str(key: str, raw: object) -> str:
    if isinstance(raw, str):
        return raw
    raise ValueError(f"setting {key}: expected a string, got {raw!r}")


def _as_color(key: str, raw: object) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool):
        color = raw
    elif isinstance(raw, str):
        try:
            color = int(raw, 0)  # accepts both "0x2ECC71" and decimal
        except ValueError:
            raise ValueError(f"setting {key}: expected an integer color, got {raw!r}") from None
    else:
        raise TypeError(f"setting {key}: expected an integer color, got {raw!r}")
    if not 0 <= color <= 0xFFFFFF:
        raise ValueError(f"setting {key}: color must be between 0x000000 and 0xFFFFFF, got {color!r}")
    return color


def _as_tags(key: str, raw: object) -> list[str]:
    """Coerce a tag-list setting (ALLIANCE_TAGS / TRACKED_ALLIANCES): env
    comma-separated string or settings list[str].

    Tags are stripped, empties dropped, duplicates removed (first occurrence
    wins) — the tag-matching semantics of task 6.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, list):
        raw_items = cast(list[object], raw)
        if not all(isinstance(item, str) for item in raw_items):
            raise TypeError(f"setting {key}: list values must be strings")
        parts = cast(list[str], raw_items)
    else:
        raise TypeError(f"setting {key}: expected comma-separated string or list, got {raw!r}")
    tags: list[str] = []
    for part in parts:
        tag = part.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


# --- shared run lock ------------------------------------------------------------


def _get_run_lock() -> asyncio.Lock:
    """The shared run lock bound to the CURRENT event loop (see module docstring)."""
    global _run_lock, _run_lock_loop
    loop = asyncio.get_running_loop()
    if _run_lock is None or _run_lock_loop is not loop:
        _run_lock = asyncio.Lock()
        _run_lock_loop = loop
    return _run_lock


async def _acquire(job: str) -> bool:
    """Try to acquire the run lock; False (and logged) when a run is in progress.

    Between the ``locked()`` check and ``await acquire()`` there is no await
    point, so on the single bot loop a second call can never race past the
    check — it is skipped, never queued.
    """
    lock = _get_run_lock()
    if lock.locked():
        _log_entry(job, "warning", f"{job} already running, skipping")
        return False
    _ = await lock.acquire()
    return True


def _release() -> None:
    _get_run_lock().release()


def _sqlite_path(env: Mapping[str, str]) -> str:
    return env.get("SQLITE_PATH", DEFAULT_SQLITE_PATH)


def _current_config() -> MergedConfig:
    conn = store.connect(_sqlite_path(os.environ))
    try:
        return load_merged_config(conn, os.environ)
    finally:
        conn.close()


def _log_entry(job: str, level: str, message: str) -> None:
    conn = store.connect(_sqlite_path(os.environ))
    try:
        store.append_log(conn, job, level, message)
    finally:
        conn.close()


def _record_failure(job: str, exc: Exception, conn: sqlite3.Connection | None) -> None:
    """Log a job failure (logger + job_log).

    ``exc`` is passed explicitly to ``logger.error`` so the traceback survives
    worker-thread calls (``logger.exception`` would find no active exception
    there); ``conn`` may be None when the failure happened before/during
    connect — the logger entry is still recorded.
    """
    logger.error("job %s failed: %s", job, exc, exc_info=exc)
    if conn is None:
        return
    try:
        store.append_log(conn, job, "error", str(exc))
    except sqlite3.Error:
        logger.exception("could not append job_log entry for %s", job)


def _record_failure_blocking(job: str, exc: Exception) -> None:
    """``_record_failure`` from a worker thread (opens its own connection)."""
    conn: sqlite3.Connection | None = None
    try:
        conn = store.connect(_sqlite_path(os.environ))
        _record_failure(job, exc, conn)
    finally:
        if conn is not None:
            conn.close()


# --- shared run functions (jobs, dashboard, /raport) ------------------------------


async def run_fetch() -> RunFetchStatus:
    """Fetch → parse → save today's map.sql snapshot (in ``FETCH_TZ``).

    All blocking work runs off the bot loop: ``fetch_map_sql`` (sync httpx,
    retries up to ~190 s) in its own ``asyncio.to_thread``, the sqlite phase
    (connect + config + save + log) in a second one that opens its own
    connection. An empty parse (0 rows — empty/truncated 200 body) logs an
    error and does NOT save a snapshot. Any failure is logged via
    ``append_log('fetch', 'error', ...)`` and never crashes the loop.

    Returns a short status string (task 12, decision (a)): ``completed``,
    ``skipped (already running)``, ``empty parse`` or ``failed``.
    """
    if not await _acquire("fetch"):
        return FETCH_STATUS_SKIPPED
    try:
        text = await asyncio.to_thread(fetch_map_sql, MAP_SQL_URL)
        result = await asyncio.to_thread(_fetch_snapshot_phase, text)
    except Exception as exc:  # noqa: BLE001 — plan: job failures are logged to job_log, never crash the loop
        await asyncio.to_thread(_record_failure_blocking, "fetch", exc)
        return FETCH_STATUS_FAILED
    finally:
        _release()
    return result


def _fetch_snapshot_phase(text: str) -> RunFetchStatus:
    """Sqlite phase of ``run_fetch``, in a worker thread (own connection).

    Config + parse + save + log. The EMPTY-PARSE GUARD lives here: 0 rows
    (empty/truncated 200 body) → ``append_log`` error and NO ``save_snapshot``
    — ``run_report``'s date guard then skips the day instead of reporting a
    misleading "0 villages". Returns the outcome status string.
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = store.connect(_sqlite_path(os.environ))
        cfg = load_merged_config(conn, os.environ)
        rows = parse_map_sql(text)
        if not rows:
            store.append_log(conn, "fetch", "error", "empty parse (0 villages) from map.sql, snapshot not saved")
            return FETCH_STATUS_EMPTY_PARSE
        snapshot_date = datetime.now(ZoneInfo(cfg.fetch_tz)).date().isoformat()
        store.save_snapshot(conn, snapshot_date, rows)
        store.append_log(conn, "fetch", "info", f"snapshot saved for {snapshot_date} ({len(rows)} villages)")
        return FETCH_STATUS_COMPLETED
    except Exception as exc:  # noqa: BLE001 — plan: job failures are logged to job_log, never crash the loop
        _record_failure("fetch", exc, conn)
        return FETCH_STATUS_FAILED
    finally:
        if conn is not None:
            conn.close()


@dataclass(frozen=True)
class _ReportPhase:
    """Outcome of ``run_report``'s blocking read+compute phase.

    ``action``: ``send`` (embeds + snapshot_date ready, send on the loop),
    ``no_data`` (no snapshots — send the no-data embed), ``stale`` /
    ``no_alliance`` / ``failed`` (already logged, nothing to send).
    """

    action: Literal["send", "no_data", "stale", "no_alliance", "failed"]
    embeds: list[discord.Embed]
    snapshot_date: str = ""


def _report_phase(require_today: bool) -> _ReportPhase:
    """The sqlite read + compute phase of ``run_report``, in a worker thread.

    Opens its own connection (never shared with the loop thread) and decides
    the outcome — send / no_data / stale / no_alliance / failed — logging the
    warnings and failures itself. Deltas across day gaps are computed, not
    None; a gap > 1 calendar day logs an info entry. ``channel.send`` is NOT
    part of this phase (it must run on the bot loop).
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = store.connect(_sqlite_path(os.environ))
        cfg = load_merged_config(conn, os.environ)
        latest = store.load_latest(conn)
        if latest is None:
            embed = discord.Embed(description=NO_DATA_YET, color=cfg.report_embed_color)
            return _ReportPhase(action="no_data", embeds=[embed])
        expected = datetime.now(ZoneInfo(cfg.fetch_tz)).date().isoformat()
        if require_today and latest.snapshot_date != expected:
            store.append_log(conn, "report", "warning", "no snapshot for today, skipping")
            return _ReportPhase(action="stale", embeds=[])
        previous = _previous_date(conn, latest.snapshot_date)
        if previous is not None:
            gap = date.fromisoformat(latest.snapshot_date) - date.fromisoformat(previous)
            if gap > timedelta(days=1):
                store.append_log(conn, "report", "info", f"deltas computed across gap: prev {previous}")
        curr_rows = store.load_villages(conn, latest.snapshot_date)
        prev_rows = store.load_villages(conn, previous) if previous is not None else None
        resolved, unresolved = resolve_alliance_ids(curr_rows, cfg.alliance_tags, conn)
        if not resolved:
            store.append_log(conn, "report", "warning", "no alliance configured, skipping report")
            return _ReportPhase(action="no_alliance", embeds=[])
        data = _build_report_data(cfg, latest.snapshot_date, curr_rows, prev_rows, resolved)
        embeds = build_report_embed(
            data,
            _resolved_tags(cfg.alliance_tags, unresolved),
            latest.snapshot_date,
            color=cfg.report_embed_color,
            sections=DAILY_SECTIONS,
            region_limit=8,
        )
        return _ReportPhase(action="send", embeds=embeds, snapshot_date=latest.snapshot_date)
    except Exception as exc:  # noqa: BLE001 — plan: job failures are logged to job_log, never crash the loop
        _record_failure("report", exc, conn)
        return _ReportPhase(action="failed", embeds=[])
    finally:
        if conn is not None:
            conn.close()


async def run_report(channel_id: int, require_today: bool = True) -> RunReportStatus:
    """Send the daily report (up to 5 embeds in one message) to ``channel_id``.

    Order of checks: (1) ``load_latest``; (2) no snapshot at all → "no data
    yet" embed; (3) ``require_today`` and the latest snapshot is not today
    (``FETCH_TZ``) → log + return without sending; (4) otherwise build the
    report from the latest + previous snapshots (deltas across day gaps are
    computed, not None — a gap logs an info entry) and send. The report is
    always built from the RESOLVED alliance subset — an empty subset logs a
    warning and skips. The read+compute phase runs in a worker thread via
    ``asyncio.to_thread``; ``channel.send`` stays on the bot loop. Exceptions
    are logged via ``append_log('report', 'error', ...)`` and never crash the
    loop.

    Returns a short status string (task 12, decision (a)): ``sent``,
    ``skipped (already running)``, ``no snapshot for today``, ``no data yet``
    (the no-data placeholder embed WAS sent), ``no alliance``,
    ``channel not found`` or ``failed``.
    """
    if not await _acquire("report"):
        return REPORT_STATUS_SKIPPED
    try:
        phase = await asyncio.to_thread(_report_phase, require_today)
        match phase.action:
            case "stale":
                return REPORT_STATUS_NO_SNAPSHOT_TODAY
            case "no_alliance":
                return REPORT_STATUS_NO_ALLIANCE
            case "failed":
                return REPORT_STATUS_FAILED
            case "send":
                message = f"report sent to channel {channel_id} (snapshot {phase.snapshot_date})"
            case "no_data":
                message = f"no snapshots yet, sent no-data embed to channel {channel_id}"
        channel = await _get_channel_on_loop(channel_id)
        if channel is None:
            return REPORT_STATUS_CHANNEL_NOT_FOUND
        embeds = phase.embeds
        assert embeds  # send/no_data always carry the embeds (see _ReportPhase)
        _ = await channel.send(embeds=embeds)
        await asyncio.to_thread(_log_entry, "report", "info", message)
    except Exception as exc:  # noqa: BLE001 — plan: job failures are logged to job_log, never crash the loop
        await asyncio.to_thread(_record_failure_blocking, "report", exc)
        return REPORT_STATUS_FAILED
    finally:
        _release()
    return REPORT_STATUS_SENT if phase.action == "send" else REPORT_STATUS_NO_DATA


async def _get_channel_on_loop(channel_id: int) -> Messageable | None:
    """Resolve the channel on the bot loop (discord.py is not thread-safe).

    ``current_bot``/``get_channel`` are loop-owned state, so this runs on the
    loop; the error logs (sqlite) go through ``_log_entry`` in a worker
    thread.
    """
    bot = current_bot
    if bot is None:
        await asyncio.to_thread(_log_entry, "report", "error", "bot not initialized")
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        await asyncio.to_thread(_log_entry, "report", "error", f"channel {channel_id} not found")
        return None
    return cast(Messageable, channel)


def _previous_date(conn: sqlite3.Connection, latest: str) -> str | None:
    """The most recent snapshot date strictly older than ``latest``, or None."""
    return max((d for d in store.list_dates(conn) if d < latest), default=None)


def _resolved_tags(tags: list[str], unresolved: list[str]) -> list[str]:
    """The configured tags that actually resolved (embed description input)."""
    normalized = [tag.strip() for tag in tags if tag.strip()]
    return [tag for tag in normalized if tag not in unresolved]


def _section_embeds(sections: AbstractSet[str], region_limit: int | None) -> list[discord.Embed]:
    """Build one section's embeds from the latest snapshot pair (worker thread).

    Own connection (never shared with the loop thread) → load latest +
    previous → resolve ids → ``_build_report_data`` → ``build_report_embed``
    with ``sections``/``region_limit``. Returns ``[]`` when there is nothing
    to show (no snapshot / no alliance / the section is empty) or on failure
    (logged via ``_record_failure`` — the caller shows the no-content
    string).
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = store.connect(_sqlite_path(os.environ))
        cfg = load_merged_config(conn, os.environ)
        latest = store.load_latest(conn)
        if latest is None:
            return []
        previous = _previous_date(conn, latest.snapshot_date)
        curr_rows = store.load_villages(conn, latest.snapshot_date)
        prev_rows = store.load_villages(conn, previous) if previous is not None else None
        resolved, unresolved = resolve_alliance_ids(curr_rows, cfg.alliance_tags, conn)
        if not resolved:
            return []
        data = _build_report_data(cfg, latest.snapshot_date, curr_rows, prev_rows, resolved)
        return build_report_embed(
            data,
            _resolved_tags(cfg.alliance_tags, unresolved),
            latest.snapshot_date,
            color=cfg.report_embed_color,
            sections=sections,
            region_limit=region_limit,
        )
    except Exception as exc:  # noqa: BLE001 — plan: job failures are logged to job_log, never crash the loop
        _record_failure("report", exc, conn)
        return []
    finally:
        if conn is not None:
            conn.close()


async def run_villages() -> list[discord.Embed]:
    """Full village events for the latest day — the /wioski runner.

    The sync work runs in a worker thread (``_section_embeds`` opens its own
    connection); the coroutine is returned for the command to await. The
    command MUST NOT wrap this in ``asyncio.to_thread`` again — an async
    callable passed to ``to_thread`` produces a discarded coroutine and no
    embeds.
    """
    return await asyncio.to_thread(_section_embeds, {"villages"}, None)


async def run_regions() -> list[discord.Embed]:
    """Full regions table with Δ % — the /regiony runner (see ``run_villages``)."""
    return await asyncio.to_thread(_section_embeds, {"regions"}, None)


def _build_report_data(
    cfg: MergedConfig,
    snapshot_date: str,
    curr_rows: list[VillageRow],
    prev_rows: list[VillageRow] | None,
    alliance_ids: set[int],
) -> ReportData:
    summary = compute_deltas(prev_rows, curr_rows, alliance_ids)
    gained, lost = village_events(prev_rows, curr_rows, alliance_ids)
    return ReportData(
        snapshot_date=snapshot_date,
        server=SERVER,
        alliance_tags=cfg.alliance_tags,
        summary=summary,
        standings=alliance_standings(prev_rows, curr_rows, cfg.tracked_alliances),
        new_villages=gained,
        lost_villages=lost,
        regions=region_stats(prev_rows, curr_rows, alliance_ids),
    )


# --- scheduler jobs ----------------------------------------------------------------


async def job_fetch() -> None:
    """APScheduler job: daily snapshot fetch (thin wrapper over ``run_fetch``)."""
    _ = await run_fetch()


async def job_report() -> None:
    """APScheduler job: daily report (pre-checked wrapper over ``run_report``).

    Reads the merged config fresh (settings may have changed via the
    dashboard) and pre-checks the resolved alliance subset BEFORE calling
    ``run_report``: missing channel, empty ``ALLIANCE_TAGS``, no snapshot
    yet, or no resolvable tag → ``append_log`` warning + skip without the
    call. ``run_report``'s own no-data path stays for ``/raport``.
    """
    cfg = await asyncio.to_thread(_current_config)
    if cfg.channel_id is None:
        await asyncio.to_thread(_log_entry, "report", "warning", "CHANNEL_ID not set (env or settings), skipping job")
        return
    if not cfg.alliance_tags:
        await asyncio.to_thread(_log_entry, "report", "warning", "no alliance configured, skipping daily report")
        return
    if not await asyncio.to_thread(_job_report_precheck, cfg):
        return
    _ = await run_report(cfg.channel_id, require_today=True)


def _job_report_precheck(cfg: MergedConfig) -> bool:
    """Resolve the configured tags against the latest snapshot, off the loop.

    Worker thread (``job_report`` calls it via ``asyncio.to_thread``): opens
    its own connection, logs a warning and returns False when no snapshot
    exists yet or no configured tag resolves — the daily job then skips
    WITHOUT calling ``run_report``.
    """
    conn = store.connect(_sqlite_path(os.environ))
    try:
        latest = store.load_latest(conn)
        if latest is None:
            store.append_log(conn, "report", "warning", "no snapshot yet, skipping daily report")
            return False
        curr_rows = store.load_villages(conn, latest.snapshot_date)
        resolved, _ = resolve_alliance_ids(curr_rows, cfg.alliance_tags, conn)
        if not resolved:
            store.append_log(conn, "report", "warning", "unresolved alliance tags, skipping daily report")
            return False
        return True
    finally:
        conn.close()


# --- bot class ----------------------------------------------------------------------


class _SchedulerJob(Protocol):
    """Minimal job surface the scheduler exposes (used by tests)."""

    id: str
    func: Callable[..., object]
    trigger: CronTrigger


class _Scheduler(Protocol):
    """The scheduler surface the bot uses.

    apscheduler 3.x ships no type stubs, so the ``AsyncIOScheduler`` instance
    is cast to this protocol at the single boundary in ``_start_scheduler`` —
    the same cast-at-one-boundary pattern as asyncpg in ``backfill.py``.
    """

    def add_job(self, func: Callable[..., object], trigger: CronTrigger, *, id: str) -> None: ...

    def start(self) -> None: ...

    def shutdown(self, *, wait: bool = True) -> None: ...

    def get_jobs(self) -> list[_SchedulerJob]: ...


class TravianBot(discord.Client):
    """The discord client: owns the command tree and the job scheduler."""

    def __init__(self, cfg: MergedConfig) -> None:
        super().__init__(intents=discord.Intents.default())
        self.cfg: MergedConfig = cfg
        self.tree: app_commands.CommandTree[TravianBot] = app_commands.CommandTree(self)
        self.scheduler: _Scheduler | None = None
        # Command registration (T11 + report trim): /raport closes over
        # run_report, /wioski + /regiony over the section runners; config is
        # re-read per invocation, so dashboard changes apply immediately.
        # on_ready's tree.sync() picks the commands up.
        register_commands(self.tree, run_report, _current_config, run_villages, run_regions)

    async def on_ready(self) -> None:
        global bot_loop
        bot_loop = asyncio.get_running_loop()
        logger.info("logged in as %s", self.user)
        _ = await self.tree.sync()
        logger.info("synced %d application commands", len(self.tree.get_commands()))
        if self.scheduler is None:
            self._start_scheduler()

    def _start_scheduler(self) -> None:
        """Create and start the AsyncIOScheduler with the fetch/report jobs.

        Must run on the bot's loop (it is — called from ``on_ready``). Each
        trigger carries its own timezone; the scheduler default timezone is
        the fetch zone.
        """
        # cast via object: apscheduler is untyped, so direct AsyncIOScheduler ->
        # _Scheduler has no structural overlap; object is the sanctioned bridge.
        scheduler = cast(_Scheduler, cast(object, AsyncIOScheduler(timezone=ZoneInfo(self.cfg.fetch_tz))))
        scheduler.add_job(
            job_fetch,
            CronTrigger(
                hour=self.cfg.fetch_hour,
                minute=self.cfg.fetch_minute,
                timezone=ZoneInfo(self.cfg.fetch_tz),
            ),
            id="job_fetch",
        )
        scheduler.add_job(
            job_report,
            CronTrigger(
                hour=self.cfg.report_hour,
                minute=self.cfg.report_minute,
                timezone=ZoneInfo(self.cfg.report_tz),
            ),
            id="job_report",
        )
        scheduler.start()
        logger.info(
            "scheduler started: job_fetch %02d:%02d %s, job_report %02d:%02d %s",
            self.cfg.fetch_hour,
            self.cfg.fetch_minute,
            self.cfg.fetch_tz,
            self.cfg.report_hour,
            self.cfg.report_minute,
            self.cfg.report_tz,
        )
        self.scheduler = scheduler


# --- dashboard bootstrap (task 12) ------------------------------------------------


class _DashboardThread(threading.Thread):
    """Daemon thread running the dashboard's uvicorn server.

    Exposes ``server`` so tests (and shutdown paths) can stop it via
    ``server.should_exit = True``.
    """

    def __init__(self, server: uvicorn.Server) -> None:
        super().__init__(target=server.run, name="dashboard-uvicorn", daemon=True)
        self.server: uvicorn.Server = server


def _dashboard_app_factory(env: Mapping[str, str]) -> Callable[[], FastAPI]:
    """Factory wiring the REAL functions into the dashboard app.

    ``get_config``/``get_status`` reuse ``_current_config`` (the shared config
    getter of the run functions — it reads ``os.environ``, which IS ``env`` in
    production); ``bot_loop_getter`` reads the module global set by
    ``on_ready`` — actions before login → 409 "bot not ready".
    """

    def factory() -> FastAPI:
        return create_app(
            DashboardDeps(
                get_status=make_status_provider(_sqlite_path(env), _current_config),
                run_fetch_fn=run_fetch,
                run_report_fn=run_report,
                bot_loop_getter=lambda: bot_loop,
                get_config=_current_config,
                env=env,
            )
        )

    return factory


def start_dashboard(app_factory: Callable[[], FastAPI], env: Mapping[str, str]) -> threading.Thread:
    """Start the dashboard (FastAPI app from ``app_factory``) in a daemon thread.

    The thread runs ``uvicorn.Server`` in factory mode: the app is created
    lazily at serve start inside the thread, so a factory failure kills only
    the dashboard, never the bot loop. The thread is a daemon — process exit
    stops it. Bind/port come from ``DASHBOARD_BIND`` (default 127.0.0.1) and
    ``DASHBOARD_PORT`` (default 8090); an unparseable port exits 1.
    """
    host = env.get("DASHBOARD_BIND", "127.0.0.1")
    port_raw = env.get("DASHBOARD_PORT", "8090")
    try:
        port = int(port_raw)
    except ValueError:
        logger.error("invalid DASHBOARD_PORT %r", port_raw)
        raise SystemExit(1) from None
    config = uvicorn.Config(app=app_factory, factory=True, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = _DashboardThread(server)
    thread.start()
    logger.info("dashboard starting on %s:%d", host, port)
    return thread


# --- entry point ----------------------------------------------------------------------


def main() -> None:
    """Entry point: schema → merged config → validation → dashboard → bot loop.

    Configures the root logger at INFO (module loggers inherit). Exits 1 with
    a readable message on any startup validation failure (token, channel,
    timezone, hour/minute range, unparseable settings).
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("starting travian report bot")
    env = os.environ
    conn = store.connect(_sqlite_path(env))
    try:
        store.init_schema(conn)
        try:
            cfg = load_merged_config(conn, env)
        except (TypeError, ValueError) as exc:
            logger.error("invalid configuration: %s", exc)
            raise SystemExit(1) from exc
    finally:
        conn.close()
    validate_config(cfg)

    global current_bot
    current_bot = TravianBot(cfg)
    # T12 dashboard bootstrap: uvicorn in a daemon thread (factory mode — the
    # app is created at serve start inside the thread). POST /api/actions/*
    # dispatches run_fetch/run_report onto this loop via
    # asyncio.run_coroutine_threadsafe(coro, bot_loop) — bot_loop is set by
    # on_ready once the loop is running; actions before that → 409.
    _ = start_dashboard(_dashboard_app_factory(env), env)
    current_bot.run(cfg.discord_token)


if __name__ == "__main__":
    main()
