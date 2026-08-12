"""Browser smoke tests: the real static UI against a throwaway FastAPI app.

One self-contained fixture: a ``tmp_path`` SQLite with two snapshots (NOVA +
ENEMY with disjoint players and > 200 disjoint gained villages per tag), a
``create_app(DashboardDeps(...))`` in explicit ``DASHBOARD_AUTH_MODE=none``,
uvicorn on a free loopback port, and Playwright Chromium — the server and the
browser are closed regardless of assertion outcome. The test never clicks
Fetch/Report, never reads a production SQLite and never uses a token.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import Browser, Page, sync_playwright

from travian import store
from travian.bot import main as bot_main
from travian.dashboard.app import DashboardDeps, create_app, make_status_provider
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
    db = tmp_path / "browser.db"
    _seed_browser_db(db)
    env = _env(db)
    get_config = _config_getter(db, env)

    async def no_fetch() -> str:
        return "unused"

    async def no_report(channel_id: int, require_today: bool = True) -> str:
        return "unused"

    app = create_app(
        DashboardDeps(
            get_status=make_status_provider(str(db), get_config),
            run_fetch_fn=no_fetch,
            run_report_fn=no_report,
            bot_loop_getter=lambda: None,
            get_config=get_config,
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
