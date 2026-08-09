# travian-discord-report-bot - Work Plan

## TL;DR (For humans)

Codzienny bot Discord raportujący stan sojuszu na serwerze Travian: Legends „Community Week x2 The Eternal War" (https://cw.x2.international.travian.com) + lokalny dashboard konfiguracyjny.

- **Co dostaniesz**: raz dziennie (domyślnie 09:00 Europe/Warsaw) na kanale Discord embed z raportem po angielsku: podsumowanie z deltami (wioski, populacja, gracze, punkty zwycięstwa), nowe i utracone wioski (nazwa, współrzędne, gracz / nowy właściciel), top gracze, tabela regionów (nasza populacja, udział %, delta) i VP. Dodatkowo komenda `/raport` (tylko admini) do ręcznego testu oraz **lokalny dashboard** (`127.0.0.1:8090`, ładny, ciemny motyw) do podglądu statusu, edycji ustawień (sojusze, godziny, kanał) i przycisków „Fetch now" / „Send report now".
- **Dlaczego tak**: dane z publicznego `map.sql` (codzienny snapshot o północy czasu serwera — zweryfikowane, bez logowania); historia w SQLite na wolumenie Dockera; baza kolegi (Supabase) tylko opcjonalnie, read-only, jako backfill. Python 3.12 + discord.py — najmniejsze zależności, jeden obraz Docker, łatwa aktualizacja (`docker compose pull && up -d`).
- **Czego NIE zrobi**: nie ma mapy web, obrazków w raporcie, linkowania mapy kolegi, dashboardu z wykresami, zapisu do bazy kolegi, scrapingu kontem gry (ToS), rozkładu plemion, komend poza `/raport`. Oficjalny status regionów (locked/contested/secured) nie jest dostępny publicznie — raport pokazuje wyliczony udział populacji w regionach.
- **Wysiłek**: ~16 zadań w 5 falach + fala weryfikacyjna (F1–F4). **Ryzyko**: awaria pobierania map.sql (retry + log do dashboardu, raport dnia pomijany), zmiana formatu dumpa przez Traviana (parser waliduje i loguje ostrzeżenia), token w repo (skan sekretów w CI), rezygnacja z mapy = brak wizualizacji (decyzja użytkownika).
- **Decyzje**: zakres zatwierdzony przez użytkownika 2026-08-09 (patrz `.omo/drafts/travian-discord-report-bot.md`).
- **Dalszy rozwój**: sekcja „Future roadmap" w tym planie (alerty, intel, historia, dashboard sojuszowy v2) — architektura v1 świadomie przygotowana pod te kierunki, bez zmian w zadaniach v1. Stack potwierdzony 2026-08-09: Python (backend) + JS (frontend v2, niezależny wybór).

## Scope

