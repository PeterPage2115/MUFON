"""Browser smoke tests: the real static UI against a throwaway FastAPI app.

One self-contained fixture: a ``tmp_path`` SQLite with two snapshots (NOVA +
ENEMY with disjoint players and > 200 disjoint gained villages per tag), a
``create_app(DashboardDeps(...))`` in explicit ``DASHBOARD_AUTH_MODE=none``,
uvicorn on a free loopback port, and Playwright Chromium — the server and the
browser are closed regardless of assertion outcome. The test never clicks
Fetch/Report, never reads a production SQLite and never uses a token.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Callable, Generator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import uvicorn
from playwright.sync_api import Browser, Page, sync_playwright

from travian import store
from travian.bot import main as bot_main
from travian.dashboard.app import DashboardDeps, RuntimeState, create_app, make_status_provider
from travian.models import VillageRow

pytestmark = pytest.mark.browser

NOVA_ID = 7
ENEMY_ID = 8
NOVA_GAINS = 220  # > default 200 rows-per-list: the combined view truncates
ENEMY_GAINS = 220


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _env(db: Path) -> dict[str, str]:
    return {
        "DISCORD_TOKEN": "test-token",
        "CHANNEL_ID": "111111111111111111",
        "ALLIANCE_TAGS": "NOVA,ENEMY",
        "TRACKED_ALLIANCES": "NOVA,ENEMY",
        "FETCH_HOUR": "0",
        "FETCH_MINUTE": "15",
        "FETCH_TZ": "Europe/London",
        "REPORT_HOUR": "9",
        "REPORT_MINUTE": "0",
        "REPORT_TZ": "Europe/Warsaw",
        "SQLITE_PATH": str(db),
        "DASHBOARD_BIND": "127.0.0.1",
        "DASHBOARD_PORT": "8090",
        "DASHBOARD_LOOPBACK_ONLY": "false",
        "DASHBOARD_AUTH_MODE": "none",  # explicit: tests are their own client
        "DASHBOARD_TOKEN": "",
    }


def _config_getter(db: Path, env: dict[str, str]) -> Callable[[], bot_main.MergedConfig]:
    def get_config() -> bot_main.MergedConfig:
        conn = store.connect(db)
        try:
            return bot_main.load_merged_config(conn, env)
        finally:
            conn.close()

    return get_config


def _row(
    village_id: int,
    *,
    x: int,
    population: int,
    player_id: int,
    player_name: str,
    alliance_id: int,
    alliance_tag: str,
) -> VillageRow:
    return VillageRow(
        village_id=village_id,
        x=x,
        y=x,
        tribe=1,
        name=f"Village {village_id}",
        player_id=player_id,
        player_name=player_name,
        alliance_id=alliance_id,
        alliance_tag=alliance_tag,
        population=population,
        region="Testland",
        is_capital=False,
        is_city=False,
        is_harbor=False,
        victory_points=10,
    )


def _seed_browser_db(db: Path) -> None:
    """Two snapshots: NOVA/ENEMY present on both days with disjoint players;
    the second day adds 220 disjoint gained villages per tag (ids 10000+ /
    20000+), so combined has 440 gained events and each tag 220."""
    conn = store.connect(db)
    store.init_schema(conn)
    base = [
        _row(1, x=1, population=100, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA"),
        _row(2, x=2, population=100, player_id=2000, player_name="ENEMY-P0", alliance_id=ENEMY_ID, alliance_tag="ENEMY"),
    ]
    prev = [_row(1, x=1, population=90, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA"),
            _row(2, x=2, population=90, player_id=2000, player_name="ENEMY-P0", alliance_id=ENEMY_ID, alliance_tag="ENEMY")]
    store.save_snapshot(conn, "2026-08-07", prev)
    curr = list(base)
    for i in range(NOVA_GAINS):
        curr.append(
            _row(10000 + i, x=100 + i, population=100 + i, player_id=1000 + i,
                 player_name=f"NOVA-P{i}", alliance_id=NOVA_ID, alliance_tag="NOVA")
        )
    for i in range(ENEMY_GAINS):
        curr.append(
            _row(20000 + i, x=200 + i, population=1000 + i, player_id=2000 + i,
                 player_name=f"ENEMY-P{i}", alliance_id=ENEMY_ID, alliance_tag="ENEMY")
        )
    store.save_snapshot(conn, "2026-08-08", curr)
    conn.close()


def _seed_wars_db(db: Path) -> None:
    """Wars scenario: NOVA(7) ↔ ENEMY(8) transfers (villages 2/3), one ENEMY
    deletion (village 4), one untracked stable village (GHOST 999) and one new
    NOVA village (7) on the second day — only the transfers + deletion count."""
    conn = store.connect(db)
    store.init_schema(conn)
    store.save_snapshot(
        conn,
        "2026-08-07",
        [
            _row(1, x=1, population=100, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA"),
            _row(2, x=2, population=200, player_id=2000, player_name="ENEMY-P0", alliance_id=ENEMY_ID, alliance_tag="ENEMY"),
            _row(3, x=3, population=300, player_id=1003, player_name="NOVA-P3", alliance_id=NOVA_ID, alliance_tag="NOVA"),
            _row(4, x=4, population=400, player_id=2004, player_name="ENEMY-P4", alliance_id=ENEMY_ID, alliance_tag="ENEMY"),
            _row(6, x=6, population=600, player_id=9006, player_name="GHOST-P6", alliance_id=999, alliance_tag="GHOST"),
        ],
    )
    store.save_snapshot(
        conn,
        "2026-08-08",
        [
            _row(1, x=1, population=110, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA"),
            # Real ownership changes (different players) — a same-player
            # switch would be an alliance change, never a wars conquest.
            _row(2, x=2, population=210, player_id=2001, player_name="NOVA-P1", alliance_id=NOVA_ID, alliance_tag="NOVA"),
            _row(3, x=3, population=310, player_id=2003, player_name="ENEMY-P3", alliance_id=ENEMY_ID, alliance_tag="ENEMY"),
            _row(6, x=6, population=610, player_id=9006, player_name="GHOST-P6", alliance_id=999, alliance_tag="GHOST"),
            _row(7, x=7, population=700, player_id=1007, player_name="NOVA-P7", alliance_id=NOVA_ID, alliance_tag="NOVA"),
        ],
    )
    conn.close()


@pytest.fixture()
def browser_app(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard (existing scenarios): DASHBOARD_AUTH_MODE=none."""
    yield from _browser_app_with_auth(tmp_path, auth_mode="none", token="")


