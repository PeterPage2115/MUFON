"""Discord embed builder for the daily report (task 8).

Pure: ``build_report_embed`` does no IO and reads no settings — the caller
(bot main, task 9) passes the color from settings and sends the Embed.
All user-facing text lives in ``travian.strings``.

Pinned structure: description ``Report for <server> — snapshot <date> —
<tags>`` (+ `` (baseline)`` with no previous snapshot); fields in order
Summary, New Villages (cap 15), Lost Villages (cap 15), Top Players × 3
separate fields (cap 5), Regions, Victory Points — ≤ 25 fields, values
≤ 1024. ``_split_into_fields`` packs lines in order into values (≤ 1024
each, ≤ ``max_fields`` values, total ≤ ``budget``); fixed blocks split at
1024 only, Regions additionally get ``6000 − fixed_len − description −
footer − 512`` (``_CHAR_MARGIN`` covers region field names). Unpacked
lines become a ``…and N more`` line when it fits, else are omitted with a
warning. The Regions field cap ``25 − fixed_after_splits`` is enforced
via ``max_fields`` — the char budget always binds first in practice.

Delta rendering: None → "—", 0 → "±0", >0 → "+N", <0 → "−N" (U+2212).
DEVIATION: PlayerStat carries no gains count (T7 compromise), so the
"Top Players — New Villages" field renders the player's current village
count — the ranking ORDER carries the gains signal; v1.1 should add a
``gains`` field to PlayerStat (see learnings T8).
"""

import logging

import discord

from travian import strings
from travian.models import DeltaSummary, PlayerStat, RegionStat, ReportData, VillageEvent

logger = logging.getLogger(__name__)

_EMBED_MAX = 6000  # Discord hard limit: total chars (title+desc+fields+footer)
_MAX_FIELDS = 25  # Discord hard limit: fields per embed
_FIELD_MAX = 1024  # Discord hard limit: chars per field value
_CHAR_MARGIN = 512  # plan: safety margin for region field names + separators
_DEFAULT_COLOR = 0x2ECC71  # plan: report embed color (settings may override)
_ITEM_CAP_NEW_LOST = 15  # plan: village event lines per New/Lost field
_ITEM_CAP_TOP = 5  # plan: player lines per Top Players field


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

    def pack(name: str, lines: list[str], item_cap: int | None = None) -> None:
        nonlocal remaining
        dropped = 0
        if item_cap is not None and len(lines) > item_cap:
            dropped = len(lines) - item_cap
            lines = lines[:item_cap]
        values, split_dropped = _split_into_fields(lines, max_fields=remaining)
        _ = _append_more(values, dropped + split_dropped)
        blocks.append((name, values))
        remaining -= len(values)

    pack(strings.FIELD_SUMMARY, _summary_lines(data.summary))
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
        values, dropped = _split_into_fields(region_lines, max_fields=remaining, budget=budget)
        if dropped > 0 and not _append_more(values, dropped, budget=budget):
            logger.warning(
                "regions truncated: %d of %d unrendered, no room for more-line", dropped, len(region_lines)
            )
        regions_values = values
    elif remaining >= 1 and budget >= len(strings.NO_REGIONS):
        regions_values = [strings.NO_REGIONS]
    else:
        regions_values = []

    # Emission order (pinned): the six fixed blocks, then Regions, then
    # Victory Points — the VP block is always the last packed block.
    for name, values in blocks[:-1]:
        for value in values:
            _ = embed.add_field(name=name, value=value, inline=False)
    for value in regions_values:
        _ = embed.add_field(name=strings.FIELD_REGIONS, value=value, inline=False)
    for value in blocks[-1][1]:
        _ = embed.add_field(name=strings.FIELD_VICTORY_POINTS, value=value, inline=False)
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


def _summary_lines(summary: DeltaSummary) -> list[str]:
    return [
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
                strings.TOP_PLAYER_NEW_VILLAGES_LINE.format(player=s.player_name, villages=s.villages)
                for s in stats
            ]
        case _:
            return [strings.NO_DATA_YET]  # unreachable: keys are pinned by the plan


def _region_lines(regions: list[RegionStat]) -> list[str]:
    return [
        strings.REGION_LINE.format(
            region=r.region,
            villages=r.our_villages,
            population=r.our_pop,
            share=strings.SHARE_PERCENT_FORMAT.format(r.share * 100),
            delta=_render_delta(r.delta),
        )
        for r in regions
    ]


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
