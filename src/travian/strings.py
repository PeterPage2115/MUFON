"""All user-facing English strings for the Discord report embeds (task 8) and
the /raport command (task 11).

Centralized here so no user-facing text is inlined in ``report_embed.py`` or
``commands.py`` — the embed builders and the slash command consume these. The
command NAME/description are Discord API surface rather than embed text, but
they follow the same centralization convention (decision, task 11).
Wording decisions (locked by tests, documented in learnings):

- delta rendering: "—" (no data), "±0" (no change), "+N", "−N" (U+2212 MINUS
  SIGN — typographically consistent with the em-dash separators).
- truncation line "…and N more" (… = U+2026 HORIZONTAL ELLIPSIS).
- separators are " — " (U+2014 em dash) and " · " (U+00B7 middle dot).
- truncated names end with "…" (U+2026): regions at 10 chars, standings
  tags at 7 (including the "★ " marker), village names at 24, player names
  at 18.
- the capped daily Regions/Standings cards are FIELD-based (inline fields,
  no code fences — Discord mobile renders them without horizontal scroll);
  the uncapped on-demand paths render the same blocks as proportional
  description lines with a short intro. No fixed-width table exists.
"""

# --- Embed description ------------------------------------------------------
DESCRIPTION_REPORT = "Report for {server}"
DESCRIPTION_SNAPSHOT = "snapshot {date}"
DESCRIPTION_JOINER = " — "
DESCRIPTION_BASELINE = " (baseline)"

# --- Footer ------------------------------------------------------------------
FOOTER_TEMPLATE = "map.sql snapshot {date} (midnight server time)"
FOOTER_NO_DATE = "map.sql snapshot (midnight server time)"

# --- Embed titles (one message, up to 4 embeds) --------------------------------
EMBED_TITLE_REPORT = "📊 Daily Report"
EMBED_TITLE_REGIONS = "🗺️ Regions"
EMBED_TITLE_STANDINGS = "⚔️ Standings"
EMBED_TITLE_VILLAGES = "🏗️ New & Lost Villages"

# --- Section headings (render only in descriptions) ----------------------------
# Summary/Regions/Standings cards carry NO heading: the embed title is the
# card heading. Village sections keep theirs (they share one embed).
HEADING_NEW_VILLAGES = "# New Villages"
HEADING_LOST_VILLAGES = "# Lost Villages"

# --- Summary KPI grid (inline fields, 3 per row) --------------------------------
KPI_VILLAGES = "Villages"
KPI_POPULATION = "Population"
KPI_PLAYERS = "Players"
KPI_VP = "VP"
KPI_REGIONS = "Regions"
KPI_NEW_LOST = "New / Lost"
KPI_VALUE = "{value:,} ({delta})"
KPI_VALUE_NO_DELTA = "{value:,}"  # parens dropped entirely when delta is None
KPI_REGIONS_VALUE = "{controlled} of {active} active regions controlled"
KPI_NEW_LOST_VALUE = "{new} new · {lost} lost"

# --- Village events ------------------------------------------------------------
VILLAGE_LINE = "**{name}** ({x}|{y})"  # region-absent fallback (no-region snapshot)
VILLAGE_FOUNDED_LINE = "**{name}** ({x}|{y}) — {region} — by {founder}"
VILLAGE_FOUNDED_NO_REGION_LINE = "**{name}** ({x}|{y}) — by {founder}"
LOST_CONQUERED_LINE = "**{name}** ({x}|{y}) — conquered by **{owner}**"  # region-absent fallback
LOST_CONQUERED_REGION_LINE = "**{name}** ({x}|{y}) — {region} — conquered by **{owner}**"
LOST_ALLIANCE_CHANGED_LINE = "**{name}** ({x}|{y}) — alliance changed to **{owner}**"  # region-absent fallback
LOST_ALLIANCE_CHANGED_REGION_LINE = "**{name}** ({x}|{y}) — {region} — alliance changed to **{owner}**"
LOST_DELETED_LINE = "**{name}** ({x}|{y}) — deleted"  # region-absent fallback
LOST_DELETED_REGION_LINE = "**{name}** ({x}|{y}) — {region} — deleted"
OWNER_UNKNOWN = "unknown"