@pytest.fixture()
def browser_app_wars(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard over the wars scenario (two snapshots, transfers)."""
    yield from _browser_app_with_auth(tmp_path, auth_mode="none", token="", seed=_seed_wars_db)


@pytest.fixture()
def browser_app_empty(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard over an EMPTY database (schema, zero snapshots)."""
    yield from _browser_app_with_auth(tmp_path, auth_mode="none", token="", seed=_seed_empty_db)


@pytest.fixture()
def browser_app_gap(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard over a 3-day-gap pair (2026-08-04 → 2026-08-08)."""
    yield from _browser_app_with_auth(tmp_path, auth_mode="none", token="", seed=_seed_gap_db)


@pytest.fixture()
def browser_app_current(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard over a current (today+yesterday) snapshot pair."""
    yield from _browser_app_with_auth(tmp_path, auth_mode="none", token="", seed=_seed_current_db)


@pytest.fixture()
def browser_app_token(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Token-mode dashboard for the auth-gate scenarios (test token only).

    Serves a REAL asyncio loop in a background thread so the Actions can
    dispatch (the production bot_loop pattern) instead of 409ing.
    """
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield from _browser_app_with_auth(
            tmp_path, auth_mode="token", token="browser-smoke-test-token", bot_loop=loop
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)


@pytest.fixture()
def browser_app_no_tracked(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard with EMPTY TRACKED_ALLIANCES: the standings picker
    must stay visible with the hint even though no defaults resolve."""
    yield from _browser_app_with_auth(
        tmp_path, auth_mode="none", token="", env_overrides={"TRACKED_ALLIANCES": ""}
    )


@pytest.fixture()
def browser_app_logs(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard with a NON-EMPTY job log (fetch/report/config rows)
    — the MAX_LOGS rendering regression scenario."""
    yield from _browser_app_with_auth(tmp_path, auth_mode="none", token="", seed=_seed_logs_db)


@pytest.fixture()
def browser_app_dates3(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard over THREE snapshot dates (2026-08-06/07/08) —
    the newest-first history ordering scenario."""
    yield from _browser_app_with_auth(tmp_path, auth_mode="none", token="", seed=_seed_dates3_db)


@pytest.fixture()
def browser_app_watch_change(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard over a same-player alliance switch (village 3) +
    a real conquest (village 2) — the conquest-vs-alliance-change scenario.
    ALLIANCE_TAGS is NOVA-only so the Events tab sees the switch as a loss."""
    yield from _browser_app_with_auth(
        tmp_path,
        auth_mode="none",
        token="",
        seed=_seed_watch_change_db,
        env_overrides={"ALLIANCE_TAGS": "NOVA"},
    )


@pytest.fixture()
def browser_app_oauth_member(tmp_path: Path, monkeypatch) -> Generator[tuple[str, Browser], None, None]:
    """OAuth-mode dashboard where the logged-in user is a plain member.

    The Discord HTTP surface is monkeypatched in-process (the uvicorn app
    runs in a thread of this pytest process); the browser never talks to
    Discord. The session is created through the real callback with a state
    seeded via the app's module-level store — the exact flow the API tests
    use, minus the redirect to discord.com.
    """
    from travian.dashboard import auth as dashboard_auth

    monkeypatch.setattr(dashboard_auth, "exchange_code", lambda *a, **k: {"access_token": "at"})
    monkeypatch.setattr(dashboard_auth, "fetch_user", lambda token: {"id": "u1", "username": "Tester"})
    monkeypatch.setattr(dashboard_auth, "fetch_guild_member", lambda token, guild_id: {"roles": []})
    monkeypatch.setattr(dashboard_auth, "fetch_guilds", lambda token: [])
    yield from _browser_app_with_auth(tmp_path, auth_mode="oauth", token="")


def _seed_empty_db(db: Path) -> None:
    """Schema only, no snapshots — the empty/no-data contract."""
    conn = store.connect(db)
    store.init_schema(conn)
    conn.close()


def _seed_logs_db(db: Path) -> None:
    """The browser seed plus a NON-EMPTY job log (3 rows)."""
    _seed_browser_db(db)
    conn = store.connect(db)
    store.append_log(conn, "fetch", "info", "snapshot saved for 2026-08-08 (442 villages)")
    store.append_log(conn, "report", "info", "report sent to channel 111111111111111111 (snapshot 2026-08-08)")
    store.append_log(conn, "config", "warning", "unresolved alliance tags: []")
    conn.close()


def _seed_dates3_db(db: Path) -> None:
    """Three snapshot dates with a single growing NOVA village — enough
    history for the newest-first ordering assertions."""
    conn = store.connect(db)
    store.init_schema(conn)
    for day, pop in (("2026-08-06", 100), ("2026-08-07", 110), ("2026-08-08", 120)):
        store.save_snapshot(
            conn,
            day,
            [_row(1, x=1, population=pop, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA")],
        )
    conn.close()


def _seed_watch_change_db(db: Path) -> None:
    """Same-player alliance switch (village 3: NOVA-P3 → ENEMY, SAME player)
    + a real conquest (village 2: ENEMY-P0 → NOVA-P1, DIFFERENT player) +
    a stable village (1). The switch must appear exactly once, as an
    alliance change — never conquest, never village_lost."""
    conn = store.connect(db)
    store.init_schema(conn)
    store.save_snapshot(
        conn,
        "2026-08-07",
        [
            _row(1, x=1, population=100, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA"),
            _row(2, x=2, population=200, player_id=2000, player_name="ENEMY-P0", alliance_id=ENEMY_ID, alliance_tag="ENEMY"),
            _row(3, x=3, population=300, player_id=1003, player_name="NOVA-P3", alliance_id=NOVA_ID, alliance_tag="NOVA"),
        ],
    )
    store.save_snapshot(
        conn,
        "2026-08-08",
        [
            _row(1, x=1, population=110, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA"),
            _row(2, x=2, population=210, player_id=2001, player_name="NOVA-P1", alliance_id=NOVA_ID, alliance_tag="NOVA"),
            _row(3, x=3, population=310, player_id=1003, player_name="NOVA-P3", alliance_id=ENEMY_ID, alliance_tag="ENEMY"),
        ],
    )
    conn.close()


def _seed_gap_db(db: Path) -> None:
    """Two snapshots with a 3-day gap (2026-08-04 → 2026-08-08)."""
    conn = store.connect(db)
    store.init_schema(conn)
    store.save_snapshot(
        conn,
        "2026-08-04",
        [_row(1, x=1, population=100, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA")],
    )
    store.save_snapshot(
        conn,
        "2026-08-08",
        [_row(1, x=1, population=110, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA")],
    )
    conn.close()


def _seed_current_db(db: Path) -> None:
    """Yesterday + today (FETCH_TZ=Europe/London) — a current, gap-free pair."""
    conn = store.connect(db)
    store.init_schema(conn)
    today = datetime.now(ZoneInfo("Europe/London")).date()
    for day, population in ((today - timedelta(days=1), 100), (today, 110)):
        store.save_snapshot(
            conn,
            day.isoformat(),
            [_row(1, x=1, population=population, player_id=1000, player_name="NOVA-P0", alliance_id=NOVA_ID, alliance_tag="NOVA")],
        )
    conn.close()


def _browser_app_with_auth(
    tmp_path: Path,
    *,
    auth_mode: str,
    token: str,
    bot_loop: asyncio.AbstractEventLoop | None = None,
    seed: Callable[[Path], None] = _seed_browser_db,
    env_overrides: dict[str, str] | None = None,
) -> Generator[tuple[str, Browser], None, None]:
    db = tmp_path / "browser.db"
    seed(db)
    port = _free_port()
    env = _env(db)
    env["DASHBOARD_AUTH_MODE"] = auth_mode
    env["DASHBOARD_TOKEN"] = token
    if env_overrides:
        env.update(env_overrides)
    if auth_mode == "token":
        # The middleware decides auth from env: with a loopback bind the
        # legacy heuristic resolves to "none" and the token is never checked.
        # A non-loopback bind (compose-style) makes the token mode real.
        env["DASHBOARD_BIND"] = "0.0.0.0"
    if auth_mode == "oauth":
        # Complete OAuth env: the app factory resolves real oauth mode; the
        # Discord HTTP calls are monkeypatched by the oauth fixtures.
        env.update(
            {
                "OAUTH_CLIENT_ID": "cid",
                "OAUTH_CLIENT_SECRET": "csec",
                "OAUTH_GUILD_ID": "100",
                "OAUTH_PUBLIC_ORIGIN": f"http://127.0.0.1:{port}",
            }
        )
    get_config = _config_getter(db, env)

    async def no_fetch(run_id: str | None = None) -> str:
        # Simulate the bot-side run lifecycle so the Operations run history
        # settles (the real bot writes these rows inside run_fetch).
        if run_id:
            conn = store.connect(db)
            try:
                store.finish_run(conn, run_id=run_id, status="succeeded", result="completed")
            finally:
                conn.close()
        return "completed"

    async def no_report(channel_id: int, require_today: bool = True, run_id: str | None = None) -> str:
        if run_id:
            conn = store.connect(db)
            try:
                store.finish_run(conn, run_id=run_id, status="succeeded", result="sent")
            finally:
                conn.close()
        return "sent"

    app = create_app(
        DashboardDeps(
            get_status=make_status_provider(str(db), get_config),
            run_fetch_fn=no_fetch,
            run_report_fn=no_report,
            bot_loop_getter=lambda: bot_loop,
            get_config=get_config,
            get_runtime_state=lambda: RuntimeState(True, True),
            env=env,
        )
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    ready = False
    while time.monotonic() < deadline:
        try:
            if httpx.get(url + "/healthz", timeout=1).status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    assert ready, "uvicorn never served /healthz"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            yield url, browser
        finally:
            browser.close()
    server.should_exit = True
    thread.join(timeout=5)
    assert not thread.is_alive()


def _standings_labels(page: Page) -> list[str]:
    return page.evaluate(
        """() => {
            const chart = Chart.getChart(document.getElementById('analysis-chart-standings'));
            return chart ? chart.data.datasets.map((d) => d.label) : [];
        }"""
    )


def _collect_page_errors(page: Page) -> list[str]:
    """Attach console-error + pageerror collectors; returns the list to assert on."""
    errors: list[str] = []

    def on_console(msg) -> None:
        if msg.type == "error":
            errors.append("console.error: " + msg.text)

    def on_pageerror(exc) -> None:
        errors.append("pageerror: " + str(exc))

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    return errors


def test_filter_switch_players_events_standings_picker_and_limit(
    browser_app: tuple[str, Browser]
) -> None:
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    # Regions is the default tab — wait for its first load to settle.
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # 1. Global filter switch changes the Players table (combined → NOVA →
    # combined again, restoring the shared selection for the Events step).
    page.click("#tab-players")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-players-population] tr'); return el && el.textContent.includes('ENEMY-P219'); }"
    )
    page.click('[data-alliance="NOVA"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-players-population] tr'); return el && el.textContent.includes('NOVA-P219'); }"
    )
    assert "ENEMY-P" not in page.text_content("[data-players-population]")
    page.click('[data-alliance="combined"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-players-population] tr'); return el && el.textContent.includes('ENEMY-P219'); }"
    )

    # 2. Events: first load ends with aria-busy="false" (combined: 200 / 440).
    page.click("#tab-events")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-events-gained-count]'); return el && el.textContent.trim() === '200 / 440'; }"
    )
    page.wait_for_function(
        "() => document.getElementById('panel-events').getAttribute('aria-busy') === 'false'"
    )
    assert page.get_attribute("#panel-events", "aria-busy") == "false"
    # Export note is visible while a list is truncated.
    assert not page.locator("#analysis-events-export-note").evaluate("(el) => el.hidden")

    # 3. Switching the tag while Events is active re-runs the loader and
    # changes the result (NOVA-only: 200 / 220) — the aria-busy fix makes the
    # reload actually fire (a stale busy panel would skip it).
    page.click('[data-alliance="NOVA"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-events-gained-count]'); return el && el.textContent.trim() === '200 / 220'; }"
    )
    page.wait_for_function(
        "() => document.getElementById('panel-events').getAttribute('aria-busy') === 'false'"
    )
    assert page.get_attribute("#panel-events", "aria-busy") == "false"

    # 4. Events limit: 1000 un-truncates the list; back to 200 re-truncates.
    page.select_option("#analysis-events-limit", "1000")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-events-gained-count]'); return el && el.textContent.trim() === '220'; }"
    )
    assert page.locator("#analysis-events-export-note").evaluate("(el) => el.hidden")
    page.select_option("#analysis-events-limit", "200")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-events-gained-count]'); return el && el.textContent.trim() === '200 / 220'; }"
    )
    # The CSV download name reflects the visible limit.
    with page.expect_download() as download_info:
        page.click('[data-export="events"]')
    assert "limit-200" in download_info.value.suggested_filename

    # 5. Alliances tab: global filter hidden, local picker drives the chart.
    page.click("#tab-alliances")
    assert page.locator("#analysis-alliance-filter").evaluate("(el) => el.hidden")
    page.wait_for_function(
        "() => { const c = Chart.getChart(document.getElementById('analysis-chart-standings')); return c && c.data.datasets.length === 2; }"
    )
    assert _standings_labels(page) == ["ENEMY", "NOVA"]  # latest-pop desc

    # Search narrows the options without unchecking; unchecking ENEMY + Apply
    # redraws exactly the selected series.
    page.fill("#analysis-standings-search", "NOVA")
    assert page.locator("#analysis-standings-options label", has_text="ENEMY").evaluate("(el) => el.hidden")
    page.uncheck("#analysis-standings-options input[value='ENEMY']")
    page.click("#analysis-standings-apply")
    page.wait_for_function(
        "() => { const c = Chart.getChart(document.getElementById('analysis-chart-standings')); return c && c.data.datasets.length === 1; }"
    )
    assert _standings_labels(page) == ["NOVA"]

    # Reset restores the first eight defaults (both tags here).
    page.click("#analysis-standings-reset")
    page.wait_for_function(
        "() => { const c = Chart.getChart(document.getElementById('analysis-chart-standings')); return c && c.data.datasets.length === 2; }"
    )
    assert _standings_labels(page) == ["ENEMY", "NOVA"]

    # Back on Regions the global filter is visible again.
    page.click("#tab-regions")
    assert not page.locator("#analysis-alliance-filter").evaluate("(el) => el.hidden")
    page.close()


def test_overview_view_status_and_job_log(browser_app: tuple[str, Browser]) -> None:
    """Role-aware landing: an admin/token operator starts on Overview; panel
    switching, KPI tiles and the admin job log all render."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url, wait_until="domcontentloaded")

    # Overview is the initial view for admins/token (OAuth members land on
    # Intelligence); Intelligence starts hidden.
    assert page.get_attribute("#dashboard-panel-overview", "hidden") is None
    assert page.get_attribute("#dashboard-panel-intelligence", "hidden") is not None
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() !== '—'; }"
    )

    # A successful status read marks the connection online + last good load.
    page.wait_for_function("() => !document.getElementById('last-good-load').hidden")
    assert "Connected" in page.text_content("[data-connection-state]")

    # KPI tiles carry the seeded snapshot numbers (442 villages; distinct
    # player ids are 440: the seeded player ids 1000+i collide with the
    # gained-village ids 10000+i -> same player ids 1000..1219 reused).
    assert page.text_content('[data-status-value="villages"]').strip() == "442"
    assert page.text_content('[data-status-value="players"]').strip() == "440"
    assert page.text_content('[data-status-value="alliances"]').strip() == "2"
    assert page.text_content('[data-status-value="snapshot_date"]').strip() == "2026-08-08"
    # Fixed seed dates are in the past → the text-first freshness label says Stale.
    assert page.text_content('[data-status-value="freshness"]').strip().startswith("Stale \u00b7")

    # Stale state drives the card badge (no job-log errors to outrank it) and
    # the freshness warning carries the API-provided age + latest date.
    assert page.text_content("[data-status-state-label]").strip() == "Stale data"
    warning = page.text_content("[data-status-freshness]")
    assert warning.startswith("Snapshot is ")
    assert "day" in warning
    assert "old. Latest snapshot: 2026-08-08." in warning
    assert page.get_attribute("[data-status-freshness]", "class").count("is-hidden") == 0

    # No success log rows exist → both last-success fields render "Never".
    assert page.text_content('[data-status-value="last_successful_fetch"]').strip() == "Never"
    assert page.text_content('[data-status-value="last_successful_report"]').strip() == "Never"

    # The admin job log settles (seed DB has no log rows -> "No activity yet.").
    page.wait_for_function(
        "() => document.getElementById('job-log').getAttribute('aria-busy') === 'false'"
    )
    assert page.text_content("#log-count").strip() == "No activity yet"
    # The log caption documents the manual-refresh lifecycle.
    assert page.text_content("#log-refresh-note").strip() == "Manual refresh · UTC"

    # Into Intelligence: the panel pair flips; Regions is the default tab.
    page.click("#dashboard-tab-intelligence")
    assert page.get_attribute("#dashboard-panel-intelligence", "hidden") is None
    assert page.get_attribute("#dashboard-panel-overview", "hidden") is not None
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # Manual Refresh exists on the shell and is idle outside a request.
    assert page.text_content("#refresh-dashboard .button-label").strip() == "Refresh dashboard"
    assert errors == []
    page.close()


def test_analysis_tabs_load_without_console_errors(browser_app: tuple[str, Browser]) -> None:
    """Alliances/Changes/Players tabs settle to aria-busy=false without JS errors."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    for tab_id in ("tab-alliances", "tab-changes", "tab-players", "tab-wars", "tab-regions"):
        page.click("#" + tab_id)
        page.wait_for_function(
            "() => document.getElementById('panel-" + tab_id[4:] + "').getAttribute('aria-busy') === 'false'"
        )
    assert errors == []
    page.close()


def test_village_explorer_search_and_history(browser_app: tuple[str, Browser]) -> None:
    """Villages: name search renders a row and the history detail opens."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    page.click("#tab-villages")
    # Substring search: "NOVA-P5" also matches NOVA-P50..P59, so assert the
    # exact village is among the results rather than the whole body.
    page.fill("#village-search-input", "NOVA-P5")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-villages-table]'); return el && !el.hidden && el.textContent.includes('NOVA-P5'); }"
    )
    body = page.text_content("[data-villages-body]")
    assert "Village 10005" in body
    # The search result row: population + player + alliance columns.
    assert "105" in body
    assert "NOVA-P5" in body
    assert "NOVA" in body

    # Open the history detail. Village 10005 only exists in the 08-08
    # snapshot (gained that day), so exactly one observation is stored and
    # the single-point note replaces the trend chart.
    page.click('[aria-label="Open history for Village 10005"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-village-history-table]'); return el && !el.hidden; }"
    )
    assert page.text_content("[data-village-history-body]").count("2026-08-0") == 1
    assert "Village 10005" in page.text_content("#village-detail-name")
    assert "Only one stored observation" in page.text_content("[data-village-detail-note]")
    assert errors == []
    page.close()


