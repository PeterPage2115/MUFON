"""Unit tests for travian.bot.report_embed — the daily report embed builder.

Decisions locked by these tests (all wording lives in ``travian.strings``):

- Pinned structure (plan): Summary (last line "Regions controlled: N of M"
  when regions exist), Regions (fenced control table, cap 20 data rows, only
  when ``data.regions`` is non-empty), Standings (fenced table, only when
  ``data.standings`` is non-empty, OUR tags ★ + footnote), New Villages (cap
  15), Lost Villages (cap 15), Top Players × 3 SEPARATE fields (Population /
  Growth / New Villages, cap 5), Victory Points — in that order, ≤ 25 fields
  total, every field value ≤ 1024 chars.
- ``_split_into_fields`` packs plain lines (≤ 1024 each); ``_fenced_chunks``
  packs code-fenced tables (cap ``_FIELD_MAX − 8`` per value + fences,
  more-line inside the last chunk via ``_append_more``). Fixed blocks split
  at 1024 chars; each split reduces the Regions cap by 1: ``regions_cap =
  25 − fixed_after_splits``. Region lines additionally stop at a char budget
  of ``6000 − fixed_len − description − footer − 512``.
- Delta rendering: None → "—", 0 → "±0", >0 → "+N", <0 → "−N" (U+2212).
- Baseline day (no previous snapshot): all deltas "—" + " (baseline)" in
  the description.
- 0 events → sensible empty states ("No new villages." etc.).

SIZE_OK (~460 pure LOC): declarative single-contract test file, same
precedent as test_models/test_metrics/test_backfill.
"""

import logging
from typing import Any, Literal

import discord
import pytest

from travian import strings
from travian.bot import report_embed
from travian.bot.report_embed import (
    _append_more,
    _fenced_chunks,
    _region_lines,
    _split_into_fields,
    build_report_embed,
)
from travian.models import (
    AllianceStat,
    DeltaSummary,
    PlayerStat,
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
    )


def make_player(
    player_id: int,
    name: str,
    population: int,
    villages: int,
    growth: int | None,
    gains: int | None = None,
) -> PlayerStat:
    return PlayerStat(
        player_id=player_id,
        player_name=name,
        population=population,
        villages=villages,
        growth=growth,
        gains=gains,
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
        "top_players": {"population": [], "growth": [], "new_villages": []},
        "regions": [],
        "vp_total": 340,
        "vp_delta": 10,
    }
    defaults.update(overrides)
    return ReportData(**defaults)


def field_value(field: Any) -> str:
    """Test helper: the field's text (discord.py's stubs say Optional)."""
    value = field.value
    assert isinstance(value, str)
    return value


def field_name(field: Any) -> str:
    """Test helper: the field's name (discord.py's stubs say Optional)."""
    name = field.name
    assert isinstance(name, str)
    return name


def default_report() -> ReportData:
    """Fully populated report: one of everything, moderate sizes."""
    return make_report(
        new_villages=[make_event(1, "King's Landing", 45, -23)],
        lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered", tag="ENEMY")],
        top_players={
            "population": [make_player(1, "Tyrion Lannister", 1200, 5, 150)],
            "growth": [make_player(1, "Tyrion Lannister", 1200, 5, 150)],
            "new_villages": [make_player(1, "Tyrion Lannister", 1200, 5, 150, gains=1)],
        },
        regions=[make_region("Dacia", 12, 340, 800, 0.425, 5)],
    )


def worst_case_report() -> ReportData:
    """Worst-case fixture: forces ≥2 fixed-block >1024 splits and region
    truncation by the char budget. Names are programmatically long so the
    test fails loudly if the fixture ever stops being worst-case."""
    new = [make_event(i, "A" * 90, 12, -34) for i in range(15)]
    lost = [make_event(i, "B" * 90, 12, -34, event="lost_conquered", tag="ENEMY") for i in range(15)]
    players = [make_player(i, "P" * 63, 1000 + i, 5, 10 + i, gains=1 + i) for i in range(5)]
    regions = [make_region(f"Region{i:02d}" + "R" * 40, 12, 340, 800, 0.425, 5) for i in range(30)]
    return make_report(
        new_villages=new,
        lost_villages=lost,
        top_players={"population": players, "growth": players, "new_villages": players},
        regions=regions,
    )


