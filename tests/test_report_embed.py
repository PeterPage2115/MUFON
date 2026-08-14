"""Unit tests for travian.bot.report_embed — the daily report embed builder.

Decisions locked by these tests (all wording lives in ``travian.strings``):

- Pinned structure: ONE message with up to 4 embeds — Daily Report (only
  when "summary" is in ``sections``), Regions (only when "regions" in
  ``sections`` and regions exist), Standings (only when "standings" in
  ``sections`` and standings exist, our tags first ★), New & Lost Villages
  (only when "villages" in ``sections`` and events exist). The daily subset
  is ``DAILY_SECTIONS`` (summary + regions + standings) with
  ``region_limit=10`` and ``standings_limit=10``; the on-demand commands
  request a single section. Only the first embed carries the context
  description (its own card heading is the title — no ``# Summary`` inside);
  every embed carries the footer.
- CAPPED daily cards (limits set) are FIELD-based: one inline field per
  region/alliance (name ≤ 256, value ≤ 1024, ≤ 25 fields per embed), a
  ``More regions``/``More alliances`` field collapses the tail, ``Legend``
  explains the glyphs (★ = our alliances) and the Regions card adds a
  ``Biggest moves`` field when deltas exist. No code fences anywhere.
- UNCAPPED paths (``/regiony``, the pure builder's full list) render the
  same blocks as proportional description lines under a short intro:
  Regions adds the ``Inactive regions`` heading, ``_fit_lines`` truncates
  to the 4096-char budget with a ``…and N more`` line.
- KPI grid: inline fields (Villages, Population, Players, VP, Regions,
  New / Lost), values grouped, parens dropped when the delta is None.
- Region activity rule (game rule): a region is ACTIVE with total population
  ≥ 4,000; control = active AND strictly > 50% of the total (exactly 50% is
  NOT controlled — "+1" cell). Inactive regions follow the ``Inactive
  regions`` heading with "—" in To 50%. The Δ % value (our control-share
  change vs yesterday, "—" on baseline days, "±0.0%" below 0.05 pp) sits in
  the second field/line. With ``region_limit`` the card keeps only the top
  *limit* ACTIVE regions and collapses the rest (remaining active + all
  inactive) behind ``More regions``; ``region_names`` restricts the list
  AND the KPI to the selected exact names (unknown names dropped); the
  movers field names the best/worst Δ % moves when deltas exist.
- Standings: with ``standings_limit`` only the top *limit* by current
  population (tag ASC tie-break) render, ★/ours-first applies AFTER the
  selection and the tail collapses behind ``More alliances``; markers stay
  within 7 visible chars including the "★ " marker.
- Village event lines carry the region; new lines show the founder
  ("by <player>"), lost lines the conqueror ("conquered by <tag>" /
  "deleted") — EXCEPT same-player transitions, which render "alliance
  changed to <tag>" and never say "conquered" (the owner moved; the
  village was not taken). The metrics layer pre-sorts gained by region and
  lost by conqueror with deleted last.
- Baseline day (no previous snapshot): KPI parens dropped, all Δ cells
  "—", " (baseline)" in the description.
- Caps: 15 village events per section (more-line when exceeded); names
  truncated (region 10, tag 7 incl. marker, village 24).
- Delta rendering: None → "—", 0 → "±0", >0 → "+N", <0 → "−N" (U+2212).
"""

from typing import Any, Literal

import discord

from travian import strings
from travian.bot.report_embed import (
    DAILY_SECTIONS,
    REPORT_SECTIONS,
    _fit_lines,
    build_report_embed,
)
from travian.models import (
    AllianceStat,
    DeltaSummary,
    RegionStat,
    ReportData,
    VillageEvent,
)


def make_event(
    village_id: int,
    name: str = "Village 1",
    x: int = 1,
    y: int = -1,
    event: Literal["gained", "lost_conquered", "lost_deleted"] = "gained",
    tag: str | None = None,
    player: str | None = None,
    region: str | None = None,
    same_player: bool = False,
) -> VillageEvent:
    """Fixture: one village event (defaults to a plain gained event)."""
    return VillageEvent(
        village_id=village_id,
        village_name=name,
        x=x,
        y=y,
        event=event,
        new_owner_tag=tag,
        new_owner_player=player,
        old_player=None,
        region=region,
        same_player=same_player,
    )


def make_region(
    region: str,
    our_villages: int,
    our_pop: int,
    total_pop: int,
    share: float,
    delta: int | None,
    our_vp: int = 0,
    vp_delta: int | None = None,
    share_delta: float | None = None,
) -> RegionStat:
    return RegionStat(
        region=region,
        our_villages=our_villages,
        our_pop=our_pop,
        region_total_pop=total_pop,
        share=share,
        delta=delta,
        our_vp=our_vp,
        vp_delta=vp_delta,
        share_delta=share_delta,
    )


def make_summary(
    villages: int = 42,
    population: int = 5000,
    players: int = 11,
    vp: int = 340,
    villages_delta: int | None = 1,
    population_delta: int | None = 120,
    players_delta: int | None = 0,
    vp_delta: int | None = 10,
) -> DeltaSummary:
    return DeltaSummary(
        villages=villages,
        population=population,
        players=players,
        vp=vp,
        villages_delta=villages_delta,
        population_delta=population_delta,
        players_delta=players_delta,
        vp_delta=vp_delta,
    )