def test_csv_exports_for_regions_and_changes(browser_app: tuple[str, Browser]) -> None:
    """Regions and Changes exports download with snapshot-date filenames."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-regions-body] tr'); return el; }"
    )
    # Export button enables once the regions payload is present.
    page.wait_for_function("() => !document.querySelector('[data-export=\"regions\"]').disabled")
    with page.expect_download() as download_info:
        page.click('[data-export="regions"]')
    assert "regions-2026-08-08.csv" in download_info.value.suggested_filename

    page.click("#tab-changes")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-changes-body] tr'); return el; }"
    )
    page.wait_for_function("() => !document.querySelector('[data-export=\"changes\"]').disabled")
    with page.expect_download() as download_info:
        page.click('[data-export="changes"]')
    assert "changes-2026-08-08.csv" in download_info.value.suggested_filename
    assert errors == []
    page.close()


def test_empty_db_no_data_states(browser_app_empty: tuple[str, Browser]) -> None:
    """Empty DB: every view settles to aria-busy=false with an empty state, no JS errors."""
    url, browser = browser_app_empty
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")

    # Regions is the default tab: it settles and shows the no-data message.
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    assert page.get_attribute("#panel-regions", "aria-busy") == "false"
    assert "No data yet." in page.text_content("#panel-regions")
    assert page.get_attribute("#panel-regions", "class").count("is-empty") == 1

    # Other analysis tabs settle too.
    for tab_id in ("tab-alliances", "tab-players", "tab-events", "tab-wars", "tab-changes"):
        page.click("#" + tab_id)
        page.wait_for_function(
            "() => document.getElementById('panel-" + tab_id[4:] + "').getAttribute('aria-busy') === 'false'"
        )

    # Overview: KPI tiles settle and the header reports no snapshot.
    page.click("#dashboard-tab-overview")
    page.wait_for_function(
        "() => document.querySelector('.metric-grid').getAttribute('aria-busy') === 'false'"
    )
    assert page.text_content("[data-header-snapshot]").strip() == "No snapshot yet"
    assert page.text_content('[data-status-value="snapshot_date"]').strip() == "—"
    assert page.text_content('[data-status-value="freshness"]').strip() == "No data"
    # Empty DB: the badge says "No snapshot" and the freshness warning text
    # explains the state (metric label "No data" stays).
    assert page.text_content("[data-status-state-label]").strip() == "No snapshot"
    assert "No snapshot has been stored yet" in page.text_content("[data-status-freshness]")
    assert page.get_attribute("[data-status-freshness]", "class").count("is-hidden") == 0
    assert page.text_content('[data-status-value="last_successful_fetch"]').strip() == "Never"
    assert page.text_content('[data-status-value="last_successful_report"]').strip() == "Never"
    assert errors == []
    page.close()


def test_overview_gap_badge_warning_and_note(browser_app_gap: tuple[str, Browser]) -> None:
    """Seeded 3-day gap: badge + warning show the count and BOTH dates; the
    freshness note exposes the previous date as the comparison baseline."""
    url, browser = browser_app_gap
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url, wait_until="domcontentloaded")
    page.click("#dashboard-tab-overview")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() !== '—'; }"
    )

    assert page.text_content("[data-status-state-label]").strip() == "Snapshot gap"
    assert page.text_content('[data-status-value="freshness"]').strip() == "Gap \u00b7 3 missing days"
    warning = page.text_content("[data-status-freshness]")
    assert "3 days missing between 2026-08-04 and 2026-08-08." in warning
    assert page.get_attribute("[data-status-freshness]", "class").count("is-hidden") == 0
    # The note names the stored previous date — never inferred in JS.
    assert "prev 2026-08-04" in page.text_content('[data-status-note="freshness"]')
    assert errors == []
    page.close()


def test_overview_current_hides_warning(browser_app_current: tuple[str, Browser]) -> None:
    """Current snapshot pair: badge stays Watching, the freshness warning is
    hidden and the metric label says Current."""
    url, browser = browser_app_current
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url, wait_until="domcontentloaded")
    page.click("#dashboard-tab-overview")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() !== '—'; }"
    )

    assert page.text_content("[data-status-state-label]").strip() == "Watching"
    assert page.text_content('[data-status-value="freshness"]').strip() == "Current"
    assert page.get_attribute("[data-status-freshness]", "class").count("is-hidden") == 1
    assert errors == []
    page.close()


def test_empty_db_village_search_no_results(browser_app_empty: tuple[str, Browser]) -> None:
    """Empty DB: a village search settles with the empty-state prompt, no errors."""
    url, browser = browser_app_empty
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    page.click("#tab-villages")
    page.fill("#village-search-input", "nonexistent-village")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-villages-empty]'); return el && !el.hidden && el.textContent.includes('No villages found'); }"
    )
    assert page.get_attribute("#panel-villages", "aria-busy") == "false"
    assert errors == []
    page.close()


def test_token_gate_blocks_until_unlock(browser_app_token: tuple[str, Browser]) -> None:
    """Token mode: protected data stays hidden until the token is entered."""
    url, browser = browser_app_token
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url, wait_until="domcontentloaded")

    # Without a token every protected request 401s -> the unlock dialog opens.
    page.wait_for_selector("#token-dialog[open]", timeout=15000)
    assert "Access token required" in page.text_content("#token-dialog-title")

    # The Operations tab stays hidden and no protected data is rendered.
    assert page.get_attribute("#dashboard-tab-operations", "hidden") is not None
    assert page.text_content('[data-status-value="villages"]').strip() == "—"

    # Enter the test token: the UI stores it and reloads, then loads data.
    page.fill("#token-input", "browser-smoke-test-token")
    page.click('#token-form button[type="submit"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() === '442'; }"
    )
    assert page.text_content('[data-status-value="villages"]').strip() == "442"
    assert errors == []
    page.close()


def test_operations_admin_flow_with_token(browser_app_token: tuple[str, Browser]) -> None:
    """Token holder is admin: Operations loads settings and runs an action."""
    url, browser = browser_app_token
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    # Unlock exactly like a user would (the fixture serves token mode).
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("#token-dialog[open]", timeout=15000)
    page.fill("#token-input", "browser-smoke-test-token")
    page.click('#token-form button[type="submit"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() === '442'; }"
    )

    # Admin sees the Operations tab and the settings form loads on entry.
    assert page.get_attribute("#dashboard-tab-operations", "hidden") is None
    page.click("#dashboard-tab-operations")
    page.wait_for_function(
        "() => { const el = document.getElementById('ALLIANCE_TAGS'); return el && el.value.includes('NOVA'); }"
    )
    assert page.input_value("#ALLIANCE_TAGS") == "NOVA\nENEMY"
    assert page.input_value("#FETCH_HOUR") == "0"

    # Fetch action runs (fake run_fetch returns "completed") and the button
    # returns to its idle state with the outcome in the feedback line.
    page.click("#fetch-action")
    # Feedback appears when the action API resolves; the button returns to
    # idle only after the status/logs refresh that follows it.
    page.wait_for_function(
        "() => { const fb = document.querySelector('#action-feedback span:last-child'); return fb && fb.textContent.includes('Result: completed'); }"
    )
    page.wait_for_function(
        "() => document.getElementById('fetch-action').getAttribute('aria-busy') === 'false'"
    )
    assert page.get_attribute("#fetch-action", "aria-busy") == "false"
    # The button is fully idle: enabled, no loading class, and the SAME
    # action can run again (the log refresh can never strand it).
    assert page.get_attribute("#fetch-action", "disabled") is None
    assert page.locator("#fetch-action.is-loading").count() == 0
    assert page.text_content("#fetch-action .button-label").strip() == "Fetch now"
    page.click("#fetch-action")
    page.wait_for_function(
        "() => { const fb = document.querySelector('#action-feedback span:last-child'); return fb && fb.textContent.includes('Result: completed'); }"
    )
    page.wait_for_function(
        "() => document.getElementById('fetch-action').getAttribute('aria-busy') === 'false'"
    )
    assert errors == []
    page.close()


def test_wars_tab_matrix_drilldown_and_csv(browser_app_wars: tuple[str, Browser]) -> None:
    """Wars tab: conquest matrix, drill-down detail, deleted list and CSV export.

    Seed: NOVA→ENEMY (village 3) and ENEMY→NOVA (village 2) transfers, ENEMY
    deletion (village 4), untracked GHOST stable and a new NOVA village — only
    the two transfers and the deletion may appear.
    """
    url, browser = browser_app_wars
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.click("#tab-wars")
    page.wait_for_function(
        "() => document.getElementById('panel-wars').getAttribute('aria-busy') === 'false'"
    )

    # Matrix head lists both tracked tags; two conquest cells exist.
    page.wait_for_function(
        "() => { const head = document.querySelector('[data-wars-matrix-head]'); return head && head.textContent.indexOf('ENEMY') !== -1 && head.textContent.indexOf('NOVA') !== -1; }"
    )
    assert page.locator("[data-wars-matrix-body] .wars-cell").count() == 2
    # Detail defaults to the first (sorted) pair: ENEMY → NOVA.
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-wars-detail-title]'); return el && el.textContent.indexOf('ENEMY') !== -1; }"
    )
    assert "Village 2" in page.text_content("[data-wars-entries]")

    # Click the NOVA→ENEMY cell (row NOVA, its only button) → drill-down.
    page.locator("[data-wars-matrix-body] tr", has_text="NOVA").locator("button").click()
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-wars-detail-title]'); return el && el.textContent.indexOf('NOVA \u2192 ENEMY') !== -1; }"
    )
    assert "Village 3" in page.text_content("[data-wars-entries]")

    # Deleted list shows the ENEMY deletion only.
    assert page.text_content("[data-wars-deleted-count]").strip() == "1"
    assert "Village 4" in page.text_content("[data-wars-deleted]")

    # CSV export names the selected range.
    page.wait_for_function("() => !document.querySelector('[data-export=\"wars\"]').disabled")
    with page.expect_download() as download_info:
        page.click('[data-export="wars"]')
    assert "wars-2026-08-07-2026-08-08.csv" in download_info.value.suggested_filename
    assert errors == []
    page.close()


# --- Faza 2/2a: trust & UX contracts -----------------------------------------
#
# Mobile reflow, keyboard navigation, chart data tables, connection banner +
# Retry, panel Retry, OAuth member UI, sessionStorage token, settings save
# flow (incl. invalid hex blocked pre-PUT and the dynamic alliance filter),
# empty standings picker, from>=to stale-list clearing, semantic region
# meter, and the no-background-polling request trace.

def _install_request_counter(page: Page) -> None:
    """Count fetch() calls per URL prefix from inside the page.

    Installed as an init script so the counter survives navigations (e.g.
    the token-dialog reload) and wraps the page's fetch before any dashboard
    request fires.
    """
    page.add_init_script(
        """
        window.__reqCounts = {};
        const origFetch = window.fetch;
        window.fetch = function (input, init) {
            const url = typeof input === 'string' ? input : input.url;
            const key = url.split('?')[0];
            window.__reqCounts[key] = (window.__reqCounts[key] || 0) + 1;
            return origFetch.apply(this, arguments);
        };
        """
    )


def _count(page: Page, prefix: str) -> int:
    return page.evaluate(
        """(prefix) => {
            let total = 0;
            for (const key of Object.keys(window.__reqCounts || {})) {
                if (key.includes(prefix)) total += window.__reqCounts[key];
            }
            return total;
        }""",
        prefix,
    )


@pytest.mark.parametrize("width", [375, 686])
def test_regions_responsive_layout_has_no_local_scroll(browser_app: tuple[str, Browser], width: int) -> None:
    """375/686 px: the document never scrolls horizontally, the Regions
    summary reflows into compact two-line cards (five cells, visible
    Pop/Δ/To-50% labels) and keeps its semantic progressbar — no local
    horizontal scroller."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # No horizontal document scroll at either width.
    no_overflow = page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    assert no_overflow, f"document overflows at {width}px"
    # The Regions wrapper never scrolls locally either.
    wrap_ok = page.evaluate(
        "() => { const el = document.querySelector('.data-table__wrap--regions'); return el.scrollWidth <= el.clientWidth + 1; }"
    )
    assert wrap_ok, f"regions wrap overflows at {width}px"
    # Both tab bars WRAP on phones: no horizontal strip, every label
    # reachable (the old overflow-x strip is gone).
    bars = page.evaluate(
        "() => Array.from(document.querySelectorAll('.tab-bar--views, .tab-bar--analysis')).map((el) => el.scrollWidth <= el.clientWidth + 1)"
    )
    assert bars and all(bars), bars
    analysis_labels = page.evaluate(
        "() => Array.from(document.querySelectorAll('#analysis-tabs .tab-bar__tab')).map((t) => t.textContent.trim())"
    )
    assert analysis_labels == ["Regions", "Alliances", "Players", "Events", "Wars", "Changes", "Compare", "Watch", "Roster", "Villages"]

    # Exactly five cells; the mobile data labels are rendered (::before).
    assert page.locator("[data-regions-body] tr").count() == 1
    first_row_cells = page.locator("[data-regions-body] tr").first.locator("td")
    assert first_row_cells.count() == 5
    labels = first_row_cells.evaluate_all(
        "(els) => els.map((e) => e.getAttribute('data-label'))"
    )
    assert labels == ["Region", "Control", "Pop", "Δ", "To 50%"]
    before = page.evaluate(
        "() => getComputedStyle(document.querySelectorAll('[data-regions-body] td')[2], '::before').content"
    )
    assert "Pop" in before
    # The progressbar stays semantic with aria-valuenow.
    meter = page.locator("[data-regions-body] .control-bar").first
    assert meter.get_attribute("role") == "progressbar"
    assert meter.get_attribute("aria-valuemin") == "0"
    assert meter.get_attribute("aria-valuemax") == "100"
    assert meter.get_attribute("aria-valuenow") is not None
    page.close()


