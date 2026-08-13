# AGENTS.md — zasady pracy w tym repozytorium

Jedno źródło zasad dla wykonawców (ludzi i agentów) pracujących w `MUFON`.
Przeczytaj ten plik przed pierwszą zmianą; `ROADMAP.md` pozostaje jedynym
aktywnym planem produktu.

## Kolejność lektury

1. `README.md` — funkcje, konfiguracja env, deployment i rollback.
2. `ROADMAP.md` — aktywny kontrakt produktu: co jest zrobione, co jest P0/P1/P2.
3. `DESIGN.md` — specyfikacja wizualna UI (nie roadmapa).
4. Kod i testy: `src/travian`, `tests`.

## Mapa repozytorium

- `src/travian/bot/main.py` — entrypoint: pętla Discorda, APScheduler,
  `run_fetch`/`run_report`, wiring `DashboardDeps`.
- `src/travian/bot/commands.py`, `src/travian/bot/report_embed.py` — komendy
  i embed raportu Discord.
- `src/travian/dashboard/app.py` — FastAPI: `create_app`, `DashboardDeps`,
  middleware auth, `/healthz`, `/readyz`, `/api/*`.
- `src/travian/dashboard/auth.py` — `SessionStore`, `ActionLimiter`, OAuth helpers.
- `src/travian/dashboard/static/` — vanilla JS + vendored Chart.js (bez CDN).
- `src/travian/store.py` — SQLite/WAL: schema, snapshoty, settings, job log.
- `src/travian/analysis.py`, `metrics.py`, `models.py`, `strings.py`,
  `backfill.py`, `map_sql.py` — dane, metryki, typy i backfill.
- `tests/` — pytest; `-m browser` = Playwright (wymaga Chromium),
  `-m live` = opt-in live smoke (wymaga `DASHBOARD_LIVE_URL`).
- Root: `Dockerfile`, `docker-compose.yml`, `update.sh`,
  `.github/workflows/build.yml` — deployment i CI.

## Weryfikacja (pełny gate przed push)

```bash
uv run pytest -q
uv run ruff check src tests
uv run basedpyright src
uv run pytest tests/test_dashboard_browser.py -m browser -v
```

Playwright wymaga `uv run playwright install chromium` (CI robi to sam).
Testy `-m live` bez `DASHBOARD_LIVE_URL` mają być skipped — nigdy nie łącz
się z produkcją bez jawnego opt-in.

## Zasady bezpieczeństwa i wykonania

- **Zero sekretów** w plikach, commitach, logach i artefaktach. Tokeny/DSN
  tylko przez zmienne środowiskowe (`DASHBOARD_TOKEN`, `DISCORD_TOKEN`, …).
  `secret-scan` w CI jest obowiązkowy.
- **Live smoke jest read-only**: żadnych `PUT /api/settings`, `POST
  /api/actions/*`, fetch/report wobec produkcji. Brak bezpiecznej sesji =
  jawnie niezweryfikowane, nigdy fikcyjny token wobec produkcji.
- **Blokujące I/O poza pętlą bota** (SQLite, httpx, Discord API) odpalamy
  przez `asyncio.to_thread`; nie blokujemy pętli zdarzeń.
- **Scheduler i akcje** dispatchujemy przez pętlę bota (wzorzec
  `bot_loop_getter`); nie uruchamiaj fetch/report poza nią.
- **Kolejność pracy**: najpierw P0/P1 z `ROADMAP.md`, dopiero potem jeden
  slice P2. Nie zaczynaj nowej dużej zakładki przed zamknięciem P0/P1.
- **Push po każdym tasku**: commit scoped do jednego zadania → `git push
  origin main` → czekaj na zielony CI (quality → build-push → smoke →
  secret-scan) przed kolejnym taskiem. Bez force-push; rollback = poprzedni
  `IMAGE_TAG`/SHA.
- **Aktualizuj dokumentację**, gdy zmienia się kontrakt API, zmienne env,
  Docker/deployment lub procedura rollbacku (`README.md`, `ROADMAP.md`,
  `.env.example`).

## Reguły produktu

- UI pozostaje po **angielsku**.
- Frontend pozostaje **vanilla JS + lokalny vendored Chart.js** — bez CDN,
  bez frameworków.
- `ROADMAP.md` jest jedynym aktywnym planem; `DESIGN.md` to specyfikacja
  wizualna; `README.md` to dokumentacja użytkownika/deploymentu.
- Nowe zachowania korzystają z istniejących `run_fetch`, `run_report`,
  `job_log`, `store.list_dates` i `DashboardDeps` — bez drugich ścieżek.
