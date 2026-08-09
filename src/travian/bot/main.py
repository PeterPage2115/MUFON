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
  sending (``job_report`` keeps its own pre-check in task 10).
- **Shared run lock**: ``run_fetch``/``run_report`` are guarded by one
  ``asyncio.Lock`` (module state). A call while another run is in progress is
  SKIPPED and logged — never queued. The lock is bound per event loop
  (``_get_run_lock``): production runs everything on the bot's single loop
  (one lock), while tests drive the functions across separate
  ``asyncio.run`` invocations and must not share lock state.
- **``bot_loop`` module state**: set at runtime by ``on_ready``; task 12's
  dashboard dispatches ``run_fetch``/``run_report`` onto this loop with
  ``asyncio.run_coroutine_threadsafe(coro, bot_loop)``.
- **T9/T10 split**: T9 implements both run functions fully, but WITHOUT the
  empty-parse guard and WITHOUT ``asyncio.to_thread`` wrapping of blocking
  calls (fetch, sqlite) — those land in T10; the blocking calls are already
  isolated in the obvious places so T10's change is a small edit.
- **Logging**: ``main()`` configures the root logger at INFO
  (``logging.basicConfig``); module loggers inherit. All job errors are also
  recorded in the ``job_log`` table via ``append_log`` (job names
  ``fetch``/``report``/``config``).
- **append_log persistence**: ``store.append_log`` does NOT commit (Python
  sqlite3 legacy isolation — an uncommitted INSERT is rolled back when the
  connection closes), so every run commits its ``job_log`` rows before the
  per-operation connection closes.

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from apscheduler.schedulers.asyncio import (  # pyright: ignore[reportMissingTypeStubs]  # apscheduler 3.x ships no stubs
    AsyncIOScheduler,
)
from apscheduler.triggers.cron import CronTrigger  # pyright: ignore[reportMissingTypeStubs]
from discord import app_commands
from discord.abc import Messageable

