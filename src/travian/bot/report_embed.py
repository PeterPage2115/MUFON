"""Discord embed builder for the daily report (task 8).

Pure: ``build_report_embed`` does no IO and reads no settings — the caller
(bot main, task 9) passes the color from settings and sends the Embeds.
All user-facing text lives in ``travian.strings``.

Pinned structure: ONE message with up to 5 embeds, in order —

1. ``📊 Daily Report`` (always present): description = context line (server
   — snapshot — tags, `` (baseline)`` when there is no previous snapshot)
   + ``# Summary``; inline KPI fields (Villages, Population, Players, VP,
   Regions, New / Lost), 3 per row on desktop.
2. ``🗺️ Regions`` (only when ``data.regions`` is non-empty): fenced control
   table in the description — ACTIVE regions first (total population ≥
   4,000, the game rule), then the inactive ones after a divider. To 50%:
   ✓ when the region is controlled (active AND strictly > 50% of the total
   population), ``+N`` when active and short, ``—`` when inactive. The
   ``…and N more`` guard fits the table to the 4096-char description.
3. ``⚔️ Standings`` (only when ``data.standings`` is non-empty): fenced
   table, OUR tags first (config order within the two groups), ★ marker +
   footnote (Markdown bold does not render inside code fences).
4. ``🏗️ New & Lost Villages`` (only when either list is non-empty): bold
   event lines under ``#`` headings, cap 15 per list.
5. ``🏆 Top Players`` (title ``🏆 Victory Points`` when every top list is
   omitted; never omitted): ranked subsections (cap 5; Growth omitted when
   every delta is None, New Villages when every gain is 0) + the VP total.

Colors: embed 1 uses the configured color; embeds 2–5 use the fixed palette
constants. Every embed carries the footer. ``#``/``###`` headings render
only in descriptions (not field values — discord-api-docs issue #7167);
code fences render in descriptions, so the tables live there instead of
fields (no 1024-char field cap, no multi-field splitting).

Delta rendering: None → "—", 0 → "±0", >0 → "+N", <0 → "−N" (U+2212);
thousands are grouped in tables and KPIs.
"""

import logging
from collections.abc import Callable

import discord

from travian import strings
from travian.models import (
    AllianceStat,
    PlayerStat,
    RegionStat,
    ReportData,
    VillageEvent,
)

logger = logging.getLogger(__name__)

_EMBED_MAX = 6000  # Discord hard limit: total chars (title+desc+fields+footer)
_DESCRIPTION_MAX = 4096  # Discord hard limit: chars per embed description
_DEFAULT_COLOR = 0x2ECC71  # plan: report embed color (settings may override)
_REGION_ACTIVE_MIN = 4000  # game rule: a region is active with ≥ 4,000 total population
_ITEM_CAP_NEW_LOST = 15  # plan: village event lines per New/Lost section
_ITEM_CAP_TOP = 5  # plan: player lines per Top Players subsection
_COLOR_REGIONS = 0x1ABC9C  # teal
_COLOR_STANDINGS = 0xE67E22  # orange
_COLOR_VILLAGES = 0x3498DB  # blue
_COLOR_PLAYERS = 0xF1C40F  # gold


def build_report_embed(
    data: ReportData,
    alliance_tags: list[str],
    snapshot_date: str | None,
    color: int = _DEFAULT_COLOR,
) -> list[discord.Embed]:
    """Build the daily report embeds from *data* (pure — up to 5 Embeds for
    ONE message, in the pinned order; see the module docstring).

    ``alliance_tags`` is the plan's parameter (the bot passes the resolved
    subset); ``data.alliance_tags`` is not used.
    """
    baseline = _is_baseline(data)
    description = _build_description(data, alliance_tags, snapshot_date, baseline)
    footer = _build_footer(snapshot_date)

    embeds: list[discord.Embed] = [_summary_embed(data, description, footer, color)]
    if data.regions:
        embeds.append(_regions_embed(data.regions, footer))
    if data.standings:
        embeds.append(_standings_embed(data.standings, alliance_tags, footer))
    if data.new_villages or data.lost_villages:
        embeds.append(_villages_embed(data, footer))
    embeds.append(_players_embed(data, footer))
    return embeds


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


