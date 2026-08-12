"""Dashboard API (task 12): FastAPI app mounted in the bot process.

The app is created by :func:`create_app` from an injected :class:`DashboardDeps`
— the same injection pattern as commands.py, which keeps the module graph
single-direction (``bot/main`` → ``dashboard/app``): this module never imports
``bot/main.py``, so mounting the app from ``main()`` cannot create an import
cycle. The real wiring lives in ``bot.main._dashboard_app_factory``; tests
inject fakes.

Endpoints:

- ``GET /`` — the static UI (``static/index.html``, added by task 13; until
  then the route 404s instead of crashing).
- ``GET /api/status`` — latest snapshot (date/source), village / player /
  alliance counts and total population aggregated from the latest snapshot's
  villages, the next fetch/report occurrences (``FETCH_*``/``REPORT_*`` merged
  settings), the newest 5 ``job_log`` ERROR entries, merged ``ALLIANCE_TAGS``.
- ``GET /api/settings`` / ``PUT /api/settings`` — the merged settings. PUT
  validates EVERY key before writing (one atomic ``store.set_settings`` call,
  so a bad key never leaves a partial write) and rejects unknown keys — any
  secret (``DISCORD_TOKEN``, ``BACKFILL_DSN``), ``SQLITE_PATH``, ``DASHBOARD_*``
  etc. — with 422 naming the key. The empty ``ALLIANCE_TAGS`` state is
  env-only: clearing it via the dashboard → 422.
- ``POST /api/actions/fetch`` / ``POST /api/actions/report`` — dispatch
  ``run_fetch``/``run_report`` onto the bot loop via
  ``asyncio.run_coroutine_threadsafe`` (the loop may be None before
  ``on_ready`` → 409 "bot not ready"). The action's outcome — a short status
  string returned by the run functions (decision (a), see ``bot.main``) — is
  returned in the response body for the UI toast. ``report`` uses the merged
  ``CHANNEL_ID`` (missing → 409) and ``require_today=False`` like ``/raport``.
  ``wait_for`` uses ``asyncio.shield`` so a timeout (504) never cancels the
  running job — the lock keeps a concurrent action skipping.
- ``GET /api/logs?n=50`` — newest-first ``job_log`` rows (n clamped 1..500).
- ``GET /api/analysis/regions?days=30`` — region share series over the
  window plus the latest-pair control table (``current`` rows carry the
  server-computed ``active``/``controlled``/``to50_needed`` fields).
- ``GET /api/analysis/standings?days=30`` — population/VP series per
  TRACKED_ALLIANCES alliance (rows carry ``ours`` for the UI highlight).
- ``GET /api/analysis/dates`` — all snapshot dates ascending (Events tab
  selectors).
- ``GET /api/analysis/events?from=&to=`` — gained/lost village events
  between two dates (missing sides default to the latest pair; ``from >= to``
  or an unknown date → 422 listing the valid dates).
- ``GET /api/analysis/deltas?days=30`` — headline history with day-over-day
  deltas (``None`` on the oldest date).
- ``GET /api/analysis/regions|events|deltas?alliance=<tag>`` — per-alliance
  filtering (``combined``/absent = union of ``ALLIANCE_TAGS``; unknown tag →
  422 listing the valid tags).
- ``GET /api/auth/status`` — public: ``{"method": ..., "user": ...}`` for the
  auth-aware UI.
- ``GET /api/auth/login`` — public: 302 to Discord's authorization endpoint
  (oauth mode; 409 otherwise).
- ``GET /api/auth/callback`` — public: Discord redirect; always 302, either
  to ``/?session=<token>`` or ``/?auth_error=<reason>``.
- ``POST /api/auth/logout`` — public: invalidates the Bearer session (204).

Auth: one of three modes, computed once at app creation from env
(``_auth_method``): ``token`` (``Authorization: Bearer <DASHBOARD_TOKEN>``,
compared constant-time; token possession = admin), ``oauth`` (Discord OAuth
sessions from ``auth.SessionStore``; RBAC: members read-only, admins full —
``GET/PUT /api/settings`` and ``POST /api/actions/*`` return 403 for
members) and ``none`` (everything open, loopback use only). Admin actions
are rate-limited per user (``ActionLimiter``, 6/60 s → 429 + ``Retry-After``;
key = session token in oauth mode, client IP in token mode). The static UI
(``/``, ``/static/*``), ``/healthz`` and the whole ``/api/auth/*`` surface
are always public — the browser loads the page and logs in without
credentials, then authenticates every other API call. OAuth sessions are
in-memory (restart logs everyone out) and the redirect state tokens live
10 minutes.

- ``GET /healthz`` — always ``{"status": "ok"}``, never token-protected:
  the container HEALTHCHECK probe (the app only starts serving after
  ``main()`` passed startup validation, so a 200 implies a healthy process).

Sqlite: every operation opens its own connection via ``store.connect``
(``check_same_thread=False``, one connection per op, never shared) and closes
it afterwards — safe under WAL while the bot loop's jobs write concurrently;
``bot.main()`` runs ``init_schema`` before this app is mounted, so the schema
always exists.

allow: SIZE_OK — the plan pins ``src/travian/dashboard/app.py`` as the single
dashboard module (the UI lives in ``static/``, task 13); factory, validation
and endpoints share this file by plan contract — same rationale as the
``metrics.py``/``store.py`` markers.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Final, Protocol, TypedDict, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from travian import analysis, store
from travian.dashboard import auth
from travian.metrics import region_stats, village_events
from travian.models import RegionDay, VillageEvent

logger = logging.getLogger(__name__)

#: Static UI directory — populated by task 13 (index.html/style.css/app.js).
STATIC_DIR: Final = Path(__file__).resolve().parent / "static"

#: Settings keys the dashboard may write; everything else (DISCORD_TOKEN,
#: BACKFILL_DSN, SQLITE_PATH, DASHBOARD_* ...) → 422. Secrets never enter
#: the settings table.
ALLOWED_SETTINGS_KEYS: Final = frozenset(
    {
        "ALLIANCE_TAGS",
        "TRACKED_ALLIANCES",
        "CHANNEL_ID",
        "FETCH_HOUR",
        "FETCH_MINUTE",
        "FETCH_TZ",
        "REPORT_HOUR",
        "REPORT_MINUTE",
        "REPORT_TZ",
        "ADMIN_ROLE_ID",
        "REPORT_EMBED_COLOR",
    }
)

_ACTION_TIMEOUT: Final = 300.0  # fetch worst case ≈ 190 s with retries
_DEFAULT_SQLITE_PATH: Final = "/data/travian.db"  # shared default (bot/main.py)
_MAX_LOG_WINDOW: Final = 500


class StatusData(TypedDict):
    """The ``GET /api/status`` payload (see module docstring)."""

    snapshot_date: str | None
    snapshot_source: str | None
    villages: int
    players: int
    alliances: int
    total_population: int
    fetch_hour: int
    fetch_minute: int
    fetch_tz: str
    report_hour: int
    report_minute: int
    report_tz: str
    next_fetch: str
    next_report: str
    errors: list[dict[str, str]]
    alliance_tags: list[str]


class SettingsPayload(TypedDict):
    """The merged settings as the API exposes them (env + DB overrides)."""

    ALLIANCE_TAGS: list[str]
    TRACKED_ALLIANCES: list[str]
    CHANNEL_ID: int | None
    FETCH_HOUR: int
    FETCH_MINUTE: int
    FETCH_TZ: str
    REPORT_HOUR: int
    REPORT_MINUTE: int
    REPORT_TZ: str
    ADMIN_ROLE_ID: int | None
    REPORT_EMBED_COLOR: int


class RunFetchFn(Protocol):
    """The ``run_fetch`` surface — injected by main.py, returns a status string."""

    async def __call__(self) -> str: ...


class RunReportFn(Protocol):
    """The ``run_report`` surface — injected by main.py, returns a status string."""

    async def __call__(self, channel_id: int, require_today: bool = True) -> str: ...


class ConfigProtocol(Protocol):
    """The merged-config surface the dashboard reads — satisfied by main's
    ``MergedConfig``.

    Read-only ``@property`` members mirroring ``MergedConfig``'s frozen
    attributes (a writable protocol attribute fails the structural check
    against a frozen dataclass — the commands.py lesson); typing the getter
    against this protocol instead of importing the class keeps the module
    graph single-direction.
    """

    @property
    def channel_id(self) -> int | None: ...

    @property
    def alliance_tags(self) -> list[str]: ...

    @property
    def tracked_alliances(self) -> list[str]: ...

    @property
    def fetch_hour(self) -> int: ...

    @property
    def fetch_minute(self) -> int: ...

    @property
    def fetch_tz(self) -> str: ...

    @property
    def report_hour(self) -> int: ...

    @property
    def report_minute(self) -> int: ...

    @property
    def report_tz(self) -> str: ...

    @property
    def admin_role_id(self) -> int | None: ...

    @property
    def report_embed_color(self) -> int: ...


ConfigGetter = Callable[[], ConfigProtocol]


@dataclass(frozen=True)
class DashboardDeps:
    """Injected dependencies for :func:`create_app` (see module docstring).

    - ``get_status`` — builds the ``/api/status`` payload (real wiring:
      :func:`make_status_provider`).
    - ``run_fetch_fn``/``run_report_fn`` — the shared run functions (real:
      ``bot.main.run_fetch``/``run_report``), dispatched onto the bot loop.
    - ``bot_loop_getter`` — returns the bot's event loop (``bot.main.bot_loop``),
      None until ``on_ready`` has run.
    - ``get_config`` — the merged config getter (real: ``bot.main._current_config``).
    - ``env`` — the process environment (``DASHBOARD_*``, ``SQLITE_PATH``).
    """

    get_status: Callable[[], StatusData]
    run_fetch_fn: RunFetchFn
    run_report_fn: RunReportFn
    bot_loop_getter: Callable[[], asyncio.AbstractEventLoop | None]
    get_config: ConfigGetter
    env: Mapping[str, str]


# --- helpers ------------------------------------------------------------------


def _db_path(env: Mapping[str, str]) -> str:
    return env.get("SQLITE_PATH", _DEFAULT_SQLITE_PATH)


def _is_loopback(bind: str) -> bool:
    return bind in ("127.0.0.1", "localhost", "::1")


def _auth_required(env: Mapping[str, str]) -> bool:
    """True when every route must require ``Authorization: Bearer DASHBOARD_TOKEN``.

    Active only when the bind address is NOT loopback AND
    ``DASHBOARD_LOOPBACK_ONLY != "true"`` (compose sets it to "true" because
    the host publishes loopback-only). Computed once at app creation — env is
    static for the process.
    """
    return not _is_loopback(env.get("DASHBOARD_BIND", "127.0.0.1")) and env.get("DASHBOARD_LOOPBACK_ONLY") != "true"


def _auth_method(env: Mapping[str, str]) -> str:
    """Resolve the dashboard auth mode from env, once at app creation.

    - ``DASHBOARD_AUTH_MODE=none`` → "none".
    - ``oauth`` complete (all ``OAUTH_*`` set) → "oauth"; a missing key →
      warning + fallback to "token" (safe default, never silent openness).
    - anything else (unset or ``token``) → the legacy heuristic: "token"
      when the dashboard is reachable beyond loopback, else "none".
    """
    mode = env.get("DASHBOARD_AUTH_MODE", "").strip().lower()
    if mode == "none":
        return "none"
    if mode == "oauth":
        if env.get("OAUTH_CLIENT_ID") and env.get("OAUTH_CLIENT_SECRET") and env.get("OAUTH_GUILD_ID"):
            return "oauth"
        logger.warning(
            "DASHBOARD_AUTH_MODE=oauth requires OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET and OAUTH_GUILD_ID — falling back to token mode"
        )
        return "token"
    return "token" if _auth_required(env) else "none"


#: OAuth login state tokens (state → expiry, UTC). Module-level so every app
#: instance of the single dashboard process shares them; entries live 10
#: minutes and are pruned lazily on write.
_oauth_states: dict[str, datetime] = {}
_oauth_states_lock = threading.Lock()


def _store_oauth_state(state: str, expires: datetime) -> None:
    with _oauth_states_lock:
        now = datetime.now(UTC)
        for stale in [token for token, exp in _oauth_states.items() if exp <= now]:
            del _oauth_states[stale]
        _oauth_states[state] = expires


def _consume_oauth_state(state: str) -> bool:
    """True (and consumed) when ``state`` is known and unexpired."""
    with _oauth_states_lock:
        entry = _oauth_states.get(state)
        if entry is None:
            return False
        del _oauth_states[state]
        return entry > datetime.now(UTC)


def _next_occurrence(hour: int, minute: int, tz_name: str) -> str:
    """The next wall-clock ``hour:minute`` in ``tz_name`` as an ISO string.

    zoneinfo computes the UTC offset from the datetime's own fields (PEP 495),
    so the day-rollover result is DST-correct.
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    candidate = datetime(now.year, now.month, now.day, hour, minute, tzinfo=tz)
    if candidate <= now:
        candidate = datetime(now.year, now.month, now.day, hour, minute, tzinfo=tz) + timedelta(days=1)
    return candidate.isoformat()


