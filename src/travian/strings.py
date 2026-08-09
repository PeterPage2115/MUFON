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
- truncated names end with "…" (U+2026): regions at 12 chars, standings
  tags at 7, village names at 24, player names at 18.
- table headers are COMPUTED with the same cell widths as the data rows so
  the labels sit exactly over their columns — never hand-pad them.
"""

# --- Embed description ------------------------------------------------------
DESCRIPTION_REPORT = "Report for {server}"
DESCRIPTION_SNAPSHOT = "snapshot {date}"
DESCRIPTION_JOINER = " — "
DESCRIPTION_BASELINE = " (baseline)"

# --- Footer ------------------------------------------------------------------
FOOTER_TEMPLATE = "map.sql snapshot {date} (midnight server time)"
FOOTER_NO_DATE = "map.sql snapshot (midnight server time)"

# --- Embed titles (one message, up to 5 embeds) --------------------------------
EMBED_TITLE_REPORT = "📊 Daily Report"
EMBED_TITLE_REGIONS = "🗺️ Regions"
EMBED_TITLE_STANDINGS = "⚔️ Standings"
EMBED_TITLE_VILLAGES = "🏗️ New & Lost Villages"
EMBED_TITLE_TOP_PLAYERS = "🏆 Top Players"
EMBED_TITLE_VICTORY_POINTS = "🏆 Victory Points"  # fallback when all top lists are omitted

# --- Section headings (render only in descriptions) ----------------------------
HEADING_SUMMARY = "# Summary"
HEADING_REGIONS = "# Regions"
HEADING_STANDINGS = "# Standings"
HEADING_NEW_VILLAGES = "# New Villages"
HEADING_LOST_VILLAGES = "# Lost Villages"
HEADING_TOP_PLAYERS = "# Top Players"
HEADING_VICTORY_POINTS = "# Victory Points"
HEADING_TOP_POPULATION = "### Population"
HEADING_TOP_GROWTH = "### Growth"
HEADING_TOP_NEW_VILLAGES = "### New Villages"

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
LOST_DELETED_LINE = "**{name}** ({x}|{y}) — deleted"  # region-absent fallback
LOST_DELETED_REGION_LINE = "**{name}** ({x}|{y}) — {region} — deleted"
OWNER_UNKNOWN = "unknown"

# --- Tables (Standings + Regions, monospace-aligned) --------------------------------
# Column positions are load-bearing (see report_embed): standings tag col 0,
# pop col 8, Δ pop col 16, VP col 24, Δ VP col 32 (39-char lines); region
# name col 0, bar col 13, share col 20, pop col 27, Δ % col 35, VP Δ col 43,
# to 50% col 51 (58-char lines). Headers are built with the SAME widths as the
# data cells (numeric columns right-aligned) — do not "tidy".
STANDINGS_TABLE_HEADER = f"{'Tag':<7} {'Pop':>7} {'Δ Pop':>7} {'VP':>7} {'Δ VP':>7}"
STANDINGS_TABLE_LINE = "{tag:<7} {pop:>7,} {pop_delta:>7} {vp:>7,} {vp_delta:>7}"
STANDINGS_TABLE_DIVIDER = "─" * 39  # U+2500
STANDINGS_OURS_MARK = "★"
STANDINGS_OURS_FOOTNOTE = "_★ our alliances_"
REGION_TABLE_HEADER = f"{'Region':<12} {'Control':<13} {'Pop':>7} {'Δ %':>7} {'VP Δ':>7} {'To 50%':>7}"
REGION_TABLE_LINE = "{region:<12} {bar} {share:>6.1%} {pop:>7,} {share_delta:>7} {vp_delta:>7} {to50:>7}"
REGION_TABLE_DIVIDER = "─" * 58  # U+2500
REGION_BAR_FILL = "▓"
REGION_BAR_EMPTY = "░"
REGION_CONTROLLED = "✓"
REGION_INACTIVE_CELL = "—"  # To-50% cell for inactive regions (same glyph as DELTA_NONE, different column)
REGION_TO50_NEEDED = "+{n:,}"
REGION_LEGEND = "_✓ = we control (active region, >50% of its population) · +N = population still needed for control · — = inactive (total population below 4,000) · Δ % = our control change vs yesterday_"

# --- Top players + Victory points ------------------------------------------------
TOP_PLAYER_RANK_LINE = "**{rank}.** {player} — {value}"
VICTORY_POINTS_LINE = "Total: {value:,} ({delta})"
VICTORY_POINTS_NO_DELTA = "Total: {value:,}"  # parens dropped entirely when delta is None

# --- Delta rendering ---------------------------------------------------------------
DELTA_NONE = "—"
DELTA_ZERO = "±0"
DELTA_PLUS = "+"
DELTA_MINUS = "−"  # U+2212 MINUS SIGN

# --- Truncation --------------------------------------------------------------------
MORE_LINE = "…and {n} more"
NO_DATA_YET = "No data yet."

# --- /raport command (task 11) ------------------------------------------------------
COMMAND_RAPORT_DESCRIPTION = "Send the daily report now (admin only)"
RAPORT_NO_PERMISSION = "No permission: /raport is restricted to admins."
RAPORT_SENT = "Report sent"
RAPORT_ERROR = "Something went wrong while sending the report."
