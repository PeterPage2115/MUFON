"""Discord embed builder for the daily report (task 8).

Pure: ``build_report_embed`` does no IO and reads no settings — the caller
(bot main, task 9) passes the color from settings and sends the Embeds.
All user-facing text lives in ``travian.strings``.

Pinned structure: ONE message with up to 4 embeds, in order —

1. ``📊 Daily Report`` (only when ``"summary"`` is in ``sections`` — always
   true for the daily message): description = context line (server —
   snapshot — tags, `` (baseline)`` when there is no previous snapshot)
   + ``# Summary``; inline KPI fields (Villages, Population, Players, VP,
   Regions, New / Lost), 3 per row on desktop.
2. ``🗺️ Regions`` (only when ``"regions"`` in ``sections`` and
   ``data.regions`` non-empty): fenced control table in the description —
   ACTIVE regions first (total population ≥ 4,000, the game rule), then the
   inactive ones after a divider. Columns: region (0), control bar (13),
   share (20), pop (27), Δ % (35), VP Δ (43), To 50% (51); 58-char rows.
   To 50%: ✓ when the region is controlled (active AND strictly > 50% of
   the total population), ``+N`` when active and short, ``—`` when
   inactive. Δ % is our control-share change vs yesterday ("—" on baseline
   days); the legend below the fence explains every symbol. With
   ``region_limit`` set, only the top *limit* ACTIVE rows render and the
   rest (remaining active + all inactive) collapse behind a ``…and N more``
   line; the movers line below the legend names the best and worst Δ %
   moves of the day when any delta exists. The ``…and N more`` guard also
   fits the table to the 4096-char description.
3. ``⚔️ Standings`` (only when ``"standings"`` in ``sections`` and
   ``data.standings`` non-empty): fenced table, OUR tags first (config
   order within the two groups), ★ marker + footnote (Markdown bold does
   not render inside code fences).
4. ``🏗️ New & Lost Villages`` (only when ``"villages"`` in ``sections`` and
   either list is non-empty): bold event lines under ``#`` headings, cap 15
   per list. New lines show the region and founder (``by <player>``), lost
   lines the region and conqueror (``conquered by <tag>`` / ``deleted``);
   the metrics layer pre-sorts so new villages group by region and lost
   villages by conqueror with deleted last.

``sections`` is a subset of ``REPORT_SECTIONS``; the daily message uses
``DAILY_SECTIONS`` (summary + regions + standings, regions capped at 8
active rows) and the on-demand commands request a single section.

Colors: embed 1 uses the configured color; embeds 2–4 use the fixed palette
constants. Every embed carries the footer. ``#``/``###`` headings render
only in descriptions (not field values — discord-api-docs issue #7167);
code fences render in descriptions, so the tables live there instead of
fields (no 1024-char field cap, no multi-field splitting).

Delta rendering: None → "—", 0 → "±0", >0 → "+N", <0 → "−N" (U+2212);
thousands are grouped in tables and KPIs.
"""

import logging
from collections.abc import Set as AbstractSet
from typing import Literal

import discord

from travian import strings
from travian.analysis import region_active, region_controlled, to50_needed
from travian.models import (
    AllianceStat,
    RegionStat,
    ReportData,
    VillageEvent,
)

logger = logging.getLogger(__name__)

_EMBED_MAX = 6000  # Discord hard limit: total chars (title+desc+fields+footer)
_DESCRIPTION_MAX = 4096  # Discord hard limit: chars per embed description
_DEFAULT_COLOR = 0x2ECC71  # plan: report embed color (settings may override)
_ITEM_CAP_NEW_LOST = 15  # plan: village event lines per New/Lost section
_COLOR_REGIONS = 0x1ABC9C  # teal
_COLOR_STANDINGS = 0xE67E22  # orange
_COLOR_VILLAGES = 0x3498DB  # blue
#: Semantic error color for failure alerts (same token family as the UI's
#: ``--status-error``).
ALERT_COLOR = 0xD47769
_ALERT_REASON_MAX = 500  # generous one-line cap: far below Discord's 4096-char description