@pytest.mark.parametrize("width", [368, 375, 686, 1440])
def test_regions_controls_are_selectable_and_explicit(browser_app: tuple[str, Browser], width: int) -> None:
    """Every visible dynamic select has non-empty option text, is enabled
    and sized, and its center is not covered by another element; the region
    chart select accepts a change and a valid custom From/To pair accepts
    both dates."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    def assert_select_ok(selector: str) -> None:
        state = page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                el.scrollIntoView({ block: 'center' });
                const rect = el.getBoundingClientRect();
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;
                const top = document.elementFromPoint(cx, cy);
                const covered = top !== null && top !== el && !el.contains(top);
                const texts = Array.from(el.options).map((o) => o.textContent.trim());
                return {
                    disabled: el.disabled,
                    zero: rect.width === 0 || rect.height === 0,
                    empty: texts.length === 0 || texts.some((t) => t === ""),
                    covered: covered,
                };
            }""",
            selector,
        )
        assert state == {"disabled": False, "zero": False, "empty": False, "covered": False}, (
            selector,
            state,
        )

    visible = page.evaluate(
        """() => Array.from(document.querySelectorAll(
            '#analysis-region-select, [data-range-from], [data-range-to], #analysis-roster-date, #analysis-roster-alliance'
        ))
            .filter((s) => {
                const r = s.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            })
            .map((s) => s.id)"""
    )
    assert visible, f"no visible dynamic selects at {width}px"
    for sel_id in visible:
        assert_select_ok("#" + sel_id)

    # The region chart select accepts a change (single seeded region).
    assert page.input_value("#analysis-region-select") == "Testland"
    page.select_option("#analysis-region-select", "Testland")
    assert page.input_value("#analysis-region-select") == "Testland"

    # Custom From/To: options come from the real date catalog, both selects
    # are enabled and usable, and the pair accepts both dates.
    page.select_option("#range-regions", "custom")
    page.wait_for_function(
        "() => { const el = document.getElementById('range-regions-to'); return el && el.value === '2026-08-08' && !el.disabled && document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'; }"
    )
    assert_select_ok("#range-regions-from")
    assert_select_ok("#range-regions-to")
    assert page.input_value("#range-regions-from") == "2026-08-07"
    assert page.input_value("#range-regions-to") == "2026-08-08"
    # Both dates are selectable in each pair select; an invalid pair shows
    # the controls error, the valid pair reloads the panel. One change at a
    # time: two simultaneous pair changes would overlap reload chains.
    page.select_option("#range-regions-from", "2026-08-08")
    page.select_option("#range-regions-to", "2026-08-08")
    assert page.evaluate("() => document.getElementById('range-regions-error').hidden === false")
    page.select_option("#range-regions-from", "2026-08-07")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false' && document.getElementById('range-regions-from').value === '2026-08-07' && document.getElementById('range-regions-to').value === '2026-08-08'"
    )
    assert page.input_value("#range-regions-from") == "2026-08-07"
    assert page.input_value("#range-regions-to") == "2026-08-08"
    page.close()


