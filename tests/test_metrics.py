"""Unit tests for travian.metrics — deltas, village events, alliance resolution.

Alliance identity is tracked by ``alliance_id`` (resolved from configured tags
against the CURRENT snapshot), making reports robust to tag renames. Decisions
locked by these tests:

- ``gained`` strictly means "in curr-ours, absent from prev with ANY owner"
  (a village conquered from an enemy is NOT an event).
- ``lost_conquered`` = was ours in prev, still exists in curr, curr owner's
  alliance NOT in ``alliance_ids``. A same-alliance player change is NOT an
  event.
- ``lost_deleted`` = was ours in prev, absent from curr entirely.
- ``prev_rows=None`` (no previous snapshot) → deltas None / no events.
- Events carry the village's region (and the founder for ``gained``); gained
  sorted by region then coords, lost conquered-before-deleted then conqueror
  tag, region, coords (stable — equal keys keep snapshot order).
- ``region_stats``: regions of interest = ours in curr OR prev (lost regions
  stay listed with zeros); ``None`` region groups as ``""``; share guards
  division-by-zero → 0.0; delta/vp_delta/share_delta None only when prev is
  None (a region absent from prev yields curr − 0; share_delta = curr share
  − prev share, prev share computed from ALL prev alliances); sorted by
  ``share`` desc, region name asc.
- ``top_players``: three separate rankings capped at n; player universe =
  curr-ours ∪ prev-ours; growth None only when prev is None; strict-gained
  for ``new_villages``; ties break by ``player_name`` ascending (growth and
  gains add population before name).
"""

import logging
from typing import Any

import pytest

from travian.metrics import (
    alliance_standings,
    compute_deltas,
    region_alliance_totals,
    region_stats,
    resolve_alliance_ids,
    top_players,
    village_events,
)
from travian.models import PlayerStat, RegionStat, VillageRow
from travian.store import connect, init_schema, recent_logs


def make_village_row(**overrides: Any) -> VillageRow:
    """Fixture: one realistic village (defaults belong to alliance 7, "WOLF")."""
    values: dict[str, Any] = {
        "village_id": 57,
        "x": 45,
        "y": -23,
        "tribe": 1,
        "name": "King's Landing",
        "player_id": 7,
        "player_name": "Tyrion Lannister",
        "alliance_id": 7,
        "alliance_tag": "WOLF",
        "population": 120,
        "region": None,
        "is_capital": False,
        "is_city": False,
        "is_harbor": False,
        "victory_points": 340,
    }
    values.update(overrides)
    return VillageRow(**values)


def _row(
    village_id: int,
    alliance_id: int,
    player_id: int,
    *,
    population: int = 100,
    victory_points: int = 300,
    region: str | None = None,
) -> VillageRow:
    """Concise row builder for delta/event scenarios (tag derived from alliance)."""
    return make_village_row(
        village_id=village_id,
        alliance_id=alliance_id,
        alliance_tag=f"A{alliance_id}",
        player_id=player_id,
        player_name=f"P{player_id}",
        population=population,
        victory_points=victory_points,
        region=region,
        name=f"Village {village_id}",
    )


