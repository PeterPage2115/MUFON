# Dashboard — Iteracja 2 (naprawy + analityka) — Work Plan

Status: **propozycja do zatwierdzenia** — 2026-08-12, po pełnym przeglądzie kodu + smoke-teście UI + przeglądzie produkcji.

## TL;DR (For humans)

Po przeglądzie dashboardu (459 testów zielonych, wszystkie widoki działają na żywych danych z 2026-08-12) wyłoniły się: 4 naprawy (najważniejsza: edycja harmonogramu w dashboardzie nie przestawia realnego schedulera — tylko wyświetlacz), 4 funkcje analityczne (wyszukiwarka wiosek, war scoreboard, VP graczy, limit Events) i 3 opcjonalne tematy dalsze (smoke UI w CI, lokalizacja PL, oznaczenia dziur). Faza A (naprawy) jest mała i niezależna; faza B (funkcje) buduje na istniejących endpointach `/api/analysis/*` i tabeli `villages`, bez zmian schematu SQLite.

**Czego NIE robimy w tej iteracji**: zmiany schematu DB, zmian embeda Discord, lokalizacji PL, autentykacji (OAuth wraca bez zmian, gdy user zechce), mapy web.

## Scope

**IN (faza A — naprawy):**
- A1. Reschedule APScheduler po `PUT /api/settings` + uczciwy toast.
- A2. Vendoring Chart.js do `/static/vendor/` (koniec z CDN).
- A3. Deltы „Changes" przez dziury w datach: `prev_date` + `gap_days` per wiersz, sufiks „(N d)" w UI.
- A4. Logi z etykietą strefy (UTC) + stan karty Status zależny od błędów („Watching"/„Degraded · N errors").

**IN (faza B — funkcje):**
- B1. Wyszukiwarka wiosek: endpointy `GET /api/villages` + `GET /api/villages/{id}/history`, podzakładka „Villages" (search + tabela + karta szczegółów z wykresem populacji i historią właścicieli), badge stolica/15c/9c, przejście z zakładki Events (klik w nazwę wioski → wyszukiwarka z prefillem).
- B2. War scoreboard: `metrics.conquests_between`, endpoint `GET /api/analysis/wars`, podzakładka „Wars" (wybór from/to + macierz kto-komu + drill-down lista).
- B3. Ranking VP graczy: `PlayerStat.vp` + 4. tabela w zakładce Players.
- B4. Limit + licznik Events: `limit` (default 200, ≤1000) + `total` w payloadzie; UI pokazuje „Showing N of M — export CSV for full".

**OUT:** zmiany w report_embed/strings (raport Discord bez zmian), schema migracje, i18n, mapa web, scraping kontem gry.

## Faza A — naprawy

### A1. Reschedule harmonogramu

