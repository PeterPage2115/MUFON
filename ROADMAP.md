# Roadmap — travian-discord-report-bot / MUFON

Dokument kontraktu produktowego. Decyzje zapisane tutaj nadają priorytet pracy;
aktualizuj ten plik, gdy decyzja się zmienia. Pełny kontekst techniczny:
`README.md` (funkcje, konfiguracja, deployment) i `DESIGN.md` (system wizualny).

## 1. Decyzje produktowe

| Decyzja | Wybór | Uwagi |
|---|---|---|
| Odbiorca dashboardu | operator + członkowie sojuszu | członek = widok tylko-do-odczytu (intelligence), admin = pełna kontrola |
| Domyślny tryb auth | `token` (operator LAN) | OAuth Discord pozostaje wspieranym, opt-in trybem member/admin (`DASHBOARD_AUTH_MODE=oauth` + kompletne `OAUTH_*`); `none` tylko na loopback bindzie (fail-closed poza nim) |
| Zasięg dostępności | tylko zaufany LAN (compose publikuje `8099:8090`) | brak planów na publiczny internet; auth nie jest zabezpieczeniem klasy edge |
| Historia danych | bez automatycznego pruningu | SQLite/WAL, snapshoty dzienne; najpierw telemetria rozmiaru/wieku i profilowanie, retention jako osobny, bezpieczny task z dry-run i backupem |
| Backup | wymagany przed każdą aktualizacją obrazu | backup SQLite online API; rollback = powrót do poprzedniego obrazu po SHA |
| Identyfikacja buildu | wymagana (`/api/meta`, SHA commita) | bez niej nie da się potwierdzić, co działa na serwerze; wpisuj w deploy log faktyczny SHA z `/api/meta`, nie datę/HEAD |
| Freshness | wymagana obserwowalność wieku danych i luk | `/healthz` = liveness procesu; `/readyz` = readiness; freshness (no_data/current/stale/gap) serwer-derived, nigdy liczona w JS |
| Frontend | vanilla JS + lokalny vendored Chart.js, bez build stepu | framework rewrite jest POZA roadmapą; wejście tylko przez Framework decision gate (§8) po jawnej zmianie reguł repo |
| Kolejność prac | najpierw zaufanie i UX (P0), potem Intelligence i Operations (P1), potem jeden kontrolowany slice P2 | nie zaczynamy nowej dużej zakładki przed zamknięciem P0/P1 |

## 2. Kryteria „ready for daily use"

1. Każdy chroniony endpoint wymaga właściwej sesji (token lub OAuth).
2. Member widzi intelligence + freshness, ale nie: Job log, Settings, Actions, `/api/logs`, raw errors.
3. Admin może odczytać konfigurację i wykonać akcję (fetch/report) po świadomej decyzji; każda akcja ma identyfikowalny `run_id`.
4. Brak snapshotu oraz luka między snapshotami są czytelnie oznaczone (nigdy fałszywy day-over-day); każda porażka odczytu ma persistent state i ścieżkę Retry.
5. Działający obraz daje się jednoznacznie zidentyfikować (SHA), a rollback to powrót do poprzedniego tagu.
6. Backup SQLite można odtworzyć na czystej bazie; procedura jest udokumentowana.
7. Browser smoke (Playwright) pokrywa główne widoki, mobile 375 px, keyboard/a11y i retry/offline; live smoke jest sekret-free i read-only.
8. Pełny zestaw `pytest + ruff + basedpyright` przechodzi w CI przed publikacją obrazu.

## 3. Current truth (stan potwierdzony w kodzie i testach)

Dane, widoki i mechanizmy **wdrożone i objęte testami** — nie są „następnymi" zadaniami.

