# Roadmap — travian-discord-report-bot / MUFON

Dokument kontraktu produktowego. Decyzje zapisane tutaj nadają priorytet pracy;
aktualizuj ten plik, gdy decyzja się zmienia. Pełny kontekst techniczny:
`README.md` (funkcje, konfiguracja, deployment) i `DESIGN.md` (system wizualny).

## 1. Decyzje produktowe (stan na 2026-08)

| Decyzja | Wybór | Uwagi |
|---|---|---|
| Odbiorca dashboardu | operator + członkowie sojuszu | członek = widok tylko-do-odczytu (intelligence), admin = pełna kontrola |
| Canonical auth | `oauth` (Discord) dla członków; `token` dla operatora / trybów nie-OAuth | `DASHBOARD_AUTH_MODE` decyduje; `token` pozostaje fallbackiem i trybem domyślnym |
| Zasięg dostępności | tylko zaufany LAN (compose publikuje `8099:8090`) | brak planów na publiczny internet; auth nie jest zabezpieczeniem klasy edge |
| Historia danych | bez automatycznego pruningu do czasu pomiaru (Task 9) | SQLite/WAL, snapshoty dzienne; retencja do ustalenia po profilowaniu |
| Backup | wymagany przed każdą aktualizacją obrazu | backup SQLite online API; rollback = powrót do poprzedniego obrazu po SHA |
| Identyfikacja buildu | wymagana (`/api/meta`, SHA commita) | bez niej nie da się potwierdzić, co działa na serwerze |
| Freshness | wymagana obserwowalność wieku danych i luk | `/healthz` = liveness procesu; readiness/freshness jako osobny kontrakt (Task 7) |
| Kolejność prac | najpierw niezawodność (P0/P1), potem nowe funkcje (P2) | nie zaczynamy nowej dużej zakładki przed zamknięciem P0 |

## 2. Kryteria „ready for daily use”

1. Każdy chroniony endpoint wymaga właściwej sesji (token lub OAuth).
2. Member widzi intelligence + freshness, ale nie: Job log, Settings, Actions, `/api/logs`.
3. Admin może odczytać konfigurację i wykonać akcję (fetch/report) po świadomej decyzji.
4. Brak snapshotu oraz luka między snapshotami są czytelnie oznaczone (nigdy fałszywy day-over-day).
5. Działający obraz daje się jednoznacznie zidentyfikować (SHA), a rollback to powrót do poprzedniego tagu.
6. Backup SQLite można odtworzyć na czystej bazie; procedura jest udokumentowana.
7. Browser smoke (Playwright) pokrywa główne widoki; live smoke jest sekret-free i read-only.
8. Pełny zestaw `pytest + ruff + basedpyright` przechodzi w CI przed publikacją obrazu.

## 3. Zrobione (stan na 2026-08-13, HEAD `48b5116`)

- Parser i składowanie snapshotów `map.sql` (SQLite/WAL), delta engine, backfill z Postgres.
- Raport Discord (KPI, Regiony, Standings) + `/raport`, `/wioski`, `/regiony`.
- Dashboard: Intelligence (Regions / Alliances / Players / Events / Changes / Villages + CSV),
  Overview (Status / Job log), Operations (Actions / Settings), scheduler sync, honest deltas.
- Auth: token mode, Discord OAuth z RBAC member/admin i rate limitingiem akcji.
- CI: quality (pytest + ruff + basedpyright + browser smoke) → build-push GHCR → smoke → secret-scan.
- Iteration 4 P0: ten dokument jako kontrakt, sekret-free live smoke (marker `live`),
  browser smoke dla Intelligence/Overview/token gate/Operations (Playwright) i fix token gate.
  Pełny baseline `pytest + ruff + basedpyright` przechodzi lokalnie na tym commicie.

## 4. Następne (P0) — stabilizacja i akceptacja