class TestStructure:
    def test_field_names_and_order(self):
        data = make_report(
            standings=[make_standings("WOLF")],
            regions=[make_region("Dacia", 12, 340, 800, 0.425, 5)],
        )
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert isinstance(embed, discord.Embed)
        assert [f.name for f in embed.fields] == [
            strings.FIELD_SUMMARY,
            strings.FIELD_REGIONS,
            strings.FIELD_STANDINGS,
            strings.FIELD_NEW_VILLAGES,
            strings.FIELD_LOST_VILLAGES,
            strings.FIELD_TOP_PLAYERS_POPULATION,
            strings.FIELD_TOP_PLAYERS_GROWTH,
            strings.FIELD_TOP_PLAYERS_NEW_VILLAGES,
            strings.FIELD_VICTORY_POINTS,
        ]

    def test_three_separate_top_player_fields(self):
        embed = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert [f.name for f in embed.fields[4:7]] == [
            strings.FIELD_TOP_PLAYERS_POPULATION,
            strings.FIELD_TOP_PLAYERS_GROWTH,
            strings.FIELD_TOP_PLAYERS_NEW_VILLAGES,
        ]

    def test_description_with_date_server_and_tags(self):
        embed = build_report_embed(default_report(), ["WOLF", "FALCON"], "2026-08-08")

        assert embed.description == "Report for cw.x2.international — snapshot 2026-08-08 — WOLF, FALCON"

    def test_description_without_date(self):
        embed = build_report_embed(default_report(), ["WOLF"], None)

        assert embed.description == "Report for cw.x2.international — WOLF"

    def test_description_without_tags(self):
        embed = build_report_embed(default_report(), [], "2026-08-08")

        assert embed.description == "Report for cw.x2.international — snapshot 2026-08-08"

    def test_footer_with_date(self):
        embed = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert embed.footer.text == "map.sql snapshot 2026-08-08 (midnight server time)"

    def test_footer_without_date(self):
        embed = build_report_embed(default_report(), ["WOLF"], None)

        assert embed.footer.text == "map.sql snapshot (midnight server time)"

    def test_default_color(self):
        embed = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert embed.colour == discord.Colour(0x2ECC71)

    def test_custom_color(self):
        embed = build_report_embed(default_report(), ["WOLF"], "2026-08-08", color=0x5865F2)

        assert embed.colour == discord.Colour(0x5865F2)


