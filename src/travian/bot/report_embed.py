"""Discord embed builder for the daily report (task 8).

Pure: ``build_report_embed`` does no IO and reads no settings — the caller
(bot main, task 9) passes the color from settings and sends the Embed.
All user-facing text lives in ``travian.strings``.

Pinned structure: description ``Report for <server> — snapshot <date> —
<tags>`` (+ `` (baseline)`` with no previous snapshot); fields in order
Summary (last line "Regions controlled: N of M" when regions exist),
Regions (fenced control table; only when ``data.regions`` is non-empty,
cap 20 data rows), Standings (fenced table, only when ``data.standings``
is non-empty, OUR tags marked ★ with a footnote), New Villages (cap 15),
Lost Villages (cap 15), Top Players × 3 separate fields (cap 5), Victory
Points — ≤ 25 fields, values ≤ 1024. ``_split_into_fields`` packs plain
lines in order into values (≤ 1024 each, ≤ ``max_fields`` values, total ≤
``budget``); ``_fenced_chunks`` does the same for the code-fenced tables
(per-value cap ``_FIELD_MAX − 8`` for the fences, dropped lines become a
``…and N more`` line via ``_append_more``). Fixed blocks split at 1024
only; Regions additionally get ``6000 − fixed_len − description − footer
− 512`` (``_CHAR_MARGIN`` covers region field names + fence chars).
Unpacked lines become a ``…and N more`` line when it fits, else are
omitted (the Regions cap path warns). The Regions field cap
``25 − fixed_after_splits`` is enforced via ``max_fields`` — the char
budget always binds first in practice.

Delta rendering: None → "—", 0 → "±0", >0 → "+N", <0 → "−N" (U+2212).
Top Players — New Villages renders the strict-gained count carried by
``PlayerStat.gains`` ("+N villages"; "—" when gains is None).
"""

import logging
import math

import discord

from travian import strings
from travian.models import (
    AllianceStat,
    DeltaSummary,
    PlayerStat,
    RegionStat,
    ReportData,
    VillageEvent,
)

logger = logging.getLogger(__name__)

_EMBED_MAX = 6000  # Discord hard limit: total chars (title+desc+fields+footer)
_MAX_FIELDS = 25  # Discord hard limit: fields per embed
_FIELD_MAX = 1024  # Discord hard limit: chars per field value
_CHAR_MARGIN = 512  # plan: safety margin for region field names + separators
_DEFAULT_COLOR = 0x2ECC71  # plan: report embed color (settings may override)
_ITEM_CAP_NEW_LOST = 15  # plan: village event lines per New/Lost field
_ITEM_CAP_TOP = 5  # plan: player lines per Top Players field
_ITEM_CAP_REGIONS = 20  # plan: region data rows per Regions table (header + 20 rows ≈ 1008 chars)