def _render_table_delta(delta: int | None) -> str:
    """Delta cell for tables and KPIs: None → "—", 0 → "±0", >0 → "+N",
    <0 → "−N" (U+2212 MINUS SIGN) — thousands grouped."""
    if delta is None:
        return strings.DELTA_NONE
    if delta > 0:
        return f"{strings.DELTA_PLUS}{delta:,}"
    if delta < 0:
        return f"{strings.DELTA_MINUS}{-delta:,}"
    return strings.DELTA_ZERO


def _summary_embed(data: ReportData, description: str, footer: str, color: int) -> discord.Embed:
    """Embed 1: context description + ``# Summary`` + inline KPI fields."""
    embed = discord.Embed(
        title=strings.EMBED_TITLE_REPORT,
        color=color,
        description=f"{description}\n\n{strings.HEADING_SUMMARY}",
    )
    _ = embed.set_footer(text=footer)

    def kpi(name: str, value: int, delta: int | None) -> None:
        value_text = (
            strings.KPI_VALUE.format(value=value, delta=_render_table_delta(delta))
            if delta is not None
            else strings.KPI_VALUE_NO_DELTA.format(value=value)
        )
        _ = embed.add_field(name=name, value=value_text, inline=True)
    kpi(strings.KPI_VILLAGES, data.summary.villages, data.summary.villages_delta)
    kpi(strings.KPI_POPULATION, data.summary.population, data.summary.population_delta)
    kpi(strings.KPI_PLAYERS, data.summary.players, data.summary.players_delta)
    kpi(strings.KPI_VP, data.summary.vp, data.summary.vp_delta)
    if data.regions:
        _ = embed.add_field(
            name=strings.KPI_REGIONS,
            value=strings.KPI_REGIONS_VALUE.format(
                controlled=sum(1 for r in data.regions if _region_controlled(r)),
                total=len(data.regions),
                active=sum(1 for r in data.regions if _region_active(r)),
            ),
            inline=True,
        )
    _ = embed.add_field(
        name=strings.KPI_NEW_LOST,
        value=strings.KPI_NEW_LOST_VALUE.format(
            new=len(data.new_villages), lost=len(data.lost_villages)
        ),
        inline=True,
    )
    return embed


def _region_active(r: RegionStat) -> bool:
    """Game rule: a region is active (control possible) with ≥ 4,000 total population."""
    return r.region_total_pop >= _REGION_ACTIVE_MIN