def test_keyboard_arrow_home_end_navigation(browser_app: tuple[str, Browser]) -> None:
    """ArrowRight/ArrowLeft/Home/End walk both tablists (top-level + analysis)."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # Analysis tablist: ArrowRight from Regions activates Alliances.
    page.focus("#tab-regions")
    page.keyboard.press("ArrowRight")
    assert page.get_attribute("#tab-alliances", "aria-selected") == "true"
    page.keyboard.press("End")
    assert page.get_attribute("#tab-villages", "aria-selected") == "true"
    page.keyboard.press("Home")
    assert page.get_attribute("#tab-regions", "aria-selected") == "true"
    page.keyboard.press("ArrowLeft")
    assert page.get_attribute("#tab-villages", "aria-selected") == "true"

    # Top-level tablist: End jumps to the last visible view — for an admin
    # that is Operations (the hidden-tab filter skips nothing here).
    page.focus("#dashboard-tab-intelligence")
    page.keyboard.press("End")
    assert page.get_attribute("#dashboard-tab-operations", "aria-selected") == "true"
    assert page.get_attribute("#dashboard-panel-operations", "hidden") is None
    page.keyboard.press("Home")
    assert page.get_attribute("#dashboard-tab-intelligence", "aria-selected") == "true"
    page.close()


def test_chart_data_table_textual_fallback(browser_app: tuple[str, Browser]) -> None:
    """Every chart canvas describes its exact payload through a semantic
    Show data table (regions + village) — tooltips are never the only read."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # Regions chart: canvas describes the table; the table carries the dates.
    assert page.get_attribute("#analysis-chart-regions", "aria-describedby") == "chart-data-regions"
    page.click("#chart-data-regions summary")
    head = page.text_content("#chart-data-regions thead")
    assert "Date" in head and "Share" in head and "Our pop" in head and "Total pop" in head
    body = page.text_content("#chart-data-regions tbody")
    assert "2026-08-08" in body and "%" in body
    # The chart caption names the SELECTED RANGE, not a single "latest".
    assert "Selected range: 2026-08-07 → 2026-08-08" in page.text_content('[data-as-of="regions"]')

    # Village chart table (history payload) — Village 1 exists on both days;
    # its owner NOVA-P0 is a unique player-name match (population-ordered
    # results would otherwise bury the two-day base village under 50 gains).
    page.click("#tab-villages")
    page.fill("#village-search-input", "NOVA-P0")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-villages-table]'); return el && !el.hidden; }"
    )
    page.click('[aria-label="Open history for Village 1"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-village-history-table]'); return el && !el.hidden; }"
    )
    assert page.get_attribute("#analysis-chart-village", "aria-describedby") == "chart-data-village"
    page.click("#chart-data-village summary")
    body = page.text_content("#chart-data-village tbody")
    assert "2026-08-08" in body and "2026-08-07" in body
    page.close()


def test_connection_issue_banner_retry_and_stale_payload(browser_app: tuple[str, Browser]) -> None:
    """A failed status read keeps the last good payload, shows the persistent
    Connection issue banner with last-good time and a Retry that recovers."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() === '442'; }"
    )
    page.wait_for_function("() => !document.getElementById('last-good-load').hidden")
    assert "Connected" in page.text_content("[data-connection-state]")

    # Break /api/status, then force a read via the manual Refresh button.
    page.route("**/api/status*", lambda route: route.abort())
    page.click("#refresh-dashboard")
    page.wait_for_function(
        "() => document.getElementById('global-status-banner').hidden === false"
    )
    # The banner is the persistent, non-toast error path with a Retry label.
    banner = page.text_content("[data-global-banner-text]")
    assert "Connection issue" in banner
    assert "Last good load:" in banner
    assert page.text_content("#refresh-dashboard .button-label").strip() == "Retry dashboard"
    assert "Connection issue" in page.text_content("[data-connection-state]")
    # The last good payload stays visible — KPI values are never zeroed.
    assert page.text_content('[data-status-value="villages"]').strip() == "442"

    # Un-break and Retry: banner clears, connection returns, label resets.
    page.unroute("**/api/status*")
    page.click("#refresh-dashboard")
    page.wait_for_function(
        "() => document.getElementById('global-status-banner').hidden === true"
    )
    assert "Connected" in page.text_content("[data-connection-state]")
    assert page.text_content("#refresh-dashboard .button-label").strip() == "Refresh dashboard"
    page.close()


def test_panel_error_retry_recovers(browser_app: tuple[str, Browser]) -> None:
    """A failed analysis load renders a panel error with its own Retry —
    tab switching is not the only recovery path."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.route("**/api/analysis/regions*", lambda route: route.abort())
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_selector("#panel-regions .panel-error", timeout=15000)
    assert "Couldn't load analysis data." in page.text_content("#panel-regions .panel-error")
    assert page.locator("#panel-regions .panel-error button", has_text="Retry").count() == 1

    # Retry (route restored) recovers the table.
    page.unroute("**/api/analysis/regions*")
    page.click("#panel-regions .panel-error button")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-regions-body] tr'); return el; }"
    )
    assert page.locator("#panel-regions .panel-error").count() == 0
    page.close()


