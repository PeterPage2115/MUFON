# travian-discord-report-bot

Daily Travian server report bot with an analytics dashboard. Fetches the
public `map.sql` snapshot of a Travian server once a day, stores it in
SQLite, and posts a Discord embed report to your channel every morning —
plus a web dashboard for status, manual fetch/report, settings, and
region/alliance analytics. Members of the alliance can log in with their
Discord account (member = read-only view, admin = full control).

Product decisions, priorities and acceptance criteria live in
[`ROADMAP.md`](ROADMAP.md) — check it before starting new work.

## Overview

```
┌────────────────────────────── one process ──────────────────────────────┐
│  travian.bot.main                                                        │
│  ├── discord.py client — slash commands (/raport, /wioski, /regiony)     │
│  ├── APScheduler — daily map.sql fetch + daily report                    │
│  └── uvicorn dashboard thread — FastAPI on :8090 (/api/*, /static/*)     │
│  SQLite (default /data/travian.db) — snapshots, villages, settings, log  │
└──────────────────────────────────────────────────────────────────────────┘
```

- **One process**: bot, scheduler and dashboard run in the same Python
  process (uvicorn in a background thread). Docker runs that single image.
- **Data source**: `https://cw.x2.international.travian.com/map.sql` — a
  public file generated daily by the server. Fetched once a day (configurable
  schedule), parsed and stored as snapshots. All metrics are computed from
  stored snapshots, never from the live server.
- **Daily report**: posted by the scheduler (or manually via the dashboard /
  `/raport`) as one Discord message with 3 embed cards — Summary KPI,
  Regions (top-8 + movers) and Standings — covering the configured
  alliances (the "combined" union of `ALLIANCE_TAGS`).
- **Dashboard** (`http://<host>:8099` on the LAN, see Deployment): status,
  settings, manual fetch/report, job log, and an Analysis section with
  Regions / Alliances / Players / Events / Changes views, filterable per
  alliance, with CSV export.

## Features

### Discord report

- **Summary KPI card**: villages, players, total population, VP — with
  day-over-day deltas.
- **Regions card**: top-8 regions by our share, with control % bar, Δ %
  and the population still needed to reach 50% control; **movers**
  (regions whose share changed the most) in the footer.
- **Standings card**: population/VP per tracked alliance (allies + enemies)
  with day-over-day deltas; our tags bolded.
- **`/raport`** — posts the daily report to the invoking channel.
- **`/wioski`** — full village events (gained + lost) for the latest day.
- **`/regiony`** — full regions table with Δ %, not just the top-8.
- All three commands answer ephemerally; admin = `Manage Server`
  permission **or** the `ADMIN_ROLE_ID` role.

### Dashboard

- **Status / Settings / Actions / Logs** cards: live snapshot overview,
  schedule + alliance configuration (stored in SQLite, overrides env),
  manual "Fetch now" / "Send report now", and a live job console.
- **Analysis**: Regions (share series + control table + chart + top
  alliances per region), Alliances (population/VP series for tracked
  alliances), Players (top players by population / growth / new villages),
  Events (gained/lost between two dates), Changes (daily headline history
  with deltas). Regions/Events/Changes/Players filter by alliance
  (**Combined** = union of `ALLIANCE_TAGS`, or a single tag); CSV export on
  Regions/Events/Changes.

### Access control

- **Token mode** (default): every API call needs
  `Authorization: Bearer <DASHBOARD_TOKEN>` (constant-time comparison);
  whoever holds the token is admin.
- **Discord OAuth mode** (recommended for the alliance): members log in
  with their Discord account. Any verified guild member sees the full
  intelligence (all Analysis tabs incl. the Village explorer, CSV export,
  snapshot freshness/KPIs/schedules), while the server owner, Discord
  Administrator / Manage Server permission holders, and `ADMIN_ROLE_ID`
  holders additionally get the raw Job log, the recent-error list on the
  Status card, Settings and Actions. Settings/Actions are admin-only;
  `/api/logs` returns 403 for members, and `/api/status` reports
  `errors: []` for them. Actions are rate-limited (6 per minute per user).
- **Loopback mode**: no auth at all, only meaningful when the dashboard is
  bound to a loopback address.
- Sharing the dashboard with players requires
  `DASHBOARD_AUTH_MODE=oauth` with complete `OAUTH_*` keys — token mode is
  operator-only (the token holder is always admin, never a read-only
  player view).