def build_report_embed(
    data: ReportData,
    alliance_tags: list[str],
    snapshot_date: str | None,
    color: int = _DEFAULT_COLOR,
) -> discord.Embed:
    """Build the daily report embed from *data* (pure — returns the Embed).

    ``alliance_tags`` is the plan's parameter (the bot passes the resolved
    subset); ``data.alliance_tags`` is not used.
    """
    baseline = _is_baseline(data)
    description = _build_description(data, alliance_tags, snapshot_date, baseline)
    footer = _build_footer(snapshot_date)
    embed = discord.Embed(color=color, description=description)
    _ = embed.set_footer(text=footer)

    remaining = _MAX_FIELDS
    blocks: list[tuple[str, list[str]]] = []  # (name, values) per fixed block

    def pack(name: str, lines: list[str], item_cap: int | None = None, *, fenced: bool = False) -> None:
        nonlocal remaining
        if fenced:
            values = _fenced_chunks(lines, max_fields=remaining)
        else:
            dropped = 0
            if item_cap is not None and len(lines) > item_cap:
                dropped = len(lines) - item_cap
                lines = lines[:item_cap]
            values, split_dropped = _split_into_fields(lines, max_fields=remaining)
            _ = _append_more(values, dropped + split_dropped)
        blocks.append((name, values))
        remaining -= len(values)

    pack(strings.FIELD_SUMMARY, _summary_lines(data.summary, data.regions))
    if data.standings:
        pack(strings.FIELD_STANDINGS, _standings_lines(data.standings, alliance_tags), fenced=True)
    pack(strings.FIELD_NEW_VILLAGES, _new_village_lines(data.new_villages), item_cap=_ITEM_CAP_NEW_LOST)
    pack(strings.FIELD_LOST_VILLAGES, _lost_village_lines(data.lost_villages), item_cap=_ITEM_CAP_NEW_LOST)
    pack(
        strings.FIELD_TOP_PLAYERS_POPULATION,
        _top_player_lines(data.top_players, "population"),
        item_cap=_ITEM_CAP_TOP,
    )
    pack(strings.FIELD_TOP_PLAYERS_GROWTH, _top_player_lines(data.top_players, "growth"), item_cap=_ITEM_CAP_TOP)
    pack(
        strings.FIELD_TOP_PLAYERS_NEW_VILLAGES,
        _top_player_lines(data.top_players, "new_villages"),
        item_cap=_ITEM_CAP_TOP,
    )
    pack(strings.FIELD_VICTORY_POINTS, _victory_points_lines(data))

    fixed_len = sum(len(name) + len(value) for name, values in blocks for value in values)
    budget = _EMBED_MAX - fixed_len - len(description) - len(footer) - _CHAR_MARGIN

    if data.regions:
        region_lines = _region_lines(data.regions)
        header, rows = region_lines[0], region_lines[1:]
        cap_dropped = max(0, len(rows) - _ITEM_CAP_REGIONS)
        if cap_dropped:
            rows = rows[:_ITEM_CAP_REGIONS]
        regions_values = _fenced_chunks([header, *rows], max_fields=remaining, budget=budget)
        if cap_dropped and not _append_more(regions_values, cap_dropped, budget=budget):
            logger.warning(
                "regions truncated: %d of %d unrendered, no room for more-line",
                cap_dropped,
                len(rows) + cap_dropped,
            )
    elif remaining >= 1 and budget >= len(strings.NO_REGIONS):
        regions_values = [strings.NO_REGIONS]
    else:
        regions_values = []

    # Emission order (pinned): Summary, Regions, Standings, New Villages,
    # Lost Villages, Top Players × 3, Victory Points. Region values may be
    # empty (budget exhausted → field omitted) or a plain NO_REGIONS field.
    blocks_by_name = {name: values for name, values in blocks}
    for name in (
        strings.FIELD_SUMMARY,
        strings.FIELD_REGIONS,
        strings.FIELD_STANDINGS,
        strings.FIELD_NEW_VILLAGES,
        strings.FIELD_LOST_VILLAGES,
        strings.FIELD_TOP_PLAYERS_POPULATION,
        strings.FIELD_TOP_PLAYERS_GROWTH,
        strings.FIELD_TOP_PLAYERS_NEW_VILLAGES,
        strings.FIELD_VICTORY_POINTS,
    ):
        values = regions_values if name == strings.FIELD_REGIONS else blocks_by_name.get(name, [])
        for value in values:
            _ = embed.add_field(name=name, value=value, inline=False)
    return embed


def _is_baseline(data: ReportData) -> bool:
    s = data.summary
    return all(
        d is None for d in (s.villages_delta, s.population_delta, s.players_delta, s.vp_delta)
    )


def _build_description(
    data: ReportData,
    alliance_tags: list[str],
    snapshot_date: str | None,
    baseline: bool,
) -> str:
    parts = [strings.DESCRIPTION_REPORT.format(server=data.server)]
    if snapshot_date is not None:
        parts.append(strings.DESCRIPTION_SNAPSHOT.format(date=snapshot_date))
    if alliance_tags:
        parts.append(", ".join(alliance_tags))
    description = strings.DESCRIPTION_JOINER.join(parts)
    if baseline:
        description += strings.DESCRIPTION_BASELINE
    return description


def _build_footer(snapshot_date: str | None) -> str:
    if snapshot_date is None:
        return strings.FOOTER_NO_DATE
    return strings.FOOTER_TEMPLATE.format(date=snapshot_date)


def _render_delta(delta: int | None) -> str:
    if delta is None:
        return strings.DELTA_NONE
    if delta > 0:
        return f"{strings.DELTA_PLUS}{delta}"
    if delta < 0:
        return f"{strings.DELTA_MINUS}{-delta}"
    return strings.DELTA_ZERO


def _render_table_delta(delta: int | None) -> str:
    """Delta cell for the aligned tables: None → "—", 0 → "±0", >0 → "+N",
    <0 → "−N" (U+2212 MINUS SIGN) — thousands grouped (``_render_delta`` is
    ungrouped and stays for Summary/events)."""
    if delta is None:
        return strings.DELTA_NONE
    if delta > 0:
        return f"{strings.DELTA_PLUS}{delta:,}"
    if delta < 0:
        return f"{strings.DELTA_MINUS}{-delta:,}"
    return strings.DELTA_ZERO