def _recent_errors(conn: sqlite3.Connection, n: int = 5) -> list[dict[str, str]]:
    """The ``n`` newest ``job_log`` ERROR entries.

    DECISION: a 50-entry window is scanned (newest-first) and filtered — this
    yields the last ``n`` errors even when non-error entries outnumber them
    (a bare ``recent_logs(conn, n)`` filter would return fewer).
    """
    return [entry for entry in store.recent_logs(conn, 50) if entry["level"] == "error"][:n]


def _resolve_ids(conn: sqlite3.Connection, date: str, tags: list[str]) -> set[int]:
    """Resolve ``tags`` to alliance ids against ``date`` (unresolved tags skipped).

    Shares the metrics resolution semantics (tag → union of matching ids),
    backed by ``store.alliance_ids_by_tag``.
    """
    by_tag = store.alliance_ids_by_tag(conn, date)
    ids: set[int] = set()
    for tag in tags:
        ids.update(by_tag.get(tag, []))
    return ids


def _resolve_alliance_ids(
    conn: sqlite3.Connection, date: str, cfg: ConfigProtocol, alliance: str | None
) -> set[int]:
    """Resolve the analysis ``alliance`` filter to ids against ``date``.

    ``None``/``"combined"`` → the union of ``cfg.alliance_tags`` (the
    historical behavior); a configured tag → that tag's ids only; anything
    else → 422 naming the valid values.
    """
    if alliance is None or alliance == "combined":
        return _resolve_ids(conn, date, cfg.alliance_tags)
    if alliance in cfg.alliance_tags:
        return set(store.alliance_ids_by_tag(conn, date).get(alliance, []))
    raise HTTPException(
        status_code=422,
        detail=f"unknown alliance {alliance!r} — valid: {', '.join(cfg.alliance_tags)}",
    )