def make_standings(
    tag: str,
    villages: int = 10,
    population: int = 1000,
    players: int = 5,
    vp: int = 900,
    villages_delta: int | None = 1,
    population_delta: int | None = 50,
    players_delta: int | None = 0,
    vp_delta: int | None = 10,
) -> AllianceStat:
    return AllianceStat(
        tag=tag,
        villages=villages,
        population=population,
        players=players,
        vp=vp,
        villages_delta=villages_delta,
        population_delta=population_delta,
        players_delta=players_delta,
        vp_delta=vp_delta,
    )


def make_report(**overrides: Any) -> ReportData:
    """Fixture: a realistic report with no events (everything else defaults)."""
    defaults: dict[str, Any] = {
        "snapshot_date": "2026-08-08",
        "server": "cw.x2.international",
        "alliance_tags": ["WOLF"],
        "summary": make_summary(),
        "new_villages": [],
        "lost_villages": [],
        "regions": [],
    }
    defaults.update(overrides)
    return ReportData(**defaults)


def field_value(field: Any) -> str:
    """Test helper: the field's text (discord.py's stubs say Optional)."""
    value = field.value
    assert isinstance(value, str)
    return value


def desc(embed: discord.Embed) -> str:
    """Test helper: the embed's description (discord.py's stubs say Optional)."""
    description = embed.description
    assert description is not None
    return description


def embed_total(embed: discord.Embed) -> int:
    """Total chars of an embed: description + footer + fields (Discord limit)."""
    return len(desc(embed)) + len(embed.footer.text or "") + sum(
        len(field_name(f)) + len(field_value(f)) for f in embed.fields
    )


def field_name(field: Any) -> str:
    """Test helper: the field's name (discord.py's stubs say Optional)."""
    name = field.name
    assert isinstance(name, str)
    return name


def default_report() -> ReportData:
    """Fully populated report: one of everything → all 4 embeds present."""
    return make_report(
        standings=[make_standings("WOLF")],
        new_villages=[make_event(1, "King's Landing", 45, -23)],
        lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered", tag="ENEMY")],
        regions=[make_region("Dacia", 12, 340, 800, 0.425, 5)],
    )


def worst_case_report() -> ReportData:
    """Worst-case fixture: 15+15 capped events, 30 regions with long names."""
    new = [make_event(i, "A" * 90, 12, -34) for i in range(15)]
    lost = [make_event(i, "B" * 90, 12, -34, event="lost_conquered", tag="ENEMY") for i in range(15)]
    regions = [make_region(f"Region{i:02d}" + "R" * 40, 12, 340, 800, 0.425, 5) for i in range(30)]
    standings = [make_standings(f"T{i:02d}", population=100 + i, vp=1) for i in range(30)]
    return make_report(
        new_villages=new,
        lost_villages=lost,
        regions=regions,
        standings=standings,
    )


def fields(embed: discord.Embed) -> list[tuple[str, str]]:
    """The embed's fields as (name, value) pairs, in order."""
    return [(field_name(f), field_value(f)) for f in embed.fields]


def desc_lines(description: str) -> list[str]:
    """The description's lines."""
    return description.split("\n")