class TestResolveAllianceIds:
    def test_resolves_tags_from_current_snapshot(self):
        curr = [
            make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLF"),
            make_village_row(village_id=2, alliance_id=9, alliance_tag="FALCON"),
        ]

        resolved, unresolved = resolve_alliance_ids(curr, ["WOLF", "FALCON"])

        assert resolved == {7, 9}
        assert unresolved == []

    def test_tags_stripped_and_deduped(self):
        curr = [make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLF")]

        resolved, unresolved = resolve_alliance_ids(curr, ["  WOLF", "WOLF", " WOLF "])

        assert resolved == {7}
        assert unresolved == []

    def test_two_tags_same_alliance_id_dedupe_in_resolved(self):
        curr = [
            make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLF"),
            make_village_row(village_id=2, alliance_id=7, alliance_tag="WOLVERINE"),
        ]

        resolved, unresolved = resolve_alliance_ids(curr, ["WOLF", "WOLVERINE"])

        assert resolved == {7}
        assert unresolved == []

    def test_unresolved_tag_warns_and_returns_subset(self, caplog: pytest.LogCaptureFixture):
        curr = [make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLF")]

        with caplog.at_level(logging.WARNING):
            resolved, unresolved = resolve_alliance_ids(curr, ["WOLF", "GHOST"])

        assert resolved == {7}
        assert unresolved == ["GHOST"]
        assert "GHOST" in caplog.text

    def test_unresolved_tag_appends_log_when_conn_given(self):
        conn = connect(":memory:")
        init_schema(conn)
        curr = [make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLF")]

        resolve_alliance_ids(curr, ["WOLF", "GHOST"], conn=conn)

        logs = recent_logs(conn)
        assert any(
            log["job"] == "config"
            and log["level"] == "warning"
            and "GHOST" in log["message"]
            for log in logs
        )

    def test_all_tags_unresolved_returns_empty_set(self, caplog: pytest.LogCaptureFixture):
        curr = [make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLF")]

        with caplog.at_level(logging.WARNING):
            resolved, unresolved = resolve_alliance_ids(curr, ["GHOST", "SPOOK"])

        assert resolved == set()
        assert unresolved == ["GHOST", "SPOOK"]

    def test_no_tags_returns_empty_without_warning(self, caplog: pytest.LogCaptureFixture):
        curr = [make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLF")]

        resolved, unresolved = resolve_alliance_ids(curr, [])

        assert resolved == set()
        assert unresolved == []
        assert caplog.text == ""

    def test_empty_curr_never_raises(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING):
            resolved, unresolved = resolve_alliance_ids([], ["WOLF"])

        assert resolved == set()
        assert unresolved == ["WOLF"]
        assert "WOLF" in caplog.text

    def test_blank_after_strip_dropped_silently(self):
        curr = [make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLF")]

        resolved, unresolved = resolve_alliance_ids(curr, ["   ", "WOLF"])

        assert resolved == {7}
        assert unresolved == []

    def test_tag_match_is_case_sensitive(self, caplog: pytest.LogCaptureFixture):
        curr = [make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLF")]

        with caplog.at_level(logging.WARNING):
            resolved, unresolved = resolve_alliance_ids(curr, ["wolf"])

        assert resolved == set()
        assert unresolved == ["wolf"]
        assert "wolf" in caplog.text


class TestComputeDeltas:
    def test_aggregates_curr_and_deltas(self):
        prev = [_row(1, 7, 1, population=100, victory_points=300), _row(2, 7, 2, population=150, victory_points=400)]
        curr = [
            _row(1, 7, 1, population=110, victory_points=310),
            _row(2, 7, 2, population=150, victory_points=400),
            _row(3, 7, 3, population=200, victory_points=500),
        ]

        summary = compute_deltas(prev, curr, {7})

        assert summary.villages == 3
        assert summary.population == 460
        assert summary.players == 3
        assert summary.vp == 1210
        assert summary.villages_delta == 1
        assert summary.population_delta == 210
        assert summary.players_delta == 1
        assert summary.vp_delta == 510

    def test_filters_other_alliances(self):
        curr = [
            _row(1, 7, 1, population=100, victory_points=300),
            _row(2, 8, 5, population=9000, victory_points=1000),
        ]

        summary = compute_deltas([], curr, {7})

        assert summary.villages == 1
        assert summary.population == 100
        assert summary.players == 1
        assert summary.vp == 300

    def test_players_counts_distinct_player_ids(self):
        curr = [_row(1, 7, 1), _row(2, 7, 1)]

        summary = compute_deltas(None, curr, {7})

        assert summary.players == 1

    def test_none_prev_deltas_none(self):
        curr = [_row(1, 7, 1)]

        summary = compute_deltas(None, curr, {7})

        assert summary.villages == 1
        assert summary.population == 100
        assert summary.villages_delta is None
        assert summary.population_delta is None
        assert summary.players_delta is None
        assert summary.vp_delta is None

    def test_prev_without_alliance_deltas_curr_minus_zero(self):
        # Alliance founded yesterday: prev snapshot exists but has no such alliance.
        prev = [_row(99, 8, 9, population=500, victory_points=100)]
        curr = [_row(1, 7, 1)]

        summary = compute_deltas(prev, curr, {7})

        assert summary.villages_delta == 1
        assert summary.population_delta == 100
        assert summary.players_delta == 1
        assert summary.vp_delta == 300

    def test_empty_alliance_ids_zeroes_without_crash(self):
        curr = [_row(1, 7, 1)]

        summary = compute_deltas([], curr, set())

        assert summary.villages == 0
        assert summary.population == 0
        assert summary.players == 0
        assert summary.vp == 0
        assert summary.villages_delta == 0
        assert summary.vp_delta == 0


class TestVillageEvents:
    def test_gained_only_in_curr(self):
        prev = [_row(1, 7, 1)]
        curr = [_row(1, 7, 1), _row(2, 7, 1, region="Eboracum")]

        gained, lost = village_events(prev, curr, {7})

        assert len(gained) == 1
        event = gained[0]
        assert event.event == "gained"
        assert event.village_id == 2
        assert event.village_name == "Village 2"
        assert event.x == 45
        assert event.y == -23
        assert event.region == "Eboracum"
        assert event.new_owner_tag is None
        assert event.new_owner_player == "P1"  # the founder (curr owner)
        assert event.old_player is None
        assert lost == []

    def test_gained_requires_absent_from_prev_any_owner(self):
        # Village conquered from an enemy is NOT gained (strict "only in curr").
        prev = [_row(1, 8, 5)]
        curr = [_row(1, 7, 1)]

        gained, lost = village_events(prev, curr, {7})

        assert gained == []
        assert lost == []

    def test_lost_conquered_new_owner(self):
        prev = [_row(1, 7, 1, region="Eboracum")]
        curr = [_row(1, 8, 5, region="Eboracum")]

        gained, lost = village_events(prev, curr, {7})

        assert gained == []
        assert len(lost) == 1
        event = lost[0]
        assert event.event == "lost_conquered"
        assert event.village_id == 1
        assert event.village_name == "Village 1"
        assert event.region == "Eboracum"
        assert event.new_owner_tag == "A8"
        assert event.new_owner_player == "P5"
        assert event.old_player == "P1"

    def test_same_alliance_player_change_not_an_event(self):
        prev = [_row(1, 7, 1)]
        curr = [_row(1, 7, 2)]

        gained, lost = village_events(prev, curr, {7})

        assert gained == []
        assert lost == []

    def test_lost_deleted_absent_from_curr(self):
        prev = [_row(1, 7, 1), _row(2, 7, 1, region="Eboracum")]
        curr = [_row(1, 7, 1)]

        gained, lost = village_events(prev, curr, {7})

        assert gained == []
        assert len(lost) == 1
        event = lost[0]
        assert event.event == "lost_deleted"
        assert event.village_id == 2
        assert event.village_name == "Village 2"
        assert event.region == "Eboracum"
        assert event.old_player == "P1"
        assert event.new_owner_tag is None
        assert event.new_owner_player is None

    def test_gained_sorted_by_region_then_coords(self):
        prev = [_row(9, 7, 1)]
        curr = [
            make_village_row(village_id=1, alliance_id=7, player_id=1, region="B", x=1, y=1),
            make_village_row(village_id=2, alliance_id=7, player_id=1, region="A", x=10, y=5),
            make_village_row(village_id=3, alliance_id=7, player_id=1, region="A", x=2, y=5),
            _row(9, 7, 1),
        ]

        gained, _ = village_events(prev, curr, {7})

        # A(x=2) before A(x=10) before B — region, then x, then y.
        assert [e.village_id for e in gained] == [3, 2, 1]

    def test_lost_sorted_conqueror_grouped_deleted_last(self):
        prev = [_row(1, 7, 1), _row(2, 7, 1), _row(3, 7, 1)]
        curr = [
            make_village_row(village_id=1, alliance_id=8, alliance_tag="ZETA", player_id=5),
            make_village_row(village_id=2, alliance_id=8, alliance_tag="AAA", player_id=5),
        ]

        _, lost = village_events(prev, curr, {7})

        # Conquered before deleted; conquered grouped by owner tag asc.
        assert [e.village_id for e in lost] == [2, 1, 3]
        assert [e.event for e in lost] == ["lost_conquered", "lost_conquered", "lost_deleted"]

    def test_none_prev_returns_no_events(self):
        curr = [_row(1, 7, 1)]

        gained, lost = village_events(None, curr, {7})

        assert gained == []
        assert lost == []

    def test_empty_prev_all_curr_ours_gained(self):
        curr = [_row(1, 7, 1), _row(2, 7, 2)]

        gained, lost = village_events([], curr, {7})

        assert [e.village_id for e in gained] == [1, 2]
        assert lost == []

    def test_tag_change_between_days_no_events(self):
        # alliance_id unchanged, tag renamed → no gained/lost events
        prev = [_row(1, 7, 1)]
        curr = [make_village_row(village_id=1, alliance_id=7, alliance_tag="WOLVERINE", player_id=1)]

        gained, lost = village_events(prev, curr, {7})

        assert gained == []
        assert lost == []

    def test_full_day_over_day_scenario(self):
        prev = [_row(1, 7, 1), _row(2, 7, 2), _row(3, 7, 1), _row(4, 7, 2), _row(5, 8, 9)]
        curr = [
            _row(1, 7, 1),  # unchanged
            _row(2, 7, 2, population=300),  # grew
            _row(3, 8, 5),  # lost_conquered
            _row(5, 8, 9),  # enemy, unchanged (ignored)
            _row(6, 7, 1),  # gained
            _row(7, 7, 3),  # gained
        ]  # village 4: lost_deleted

        gained, lost = village_events(prev, curr, {7})

        assert [e.event for e in gained] == ["gained", "gained"]
        assert [e.village_id for e in gained] == [6, 7]
        assert [e.event for e in lost] == ["lost_conquered", "lost_deleted"]
        assert [e.village_id for e in lost] == [3, 4]
        assert lost[0].old_player == "P1"
        assert lost[1].old_player == "P2"

    def test_deltas_and_events_agree_on_scenario(self):
        prev = [_row(1, 7, 1), _row(2, 7, 2), _row(3, 7, 1)]
        curr = [_row(1, 7, 1), _row(2, 7, 2), _row(4, 7, 3)]

        summary = compute_deltas(prev, curr, {7})
        gained, lost = village_events(prev, curr, {7})

        assert summary.villages == 3
        assert summary.villages_delta == 0
        assert summary.players_delta == 1
        assert [e.village_id for e in gained] == [4]
        assert [e.event for e in lost] == ["lost_deleted"]
        assert lost[0].village_id == 3


class TestRegionStats:
    def test_aggregates_our_region_with_share_and_delta(self):
        prev = [
            _row(1, 7, 1, population=100, victory_points=300, region="North"),
            _row(2, 7, 2, population=50, victory_points=400, region="North"),
        ]
        curr = [
            _row(1, 7, 1, population=110, victory_points=310, region="North"),
            _row(2, 7, 2, population=50, victory_points=400, region="North"),
            _row(3, 8, 5, population=900, victory_points=1000, region="North"),
        ]

        stats = region_stats(prev, curr, {7})

        assert len(stats) == 1
        region = stats[0]
        assert isinstance(region, RegionStat)
        assert region.region == "North"
        assert region.our_villages == 2
        assert region.our_pop == 160
        assert region.region_total_pop == 1060
        assert region.share == pytest.approx(160 / 1060)
        assert region.delta == 10
        assert region.our_vp == 710  # 310 + 400
        assert region.vp_delta == 10  # 710 − (300 + 400)
        assert region.share_delta == pytest.approx(160 / 1060 - 1.0)  # prev North was ours-only

    def test_multiple_regions_aggregated_independently(self):
        curr = [
            _row(1, 7, 1, population=100, region="North"),
            _row(2, 7, 1, population=200, region="South"),
            _row(3, 8, 5, population=700, region="North"),
        ]

        stats = region_stats(None, curr, {7})

        by_name = {s.region: s for s in stats}
        assert set(by_name) == {"North", "South"}
        north = by_name["North"]
        assert (north.our_villages, north.our_pop, north.region_total_pop) == (1, 100, 800)
        assert north.share == pytest.approx(100 / 800)
        assert north.delta is None
        assert north.our_vp == 300
        assert north.vp_delta is None
        assert north.share_delta is None  # no previous snapshot
        south = by_name["South"]
        assert (south.our_villages, south.our_pop, south.region_total_pop) == (1, 200, 200)
        assert south.share == pytest.approx(1.0)
        assert south.delta is None
        assert south.our_vp == 300
        assert south.vp_delta is None
        assert south.share_delta is None

    def test_enemy_only_region_in_curr_not_included(self):
        curr = [_row(9, 8, 5, population=900, region="EnemyLand")]

        stats = region_stats(None, curr, {7})

        assert stats == []

    def test_enemy_only_region_in_prev_not_included(self):
        prev = [_row(9, 8, 5, population=900, region="EnemyLand")]
        curr = [_row(1, 7, 1, population=100, region="Home")]

        stats = region_stats(prev, curr, {7})

        assert [s.region for s in stats] == ["Home"]

    def test_lost_region_still_listed_with_zero_values(self):
        prev = [_row(1, 7, 1, population=300, victory_points=500, region="Ghost")]

        stats = region_stats(prev, [], {7})

        assert len(stats) == 1
        ghost = stats[0]
        assert ghost.region == "Ghost"
        assert ghost.our_villages == 0
        assert ghost.our_pop == 0
        assert ghost.region_total_pop == 0
        assert ghost.share == 0.0
        assert ghost.delta == -300
        assert ghost.our_vp == 0
        assert ghost.vp_delta == -500  # curr 0 − prev 500
        assert ghost.share_delta == pytest.approx(-1.0)  # 0.0 − prev 300/300

    def test_lost_region_with_enemies_share_zero_delta_negative(self):
        prev = [_row(1, 7, 1, population=300, victory_points=500, region="Ghost")]
        curr = [_row(9, 8, 5, population=500, victory_points=600, region="Ghost")]

        stats = region_stats(prev, curr, {7})

        ghost = stats[0]
        assert ghost.our_pop == 0
        assert ghost.region_total_pop == 500
        assert ghost.share == 0.0
        assert ghost.delta == -300
        assert ghost.our_vp == 0
        assert ghost.vp_delta == -500
        assert ghost.share_delta == pytest.approx(-1.0)

    def test_share_zero_when_region_total_pop_is_zero(self):
        curr = [_row(1, 7, 1, population=0, region="Empty")]

        stats = region_stats(None, curr, {7})

        assert stats[0].region_total_pop == 0
        assert stats[0].share == 0.0

    def test_none_region_grouped_as_empty_string(self):
        curr = [
            make_village_row(village_id=1, alliance_id=7, player_id=1, population=100, region=None),
            make_village_row(village_id=2, alliance_id=7, player_id=1, population=50, region=""),
        ]

        stats = region_stats(None, curr, {7})

        assert len(stats) == 1
        assert stats[0].region == ""
        assert stats[0].our_villages == 2
        assert stats[0].our_pop == 150
        assert stats[0].our_vp == 680  # 2 × 340 (make_village_row default)
        assert stats[0].vp_delta is None

    def test_delta_curr_minus_zero_when_region_absent_in_prev(self):
        prev = [_row(9, 8, 5, population=500, region="North")]
        curr = [_row(1, 7, 1, population=100, region="North")]

        stats = region_stats(prev, curr, {7})

        assert stats[0].our_pop == 100
        assert stats[0].delta == 100
        assert stats[0].our_vp == 300
        assert stats[0].vp_delta == 300  # curr 300 − prev 0
        # prev had only the enemy (prev_share 0.0) → curr share 100/100 − 0
        assert stats[0].share_delta == pytest.approx(1.0)

    def test_share_delta_tracks_progress(self):
        prev = [
            _row(1, 7, 1, population=300, region="Alpha"),
            _row(2, 8, 5, population=300, region="Alpha"),
            _row(3, 7, 1, population=100, region="Beta"),
            _row(4, 8, 5, population=400, region="Beta"),
        ]
        curr = [
            _row(1, 7, 1, population=400, region="Alpha"),
            _row(2, 8, 5, population=400, region="Alpha"),
            _row(3, 7, 1, population=150, region="Beta"),
            _row(4, 8, 5, population=350, region="Beta"),
        ]

        stats = region_stats(prev, curr, {7})

        by_name = {s.region: s for s in stats}
        # Alpha: 400/800 − 300/600 = 0.5 − 0.5 = 0.0
        assert by_name["Alpha"].share_delta == pytest.approx(0.0)
        # Beta: 150/500 − 100/500 = 0.3 − 0.2 = +0.1
        assert by_name["Beta"].share_delta == pytest.approx(0.1)

    def test_sorted_by_share_desc_then_region_name(self):
        curr = [
            _row(1, 7, 1, population=50, region="Alpha"),
            _row(2, 8, 5, population=200, region="Alpha"),
            _row(3, 7, 1, population=300, region="Beta"),
            _row(4, 7, 1, population=50, region="Gamma"),
            _row(5, 8, 5, population=50, region="Gamma"),
            _row(6, 7, 1, population=100, region="Delta"),
            _row(7, 8, 5, population=100, region="Delta"),
        ]

        stats = region_stats(None, curr, {7})

        # shares: Beta 1.0, Delta 0.5, Gamma 0.5, Alpha 0.2 — share desc,
        # name asc on the 0.5 tie.
        assert [s.region for s in stats] == ["Beta", "Delta", "Gamma", "Alpha"]


class TestRegionAllianceTotals:
    def test_top5_alliances_by_population_per_region(self):
        rows = [
            _row(1, 7, 1, population=100, region="North"),
            _row(2, 7, 2, population=200, region="North"),
            _row(3, 8, 5, population=900, region="North"),
            _row(4, 9, 6, population=50, region="North"),
            _row(5, 10, 7, population=40, region="North"),
            _row(6, 11, 8, population=30, region="North"),
            _row(7, 12, 9, population=20, region="North"),
            _row(8, 13, 10, population=10, region="North"),
        ]

        totals = region_alliance_totals(rows)

        assert totals["North"] == [
            ("A8", 900),
            ("A7", 300),
            ("A9", 50),
            ("A10", 40),
            ("A11", 30),
        ]

    def test_tiebreak_by_tag_ascending(self):
        rows = [
            _row(1, 7, 1, population=100, region="North"),
            _row(2, 8, 5, population=100, region="North"),
        ]

        totals = region_alliance_totals(rows)

        assert totals["North"] == [("A7", 100), ("A8", 100)]

    def test_none_region_grouped_as_empty_string(self):
        rows = [make_village_row(village_id=1, alliance_id=7, player_id=1, population=100, region=None)]

        totals = region_alliance_totals(rows)

        assert totals == {"": [("WOLF", 100)]}

    def test_empty_curr_returns_empty(self):
        assert region_alliance_totals([]) == {}


class TestTopPlayers:
    def test_population_ranking_with_stats_and_growth(self):
        prev = [
            _row(1, 7, 1, population=90),
            _row(2, 7, 1, population=150),
            _row(3, 7, 2, population=500),
        ]
        curr = [
            _row(1, 7, 1, population=100),
            _row(2, 7, 1, population=150),
            _row(3, 7, 2, population=400),
            _row(4, 7, 3, population=300),
            _row(5, 8, 9, population=9999),
        ]

        result = top_players(curr, prev, {7})

        ranking = result["population"]
        assert [p.player_id for p in ranking] == [2, 3, 1]
        assert isinstance(ranking[0], PlayerStat)
        assert ranking[0].player_name == "P2"
        assert ranking[0].population == 400
        assert ranking[0].villages == 1
        assert ranking[0].growth == -100
        assert ranking[1].growth == 300
        assert ranking[2].growth == 10
        assert [p.player_id for p in result["growth"]] == [3, 1, 2]
        # gains tie (P1/P2 both 0) breaks by population desc: P2 400 > P1 250
        assert [p.player_id for p in result["new_villages"]] == [3, 2, 1]
        # gains carried in every ranking: P3 gained village 4, P1/P2 none
        assert [p.gains for p in result["population"]] == [0, 1, 0]
        assert [p.gains for p in result["growth"]] == [1, 0, 0]
        assert [p.gains for p in result["new_villages"]] == [1, 0, 0]

    def test_rankings_respect_cap(self):
        curr = [_row(i, 7, i, population=100 + i) for i in range(1, 8)]
        prev = [_row(i, 7, i, population=100) for i in range(1, 8)]

        default = top_players(curr, prev, {7})
        assert len(default["population"]) == 5
        assert len(default["growth"]) == 5
        assert len(default["new_villages"]) == 5
        assert [p.player_id for p in default["growth"]] == [7, 6, 5, 4, 3]

        small = top_players(curr, prev, {7}, n=3)
        assert [p.player_id for p in small["population"]] == [7, 6, 5]

    def test_only_our_alliance_players_and_key_order(self):
        curr = [
            _row(1, 7, 1, population=100),
            _row(2, 8, 5, population=9000),
        ]

        result = top_players(curr, None, {7})

        assert list(result) == ["population", "growth", "new_villages"]
        for key in ("population", "growth", "new_villages"):
            assert [p.player_id for p in result[key]] == [1]

    def test_growth_ranking_by_delta_including_negative(self):
        prev = [
            _row(1, 7, 1, population=100),
            _row(2, 7, 2, population=100),
            _row(3, 7, 3, population=100),
        ]
        curr = [
            _row(1, 7, 1, population=200),
            _row(2, 7, 2, population=50),
            _row(3, 7, 3, population=150),
        ]

        ranking = top_players(curr, prev, {7})["growth"]

        assert [p.player_id for p in ranking] == [1, 3, 2]
        assert [p.growth for p in ranking] == [100, 50, -50]

    def test_growth_and_gains_degenerate_when_prev_none(self):
        curr = [_row(1, 7, 1, population=100), _row(2, 7, 2, population=300)]

        result = top_players(curr, None, {7})

        assert [p.player_id for p in result["growth"]] == [2, 1]
        assert all(p.growth is None for p in result["growth"])
        assert [p.player_id for p in result["new_villages"]] == [2, 1]
        assert all(p.gains == 0 for p in result["new_villages"])

    def test_growth_int_when_prev_snapshot_empty(self):
        curr = [_row(1, 7, 1, population=100)]

        result = top_players(curr, [], {7})

        assert result["population"][0].growth == 100
        assert result["growth"][0].growth == 100
        assert [p.player_id for p in result["new_villages"]] == [1]
        assert result["population"][0].gains == 1

    def test_new_villages_ranking_uses_strict_gained(self):
        prev = [_row(1, 7, 1), _row(9, 8, 5), _row(5, 8, 5)]
        curr = [
            _row(1, 7, 1),
            _row(2, 7, 1),
            _row(3, 7, 2),
            _row(9, 7, 2),
            _row(4, 7, 3),
            _row(6, 7, 3),
            _row(5, 8, 5),
        ]

        ranking = top_players(curr, prev, {7})["new_villages"]

        assert [p.player_id for p in ranking] == [3, 1, 2]
        assert [p.gains for p in ranking] == [2, 1, 1]  # P3 gained 4+6, P1 gained 2, P2 gained 3

    def test_tiebreak_by_player_name(self):
        # Equal population and growth: name asc wins over player_id order.
        prev = [
            make_village_row(village_id=1, alliance_id=7, player_id=1, player_name="Zed", population=100),
            make_village_row(village_id=2, alliance_id=7, player_id=2, player_name="Aaron", population=100),
        ]
        curr = [
            make_village_row(village_id=1, alliance_id=7, player_id=1, player_name="Zed", population=200),
            make_village_row(village_id=2, alliance_id=7, player_id=2, player_name="Aaron", population=200),
        ]

        result = top_players(curr, prev, {7})

        assert [p.player_id for p in result["population"]] == [2, 1]
        assert [p.player_id for p in result["growth"]] == [2, 1]

    def test_growth_tie_break_by_population_then_name(self):
        # Growth all +100; P2 and P3 tie on population 500 → name asc.
        prev = [
            _row(1, 7, 1, population=200),
            _row(2, 7, 2, population=400),
            _row(3, 7, 3, population=400),
        ]
        curr = [
            _row(1, 7, 1, population=300),
            _row(2, 7, 2, population=500),
            _row(3, 7, 3, population=500),
        ]

        ranking = top_players(curr, prev, {7})["growth"]

        assert [p.player_id for p in ranking] == [2, 3, 1]
        assert [p.growth for p in ranking] == [100, 100, 100]

    def test_gains_tie_break_by_population_then_name(self):
        # P2 and P3 both gained one village; higher population first.
        prev = [
            _row(1, 7, 1, population=100),
            _row(2, 7, 2, population=100),
            _row(3, 7, 3, population=100),
        ]
        curr = [
            _row(1, 7, 1, population=100),
            _row(2, 7, 2, population=100),
            _row(3, 7, 3, population=100),
            _row(4, 7, 2, population=200),
            _row(5, 7, 3, population=100),
        ]

        ranking = top_players(curr, prev, {7})["new_villages"]

        assert [p.player_id for p in ranking] == [2, 3, 1]
        assert [p.gains for p in ranking] == [1, 1, 0]

    def test_departed_player_in_growth_ranking_with_negative_growth(self):
        prev = [_row(1, 7, 1, population=100), _row(2, 7, 2, population=500)]
        curr = [_row(1, 7, 1, population=110)]

        result = top_players(curr, prev, {7})

        ranking = result["growth"]
        assert [p.player_id for p in ranking] == [1, 2]
        assert ranking[1].population == 0
        assert ranking[1].villages == 0
        assert ranking[1].growth == -500
        assert [p.player_id for p in result["population"]] == [1, 2]


class TestAllianceStandings:
    """Per-tag comparison rows for the report's Standings field."""

    def test_aggregates_per_tag_in_config_order(self):
        curr = [
            _row(1, 7, 1, population=100, victory_points=300),
            _row(2, 7, 1, population=200, victory_points=600),
            _row(3, 8, 2, population=50, victory_points=100),
            _row(4, 8, 3, population=50, victory_points=100),
        ]

        standings = alliance_standings(None, curr, ["A7", "A8"])

        assert [s.tag for s in standings] == ["A7", "A8"]  # config order, not a sort
        a7, a8 = standings
        assert (a7.villages, a7.population, a7.players, a7.vp) == (2, 300, 1, 900)
        assert (a8.villages, a8.population, a8.players, a8.vp) == (2, 100, 2, 200)
        assert all(d is None for d in (a7.villages_delta, a7.population_delta, a7.players_delta, a7.vp_delta))

    def test_deltas_from_previous_snapshot(self):
        prev = [
            _row(1, 7, 1, population=100, victory_points=300),
            _row(2, 7, 1, population=200, victory_points=600),
        ]
        curr = [
            _row(1, 7, 1, population=150, victory_points=350),
            _row(2, 7, 1, population=200, victory_points=600),
            _row(3, 7, 2, population=50, victory_points=100),
        ]

        standings = alliance_standings(prev, curr, ["A7"])

        assert len(standings) == 1
        s = standings[0]
        assert (s.villages_delta, s.population_delta, s.players_delta, s.vp_delta) == (1, 100, 1, 150)

    def test_prev_matched_by_curr_ids_survives_tag_rename(self):
        # Same alliance_id, renamed tag: deltas are real, not curr - 0.
        prev = [_row(1, 7, 1, population=100)]
        prev[0].alliance_tag = "OLD"
        curr = [_row(1, 7, 1, population=130)]

        standings = alliance_standings(prev, curr, ["A7"])

        assert standings[0].population_delta == 30

    def test_tag_absent_from_prev_yields_curr_minus_zero(self):
        prev = [_row(1, 7, 1, population=100)]
        curr = [_row(1, 7, 1, population=100), _row(2, 8, 2, population=60)]

        standings = alliance_standings(prev, curr, ["A8"])

        assert standings[0].population_delta == 60
        assert standings[0].villages_delta == 1

    def test_unresolved_tag_skipped_with_warning(self, caplog):
        curr = [_row(1, 7, 1)]

        with caplog.at_level(logging.WARNING):
            standings = alliance_standings(None, curr, ["A7", "NOPE"])

        assert [s.tag for s in standings] == ["A7"]
        assert any("NOPE" in r.message for r in caplog.records)

    def test_normalization_strip_dedupe(self):
        curr = [_row(1, 7, 1), _row(2, 8, 2)]

        standings = alliance_standings(None, curr, [" A7 ", "A7", "a7", "A8", ""])

        assert [s.tag for s in standings] == ["A7", "A8"]  # first occurrence, case-sensitive

    def test_tag_matching_multiple_ids_unions(self):
        curr = [
            _row(1, 7, 1, population=100),
            _row(2, 9, 2, population=50),
            _row(3, 9, 3, population=25),
        ]
        # A7 + A9 share the tag "A7"? No — give both ids the SAME tag.
        curr[1].alliance_tag = "A7"
        curr[2].alliance_tag = "A7"

        standings = alliance_standings(None, curr, ["A7"])

        assert (standings[0].villages, standings[0].population, standings[0].players) == (3, 175, 3)

    def test_empty_tags_yields_empty_standings(self):
        assert alliance_standings(None, [_row(1, 7, 1)], []) == []
