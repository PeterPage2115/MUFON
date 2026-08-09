# travian-discord-report-bot

Daily Travian server report bot. Fetches the `map.sql` snapshot of a Travian
server once a day, stores it in SQLite, and posts a Discord embed report to
your channel every morning — plus a small web dashboard for status, manual
fetch/report, and settings.

## Overview

```
┌────────────────────────────── one process ──────────────────────────────┐
│  travian.bot.main                                                        │
│  ├── discord.py client — slash commands (/raport) + daily scheduler      │
│  └── uvicorn dashboard thread — FastAPI on :8090 (/api/*, /static/*)     │
│  SQLite (default /data/travian.db) — snapshots, villages, settings, log  │
└──────────────────────────────────────────────────────────────────────────┘
```

- **One process**: the bot and the dashboard run in the same Python process
  (uvicorn in a background thread). Docker runs that single image.
- **Data source**: `https://cw.x2.international.travian.com/map.sql` — fetched
  daily, parsed, deduplicated and stored as snapshots. All metrics in the
  report are computed from the stored snapshots, never from the live server.
- **Daily jobs** (configurable via env/settings): fetch map.sql at midnight
  server time, post the report in the morning. The report contains Summary,
  New Villages, Lost Villages, Top Players, Regions and Victory Points.
- **`/raport`**: admin-only command that posts the report to the invoking
  channel immediately, bypassing the "today only" guard.
- **Dashboard** (`http://127.0.0.1:8090`): status, run fetch/report now, edit
  settings, view logs.

## Discord setup