- [x] Task 1 (ten dokument): kontrakt + roadmapa.
- [x] Task 2: sekret-free live smoke (`tests/test_dashboard_live.py`, marker `live`).
- [x] Task 3: browser smoke dla głównych widoków Intelligence/Overview + ścieżek pustych.
- [x] Task 4: browser smoke dla token gate i widoku Operations (admin-only).
- [x] Autoryzowany dogfood live — wykonany 2026-08-13 za zgodą operatora:
      pin deploy `de895d8` (`IMAGE_TAG` na serwerze, `build_sha` zweryfikowany
      w `/api/meta`), backup SQLite przed aktualizacją, live suite
      `tests/test_dashboard_live.py -m live` 7/7 (w tym trasy chronione tokenem),
      dogfood UI w przeglądarce z prawdziwą sesją token (Wars/Events/Changes/
      Villages bez błędów JS, freshness `current`).

## 5. Zrobione (P1, 2026-08-13) — hardening przed szerszym udostępnieniem na LAN-ie

- [x] Task 5: identyfikacja buildu (`/api/meta` z SHA) + deploy/rollback po konkretnym SHA (`IMAGE_TAG`).
- [x] Task 6: backup/restore SQLite (`python -m travian.backup`) + procedura rollbacku.
- [x] Task 7: rozdzielenie liveness (`/healthz`) od readiness/freshness (`/readyz`).
- [x] Task 8: utwardzenie OAuth (redirect-origin `OAUTH_PUBLIC_ORIGIN`, HttpOnly cookie, nagłówki).
- [x] Task 9: pomiar retencji/wydajności na read-only kopii (seed 60k wiosek × 7 dni): najwolniejszy endpoint (Regions 7d) p95 ≈ 0,63 s — poniżej progu interwencji; bez zmian `store.py`/API i bez pruningu.

## 6. Zrobione (P2, 2026-08-13) — Freshness & alerts

- [x] Ostatnie udane wykonania: `/api/status` zwraca `last_successful_fetch` /
      `last_successful_report` (UTC ISO z `job_log`; tylko wpisy ze stałych
      prefiksów sukcesu), karta Status pokazuje obie wartości („Never" gdy
      brak sukcesu). Bez zmian schematu SQLite.
- [x] Wyraźne ostrzeżenia świeżości: badge + tekstowy alert dla
      stale/gap/no_data (precedencja: błędy jobów → gap → stale → no_data),
      baseline z `previous_snapshot_date` — daty zawsze z payloadu serwera,
      nigdy liczone w JS (kontrakt no-false-day-over-day jak w Changes).
- [x] Opcjonalny alert Discord (`ALERT_CHANNEL_ID`, env-only, pusty =
      wyłączony): jeden embed na terminalną porażkę fetch/report na
      UTC-dzień, dedupe przez marker w `job_log` (przetrwa restart). Alert
      best-effort — błąd wysyłki nie zmienia statusu zadania. stale/gap/
      no-data zostają ostrzeżeniami dashboardu (nie alertują).

## 7. Później (P2) — następny slice produktowy (po zamknięciu Freshness & alerts)

Status carryoverów z historycznego draftu dashboardu (zweryfikowany 2026-08-13):

- [x] Scheduler sync, vendored Chart.js, gap-aware Changes, Village explorer,
      VP ranking graczy i limit Events — wdrożone i potwierdzone w kodzie i testach.
- [x] War scoreboard (`metrics.conquests_between`, `GET /api/analysis/wars`,
      zakładka „Wars" z macierzą kto-komu, drill-down i eksportem CSV) —
      wdrożone; wszechświat = `TRACKED_ALLIANCES` (obie strony podboju muszą
      być śledzone; usunięte wioski osobno), testy jednostkowe/API/browser.

Rekomendowany: **Intelligence** — drill-down region/player, porównania
okresów, zapisane widoki.

Alternatywy (jedna naraz, bez równoległych dużych funkcji):

- Intelligence: drill-down region/player, porównania okresów, zapisane widoki.
- Team UX: preferencje użytkownika, szybki landing per rola, lepsza wersja mobilna.
- Operations: historia uruchomień z retry policy i jawnym run identifier.

## 8. Świadomie NIE w planie (YAGNI)

- Publiczny dostęp przez internet (wymagałby proxy, TLS i zupełnie innej klasy auth).
- Automatyczny pruning danych przed pomiarem retencji (Task 9).
- Przebudowa UI/frameworka frontendu; nowe funkcje dokładamy do vanilla JS + Chart.js.
- Wsparcie wielu serwerów Travian w jednym procesie.