def test_oauth_member_landing_intelligence_and_readonly(browser_app_oauth_member: tuple[str, Browser]) -> None:
    """OAuth member: lands on Intelligence, never sees Operations/logs, and
    no /api/logs request is ever issued."""
    from datetime import UTC, datetime, timedelta

    from travian.dashboard import app as dashboard_app

    url, browser = browser_app_oauth_member
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    _install_request_counter(page)
    # Seed the OAuth state directly (the Discord redirect is external); the
    # callback runs the real flow with monkeypatched Discord calls.
    dashboard_app._store_oauth_state("browser-member-state", datetime.now(UTC) + timedelta(minutes=5))
    page.goto(url + "/api/auth/callback?code=x&state=browser-member-state", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # Member lands on Intelligence; Overview/Operations are out of reach.
    assert page.get_attribute("#dashboard-panel-intelligence", "hidden") is None
    assert page.get_attribute("#dashboard-panel-overview", "hidden") is not None
    assert page.get_attribute("#dashboard-tab-operations", "hidden") is not None
    # The user chip shows the member role.
    assert "member" in page.text_content("#user-chip")
    # Alliance filter is available (intelligence scope).
    assert page.locator("#analysis-alliance-filter .segmented__btn").count() == 3

    # The member session never requests /api/logs or settings.
    assert _count(page, "/api/logs") == 0
    assert _count(page, "/api/settings") == 0
    assert errors == []
    page.close()


def test_token_stored_in_session_storage(browser_app_token: tuple[str, Browser]) -> None:
    """The dashboard token lives in sessionStorage (same key), never
    localStorage — it survives the tab reload but not the browser session."""
    url, browser = browser_app_token
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("#token-dialog[open]", timeout=15000)
    page.fill("#token-input", "browser-smoke-test-token")
    page.click('#token-form button[type="submit"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() === '442'; }"
    )
    assert page.evaluate("() => sessionStorage.getItem('dashboard_token')") == "browser-smoke-test-token"
    assert page.evaluate("() => localStorage.getItem('dashboard_token')") is None
    page.close()


def test_settings_save_dynamic_alliance_filter_and_invalid_hex_blocked(browser_app_token: tuple[str, Browser]) -> None:
    """Full settings save flow: a new alliance tag appears in the analysis
    filter after Save, and an invalid hex color is blocked BEFORE any PUT."""
    url, browser = browser_app_token
    page = browser.new_page()
    page.set_default_timeout(15000)
    _install_request_counter(page)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("#token-dialog[open]", timeout=15000)
    page.fill("#token-input", "browser-smoke-test-token")
    page.click('#token-form button[type="submit"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() === '442'; }"
    )
    page.click("#dashboard-tab-operations")
    page.wait_for_function(
        "() => { const el = document.getElementById('ALLIANCE_TAGS'); return el && el.value.includes('NOVA'); }"
    )

    # Invalid hex: client-side validation blocks the PUT (no request).
    before_puts = _count(page, "/api/settings")
    page.fill("#REPORT_EMBED_COLOR_TEXT", "zzzzzz")
    page.click("#save-settings")
    assert "Color must be six hex digits" in page.text_content("#REPORT_EMBED_COLOR-error")
    assert _count(page, "/api/settings") == before_puts
    # The picker never accepted the invalid value (stays on the last valid).
    assert page.input_value("#REPORT_EMBED_COLOR_TEXT") == "zzzzzz"  # user text kept for correction
    page.fill("#REPORT_EMBED_COLOR_TEXT", "#D1A84A")

    # Valid save with a new tag: feedback success, filter rebuilds later.
    # One PUT + the post-save settings refresh GET (the save flow re-reads
    # settings and status after writing).
    page.fill("#ALLIANCE_TAGS", "NOVA\nENEMY\nFOO")
    page.click("#save-settings")
    page.wait_for_function(
        "() => { const el = document.getElementById('settings-feedback'); return el.textContent.includes('Settings saved'); }"
    )
    assert _count(page, "/api/settings") == before_puts + 2

    # Back to Intelligence: the rebuilt filter includes the new tag.
    page.click("#dashboard-tab-intelligence")
    page.wait_for_function(
        "() => { const btns = document.querySelectorAll('#analysis-alliance-filter .segmented__btn'); return btns.length === 4; }"
    )
    assert page.locator('[data-alliance="FOO"]').count() == 1
    page.close()


def test_standings_empty_selection_keeps_picker(browser_app_no_tracked: tuple[str, Browser]) -> None:
    """Empty TRACKED_ALLIANCES: the picker stays visible with the hint (the
    map's available tags are listed) — the user can always choose."""
    url, browser = browser_app_no_tracked
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    page.click("#tab-alliances")
    # available_tags come from the snapshot map (NOVA + ENEMY) while no
    # defaults resolve — the picker must stay visible with the hint.
    page.wait_for_function(
        "() => document.getElementById('analysis-standings-feedback').textContent.includes('Select at least one alliance')"
    )
    assert page.locator("#analysis-standings-options label").count() == 2
    assert not page.evaluate("() => document.getElementById('analysis-standings-options').offsetParent === null")
    # Recovery: check one and apply — the chart returns.
    page.check("#analysis-standings-options input[value='NOVA']")
    page.click("#analysis-standings-apply")
    page.wait_for_function(
        "() => { const c = Chart.getChart(document.getElementById('analysis-chart-standings')); return c && c.data.datasets.length === 1; }"
    )
    assert _standings_labels(page) == ["NOVA"]
    page.close()


def test_events_from_equals_to_hides_stale_list(browser_app: tuple[str, Browser]) -> None:
    """from >= to shows the controls error and hides the previous result —
    the list never contradicts the message."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    page.click("#tab-events")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-events-gained-count]'); return el && el.textContent.trim() === '200 / 440'; }"
    )
    # Both selects to the same date → error + hidden grid.
    page.select_option("#analysis-events-from", "2026-08-08")
    page.select_option("#analysis-events-to", "2026-08-08")
    assert page.evaluate("() => document.getElementById('analysis-events-error').hidden === false")
    assert "From must be earlier than To." in page.text_content("#analysis-events-error")
    assert page.evaluate("() => document.querySelector('.events-grid').hidden === true")
    page.close()


def test_region_meter_is_semantic_progressbar(browser_app: tuple[str, Browser]) -> None:
    """The control bar is a real progressbar with a visible percentage."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-regions-body] tr'); return el; }"
    )
    meter = page.locator("[data-regions-body] .control-bar").first
    assert meter.get_attribute("role") == "progressbar"
    assert meter.get_attribute("aria-valuemin") == "0"
    assert meter.get_attribute("aria-valuemax") == "100"
    assert meter.get_attribute("aria-valuenow") == "100.0"
    assert meter.text_content() == "100.0%"
    page.close()


def test_no_background_polling_after_initial_load(browser_app: tuple[str, Browser]) -> None:
    """Lifecycle contract: after the initial settle no /api/status or
    /api/analysis/* request happens without view activation, action or the
    manual Refresh; Refresh adds exactly one cycle."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    _install_request_counter(page)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    status0 = _count(page, "/api/status")
    analysis0 = _count(page, "/api/analysis")
    logs0 = _count(page, "/api/logs")

    # 2 s of idle: nothing grows (no setInterval pollers).
    page.wait_for_timeout(2000)
    assert _count(page, "/api/status") == status0
    assert _count(page, "/api/analysis") == analysis0
    assert _count(page, "/api/logs") == logs0

    # Entering a tab loads it exactly once.
    page.click("#tab-players")
    page.wait_for_function(
        "() => document.getElementById('panel-players').getAttribute('aria-busy') === 'false'"
    )
    assert _count(page, "/api/analysis") == analysis0 + 1

    # Manual Refresh: one status + one active-analysis + one logs cycle.
    page.click("#refresh-dashboard")
    page.wait_for_function(
        "() => document.querySelector('#refresh-dashboard').getAttribute('aria-busy') === 'false'"
    )
    assert _count(page, "/api/status") == status0 + 1
    assert _count(page, "/api/analysis") == analysis0 + 2
    assert _count(page, "/api/logs") == logs0 + 1

    # Idle again after the manual cycle.
    page.wait_for_timeout(2000)
    assert _count(page, "/api/status") == status0 + 1
    assert _count(page, "/api/analysis") == analysis0 + 2
    page.close()


# --- Faza 3: Intelligence drill-down + Overview command center -----------------


def test_overview_command_center_renders(browser_app: tuple[str, Browser]) -> None:
    """Admin landing shows the command center: tracked KPIs with honest
    deltas, movement, top regions and quick links — WITHOUT duplicating the
    Status card's freshness/last-success tiles."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-overview-villages]'); return el && el.textContent.trim() !== '—'; }"
    )
    # Tracked KPIs from the seeded pair (villages 442: 2 base + 440 gained).
    assert page.text_content("[data-overview-villages]").strip() == "442"
    assert page.text_content("[data-overview-villages-delta]").strip() == "+440"
    # Operational tiles live ONLY in Status: the overview never re-renders
    # them (freshness / last successful fetch / report).
    assert page.locator("[data-overview-freshness]").count() == 0
    assert page.locator("[data-overview-last-fetch]").count() == 0
    assert page.locator("[data-overview-last-report]").count() == 0
    assert page.locator('[data-status-value="freshness"]').count() == 1
    # Movement + top regions.
    assert "440 gained" in page.text_content("[data-overview-movement]")
    assert "Testland" in page.text_content("[data-overview-regions]")
    # The overview names its snapshot explicitly ("Latest snapshot:").
    assert "Latest snapshot: 2026-08-08" in page.text_content("[data-overview-asof]")
    # Quick links: analysis views + Operations (admin).
    links = page.text_content("[data-overview-links]")
    assert "Regions" in links and "Compare periods" in links and "Operations" in links
    page.close()


def test_overview_command_center_empty_db(browser_app_empty: tuple[str, Browser]) -> None:
    """Empty DB: the command center shows dashes and the no-data message."""
    url, browser = browser_app_empty
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-overview-grid]'); return el && el.hidden === false; }"
    )
    assert page.text_content("[data-overview-villages]").strip() == "—"
    assert "No regions yet." in page.text_content("[data-overview-regions]")
    page.close()


