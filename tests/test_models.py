"""Unit tests for travian.models — the cross-module data contract.

Field names here ARE the contract: map_sql (task 3), metrics (tasks 6-7) and
the embed builder (task 8) all consume these models.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from travian.models import (
    DeltaSummary,
    PlayerStat,
    RegionStat,
    ReportData,
    SnapshotDates,
    VillageEvent,
    VillageRow,
)


def make_village_row(**overrides: Any) -> VillageRow:
    """Fixture: one realistic map.sql line (region NULL, HTML entity unescaped)."""
    values: dict[str, Any] = {
        "village_id": 57,
        "x": 45,
        "y": -23,
        "tribe": 1,
        "name": "King's Landing",
        "player_id": 7,
        "player_name": "Tyrion Lannister",
        "alliance_id": 2,
        "alliance_tag": "Winterfell",
        "population": 120,
        "region": None,
        "is_capital": False,
        "is_city": False,
        "is_harbor": False,
        "victory_points": 340,
    }
    values.update(overrides)
    return VillageRow(**values)


def make_village_event(**overrides: Any) -> VillageEvent:
    """Fixture: a 'gained' event (all owner fields None by default)."""
    values: dict[str, Any] = {
        "village_id": 57,
        "village_name": "King's Landing",
        "x": 45,
        "y": -23,
        "event": "gained",
        "new_owner_tag": None,
        "new_owner_player": None,
        "old_player": None,
    }
    values.update(overrides)
    return VillageEvent(**values)


def make_report_data(**overrides: Any) -> ReportData:
    """Fixture: complete ReportData with every block populated."""
    values: dict[str, Any] = {
        "snapshot_date": "2026-08-08",
        "server": "x2.international",
        "alliance_tags": ["Winterfell"],
        "summary": DeltaSummary(
            villages=42,
            population=5000,
            players=11,
            vp=340,
            villages_delta=1,
            population_delta=120,
            players_delta=0,
            vp_delta=10,
        ),
        "new_villages": [make_village_event()],
        "lost_villages": [
            make_village_event(
                village_id=58,
                village_name="Winterfell",
                x=1,
                y=1,
                event="lost_conquered",
                new_owner_tag="House Stark",
                new_owner_player="Arya Stark",
                old_player="Tyrion Lannister",
            ),
        ],
        "regions": [
            RegionStat(
                region="North",
                our_villages=42,
                our_pop=5000,
                region_total_pop=8000,
                share=0.625,
                delta=120,
            ),
        ],
    }
    values.update(overrides)
    return ReportData(**values)


class TestVillageRow:
    def test_constructs_with_region_none(self):
        row = make_village_row()

        assert row.village_id == 57
        assert row.name == "King's Landing"
        assert row.region is None
        assert row.victory_points == 340
        assert row.is_capital is False
        assert row.is_city is False
        assert row.is_harbor is False

    def test_accepts_region_string(self):
        row = make_village_row(region="North")

        assert row.region == "North"

    def test_all_15_fields_required(self):
        with pytest.raises(ValidationError, match="player_id"):
            VillageRow(
                village_id=57,
                x=45,
                y=-23,
                tribe=1,
                name="King's Landing",
            )

    def test_wrong_field_type_has_readable_message(self):
        with pytest.raises(ValidationError, match="population"):
            make_village_row(population="abc")

    def test_serialization_roundtrip(self):
        row = make_village_row(region=None, is_capital=True)

        assert VillageRow.model_validate(row.model_dump()) == row


class TestSnapshotDates:
    def test_both_none(self):
        dates = SnapshotDates(latest=None, previous=None)

        assert dates.latest is None
        assert dates.previous is None

    def test_iso_strings(self):
        dates = SnapshotDates(latest="2026-08-08", previous="2026-08-07")

        assert dates.latest == "2026-08-08"
        assert dates.previous == "2026-08-07"

    def test_serialization_roundtrip(self):
        dates = SnapshotDates(latest="2026-08-08", previous=None)

        assert SnapshotDates.model_validate(dates.model_dump()) == dates


class TestDeltaSummary:
    def test_deltas_none_when_no_previous_snapshot(self):
        summary = DeltaSummary(
            villages=42,
            population=5000,
            players=11,
            vp=340,
            villages_delta=None,
            population_delta=None,
            players_delta=None,
            vp_delta=None,
        )

        assert summary.villages_delta is None
        assert summary.population_delta is None
        assert summary.players_delta is None
        assert summary.vp_delta is None

    def test_deltas_int_when_previous_snapshot_exists(self):
        summary = DeltaSummary(
            villages=42,
            population=5000,
            players=11,
            vp=340,
            villages_delta=1,
            population_delta=120,
            players_delta=0,
            vp_delta=10,
        )

        assert summary.villages_delta == 1
        assert summary.population_delta == 120
        assert summary.players_delta == 0
        assert summary.vp_delta == 10

    def test_serialization_roundtrip(self):
        summary = DeltaSummary(
            villages=42,
            population=5000,
            players=11,
            vp=340,
            villages_delta=None,
            population_delta=120,
            players_delta=None,
            vp_delta=10,
        )

        assert DeltaSummary.model_validate(summary.model_dump()) == summary


class TestVillageEvent:
    def test_gained(self):
        event = make_village_event()

        assert event.event == "gained"
        assert event.old_player is None
        assert event.new_owner_tag is None

    def test_lost_conquered_carries_new_owner(self):
        event = make_village_event(
            village_id=58,
            village_name="Winterfell",
            x=1,
            y=1,
            event="lost_conquered",
            new_owner_tag="House Stark",
            new_owner_player="Arya Stark",
            old_player="Tyrion Lannister",
        )

        assert event.event == "lost_conquered"
        assert event.new_owner_tag == "House Stark"
        assert event.new_owner_player == "Arya Stark"
        assert event.old_player == "Tyrion Lannister"

    def test_lost_deleted_has_no_new_owner(self):
        event = make_village_event(
            village_id=59,
            village_name="Castle Black",
            event="lost_deleted",
            old_player="Jon Snow",
        )

        assert event.event == "lost_deleted"
        assert event.new_owner_tag is None
        assert event.new_owner_player is None
        assert event.old_player == "Jon Snow"

    def test_rejects_unknown_event(self):
        with pytest.raises(ValidationError, match="event"):
            make_village_event(event="stolen")

    def test_serialization_roundtrip(self):
        event = make_village_event(
            village_id=58,
            event="lost_conquered",
            new_owner_tag="House Stark",
            new_owner_player="Arya Stark",
            old_player="Tyrion Lannister",
        )

        assert VillageEvent.model_validate(event.model_dump()) == event


class TestPlayerStat:
    def test_growth_none_when_no_previous(self):
        stat = PlayerStat(
            player_id=7,
            player_name="Tyrion Lannister",
            population=5000,
            villages=42,
            growth=None,
            vp=900,
        )

        assert stat.growth is None
        assert stat.vp == 900

    def test_serialization_roundtrip(self):
        stat = PlayerStat(
            player_id=7,
            player_name="Tyrion Lannister",
            population=5000,
            villages=42,
            growth=120,
            vp=900,
        )

        assert PlayerStat.model_validate(stat.model_dump()) == stat

    def test_gains_defaults_to_none(self):
        stat = PlayerStat(
            player_id=7,
            player_name="Tyrion Lannister",
            population=5000,
            villages=42,
            growth=120,
            vp=900,
        )

        assert stat.gains is None

    def test_vp_required(self):
        with pytest.raises(ValidationError):
            PlayerStat(
                player_id=7,
                player_name="Tyrion Lannister",
                population=5000,
                villages=42,
                growth=120,
            )

    def test_gains_roundtrip(self):
        stat = PlayerStat(
            player_id=7,
            player_name="Tyrion Lannister",
            population=5000,
            villages=42,
            growth=120,
            vp=900,
            gains=3,
        )

        assert PlayerStat.model_validate(stat.model_dump()) == stat
        assert stat.gains == 3

class TestRegionStat:
    def test_share_is_fraction(self):
        stat = RegionStat(
            region="North",
            our_villages=42,
            our_pop=5000,
            region_total_pop=8000,
            share=0.625,
            delta=120,
        )

        assert stat.share == 0.625

    def test_delta_none_when_no_previous(self):
        stat = RegionStat(
            region="North",
            our_villages=42,
            our_pop=5000,
            region_total_pop=8000,
            share=0.625,
            delta=None,
        )

        assert stat.delta is None

    def test_serialization_roundtrip(self):
        stat = RegionStat(
            region="North",
            our_villages=42,
            our_pop=5000,
            region_total_pop=8000,
            share=0.625,
            delta=None,
        )

        assert RegionStat.model_validate(stat.model_dump()) == stat


class TestReportData:
    def test_complete_fixture_builds(self):
        report = make_report_data()

        assert report.snapshot_date == "2026-08-08"
        assert report.server == "x2.international"
        assert report.alliance_tags == ["Winterfell"]
        assert report.summary.villages == 42
        assert len(report.new_villages) == 1
        assert report.new_villages[0].event == "gained"
        assert len(report.lost_villages) == 1
        assert report.lost_villages[0].event == "lost_conquered"
        assert report.regions[0].share == 0.625

    def test_empty_lists_and_none_deltas_ok(self):
        report = make_report_data(
            snapshot_date=None,
            summary=DeltaSummary(
                villages=0,
                population=0,
                players=0,
                vp=0,
                villages_delta=None,
                population_delta=None,
                players_delta=None,
                vp_delta=None,
            ),
            new_villages=[],
            lost_villages=[],
            regions=[],
        )

        assert report.snapshot_date is None
        assert report.new_villages == []
        assert report.lost_villages == []
        assert report.regions == []

    def test_serialization_roundtrip(self):
        report = make_report_data()

        assert ReportData.model_validate(report.model_dump()) == report