def _summary_lines(summary: DeltaSummary, regions: list[RegionStat]) -> list[str]:
    lines = [
        strings.SUMMARY_LINE.format(
            label=strings.SUMMARY_VILLAGES, value=summary.villages, delta=_render_delta(summary.villages_delta)
        ),
        strings.SUMMARY_LINE.format(
            label=strings.SUMMARY_POPULATION, value=summary.population, delta=_render_delta(summary.population_delta)
        ),
        strings.SUMMARY_LINE.format(
            label=strings.SUMMARY_PLAYERS, value=summary.players, delta=_render_delta(summary.players_delta)
        ),
        strings.SUMMARY_LINE.format(label=strings.SUMMARY_VP, value=summary.vp, delta=_render_delta(summary.vp_delta)),
    ]
    if regions:
        lines.append(
            strings.SUMMARY_REGIONS_LINE.format(
                controlled=sum(1 for r in regions if r.share >= 0.5), total=len(regions)
            )
        )
    return lines


def _standings_lines(standings: list[AllianceStat], our_tags: list[str]) -> list[str]:
    """Fenced table: header + one row per tracked alliance + ★ footnote.

    OUR tags (``alliance_tags``) get the ★ marker — Markdown bold does not
    render inside code fences, so the footnote explains it. Rows keep the
    config order (families stay grouped).
    """
    ours = set(our_tags)
    return [
        strings.STANDINGS_TABLE_HEADER,
        *[
            strings.STANDINGS_TABLE_LINE.format(
                tag=(strings.STANDINGS_OURS_MARK + s.tag) if s.tag in ours else s.tag,
                pop=s.population,
                pop_delta=_render_table_delta(s.population_delta),
                vp=s.vp,
                vp_delta=_render_table_delta(s.vp_delta),
            )
            for s in standings
        ],
        strings.STANDINGS_OURS_FOOTNOTE,
    ]


def _new_village_lines(events: list[VillageEvent]) -> list[str]:
    if not events:
        return [strings.NO_NEW_VILLAGES]
    return [strings.VILLAGE_LINE.format(name=ev.village_name, x=ev.x, y=ev.y) for ev in events]


def _lost_village_lines(events: list[VillageEvent]) -> list[str]:
    if not events:
        return [strings.NO_LOST_VILLAGES]
    lines: list[str] = []
    for ev in events:
        match ev.event:
            case "lost_conquered":
                owner = ev.new_owner_tag or ev.new_owner_player or strings.OWNER_UNKNOWN
                lines.append(
                    strings.LOST_CONQUERED_LINE.format(name=ev.village_name, x=ev.x, y=ev.y, owner=owner)
                )
            case "lost_deleted":
                lines.append(strings.LOST_DELETED_LINE.format(name=ev.village_name, x=ev.x, y=ev.y))
            case "gained":
                # Not expected in the lost list; render the bare identity.
                lines.append(strings.VILLAGE_LINE.format(name=ev.village_name, x=ev.x, y=ev.y))
    return lines


def _top_player_lines(top_players: dict[str, list[PlayerStat]], key: str) -> list[str]:
    stats = top_players.get(key, [])
    if not stats:
        return [strings.NO_DATA_YET]
    match key:
        case "population":
            return [
                strings.TOP_PLAYER_POPULATION_LINE.format(
                    player=s.player_name, population=s.population, villages=s.villages
                )
                for s in stats
            ]
        case "growth":
            return [
                strings.TOP_PLAYER_GROWTH_LINE.format(player=s.player_name, growth=_render_delta(s.growth))
                for s in stats
            ]
        case "new_villages":
            return [
                strings.TOP_PLAYER_NEW_VILLAGES_LINE.format(
                    player=s.player_name, gains=s.gains if s.gains is not None else strings.DELTA_NONE
                )
                for s in stats
            ]
        case _:
            return [strings.NO_DATA_YET]  # unreachable: keys are pinned by the plan