def test_url_state_range_tab_and_alliance(browser_app: tuple[str, Browser]) -> None:
    """URL state drives the Intelligence context: ?tab=changes&range_changes=30
    activates the tab and ITS range; each tab keeps its own range keys."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence&tab=changes&range_changes=30", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-changes').getAttribute('aria-busy') === 'false'"
    )
    assert page.get_attribute("#tab-changes", "aria-selected") == "true"
    assert page.input_value("#range-changes") == "30"
    assert page.input_value("#range-regions") == "7"  # untouched tab keeps its default
    assert "range_changes=30" in page.url
    assert "range_regions" not in page.url  # default 7 is not written

    # The changes range reports its window in the toolbar.
    assert "Selected range: 2026-08-07" in page.text_content("[data-changes-toolbar]")

    # Changing the REGIONS range never touches the CHANGES range.
    page.click("#tab-regions")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    page.select_option("#range-regions", "60")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    assert "range_regions=60" in page.url
    assert "range_changes=30" in page.url
    page.click("#tab-changes")
    assert page.input_value("#range-changes") == "30"
    page.close()


def test_url_state_invalid_range_falls_back(browser_app: tuple[str, Browser]) -> None:
    """An invalid per-tab range value is rejected to the 7-day default
    (no request loop, no broken pair)."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence&range_regions=99&range_changes=custom", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    assert page.input_value("#range-regions") == "7"
    # Incomplete custom (no from/to) also lands on the safe default.
    assert page.input_value("#range-changes") == "7"
    page.close()


def test_compare_tab_pair_and_validation(browser_app: tuple[str, Browser]) -> None:
    """Compare: the pair renders summary + region deltas + movement; from>=to
    hides the stale table."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    page.click("#tab-compare")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-compare-summary]'); return el && !el.hidden; }"
    )
    # Seeded pair 08-07 → 08-08: 442 villages at 08-08.
    assert "442" in page.text_content("[data-compare-villages]")
    assert "440 gained" in page.text_content("[data-compare-movement]")
    assert "Comparing 2026-08-07" in page.text_content("[data-compare-range-note]")
    # Region table with share deltas.
    body = page.text_content("[data-compare-body]")
    assert "Testland" in body and "%" in body

    # from >= to hides the stale table with the controls error.
    page.select_option("#analysis-compare-to", "2026-08-07")
    assert page.evaluate("() => document.getElementById('analysis-compare-error').hidden === false")
    assert page.evaluate("() => document.querySelector('[data-compare-table]').hidden === true")
    page.close()


def test_player_history_drilldown(browser_app: tuple[str, Browser]) -> None:
    """Players: clicking a name opens the per-snapshot history; a history-only
    player gets the absent badge."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence&tab=players", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-players-population] tr'); return el; }"
    )
    # ENEMY-P219 tops the population ranking (gained village, pop 1219).
    # It exists only in the 08-08 snapshot → one observation, no trend chart.
    page.click('[aria-label="Open history for ENEMY-P219"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-player-history-table]'); return el && !el.hidden; }"
    )
    history = page.text_content("[data-player-history-body]")
    assert "2026-08-08" in history and history.count("2026-08-0") == 1
    assert page.evaluate("() => document.querySelector('[data-player-absent]').hidden === true")
    # Single observation: the textual note replaces the trend chart.
    assert "Only one stored observation" in page.text_content("[data-player-detail-note]")
    page.close()


def test_region_villages_drilldown(browser_app: tuple[str, Browser]) -> None:
    """Regions: clicking a region opens its villages with tracked/other side."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-regions-body] tr'); return el; }"
    )
    page.click('[aria-label="Open villages of Testland"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-region-villages-body] tr'); return el; }"
    )
    body = page.text_content("[data-region-villages-body]")
    # Both seeded alliances are tracked in the combined universe — every row
    # carries the explicit side label (never color-only). The 200-row window
    # fills with the ENEMY gained villages (population DESC).
    assert "tracked" in body
    assert "Village 20219" in body
    assert "· 200 villages" in page.text_content("#region-detail-title")
    # The row opens the village explorer history from the detail.
    page.click('[aria-label="Open history for Village 20219"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-village-history-table]'); return el && !el.hidden; }"
    )
    page.close()


def test_players_standings_and_compare_exports(browser_app: tuple[str, Browser]) -> None:
    """The new CSV exports follow the payload: players-<date>, standings-<date>,
    deltas-<from>-<to>; empty payloads disable the buttons."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    # Players export names the LATEST snapshot pair explicitly.
    page.click("#tab-players")
    page.wait_for_function(
        "() => !document.querySelector('[data-export=\"players\"]').disabled"
    )
    with page.expect_download() as download_info:
        page.click('[data-export="players"]')
    assert "players-2026-08-08.csv" in download_info.value.suggested_filename

    # Standings export.
    page.click("#tab-alliances")
    page.wait_for_function(
        "() => !document.querySelector('[data-export=\"standings\"]').disabled"
    )
    with page.expect_download() as download_info:
        page.click('[data-export="standings"]')
    assert "standings-2026-08-08.csv" in download_info.value.suggested_filename

    # Compare export names the range.
    page.click("#tab-compare")
    page.wait_for_function(
        "() => !document.querySelector('[data-export=\"compare\"]').disabled"
    )
    with page.expect_download() as download_info:
        page.click('[data-export="compare"]')
    assert "deltas-2026-08-07-2026-08-08.csv" in download_info.value.suggested_filename
    page.close()


# --- Faza 4: identifiable runs in the Operations UI ----------------------------


def test_report_action_requires_confirmation(browser_app_token: tuple[str, Browser]) -> None:
    """Send report now asks for confirmation (channel + snapshot) before any
    Discord message; Cancel closes it; Confirm dispatches the action."""
    url, browser = browser_app_token
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("#token-dialog[open]", timeout=15000)
    page.fill("#token-input", "browser-smoke-test-token")
    page.click('#token-form button[type="submit"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() === '442'; }"
    )
    page.click("#dashboard-tab-operations")
    page.wait_for_function(
        "() => { const el = document.getElementById('ALLIANCE_TAGS'); return el && el.value.includes('NOVA'); }"
    )

    # The report button opens the confirmation with channel + snapshot date.
    page.click("#report-action")
    assert page.evaluate("() => document.getElementById('action-confirm').hidden === false")
    copy = page.text_content("[data-action-confirm-copy]")
    assert "channel 111111111111111111" in copy
    assert "2026-08-08" in copy

    # Cancel closes it without dispatching anything.
    page.click("#action-confirm-no")
    assert page.evaluate("() => document.getElementById('action-confirm').hidden === true")

    # Confirm runs the action and the feedback names the run id.
    page.click("#report-action")
    page.click("#action-confirm-yes")
    page.wait_for_function(
        "() => { const fb = document.querySelector('#action-feedback span:last-child'); return fb && fb.textContent.includes('Result: sent'); }"
    )
    feedback = page.text_content("#action-feedback span:last-child")
    assert "run " in feedback
    page.close()


def test_run_history_renders_and_filters(browser_app_token: tuple[str, Browser]) -> None:
    """The run history table lists runs with filters; the db-stats caption
    shows the storage telemetry."""
    url, browser = browser_app_token
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("#token-dialog[open]", timeout=15000)
    page.fill("#token-input", "browser-smoke-test-token")
    page.click('#token-form button[type="submit"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() === '442'; }"
    )
    page.click("#dashboard-tab-operations")
    # Run a fetch so a run row exists (source dashboard, terminal succeeded).
    page.click("#fetch-action")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-runs-body] tr'); return el && el.textContent.includes('succeeded'); }"
    )
    body = page.text_content("[data-runs-body]")
    assert "fetch" in body and "dashboard" in body and "succeeded" in body

    # Filters narrow the list.
    page.select_option("#runs-job", "report")
    page.wait_for_function(
        "() => document.querySelector('[data-runs-body]').textContent.includes('No runs yet.')"
    )
    page.select_option("#runs-job", "fetch")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-runs-body] tr'); return el && el.textContent.includes('succeeded'); }"
    )

    # Storage telemetry caption.
    stats = page.text_content("[data-db-stats]")
    assert "snapshot" in stats and "→" in stats
    page.close()


def test_retry_creates_new_run_id(browser_app_token: tuple[str, Browser]) -> None:
    """A second fetch action produces a NEW run id (manual retry, no
    automatic retry anywhere)."""
    url, browser = browser_app_token
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("#token-dialog[open]", timeout=15000)
    page.fill("#token-input", "browser-smoke-test-token")
    page.click('#token-form button[type="submit"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() === '442'; }"
    )
    page.click("#dashboard-tab-operations")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-runs-body]'); return el; }"
    )

    page.click("#fetch-action")
    page.wait_for_function(
        "() => { const fb = document.querySelector('#action-feedback span:last-child'); return fb && fb.textContent.includes('Result: completed'); }"
    )
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-runs-body] tr'); return el && el.textContent.includes('succeeded'); }"
    )
    first_rows = page.text_content("[data-runs-body]")

    page.click("#fetch-action")
    page.wait_for_function(
        "() => { const fb = document.querySelector('#action-feedback span:last-child'); return fb && fb.textContent.includes('Result: completed'); }"
    )
    page.wait_for_function(
        "() => { const rows = document.querySelector('[data-runs-body]').querySelectorAll('tr'); return rows.length >= 2; }"
    )
    # Both rows are distinct runs (new run id per action — nothing auto-retried).
    rows = page.text_content("[data-runs-body]")
    assert first_rows != rows
    assert rows.count("succeeded") >= 2
    page.close()