#: Every embed a report can carry; ``sections`` must be a subset of this.
REPORT_SECTIONS = frozenset({"summary", "regions", "standings", "villages"})
#: The daily message's subset — villages (and everything after it) left out.
DAILY_SECTIONS = frozenset({"summary", "regions", "standings"})


def build_report_embed(
    data: ReportData,
    alliance_tags: list[str],
    snapshot_date: str | None,
    color: int = _DEFAULT_COLOR,
    *,
    sections: AbstractSet[str] = REPORT_SECTIONS,
    region_limit: int | None = None,
) -> list[discord.Embed]:
    """Build the report embeds from *data* (pure — up to 4 Embeds for ONE
    message, in the pinned order; see the module docstring).

    ``sections`` selects which embeds to build (``DAILY_SECTIONS`` for the
    daily message, single-section sets for the on-demand commands);
    ``region_limit`` caps the Regions table to the top *limit* ACTIVE rows
    with the rest collapsed behind a ``…and N more`` line. ``alliance_tags``
    is the plan's parameter (the bot passes the resolved subset);
    ``data.alliance_tags`` is not used.
    """
    baseline = _is_baseline(data)
    description = _build_description(data, alliance_tags, snapshot_date, baseline)
    footer = _build_footer(snapshot_date)

    embeds: list[discord.Embed] = []
    if "summary" in sections:
        embeds.append(_summary_embed(data, description, footer, color))
    if "regions" in sections and data.regions:
        embeds.append(_regions_embed(data.regions, footer, limit=region_limit))
    if "standings" in sections and data.standings:
        embeds.append(_standings_embed(data.standings, alliance_tags, footer))
    if "villages" in sections and (data.new_villages or data.lost_villages):
        embeds.append(_villages_embed(data, footer))
    return embeds


def build_failure_alert(job: Literal["fetch", "report"], reason: str, occurred_at: str) -> discord.Embed:
    """One alert embed for a terminal ``job`` failure (pure, unit-testable).

    ``reason`` is normalized to a single line and capped so a long exception
    cannot exceed Discord limits or produce a multi-line alert;
    ``occurred_at`` is the UTC ISO failure timestamp. All user-facing text
    lives in ``travian.strings``.
    """
    return discord.Embed(
        title=strings.ALERT_TITLE,
        description=strings.ALERT_DESCRIPTION.format(
            job=job,
            occurred_at=occurred_at,
            reason=_normalize_reason(reason),
        ),
        color=ALERT_COLOR,
    )


def _normalize_reason(reason: str, max_len: int = _ALERT_REASON_MAX) -> str:
    """Collapse ``reason`` to ONE line (whitespace/newlines → single spaces)
    and truncate to ``max_len`` chars with a trailing ``…``."""
    line = " ".join(reason.split())
    return _truncate(line, max_len) if len(line) > max_len else line


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


def _render_share_delta(delta: float | None) -> str:
    """Δ % cell: None → "—", |d| < 0.05 pp → "±0.0%", else "+2.1%" / "−0.5%"
    (U+2212 MINUS SIGN) — the legend explains the column."""
    if delta is None:
        return strings.DELTA_NONE
    if abs(delta) < 0.0005:
        return strings.DELTA_ZERO + ".0%"
    if delta > 0:
        return f"{strings.DELTA_PLUS}{delta:.1%}"
    return f"{strings.DELTA_MINUS}{-delta:.1%}"


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
                controlled=sum(1 for r in data.regions if region_controlled(r)),
                active=sum(1 for r in data.regions if region_active(r)),
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


def _region_line(r: RegionStat) -> str:
    fills = min(6, round(r.share * 6))
    bar = strings.REGION_BAR_FILL * fills + strings.REGION_BAR_EMPTY * (6 - fills)
    needed = to50_needed(r)
    if region_controlled(r):
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
        share_delta=_render_share_delta(r.share_delta),
        vp_delta=_render_table_delta(r.vp_delta),
        to50=to50,
    )


def _mover_token(delta: float, region: str) -> str:
    """One movers-line token: ``+3.3% Corinium`` / ``−5.3% Teutones``."""
    return f"{_render_share_delta(delta)} {region}"