**IN**
- Codzienne pobieranie i parsowanie `https://cw.x2.international.travian.com/map.sql` (00:15 Europe/London), snapshoty w SQLite (`/data/travian.db`, wolumen Dockera).
- Silnik metryk: delty wioski/populacja/gracze/VP vs poprzedni dzień (po `alliance_id` — odporność na zmianę tagu sojuszu), nowe/utracone (podział: zdobyte przez kogoś vs usunięte), top gracze (populacja / przyrost / nowe wioski), regiony (nasza populacja, udział % w regionie, delta; top-5 sojuszy liczone w `region_stats` — dane dla v2, w v1 embed niewyświetlane), suma VP.
- Bot discord.py: codzienny raport (EN embed, bloki: Summary, New Villages, Lost Villages, Top Players, Regions, Victory Points), komenda `/raport` (admin-only: `manage_guild` lub rola z `ADMIN_ROLE_ID`), APScheduler, ustawienia = env z nadpisaniem przez tabelę `settings` w SQLite.
- Lokalny dashboard (FastAPI w tym samym procesie co bot, bind `127.0.0.1:8090`): status (ostatni snapshot, liczby, następny fetch/raport), edycja ustawień, przyciski Fetch now / Send report now (te same funkcje co joby), log zdarzeń/błędów. **UI ma być dopracowane** (ciemny motyw, karty, walidacja formularzy, toasty) — wymaganie użytkownika.
- Backfill (opcjonalny): `python -m travian.backfill` czyta bazę kolegi (read-only, `BACKFILL_DSN`) i importuje dostępne daty snapshotów; brak dostępu = czyste wyjście i komunikat.
- Deployment: repo GitHub (prywatne: https://github.com/PeterPage2115/MUFON.git), jeden obraz `python:3.12-alpine` na GHCR (`ghcr.io/peterpage2115/mufon`), GitHub Actions build+push, `docker-compose.yml`, `update.sh`, `.env.example`, README z instrukcją setupu Discorda.

**OUT / Must-NOT-Have**
- Mapa web, obrazki w raporcie, linki do mapy kolegi, dashboard analityczny (wykresy/trendy), zapis do bazy kolegi, scraping kontem gry (ToS), rozkład plemion, inne komendy Discord poza `/raport`, deployment nie-Dockerowy.

**Config surface (env → tabela `settings` nadpisuje; token NIGDY w settings)**
| klucz env | default | opis |
|---|---|---|
| DISCORD_TOKEN | — | token bota (tylko env, sekret) |
| CHANNEL_ID | — | kanał raportu dziennego |
| ALLIANCE_TAGS | — | lista tagów oddzielona przecinkami |
| FETCH_HOUR / FETCH_MINUTE / FETCH_TZ | 0 / 15 / Europe/London | pora pobierania map.sql |
| REPORT_HOUR / REPORT_MINUTE / REPORT_TZ | 9 / 0 / Europe/Warsaw | pora raportu |
| ADMIN_ROLE_ID | — | rola uprawniająca do `/raport` (opcjonalna) |
| SQLITE_PATH | /data/travian.db | ścieżka bazy |
| DASHBOARD_BIND / DASHBOARD_PORT / DASHBOARD_TOKEN | 127.0.0.1 / 8090 / — | bind wewnątrz kontenera — w compose ustaw `0.0.0.0` (loopback-only gwarantuje mapping `127.0.0.1:8090:8090` po stronie HOSTA); middleware wymaga DASHBOARD_TOKEN gdy `bind != loopback` ORAZ `DASHBOARD_LOOPBACK_ONLY != true` |
| DASHBOARD_LOOPBACK_ONLY | false | w compose ustaw `true` — Docker z loopback-only publish'em hosta NIE wymaga tokenu mimo bindu 0.0.0.0 w kontenerze |
| REPORT_EMBED_COLOR | 0x2ECC71 | kolor embeda |
| BACKFILL_DSN | — | opcjonalny, read-only |

**Data contracts**
- map.sql: linie `INSERT INTO \`x_world\` VALUES (id,x,y,tribe,village_id,'name',player_id,'player_name',alliance_id,'alliance_tag',population,'region',capital,city,harbor,vp);` — 16 pól, region może być NULL→"", nazwy z encjami HTML (`&#39;`) i unicode, `''` wewnątrz stringów, TRUE/FALSE. Zweryfikowane na żywo 2026-08-08.
- SQLite DDL (typy; definitywny DDL w `init_schema` zad. 4): `snapshots(snapshot_date TEXT PK, created_at TEXT, source TEXT)`; `villages(snapshot_date TEXT, village_id INTEGER, x INTEGER, y INTEGER, tribe INTEGER, name TEXT, player_id INTEGER, player_name TEXT, alliance_id INTEGER, alliance_tag TEXT, population INTEGER, region TEXT NULL, is_capital INTEGER 0/1, is_city INTEGER 0/1, is_harbor INTEGER 0/1, victory_points INTEGER, PK(snapshot_date, village_id))` + indeksy `(snapshot_date, alliance_id)`, `(snapshot_date, region)`; `settings(key TEXT PK, value TEXT — JSON, updated_at TEXT)`; `job_log(id INTEGER PK AUTOINCREMENT, ts TEXT, job TEXT, level TEXT, message TEXT)`. WAL mode. `snapshot_date` = data północy serwera wg `FETCH_TZ` (zad. 10).
- Limity embeda Discord: wartość pola ≤1024 znaki; ≤25 pól ŁĄCZNIE. **Struktura pól (pin)**: Top Players = 3 OSOBNE pola (Population / Growth / New Villages) → **7 pól stałych** (Summary, New Villages, Lost Villages, 3× Top Players, Victory Points) + Regions; Regions ≤18 pól (7 + 18 = 25), nadmiar → „…and N more". Każde rozdzielenie pola >1024 znaków zwiększa licznik pól stałych o 1 — budżet 25 MUSI się domknąć: `Regions cap = 25 − liczba pól stałych po splicie` (twarda asercja `len(embed.fields) <= 25` w teście; fixture wymusza co najmniej DWA splity bloków stałych). Dodatkowy limit Discorda: ŁĄCZNA długość embeda ≤6000 znaków (nazwa pola ≤256; limit obejmuje też description i footer — `len(embed)` w discord.py sumuje nazwy pól + wartości + description + footer) — Regions budżetowane także po znakach: `Regions chars ≤ 6000 − len(fixed_after_splits) − len(description) − len(footer) − 512` (margines ≥512 na nazwy pól i separatory); asercja `len(embed) <= 6000` w teście, fixture pokrywa OBA limity.

## Verification strategy

- **TDD (pytest)** dla: parsera (fixture z PRAWDZIWYMI liniami z serwera cw.x2 — w tym `King&#39;s Landing`, unicode, NULL, TRUE/FALSE), store (tmp SQLite, idempotencja upsert), metryk (ręcznie zbudowane snapshoty, edge cases: zmiana tagu sojuszu, wioska zdobyta vs usunięta, brak poprzedniego snapshotu), buildera embeda (struktura, limity 1024/25), API dashboardu (TestClient, walidacja ustawień).
- **QA agentowe (happy + failure)** każdego zadania z dowodem: pytest green, uruchomienie bota na serwerze testowym Discorda z realnym raportem, playwright dla dashboardu (funkcjonalnie + wizualnie), `docker compose build` + healthcheck, skan sekretów.
- **Umiejętności do załadowania przez wykonawcę**: `/programming` (cały kod Python), `/frontend` + `/visual-qa` (zadanie dashboardu — wymóg „ładnie i sensownie"), `/playwright` (QA przeglądarkowe).
- **Staging**: serwer/testowy kanał Discord; raport walidowany przez porównanie z surowymi danymi snapshotu (liczby w embedzie == agregacja z SQL).

## Execution strategy

Fale (kolejność wymuszona zależnościami: 2←1, 3←2, 4←3, 5←4; zależności taskowe: 3←2, 4←3, 5←4, 6←4, 7←6, 8←7, 11←9, 13←12):
1. **Repo + warstwa danych** (zad. 1–5): scaffold, modele, parser, store, backfill CLI.
2. **Metryki + raport** (zad. 6–8): delty, regiony/top, assembly + embed builder.
3. **Bot** (zad. 9–11): scheduler + joby + `/raport`.
4. **Dashboard** (zad. 12–13): API + dopracowane UI.
5. **Deployment** (zad. 14–16): Docker, GHCR CI, update.sh/README.
Final verification wave F1–F4 po wszystkim.

Struktura repo:
```
pyproject.toml, uv.lock, .gitignore, .env.example, README.md, Dockerfile,
docker-compose.yml, update.sh, .github/workflows/build.yml,
src/travian/{__init__,models,map_sql,store,metrics,strings,backfill}.py
src/travian/bot/{__init__,main,report_embed,commands}.py
src/travian/dashboard/{__init__,app}.py
src/travian/dashboard/static/{index.html,style.css,app.js}
tests/{test_models,test_map_sql,test_store,test_metrics,test_backfill,test_report_embed,test_dashboard_api}.py
tests/fixtures/{map_sql_sample.txt}
```
Dashboard działa W TYM SAMYM procesie co bot (uvicorn w wątku tła; przyciski wywołują te same funkcje jobów; bootstrap wykonuje zad. 12) — brak IPC, jedna usługa w compose z `ports: "127.0.0.1:8090:8090"`. Akcje dashboardu (POST /api/actions/*) wywołują `run_fetch`/`run_report` przez `asyncio.run_coroutine_threadsafe(..., bot_loop)` (wątek uvicorna → pętla bota) — `channel.send` MUSI wykonywać się na pętli discord.py. `settings` nadpisuje env; bot czyta ustawienia przed każdym jobem; `DISCORD_TOKEN` tylko z env.

**Roadmap alignment** (decyzje v1 służące v2, bez zmian zadań): wspólny rdzeń `travian/` (parser/store/metryki) jest jedynym dostawcą danych dla bota i przyszłego dashboardu; snapshoty per-dzień = fundament trendów; śledzenie po `alliance_id` = odporność na zmianę tagu; `region_stats` liczy `region_total_pop` i top-5 sojuszy = wejściówki planera osiedlania (v2); settings JSON = rozszerzalność bez restartu; `village_events` z zad. 6 (liczone ze snapshotów, bez osobnej tabeli) = baza alertów (v1.1) — alerty przeliczają je ze snapshotów tym samym silnikiem.

## Todos

### Wave 1 — Repo + data layer

- [ ] 1. Init repo: `git init`, `.gitignore` (`.env`, `data/`, `__pycache__/`, `.venv/`), `pyproject.toml` (uv, python>=3.12, deps: discord.py>=2.3, APScheduler>=3.10 — CronTrigger z `zoneinfo`, httpx, pydantic>=2, fastapi, uvicorn, asyncpg>=0.29 — musllinux wheels dla python:3.12-alpine, tzdata (gwarantuje `ZoneInfo` na alpine bez systemowej bazy stref); dev: pytest, ruff, basedpyright), layout `src/travian/...` jak wyżej, pusty README stub, `.env.example` z komentarzami (tabela Config surface powyżej).
  - References: `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`
  - Acceptance: `uv sync` kończy się sukcesem; `uv run ruff check .` i `uv run basedpyright src` przechodzą na pustym drzewie; `uv run pytest` działa (0 testów).
  - QA happy: wszystkie komendy z Acceptance wykonane bez błędów (dowód: output w terminalu). QA failure: brak `uv`/zły python → komunikat w README („zainstaluj uv"); nie dotyczy testu.
  - Commit: `chore: scaffold uv project`
- [ ] 2. `src/travian/models.py`: pydantic v2 — `VillageRow` (16 pól jak w Data contracts, region: str|None), `SnapshotDates`, `DeltaSummary` (villages/population/players/vp + deltas), `VillageEvent` (village, event: gained|lost_conquered|lost_deleted, new_owner_tag/player, old_player), `PlayerStat`, `RegionStat` (region, our_villages, our_pop, region_total_pop, share, delta), `ReportData` (wszystkie bloki).
  - References: `src/travian/models.py`, `tests/test_models.py` (nowy)
  - Acceptance: modele budują się z danych fixture; `ReportData` kompletny dla fixture.
  - QA happy: test konstrukcji + serializacji. QA failure: zły typ pola → `ValidationError` z czytelnym komunikatem.
  - Commit: `feat: typed models`
- [ ] 3. `src/travian/map_sql.py`: `fetch_map_sql(url) -> str` (httpx, timeout 60s, retry 3 z backoffem 2/5/10s) i `parse_map_sql(text) -> list[VillageRow]` — przetwarza tylko linie `INSERT INTO \`x_world\``, poprawnie parsuje stringi z `''`, encjami HTML (`html.unescape`) i unicode, TRUE/FALSE→bool, NULL→None; linie uszkodzone: `logging.warning` + pominięcie (nie crash); dedupe po `village_id` (ostatni wygrywa); walidacja liczby pól (16) z ostrzeżeniem.
  - References: `src/travian/map_sql.py`, `tests/test_map_sql.py`, `tests/fixtures/map_sql_sample.txt`
  - Acceptance: testy TDD z `tests/fixtures/map_sql_sample.txt` (wklejone REALNE linie z serwera cw.x2 z 2026-08-08, w tym `King&#39;s Landing`, nazwy unicode, NULL, TRUE/FALSE, VP>0) — parsowanie == oczekiwane dict; test fetch z mockiem httpx (sukces i 3 retry po błędzie).
  - QA happy: pytest zielony (dowód: `uv run pytest tests/test_map_sql.py -v`). QA failure: linia `INSERT ... VALUES (1,2,3);` (za mało pól) → warning w logu, reszta pliku sparsowana.
  - Commit: `feat: map.sql parser with tests`
- [ ] 4. `src/travian/store.py`: połączenie sqlite3 (WAL; nowe połączenie PER OPERACJA — `check_same_thread`; wątek uvicorna i pętla bota nie współdzielą połączeń), `init_schema(conn)`, `save_snapshot(conn, date, rows)` (upsert per (date, village_id); transakcja; rekord w `snapshots` przez `INSERT OR REPLACE` — powtórny fetch tego samego dnia NIE może rzucić IntegrityError na PK `snapshot_date`), `load_villages(conn, date)`, `list_dates(conn)`, `load_latest(conn)`, `get_settings(conn)/set_settings(conn, kvs)` (JSON-owa wartość, walidacja typów), `append_log(conn, job, level, message)`, `recent_logs(conn, n)`.
  - References: `src/travian/store.py`, `tests/test_store.py`
  - Acceptance: TDD na tmp SQLite — save/load roundtrip, upsert idempotentny (2× save TEJ SAMEJ daty = 1 wiersz w `snapshots` i 1× wiersze w `villages`, bez IntegrityError), load_latest zwraca najnowszą datę, settings set/get, logi w kolejności.
  - QA happy: pytest zielony. QA failure: zapis tej samej daty z innymi danymi → nadpisanie bez duplikatów (test).
  - Commit: `feat: sqlite snapshot store`
- [ ] 5. `src/travian/backfill.py`: CLI `python -m travian.backfill` — łączy się z `BACKFILL_DSN` (psycopg2/asyncpg — wybierz asyncpg), introspekcja `information_schema` w poszukiwaniu tabeli z kolumnami `_date` i `village_id` (np. `village_snapshot`), pobiera DISTINCT `_date`, strumieniuje wiersze (batche 5000) → `save_snapshot` do SQLite; mapowanie: `village_id,x,y,tribe,village_name,player_id,player_name,alliance_id,alliance_tag,population,region,isCapital,isCity,isHarbor,victory_points,_date` (UWAGA: to nazwy kolumn ŹRÓDŁOWYCH — baza kolegi, camelCase; DDL docelowy jest snake_case `is_capital`/`name` — mapowanie tłumaczy). Flaga `--dry-run`: wypisuje znalezione tabele i daty bez zapisu. Po introspekcji WALIDUJ komplet wymaganych kolumn (lista mapowania niżej) — brak którejś → czytelny komunikat + skip + exit 0 (bez KeyError). Brak DSN / brak tabeli / błąd połączenia: czytelny komunikat + exit 0 (bot nie zależy od backfillu).
  - References: `src/travian/backfill.py`, `tests/test_backfill.py` (nowy), `src/travian/store.py` (save_snapshot)
  - Acceptance: jednostkowy test mapowania wiersza dict→`VillageRow`; CLI z pustym DSN wypisuje „BACKFILL_DSN not set, skipping" i exit 0; `--dry-run` nie pisze do SQLite.
  - QA happy: `BACKFILL_DSN=... uv run python -m travian.backfill --dry-run` pokazuje znalezione tabele i daty bez zapisu (jeśli dostęp do bazy kolegi — inaczej test na mocku). QA failure: zły DSN → komunikat błędu, exit 0, brak śladów w SQLite; tabela znaleziona ale bez wymaganej kolumny → czytelny komunikat + exit 0 (test).
  - Commit: `feat: optional supabase backfill cli`

### Wave 2 — Metrics + report

- [ ] 6. `src/travian/metrics.py` — delty: `compute_deltas(prev_rows, curr_rows, alliance_ids) -> DeltaSummary` + `village_events(...)`. Sojusz identyfikowany po `alliance_id` (tagi z configu rozwiązane do id z bieżącego snapshotu — odporność na zmianę tagu; lista tagów normalizowana: `strip()` + dedupe, jak w zad. 12). Tag z `ALLIANCE_TAGS` NIEOBECNY w bieżącym snapshotcie (sojusz rozwiązany/nie istnieje): `logging.warning` + `append_log` z listą nierozwiązywalnych tagów; raport budowany z rozwiązanego podzbioru (nie zerowy, nie crash); jeśli ŻADEN tag się nie rozwiąże → traktowane jak puste `ALLIANCE_TAGS` (warning + raport dzienny pomijany). `ALLIANCE_TAGS` puste/nieustawione → `append_log(warning 'no alliance configured')` + raport dzienny pomijany; `/raport` (admin) nadal dozwolone. Zdarzenia: gained (village_id tylko w curr), lost_conquered (w prev-our, w curr-inny właściciel → new_owner_tag/new_player), lost_deleted (w prev-our, brak w curr).
  - References: `src/travian/metrics.py`, `tests/test_metrics.py`, `src/travian/models.py` (DeltaSummary/VillageEvent)
  - Acceptance: TDD na ręcznych snapshotach: wszystkie 3 typy zdarzeń, delty liczb (wioski/populacja/gracze/VP), sojusz z wieloma tagami→jednym id, zmiana tagu między dniami NIE generuje gained/lost.
  - QA happy: pytest zielony. QA failure: pusty prev (pierwszy dzień) → delty None/„—" bez crasha (test); tag z ALLIANCE_TAGS nieobecny w snapshotcie → warning w logu, raport z rozwiązanego podzbioru (test); żaden tag nierozwiązany → jak puste ALLIANCE_TAGS (warning + skip dzienny) (test); ALLIANCE_TAGS puste → warning + skip raportu dziennego (test).
  - Commit: `feat: delta engine`
- [ ] 7. `src/travian/metrics.py` — regiony i top gracze: `region_stats(prev_rows, curr_rows, alliance_ids) -> list[RegionStat]` (regiony gdzie mamy wioski teraz lub wczoraj: our_villages, our_pop, region_total_pop, share=our_pop/total, delta_pop vs prev; top-5 sojuszy wg populacji w regionie), `top_players(curr_rows, prev_rows, alliance_ids, n=5) -> dict[str, list[PlayerStat]]` — TRZY rankingi osobno: `population`, `growth`, `new_villages` (każdy cap n; `prev=None` → growth 0).
  - References: `src/travian/metrics.py`, `tests/test_metrics.py`, `src/travian/models.py` (RegionStat/PlayerStat)
  - Acceptance: TDD — poprawne agregacje, share w %, sortowanie malejąco, cap n, sojusz bez wiosek w regionie nie pojawia się; trzy rankingi top_players zwracane osobno.
  - QA happy: pytest zielony. QA failure: region z populacją 0 w region_total → share = 0 zamiast dzielenia przez zero (test).
  - Commit: `feat: region and player stats`
- [ ] 8. `src/travian/strings.py` (wszystkie teksty EN) + `src/travian/bot/report_embed.py`: `build_report_embed(data: ReportData, alliance_tags, snapshot_date) -> discord.Embed` — description z datą i serwerem; pola: Summary, New Villages (cap 15 + „…and N more"), Lost Villages (cap 15, z nowym właścicielem lub „deleted"), Top Players (3 OSOBNE pola: Population / Growth / New Villages, każde cap 5), Regions (linie „Region — N vil · M pop (share%) · Δ", cięcie pól po 1024 znaki, Regions ≤18 pól — 7 stałych + Regions ≤25 ŁĄCZNIE, nadmiar → „…and N more"; każdy split bloku stałego >1024 zmniejsza cap Regions o 1: `Regions ≤ 25 − len(fixed_after_splits)`; linie renderowane W KOLEJNOŚCI aż do wyczerpania 1024-znakowego capu pola LUB pozostałego budżetu znaków `6000 − len(fixed_after_splits) − len(description) − len(footer) − 512` (margines na nazwy pól i separatory) — pominięte regiony podsumowane jako „…and N more"), Victory Points (suma + delta); footer „map.sql snapshot YYYY-MM-DD (midnight server time)"; kolor z settings.
  - References: `src/travian/strings.py`, `src/travian/bot/report_embed.py`, `tests/test_report_embed.py`
  - Acceptance: TDD — struktura embeda (nazwy pól EN, 3 OSOBNE pola Top Players), limity (worst-case fixture WYMUSZA co najmniej DWA splity bloków stałych >1024 znaki: `len(embed.fields) <= 25` trzyma się, bo cap Regions liczony dynamicznie `25 − len(fixed_after_splits)`; `len(embed) <= 6000` — łączna długość, fixture pokrywa oba limity; żadne pole >1024, nazwa pola ≤256), brak poprzedniego dnia → delty „—" i adnotacja baseline.
  - QA happy: pytest zielony (dowód: `uv run pytest tests/test_report_embed.py -v`). QA failure: dane z 0 zdarzeń → pola „None" w sensownym języku (test).
  - Commit: `feat: report embed builder`

### Wave 3 — Bot

- [ ] 9. `src/travian/bot/main.py`: discord.Client + `app_commands.CommandTree(client)` (intents default), start: init_schema, load settings (env + DB), start AsyncIOScheduler z jobami: `job_fetch` (CronTrigger FETCH_HOUR/FETCH_MINUTE tz FETCH_TZ) i `job_report` (CronTrigger REPORT_HOUR/REPORT_MINUTE tz REPORT_TZ); on_ready → `tree.sync()` + log; walidacja startowa na wartość MERGED (env + tabela `settings`): `DISCORD_TOKEN` sprawdzany TYLKO z env (nigdy w settings), `CHANNEL_ID` z merged — brak któregoś → czytelny błąd i exit 1; ORAZ walidacja stref/godzin (MERGED): `FETCH_TZ`/`REPORT_TZ` przez konstrukcję `ZoneInfo(...)` w try/except (`ZoneInfoNotFoundError` → czytelny błąd + exit 1), godziny 0–23 / minuty 0–59 — jak walidacja PUT w zad. 12 (`ALLIANCE_TAGS` NIE jest wymagany przy starcie — patrz zad. 6: puste/nierozwiązywalne → warning + skip dzienny); funkcje `run_fetch()` / `run_report(channel_id, require_today: bool = True)` współdzielone przez joby, dashboard (montowany w zad. 12) i `/raport` (async, łapią wyjątki → `append_log`).
  - References: `src/travian/bot/main.py`, `src/travian/store.py` (settings), `src/travian/dashboard/app.py` (tylko interfejs współdzielonych funkcji)
  - Acceptance: start z testowym tokenem (staging) na lokalnej maszynie: bot się loguje, joby fetch i report zarejestrowane w schedulerze; dashboard NIE jest jeszcze montowany (bootstrap w zad. 12).
  - QA happy: uruchomienie i logi (dowód: output). QA failure: brak tokenu → exit 1 z komunikatem (test przez `docker compose run` z pustym env).
  - Commit: `feat: bot entrypoint with scheduler`
- [ ] 10. `job_fetch` / `run_fetch`: fetch → parse → `save_snapshot(date=datetime.now(ZoneInfo(FETCH_TZ)).date(), source='map.sql')` → log (snapshot_date = data północy serwera wg FETCH_TZ); awaria fetch (po retry) → `append_log(error)`. GUARD PUSTEGO PARSE'U: jeśli `parse_map_sql` zwróci 0 wierszy (puste/niekompletne body z kodem 200) → `append_log(error 'empty parse')` + NIE wywołuj `save_snapshot` — guard daty w `run_report` wtedy zadziała (raport dnia pomijany, zero mylących „0 wiosek"). BLOKOWANIA POZA PĘTLĄ BOTA: `fetch_map_sql` (httpx SYNC, timeout 60s, retry 3) oraz operacje sqlite (`save_snapshot`, `load_latest`, `load_villages`, `list_dates` — WSZYSTKIE odczyty fazy ładowania danych run_report) wykonywane przez `await asyncio.to_thread(...)` — pętla discord.py NIE może zamarznąć (heartbeat ~41s; fetch z retry do ~190s → gateway disconnect, utracone joby); `channel.send` zostaje NA pętli. `run_report(channel_id, require_today: bool = True)` — KOLEJNOŚĆ sprawdzeń: (1) `latest = load_latest(conn)`; (2) jeśli `latest is None` → embed „no data yet" (zero snapshotów w bazie); (3) inaczej `expected = datetime.now(ZoneInfo(FETCH_TZ)).date().isoformat()` i jeśli `require_today` ORAZ `latest.snapshot_date != expected` → `append_log('no snapshot for today, skipping')` i powrót BEZ wysyłki (porównanie stringów ISO — `snapshot_date` to TEXT; zapobiega starym raportom po awarii fetch); (4) inaczej: `prev = max(snapshot_date strictly < latest)` — delty przez ewentualną lukę dni są liczone i logowane, nie None → najnowszy + prev → `ReportData` → `build_report_embed` → wysłanie do podanego kanału → log sukcesu. Job dzienny woła `run_report` z `require_today=True` (default); `/raport` z `require_today=False` (świadomy override admina do testów). `job_report` PRZED wywołaniem sprawdza resolved alliance subset (zad. 6): puste/nierozwiązywalne → `append_log` + skip BEZ wołania `run_report`; `run_report` ZAWSZE buduje raport z rozwiązanego podzbioru — dzięki temu `/raport` działa też bez skonfigurowanego sojuszu. Wszystkie wejścia (`job_report`, `/raport`, akcje dashboardu) współdzielą `asyncio.Lock()` na `run_fetch`/`run_report` — przy współbieżnym wywołaniu drugie jest pomijane i logowane (test: akcja w trakcie joba → drugie wywołanie skip + log).
  - References: `src/travian/bot/main.py` (job_fetch/job_report, run_fetch/run_report), `src/travian/map_sql.py`, `src/travian/bot/report_embed.py`
  - Acceptance: testy integracyjne z fixture (mock httpx i mock discord channel): wysłano embed z poprawną treścią; fetch fail → job_log zawiera error, nic nie wysłane (guard daty: brak snapshotu na dziś → skip bez wysyłki); baza bez snapshotów → „no data yet"; puste body (200, 0 wierszy) → brak wiersza `snapshots`, brak raportu, wpis „empty parse" w logu (test); test blokowania: wolny fetch (mock z opóźnieniem) NIE zamraża pętli — inna komenda slash odpowiada w trakcie (asyncio.to_thread).
  - QA happy: na stagingowym kanale pojawia się raport; `job_log` ma wpisy success. QA failure: odcięty internet podczas fetch → error w logu dashboardu, brak crasha procesu.
  - Commit: `feat: daily fetch and report jobs`
- [ ] 11. `src/travian/bot/commands.py`: slash `/raport` — check admina (`interaction.user.guild_permissions.manage_guild` LUB `ADMIN_ROLE_ID` w settings/env; inaczej ephemeral „no permission"), `defer()`, `run_report(invoking_channel, require_today=False)` — świadomy override guarda daty (manualne testowanie działa też po awarii fetch), odpowiedź „Report sent" / błąd; wpięcie do tree.
  - References: `src/travian/bot/commands.py`, `src/travian/bot/main.py` (tree, run_report)
  - Acceptance: test z mockiem interakcji: admin → wywołano `run_report` z `require_today=False` (override guarda); nie-admin → odmowa, brak wysyłki.
  - QA happy: na stagingu `/raport` jako admin wysyła raport do kanału wywołania. QA failure: `/raport` bez uprawnień → ephemeral odmowa (dowód: screenshot/snapshot Discorda).
  - Commit: `feat: admin-only /raport command`

### Wave 4 — Dashboard

- [ ] 12. `src/travian/dashboard/app.py`: FastAPI montowany w procesie bota (bootstrap uvicorna w wątku tła z `lifespan` — wykonuje go to zadanie, nie zad. 9): `GET /` (statyczny UI), `GET /api/status` (ostatni snapshot, liczba wiosek/graczy/sojuszy, następne czasy fetch/report z settings, ostatnie błędy z job_log), `GET /api/settings`, `PUT /api/settings` (walidacja: tagi niepuste po `strip()` + dedupe — wyczyszczenie `ALLIANCE_TAGS` przez dashboard → 422, stan pusty osiągalny tylko z env, godziny 0–23, minuty 0–59, tz z `zoneinfo`, channel_id int, kolor hex; ODRZUCA 422 każdy klucz spoza dozwolonej listy — w tym `DISCORD_TOKEN` i `BACKFILL_DSN`; sekrety NIGDY nie trafiają do settings). Dozwolone klucze settings: `ALLIANCE_TAGS, CHANNEL_ID, FETCH_HOUR, FETCH_MINUTE, FETCH_TZ, REPORT_HOUR, REPORT_MINUTE, REPORT_TZ, ADMIN_ROLE_ID, REPORT_EMBED_COLOR` — reszta (`SQLITE_PATH`, `DASHBOARD_*` itd.) → 422. `POST /api/actions/fetch` i `POST /api/actions/report` (wywołują współdzielone `run_fetch`/`run_report` przez `asyncio.run_coroutine_threadsafe(..., bot_loop)` — dispatch cross-loop na pętlę bota; `run_report` wołane z `require_today=False` jak `/raport`; kanał docelowy: `CHANNEL_ID` z MERGED settings (env+DB), brak → 409 w odpowiedzi i toście; wynik „skipped / no snapshot" zwracany w odpowiedzi i toście (zad. 13); zwracają status; `run_fetch`/`run_report` chronione współdzielonym `asyncio.Lock()` w main.py — wywołanie w trakcie trwającego joba → drugie pomijane i logowane), `GET /api/logs?n=50`; middleware: wymaga `DASHBOARD_TOKEN` (Bearer) tylko gdy `DASHBOARD_BIND != loopback` ORAZ `DASHBOARD_LOOPBACK_ONLY != true` — w Dockerze compose ustawia `DASHBOARD_LOOPBACK_ONLY=true`, więc loopback-only publish hosta nie wymusza tokenu.
  - References: `src/travian/dashboard/app.py`, `tests/test_dashboard_api.py`, `src/travian/store.py`, `src/travian/bot/main.py` (współdzielone funkcje)
  - Acceptance: TestClient — GET status/settings/logs 200; PUT z błędną walidacją (w tym payload z `discord_token`) → 422 z komunikatem, tabela `settings` bez zmian; PUT poprawny → zapis do `settings`; POST actions → wykonane (mock funkcji); po starcie bota (staging) GET `127.0.0.1:8090/api/status` → 200 (bootstrap wątku uvicorna).
  - QA happy: pytest zielony. QA failure: `PUT` z `report_hour=25` → 422, tabela settings bez zmian (test).
  - Commit: `feat: dashboard API`
- [ ] 13. `src/travian/dashboard/static/` — UI single-page (bez build step): **załaduj `/frontend` (design) i po implementacji `/visual-qa`**; ciemny motyw (paleta wg design taste routera, spójna z klimatem Traviana), layout: header z nazwą i datą snapshotu; karty: Status (liczby + następne biegi), Settings (formularz z walidacją inline i feedbackiem), Actions (przyciski Fetch now / Send report now z loading + toast z wynikiem), Job log (przewijana lista z poziomami); responsywność, brak zewnętrznych CDN (self-contained).
  - References: `src/travian/dashboard/static/{index.html,style.css,app.js}`, skill `/frontend`, skill `/visual-qa`
  - Acceptance: playwright — strona ładuje się, formularz zapisuje ustawienia (stan po reloadzie), przyciski wyzwalają akcje i odświeżają status, log się aktualizuje; visual QA przechodzi (dobry werdykt).
  - QA happy: pełny przepływ przez przeglądarkę (dowód: zrzuty ekranu + snapshot). QA failure: nieprawidłowe dane w formularzu → czytelny komunikat walidacji bez zapisu (dowód: zrzut).
  - Commit: `feat: polished dashboard UI`

### Wave 5 — Deployment

- [ ] 14. `Dockerfile` (python:3.12-alpine, instalacja przez uv — zależności muszą mieć musllinux wheels, np. asyncpg>=0.29, bez build-deps w obrazie; non-root user, `HEALTHCHECK` na 127.0.0.1:8090/api/status przez `python -c urllib`; `tzdata` w depsach (pip) gwarantuje `ZoneInfo(FETCH_TZ)` — QA smoke w kontenerze: `python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Warsaw')"` musi przejść), `docker-compose.yml` (usługa `bot`: image GHCR, `volumes: travian-data:/data`, `ports: "127.0.0.1:8090:8090"` — loopback-only po stronie HOSTA, `environment: DASHBOARD_BIND=0.0.0.0` — bind WEWNĄTRZ kontenera musi być 0.0.0.0, żeby publikacja portu działała, + `DASHBOARD_LOOPBACK_ONLY=true` — loopback-only publish hosta wystarcza, token niewymagany, `env_file: .env`, `restart: unless-stopped`, `TZ`), `.env.example` (kompletny).
  - References: `Dockerfile`, `docker-compose.yml`, `.env.example`
  - Acceptance: `docker compose build` przechodzi; kontener startuje z `.env` (testowy token → czytelny błąd tokenu), healthcheck zielony po podaniu prawdziwego env (na maszynie użytkownika).
  - QA happy: build + run (dowód: `docker compose ps` healthy). QA failure: brak `DISCORD_TOKEN` lub `CHANNEL_ID` (każda osobno testowana) → exit 1 z czytelnym komunikatem (dowód: `docker compose logs`). Przy `restart: unless-stopped` Docker restartuje zakończony kontener — restart-loop po braku env jest OBSERWOWALNY w logach i udokumentowany w README troubleshooting (zad. 15): poprawa env zatrzymuje pętlę; `ALLIANCE_TAGS` nieobecne NIE jest fatalne (warning + skip dzienny — zad. 6).
  - Commit: `feat: docker packaging`
- [ ] 15. `.github/workflows/build.yml`: DWA joby — (a) build + push do `ghcr.io/peterpage2115/mufon` (repo: https://github.com/PeterPage2115/MUFON.git, prywatne) na push do main (tagi: `latest` + sha), permissions `packages: write`, job z warunkiem `if: github.ref == 'refs/heads/main'`; (b) skan sekretów (gitleaks jako primary; prosty grep DISCORD_TOKEN tylko awaryjnie) — trigger `push:` (wszystkie gałęzie) + `pull_request:`, job bezwarunkowy (alternatywnie: dwa osobne pliki workflow); README.md: setup Discorda (developer portal, invite z DWOMA scope'ami: `bot` z permissions Send Messages + Embed Links ORAZ `applications.commands`, channel id), env, `update.sh`, dostęp do dashboardu (ssh tunnel/localhost), backfill, troubleshooting (w tym: `DASHBOARD_LOOPBACK_ONLY=true` TYLKO w compose ORAZ TYLKO przy publish `127.0.0.1:...` — przy szerszym expose wymagany token; poza Dockerem bind != loopback wymaga tokenu; `snapshot_date` liczony w FETCH_TZ — zmiana FETCH_TZ wymaga zgodności z czasem serwera; restart-loop przy braku env — czytelny błąd w logach, poprawa env zatrzymuje pętlę). Setup GHCR na serwerze: `echo <PAT> | docker login ghcr.io -u PeterPage2115 --password-stdin` (PAT scope `read:packages`, przechowywany na serwerze) — PRYWATNE repo = prywatny obraz; pull bez logowania zwróci `unauthorized`.
  - References: `.github/workflows/build.yml`, `README.md`, `pyproject.toml`
  - Acceptance: workflow poprawny (lint/actionlint jeśli dostępny); po pushu obraz w GHCR; README kompletny.
  - QA happy: `git push` na main → workflow green (dowód: log CI). QA failure: sekret w repo → job (b) CI fail (test: commit z dummy tokenem w OSOBNYM pliku `test-secret.env` na gałęzi testowej przez `git add -f` — NIE commituj prawdziwego tokenu ani `.env`; `.env` jest w .gitignore; dummy MUSI mieć format tokenu Discorda `[MN][\w-]{23,}\.[\w-]{6}\.[\w-]{27,}`, żeby gitleaks faktycznie go wykrył; push na gałąź testową wyzwala job (b) skanowania).
  - Commit: `ci: ghcr build and push`
- [ ] 16. `update.sh` (`docker compose pull && docker compose up -d`), finalny `.gitignore` + weryfikacja, że `.env` nigdy nie trafia do repo (grep w CI); smoke-test aktualizacji na serwerze użytkownika. Udokumentuj w `update.sh`/README, że `docker login ghcr.io` (zad. 15) MUSI poprzedzać pull przy prywatnym obrazie; skrypt sprawdza `docker manifest inspect ghcr.io/peterpage2115/mufon:latest` przed pull (czytelny błąd przy braku obrazu/braku logowania).
  - References: `update.sh`, `.gitignore`, `.github/workflows/build.yml` (secret scan)
  - Acceptance: skrypt idempotentny (2× uruchomienie = brak błędu); repo nie zawiera sekretów.
  - QA happy: `./update.sh` na serwerze z nowym obrazem (dowód: `docker compose ps` + logi). QA failure: brak obrazu w GHCR → czytelny błąd skryptu (sprawdzany `docker manifest inspect` przed pull).
  - Commit: `chore: update script and secret guards`

## Final verification wave

- [ ] F1. **Plan compliance audit**: każdy todo z Wavów 1–5 ma wykonane kryteria akceptacji; brak zadań pominiętych; struktura repo zgodna z Execution strategy; konfiguracja zgodna z Config surface.
  - Dowód: checklista w logu sesji; `uv run pytest` zielone w całości; `uv run ruff check .` i `uv run basedpyright src` czyste.
- [ ] F2. **Code quality review**: przegląd kodu (w tym zadania delegowane równolegle) — typy, obsługa błędów, brak sekretów, limity embeda, walidacja settings; brak logiki biznesowej bez testu.
  - Dowód: raport przeglądu z cytatami plików; skan sekretów czysty.
- [ ] F3. **Real manual QA**: na stagingowym kanale: raport dzienny (wygenerowany ręcznie przez `/raport`) ma poprawne liczby wg agregacji SQL ze snapshotu; dashboard: edycja ustawień + Fetch now + Send report now + log; aktualizacja przez `./update.sh` (dowód obejmuje `docker compose pull` na serwerze po zalogowaniu do GHCR — patrz zad. 15).
  - Dowód: zrzuty ekranu Discorda i dashboardu, output komend, logi kontenera.
- [ ] F4. **Scope fidelity**: raport zawiera wyłącznie dozwolone bloki (Summary, New Villages, Lost Villages, Top Players, Regions, Victory Points); brak mapy/obrazków/linków; brak zapisu do bazy kolegi; `/raport` działa tylko dla adminów; dashboard NIE wystawiony publicznie — w Dockerze exposure ogranicza mapping `127.0.0.1:8090:8090` (host) przy `DASHBOARD_LOOPBACK_ONLY=true` (token niewymagany), poza Dockerem default bind 127.0.0.1.
  - Dowód: wygenerowany embed + inspekcja kodu (grep za `supabase` — tylko `backfill.py`, read-only; grep w docker-compose.yml: `ports` z prefiksem `127.0.0.1:`).

## Commit strategy

- Jeden commit na todo (numery wg Todos), message conventional wg linii Commit każdego zadania.
- Repo zdalne (utworzone przez użytkownika, prywatne): `git remote add origin https://github.com/PeterPage2115/MUFON.git`; pierwszy push w zad. 1 po commicie scaffolda.
- Gałąź robocza: worker tworzy gałąź `feat/travian-report-bot` (lub worktree przez `$start-work --worktree`); PR do `main` dopiero po przejściu F1–F4.
- Nigdy nie commituj `.env`, `data/`, tokenów (guard w CI + `.gitignore`).
- `.omo/` (draft + plan) commituj razem z pierwszym commitem repo.

## Future roadmap

Celowo POZA zakresem v1. Architektura v1 już przygotowana pod te kierunki (wspólny rdzeń `travian/`, snapshoty per-dzień w SQLite, śledzenie po `alliance_id`, `region_stats` z `region_total_pop` + top-5 sojuszy — wejściówki planera osiedlania, tabela `settings` z wartościami JSON, joby APScheduler). Żadne zadanie v1 nie wymaga zmian.

**v1.1 (tanie, po ustabilizowaniu v1):**
- **Alerty dzienne (watch)**: nowy job sprawdzający warunki przy każdym snapshotcie — reużywa `village_events` z zad. 6, przeliczane ze snapshotów bez osobnej persystencji (utrata wioski, nowa wioska wroga w obserwowanym regionie, członek stracił N wiosek, członek opuścił sojusz, skok populacji obserwowanego gracza). Nowe klucze settings `WATCH_*`; osobny embed „Watch".
- **Intel o wrogach/sojusznikach**: ten sam silnik metryk z innymi tagami (lista w configu) — zero nowego engine'a.
- **Historia**: `/hist region <name>` / `/hist player <name>` (tekstowo ze snapshotów).
- **Wielokanałowość**: `CHANNEL_ID` → lista (settings już JSON).

**v2 — Dashboard sojuszowy (web, domena + Cloudflare Tunnel):**
- Interaktywna mapa świata (kropki wiosek, kolory wg plemienia/sojuszu, podświetlenie naszych, filtr regionu); głębokie linki z raportów (x|y / region).
- **Widok regionów / planer osiedlania**: unlocked (top-5 sojuszy ≥ 4000 pop) / contested (udział > 50%) liczone ze snapshotów (`region_total_pop`, top-5 już w `region_stats`), spawn regions, projekcja naszej kontroli.
- **Roster**: członkowie, wioski, populacja, przyrost, regiony.
- **Trendy**: wykresy populacji/wiosek/VP w czasie (historia SQLite + opcjonalny backfill).
- Model procesu: rozszerzenie tej samej aplikacji FastAPI (wzorzec same-process jak v1) LUB drugi kontener z read-only montażem wolumenu (SQLite WAL dopuszcza równoległych czytelników).
- **Polityka frontendu (v2, decyzja 2026-08-09)**: mapa i wykresy będą JS-em (Svelte lub React — wybór przy v2), serwowane statycznie przez FastAPI z tego samego obrazu; v1 config dashboard pozostaje vanilla JS bez build stepu. Backend (Python) i frontend (JS) to niezależne wybory — v1 niczego nie blokuje, żadnych zmian w zadaniach v1.

**Świadomie NIE planowane (ToS / wymaga logowania):** dane w czasie rzeczywistym, oficjalny status regionów, scraping statystyk gry.

**Retencja:** SQLite rośnie ~7k wierszy/dzień; przy wdrożeniu funkcji historycznych dodać opcjonalny prune (np. ostatnie 6 miesięcy).

## Success criteria

1. Codziennie o skonfigurowanej porze na kanale pojawia się embed z poprawnymi blokami i deltami vs poprzedni dzień (dowód: historia kanału przez 2+ dni — sprawdzenie po wdrożeniu na serwerze, poza F-wave, bo wymaga 2 dni działania).
2. `/raport` działa dla adminów i odmawia innym.
3. Dashboard dostępny na `127.0.0.1:8090`, edycja ustawień trwa po restarcie kontenera, przyciski akcji działają, log pokazuje błędy.
4. Aktualizacja na serwerze domowym = `./update.sh` (obraz z GHCR), zero ręcznych kroków poza env.
5. Wszystkie testy zielone; brak sekretów w repo; zero crashy procesu przy awarii fetch (error w logu, następny dzień działa).