# --- Faza 5: Watch feed + Roster -------------------------------------------------


def test_watch_tab_items_filters_and_links(browser_app: tuple[str, Browser]) -> None:
    """Watch: normalized items with severity labels and counters; the
    severity filter narrows; village names open the explorer history."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    page.click("#tab-watch")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-watch-list] li'); return el; }"
    )
    # The seeded pair: 440 gained villages (info) → warnings first.
    first = page.text_content("[data-watch-list] li")
    assert "WRN" in first or "INF" in first
    counts = page.text_content("[data-watch-counts]")
    assert "440 items" in counts
    assert "0 warnings" in counts and "440 info" in counts
    assert "Comparing 2026-08-07 → 2026-08-08" in page.text_content("[data-watch-range]")

    # Severity filter narrows to zero warnings (all info).
    page.select_option("#analysis-watch-severity", "warning")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-watch-list]'); return el && el.children.length === 0; }"
    )
    assert "0 of 440 items" in page.text_content("[data-watch-counts]")
    page.select_option("#analysis-watch-severity", "")

    # A gained item links to the village explorer history.
    page.click('[data-watch-list] [aria-label^="Open history for Village 10000"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-village-history-table]'); return el && !el.hidden; }"
    )
    page.close()


def test_roster_tab_rows_and_player_detail_link(browser_app: tuple[str, Browser]) -> None:
    """Roster: per-player rows with growth and an alliance filter; the player
    name reuses the player-history detail."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    page.click("#tab-roster")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-roster-body] tr'); return el; }"
    )
    body = page.text_content("[data-roster-body]")
    assert "ENEMY-P219" in body  # top by population (gained village, 1219 pop)
    assert "+1,219" in body  # growth vs the previous snapshot
    assert "440 players on 2026-08-08" in page.text_content("[data-roster-note]")

    # The alliance select narrows to NOVA only.
    page.select_option("#analysis-roster-alliance", "NOVA")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-roster-body] tr'); return el && el.textContent.includes('NOVA-P219'); }"
    )
    assert "220 players on 2026-08-08" in page.text_content("[data-roster-note]")

    # Player row opens the shared player-history detail.
    page.click('[aria-label="Open history for NOVA-P219"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-player-history-table]'); return el && !el.hidden; }"
    )
    assert "NOVA-P219" in page.text_content("#player-detail-name")
    page.close()


def test_roster_export(browser_app: tuple[str, Browser]) -> None:
    """Roster CSV names the snapshot and the alliance."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    page.click("#tab-roster")
    page.wait_for_function(
        "() => !document.querySelector('[data-export=\"roster\"]').disabled"
    )
    with page.expect_download() as download_info:
        page.click('[data-export="roster"]')
    assert "roster-2026-08-08-combined.csv" in download_info.value.suggested_filename
    page.close()


# --- dashboard-report-ux slice -------------------------------------------------


def test_version_header_from_meta(browser_app: tuple[str, Browser]) -> None:
    """The header shows the installed VERSION (v<version>); /api/meta keeps
    both version and build_sha for deployment verification."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.querySelector('[data-header-build]').textContent.startsWith('v')"
    )
    meta = httpx.get(url + "/api/meta").json()
    assert set(meta) == {"version", "build_sha"}
    assert page.text_content("[data-header-build]").strip() == "v" + meta["version"]
    page.close()


def test_log_refresh_with_entries(browser_app_logs: tuple[str, Browser]) -> None:
    """A NON-EMPTY job log renders on the Overview and survives a manual
    Refresh without page errors (the MAX_LOGS ReferenceError regression:
    the old code stranded on undefined MAX_LOGS)."""
    url, browser = browser_app_logs
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const el = document.getElementById('job-log'); return el && el.getAttribute('aria-busy') === 'false'; }"
    )
    assert "snapshot saved for 2026-08-08" in page.text_content("#job-log")
    assert page.text_content("#log-count").strip() == "3 entries"
    assert errors == []

    # Manual Refresh re-renders the same entries and settles to idle.
    page.click("#refresh-dashboard")
    page.wait_for_function(
        "() => document.getElementById('refresh-dashboard').getAttribute('aria-busy') === 'false'"
    )
    assert page.get_attribute("#job-log", "aria-busy") == "false"
    assert "snapshot saved for 2026-08-08" in page.text_content("#job-log")
    assert page.text_content("#log-count").strip() == "3 entries"
    assert errors == []
    page.close()


def test_per_tab_ranges_independent_and_custom(browser_app: tuple[str, Browser]) -> None:
    """Each historical tab owns exactly one range: regions 30 stays 30 while
    changes stays 7; a custom events pair writes ONLY its own URL keys and
    never touches Compare; the first load defaults every tab to 7."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # First load: 7 everywhere, no range keys in the URL.
    assert page.input_value("#range-regions") == "7"
    assert page.input_value("#range-events") == "7"
    assert "range_" not in page.url

    # Regions 30 → its own URL key; the toolbar names the latest pair and
    # the chart caption the selected range (no duplicate "latest" copy).
    page.select_option("#range-regions", "30")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    assert "range_regions=30" in page.url
    assert "Latest snapshot: 2026-08-08 vs previous snapshot" in page.text_content("[data-regions-toolbar]")
    assert "Selected range: 2026-08-07 → 2026-08-08" in page.text_content('[data-as-of="regions"]')

    # Changes stays 7 — the regions choice never leaks across tabs.
    page.click("#tab-changes")
    page.wait_for_function(
        "() => document.getElementById('panel-changes').getAttribute('aria-busy') === 'false'"
    )
    assert page.input_value("#range-changes") == "7"
    page.click("#tab-regions")
    assert page.input_value("#range-regions") == "30"

    # Events custom pair: only its own URL keys appear.
    page.click("#tab-events")
    page.wait_for_function(
        "() => document.getElementById('panel-events').getAttribute('aria-busy') === 'false'"
    )
    page.select_option("#range-events", "custom")
    page.wait_for_function("() => window.location.search.includes('range_events=custom')")
    assert "from_events=2026-08-07" in page.url
    assert "to_events=2026-08-08" in page.url
    assert "from_compare" not in page.url

    # Compare keeps its own 7-day default.
    page.click("#tab-compare")
    page.wait_for_function(
        "() => document.getElementById('panel-compare').getAttribute('aria-busy') === 'false'"
    )
    assert page.input_value("#range-compare") == "7"
    assert "from_compare" not in page.url
    page.close()


def test_history_tables_newest_first(browser_app_dates3: tuple[str, Browser]) -> None:
    """Newest observations render FIRST in the history tables (Changes,
    chart data tables, village history) while the Chart.js payloads stay
    ASC — the table order never contradicts the trend axis."""
    url, browser = browser_app_dates3
    page = browser.new_page()
    page.set_default_timeout(15000)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # Changes table: newest first, oldest last.
    page.click("#tab-changes")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-changes-body] tr'); return el && el.textContent.startsWith('2026-08-'); }"
    )
    rows = page.locator("[data-changes-body] tr")
    assert rows.nth(0).text_content().startswith("2026-08-08")
    assert rows.nth(2).text_content().startswith("2026-08-06")

    # Regions chart data table: newest first; the chart labels stay ASC.
    page.click("#tab-regions")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    page.click("#chart-data-regions summary")
    body_rows = page.locator("#chart-data-regions tbody tr")
    assert "2026-08-08" in body_rows.nth(0).text_content()
    assert "2026-08-06" in body_rows.nth(2).text_content()
    labels = page.evaluate(
        "() => Chart.getChart(document.getElementById('analysis-chart-regions')).data.labels"
    )
    assert labels == ["2026-08-06", "2026-08-07", "2026-08-08"]

    # Village history table: newest first (Village 1 exists all three days).
    page.click("#tab-villages")
    page.fill("#village-search-input", "Village 1")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-villages-table]'); return el && !el.hidden; }"
    )
    page.click('[aria-label="Open history for Village 1"]')
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-village-history-table]'); return el && !el.hidden; }"
    )
    hrows = page.locator("[data-village-history-body] tr")
    assert hrows.nth(0).text_content().startswith("2026-08-08")
    assert hrows.nth(2).text_content().startswith("2026-08-06")
    page.close()


def test_watch_alliance_change_not_conquest(browser_app_watch_change: tuple[str, Browser]) -> None:
    """A same-player alliance switch appears EXACTLY once in Watch as
    alliance_change (info); the Events tab says "Alliance changed to",
    never "conquered"; the real conquest keeps its own copy and no item
    repeats the direction in its message."""
    url, browser = browser_app_watch_change
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url + "?view=intelligence", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # Events: village 3 lost with the SAME player → alliance-change copy.
    page.click("#tab-events")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-events-lost] li'); return el; }"
    )
    lost = page.text_content("[data-events-lost]")
    assert "Alliance changed to ENEMY" in lost
    assert "Village 3" in lost
    assert "conquered" not in lost

    # Watch: exactly one alliance_change item; village 3 is never a
    # village_lost; messages never repeat the tags direction.
    page.click("#tab-watch")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-watch-list] li'); return el; }"
    )
    kinds = page.evaluate(
        "() => Array.from(document.querySelectorAll('.watch-item__kind')).map((el) => el.textContent)"
    )
    assert kinds.count("alliance_change") == 1
    assert "village_lost" not in kinds
    messages = page.evaluate(
        "() => Array.from(document.querySelectorAll('.watch-item__message')).map((el) => el.textContent)"
    )
    assert "Village 3 alliance changed" in messages
    assert "Village 2 conquered" in messages
    assert all("\u2192" not in m for m in messages)
    assert errors == []
    page.close()