- **Dane**: parser i składowanie snapshotów `map.sql` (SQLite/WAL), delta engine, backfill z Postgres, retencja bez auto-pruningu, backup/restore (`python -m travian.backup`).
- **Raport Discord**: KPI, Regiony, Standings (3 karty), `/raport`, `/wioski`, `/regiony`; alerty terminalnych porażek przez `ALERT_CHANNEL_ID` (env-only, dedupe per UTC dzień). Regions renderuje się w **kompaktowym układzie mobilnym** (dwie krótkie linie na region, twardy limit 36 znaków na linię — bez poziomego scrolla na Discord mobile); raport dzienny ma limit **top-10** dla regionów i standings (`REPORT_REGIONS` filtruje regiony raportu, nieznane nazwy = warning + fallback top-10).
- **Dashboard — Intelligence**: zakładki `Regions`, `Alliances`, `Players` (pop/growth/VP), `Events` (limit + total), `Wars` (macierz kto-komu, `metrics.conquests_between`), `Changes` (gap-aware), `Villages` (wyszukiwarka + historia); wykresy vendored Chart.js, eksporty CSV, filtr alliance (Combined = suma `ALLIANCE_TAGS` lub pojedynczy tag). Każda zakładka historyczna ma **własny zakres** (`7|30|60|Custom`, klucze URL `range_<tab>`/`from_<tab>`/`to_<tab>`, preferencja `mufon.dashboard.view.v2`); tabele historyczne pokazują **najnowsze obserwacje pierwsze** (dane Chart.js pozostają ASC); wars rozróżnia **conquest od zmiany sojuszu** (`same_player`/`AllianceChangeEvent` — ta sama osoba zmieniająca sojusz nigdy nie jest „conquered").
- **Dashboard — Overview**: Status (freshness, ostatnie udane fetch/report, błędy), Job log (UTC), scheduler sync (zmiana settings przestawia realne triggery APScheduler); Overview jest command center bez powielania tile'ów operational (freshness/last-success żyją tylko w Status).
- **Dashboard — Operations**: Actions (Fetch now / Send report now, rate limit), Settings (walidacja, sekrety nigdy w DB).
- **Auth**: token mode (Bearer, constant-time, domyślny), Discord OAuth opt-in (HttpOnly cookie, RBAC member/admin, rate limit akcji), `none` tylko loopback; public pozostają `/`, `/static/*`, `/healthz`, `/readyz`, `/api/meta`, `/api/auth/*`.
- **Operacyjne**: `/healthz` (liveness) vs `/readyz` (readiness), `/api/meta` z `build_sha`, CI quality → build-push GHCR → smoke → secret-scan, deploy/rollback po `IMAGE_TAG=<sha>`.
- **Wydajność**: dashboard aggregates są SQL-owe (GROUP BY region/player, id-set movement), a payload Regions jest cache'owany per tożsamość snapshotu; pomiar na kopii 60k wiosek × 7 dni: Regions 7d p95 ≈ 4 ms (cache ciepły), Overview p95 ≈ 480 ms — poniżej progu 1,0 s p95.

### Potwierdzone luki (kolejność naprawy w §4–§7)

- Brak otwartych luk P0/P1 — wszystkie pozycje z §4–§6 są wdrożone i objęte
  testami (pytest + browser smoke).
- Pozostałe prace są operacyjne (wdrożenie nowego obrazu serwera +
  weryfikacja OAuth) oraz wyszczególnione w §8 jako świadomie poza planem.
- Freshness/alerts i Watch/Roster **nie dostają drugiej implementacji**:
  istniejące mechanizmy (`compute_freshness`, failure alerts, `analysis_watch`,
  `analysis_roster`) są jedynymi ścieżkami — nowe zachowania wyłącznie je
  rozszerzają.

## 4. P0 — Trust & UX (bieżąca praca; kolejność: kontrakty → stany → wygląd → funkcje)

1. **Bezpieczeństwo**: jawny `DASHBOARD_AUTH_MODE=none` na nie-loopback bindzie kończy proces (fail-closed); `job_health` (`fetch`/`report`: `last_success`/`last_error`/`last_warning`) w `/api/status` dla membera; filtry `job`/`level` w `/api/logs`; rate limit obejmuje `PUT /api/settings`.
2. **Lifecycle danych**: brak background pollingu po initial load; jeden globalny `Refresh dashboard` (po błędzie `Retry dashboard`), refresh-on-view-activation i refresh-after-action; `last_good_load`; stale dane po błędzie pozostają widoczne z datą ostatniego dobrego odczytu; logi `Manual refresh · UTC`.
3. **Stany błędów**: persistent global banner (`Connection issue` + Retry), panelowy `showPanelError(panel, retry)` dla każdej zakładki analiz; `from >= to` czyści listę zamiast przeczyć komunikatowi.
4. **Dostępność**: każdy wykres ma `<details>` `Show data table` (semantyczna tabela z payloadu, `aria-describedby`); region meter jako `role="progressbar"` z widocznym %; mobile 375 px bez poziomego scrolla dokumentu (tabele scrollują lokalnie, Δ % i „To 50%" nie znikają); keyboard po obu tablistach.
5. **Poprawki stanu**: `renderAllianceFilter` odtwarza przyciski po zmianie tagów i resetuje filtr do `combined`; `settingsFromForm` czyta canonical `REPORT_EMBED_COLOR_TEXT` i blokuje PUT dla złego hex; pusty picker standings zostaje widoczny z komunikatem.
6. **Sekrety**: token dashboardu w `sessionStorage` (nie `localStorage`), OAuth cookie HttpOnly; brak storage nie blokuje dashboardu.
7. **Struktura kodu**: monolityczny `app.js` (3037 linii) dzielony na natywne ES modules (`api`, `auth`, `status`, `analysis`, `operations`, `ui`) bez bundlera; jeden `type="module"` w `index.html`.

## 5. P1 — Intelligence (po P0)

- `GET /api/analysis/overview` (days 2..60, alliance) — freshness, summary current/previous/delta, top-8 regionów, top-5 playerów, movement; zero snapshotów = 200 null/empty, jeden snapshot = brak syntetycznej delty.
- `GET /api/analysis/compare?from=&to=&alliance=` — pary dat z `elapsed_days`, `share_delta`, `pop_delta`, `movement`; jawna nieznana data / `from >= to` = 422 z listą valid dates.
- `GET /api/analysis/players/{id}/history` — agregacja po stabilnym `player_id`; 404 dla nieznanego, `present_in_latest` dla historycznego.
- `GET /api/analysis/regions/{region}/villages?date=&alliance=&limit=200` — lista wiosek regionu z `side: tracked|other`, najnowsza data domyślnie.
- Eksporty CSV Players / Standings / Deltas (klient-side, disabled przy pustym payloadzie); Events/Wars zachowują jawny limit — skrócony eksport nigdy nie jest nazywany „full".
- Zakresy per zakładka (zamiast wspólnego context bara): każda zakładka historyczna (`regions`, `alliances`, `changes`, `events`, `wars`, `compare`, `watch`) ma własny `7|30|60|Custom` (custom = para From/To z `GET /api/analysis/dates`); `7` = ostatnie 7 dostępnych dat snapshotów; `from >= to` czyści widok i pokazuje błąd bez requestu; stan w URL (`range_<tab>`/`from_<tab>`/`to_<tab>`) + lokalna preferencja `mufon.dashboard.view.v2`; niepoprawne wartości → bezpieczny default 7 bez request loop.
- Wydajność: agregacje w SQL (`GROUP BY snapshot_date, region, alliance_tag`), próg p95 Regions/Overview ≤ 1,0 s na seedzie 60k×7; przekroczenie = SQL aggregation/cache per `(db_path, snapshot_date, days, alliance)` w tej samej fazie.

## 6. P1 — Operations (po P0; kontrakty równoległe z §5)

- Additive table `job_runs` (`CREATE TABLE IF NOT EXISTS`, indeks `(started_at, job)`), statusy `pending|running|succeeded|skipped|failed|timed_out`; istniejące DB aktualizują się bez kasowania snapshotów/logów.
- `run_fetch(run_id=None)` / `run_report(..., run_id=None)` — wspólny kontrakt bez drugiej ścieżki danych; źródła runów `scheduler` / `discord` / `dashboard`; lock `_get_run_lock` nadal skipuje konkurencyjny run (nigdy nie kolejkuje).
- Akcje zwracają `{"status","run_id","message"}`; timeout 504 zwraca `run_id` (`asyncio.shield` zostaje — run może skończyć się później); `GET /api/operations/runs` + `GET /api/operations/runs/{run_id}` (admin-only, 404 unknown, limit 1..200).
- Operations UI: potwierdzenie `Send report now` (kanał + snapshot date), karta aktywnego runu z ograniczonym pollingiem **tylko podczas jawnie uruchomionej akcji**, historia runów z filtrami job/status, manual `Retry` = nowy run ID. Bez automatycznego retry i bez dodatkowego Discord reportu bez świadomego kliknięcia.
- Telemetria storage: `store.database_stats` (`db_size_bytes`, `snapshot_count`, `oldest_snapshot_date`, `latest_snapshot_date`) — informacyjnie; auto-prune poza zakresem do osobnego tasku z dry-run, backupem i ochroną dwóch najnowszych snapshotów.

## 7. P2 — Watch & roster (jeden kontrolowany slice po P0/P1)

- [x] `GET /api/analysis/watch?from=&to=&alliance=&limit=200` — znormalizowany `items[]` (`kind`: village_gained/lost/conquest/deleted/alliance_change, `severity`: info/warning, daty, village_id, tagi, population, message); reuse `village_events` + `conquests_between` + `alliance_changes_between`, zero nowych alertów, zero Discord sends; mniej niż 2 snapshoty = 200 empty.
- Zakładka `Watch`: filtry severity/kind (w tym `alliance_change`), liczniki, linki do Village/Region/Player detail; stany no-data/gap jawne; kolor nigdy jedynym sygnałem.
- [x] `GET /api/analysis/roster?date=&alliance=&limit=200` — SQL aggregate po `player_id` (`player_id`, `player_name`, `alliance_tag`, `villages`, `population`, `vp`, `growth`); klik gracza → player history; brak listy wiosek w roster row.
- [x] **Follow-up (dashboard-report-ux)**: raport dzienny ograniczony do top-10 regionów i standings z opcjonalnym `REPORT_REGIONS` (exact snapshot names, max 10, empty = top-10; nieznane nazwy → warning joba + fallback); `GET /api/analysis/standings` zwraca `available_tags` jako top-10 wg populacji na końcu zakresu + `available_total`/`available_truncated`; **uczciwe rozróżnienie conquest od zmiany sojuszu**: `VillageEvent.same_player` + `metrics.alliance_changes_between` (`player_id` bez zmiany = `alliance_change`, nigdy conquest/village_lost; wars/macierz liczy tylko realne przejęcia).
- Po tym slice'u nie zaczynamy interaktywnej mapy ani oficjalnych statusów regionów — snapshot map data nie daje `locked/contested/secured`; następny kierunek wymaga osobnego kontraktu.

## 8. Świadomie NIE w planie (YAGNI) + Framework decision gate

- Publiczny dostęp przez internet (wymagałby proxy, TLS i innej klasy auth).
- Automatyczny pruning danych przed pomiarem retencji i osobno zatwierdzonym taskiem.
- **Framework rewrite frontendu** (React/Svelte/Vue/Alpine/Preact/Lit): poza bieżącą roadmapą. Obecne wady są błędami lifecycle/render/state, nie problemem vanilla JS; `AGENTS.md` wymaga vanilla bez frameworków, `DESIGN.md` zakłada self-contained/no-build, CI/Docker nie mają Node toolchaina.
  - Wejście wyłącznie przez **Framework decision gate**: osobny, zatwierdzony task po zamknięciu P0/P1, wymagający (a) jawnej zmiany reguły `AGENTS.md` i akceptacji Node build w CI/Docker, (b) równoległego spike'a jednego widoku, (c) pixel/ARIA parity, (d) offline vendored Chart.js, (e) p95 Regions 7d ≤ 1,0 s, (f) rollback starego asset bundle po SHA. Bez tych dowodów pozostaje modular vanilla.
- Wsparcie wielu serwerów Travian w jednym procesie.
- Lokalizacja PL UI (UI pozostaje po angielsku), interaktywna mapa, oficjalne statusy regionów, scraping kontem gry, dane w czasie rzeczywistym.

## 9. Quality gate

Przed każdym push i przed wydaniem (z katalogu repo):

```bash
uv run pytest -q
uv run ruff check src tests
uv run basedpyright src
uv run pytest tests/test_dashboard_browser.py -m browser -v
```

Po wdrożeniu obrazu — wyłącznie read-only live smoke:
`DASHBOARD_LIVE_URL` + opcjonalnie `DASHBOARD_TOKEN` wobec `tests/test_dashboard_live.py -m live`;
`DASHBOARD_EXPECTED_SHA` weryfikuje `/api/meta`. Nigdy live `PUT /api/settings` ani `POST /api/actions/*`.

Deploy: commit scoped do jednego zadania → `git push origin main` → zielony CI → `IMAGE_TAG=<sha> ./update.sh`; rollback = poprzedni SHA. Backup przed każdą aktualizacją.