# --- Discord cards (Standings + Regions) ----------------------------------------
# The capped DAILY cards render each item as an inline FIELD (name ≤ 256,
# value ≤ 1024, ≤ 25 fields per embed): Discord mobile lays fields out
# without horizontal scroll. The uncapped on-demand paths render the same
# blocks as proportional description lines (4096-char budget) — no
# fixed-width fence anywhere.
REGION_FIELD_NAME = "{region} · {share}"
REGION_FIELD_VALUE = "{pop:,} pop\nΔ {share_delta} · VP {vp_delta} · {to50}"
REGION_MORE_FIELDS = "More regions"
REGION_MORE_FIELDS_VALUE = "{n} not shown"
REGION_LEGEND_FIELD = "Legend"
REGION_LEGEND_FIELD_VALUE = "✓ controlled · +N needed · — inactive · Δ share vs previous snapshot"
REGION_MOVERS_FIELD = "Biggest moves"
REGION_MOVERS_FIELD_VALUE = "{best} · {worst}"
REGION_MOVERS_SINGLE_FIELD = "Biggest move"
REGION_MOVERS_SINGLE_FIELD_VALUE = "{move}"
STANDINGS_FIELD_NAME = "{marker}{tag}"
STANDINGS_FIELD_VALUE = "Pop {pop:,} · Δ {pop_delta}\nVP {vp:,} · Δ {vp_delta}"
STANDINGS_MARKER = "★ "  # marker + tag stay within 7 visible chars
STANDINGS_MORE_FIELDS = "More alliances"
STANDINGS_MORE_FIELDS_VALUE = "{n} not shown"
STANDINGS_LEGEND_FIELD = "Legend"
STANDINGS_LEGEND_FIELD_VALUE = "★ our alliances"
REGION_TEXT_LINE = "{region} · {share} · {pop}"
REGION_TEXT_DELTA_LINE = "Δ {share_delta} · VP {vp_delta} · {to50}"
REGION_DESCRIPTION_INTRO = "Control share and change vs previous snapshot"
REGION_INACTIVE_HEADING = "Inactive regions"
STANDINGS_TEXT_LINE = "{tag} · Pop {pop:,} · Δ {pop_delta} · VP {vp:,} · Δ {vp_delta}"
STANDINGS_DESCRIPTION_INTRO = "Population and VP · change vs previous snapshot"
REGION_CONTROLLED = "✓"
REGION_INACTIVE_CELL = "—"  # To-50% cell for inactive regions (same glyph as DELTA_NONE, different column)
REGION_TO50_NEEDED = "+{n:,}"

# --- Delta rendering ---------------------------------------------------------------
DELTA_NONE = "—"
DELTA_ZERO = "±0"
DELTA_PLUS = "+"
DELTA_MINUS = "−"  # U+2212 MINUS SIGN

# --- Truncation --------------------------------------------------------------------
MORE_LINE = "…and {n} more"
NO_DATA_YET = "No data yet."

# --- Failure alert (freshness & alerts) ---------------------------------------------
# Opt-in Discord alert after a terminal fetch/report failure: title, a
# description with the job/time, the normalized one-line reason and a pointer
# to the dashboard job log.
ALERT_TITLE = "MUFON job failure"
ALERT_DESCRIPTION = "{job} failed at {occurred_at}.\n{reason}\n\nSee the dashboard job log for details."

# --- /raport command (task 11) ------------------------------------------------------
COMMAND_RAPORT_DESCRIPTION = "Send the daily report now (admin only)"
RAPORT_NO_PERMISSION = "No permission: /raport is restricted to admins."
RAPORT_SENT = "Report sent"
RAPORT_ERROR = "Something went wrong while sending the report."

# --- /wioski + /regiony commands (report trim) --------------------------------------
COMMAND_WIOSKI_DESCRIPTION = "Village events (new/lost) for the latest day (admin only)"
COMMAND_REGIONS_DESCRIPTION = "Full regions table with Δ % (admin only)"
COMMAND_NO_PERMISSION = "No permission: this command is restricted to admins."
WIOSKI_NO_EVENTS = "No village events in the latest snapshot pair."
REGIONS_NO_DATA = "No region data yet."
