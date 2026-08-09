"""All user-facing English strings for the Discord report embed (task 8) and
the /raport command (task 11).

Centralized here so no user-facing text is inlined in ``report_embed.py`` or
``commands.py`` — the embed builder and the slash command consume these. The
command NAME/description are Discord API surface rather than embed text, but
they follow the same centralization convention (decision, task 11).
Wording decisions (locked by tests, documented in learnings):

- delta rendering: "—" (no data), "±0" (no change), "+N", "−N" (U+2212 MINUS
  SIGN — typographically consistent with the em-dash separators).
- truncation line "…and N more" (… = U+2026 HORIZONTAL ELLIPSIS).
- separators are " — " (U+2014 em dash) and " · " (U+00B7 middle dot).
"""

# --- Embed description ------------------------------------------------------
DESCRIPTION_REPORT = "Report for {server}"
DESCRIPTION_SNAPSHOT = "snapshot {date}"
DESCRIPTION_JOINER = " — "
DESCRIPTION_BASELINE = " (baseline)"

# --- Footer ------------------------------------------------------------------
FOOTER_TEMPLATE = "map.sql snapshot {date} (midnight server time)"
FOOTER_NO_DATE = "map.sql snapshot (midnight server time)"

# --- Field names --------------------------------------------------------------
FIELD_SUMMARY = "Summary"
FIELD_STANDINGS = "Standings"
FIELD_NEW_VILLAGES = "New Villages"
FIELD_LOST_VILLAGES = "Lost Villages"
FIELD_TOP_PLAYERS_POPULATION = "Top Players — Population"
FIELD_TOP_PLAYERS_GROWTH = "Top Players — Growth"
FIELD_TOP_PLAYERS_NEW_VILLAGES = "Top Players — New Villages"
FIELD_REGIONS = "Regions"
FIELD_VICTORY_POINTS = "Victory Points"

# --- Summary ------------------------------------------------------------------
SUMMARY_LINE = "{label}: {value} ({delta})"
SUMMARY_VILLAGES = "Villages"
SUMMARY_POPULATION = "Population"
SUMMARY_PLAYERS = "Players"
SUMMARY_VP = "VP"
SUMMARY_REGIONS_LINE = "Regions controlled: {controlled} of {total}"

# --- Village events ------------------------------------------------------------
VILLAGE_LINE = "**{name}** ({x}|{y})"
LOST_CONQUERED_LINE = "**{name}** ({x}|{y}) — conquered by {owner}"
LOST_DELETED_LINE = "**{name}** ({x}|{y}) — deleted"
OWNER_UNKNOWN = "unknown"

# --- Top players ----------------------------------------------------------------
TOP_PLAYER_POPULATION_LINE = "{player} — {population} ({villages})"
TOP_PLAYER_GROWTH_LINE = "{player} — {growth}"
TOP_PLAYER_NEW_VILLAGES_LINE = "{player} — +{gains} villages"

# --- Tables (Standings + Regions, monospace-aligned) --------------------------------
# Column positions are load-bearing (see report_embed): standings tag col 0,
# pop col 8, Δ pop col 16, VP col 24, Δ VP col 32; region name col 0, bar col
# 13, control col 20, pop col 26, VP Δ col 33, to 50% col 41. Do not "tidy".
STANDINGS_TABLE_HEADER = "Tag      Pop      Δ Pop    VP       Δ VP"
STANDINGS_TABLE_LINE = "{tag:<7} {pop:>7,} {pop_delta:>7} {vp:>7,} {vp_delta:>7}"
STANDINGS_OURS_MARK = "★"
STANDINGS_OURS_FOOTNOTE = "★ our alliances"
REGION_TABLE_HEADER = "Region       Control      Pop      VP Δ    To 50%"
REGION_TABLE_LINE = "{region:<12} {bar} {share:>5.1%} {pop:>6,} {vp_delta:>7} {to50:>7}"
REGION_BAR_FILL = "▓"
REGION_BAR_EMPTY = "░"
REGION_CONTROLLED = "✓"
REGION_TO50_NEEDED = "+{n:,}"

# --- Victory points ---------------------------------------------------------------
VICTORY_POINTS_LINE = "Total: {value} ({delta})"

# --- Delta rendering ---------------------------------------------------------------
DELTA_NONE = "—"
DELTA_ZERO = "±0"
DELTA_PLUS = "+"
DELTA_MINUS = "−"  # U+2212 MINUS SIGN

# --- Truncation and empty states ----------------------------------------------------
MORE_LINE = "…and {n} more"
NO_NEW_VILLAGES = "No new villages."
NO_LOST_VILLAGES = "No lost villages."
NO_DATA_YET = "No data yet."
NO_REGIONS = "No regions."

# --- /raport command (task 11) ------------------------------------------------------
COMMAND_RAPORT_DESCRIPTION = "Send the daily report now (admin only)"
RAPORT_NO_PERMISSION = "No permission: /raport is restricted to admins."
RAPORT_SENT = "Report sent"
RAPORT_ERROR = "Something went wrong while sending the report."
