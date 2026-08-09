"""All user-facing English strings for the Discord report embed (task 8).

Centralized here so no user-facing text is inlined in ``report_embed.py`` —
the embed builder (and only the builder) consumes these. Wording decisions
(locked by tests, documented in learnings):

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

# --- Village events ------------------------------------------------------------
VILLAGE_LINE = "**{name}** ({x}|{y})"
LOST_CONQUERED_LINE = "**{name}** ({x}|{y}) — conquered by {owner}"
LOST_DELETED_LINE = "**{name}** ({x}|{y}) — deleted"
OWNER_UNKNOWN = "unknown"

# --- Top players ----------------------------------------------------------------
TOP_PLAYER_POPULATION_LINE = "{player} — {population} ({villages})"
TOP_PLAYER_GROWTH_LINE = "{player} — {growth}"
TOP_PLAYER_NEW_VILLAGES_LINE = "{player} — +{gains} villages"

# --- Regions ---------------------------------------------------------------------
REGION_LINE = "{region} — {villages} vil · {population} pop ({share}%) · {delta}"
SHARE_PERCENT_FORMAT = "{:.1f}"

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