def _region_lines(regions: list[RegionStat]) -> list[str]:
    """Fenced table: header + one row per region.

    ``bar`` is 6 cells (▓ fill proportional to share, ≥ 1.0 → 6 fills);
    ``to50`` is ✓ when share ≥ 0.5, "—" when the region has no population
    at all, else the population still needed to reach 50%.
    """
    rows: list[str] = []
    for r in regions:
        fills = min(6, round(r.share * 6))
        bar = strings.REGION_BAR_FILL * fills + strings.REGION_BAR_EMPTY * (6 - fills)
        if r.share >= 0.5:
            to50 = strings.REGION_CONTROLLED
        elif r.region_total_pop == 0:
            to50 = strings.DELTA_NONE
        else:
            to50 = strings.REGION_TO50_NEEDED.format(n=math.ceil(r.region_total_pop * 0.5) - r.our_pop)
        rows.append(
            strings.REGION_TABLE_LINE.format(
                region=r.region,
                bar=bar,
                share=r.share,
                pop=r.our_pop,
                vp_delta=_render_table_delta(r.vp_delta),
                to50=to50,
            )
        )
    return [strings.REGION_TABLE_HEADER, *rows]


def _victory_points_lines(data: ReportData) -> list[str]:
    return [strings.VICTORY_POINTS_LINE.format(value=data.vp_total, delta=_render_delta(data.vp_delta))]


def _split_into_fields(
    lines: list[str],
    *,
    max_fields: int | None = None,
    budget: int | None = None,
) -> tuple[list[str], int]:
    """Pack *lines* in order into values (each ≤ 1024 chars, ≤ *max_fields*
    values, total ≤ *budget*). Returns ``(values, dropped)``.
    """
    values: list[str] = []
    current: list[str] = []
    total = 0

    for i, line in enumerate(lines):
        if max_fields is not None and len(values) >= max_fields and not current:
            return values, len(lines) - i
        if len(line) > _FIELD_MAX:
            line = line[:_FIELD_MAX]
        candidate = "\n".join((*current, line)) if current else line
        if len(candidate) > _FIELD_MAX:
            if max_fields is not None and len(values) >= max_fields:
                return values, len(lines) - i
            value = "\n".join(current)
            values.append(value)
            total += len(value)
            current = []
            candidate = line
            if max_fields is not None and len(values) >= max_fields:
                return values, len(lines) - i  # cap reached — drop line and the rest
        if budget is not None and total + len(candidate) > budget:
            if current:
                value = "\n".join(current)
                values.append(value)
            return values, len(lines) - i
        current.append(line)
    if current:
        values.append("\n".join(current))
    return values, 0


def _fenced_chunks(
    lines: list[str],
    *,
    max_fields: int | None = None,
    budget: int | None = None,
) -> list[str]:
    """Pack *lines* in order into code-fenced values (triple-backtick blocks),
    each ≤ 1024 chars. Mirrors ``_split_into_fields`` with two changes: every
    value is wrapped in fences (per-value cap ``_FIELD_MAX − 8``) and dropped
    lines (max_fields/budget) become a ``…and N more`` line inside the last
    chunk via ``_append_more``.
    """
    values: list[str] = []
    current: list[str] = []
    total = 0
    cap = _FIELD_MAX - 8

    def finish(vals: list[str]) -> list[str]:
        wrapped = [f"```\n{value}\n```" for value in vals]
        # Defensive: the _FIELD_MAX − 8 per-value cap normally keeps the
        # wrapped value ≤ 1024; a mid-table more-line can still push it past
        # the limit, so clamp (pathological input only).
        return [value[:_FIELD_MAX] for value in wrapped]

    for i, line in enumerate(lines):
        if max_fields is not None and len(values) >= max_fields and not current:
            _ = _append_more(values, len(lines) - i, budget=budget)
            return finish(values)
        if len(line) > cap:
            line = line[:cap]
        candidate = "\n".join((*current, line)) if current else line
        if len(candidate) > cap:
            if max_fields is not None and len(values) >= max_fields:
                _ = _append_more(values, len(lines) - i, budget=budget)
                return finish(values)
            value = "\n".join(current)
            values.append(value)
            total += len(value)
            current = []
            candidate = line
            if max_fields is not None and len(values) >= max_fields:
                _ = _append_more(values, len(lines) - i, budget=budget)
                return finish(values)
        if budget is not None and total + len(candidate) > budget:
            if current:
                value = "\n".join(current)
                values.append(value)
                total += len(value)
            _ = _append_more(values, len(lines) - i, budget=budget)
            return finish(values)
        current.append(line)
    if current:
        values.append("\n".join(current))
    return finish(values)


def _append_more(values: list[str], dropped: int, *, budget: int | None = None) -> bool:
    if dropped <= 0:
        return True
    if not values:
        return False
    more = strings.MORE_LINE.format(n=dropped)
    candidate = f"{values[-1]}\n{more}"
    if len(candidate) > _FIELD_MAX:
        return False
    if budget is not None and sum(len(v) for v in values[:-1]) + len(candidate) > budget:
        return False
    values[-1] = candidate
    return True