class TestStructure:
    def test_four_embeds_titles_in_order(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert [e.title for e in embeds] == [
            strings.EMBED_TITLE_REPORT,
            strings.EMBED_TITLE_REGIONS,
            strings.EMBED_TITLE_STANDINGS,
            strings.EMBED_TITLE_VILLAGES,
        ]

    def test_description_context_on_first_embed_only(self):
        embeds = build_report_embed(default_report(), ["WOLF", "FALCON"], "2026-08-08")

        assert embeds[0].description == "Report for cw.x2.international — snapshot 2026-08-08 — WOLF, FALCON"
        assert all("Report for" not in (e.description or "") for e in embeds[1:])

    def test_description_without_date(self):
        embeds = build_report_embed(default_report(), ["WOLF"], None)

        assert embeds[0].description == "Report for cw.x2.international — WOLF"

    def test_description_without_tags(self):
        embeds = build_report_embed(default_report(), [], "2026-08-08")

        assert embeds[0].description == "Report for cw.x2.international — snapshot 2026-08-08"

    def test_footer_on_every_embed(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert all(e.footer.text == "map.sql snapshot 2026-08-08 (midnight server time)" for e in embeds)

    def test_footer_without_date(self):
        embeds = build_report_embed(default_report(), ["WOLF"], None)

        assert all(e.footer.text == strings.FOOTER_NO_DATE for e in embeds)

    def test_default_color_and_palette(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert embeds[0].colour == discord.Colour(0x2ECC71)
        assert embeds[1].colour == discord.Colour(0x1ABC9C)
        assert embeds[2].colour == discord.Colour(0xE67E22)
        assert embeds[3].colour == discord.Colour(0x3498DB)

    def test_custom_color_applies_to_first_embed_only(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08", color=0x5865F2)

        assert embeds[0].colour == discord.Colour(0x5865F2)
        assert embeds[1].colour == discord.Colour(0x1ABC9C)

    def test_every_embed_within_limits(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        for e in embeds:
            assert len(desc(e)) <= 4096
            assert embed_total(e) <= 6000

    def test_summary_embed_only_when_everything_empty(self):
        embeds = build_report_embed(make_report(), ["WOLF"], "2026-08-08")

        assert len(embeds) == 1
        assert embeds[0].title == strings.EMBED_TITLE_REPORT


class TestSummaryKpi:
    def test_kpi_field_names_and_order(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert [f.name for f in embeds[0].fields] == [
            strings.KPI_VILLAGES,
            strings.KPI_POPULATION,
            strings.KPI_PLAYERS,
            strings.KPI_VP,
            strings.KPI_REGIONS,
            strings.KPI_NEW_LOST,
        ]

    def test_kpi_values_grouped_with_deltas(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert [field_value(f) for f in embeds[0].fields[:4]] == [
            "42 (+1)",
            "5,000 (+120)",
            "11 (±0)",
            "340 (+10)",
        ]

    def test_kpi_parens_dropped_when_delta_none(self):
        data = make_report(
            summary=make_summary(
                villages_delta=None, population_delta=None, players_delta=None, vp_delta=None
            ),
            vp_delta=None,
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert [field_value(f) for f in embeds[0].fields[:4]] == ["42", "5,000", "11", "340"]

    def test_kpi_negative_delta_uses_unicode_minus(self):
        data = make_report(summary=make_summary(players_delta=-3))
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert [field_value(f) for f in embeds[0].fields[:4]][2] == "11 (−3)"

    def test_kpi_regions_value(self):
        regions = [
            make_region("A", 1, 12000, 20000, 0.6, 1),  # active, controlled
            make_region("B", 1, 1000, 5000, 0.2, 1),  # active, short
            make_region("C", 1, 300, 500, 0.6, 1),  # inactive (total < 4,000)
        ]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")

        kpi = next(f for f in embeds[0].fields if f.name == strings.KPI_REGIONS)
        assert field_value(kpi) == "1 of 2 active regions controlled"

    def test_kpi_regions_omitted_when_no_regions(self):
        embeds = build_report_embed(make_report(), ["WOLF"], "2026-08-08")

        assert [f.name for f in embeds[0].fields] == [
            strings.KPI_VILLAGES,
            strings.KPI_POPULATION,
            strings.KPI_PLAYERS,
            strings.KPI_VP,
            strings.KPI_NEW_LOST,
        ]

    def test_kpi_new_lost_counts(self):
        events = [make_event(i) for i in range(3)]
        lost = [make_event(i, event="lost_deleted") for i in range(2)]
        embeds = build_report_embed(make_report(new_villages=events, lost_villages=lost), ["WOLF"], "2026-08-08")

        kpi = next(f for f in embeds[0].fields if f.name == strings.KPI_NEW_LOST)
        assert field_value(kpi) == "3 new · 2 lost"


class TestRegionTable:
    """The uncapped contract: each region is two proportional lines (region ·
    share · pop / Δ · VP · to50), region names truncate to 10 chars, active
    regions precede inactive after the ``Inactive regions`` heading — no
    code fence, no fixed columns, no width cap."""

    def test_two_lines_per_region_with_all_values(self):
        regions = [
            make_region("Eburacum", 79, 39221, 71800, 0.546, 1814, our_vp=5000, vp_delta=1814, share_delta=0.021),
            make_region("Borders", 5, 2500, 18000, 0.25, 100, our_vp=600, vp_delta=-25, share_delta=-0.005),
            make_region("Segestica", 1, 126, 2333, 0.054, 0, our_vp=10, vp_delta=0, share_delta=0.0),
        ]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")

        assert desc_lines(desc(embeds[1])) == [
            strings.REGION_DESCRIPTION_INTRO,
            "Eburacum · 54.6% · 39,221",
            "Δ +2.1% · VP +1,814 · ✓",
            "Borders · 25.0% · 2,500",
            "Δ −0.5% · VP −25 · +6,501",
            strings.REGION_INACTIVE_HEADING,
            "Segestica · 5.4% · 126",
            "Δ ±0.0% · VP ±0 · —",
        ]

    def test_no_code_fence_anywhere(self):
        regions = [make_region("Eburacum", 79, 39221, 71800, 0.546, 1814, share_delta=0.021)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")
        capped = build_report_embed(
            make_report(regions=regions), ["WOLF"], "2026-08-08", region_limit=10
        )

        assert "```" not in desc(embeds[1])
        assert "```" not in desc(capped[1])

    def test_long_region_name_truncated_to_10(self):
        regions = [make_region("DurnonovariaExtra", 1, 1000, 5000, 0.2, 1)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")

        line = next(l for l in desc_lines(desc(embeds[1])) if l.startswith("Durnonova"))
        assert line.startswith("Durnonova… ")
        assert len(line) <= 30

    def test_strict_more_than_half_is_not_controlled(self):
        # Exactly 50% of an even total: needs +1 to exceed half — regression
        # for the old ceil() formula that rendered "+0"/✓ at exactly 50%.
        regions = [make_region("Half", 1, 4000, 8000, 0.5, 1)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")

        lines = desc_lines(desc(embeds[1]))
        assert lines[1] == "Half · 50.0% · 4,000"
        assert lines[2] == "Δ — · VP — · +1"

    def test_zero_pop_region_is_inactive(self):
        regions = [make_region("Empty", 1, 0, 0, 0.0, 1)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")

        lines = desc_lines(desc(embeds[1]))
        # No active regions → the heading opens the inactive block.
        assert lines == [
            strings.REGION_DESCRIPTION_INTRO,
            strings.REGION_INACTIVE_HEADING,
            "Empty · 0.0% · 0",
            "Δ — · VP — · —",
        ]

    def test_vp_delta_dash_on_baseline(self):
        regions = [make_region("A", 1, 1000, 5000, 0.2, None, vp_delta=None)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")

        lines = desc_lines(desc(embeds[1]))
        assert lines[2] == "Δ — · VP — · +1,501"

    def test_more_line_on_pathological_overflow(self):
        regions = [make_region(f"Region {i:02d}", 1, 1000, 5000, 0.2, 1) for i in range(90)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")

        description = desc(embeds[1])
        assert len(description) <= 4096
        lines = desc_lines(description)
        # The description is truncated to the 4096-char budget — never all
        # 180 lines (a more-line only fits when the budget leaves room).
        assert len(lines) < 180
        assert "Region 89" not in "\n".join(lines)


class TestSectionsAndRegionLimit:
    def test_daily_sections_exclude_villages(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08", sections=DAILY_SECTIONS)

        assert [e.title for e in embeds] == [
            strings.EMBED_TITLE_REPORT,
            strings.EMBED_TITLE_REGIONS,
            strings.EMBED_TITLE_STANDINGS,
        ]

    def test_villages_only_section(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08", sections={"villages"})

        assert [e.title for e in embeds] == [strings.EMBED_TITLE_VILLAGES]
        assert desc(embeds[0]).startswith("# New Villages")

    def test_regions_only_section(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08", sections={"regions"})

        assert [e.title for e in embeds] == [strings.EMBED_TITLE_REGIONS]

    def test_summary_only_section(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08", sections={"summary"})

        assert [e.title for e in embeds] == [strings.EMBED_TITLE_REPORT]

    def test_empty_sections_yields_no_embeds(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08", sections=frozenset())

        assert embeds == []

    def test_sections_must_be_subset(self):
        assert DAILY_SECTIONS <= REPORT_SECTIONS
        assert {"villages"} <= REPORT_SECTIONS

    def test_region_limit_condenses_with_more_field(self):
        regions = [make_region(f"Region {i:02d}", 1, 1000, 5000, 0.2, 1) for i in range(30)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08", region_limit=8)

        assert fields(embeds[1]) == [
            ("Region 00 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            ("Region 01 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            ("Region 02 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            ("Region 03 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            ("Region 04 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            ("Region 05 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            ("Region 06 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            ("Region 07 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            (strings.REGION_MORE_FIELDS, "22 not shown"),
            (strings.REGION_LEGEND_FIELD, strings.REGION_LEGEND_FIELD_VALUE),
        ]
        # The inline region fields come first, the card fields after.
        assert [f.inline for f in embeds[1].fields] == [True] * 8 + [False, False]

    def test_region_limit_zero_shows_only_more_field(self):
        regions = [make_region(f"Region {i:02d}", 1, 1000, 5000, 0.2, 1) for i in range(5)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08", region_limit=0)

        assert fields(embeds[1]) == [
            (strings.REGION_MORE_FIELDS, "5 not shown"),
            (strings.REGION_LEGEND_FIELD, strings.REGION_LEGEND_FIELD_VALUE),
        ]

    def test_region_limit_none_keeps_full_table(self):
        regions = [make_region(f"Region {i:02d}", 1, 1000, 5000, 0.2, 1) for i in range(30)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")

        lines = desc_lines(desc(embeds[1]))
        assert "Region 29" in "\n".join(lines)
        assert not any(line.startswith("…and ") for line in lines)

    def test_region_limit_counts_only_active_rows(self):
        # 3 active + 2 inactive; limit 2 → 2 active regions + more-field for 3.
        regions = [
            make_region(f"Active {i}", 1, 1000, 5000, 0.2, 1) for i in range(3)
        ] + [
            make_region(f"Inactive {i}", 1, 100, 500, 0.2, 1) for i in range(2)
        ]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08", region_limit=2)

        assert fields(embeds[1]) == [
            ("Active 0 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            ("Active 1 · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            (strings.REGION_MORE_FIELDS, "3 not shown"),
            (strings.REGION_LEGEND_FIELD, strings.REGION_LEGEND_FIELD_VALUE),
        ]
        assert "Inactive" not in " ".join(v for _, v in fields(embeds[1]))

    def test_region_limit_hides_inactive_block_behind_more_field(self):
        # With a limit set, inactive regions are ALWAYS behind the more-field
        # (the plan's assumption: region_limit counts only top ACTIVE rows).
        regions = [make_region("A", 1, 1000, 5000, 0.2, 1), make_region("B", 1, 900, 4000, 0.225, 1)]
        regions += [make_region("C", 1, 100, 500, 0.2, 1), make_region("D", 1, 100, 500, 0.2, 1)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08", region_limit=10)

        assert fields(embeds[1]) == [
            ("B · 22.5%", "900 pop\nΔ — · VP — · +1,101"),
            ("A · 20.0%", "1,000 pop\nΔ — · VP — · +1,501"),
            (strings.REGION_MORE_FIELDS, "2 not shown"),
            (strings.REGION_LEGEND_FIELD, strings.REGION_LEGEND_FIELD_VALUE),
        ]
        assert not any(k.startswith(("C", "D")) for k, _ in fields(embeds[1]))


class TestRegionMoversLine:
    """The capped card closes with a ``Biggest moves`` field naming the
    best/worst Δ % moves of the day (a single candidate uses its own
    one-move form)."""

    def test_best_and_worst_from_deltas(self):
        regions = [
            make_region("Corinium", 1, 1000, 5000, 0.2, 1, share_delta=0.033),
            make_region("Teutones", 1, 1000, 5000, 0.2, 1, share_delta=-0.053),
            make_region("Steady", 1, 1000, 5000, 0.2, 1, share_delta=0.0),
        ]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08", region_limit=10)

        assert fields(embeds[1])[-2:] == [
            (strings.REGION_LEGEND_FIELD, strings.REGION_LEGEND_FIELD_VALUE),
            (strings.REGION_MOVERS_FIELD, "+3.3% Corinium · −5.3% Teutones"),
        ]

    def test_single_candidate_renders_one_move(self):
        regions = [make_region("Only", 1, 1000, 5000, 0.2, 1, share_delta=0.021)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08", region_limit=10)

        assert fields(embeds[1])[-1] == (strings.REGION_MOVERS_SINGLE_FIELD, "+2.1% Only")

    def test_omitted_when_no_deltas(self):
        regions = [make_region("A", 1, 1000, 5000, 0.2, 1)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08", region_limit=10)

        names = [name for name, _ in fields(embeds[1])]
        assert strings.REGION_MOVERS_FIELD not in names
        assert strings.REGION_MOVERS_SINGLE_FIELD not in names
        assert fields(embeds[1])[-1] == (strings.REGION_LEGEND_FIELD, strings.REGION_LEGEND_FIELD_VALUE)

    def test_ties_break_by_region_name(self):
        # Equal deltas: best = lexicographically largest region, worst = smallest.
        regions = [
            make_region("Alpha", 1, 1000, 5000, 0.2, 1, share_delta=0.02),
            make_region("Beta", 1, 1000, 5000, 0.2, 1, share_delta=0.02),
            make_region("Gamma", 1, 1000, 5000, 0.2, 1, share_delta=-0.01),
            make_region("Delta", 1, 1000, 5000, 0.2, 1, share_delta=-0.01),
        ]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08", region_limit=10)

        assert fields(embeds[1])[-1] == (
            strings.REGION_MOVERS_FIELD,
            "+2.0% Beta · −1.0% Delta",
        )


class TestStandingsTable:
    def test_text_lines_ours_first(self):
        data = make_report(
            standings=[
                make_standings("AAA", population=3000, vp=800, population_delta=-50, vp_delta=0),
                make_standings("WOLF", population=5000, vp=900, population_delta=120, vp_delta=10),
                make_standings("BBB", population=1000, vp=700, population_delta=None, vp_delta=None),
            ]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        standings = next(e for e in embeds if e.title == strings.EMBED_TITLE_STANDINGS)
        assert desc(standings) == (
            f"{strings.STANDINGS_DESCRIPTION_INTRO}\n"
            "WOLF · Pop 5,000 · Δ +120 · VP 900 · Δ +10\n"
            "AAA · Pop 3,000 · Δ −50 · VP 800 · Δ ±0\n"
            "BBB · Pop 1,000 · Δ — · VP 700 · Δ —"
        )

    def test_our_tags_marked_with_star_only(self):
        data = make_report(
            standings=[make_standings("WOLF"), make_standings("WOLF2"), make_standings("AAA")]
        )
        embeds = build_report_embed(data, ["WOLF", "WOLF2"], "2026-08-08", standings_limit=10)

        assert [name for name, _ in fields(embeds[1])][:3] == ["★ WOLF", "★ WOLF2", "AAA"]
        assert "★ AAA" not in " ".join(fields(embeds[1])[0])

    def test_field_values_exact(self):
        data = make_report(
            standings=[
                make_standings("WOLF", population=5000, vp=900, population_delta=120, vp_delta=10),
                make_standings("AAA", population=3000, vp=800, population_delta=-50, vp_delta=0),
                make_standings("BBB", population=1000, vp=700, population_delta=None, vp_delta=None),
            ]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08", standings_limit=10)

        assert fields(embeds[1])[:3] == [
            ("★ WOLF", "Pop 5,000 · Δ +120\nVP 900 · Δ +10"),
            ("AAA", "Pop 3,000 · Δ −50\nVP 800 · Δ ±0"),
            ("BBB", "Pop 1,000 · Δ —\nVP 700 · Δ —"),
        ]
        assert fields(embeds[1])[-1] == (strings.STANDINGS_LEGEND_FIELD, strings.STANDINGS_LEGEND_FIELD_VALUE)

    def test_omitted_when_empty(self):
        embeds = build_report_embed(make_report(), ["WOLF"], "2026-08-08")

        assert strings.EMBED_TITLE_STANDINGS not in [e.title for e in embeds]

    def test_long_tag_truncated(self):
        data = make_report(
            standings=[
                make_standings("VERYLONGTAG", population=1000, vp=1),
                make_standings("OURSVERYLONG", population=2000, vp=2),
            ]
        )
        embeds = build_report_embed(data, ["OURSVERYLONG"], "2026-08-08", standings_limit=10)

        # Marker + tag stay within 7 visible chars.
        assert fields(embeds[1])[0][0] == "★ OURS…"
        assert fields(embeds[1])[1][0] == "VERYLO…"

    def test_more_line_on_overflow(self):
        # 130 wide rows ≈ 6,500 chars — over the 4,096 description budget;
        # the tail collapses behind the more-line.
        standings = [
            make_standings(f"T{i:02d}", population=9999999 + i, vp=9999999) for i in range(130)
        ]
        embeds = build_report_embed(make_report(standings=standings), ["WOLF"], "2026-08-08")

        description = desc(embeds[1])
        assert len(description) <= 4096
        assert desc_lines(description)[-1].startswith("…and ")
        assert "T129" not in description

    def test_baseline_dashes(self):
        data = make_report(
            standings=[make_standings("WOLF", population_delta=None, vp_delta=None)]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        lines = desc_lines(desc(embeds[1]))
        assert lines[1] == "WOLF · Pop 1,000 · Δ — · VP 900 · Δ —"


class TestVillageEvents:
    def test_new_village_section(self):
        data = make_report(
            new_villages=[make_event(1, "King's Landing", 45, -23, region="Eboracum", player="Tyrion Lannister")]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert desc(villages) == "# New Villages\n\n**King's Landing** (45|-23) — Eboracum — by Tyrion Lannister"

    def test_new_village_no_region_founder_fallbacks(self):
        # Region-absent snapshots: founder-only line; unknown founder → "unknown".
        data = make_report(
            new_villages=[
                make_event(1, "Village 1"),
                make_event(2, "Village 2", player="Tyrion Lannister"),
            ]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert desc(villages) == (
            "# New Villages\n\n"
            "**Village 1** (1|-1) — by unknown\n"
            "**Village 2** (1|-1) — by Tyrion Lannister"
        )

    def test_lost_conquered_with_bold_owner(self):
        data = make_report(
            lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered", tag="ENEMY", region="North")]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert desc(villages) == (
            "# Lost Villages\n\n**Winterfell** (2|-5) — North — conquered by **ENEMY**"
        )

    def test_lost_conquered_player_fallback(self):
        data = make_report(
            lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered", player="Some Player")]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert "conquered by **Some Player**" in desc(villages)

    def test_lost_conquered_unknown_owner_fallback(self):
        data = make_report(lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered")])
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert "conquered by **unknown**" in desc(villages)

    def test_lost_deleted_line(self):
        data = make_report(lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_deleted")])
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert desc(villages) == "# Lost Villages\n\n**Winterfell** (2|-5) — deleted"

    def test_both_sections_in_one_description(self):
        data = make_report(
            new_villages=[make_event(1, "King's Landing", 45, -23, region="Eboracum", player="Tyrion Lannister")],
            lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered", tag="ENEMY", region="North")],
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert desc(villages) == (
            "# New Villages\n\n**King's Landing** (45|-23) — Eboracum — by Tyrion Lannister\n\n"
            "# Lost Villages\n\n**Winterfell** (2|-5) — North — conquered by **ENEMY**"
        )

    def test_cap_15_with_more_line(self):
        events = [make_event(i, f"Village {i}") for i in range(17)]
        embeds = build_report_embed(make_report(new_villages=events), ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        lines = desc(villages).split("\n")
        assert lines[0] == "# New Villages"
        assert lines[2] == "**Village 0** (1|-1) — by unknown"
        assert lines[-1] == strings.MORE_LINE.format(n=2)

    def test_lost_cap_15_with_more_line(self):
        events = [make_event(i, f"Village {i}", event="lost_deleted") for i in range(17)]
        embeds = build_report_embed(make_report(lost_villages=events), ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert desc(villages).split("\n")[-1] == strings.MORE_LINE.format(n=2)

    def test_name_truncated_at_24(self):
        events = [make_event(1, "A" * 30, region="Eboracum", player="Tyrion Lannister")]
        embeds = build_report_embed(make_report(new_villages=events), ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert desc(villages) == (
            f"# New Villages\n\n**{'A' * 23}…** (1|-1) — Eboracum — by Tyrion Lannister"
        )

    def test_embed_omitted_when_both_empty(self):
        embeds = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert strings.EMBED_TITLE_VILLAGES in [e.title for e in embeds]
        embeds = build_report_embed(make_report(), ["WOLF"], "2026-08-08")
        assert strings.EMBED_TITLE_VILLAGES not in [e.title for e in embeds]


class TestOmission:
    def test_minimal_report_one_embed(self):
        embeds = build_report_embed(make_report(), ["WOLF"], "2026-08-08")

        assert [e.title for e in embeds] == [strings.EMBED_TITLE_REPORT]

    def test_no_regions(self):
        data = default_report()
        data = make_report(
            standings=data.standings,
            new_villages=data.new_villages,
            lost_villages=data.lost_villages,
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert strings.EMBED_TITLE_REGIONS not in [e.title for e in embeds]
        assert len(embeds) == 3

    def test_no_standings(self):
        data = default_report()
        data = make_report(
            regions=data.regions,
            new_villages=data.new_villages,
            lost_villages=data.lost_villages,
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert strings.EMBED_TITLE_STANDINGS not in [e.title for e in embeds]
        assert len(embeds) == 3

    def test_no_events(self):
        data = default_report()
        data = make_report(standings=data.standings, regions=data.regions)
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert strings.EMBED_TITLE_VILLAGES not in [e.title for e in embeds]
        assert len(embeds) == 3

    def test_baseline_day(self):
        data = make_report(
            summary=make_summary(
                villages_delta=None, population_delta=None, players_delta=None, vp_delta=None
            ),
            regions=[make_region("A", 1, 1000, 5000, 0.2, None)],
            standings=[make_standings("WOLF", population_delta=None, vp_delta=None)],
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert [e.title for e in embeds] == [
            strings.EMBED_TITLE_REPORT,
            strings.EMBED_TITLE_REGIONS,
            strings.EMBED_TITLE_STANDINGS,
        ]


class TestBaseline:
    def test_baseline_annotation_and_dashes(self):
        data = make_report(
            summary=make_summary(
                villages_delta=None, population_delta=None, players_delta=None, vp_delta=None
            ),
            regions=[make_region("A", 1, 4000, 8000, 0.5, None)],
            standings=[make_standings("WOLF", population_delta=None, vp_delta=None)],
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert embeds[0].description == (
            "Report for cw.x2.international — snapshot 2026-08-08 — WOLF (baseline)"
        )
        assert [field_value(f) for f in embeds[0].fields[:4]] == ["42", "5,000", "11", "340"]
        # Regions lines: Δ % and VP Δ render "—"; exactly 50% is NOT
        # controlled → "+1".
        regions_lines = desc_lines(desc(embeds[1]))
        assert regions_lines[0] == strings.REGION_DESCRIPTION_INTRO
        assert regions_lines[1] == "A · 50.0% · 4,000"
        assert regions_lines[2] == "Δ — · VP — · +1"
        # Standings row: both Δ columns render "—".
        standings_lines = desc_lines(desc(embeds[2]))
        assert standings_lines[1] == "WOLF · Pop 1,000 · Δ — · VP 900 · Δ —"


class TestLimits:
    def test_worst_case_every_embed_within_limits(self):
        embeds = build_report_embed(worst_case_report(), ["WOLF"], "2026-08-08")

        assert len(embeds) == 4
        assert len(embeds[0].fields) <= 6
        for e in embeds:
            assert len(desc(e)) <= 4096
            assert embed_total(e) <= 6000

    def test_worst_case_capped_fields_within_limits(self):
        # The daily card: 10+10 capped rows as inline fields — every field
        # name <= 256, every value <= 1024, <= 25 fields, description and
        # total length within Discord limits.
        embeds = build_report_embed(
            worst_case_report(),
            ["WOLF"],
            "2026-08-08",
            sections=DAILY_SECTIONS,
            region_limit=10,
            standings_limit=10,
        )

        assert len(embeds) == 3
        for e in embeds:
            assert len(e.fields) <= 25
            assert len(desc(e)) <= 4096
            for f in e.fields:
                assert len(field_name(f)) <= 256
                assert len(field_value(f)) <= 1024
            assert embed_total(e) <= 6000
        # The capped cards carry their tail + legend fields (the worst-case
        # regions are all inactive: total 800 < 4,000 → zero shown).
        assert (strings.REGION_MORE_FIELDS, "30 not shown") in fields(embeds[1])
        assert (strings.STANDINGS_MORE_FIELDS, "20 not shown") in fields(embeds[2])
        assert (strings.STANDINGS_LEGEND_FIELD, strings.STANDINGS_LEGEND_FIELD_VALUE) in fields(embeds[2])

    def test_worst_case_regions_all_fit_no_more_line(self):
        # 30 regions × 2 proportional lines ≈ 1,740 — the 4096-char
        # description holds all of them (no field cap, no fence).
        embeds = build_report_embed(worst_case_report(), ["WOLF"], "2026-08-08")

        lines = desc_lines(desc(embeds[1]))
        assert "Region29" in "\n".join(lines)
        assert not any(line.startswith("…and ") for line in lines)


class TestRegionNamesFilter:
    """REPORT_REGIONS: the filter runs BEFORE the KPI Regions field, the
    movers line and the compact body — all three describe the same scope."""

    def test_filters_kpi_movers_and_body(self):
        regions = [
            make_region("Alpha", 1, 1000, 5000, 0.2, 1, share_delta=0.02),
            make_region("Beta", 1, 1000, 5000, 0.2, 1, share_delta=0.01),
            make_region("Gamma", 1, 1000, 5000, 0.2, 1, share_delta=0.03),
        ]
        embeds = build_report_embed(
            make_report(regions=regions),
            ["WOLF"],
            "2026-08-08",
            region_names=["Gamma", "Alpha"],
            region_limit=10,
        )

        names = [name for name, _ in fields(embeds[1])]
        assert "Beta" not in " ".join(names)
        assert names[:2] == ["Alpha · 20.0%", "Gamma · 20.0%"]  # share tie → name asc within the subset
        kpi = next(f for f in embeds[0].fields if f.name == strings.KPI_REGIONS)
        assert field_value(kpi) == "0 of 2 active regions controlled"
        # Movers restricted to the filtered scope.
        assert fields(embeds[1])[-1] == (
            strings.REGION_MOVERS_FIELD,
            "+3.0% Gamma · +2.0% Alpha",
        )

    def test_unknown_names_dropped(self):
        regions = [make_region("Alpha", 1, 1000, 5000, 0.2, 1)]
        embeds = build_report_embed(
            make_report(regions=regions), ["WOLF"], "2026-08-08", region_names=["Nope", "Alpha"]
        )

        lines = desc_lines(desc(embeds[1]))
        assert lines[1].startswith("Alpha")
        assert "Nope" not in desc(embeds[1])

    def test_empty_match_set_yields_no_regions_embed(self):
        # Zero matches: the caller falls back to the top-10, but the pure
        # builder itself must simply omit the Regions block and its KPI.
        regions = [make_region("Alpha", 1, 1000, 5000, 0.2, 1)]
        embeds = build_report_embed(
            make_report(regions=regions), ["WOLF"], "2026-08-08", region_names=["Nope"]
        )

        assert strings.EMBED_TITLE_REGIONS not in [e.title for e in embeds]
        assert not any(f.name == strings.KPI_REGIONS for f in embeds[0].fields)

    def test_none_keeps_full_scope(self):
        regions = [make_region("Alpha", 1, 1000, 5000, 0.2, 1), make_region("Beta", 1, 1000, 5000, 0.2, 1)]
        embeds = build_report_embed(make_report(regions=regions), ["WOLF"], "2026-08-08")

        lines = desc_lines(desc(embeds[1]))
        assert "Beta" in "\n".join(lines)
        kpi = next(f for f in embeds[0].fields if f.name == strings.KPI_REGIONS)
        assert field_value(kpi) == "0 of 2 active regions controlled"


class TestStandingsLimit:
    """Daily-report standings cap: top *limit* by CURRENT population (tag ASC
    tie-break), then the ★/ours-first ordering; the tail collapses behind a
    ``More alliances`` field and ``Legend`` always closes the card."""

    def test_top_10_by_current_population_with_more_field(self):
        standings = [make_standings(f"T{i:02d}", population=100 + i, vp=1) for i in range(30)]
        embeds = build_report_embed(make_report(standings=standings), ["WOLF"], "2026-08-08", standings_limit=10)

        assert fields(embeds[1]) == [
            (f"T{i:02d}", f"Pop {100 + i:,} · Δ +50\nVP 1 · Δ +10") for i in range(29, 19, -1)
        ] + [
            (strings.STANDINGS_MORE_FIELDS, "20 not shown"),
            (strings.STANDINGS_LEGEND_FIELD, strings.STANDINGS_LEGEND_FIELD_VALUE),
        ]
        # 10 inline + More + Legend = 12 fields, far below Discord's 25.
        assert len(embeds[1].fields) == 12
        assert [f.inline for f in embeds[1].fields] == [True] * 10 + [False, False]

    def test_ours_outside_top_10_not_injected(self):
        standings = [make_standings(f"T{i:02d}", population=1000 + i, vp=1) for i in range(10)]
        standings += [make_standings("WOLF", population=500, vp=1), make_standings("ENEMY", population=400, vp=1)]
        embeds = build_report_embed(make_report(standings=standings), ["WOLF"], "2026-08-08", standings_limit=10)

        names = [name for name, _ in fields(embeds[1])]
        assert "WOLF" not in names
        assert "ENEMY" not in names
        assert (strings.STANDINGS_MORE_FIELDS, "2 not shown") in fields(embeds[1])

    def test_ours_first_within_selection(self):
        standings = [
            make_standings("AAA", population=3000, vp=1),
            make_standings("WOLF", population=5000, vp=1),
            make_standings("BBB", population=1000, vp=1),
            make_standings("CCC", population=2000, vp=1),
        ]
        embeds = build_report_embed(make_report(standings=standings), ["WOLF"], "2026-08-08", standings_limit=3)

        assert [name for name, _ in fields(embeds[1])] == [
            "★ WOLF",  # ours first, marked
            "AAA",
            "CCC",
            strings.STANDINGS_MORE_FIELDS,
            strings.STANDINGS_LEGEND_FIELD,
        ]
        more = next(v for n, v in fields(embeds[1]) if n == strings.STANDINGS_MORE_FIELDS)
        assert more == "1 not shown"

    def test_tag_asc_tiebreak(self):
        standings = [
            make_standings("BBB", population=100, vp=1),
            make_standings("AAA", population=100, vp=1),
        ]
        embeds = build_report_embed(make_report(standings=standings), ["WOLF"], "2026-08-08", standings_limit=1)

        assert [name for name, _ in fields(embeds[1])] == [
            "AAA",  # equal pop → tag ASC
            strings.STANDINGS_MORE_FIELDS,
            strings.STANDINGS_LEGEND_FIELD,
        ]

    def test_no_limit_keeps_full_ours_first_order(self):
        standings = [make_standings("AAA", population=100, vp=1), make_standings("WOLF", population=200, vp=1)]
        embeds = build_report_embed(make_report(standings=standings), ["WOLF"], "2026-08-08")

        lines = desc_lines(desc(embeds[1]))
        assert lines[1].startswith("WOLF · Pop 200")
        assert lines[2].startswith("AAA · Pop 100")
        assert "…and " not in desc(embeds[1])


class TestSamePlayerEvents:
    """A same-player transition is an ALLIANCE CHANGE, never a conquest —
    the report must not say "conquered" for it."""

    def test_lost_line_alliance_changed_not_conquered(self):
        data = make_report(
            lost_villages=[
                make_event(2, "Winterfell", 2, -5, event="lost_conquered", tag="ENEMY", region="North", same_player=True)
            ]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert desc(villages) == (
            "# Lost Villages\n\n**Winterfell** (2|-5) — North — alliance changed to **ENEMY**"
        )
        assert "conquered" not in desc(villages)

    def test_lost_line_alliance_changed_no_region(self):
        data = make_report(
            lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered", tag="ENEMY", same_player=True)]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert "**Winterfell** (2|-5) — alliance changed to **ENEMY**" in desc(villages)

    def test_real_conquest_keeps_conquered_copy(self):
        data = make_report(
            lost_villages=[
                make_event(2, "Winterfell", 2, -5, event="lost_conquered", tag="ENEMY", region="North")
            ]
        )
        embeds = build_report_embed(data, ["WOLF"], "2026-08-08")

        villages = next(e for e in embeds if e.title == strings.EMBED_TITLE_VILLAGES)
        assert "conquered by **ENEMY**" in desc(villages)


class TestFitLines:
    def test_fits_all_when_under_budget(self):
        assert _fit_lines(["a", "b"], budget=5) == ["a", "b"]

    def test_drops_trailing_lines_over_budget(self):
        assert _fit_lines(["a", "b", "c"], budget=3) == ["a", "b"]

    def test_more_line_appended_when_dropped_and_fits(self):
        lines = ["x" * 100, "y" * 100]

        kept = _fit_lines(lines, budget=150)

        assert kept == ["x" * 100, strings.MORE_LINE.format(n=1)]
        assert len("\n".join(kept)) <= 150

    def test_more_line_omitted_when_tight(self):
        lines = ["x" * 100, "y" * 100]

        assert _fit_lines(lines, budget=110) == ["x" * 100]

    def test_oversized_single_line_clamped(self):
        assert _fit_lines(["x" * 5000], budget=2000) == []

    def test_empty_lines(self):
        assert _fit_lines([], budget=10) == []

    def test_zero_budget(self):
        assert _fit_lines(["a"], budget=0) == []

    def test_dropped_count_in_more_line(self):
        # 20 short lines fit (219 chars); the next line (100 chars) busts the
        # budget and the 10 dropped lines become a more-line that fits.
        lines = ["x" * 10] * 20 + ["x" * 100] * 10

        kept = _fit_lines(lines, budget=240)

        assert kept == ["x" * 10] * 20 + [strings.MORE_LINE.format(n=10)]
        assert len("\n".join(kept)) <= 240
