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
- Events are sorted by village_id for stable embeds.
"""

import logging
from typing import Any

import pytest

from travian.metrics import compute_deltas, resolve_alliance_ids, village_events
from travian.models import VillageRow
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
        curr = [_row(1, 7, 1), _row(2, 7, 1)]

        gained, lost = village_events(prev, curr, {7})

        assert len(gained) == 1
        event = gained[0]
        assert event.event == "gained"
        assert event.village_id == 2
        assert event.village_name == "Village 2"
        assert event.x == 45
        assert event.y == -23
        assert event.new_owner_tag is None
        assert event.new_owner_player is None
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
        prev = [_row(1, 7, 1)]
        curr = [_row(1, 8, 5)]

        gained, lost = village_events(prev, curr, {7})

        assert gained == []
        assert len(lost) == 1
        event = lost[0]
        assert event.event == "lost_conquered"
        assert event.village_id == 1
        assert event.village_name == "Village 1"
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
        prev = [_row(1, 7, 1), _row(2, 7, 1)]
        curr = [_row(1, 7, 1)]

        gained, lost = village_events(prev, curr, {7})

        assert gained == []
        assert len(lost) == 1
        event = lost[0]
        assert event.event == "lost_deleted"
        assert event.village_id == 2
        assert event.village_name == "Village 2"
        assert event.old_player == "P1"
        assert event.new_owner_tag is None
        assert event.new_owner_player is None

    def test_events_sorted_by_village_id(self):
        prev = [_row(3, 7, 1), _row(1, 7, 1)]
        curr = [_row(1, 8, 5), _row(2, 7, 1), _row(3, 8, 5), _row(4, 7, 1)]

        gained, lost = village_events(prev, curr, {7})

        assert [e.village_id for e in gained] == [2, 4]
        assert [e.event for e in lost] == ["lost_conquered", "lost_conquered"]
        assert [e.village_id for e in lost] == [1, 3]

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