def _region_movers_line(regions: list[RegionStat]) -> str | None:
    """The best/worst Δ % movers line, or None when no region has a delta.

    Selection (deterministic): every region with a non-None ``share_delta``
    is a candidate; best = max by ``(share_delta, region)``, worst = min by
    the same key (delta ties break by region name). A single candidate
    renders the one-move form.
    """
    candidates = [(r.share_delta, r.region) for r in regions if r.share_delta is not None]
    if not candidates:
        return None
    best = max(candidates, key=lambda c: (c[0], c[1]))
    worst = min(candidates, key=lambda c: (c[0], c[1]))
    if best == worst:
        return strings.REGION_MOVERS_SINGLE.format(move=_mover_token(*best))
    return strings.REGION_MOVERS_LINE.format(best=_mover_token(*best), worst=_mover_token(*worst))


def _regions_embed(regions: list[RegionStat], footer: str, limit: int | None = None) -> discord.Embed:
    """Embed 2: fenced control table — active regions first, inactive after a
    divider. With ``limit`` only the top *limit* active rows render and the
    rest (remaining active + all inactive) collapse behind a ``…and N more``
    line (preceded by the divider); the movers line follows the legend when
    any region has a Δ %."""
    active = sorted((r for r in regions if region_active(r)), key=lambda r: (-r.share, r.region))
    inactive = sorted((r for r in regions if not region_active(r)), key=lambda r: (-r.share, r.region))
    lines = [strings.REGION_TABLE_HEADER, strings.REGION_TABLE_DIVIDER]
    if limit is not None:
        shown = min(limit, len(active))
        lines.extend(_region_line(r) for r in active[:shown])
        hidden = len(regions) - shown
        if hidden > 0:
            lines.append(strings.REGION_TABLE_DIVIDER)
            lines.append(strings.MORE_LINE.format(n=hidden))
    else:
        lines.extend(_region_line(r) for r in active)
        if inactive:
            lines.append(strings.REGION_TABLE_DIVIDER)
            lines.extend(_region_line(r) for r in inactive)
    prefix = f"{strings.HEADING_REGIONS}\n\n```\n"
    suffix = f"\n```\n\n{strings.REGION_LEGEND}"
    movers = _region_movers_line(regions)
    if movers is not None:
        suffix += f"\n{movers}"
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


def _new_village_line(ev: VillageEvent) -> str:
    name = _truncate(ev.village_name, 24)
    founder = ev.new_owner_player or strings.OWNER_UNKNOWN
    if ev.region is None:
        return strings.VILLAGE_FOUNDED_NO_REGION_LINE.format(name=name, x=ev.x, y=ev.y, founder=founder)
    return strings.VILLAGE_FOUNDED_LINE.format(name=name, x=ev.x, y=ev.y, region=ev.region, founder=founder)


def _lost_village_line(ev: VillageEvent) -> str:
    name = _truncate(ev.village_name, 24)
    match ev.event:
        case "lost_conquered":
            owner = ev.new_owner_tag or ev.new_owner_player or strings.OWNER_UNKNOWN
            if ev.region is None:
                return strings.LOST_CONQUERED_LINE.format(name=name, x=ev.x, y=ev.y, owner=owner)
            return strings.LOST_CONQUERED_REGION_LINE.format(
                name=name, x=ev.x, y=ev.y, region=ev.region, owner=owner
            )
        case "lost_deleted":
            if ev.region is None:
                return strings.LOST_DELETED_LINE.format(name=name, x=ev.x, y=ev.y)
            return strings.LOST_DELETED_REGION_LINE.format(name=name, x=ev.x, y=ev.y, region=ev.region)
        case "gained":
            # Not expected in the lost list; render the bare identity.
            return strings.VILLAGE_LINE.format(name=name, x=ev.x, y=ev.y)


def _villages_embed(data: ReportData, footer: str) -> discord.Embed:
    """Embed 4: new/lost village events under ``#`` headings, cap 15 each."""
    parts: list[str] = []
    if data.new_villages:
        lines = [_new_village_line(ev) for ev in data.new_villages[:_ITEM_CAP_NEW_LOST]]
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