from travian import store
from travian.bot.report_embed import build_report_embed
from travian.map_sql import fetch_map_sql, parse_map_sql
from travian.metrics import (
    compute_deltas,
    region_stats,
    resolve_alliance_ids,
    top_players,
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
        alliance_tags=_as_tags(_pick(env, db, "ALLIANCE_TAGS")),
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
    """Settings-table value for ``key``, falling back to env, then ``default``."""
    if key in db:
        return db[key]
    if key in env:
        return env[key]
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
        return raw
    if isinstance(raw, str):
        try:
            return int(raw, 0)  # accepts both "0x2ECC71" and decimal
        except ValueError:
            raise ValueError(f"setting {key}: expected an integer color, got {raw!r}") from None
    raise ValueError(f"setting {key}: expected an integer color, got {raw!r}")


def _as_tags(raw: object) -> list[str]:
    """Coerce ALLIANCE_TAGS: env comma-separated string or settings list[str].

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
            raise TypeError("setting ALLIANCE_TAGS: list values must be strings")
        parts = cast(list[str], raw_items)
    else:
        raise TypeError(f"setting ALLIANCE_TAGS: expected comma-separated string or list, got {raw!r}")
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
        conn.commit()  # append_log does not commit — legacy sqlite3 rolls back on close
    finally:
        conn.close()


def _record_failure(job: str, exc: Exception, conn: sqlite3.Connection) -> None:
    """Log a job failure (logger + job_log). Call only from inside the except
    block so ``logger.exception`` picks up the active exception."""
    logger.exception("job %s failed: %s", job, exc)
    try:
        store.append_log(conn, job, "error", str(exc))
    except sqlite3.Error:
        logger.exception("could not append job_log entry for %s", job)


# --- shared run functions (jobs, dashboard, /raport) ------------------------------


async def run_fetch() -> None:
    """Fetch → parse → save today's map.sql snapshot (in ``FETCH_TZ``).

    Any failure is logged via ``append_log('fetch', 'error', ...)`` and never
    crashes the loop. NOTE (task 10 split): the empty-parse guard and
    ``asyncio.to_thread`` wrapping of the blocking fetch/sqlite calls land in
    task 10; here they are direct.
    """
    if not await _acquire("fetch"):
        return
    conn: sqlite3.Connection | None = None
    try:
        conn = store.connect(_sqlite_path(os.environ))
        try:
            cfg = load_merged_config(conn, os.environ)
            text = fetch_map_sql(MAP_SQL_URL)
            rows = parse_map_sql(text)
            date = datetime.now(ZoneInfo(cfg.fetch_tz)).date().isoformat()
            store.save_snapshot(conn, date, rows)
            store.append_log(conn, "fetch", "info", f"snapshot saved for {date} ({len(rows)} villages)")
        except Exception as exc:  # noqa: BLE001 — plan: job failures are logged to job_log, never crash the loop
            _record_failure("fetch", exc, conn)
    finally:
        if conn is not None:
            conn.commit()  # persist append_log rows — legacy sqlite3 rolls back on close
            conn.close()
        _release()


async def run_report(channel_id: int, require_today: bool = True) -> None:
    """Send the daily report embed to ``channel_id``.

    Order of checks: (1) ``load_latest``; (2) no snapshot at all → "no data
    yet" embed; (3) ``require_today`` and the latest snapshot is not today
    (``FETCH_TZ``) → log + return without sending; (4) otherwise build the
    report from the latest + previous snapshots (deltas across day gaps are
    computed, not None) and send. The report is always built from the
    RESOLVED alliance subset — an empty subset logs a warning and skips.
    Exceptions are logged via ``append_log('report', 'error', ...)`` and
    never crash the loop.
    """
    if not await _acquire("report"):
        return
    conn: sqlite3.Connection | None = None
    try:
        conn = store.connect(_sqlite_path(os.environ))
        try:
            cfg = load_merged_config(conn, os.environ)
            latest = store.load_latest(conn)
            if latest is None:
                await _send_no_data(conn, cfg, channel_id)
                return
            expected = datetime.now(ZoneInfo(cfg.fetch_tz)).date().isoformat()
            if require_today and latest.snapshot_date != expected:
                store.append_log(conn, "report", "warning", "no snapshot for today, skipping")
                return
            previous = _previous_date(conn, latest.snapshot_date)
            curr_rows = store.load_villages(conn, latest.snapshot_date)
            prev_rows = store.load_villages(conn, previous) if previous is not None else None
            resolved, unresolved = resolve_alliance_ids(curr_rows, cfg.alliance_tags, conn)
            if not resolved:
                store.append_log(conn, "report", "warning", "no alliance configured, skipping report")
                return
            data = _build_report_data(cfg, latest.snapshot_date, curr_rows, prev_rows, resolved)
            embed = build_report_embed(
                data,
                _resolved_tags(cfg.alliance_tags, unresolved),
                latest.snapshot_date,
                color=cfg.report_embed_color,
            )
            channel = _get_channel_or_log(conn, channel_id)
            if channel is None:
                return
            _ = await channel.send(embed=embed)
            store.append_log(
                conn, "report", "info", f"report sent to channel {channel_id} (snapshot {latest.snapshot_date})"
            )
        except Exception as exc:  # noqa: BLE001 — plan: job failures are logged to job_log, never crash the loop
            _record_failure("report", exc, conn)
    finally:
        if conn is not None:
            conn.commit()  # persist append_log rows — legacy sqlite3 rolls back on close
            conn.close()
        _release()


def _previous_date(conn: sqlite3.Connection, latest: str) -> str | None:
    """The most recent snapshot date strictly older than ``latest``, or None."""
    return max((d for d in store.list_dates(conn) if d < latest), default=None)


def _resolved_tags(tags: list[str], unresolved: list[str]) -> list[str]:
    """The configured tags that actually resolved (embed description input)."""
    normalized = [tag.strip() for tag in tags if tag.strip()]
    return [tag for tag in normalized if tag not in unresolved]


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
        new_villages=gained,
        lost_villages=lost,
        top_players=top_players(curr_rows, prev_rows, alliance_ids),
        regions=region_stats(prev_rows, curr_rows, alliance_ids),
        vp_total=summary.vp,
        vp_delta=summary.vp_delta,
    )


def _get_channel_or_log(conn: sqlite3.Connection, channel_id: int) -> Messageable | None:
    bot = current_bot
    if bot is None:
        store.append_log(conn, "report", "error", "bot not initialized")
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        store.append_log(conn, "report", "error", f"channel {channel_id} not found")
        return None
    return cast(Messageable, channel)


async def _send_no_data(conn: sqlite3.Connection, cfg: MergedConfig, channel_id: int) -> None:
    channel = _get_channel_or_log(conn, channel_id)
    if channel is None:
        return
    embed = discord.Embed(description=NO_DATA_YET, color=cfg.report_embed_color)
    _ = await channel.send(embed=embed)
    store.append_log(conn, "report", "info", f"no snapshots yet, sent no-data embed to channel {channel_id}")


# --- scheduler jobs ----------------------------------------------------------------


async def job_fetch() -> None:
    """APScheduler job: daily snapshot fetch (thin wrapper over ``run_fetch``)."""
    await run_fetch()


async def job_report() -> None:
    """APScheduler job: daily report (thin wrapper over ``run_report``).

    Reads the merged config fresh (settings may have changed via the
    dashboard) and resolves the target channel; task 10 adds the resolved-
    subset pre-check here.
    """
    cfg = _current_config()
    if cfg.channel_id is None:
        _log_entry("report", "warning", "CHANNEL_ID not set (env or settings), skipping job")
        return
    await run_report(cfg.channel_id, require_today=True)


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


# --- entry point ----------------------------------------------------------------------


def main() -> None:
    """Entry point: schema → merged config → validation → bot loop.

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
    # T12 dashboard bootstrap plugs in here: uvicorn in a background thread
    # (with lifespan) serving the FastAPI app; POST /api/actions/* dispatches
    # run_fetch/run_report onto this loop via
    # asyncio.run_coroutine_threadsafe(coro, bot_loop) — bot_loop is set by
    # on_ready once the loop is running.
    current_bot.run(cfg.discord_token)


if __name__ == "__main__":
    main()