def _event_dict(event: VillageEvent) -> dict[str, object]:
    """One village event in the events-browser payload shape."""
    return {
        "village_name": event.village_name,
        "x": event.x,
        "y": event.y,
        "region": event.region,
        "event": event.event,
        "owner_tag": event.new_owner_tag,
        "owner_player": event.new_owner_player,
    }


def _settings_payload(cfg: ConfigProtocol) -> SettingsPayload:
    return SettingsPayload(
        ALLIANCE_TAGS=cfg.alliance_tags,
        TRACKED_ALLIANCES=cfg.tracked_alliances,
        CHANNEL_ID=cfg.channel_id,
        FETCH_HOUR=cfg.fetch_hour,
        FETCH_MINUTE=cfg.fetch_minute,
        FETCH_TZ=cfg.fetch_tz,
        REPORT_HOUR=cfg.report_hour,
        REPORT_MINUTE=cfg.report_minute,
        REPORT_TZ=cfg.report_tz,
        ADMIN_ROLE_ID=cfg.admin_role_id,
        REPORT_EMBED_COLOR=cfg.report_embed_color,
    )


def make_status_provider(db_path: str, get_config: ConfigGetter) -> Callable[[], StatusData]:
    """Build the real ``/api/status`` payload builder for ``db_path``.

    Each call opens its own sqlite connection (per-operation policy — safe
    under WAL while the bot loop's jobs write concurrently); the merged config
    comes from the injected ``get_config``.
    """
    def get_status() -> StatusData:
        conn = store.connect(db_path)
        try:
            cfg = get_config()
            latest = store.load_latest(conn)
            snapshot_date: str | None = None
            snapshot_source: str | None = None
            villages = 0
            players = 0
            alliances = 0
            total_population = 0
            if latest is not None:
                rows = store.load_villages(conn, latest.snapshot_date)
                snapshot_date = latest.snapshot_date
                snapshot_source = latest.source
                villages = len(rows)
                players = len({row.player_id for row in rows})
                # alliance_id 0 = no alliance (map.sql convention)
                alliances = len({row.alliance_id for row in rows if row.alliance_id != 0})
                total_population = sum(row.population for row in rows)
            return StatusData(
                snapshot_date=snapshot_date,
                snapshot_source=snapshot_source,
                villages=villages,
                players=players,
                alliances=alliances,
                total_population=total_population,
                fetch_hour=cfg.fetch_hour,
                fetch_minute=cfg.fetch_minute,
                fetch_tz=cfg.fetch_tz,
                report_hour=cfg.report_hour,
                report_minute=cfg.report_minute,
                report_tz=cfg.report_tz,
                next_fetch=_next_occurrence(cfg.fetch_hour, cfg.fetch_minute, cfg.fetch_tz),
                next_report=_next_occurrence(cfg.report_hour, cfg.report_minute, cfg.report_tz),
                errors=_recent_errors(conn),
                alliance_tags=cfg.alliance_tags,
            )
        finally:
            conn.close()

    return get_status


