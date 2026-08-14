"""Discord embed builder for the daily report (task 8).

Pure: ``build_report_embed`` does no IO and reads no settings — the caller
(bot main, task 9) passes the color from settings and sends the Embeds.
All user-facing text lives in ``travian.strings``.

Pinned structure: ONE message with up to 4 embeds, in order —

1. ``📊 Daily Report`` (only when ``"summary"`` is in ``sections`` — always
   true for the daily message): description = the context line (server —
   snapshot — tags, `` (baseline)`` when there is no previous snapshot);
   inline KPI fields (Villages, Population, Players, VP, Regions,
   New / Lost), 3 per row on desktop. The embed title is the card heading —
   no ``# Summary`` inside the description.
2. ``🗺️ Regions`` (only when ``"regions"`` in ``sections`` and
   ``data.regions`` non-empty): ACTIVE regions first (total population ≥
   4,000, the game rule), then the inactive ones. Capped (``region_limit``
   set, the daily card): each shown region is one inline field
   (``<region> · <share>`` / ``<pop> pop + Δ line``) and the tail collapses
   behind a ``More regions`` field; ``Legend`` explains the glyphs and a
   ``Biggest moves`` field names the best/worst Δ % moves when deltas
   exist. Uncapped (``/regiony``): the same blocks as proportional
   description lines under the intro ``Control share and change vs previous
   snapshot``, inactive regions after the ``Inactive regions`` heading,
   truncated only by the 4096-char budget. To 50%: ✓ when the region is
   controlled (active AND strictly > 50% of the total population), ``+N``
   when active and short, ``—`` when inactive. Δ % is our control-share
   change vs yesterday ("—" on baseline days). ``region_names`` (exact
   snapshot names, max 10) restricts the list AND the Summary KPI to those
   regions.
3. ``⚔️ Standings`` (only when ``"standings"`` in ``sections`` and
   ``data.standings`` non-empty): OUR tags first (config order within the
   two groups). Capped (``standings_limit`` set, the daily card): each
   selected alliance is one inline field (``<★ tag>`` / pop+VP with Δs), a
   ``More alliances`` field collapses the tail and ``Legend`` (``★ our
   alliances``) always closes the card. Uncapped: the same rows as
   proportional description lines under the intro ``Population and VP ·
   change vs previous snapshot``.
4. ``🏗️ New & Lost Villages`` (only when ``"villages"`` in ``sections`` and
   either list is non-empty): bold event lines under ``#`` headings, cap 15
   per list. New lines show the region and founder (``by <player>``), lost
   lines the region and conqueror (``conquered by <tag>`` / ``deleted``);
   the metrics layer pre-sorts so new villages group by region and lost
   villages by conqueror with deleted last.

``sections`` is a subset of ``REPORT_SECTIONS``; the daily message uses
``DAILY_SECTIONS`` (summary + regions + standings, regions and standings
capped at 10 rows each) and the on-demand commands request a single section.

Colors: embed 1 uses the configured color; embeds 2–4 use the fixed palette
constants. Every embed carries the footer. ``#``/``###`` headings render
only in descriptions (not field values — discord-api-docs issue #7167), so
the village sections keep their headings while the capped cards use fields
(no code fences, no 1024-char multi-field splitting).

Delta rendering: None → "—", 0 → "±0", >0 → "+N", <0 → "−N" (U+2212);
thousands are grouped in tables and KPIs.
"""

import logging
from collections.abc import Sequence
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
    region_names: Sequence[str] | None = None,
    standings_limit: int | None = None,
) -> list[discord.Embed]:
    """Build the report embeds from *data* (pure — up to 4 Embeds for ONE
    message, in the pinned order; see the module docstring).

    ``sections`` selects which embeds to build (``DAILY_SECTIONS`` for the
    daily message, single-section sets for the on-demand commands);
    ``region_limit`` caps the Regions card to the top *limit* ACTIVE regions
    with the rest collapsed behind a ``More regions`` field.
    ``region_names`` (exact snapshot region names, max 10) restricts the
    Regions list AND the Summary KPI Regions field to those regions — the
    filter runs BEFORE the KPI, movers and body, so all three describe the
    same scope; unknown names are dropped, an empty match set is up to the
    caller to fall back. ``standings_limit`` caps the Standings table to the
    top *limit* alliances by current population (tag ASC tie-break) before
    the ★/ours-first ordering. ``alliance_tags`` is the plan's parameter
    (the bot passes the resolved subset); ``data.alliance_tags`` is not
    used.
    """
    baseline = _is_baseline(data)
    description = _build_description(data, alliance_tags, snapshot_date, baseline)
    footer = _build_footer(snapshot_date)

    regions = data.regions
    if region_names:
        wanted = set(region_names)
        regions = [r for r in regions if r.region in wanted]

    embeds: list[discord.Embed] = []
    if "summary" in sections:
        embeds.append(_summary_embed(data, description, footer, color, regions=regions))
    if "regions" in sections and regions:
        embeds.append(_regions_embed(regions, footer, limit=region_limit))
    if "standings" in sections and data.standings:
        embeds.append(_standings_embed(data.standings, alliance_tags, footer, limit=standings_limit))
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