- The dashboard is designed for **LAN** use (see Deployment); it is not a
  hardened public internet service.

## Configuration

Copy the template and fill it in:

```bash
cp .env.example .env
```

`DISCORD_TOKEN` and `CHANNEL_ID` are required; everything else has a working
default. Settings edited in the dashboard are stored in the DB and
**override** the `.env` values (except `DISCORD_TOKEN`, `SQLITE_PATH`, the
`DASHBOARD_*` and `OAUTH_*` keys, which are environment-only). An empty
`.env` value counts as unset.

| Key | Default | Meaning |
|---|---|---|
| `DISCORD_TOKEN` | *(required)* | Bot token — env only, never in the DB. |
| `CHANNEL_ID` | *(required)* | Channel where the daily report is posted. |
| `ALLIANCE_TAGS` | *(empty)* | Comma-separated alliance tags, e.g. `UFO,PR-U`. Empty → daily report skipped with a warning. |
| `TRACKED_ALLIANCES` | *(empty)* | Comma-separated tags of ALL alliances (allies + enemies) compared in the report's **Standings** card. OUR tags (`ALLIANCE_TAGS`) are bold; empty → no Standings card. |
| `ADMIN_ROLE_ID` | *(empty)* | Role allowed to trigger `/raport` (empty = any member with Manage Server) and the dashboard admin role in OAuth mode. Dashboard admins in OAuth mode are `ADMIN_ROLE_ID` holders, the server owner, and Discord Administrator / Manage Server permission holders. |
| `FETCH_HOUR` | `0` | Hour (24h) of the daily map.sql fetch. |
| `FETCH_MINUTE` | `15` | Minute of the daily map.sql fetch. |
| `FETCH_TZ` | `Europe/London` | IANA timezone of the fetch schedule. |
| `REPORT_HOUR` | `9` | Hour of the daily report. |
| `REPORT_MINUTE` | `0` | Minute of the daily report. |
| `REPORT_TZ` | `Europe/Warsaw` | IANA timezone of the report schedule. |
| `SQLITE_PATH` | `/data/travian.db` | SQLite database file path — env only. |
| `DASHBOARD_BIND` | `127.0.0.1` | Dashboard bind address — env only. Compose overrides to `0.0.0.0` (see Deployment). |
| `DASHBOARD_PORT` | `8090` | Dashboard port — env only. |
| `DASHBOARD_TOKEN` | *(empty)* | Bearer token required in token mode — env only. |
| `DASHBOARD_LOOPBACK_ONLY` | `false` | `true` forces loopback mode (no auth) — only safe with a loopback bind. |
| `DASHBOARD_AUTH_MODE` | `token` | `token` (default) \| `oauth` \| `none`. `oauth` needs the `OAUTH_*` keys; `none` disables auth entirely. |
| `OAUTH_CLIENT_ID` | *(empty)* | Discord application client ID (OAuth mode). |
| `OAUTH_CLIENT_SECRET` | *(empty)* | Discord application client secret (OAuth mode) — env only. |
| `OAUTH_GUILD_ID` | *(empty)* | Discord guild (server) whose members may log in (OAuth mode). |
| `REPORT_EMBED_COLOR` | `0x2ECC71` | Embed accent color as a hex value (validated at startup). |
| `BACKFILL_DSN` | *(empty)* | Read-only Postgres DSN of the source snapshot DB (see Backfill). |

## Commands

| Command | Description | Admin required |
|---|---|---|
| `/raport` | Posts the daily report (3 cards) to the invoking channel | yes (Manage Server or `ADMIN_ROLE_ID`) |
| `/wioski` | Full village events for the latest day | yes |
| `/regiony` | Full regions table with Δ % | yes |

All answers are ephemeral. Admin = `Manage Server` permission in the guild
**or** the configured `ADMIN_ROLE_ID` role.

## Dashboard & auth modes

The dashboard listens on port **8090** (published as **8099** by the
compose file — `http://<host>:8099`). Auth is decided once at startup from
env:

| `DASHBOARD_AUTH_MODE` | Behavior |
|---|---|
| `token` (default) | Every API call needs `Authorization: Bearer <DASHBOARD_TOKEN>` (constant-time comparison). Whoever holds the token is admin. |
| `oauth` | Discord OAuth login; guild members get a read-only view (Settings/Actions hidden). Full control goes to `ADMIN_ROLE_ID` holders, the server owner, and Discord Administrator / Manage Server permission holders. Actions rate-limited (6/minute/user). |
| `none` | No auth — only sensible with a loopback bind. |

With `DASHBOARD_AUTH_MODE` unset, the legacy heuristic applies: non-loopback
bind with `DASHBOARD_LOOPBACK_ONLY != "true"` → `token`, otherwise `none`.

### Setting up Discord OAuth (operator checklist)

1. Create/select an application at the
   [Discord Developer Portal](https://discord.com/developers/applications).
2. **OAuth2 → General**: copy **Client ID** (`OAUTH_CLIENT_ID`) and **Client
   Secret** (`OAUTH_CLIENT_SECRET`).
3. **OAuth2 → Redirects**: add one redirect URL **per host/port you will log
   in from**, e.g. `http://192.168.1.164:8099/api/auth/callback` (the
   redirect URI is built from the browser's request host — every host/port
   used must be registered).
4. Make sure the bot is in your server, and add
   `OAUTH_GUILD_ID` (right-click the server → Copy Server ID with Developer
   Mode enabled).
5. Set `DASHBOARD_AUTH_MODE=oauth` plus the three `OAUTH_*` keys and
   restart. Members log in with **Sign in with Discord**; the first person
   to log in must be the server owner, a Discord Administrator / Manage
   Server permission holder, or an `ADMIN_ROLE_ID` holder to configure the
   dashboard.

Notes: OAuth sessions live in memory (TTL 7 days) — restarting the bot logs
everyone out. If an `OAUTH_*` key is missing while `oauth` is requested, the
dashboard falls back to `token` mode and logs a warning.

## Deployment

### Docker (any host)

```bash
cp .env.example .env      # fill DISCORD_TOKEN and CHANNEL_ID at minimum
docker compose up -d      # builds locally OR pulls the GHCR image
docker compose logs -f    # watch startup
```

- The container runs as a non-root user; SQLite lives on the `travian-data`
  volume (`/data`), so data survives container recreation.
- The port is published as **8099** on all host interfaces
  (`"8099:8090"`) — access control is `DASHBOARD_TOKEN` (token mode) or
  Discord OAuth (oauth mode). Do not remove the auth while the port is open.
- On the very first run there is **no snapshot yet** — the daily report is
  skipped ("no data yet") until the first map.sql fetch completes. Use the
  dashboard **Fetch now** button (or wait for the scheduled fetch).

### Unraid (compose manager)

The same compose file is managed through the Unraid **Compose Manager**
plugin (`/boot/config/plugins/compose.manager/projects/mufon`). Updates are
identical: pull the new image and recreate the container; the `travian-data`
volume keeps the data.

### Updating

```bash
./update.sh    # docker compose pull && docker compose up -d (IMAGE_TAG=latest)
```

The GHCR package is **public**, so `docker compose pull` works anonymously —
no `docker login` needed. `./update.sh` runs `docker manifest inspect`
before pulling and fails with a readable message if the image is missing
(CI hasn't pushed) or the package was made private again (then log in once
per server: `echo <PAT> | docker login ghcr.io -u PeterPage2115
--password-stdin`, where `<PAT>` is a GitHub Personal Access Token with the
`read:packages` scope).

Every image carries its commit SHA: `GET /api/meta` (public, no auth)
returns `{"version": ..., "build_sha": ...}` — the header bar of the
dashboard shows `build_sha` too. The SHA tag (`ghcr.io/peterpage2115/mufon:<sha>`)
is pushed by CI alongside `latest`, so a pinned deploy or a rollback to a
known-good commit is `IMAGE_TAG=<full-sha> ./update.sh`.

Recommended update sequence (deploy or rollback):

1. **Back up** the SQLite database (see Backup) and note the **current**
   `/api/meta` `build_sha`.
2. `IMAGE_TAG=<nowy-sha> ./update.sh` (defaults to `latest` when unset).
3. Verify: `GET /healthz` → 200, `GET /readyz` → 200, and `GET /api/meta`
   → `build_sha` equals the deployed tag.
4. On regression: `IMAGE_TAG=<poprzedni-sha> ./update.sh` and re-check the
   same endpoints plus the data volume.

New images are built by CI from `main` (the `build` workflow runs the full
test suite + lint + type check before building, smoke-tests `/healthz`, and
verifies `/api/meta` reports the pushed commit SHA).

## Development

```bash
uv sync --dev                        # install locked dependencies + dev tools
uv run playwright install chromium   # browser for the smoke tests (once)
uv run pytest -q                     # full test suite (incl. the browser smoke)
uv run ruff check src tests
uv run basedpyright src              # strict type check (0 errors / 0 warnings / 0 notes)
```

### Live smoke (optional)

Point the read-only smoke tests at a running deployment — no mutations, no
secrets printed:

```bash
DASHBOARD_LIVE_URL='http://<host>:8099' uv run pytest tests/test_dashboard_live.py -m live -v
DASHBOARD_LIVE_URL='http://<host>:8099' DASHBOARD_TOKEN='<token>' uv run pytest tests/test_dashboard_live.py -m live -v
```

The token (when set) is read from the environment only and never logged; the
tests hit `/healthz`, the static UI, `/api/auth/status`, anonymous 401s and
(read-only) `/api/status` + `/api/analysis/dates` + `/api/analysis/regions`.
With `DASHBOARD_EXPECTED_SHA` set, `/api/meta` must report that exact SHA.

CI runs exactly these gates on every push to `main` and every pull request
(the `quality` job installs Chromium with `playwright install --with-deps`
before `pytest`), then builds/pushes the image and smoke-tests it.

## Backfill (optional)

If you have read-only access to a Postgres snapshot DB (e.g. the server's
Supabase export), you can import its historical snapshots into SQLite:

```bash
export BACKFILL_DSN='postgresql://user:pass@host/db'
python -m travian.backfill            # import all snapshots
python -m travian.backfill --dry-run  # print what would be imported, write nothing
python -m travian.backfill --sqlite /path/to/custom.db
```

The backfill is strictly read-only on the source: it auto-detects the
snapshot table (must contain `_date` and `village_id` columns) and streams
rows in batches. Imported snapshots give the daily report a previous day to
compute deltas against.

## Data & privacy

- `map.sql` is **public** — anyone can download it; the data it contains
  (village/player/alliance names and populations) is not secret.
- The dashboard exposes exactly this data plus derived analytics
  (per-alliance statistics, events, rankings). The auth mode controls who
  can read it; nothing is end-to-end encrypted.
- No data leaves your server: the dashboard talks to your browser directly,
  and Discord OAuth only exchanges identity info (user id, name, guild
  membership) with Discord itself.

## Troubleshooting

- **Container restarts in a loop** / **reports `unhealthy`**: startup
  validation failed — most often a missing `DISCORD_TOKEN` or `CHANNEL_ID`,
  an invalid timezone, or a `REPORT_EMBED_COLOR` outside `0x000000`-`0xFFFFFF`.
  The process exits before the dashboard starts, so `/healthz` never answers.
  Fix `.env`/settings and restart. Missing `ALLIANCE_TAGS` is **not** fatal:
  the daily report is skipped with a warning.
- **`401 unauthorized` on API calls**: token mode — wrong or missing
  `DASHBOARD_TOKEN` (the UI asks for it on first load); oauth mode — the
  session expired or the browser cleared storage (log in again).
- **`ERR_CONNECTION_REFUSED` in the browser**: the dashboard port is not
  reachable from this machine. If it is bound loopback-only, open an SSH
  tunnel (`ssh -L 8099:127.0.0.1:8099 user@server`) or use a host on the
  same LAN as the compose host (`http://<host>:8099`).
- **OAuth login fails with `not_a_member`**: the Discord account is not a
  member of `OAUTH_GUILD_ID`. `invalid_state` after a long tab → log in
  again (state tokens expire after 10 minutes).
- **`unauthorized`/`denied` when pulling**: the image was never published
  (CI builds only from `main` — check Actions) or the GHCR package was made
  private — log in to GHCR first (see Updating).
- **A day is missing from the report**: an empty/truncated map.sql response
  is treated as a fetch failure — no snapshot is saved that day (look for
  `empty parse` in the logs).
- **Reports look stale**: check that the fetch job actually ran (dashboard
  logs / `docker compose logs`); deltas across a gap are computed and logged,
  not skipped.