class TestContentFormatting:
    def test_summary_lines_with_deltas(self):
        embed = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        # default_report has a region (Dacia, share 0.425) → the summary ends
        # with the regions-controlled line (0 of 1).
        assert field_value(embed.fields[0]) == (
            "Villages: 42 (+1)\nPopulation: 5000 (+120)\nPlayers: 11 (±0)\nVP: 340 (+10)"
            "\nRegions controlled: 0 of 1"
        )

    def test_summary_negative_delta_uses_unicode_minus(self):
        data = make_report(summary=make_summary(players_delta=-3))
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert "Players: 11 (−3)" in field_value(embed.fields[0])
        assert "-3" not in field_value(embed.fields[0])  # ASCII minus is never used

    def test_new_village_line_bold_name_with_coords(self):
        embed = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        # default_report has regions → order is Summary, Regions, New Villages.
        assert field_value(embed.fields[2]) == "**King's Landing** (45|-23)"

    def test_lost_conquered_with_tag(self):
        data = make_report(lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered", tag="ENEMY")])
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        # No regions/standings → order: Summary, Regions (NO_REGIONS), New, Lost.
        assert field_value(embed.fields[3]) == "**Winterfell** (2|-5) — conquered by ENEMY"

    def test_lost_conquered_player_fallback(self):
        data = make_report(lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered", player="Some Player")])
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert field_value(embed.fields[3]) == "**Winterfell** (2|-5) — conquered by Some Player"

    def test_lost_conquered_unknown_owner_fallback(self):
        data = make_report(lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_conquered")])
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert field_value(embed.fields[3]) == "**Winterfell** (2|-5) — conquered by unknown"

    def test_lost_deleted_line(self):
        data = make_report(lost_villages=[make_event(2, "Winterfell", 2, -5, event="lost_deleted")])
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert field_value(embed.fields[3]) == "**Winterfell** (2|-5) — deleted"

    def test_top_players_population_line(self):
        data = make_report(top_players={"population": [make_player(1, "Tyrion Lannister", 1200, 5, 150)]})
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        field = next(f for f in embed.fields if f.name == strings.FIELD_TOP_PLAYERS_POPULATION)
        assert field_value(field) == "Tyrion Lannister — 1200 (5)"

    def test_top_players_growth_renders_delta(self):
        players = [
            make_player(1, "Grower", 1200, 5, 150),
            make_player(2, "Shrinker", 800, 4, -50),
            make_player(3, "Steady", 700, 3, 0),
        ]
        data = make_report(top_players={"growth": players})
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        field = next(f for f in embed.fields if f.name == strings.FIELD_TOP_PLAYERS_GROWTH)
        assert field_value(field).split("\n") == ["Grower — +150", "Shrinker — −50", "Steady — ±0"]

    def test_top_players_new_villages_line(self):
        data = make_report(top_players={"new_villages": [make_player(1, "Founder", 1200, 5, 150, gains=2)]})
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        field = next(f for f in embed.fields if f.name == strings.FIELD_TOP_PLAYERS_NEW_VILLAGES)
        assert field_value(field) == "Founder — +2 villages"

    def test_top_players_new_villages_gains_none_fallback(self):
        data = make_report(top_players={"new_villages": [make_player(1, "Founder", 1200, 5, 150)]})
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        field = next(f for f in embed.fields if f.name == strings.FIELD_TOP_PLAYERS_NEW_VILLAGES)
        assert field_value(field) == "Founder — +— villages"  # gains None → DELTA_NONE, never crashes

    def test_region_line_format(self):
        regions = [
            make_region("Eburacum", 79, 39221, 71800, 0.546, 1814, our_vp=5000, vp_delta=1814),
            make_region("Borders", 5, 2500, 18000, 0.25, 100, our_vp=600, vp_delta=-25),
        ]
        data = make_report(regions=regions)
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        field = next(f for f in embed.fields if f.name == strings.FIELD_REGIONS)
        assert field_value(field) == (
            f"```\n{strings.REGION_TABLE_HEADER}\n"
            "Eburacum     ▓▓▓░░░ 54.6% 39,221  +1,814       ✓\n"
            "Borders      ▓▓░░░░ 25.0%  2,500     −25  +6,500\n"
            "```"
        )

    def test_region_share_percent_rounding_and_delta_none(self):
        regions = [
            make_region("A", 1, 100, 300, 0.333333, None),  # 33.3%, 2/6 bar, to50 +50
            make_region("B", 1, 100, 100, 1.0, None),  # 100.0%, full bar, controlled
            make_region("C", 1, 0, 500, 0.0, None),  # 0.0%, empty bar, to50 +250
            make_region("D", 1, 0, 0, 0.0, None),  # total 0 → to50 "—"
        ]
        data = make_report(regions=regions)
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        field = next(f for f in embed.fields if f.name == strings.FIELD_REGIONS)
        lines = field_value(field).split("\n")
        assert lines[1] == strings.REGION_TABLE_HEADER
        assert lines[2] == "A            ▓▓░░░░ 33.3%    100       —     +50"
        assert lines[3] == "B            ▓▓▓▓▓▓ 100.0%    100       —       ✓"
        assert lines[4] == "C            ░░░░░░  0.0%      0       —    +250"
        assert lines[5] == "D            ░░░░░░  0.0%      0       —       —"

    def test_summary_regions_controlled_line(self):
        regions = [
            make_region("A", 1, 100, 200, 0.5, 1),
            make_region("B", 1, 60, 200, 0.3, 1),
        ]
        data = make_report(regions=regions)
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        summary = field_value(embed.fields[0])
        assert strings.SUMMARY_REGIONS_LINE.format(controlled=1, total=2) in summary

    def test_summary_regions_line_absent_when_no_regions(self):
        embed = build_report_embed(make_report(), ["WOLF"], "2026-08-08")

        assert "Regions controlled" not in field_value(embed.fields[0])

    def test_victory_points_line(self):
        embed = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert field_value(embed.fields[-1]) == "Total: 340 (+10)"


class TestStandings:
    """The Standings comparison field (TRACKED_ALLIANCES) — fenced table."""

    def test_field_position_after_summary_and_line_format(self):
        data = make_report(
            standings=[
                make_standings("WOLF", population=5000, vp=900, population_delta=120, vp_delta=10),
                make_standings("AAA", population=3000, vp=800, population_delta=-50, vp_delta=0),
                make_standings("BBB", population=1000, vp=700, population_delta=None, vp_delta=None),
            ]
        )

        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        # No regions in this report → the plain NO_REGIONS field occupies the
        # Regions slot, so Standings sits at index 2.
        names = [f.name for f in embed.fields]
        assert names[0] == strings.FIELD_SUMMARY
        assert names[1] == strings.FIELD_REGIONS
        assert names[2] == strings.FIELD_STANDINGS
        assert names[3] == strings.FIELD_NEW_VILLAGES
        assert field_value(embed.fields[2]) == (
            f"```\n{strings.STANDINGS_TABLE_HEADER}\n"
            "★WOLF     5,000    +120     900     +10\n"
            "AAA       3,000     −50     800      ±0\n"
            "BBB       1,000       —     700       —\n"
            f"{strings.STANDINGS_OURS_FOOTNOTE}\n"
            "```"
        )

    def test_our_tags_marked_with_star_only(self):
        data = make_report(standings=[make_standings("WOLF"), make_standings("WOLF2"), make_standings("AAA")])

        embed = build_report_embed(data, ["WOLF", "WOLF2"], "2026-08-08")

        value = field_value(next(f for f in embed.fields if f.name == strings.FIELD_STANDINGS))
        lines = value.split("\n")
        assert lines[0] == "```"
        assert lines[1] == strings.STANDINGS_TABLE_HEADER
        assert lines[2].startswith("★WOLF ")
        assert lines[3].startswith("★WOLF2")
        assert lines[4].startswith("AAA ")
        assert "★AAA" not in value

    def test_empty_standings_hides_field(self):
        embed = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        assert strings.FIELD_STANDINGS not in [f.name for f in embed.fields]

    def test_standings_field_respects_field_cap(self):
        # 30 tracked alliances: must not exceed the 25-field cap or 1024 chars
        # (fences included); capped lines survive as a more-line.
        standings = [make_standings(f"T{i}", population=100 + i, vp=1) for i in range(30)]
        data = make_report(standings=standings)

        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        standings_fields = [f for f in embed.fields if f.name == strings.FIELD_STANDINGS]
        assert len(embed.fields) <= 25
        assert all(len(field_value(f)) <= 1024 for f in standings_fields)
        assert standings_fields  # capped lines survive
        last = field_value(standings_fields[-1])
        assert last.startswith("```\n")
        assert last.endswith("\n```")


class TestEmptyStates:
    def test_zero_events_empty_states(self):
        embed = build_report_embed(make_report(), ["WOLF"], "2026-08-08")

        # Order: Summary, Regions (NO_REGIONS), New Villages, Lost Villages, ...
        assert field_value(embed.fields[2]) == strings.NO_NEW_VILLAGES
        assert field_value(embed.fields[3]) == strings.NO_LOST_VILLAGES
        for field in embed.fields[4:7]:
            assert field_value(field) == strings.NO_DATA_YET
        regions = next(f for f in embed.fields if f.name == strings.FIELD_REGIONS)
        assert field_value(regions) == strings.NO_REGIONS


class TestBaseline:
    def test_baseline_annotation_and_dashes(self):
        data = make_report(
            summary=make_summary(
                villages_delta=None,
                population_delta=None,
                players_delta=None,
                vp_delta=None,
            ),
            vp_delta=None,
            top_players={"growth": [make_player(1, "P1", 100, 1, None)]},
            regions=[make_region("A", 1, 100, 200, 0.5, None)],
            standings=[make_standings("WOLF", population_delta=None, vp_delta=None)],
        )
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        assert embed.description == "Report for cw.x2.international — snapshot 2026-08-08 — WOLF (baseline)"
        assert field_value(embed.fields[0]) == (
            "Villages: 42 (—)\nPopulation: 5000 (—)\nPlayers: 11 (—)\nVP: 340 (—)"
            "\nRegions controlled: 1 of 1"
        )
        growth = next(f for f in embed.fields if f.name == strings.FIELD_TOP_PLAYERS_GROWTH)
        assert field_value(growth) == "P1 — —"
        vp = next(f for f in embed.fields if f.name == strings.FIELD_VICTORY_POINTS)
        assert field_value(vp) == "Total: 340 (—)"
        # Regions row: the VP Δ column renders "—" (no previous snapshot).
        regions = next(f for f in embed.fields if f.name == strings.FIELD_REGIONS)
        assert field_value(regions) == (
            f"```\n{strings.REGION_TABLE_HEADER}\n"
            "A            ▓▓▓░░░ 50.0%    100       —       ✓\n"
            "```"
        )
        # Standings row: both Δ columns render "—".
        standings = next(f for f in embed.fields if f.name == strings.FIELD_STANDINGS)
        assert field_value(standings) == (
            f"```\n{strings.STANDINGS_TABLE_HEADER}\n"
            "★WOLF     1,000       —     900       —\n"
            f"{strings.STANDINGS_OURS_FOOTNOTE}\n"
            "```"
        )


class TestCaps:
    def test_new_villages_cap_15_with_more_line(self):
        events = [make_event(i, f"Village {i}") for i in range(17)]
        embed = build_report_embed(make_report(new_villages=events), ["WOLF"], "2026-08-08")

        lines = field_value(embed.fields[2]).split("\n")
        assert len(lines) == 16  # 15 items + more-line
        assert lines[0] == "**Village 0** (1|-1)"
        assert lines[-1] == strings.MORE_LINE.format(n=2)

    def test_lost_villages_cap_15_with_more_line(self):
        events = [make_event(i, f"Village {i}", event="lost_deleted") for i in range(17)]
        embed = build_report_embed(make_report(lost_villages=events), ["WOLF"], "2026-08-08")

        lines = field_value(embed.fields[3]).split("\n")
        assert len(lines) == 16
        assert lines[0] == "**Village 0** (1|-1) — deleted"
        assert lines[-1] == strings.MORE_LINE.format(n=2)

    def test_top_players_cap_5_with_more_line(self):
        players = [make_player(i, f"Player {i}", 1000 + i, 5, 10) for i in range(7)]
        data = make_report(top_players={"population": players})
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        field = next(f for f in embed.fields if f.name == strings.FIELD_TOP_PLAYERS_POPULATION)
        lines = field_value(field).split("\n")
        assert len(lines) == 6  # 5 players + more-line
        assert lines[-1] == strings.MORE_LINE.format(n=2)

    def test_no_more_line_at_exact_cap(self):
        events = [make_event(i, f"Village {i}") for i in range(15)]
        embed = build_report_embed(make_report(new_villages=events), ["WOLF"], "2026-08-08")

        assert len(field_value(embed.fields[2]).split("\n")) == 15
        assert "more" not in field_value(embed.fields[2])


class TestLimits:
    def test_len_counts_description_footer_and_fields(self):
        embed = build_report_embed(default_report(), ["WOLF"], "2026-08-08")

        total = len(embed.description or "") + len(embed.footer.text or "")
        total += sum(len(field_name(f)) + len(field_value(f)) for f in embed.fields)
        assert len(embed) == total

    def test_worst_case_fixed_splits_and_embed_limits(self):
        embed = build_report_embed(worst_case_report(), ["WOLF"], "2026-08-08")

        fixed = [f for f in embed.fields if f.name != strings.FIELD_REGIONS]
        # Precondition: the fixture MUST force two fixed-block >1024 splits
        # (7 blocks + New Villages split + Lost Villages split = 9 fields).
        assert len(fixed) == 9
        assert len(embed.fields) <= 25
        assert len(embed) <= 6000
        assert all(len(field_value(f)) <= 1024 for f in embed.fields)
        assert all(len(field_name(f)) <= 256 for f in embed.fields)

    def test_worst_case_regions_respect_reduced_cap_and_budget(self):
        data = worst_case_report()
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        regions_fields = [f for f in embed.fields if f.name == strings.FIELD_REGIONS]
        fixed_count = len(embed.fields) - len(regions_fields)
        assert len(regions_fields) <= 25 - fixed_count  # regions cap = 25 − fixed_after_splits
        assert regions_fields

        fixed_len = sum(
            len(field_name(f)) + len(field_value(f)) for f in embed.fields if f.name != strings.FIELD_REGIONS
        )
        budget = 6000 - fixed_len - len(embed.description or "") - len(embed.footer.text or "") - report_embed._CHAR_MARGIN

        region_lines = _region_lines(data.regions)
        capped_lines = [region_lines[0], *region_lines[1 : report_embed._ITEM_CAP_REGIONS + 1]]
        # Preconditions (self-computing — the test fails loudly if the
        # fixture ever stops being worst-case): the budget must truncate
        # inside the capped lines, and the more-line for the split drop must
        # fit after the last packed line.
        packed = 0
        used = 0
        for line in capped_lines:
            if used + len(line) + (1 if used else 0) > budget:
                break
            used += len(line) + (1 if used else 0)
            packed += 1
        assert packed < len(capped_lines)
        split_dropped = len(capped_lines) - packed
        assert used + 1 + len(strings.MORE_LINE.format(n=split_dropped)) <= budget

        lines = [line for field in regions_fields for line in field_value(field).split("\n")]
        assert len(lines) == 2 + packed + 1  # fences + packed lines + the more-line
        assert lines[1 + packed] == strings.MORE_LINE.format(n=split_dropped)

    def test_regions_cap_20_rows_with_more_line(self):
        regions = [make_region(f"Region {i}", 1, 100, 200, 0.5, 1) for i in range(22)]
        data = make_report(regions=regions)
        embed = build_report_embed(data, ["WOLF"], "2026-08-08")

        regions_fields = [f for f in embed.fields if f.name == strings.FIELD_REGIONS]
        # header + 20 rows = 1029 chars > _FIELD_MAX − 8, so the capped table
        # splits into two fenced fields; the cap's more-line rides the last.
        assert len(regions_fields) == 2
        values = [field_value(f) for f in regions_fields]
        assert all(v.startswith("```\n") for v in values)
        assert values[0].endswith("\n```")
        assert values[0].split("\n")[1] == strings.REGION_TABLE_HEADER
        rows = [
            line for v in values for line in v.split("\n")
            if line.startswith("Region ") and line != strings.REGION_TABLE_HEADER
        ]
        assert len(rows) == 20
        assert rows[0] == "Region 0     ▓▓▓░░░ 50.0%    100       —       ✓"
        assert rows[-1].startswith("Region 19")
        assert "Region 20" not in "\n".join(values)
        # The cap's more-line is appended after the last chunk's closing fence.
        assert values[-1].split("\n")[-1] == strings.MORE_LINE.format(n=2)
        assert all(len(field_value(f)) <= 1024 for f in embed.fields)
        assert len(embed.fields) <= 25
        assert len(embed) <= 6000


class TestRegionsTruncation:
    def test_regions_omitted_when_budget_exhausted_logs_warning(self, caplog: pytest.LogCaptureFixture, monkeypatch):
        monkeypatch.setattr(report_embed, "_CHAR_MARGIN", 5000)
        with caplog.at_level(logging.WARNING, logger="travian.bot.report_embed"):
            embed = build_report_embed(worst_case_report(), ["WOLF"], "2026-08-08")

        assert not [f for f in embed.fields if f.name == strings.FIELD_REGIONS]
        assert "regions truncated" in caplog.text


class TestSplitter:
    def test_packs_lines_within_max_len(self):
        lines = [f"L{i:02d}" + "x" * 97 for i in range(25)]  # 100 chars each

        values, dropped = _split_into_fields(lines)

        assert dropped == 0
        assert all(len(v) <= 1024 for v in values)
        assert sum(len(v.split("\n")) for v in values) == 25
        assert values[0].split("\n")[0] == lines[0]

    def test_max_fields_drops_remainder(self):
        lines = [f"L{i:02d}" + "x" * 97 for i in range(25)]

        values, dropped = _split_into_fields(lines, max_fields=2)

        assert len(values) == 2
        assert dropped == 5
        assert all(len(v) <= 1024 for v in values)

    def test_budget_drops_remainder(self):
        lines = [f"L{i}" for i in range(10)]

        values, dropped = _split_into_fields(lines, budget=8)

        assert values == ["L0\nL1\nL2"]
        assert dropped == 7

    def test_budget_counts_total_across_values(self):
        lines = [f"L{i:02d}" + "x" * 97 for i in range(25)]  # 100 chars each

        values, dropped = _split_into_fields(lines, max_fields=2, budget=1200)

        # Field 1 = 1009 chars; the second field ("L10") fits the budget but
        # the third would bust it — the partial field is flushed.
        assert values == ["\n".join(lines[:10]), lines[10]]
        assert dropped == 14

    def test_oversized_single_line_truncated(self):
        values, dropped = _split_into_fields(["x" * 2000])

        assert values == ["x" * 1024]
        assert dropped == 0

    def test_empty_lines(self):
        assert _split_into_fields([]) == ([], 0)

    def test_zero_max_fields(self):
        values, dropped = _split_into_fields(["a", "b"], max_fields=0)

        assert values == []
        assert dropped == 2

    def test_zero_budget(self):
        values, dropped = _split_into_fields(["a"], budget=0)

        assert values == []
        assert dropped == 1


class TestAppendMore:
    def test_appends_when_fits(self):
        values = ["a"]

        assert _append_more(values, 3) is True
        assert values == ["a\n" + strings.MORE_LINE.format(n=3)]

    def test_omits_when_budget_tight(self):
        values = ["a" * 100]

        assert _append_more(values, 3, budget=50) is False
        assert values == ["a" * 100]

    def test_noop_when_nothing_dropped(self):
        values = ["a"]

        assert _append_more(values, 0) is True
        assert values == ["a"]

    def test_noop_when_value_over_max_len(self):
        values = ["x" * 1024]

        assert _append_more(values, 3) is False
        assert values == ["x" * 1024]

    def test_empty_values_is_failure(self):
        assert _append_more([], 3) is False


class TestFencedChunks:
    """The code-fenced table splitter (Standings/Regions fields)."""

    def test_single_chunk_wrapped_in_fences(self):
        assert _fenced_chunks(["a", "b"]) == ["```\na\nb\n```"]

    def test_multi_chunk_split_at_field_max_minus_fences(self):
        line = "x" * (report_embed._FIELD_MAX - 8)

        values = _fenced_chunks([line, line, line])

        assert len(values) == 3
        assert all(v == f"```\n{line}\n```" for v in values)
        assert all(len(v) == report_embed._FIELD_MAX for v in values)
        assert values[0].split("\n")[1] == line

    def test_budget_respected_across_chunks(self):
        lines = [f"L{i:02d}" + "x" * 97 for i in range(25)]  # 100 chars each

        values = _fenced_chunks(lines, max_fields=2, budget=1200)

        # Field 1 = 1009 chars; the second field ("L10") fits the budget but
        # the third would bust it — the partial field is flushed and the 14
        # dropped lines become a more-line inside the last chunk.
        assert values == [
            "```\n" + "\n".join(lines[:10]) + "\n```",
            "```\n" + lines[10] + "\n" + strings.MORE_LINE.format(n=14) + "\n```",
        ]

    def test_budget_drop_silent_when_more_line_does_not_fit(self):
        values = _fenced_chunks([f"L{i}" for i in range(10)], budget=8)

        assert values == ["```\nL0\nL1\nL2\n```"]

    def test_budget_drop_appends_more_line_inside_last_chunk(self):
        values = _fenced_chunks(["x" * 100, "y" * 100], budget=120)

        assert values == ["```\n" + "x" * 100 + "\n" + strings.MORE_LINE.format(n=1) + "\n```"]

    def test_max_fields_drops_rest(self):
        lines = [f"L{i:02d}" + "x" * 95 for i in range(25)]  # 98 chars each

        values = _fenced_chunks(lines, max_fields=2)

        # Two 10-line chunks (989 chars each); the 5 dropped lines become a
        # more-line inside the last chunk.
        assert values == [
            "```\n" + "\n".join(lines[:10]) + "\n```",
            "```\n" + "\n".join(lines[10:20]) + "\n" + strings.MORE_LINE.format(n=5) + "\n```",
        ]

    def test_oversized_single_line_truncated_to_field_max(self):
        values = _fenced_chunks(["x" * 2000])

        assert len(values) == 1
        assert len(values[0]) == report_embed._FIELD_MAX  # fences included
        assert values[0].split("\n")[1] == "x" * (report_embed._FIELD_MAX - 8)

    def test_empty_lines(self):
        assert _fenced_chunks([]) == []

    def test_zero_max_fields(self):
        assert _fenced_chunks(["a", "b"], max_fields=0) == []
