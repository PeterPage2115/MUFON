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


@pytest.fixture()
def browser_app(tmp_path: Path) -> Generator[tuple[str, Browser], None, None]:
    """Auth-free dashboard (existing scenarios): DASHBOARD_AUTH_MODE=none."""
    yield from _browser_app_with_auth(tmp_path, auth_mode="none", token="")


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


def _seed_empty_db(db: Path) -> None:
    """Schema only, no snapshots — the empty/no-data contract."""
    conn = store.connect(db)
    store.init_schema(conn)
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
) -> Generator[tuple[str, Browser], None, None]:
    db = tmp_path / "browser.db"
    seed(db)
    env = _env(db)
    env["DASHBOARD_AUTH_MODE"] = auth_mode
    env["DASHBOARD_TOKEN"] = token
    if auth_mode == "token":
        # The middleware decides auth from env: with a loopback bind the
        # legacy heuristic resolves to "none" and the token is never checked.
        # A non-loopback bind (compose-style) makes the token mode real.
        env["DASHBOARD_BIND"] = "0.0.0.0"
    get_config = _config_getter(db, env)

    async def no_fetch() -> str:
        return "completed"

    async def no_report(channel_id: int, require_today: bool = True) -> str:
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
    port = _free_port()
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
    page.goto(url, wait_until="domcontentloaded")
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
    """Overview tab: panel switching, KPI tiles and the admin job log render."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    # Intelligence is the initial view; Overview starts hidden.
    assert page.get_attribute("#dashboard-panel-intelligence", "hidden") is None
    assert page.get_attribute("#dashboard-panel-overview", "hidden") is not None

    page.click("#dashboard-tab-overview")
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-status-value=\"villages\"]'); return el && el.textContent.trim() !== '—'; }"
    )
    assert page.get_attribute("#dashboard-panel-overview", "hidden") is None
    assert page.get_attribute("#dashboard-panel-intelligence", "hidden") is not None
    assert page.get_attribute("#dashboard-tab-overview", "aria-selected") == "true"

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

    # Back to Intelligence: the panel pair flips again.
    page.click("#dashboard-tab-intelligence")
    assert page.get_attribute("#dashboard-panel-intelligence", "hidden") is None
    assert page.get_attribute("#dashboard-panel-overview", "hidden") is not None
    assert errors == []
    page.close()


def test_analysis_tabs_load_without_console_errors(browser_app: tuple[str, Browser]) -> None:
    """Alliances/Changes/Players tabs settle to aria-busy=false without JS errors."""
    url, browser = browser_app
    page = browser.new_page()
    page.set_default_timeout(15000)
    errors = _collect_page_errors(page)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )

    for tab_id in ("tab-alliances", "tab-changes", "tab-players", "tab-regions"):
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
    page.goto(url, wait_until="domcontentloaded")
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
    page.goto(url, wait_until="domcontentloaded")
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
    page.goto(url, wait_until="domcontentloaded")

    # Regions is the default tab: it settles and shows the no-data message.
    page.wait_for_function(
        "() => document.getElementById('panel-regions').getAttribute('aria-busy') === 'false'"
    )
    assert page.get_attribute("#panel-regions", "aria-busy") == "false"
    assert "No data yet." in page.text_content("#panel-regions")
    assert page.get_attribute("#panel-regions", "class").count("is-empty") == 1

    # Other analysis tabs settle too.
    for tab_id in ("tab-alliances", "tab-players", "tab-events", "tab-changes"):
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
    page.goto(url, wait_until="domcontentloaded")
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
    assert errors == []
    page.close()