**Problem (potwierdzony):** `TravianBot.__init__` (main.py:824-843) tworzy `CronTrigger` z configu startowego; `PUT /api/settings` zapisuje tylko do bazy. UI pokazuje nowy `next_fetch` (z merged config), scheduler odpala po staremu; toast kłamie („will pick up the new schedule on the next run").

**Zmiany:**
- `main.py`: wyciągnąć `_fetch_trigger(cfg) -> CronTrigger` / `_report_trigger(cfg) -> CronTrigger` (jedno źródło prawdy dla startu i reschedule). `TravianBot` przechowuje `scheduler` + `job_ids` (już ma scheduler jako atrybut). Nowa metoda `reschedule()`: czyta merged config (własne połączenie sqlite), porównuje aktualne triggery (`scheduler.get_job(id).trigger`) z nowymi; na różnicę `scheduler.reschedule_job(...)` + wpis `append_log('config', 'info', ...)`.
- `dashboard/app.py`: opcjonalne pole `reschedule_fn: Callable[[], None]` w `DashboardDeps` (wzorzec jak `run_fetch_fn`). `put_settings` po udanym zapisie: `bot_loop_getter().call_soon_threadsafe(reschedule_fn)` (bot loop; APScheduler nie jest thread-safe). Brak funckji (testy/fake) = no-op, bez błędu.
- `main.py::_dashboard_app_factory`: przekazać realny `reschedule_fn`.
- `app.js`: toast po zapisie → „Settings saved. The schedule applies immediately."; usunąć fałszywą obietnicę.

**Testy:** `test_bot_main` — reschedule zmienia trigger jobu (fake scheduler z `get_job/reschedule_job`); brak zmian gdy config identyczny; `test_dashboard_api` — PUT settings wywołuje `reschedule_fn` dokładnie raz (fake rejestrujący), w tym przez `LoopThread`.

**Akceptacja:** zmiana FETCH_HOUR w UI → `scheduler.get_job('fetch').trigger` ma nową godzinę (dowód w smoke), toast bez kłamstwa. Odporność: fetch/report job dalej po starcie bez zmian.

### A2. Vendoring Chart.js

**Problem:** `index.html` ładuje `https://cdn.jsdelivr.net/npm/chart.js@4` — przeczy DESIGN.md §3 („No external font or CDN... consistently offline"); przy braku internetu wykresy znikają.

**Zmiany:** pobrać `chart.umd.min.js` (4.x, wersja przypięta przy implementacji) + plik licencji MIT do `src/travian/dashboard/static/vendor/`; `index.html` → `<script src="/static/vendor/chart.umd.min.js" defer>`; notatka w DESIGN.md §3 (zasób vendored, nie CDN). StaticFiles serwuje katalog bez zmian; obraz Dockera zawiera plik automatycznie.

**Testy:** smoke — wykresy renderują się przy zablokowanym dostępie do internetu (sprawdzenie w browserze z offlinowym profilem lub usuniętym CDN-em).

**Akceptacja:** zero zewnętrznych żądań sieciowych z dashboardu (Network panel w DevTools/playwright).

### A3. Deltы przez dziury w datach

**Problem (dowód z produkcji):** luka 08-02 → 08-08; wiersz 08-08 pokazuje `+62 212 pop` jako zwykły dzienny Δ (dziś prezentowane jak 1 dzień).

**Zmiany:** `analysis.summary_history` — każdy wiersz dostaje `prev_date: str | None` (rzeczywista poprzednia data snapshotu w oknie) i `gap_days: int | None` (dni między datami, None/0 gdy 1 dzień lub brak poprzednika). `app.js::changeRow` — komórki Δ z `gap_days > 1` dostają sufiks „(N d)" (klasa `faint`) + `title="vs {prev_date}"`. Bez zmian w `metrics.compute_deltas` (semantyka delt bez zmian — tylko prezentacja).

**Testy:** `test_analysis` — seria z luką daje `prev_date`/`gap_days` zgodne; wiersz bez poprzednika → `None`.

**Akceptacja:** Changes w produkcji pokazuje „+62,212 (6 d)" dla 08-08; sąsiednie dni bez sufiksu.

### A4. Logi UTC + stan karty Status

**Problem:** `formatTime` (app.js:77) renderuje timestampy UTC w lokalnej strefie przeglądarki bez etykiety; badge „Watching" jest zahardkodowany w HTML.

**Zmiany:** `formatTime` → format w UTC + przyrostek „Z"? **Decyzja:** renderować UTC i dopisać caption w stopce logów „Times are UTC" (spójne z `docker logs`); `renderStatus` — przy `errors.length > 0` dodać klasę `card-state--degraded` i tekst „Degraded · {N} error(s)"; style.css — wariant degraded na `--status-error`. `aria-live` już jest.

**Testy:** smoke UI (seed z błędem → badge degraded; czyste → Watching).

**Akceptacja:** korelacja logów z `docker compose logs` bez arytmetyki stref; stan karty reaguje na błędy.

## Faza B — funkcje

### B1. Wyszukiwarka wiosek

**Kontrakt API:**
- `GET /api/villages?q=<tekst>&alliance=<tag>&date=<YYYY-MM-DD>&limit=50` — szuka w NAJNOWSZYM snapshocie (lub `date`): `name LIKE %q%` OR `player_name LIKE %q%` (case-insensitive dla ASCII; unicode wg SQLite default — akceptowalne v1) OR dokładne współrzędne `x|y` (format gry). Zwraca: `village_id, name, x, y, region, population, player_id, player_name, alliance_id, alliance_tag, is_capital, is_city, is_harbor`. `alliance` filtruje po tagu (walidacja jak w `/api/analysis/*`). Limit 1..200.
- `GET /api/villages/{village_id}/history?days=30` — per snapshot (ostatnie `days`): `date, name, population, player_name, alliance_tag, x, y` (historia przejęć = zmiany gracza/sojuszu widoczne w tabeli).

**Store:** `search_villages(conn, date, q, limit, alliance_ids)` (LIKE, ~6,5 k wierszy — OK bez FTS; FTS5 jako dalsza opcja), `village_history(conn, village_id, days)` (JOIN na `villages` po `village_id`, ORDER BY date DESC LIMIT days). Bez zmian DDL; istniejący indeks `(snapshot_date, alliance_id)` wystarcza.

**UI:** podzakładka „Villages" w Intelligence (tab bar + panel jak inne): input z debounce 300 ms, tabela wyników (name, coords `(x|y)`, player, tag, pop, badge CAP/15c/9c), klik wiersza → karta szczegółów (wykres populacji z Chart.js + tabela historii właścicieli/sojuszy). W Events: nazwa wioski staje się linkiem ustawiającym wyszukiwarkę (stan `analysisState.villageQuery` + przełączenie na zakładkę).

**Testy:** `test_dashboard_api` — match po nazwie/graczu, coords `x|y` (oba znaki `|` i `,`), nieznana data → 422, limit clamp, `alliance` filtr; `test_store` — historia. Smoke UI: wyszukanie istniejącej wioski produkcyjnej.

**Akceptacja:** znalezienie „Gulltown" i „Kozok009" z produkcji; klik z Events → karta wioski; wykres populacji renderuje się offline (vendored Chart.js).

### B2. War scoreboard

**Kontrakt:** `GET /api/analysis/wars?from=&to=&days=…` — semantyka pary dat jak Events (from/to selecty, default ostatnia para). Payload: `{"pairs": [{"from_tag", "to_tag", "villages", "population", "entries": [{village_name, x, y, region, from_player, to_player, population}]}], "deleted": [...]}`.

**Metrics:** nowa funkcja `conquests_between(prev_rows, curr_rows, tracked_ids)` — wioski, których właściciel w `prev` miał `alliance_id` ∈ tracked_ids A i w `curr` ∈ tracked_ids B, A≠B (oba z tracked; reszta ignorowana). `deleted` = były w tracked, zniknęły. To rozszerzenie `village_events`, nie zmiana jego semantyki (endpoint Events nietknięty).

**UI:** podzakładka „Wars": selecty from/to (reuse `analysis-dates`), macierz `atakujący × obrońca` (komórka = liczba wiosek + pop; nasze tagi podświetlone złotem jak w Standings), klik komórki → lista wiosek pod spodem (pattern z Events). CSV export macierzy.

**Testy:** `test_metrics` — para A→B, B→A, w obrębie tego samego tagu (ignorowane), wioska spoza tracked (ignorowana), deleted; `test_dashboard_api` — endpoint + 422 na złe daty.

**Akceptacja:** na produkcji (UFO/PR-U w TRACKED_ALLIANCES) macierz pokazuje realne podboje między sojuszami, klik daje listę.

### B3. Ranking VP graczy

**Zmiany:** `metrics.top_players` — dopisać `vp` do agregacji per gracz (SUM `victory_points` z curr); `models.PlayerStat` + `vp: int | None = None` (opcjonalne, bez psucia starych testów); endpoint `players` dodaje listę `"vp"` (top 10 po vp); UI: 4. tabela „Top by VP" (players-grid 2×2).

**Testy:** `test_metrics` — ranking vp, tie-break player_id (jak inne); `test_dashboard_api` — payload.

**Akceptacja:** VP w zakładce Players zgadza się z SUM z SQL na snapshotcie.

### B4. Limit Events

**Zmiany:** endpoint `events` — `limit` (default 200, ≤1000) + `total` (pełne liczby); UI — render pierwsze `limit`, caption „Showing 200 of 312 — export CSV for the full list" gdy `total > limit`. CSV eksportuje pełny payload (serwer wysyła `total`, lista okrojona — CSV ma to co widoczne; **decyzja:** eksport z pełnych danych = drugi parametr? Nie — prościej: `limit` kroi tylko DOM; serwer zwraca `total` + skróconą listę; CSV eksportuje skróconą + caption; pełny export dostępny po wyższych `limit` z UI selecta (200/500/1000).)

**Testy:** endpoint — `limit` działa, `total` poprawny, 422 poza zakresem.

**Akceptacja:** dzień wojenny z >200 eventami renderuje się płynnie, licznik prawdziwy.

## Faza C — opcjonalne (osobne iteracje)

- C1. Smoke-test UI w CI (Playwright na seedowanej bazie — chroni app.js, dziś bez testów).
- C2. Lokalizacja PL (embedy przez `strings.py` + UI; większy projekt, osobno).
- C3. Wskaźnik dziur dat na osiach wykresów (kropkowana linia) — kosmetyka po A3.

## Pytania otwarte (decyzje do zatwierdzenia)

1. **War scoreboard:** tylko pary z TRACKED_ALLIANCES (rekomendacja) czy też podboje do/z sojuszy spoza listy (np. „każdy kto nam zabrał")?
2. **Wyszukiwarka:** format współrzędnych `x|y` jak w grze — czy też wspierać `x,y` i samą nazwę z cyframi? (rekomendacja: `x|y` + `x,y`, normalizacja spacji.)
3. **Eksport CSV Events przy limicie:** eksport widocznej (skróconej) listy vs pełnej? (rekomendacja: select limitu w UI zamiast dwóch ścieżek.)
4. **A4:** logi renderowane w UTC z captionem — OK, czy wolisz lokalną strefę z etykietą z serwera?
5. **Kolejność:** faza A w całości najpierw, potem B1→B3→B2→B4? Czy B1 przed resztą napraw?
6. **Chart.js:** przypięcie do najnowszej 4.x przy implementacji — OK?

## Verification strategy

- TDD jak w v1: pytest per zmiana (store/metrics/analiza/API), `uv run ruff check`, `uv run basedpyright src` — zielone po każdej fazie.
- QA: smoke UI przeglądarką na seedowanej bazie (wzorzec z tego przeglądu) + po deployu weryfikacja na produkcji przez dashboard (token po rotacji).
- Dowody w `artifact://` jak w dotychczasowym workflow; repo czyste po każdej fazie.

## Ryzyka

- A1: thread-safety APScheduler — reschedule TYLKO przez bot loop (`call_soon_threadsafe`); test z realnym loopem.
- B1: wydajność LIKE na 6,5 k wierszy — trywialna; FTS5 dopiero przy bólu.
- B2: semantyka „conquest" zależy od kompletności snapshotów — dziury w datach mogą skleić dwa podboje w jeden; dokumentujemy („between selected dates").
- A2: wersja chart.js — pin + plik licencji w repo; brak wpływu na testy Pythona.