1. Create an application at the
   [Discord Developer Portal](https://discord.com/developers/applications).
2. Open **Bot** in the left sidebar → **Reset Token** → copy the token. This
   is `DISCORD_TOKEN`. Treat it as a secret: it is read **only** from the
   environment, never stored in the settings database.
3. In **General Information** copy your **Application ID** (`client_id`).
4. Invite the bot to your server with **both** scopes. Use the URL below —
   note `scope=bot%20applications.commands` (URL-encoded
   `bot applications.commands`) and `permissions=18432`:

   ```
   https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=18432&scope=bot%20applications.commands
   ```

   The permission integer `18432` = **2048** (Send Messages) + **16384**
   (Embed Links). You can also select those two permissions manually in the
   invite generator; the `applications.commands` scope is required for slash
   commands (`/raport`) to work.

5. Get your channel ID: enable **Developer Mode** in Discord settings
   (Settings → Advanced → Developer Mode), right-click the channel in the
   sidebar → **Copy Channel ID**. This is `CHANNEL_ID` (a snowflake, up to 19
   digits — the dashboard handles it as an exact integer).

## Configuration

Copy the template and fill it in:

```bash
cp .env.example .env
```

`DISCORD_TOKEN` and `CHANNEL_ID` are required; everything else has a working
default. Settings edited in the dashboard are stored in the DB and **override
** the `.env` values (except `DISCORD_TOKEN`, `SQLITE_PATH` and the
`DASHBOARD_*` keys, which are environment-only). An empty `.env` value counts
as unset.

| Key | Default | Meaning |
|---|---|---|
| `DISCORD_TOKEN` | *(required)* | Bot token — env only, never in the DB. |
| `CHANNEL_ID` | *(required)* | Channel where the daily report is posted. |
| `ALLIANCE_TAGS` | *(empty)* | Comma-separated alliance tags, e.g. `AAA,BBB`. Empty → daily report skipped with a warning. |
| `ADMIN_ROLE_ID` | *(empty)* | Role allowed to trigger `/raport` (empty = any member with Manage Server). |
| `FETCH_HOUR` | `0` | Hour (24h) of the daily map.sql fetch. |
| `FETCH_MINUTE` | `15` | Minute of the daily map.sql fetch. |
| `FETCH_TZ` | `Europe/London` | IANA timezone of the fetch schedule. |
| `REPORT_HOUR` | `9` | Hour of the daily report. |
| `REPORT_MINUTE` | `0` | Minute of the daily report. |
| `REPORT_TZ` | `Europe/Warsaw` | IANA timezone of the report schedule. |
| `SQLITE_PATH` | `/data/travian.db` | SQLite database file path — env only. |
| `DASHBOARD_BIND` | `127.0.0.1` | Dashboard bind address — env only. Compose overrides to `0.0.0.0` (see Security). |
| `DASHBOARD_PORT` | `8090` | Dashboard port — env only. |
| `DASHBOARD_TOKEN` | *(empty)* | Bearer token required for dashboard actions — env only. |
| `DASHBOARD_LOOPBACK_ONLY` | `false` | `true` disables the token requirement when bound to loopback — env only. |
| `REPORT_EMBED_COLOR` | `0x2ECC71` | Embed accent color as a hex value. |
| `BACKFILL_DSN` | *(empty)* | Read-only Postgres DSN of the source snapshot DB (see Backfill). |

## Run with Docker

Prerequisities: Docker with the Compose plugin.

```bash
cp .env.example .env      # fill DISCORD_TOKEN and CHANNEL_ID at minimum
docker compose up -d      # builds locally OR pulls the GHCR image
docker compose logs -f    # watch startup
```

- The container runs as a non-root user; SQLite lives on the `travian-data`
  volume (`/data`), so data survives container recreation.
- On the very first run there is **no snapshot yet** — the daily report is
  skipped ("no data yet") until the first map.sql fetch completes. Use the
  dashboard **Fetch now** button (or wait for the scheduled fetch).
- The healthcheck (`/api/status`) only turns green with a **real**
  `DISCORD_TOKEN` in `.env` — the process exits with a clear error before the
  dashboard starts otherwise.

## Updating

```bash
./update.sh    # docker compose pull && docker compose up -d
```

The GHCR package is **public**, so `docker compose pull` works anonymously —
no `docker login` needed. `./update.sh` runs `docker manifest inspect` before
pulling and fails with a readable message if the image is missing (CI hasn't
pushed) or the package was made private again (then log in once per server:
`echo <PAT> | docker login ghcr.io -u PeterPage2115 --password-stdin`, where
`<PAT>` is a GitHub Personal Access Token with the `read:packages` scope).

## Dashboard access

The dashboard listens on port **8090**.

- **Same machine (or Docker host)**: `http://127.0.0.1:8090`.
- **Remote server**: open an SSH tunnel and browse locally:

  ```bash
  ssh -L 8090:127.0.0.1:8090 user@server
  # then open http://127.0.0.1:8090 in your local browser
  ```

### Security model

The dashboard exposes admin actions (fetch, send report, edit settings) over
an unauthenticated HTTP API unless a token is configured:

| Setup | Token required? |
|---|---|
| Compose default (`DASHBOARD_BIND=0.0.0.0` + `DASHBOARD_LOOPBACK_ONLY=true` + host publish `127.0.0.1:8090:8090`) | **No** — the host-side loopback-only publish is the security boundary; the dashboard never leaves localhost. |
| Wider exposure (e.g. host publish on a routable interface) | **Yes** — set `DASHBOARD_TOKEN`; requests need `Authorization: Bearer <token>`. |
| Running outside Docker with `DASHBOARD_BIND` != loopback | **Yes** — token required. Loopback bind (`127.0.0.1`) with `DASHBOARD_LOOPBACK_ONLY=true` needs none. |

`DASHBOARD_LOOPBACK_ONLY=true` must only ever be combined with a
loopback-only bind (`127.0.0.1`, `localhost`, `::1`) — that is what
docker-compose.yml does. Do not copy that flag into a setup that binds a
routable address; there the token is mandatory.

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

## Troubleshooting

- **Container restarts in a loop** (visible in `docker compose logs`):
  startup validation failed — most often a missing `DISCORD_TOKEN` or
  `CHANNEL_ID`. Fix `.env` and run `docker compose up -d`; the loop stops as
  soon as the env is valid. Missing `ALLIANCE_TAGS` is **not** fatal: the
  daily report is skipped with a warning.
- **`FETCH_TZ` change requires server-midnight agreement**: `snapshot_date`
  is computed in `FETCH_TZ` — the map.sql file is generated at the Travian
  server's midnight. If `FETCH_TZ` disagrees with the server's timezone, the
  "today" snapshot never matches and the daily report is skipped as stale.
- **Container reports `unhealthy`**: `docker compose ps` shows unhealthy —
  the healthcheck only passes once the process is up with a real
  `DISCORD_TOKEN`. Check `docker compose logs` for the startup error.
- **`unauthorized`/`denied` when pulling**: the image was never published
  (CI builds only from `main` — check Actions) or the GHCR package was made
  private — log in to GHCR first (see Updating).
- **A day is missing from the report**: an empty/truncated map.sql response
  is treated as a fetch failure — no snapshot is saved that day (look for
  `empty parse` in the logs).
- **Reports look stale**: check that the fetch job actually ran (dashboard
  logs / `docker compose logs`); deltas across a gap are computed and logged,
  not skipped.

## Development

```bash
uv sync                 # install locked dependencies + dev tools
uv run pytest           # 295 tests
uv run ruff check .     # lint
uv run basedpyright src # strict type check (0 errors / 0 warnings / 0 notes)
```

Roadmap: v1.1 — cross-alliance conquest context, roster tracking, more
dashboard widgets.