def _summary_embed(
    data: ReportData,
    description: str,
    footer: str,
    color: int,
    regions: list[RegionStat],
) -> discord.Embed:
    """Embed 1: context description + inline KPI fields — no ``# Summary``
    heading (the embed title is the card heading).

    ``regions`` is the (possibly REPORT_REGIONS-filtered) region list — the
    Regions KPI must describe the same scope as the Regions embed."""
    embed = discord.Embed(
        title=strings.EMBED_TITLE_REPORT,
        color=color,
        description=description,
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
    if regions:
        _ = embed.add_field(
            name=strings.KPI_REGIONS,
            value=strings.KPI_REGIONS_VALUE.format(
                controlled=sum(1 for r in regions if region_controlled(r)),
                active=sum(1 for r in regions if region_active(r)),
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


def _region_to50(r: RegionStat) -> str:
    """The To-50% cell: ✓ when controlled, — when inactive, else +N needed."""
    needed = to50_needed(r)
    if region_controlled(r):
        return strings.REGION_CONTROLLED
    if needed is None:
        return strings.REGION_INACTIVE_CELL
    return strings.REGION_TO50_NEEDED.format(n=needed)


def _mover_token(delta: float, region: str) -> str:
    """One movers token: ``+3.3% Corinium`` / ``−5.3% Teutones``."""
    return f"{_render_share_delta(delta)} {region}"


def _region_movers_field(regions: list[RegionStat]) -> tuple[str, str] | None:
    """The best/worst Δ % movers as a (name, value) field, or None when no
    region has a delta.

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
        return (
            strings.REGION_MOVERS_SINGLE_FIELD,
            strings.REGION_MOVERS_SINGLE_FIELD_VALUE.format(move=_mover_token(*best)),
        )
    return (
        strings.REGION_MOVERS_FIELD,
        strings.REGION_MOVERS_FIELD_VALUE.format(best=_mover_token(*best), worst=_mover_token(*worst)),
    )


def _region_text_lines(r: RegionStat) -> list[str]:
    """The proportional two-line rendering of one region (uncapped list):
    ``<region> · <share> · <pop>`` and ``Δ <share_delta> · VP <vp_delta> ·
    <to50>`` — no fixed columns, no width cap (no code fence)."""
    return [
        strings.REGION_TEXT_LINE.format(
            region=_truncate(r.region, 10),
            share=f"{r.share:.1%}",
            pop=f"{r.our_pop:,}",
        ),
        strings.REGION_TEXT_DELTA_LINE.format(
            share_delta=_render_share_delta(r.share_delta),
            vp_delta=_render_table_delta(r.vp_delta),
            to50=_region_to50(r),
        ),
    ]


def _regions_embed(regions: list[RegionStat], footer: str, limit: int | None = None) -> discord.Embed:
    """Embed 2: region list — active regions first, inactive after the
    heading. Capped (``limit``, the daily card): each shown ACTIVE region
    is one inline field (the share percentage in the name is the control
    signal) and the rest collapse behind a ``More regions`` field, with
    ``Legend`` and (when deltas exist) ``Biggest moves`` closing the card.
    Uncapped (``/regiony``): the same blocks as proportional description
    lines under the intro, fitted to the 4096-char budget with the
    ``…and N more`` guard."""
    active = sorted((r for r in regions if region_active(r)), key=lambda r: (-r.share, r.region))
    inactive = sorted((r for r in regions if not region_active(r)), key=lambda r: (-r.share, r.region))
    if limit is not None:
        embed = discord.Embed(
            title=strings.EMBED_TITLE_REGIONS,
            color=_COLOR_REGIONS,
            description="",
        )
        _ = embed.set_footer(text=footer)
        shown = min(limit, len(active))
        for r in active[:shown]:
            _ = embed.add_field(
                name=strings.REGION_FIELD_NAME.format(
                    region=_truncate(r.region, 10),
                    share=f"{r.share:.1%}",
                ),
                value=strings.REGION_FIELD_VALUE.format(
                    pop=r.our_pop,
                    share_delta=_render_share_delta(r.share_delta),
                    vp_delta=_render_table_delta(r.vp_delta),
                    to50=_region_to50(r),
                ),
                inline=True,
            )
        hidden = len(regions) - shown
        if hidden > 0:
            _ = embed.add_field(
                name=strings.REGION_MORE_FIELDS,
                value=strings.REGION_MORE_FIELDS_VALUE.format(n=hidden),
                inline=False,
            )
        _ = embed.add_field(
            name=strings.REGION_LEGEND_FIELD,
            value=strings.REGION_LEGEND_FIELD_VALUE,
            inline=False,
        )
        movers = _region_movers_field(regions)
        if movers is not None:
            _ = embed.add_field(name=movers[0], value=movers[1], inline=False)
        return embed

    lines: list[str] = []
    for r in active:
        lines.extend(_region_text_lines(r))
    if inactive:
        lines.append(strings.REGION_INACTIVE_HEADING)
        for r in inactive:
            lines.extend(_region_text_lines(r))
    intro = strings.REGION_DESCRIPTION_INTRO
    table = "\n".join(_fit_lines(lines, budget=_DESCRIPTION_MAX - len(intro) - 1))
    embed = discord.Embed(
        title=strings.EMBED_TITLE_REGIONS,
        color=_COLOR_REGIONS,
        description=f"{intro}\n{table}" if table else intro,
    )
    _ = embed.set_footer(text=footer)
    return embed


def _standings_embed(
    standings: list[AllianceStat],
    our_tags: list[str],
    footer: str,
    limit: int | None = None,
) -> discord.Embed:
    """Embed 3: alliance list — OUR tags first (★ marker), the rest in
    config order. Capped (``limit``, the daily card): only the top *limit*
    by CURRENT population (tag ASC tie-break) render as inline fields; the
    ★ / ours-first ordering is applied AFTER the selection, the truncated
    tail collapses behind a ``More alliances`` field and ``Legend``
    (``★ our alliances``) always closes the card. Uncapped: the same rows
    as proportional description lines under the intro."""
    ours = set(our_tags)
    if limit is not None:
        ranked = sorted(standings, key=lambda s: (-s.population, s.tag))[:limit]
        hidden = len(standings) - len(ranked)
        ordered = [s for s in ranked if s.tag in ours] + [s for s in ranked if s.tag not in ours]
        embed = discord.Embed(
            title=strings.EMBED_TITLE_STANDINGS,
            color=_COLOR_STANDINGS,
            description="",
        )
        _ = embed.set_footer(text=footer)
        for s in ordered:
            marker = strings.STANDINGS_MARKER if s.tag in ours else ""
            _ = embed.add_field(
                name=strings.STANDINGS_FIELD_NAME.format(
                    marker=marker,
                    tag=_truncate(s.tag, 7 - len(marker)),
                ),
                value=strings.STANDINGS_FIELD_VALUE.format(
                    pop=s.population,
                    pop_delta=_render_table_delta(s.population_delta),
                    vp=s.vp,
                    vp_delta=_render_table_delta(s.vp_delta),
                ),
                inline=True,
            )
        if hidden > 0:
            _ = embed.add_field(
                name=strings.STANDINGS_MORE_FIELDS,
                value=strings.STANDINGS_MORE_FIELDS_VALUE.format(n=hidden),
                inline=False,
            )
        _ = embed.add_field(
            name=strings.STANDINGS_LEGEND_FIELD,
            value=strings.STANDINGS_LEGEND_FIELD_VALUE,
            inline=False,
        )
        return embed

    ordered = [s for s in standings if s.tag in ours] + [s for s in standings if s.tag not in ours]
    lines = [
        strings.STANDINGS_TEXT_LINE.format(
            tag=_truncate(s.tag, 7),
            pop=s.population,
            pop_delta=_render_table_delta(s.population_delta),
            vp=s.vp,
            vp_delta=_render_table_delta(s.vp_delta),
        )
        for s in ordered
    ]
    intro = strings.STANDINGS_DESCRIPTION_INTRO
    table = "\n".join(_fit_lines(lines, budget=_DESCRIPTION_MAX - len(intro) - 1))
    embed = discord.Embed(
        title=strings.EMBED_TITLE_STANDINGS,
        color=_COLOR_STANDINGS,
        description=f"{intro}\n{table}" if table else intro,
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
            if ev.same_player:
                # The owner switched alliances (stable player_id) — the
                # village was NOT taken, so "conquered" would be a lie.
                if ev.region is None:
                    return strings.LOST_ALLIANCE_CHANGED_LINE.format(name=name, x=ev.x, y=ev.y, owner=owner)
                return strings.LOST_ALLIANCE_CHANGED_REGION_LINE.format(
                    name=name, x=ev.x, y=ev.y, region=ev.region, owner=owner
                )
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