# --- PUT /api/settings validation -----------------------------------------------


def _int_setting(key: str, value: object) -> int:
    """Coerce a settings value to int; ``bool`` is not an int here."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise HTTPException(status_code=422, detail=f"{key} must be an integer, got {value!r}")


def _normalize_tags(key: str, value: object) -> list[str]:
    """Strip + dedupe a tag-list setting value (list of strings). Empty → []."""
    if not isinstance(value, list):
        raise HTTPException(status_code=422, detail=f"{key} must be a list of strings")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise HTTPException(status_code=422, detail=f"{key} must be a list of strings")
    tags: list[str] = []
    for item in cast(list[str], items):
        tag = item.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _validate_tags(value: object) -> list[str]:
    """ALLIANCE_TAGS: list of strings, stripped + deduped; empty after → 422.

    The empty state is env-only: clearing the tags via the dashboard is
    rejected, so a misclick can never silently disable the daily report.
    """
    tags = _normalize_tags("ALLIANCE_TAGS", value)
    if not tags:
        raise HTTPException(
            status_code=422,
            detail="clearing ALLIANCE_TAGS via dashboard is not allowed — empty state is env-only",
        )
    return tags


def _validate_payload(payload: dict[str, object]) -> dict[str, store.JsonValue]:
    """Validate a ``PUT /api/settings`` body — 422 on the FIRST problem.

    Unknown keys (any secret, ``SQLITE_PATH``, ``DASHBOARD_*``, ...) are
    rejected before anything is validated or written; the returned dict is the
    validated subset, handed to ONE atomic ``store.set_settings`` call.
    """
    unknown = sorted(key for key in payload if key not in ALLOWED_SETTINGS_KEYS)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unknown setting key(s): {', '.join(unknown)}"
                f" — allowed: {', '.join(sorted(ALLOWED_SETTINGS_KEYS))}"
            ),
        )
    validated: dict[str, store.JsonValue] = {}
    for key, value in payload.items():
        match key:
            case "ALLIANCE_TAGS":
                validated[key] = cast(store.JsonValue, _validate_tags(value))
            case "TRACKED_ALLIANCES":
                # Empty is allowed: it just hides the Standings field.
                validated[key] = cast(store.JsonValue, _normalize_tags("TRACKED_ALLIANCES", value))
            case "CHANNEL_ID":
                validated[key] = _int_setting(key, value)
            case "FETCH_HOUR" | "REPORT_HOUR":
                hour = _int_setting(key, value)
                if not 0 <= hour <= 23:
                    raise HTTPException(status_code=422, detail=f"{key} must be between 0 and 23, got {hour}")
                validated[key] = hour
            case "FETCH_MINUTE" | "REPORT_MINUTE":
                minute = _int_setting(key, value)
                if not 0 <= minute <= 59:
                    raise HTTPException(status_code=422, detail=f"{key} must be between 0 and 59, got {minute}")
                validated[key] = minute
            case "FETCH_TZ" | "REPORT_TZ":
                if not isinstance(value, str):
                    raise HTTPException(status_code=422, detail=f"{key} must be a timezone name, got {value!r}")
                try:
                    _ = ZoneInfo(value)
                except (ZoneInfoNotFoundError, ValueError):
                    raise HTTPException(status_code=422, detail=f"{key}: unknown timezone {value!r}") from None
                validated[key] = value
            case "ADMIN_ROLE_ID":
                validated[key] = None if value is None else _int_setting(key, value)
            case "REPORT_EMBED_COLOR":
                color = _int_setting(key, value)
                if not 0 <= color <= 0xFFFFFF:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{key} must be between 0x000000 and 0xFFFFFF, got {color}",
                    )
                validated[key] = color
            case _:
                raise AssertionError(f"unhandled settings key {key!r}")  # unreachable: keys pre-validated
    return validated


# --- app factory ---------------------------------------------------------------


def create_app(deps: DashboardDeps) -> FastAPI:
    """Build the dashboard FastAPI app bound to ``deps`` (see module docstring).

    Handlers are defined then explicitly registered via the route helpers —
    the call form makes each handler an argument, so basedpyright's
    unused-function check stays silent (decorator form would flag them).
    """
    app = FastAPI(title="travian report bot dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    auth_method = _auth_method(deps.env)
    session_store = auth.SessionStore()
    action_limiter = auth.ActionLimiter()  # 6 actions / 60 s per user
    db_path = _db_path(deps.env)

    def _is_public(path: str) -> bool:
        # The static UI, the healthcheck probe and the whole /api/auth/*
        # surface stay public so the browser can load the page and log in
        # without credentials; every other /api/* route is protected.
        return path == "/" or path == "/healthz" or path.startswith(("/static/", "/api/auth/"))

    def _rate_limited(path: str, method: str, key: str) -> bool:
        if not (path.startswith("/api/actions/") or (path == "/api/settings" and method == "PUT")):
            return False
        return not action_limiter.allow(key)

    async def _auth_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if _is_public(path) or not path.startswith("/api/"):
            return await call_next(request)
        if auth_method == "none":
            return await call_next(request)

        supplied = request.headers.get("authorization", "")
        bearer = supplied[len("Bearer ") :] if supplied.startswith("Bearer ") else ""
        if auth_method == "token":
            expected = deps.env.get("DASHBOARD_TOKEN", "")
            # Constant-time comparison; an empty expected token rejects
            # everything (the middleware being active without a token is a
            # configuration error and must not silently open the API).
            if not expected or not bearer or not hmac.compare_digest(bearer, expected):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            rate_key = request.client.host if request.client is not None else "unknown"
        else:  # oauth
            session = session_store.get(bearer) if bearer else None
            if session is None:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            request.state.user = session
            rate_key = session.token

        if _rate_limited(path, request.method, rate_key):
            return JSONResponse(
                {"error": "rate limited"},
                status_code=429,
                headers={"Retry-After": str(action_limiter.retry_after(rate_key))},
            )
        return await call_next(request)

    def index() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=404, detail="static/index.html not built yet")
        return FileResponse(index_path)

    def healthz() -> dict[str, str]:
        """Container HEALTHCHECK probe — always public, never token-protected.

        The app only starts serving after ``main()`` passed startup
        validation, so a 200 implies the process is healthy (the pre-token
        semantics of /api/status, without needing the Bearer header).
        """
        return {"status": "ok"}

    def _admin_ok(request: Request) -> bool:
        """RBAC gate: token mode treats token possession as admin; oauth mode
        requires the session's ``admin`` flag (the middleware has already
        401'd non-sessions)."""
        if auth_method != "oauth":
            return True
        user = getattr(request.state, "user", None)
        return isinstance(user, auth.Session) and user.admin

    async def auth_status(request: Request) -> dict[str, object]:
        """Public: the active auth method + the logged-in user (oauth only)."""
        user: dict[str, object] | None = None
        if auth_method == "oauth":
            supplied = request.headers.get("authorization", "")
            if supplied.startswith("Bearer "):
                session = session_store.get(supplied[len("Bearer ") :])
                if session is not None:
                    user = {"name": session.username, "admin": session.admin}
        return {"method": auth_method, "user": user}

    def auth_login(request: Request) -> Response:
        """Public: redirect to Discord's authorization page (oauth mode)."""
        if auth_method != "oauth":
            return JSONResponse({"error": "oauth not enabled"}, status_code=409)
        state = secrets.token_urlsafe(16)
        _store_oauth_state(state, datetime.now(UTC) + timedelta(minutes=10))
        redirect_uri = str(request.base_url) + "api/auth/callback"
        client_id = deps.env.get("OAUTH_CLIENT_ID", "")
        return RedirectResponse(auth.authorize_url(client_id, redirect_uri, state), status_code=302)

    async def auth_callback(request: Request) -> Response:
        """Public: Discord redirect target — exchanges the code, resolves
        member/admin and lands on ``/?session=<token>`` (the UI adopts it).
        Failures redirect to ``/?auth_error=<reason>``; always 302."""
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state or not _consume_oauth_state(state):
            return RedirectResponse("/?#auth_error=invalid_state", status_code=302)
        redirect_uri = str(request.base_url) + "api/auth/callback"
        client_id = deps.env.get("OAUTH_CLIENT_ID", "")
        client_secret = deps.env.get("OAUTH_CLIENT_SECRET", "")
        try:
            token_data = await asyncio.to_thread(auth.exchange_code, client_id, client_secret, code, redirect_uri)
            access_token_raw = token_data.get("access_token")
            if not isinstance(access_token_raw, str):
                return RedirectResponse("/?#auth_error=login_failed", status_code=302)
            user = await asyncio.to_thread(auth.fetch_user, access_token_raw)
            member = await asyncio.to_thread(auth.fetch_guild_member, access_token_raw, deps.env.get("OAUTH_GUILD_ID", ""))
            # Member-endpoint fallback: the guilds list alone can still prove
            # membership (and carries the Manage Server permission bit).
            if member is None:
                guilds = await asyncio.to_thread(auth.fetch_guilds, access_token_raw)
            else:
                guilds = []
        except Exception:  # noqa: BLE001 — any OAuth failure is a login failure, never a crash
            return RedirectResponse("/?#auth_error=login_failed", status_code=302)
        guild_id_raw = deps.env.get("OAUTH_GUILD_ID", "")
        try:
            guild_id = int(guild_id_raw)
        except ValueError:
            return RedirectResponse("/?#auth_error=login_failed", status_code=302)
        cfg = deps.get_config()
        is_member, is_admin = auth.resolve_admin(member, guilds, guild_id, cfg.admin_role_id)
        if not is_member:
            return RedirectResponse("/?#auth_error=not_a_member", status_code=302)
        user_id_raw = user.get("id")
        username_raw = user.get("username")
        user_id = str(user_id_raw) if user_id_raw is not None else ""
        username = str(username_raw) if username_raw is not None else "unknown"
        token = session_store.create(user_id, username, is_admin)
        return RedirectResponse(f"/?session={token}", status_code=302)

    async def auth_logout(request: Request) -> Response:
        """Public: invalidate the session from the Bearer header (204)."""
        supplied = request.headers.get("authorization", "")
        if supplied.startswith("Bearer "):
            session_store.delete(supplied[len("Bearer ") :])
        return Response(status_code=204)

    async def status() -> StatusData:
        return await asyncio.to_thread(deps.get_status)

    async def get_settings(request: Request) -> Response:
        # Settings are configuration — in oauth mode only admins may read
        # them (members get data via /api/status and /api/analysis/*).
        if not _admin_ok(request):
            return JSONResponse({"error": "admin required"}, status_code=403)
        cfg = await asyncio.to_thread(deps.get_config)
        return JSONResponse(_settings_payload(cfg))

    async def put_settings(request: Request, payload: dict[str, object]) -> Response:
        if not _admin_ok(request):
            return JSONResponse({"error": "admin required"}, status_code=403)
        validated = _validate_payload(payload)

        def write() -> SettingsPayload:
            conn = store.connect(db_path)
            try:
                store.set_settings(conn, validated)
            finally:
                conn.close()
            return _settings_payload(deps.get_config())

        return JSONResponse(await asyncio.to_thread(write))

    async def fetch_now(request: Request) -> Response:
        if not _admin_ok(request):
            return JSONResponse({"error": "admin required"}, status_code=403)
        loop = deps.bot_loop_getter()
        if loop is None:
            return JSONResponse({"error": "bot not ready"}, status_code=409)
        future = asyncio.run_coroutine_threadsafe(deps.run_fetch_fn(), loop)
        try:
            result = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), timeout=_ACTION_TIMEOUT)
        except TimeoutError:
            return JSONResponse({"error": "fetch timed out"}, status_code=504)
        return JSONResponse({"status": "ok", "message": result})

    async def report_now(request: Request) -> Response:
        if not _admin_ok(request):
            return JSONResponse({"error": "admin required"}, status_code=403)
        loop = deps.bot_loop_getter()
        if loop is None:
            return JSONResponse({"error": "bot not ready"}, status_code=409)
        cfg = await asyncio.to_thread(deps.get_config)
        channel_id = cfg.channel_id
        if channel_id is None:
            return JSONResponse({"error": "CHANNEL_ID not configured"}, status_code=409)
        future = asyncio.run_coroutine_threadsafe(deps.run_report_fn(channel_id, require_today=False), loop)
        try:
            result = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), timeout=_ACTION_TIMEOUT)
        except TimeoutError:
            return JSONResponse({"error": "report timed out"}, status_code=504)
        return JSONResponse({"status": "ok", "message": result})

    async def logs(n: Annotated[int, Query(ge=1, le=_MAX_LOG_WINDOW)] = 50) -> list[dict[str, str]]:
        def read() -> list[dict[str, str]]:
            conn = store.connect(db_path)
            try:
                return store.recent_logs(conn, n)
            finally:
                conn.close()

        return await asyncio.to_thread(read)

    # --- analysis endpoints (report trim) --------------------------------------
    #
    # All of them: asyncio.to_thread + own store.connect per op (existing
    # pattern), config tags resolved against the LATEST snapshot date
    # (fallback: latest snapshot date; no snapshot → empty payloads). ``days``
    # is at least 2 because deltas and charts need a pair of dates.

    async def analysis_regions(
        days: Annotated[int, Query(ge=2, le=60)] = 30,
        alliance: str | None = None,
    ) -> dict[str, object]:
        def read() -> dict[str, object]:
            conn = store.connect(db_path)
            try:
                cfg = deps.get_config()
                latest = store.load_latest(conn)
                if latest is None:
                    return {"dates": [], "series": {}, "current": []}
                dates = store.list_dates(conn)[-days:]
                ids = _resolve_alliance_ids(conn, latest.snapshot_date, cfg, alliance)
                day_rows = store.region_days(conn, dates[0], dates[-1], ids)
                by_region: dict[str, list[RegionDay]] = {}
                for day in day_rows:
                    by_region.setdefault(day.region, []).append(day)
                share_series = analysis.region_share_series(day_rows)
                series: dict[str, list[dict[str, object]]] = {}
                for region in sorted(share_series):
                    days_list = by_region[region]
                    # Regions with no our-population over the window are
                    # dropped (flat 0.0% enemy-only lines); regions we left
                    # keep their declining line.
                    if not any(d.our_pop > 0 for d in days_list):
                        continue
                    series[region] = [
                        {"date": d.date, "share": share, "our_pop": d.our_pop, "total_pop": d.total_pop}
                        for (_, share), d in zip(share_series[region], days_list, strict=True)
                    ]
                # current = the latest-pair table (identical numbers to the
                # report's Regions embed) plus the server-computed control
                # fields — the UI formats, never re-implements the rules.
                prev_date = dates[-2] if len(dates) >= 2 else None
                curr_rows = store.load_villages(conn, latest.snapshot_date)
                prev_rows = store.load_villages(conn, prev_date) if prev_date is not None else None
                stats = region_stats(prev_rows, curr_rows, ids)
                current: list[dict[str, object]] = []
                for stat in stats:
                    row = stat.model_dump()
                    row["active"] = analysis.region_active(stat)
                    row["controlled"] = analysis.region_controlled(stat)
                    row["to50_needed"] = analysis.to50_needed(stat)
                    current.append(row)
                return {"dates": dates, "series": series, "current": current}
            finally:
                conn.close()

        return await asyncio.to_thread(read)

    async def analysis_standings(days: Annotated[int, Query(ge=2, le=60)] = 30) -> dict[str, object]:
        def read() -> dict[str, object]:
            conn = store.connect(db_path)
            try:
                cfg = deps.get_config()
                latest = store.load_latest(conn)
                if latest is None:
                    return {"dates": [], "series": []}
                dates = store.list_dates(conn)[-days:]
                ids = _resolve_ids(conn, latest.snapshot_date, cfg.tracked_alliances)
                if not ids:
                    return {"dates": dates, "series": []}
                day_rows = store.alliance_days(conn, dates[0], dates[-1], ids)
                series = analysis.standings_series(day_rows, set(cfg.alliance_tags))
                return {"dates": dates, "series": series}
            finally:
                conn.close()

        return await asyncio.to_thread(read)

    async def analysis_dates() -> dict[str, object]:
        def read() -> dict[str, object]:
            conn = store.connect(db_path)
            try:
                return {"dates": store.list_dates(conn)}
            finally:
                conn.close()

        return await asyncio.to_thread(read)

    async def analysis_events(
        from_: Annotated[str | None, Query(alias="from")] = None,
        to: str | None = None,
        alliance: str | None = None,
    ) -> dict[str, object]:
        def read() -> dict[str, object]:
            conn = store.connect(db_path)
            try:
                cfg = deps.get_config()
                all_dates = store.list_dates(conn)
                if not all_dates:
                    return {"gained": [], "lost": []}
                latest = all_dates[-1]
                from_date = from_ if from_ is not None else (all_dates[-2] if len(all_dates) >= 2 else latest)
                to_date = to if to is not None else latest
                if from_date not in all_dates:
                    raise HTTPException(
                        status_code=422,
                        detail=f"unknown 'from' date {from_date!r} — valid dates: {', '.join(all_dates)}",
                    )
                if to_date not in all_dates:
                    raise HTTPException(
                        status_code=422,
                        detail=f"unknown 'to' date {to_date!r} — valid dates: {', '.join(all_dates)}",
                    )
                if from_date >= to_date:
                    raise HTTPException(status_code=422, detail="'from' must be earlier than 'to'")
                ids = _resolve_alliance_ids(conn, latest, cfg, alliance)
                prev_rows = store.load_villages(conn, from_date)
                curr_rows = store.load_villages(conn, to_date)
                gained, lost = village_events(prev_rows, curr_rows, ids)
                return {
                    "gained": [_event_dict(e) for e in gained],
                    "lost": [_event_dict(e) for e in lost],
                }
            finally:
                conn.close()

        return await asyncio.to_thread(read)

    async def analysis_deltas(
        days: Annotated[int, Query(ge=2, le=60)] = 30,
        alliance: str | None = None,
    ) -> dict[str, object]:
        def read() -> dict[str, object]:
            conn = store.connect(db_path)
            try:
                cfg = deps.get_config()
                latest = store.load_latest(conn)
                if latest is None:
                    return {"dates": [], "rows": []}
                dates = store.list_dates(conn)[-days:]
                ids = _resolve_alliance_ids(conn, latest.snapshot_date, cfg, alliance)
                day_rows = store.summary_days(conn, dates[0], dates[-1], ids)
                return {"dates": dates, "rows": analysis.summary_history(day_rows)}
            finally:
                conn.close()

        return await asyncio.to_thread(read)

    _ = app.middleware("http")(_auth_middleware)
    _ = app.get("/")(index)
    _ = app.get("/healthz")(healthz)
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    _ = app.get("/api/auth/status")(auth_status)
    _ = app.get("/api/auth/login")(auth_login)
    _ = app.get("/api/auth/callback")(auth_callback)
    _ = app.post("/api/auth/logout")(auth_logout)
    _ = app.get("/api/status")(status)
    _ = app.get("/api/settings")(get_settings)
    _ = app.put("/api/settings")(put_settings)
    _ = app.post("/api/actions/fetch")(fetch_now)
    _ = app.post("/api/actions/report")(report_now)
    _ = app.get("/api/logs")(logs)
    _ = app.get("/api/analysis/regions")(analysis_regions)
    _ = app.get("/api/analysis/standings")(analysis_standings)
    _ = app.get("/api/analysis/dates")(analysis_dates)
    _ = app.get("/api/analysis/events")(analysis_events)
    _ = app.get("/api/analysis/deltas")(analysis_deltas)
    return app