def _to50_needed(r: RegionStat) -> int | None:
    """Population still needed to exceed 50% — None when the region is inactive.

    ``(total // 2) + 1`` is the first population count strictly above half
    (the old ``ceil(total * 0.5)`` rendered "+0" at exactly 50% of an even
    total).
    """
    if not _region_active(r):
        return None
    return (r.region_total_pop // 2) + 1 - r.our_pop


def _region_controlled(r: RegionStat) -> bool:
    """Control = active region with strictly more than half the population."""
    needed = _to50_needed(r)
    return needed is not None and needed <= 0


def _region_line(r: RegionStat) -> str:
    fills = min(6, round(r.share * 6))
    bar = strings.REGION_BAR_FILL * fills + strings.REGION_BAR_EMPTY * (6 - fills)
    needed = _to50_needed(r)
    if _region_controlled(r):
        to50 = strings.REGION_CONTROLLED
    elif needed is None:
        to50 = strings.REGION_INACTIVE_CELL
    else:
        to50 = strings.REGION_TO50_NEEDED.format(n=needed)
    return strings.REGION_TABLE_LINE.format(
        region=_truncate(r.region, 12),
        bar=bar,
        share=r.share,
        pop=r.our_pop,
        vp_delta=_render_table_delta(r.vp_delta),
        to50=to50,
    )


def _regions_embed(regions: list[RegionStat], footer: str) -> discord.Embed:
    """Embed 2: fenced control table — active regions first, inactive after a divider."""
    active = sorted((r for r in regions if _region_active(r)), key=lambda r: (-r.share, r.region))
    inactive = sorted((r for r in regions if not _region_active(r)), key=lambda r: (-r.share, r.region))
    lines = [strings.REGION_TABLE_HEADER, strings.REGION_TABLE_DIVIDER]
    lines.extend(_region_line(r) for r in active)
    if inactive:
        lines.append(strings.REGION_TABLE_DIVIDER)
        lines.extend(_region_line(r) for r in inactive)
    prefix = f"{strings.HEADING_REGIONS}\n\n```\n"
    suffix = f"\n```\n\n{strings.REGION_INACTIVE_NOTE}"
    budget = _DESCRIPTION_MAX - len(prefix) - len(suffix)
    table = "\n".join(_fit_lines(lines, budget=budget))
    embed = discord.Embed(
        title=strings.EMBED_TITLE_REGIONS,
        color=_COLOR_REGIONS,
        description=prefix + table + suffix,
    )
    _ = embed.set_footer(text=footer)
    return embed


def _standings_embed(standings: list[AllianceStat], our_tags: list[str], footer: str) -> discord.Embed:
    """Embed 3: fenced comparison table — OUR tags first (★ marker), the rest
    in config order. Markdown bold does not render inside code fences, so the
    footnote explains the ★ (string decision)."""
    ours = set(our_tags)
    ordered = [s for s in standings if s.tag in ours] + [s for s in standings if s.tag not in ours]
    rows = [strings.STANDINGS_TABLE_HEADER, strings.STANDINGS_TABLE_DIVIDER]
    for s in ordered:
        tag = _truncate(s.tag, 7)
        if s.tag in ours:
            tag = _truncate(strings.STANDINGS_OURS_MARK + s.tag, 7)
        rows.append(
            strings.STANDINGS_TABLE_LINE.format(
                tag=tag,
                pop=s.population,
                pop_delta=_render_table_delta(s.population_delta),
                vp=s.vp,
                vp_delta=_render_table_delta(s.vp_delta),
            )
        )
    prefix = f"{strings.HEADING_STANDINGS}\n\n```\n"
    suffix = f"\n```\n\n{strings.STANDINGS_OURS_FOOTNOTE}"
    budget = _DESCRIPTION_MAX - len(prefix) - len(suffix)
    table = "\n".join(_fit_lines(rows, budget=budget))
    embed = discord.Embed(
        title=strings.EMBED_TITLE_STANDINGS,
        color=_COLOR_STANDINGS,
        description=prefix + table + suffix,
    )
    _ = embed.set_footer(text=footer)
    return embed


def _truncate(text: str, max_len: int) -> str:
    """Truncate *text* to *max_len* chars with a trailing … (U+2026)."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _lost_village_line(ev: VillageEvent) -> str:
    name = _truncate(ev.village_name, 24)
    match ev.event:
        case "lost_conquered":
            owner = ev.new_owner_tag or ev.new_owner_player or strings.OWNER_UNKNOWN
            return strings.LOST_CONQUERED_LINE.format(name=name, x=ev.x, y=ev.y, owner=owner)
        case "lost_deleted":
            return strings.LOST_DELETED_LINE.format(name=name, x=ev.x, y=ev.y)
        case "gained":
            # Not expected in the lost list; render the bare identity.
            return strings.VILLAGE_LINE.format(name=name, x=ev.x, y=ev.y)


def _villages_embed(data: ReportData, footer: str) -> discord.Embed:
    """Embed 4: new/lost village events under ``#`` headings, cap 15 each."""
    parts: list[str] = []
    if data.new_villages:
        lines = [
            strings.VILLAGE_LINE.format(name=_truncate(ev.village_name, 24), x=ev.x, y=ev.y)
            for ev in data.new_villages[:_ITEM_CAP_NEW_LOST]
        ]
        if len(data.new_villages) > _ITEM_CAP_NEW_LOST:
            lines.append(strings.MORE_LINE.format(n=len(data.new_villages) - _ITEM_CAP_NEW_LOST))
        parts.append(strings.HEADING_NEW_VILLAGES)
        parts.append("\n".join(lines))
    if data.lost_villages:
        lines = [_lost_village_line(ev) for ev in data.lost_villages[:_ITEM_CAP_NEW_LOST]]
        if len(data.lost_villages) > _ITEM_CAP_NEW_LOST:
            lines.append(strings.MORE_LINE.format(n=len(data.lost_villages) - _ITEM_CAP_NEW_LOST))
        parts.append(strings.HEADING_LOST_VILLAGES)
        parts.append("\n".join(lines))
    embed = discord.Embed(
        title=strings.EMBED_TITLE_VILLAGES,
        color=_COLOR_VILLAGES,
        description="\n\n".join(parts),
    )
    _ = embed.set_footer(text=footer)
    return embed


def _top_subsection(
    heading: str, stats: list[PlayerStat], value: Callable[[PlayerStat], str]
) -> str:
    lines = [
        strings.TOP_PLAYER_RANK_LINE.format(
            rank=i + 1, player=_truncate(s.player_name, 18), value=value(s)
        )
        for i, s in enumerate(stats[:_ITEM_CAP_TOP])
    ]
    if len(stats) > _ITEM_CAP_TOP:
        lines.append(strings.MORE_LINE.format(n=len(stats) - _ITEM_CAP_TOP))
    return f"{heading}\n\n" + "\n".join(lines)


def _population_value(s: PlayerStat) -> str:
    return f"{s.population:,} ({s.villages})"


def _growth_value(s: PlayerStat) -> str:
    return _render_table_delta(s.growth)


def _gains_value(s: PlayerStat) -> str:
    return f"+{s.gains or 0} villages"


def _players_embed(data: ReportData, footer: str) -> discord.Embed:
    """Embed 5: ranked top-player subsections + the VP total. Never omitted."""
    subsections: list[str] = []
    top = data.top_players
    population = top.get("population", [])
    if population:
        subsections.append(_top_subsection(strings.HEADING_TOP_POPULATION, population, _population_value))
    growth = top.get("growth", [])
    if growth and any(s.growth is not None for s in growth):
        subsections.append(_top_subsection(strings.HEADING_TOP_GROWTH, growth, _growth_value))
    new_villages = top.get("new_villages", [])
    if new_villages and any((s.gains or 0) > 0 for s in new_villages):
        subsections.append(_top_subsection(strings.HEADING_TOP_NEW_VILLAGES, new_villages, _gains_value))

    vp_line = (
        strings.VICTORY_POINTS_LINE.format(value=data.vp_total, delta=_render_table_delta(data.vp_delta))
        if data.vp_delta is not None
        else strings.VICTORY_POINTS_NO_DELTA.format(value=data.vp_total)
    )
    if subsections:
        title = strings.EMBED_TITLE_TOP_PLAYERS
        description = (
            f"{strings.HEADING_TOP_PLAYERS}\n\n"
            + "\n\n".join(subsections)
            + f"\n\n{strings.HEADING_VICTORY_POINTS}\n\n"
            + vp_line
        )
    else:
        title = strings.EMBED_TITLE_VICTORY_POINTS
        description = f"{strings.HEADING_VICTORY_POINTS}\n\n{vp_line}"
    embed = discord.Embed(title=title, color=_COLOR_PLAYERS, description=description)
    _ = embed.set_footer(text=footer)
    return embed


def _fit_lines(lines: list[str], *, budget: int) -> list[str]:
    """Drop trailing *lines* that don't fit *budget* (joined by ``\n``);
    when lines are dropped and the more-line fits, append MORE_LINE.
    Mirrors the old ``_split_into_fields`` budget semantics for one value."""
    kept: list[str] = []
    total = 0  # chars of "\n".join(kept)
    for i, line in enumerate(lines):
        if len(line) > _DESCRIPTION_MAX:
            line = line[:_DESCRIPTION_MAX]  # defensive clamp (pathological)
        candidate_len = total + (1 if kept else 0) + len(line)
        if candidate_len > budget:
            more = strings.MORE_LINE.format(n=len(lines) - i)
            if kept and total + 1 + len(more) <= budget:
                kept.append(more)
            return kept
        kept.append(line)
        total = candidate_len
    return kept
