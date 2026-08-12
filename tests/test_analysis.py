"""Tests for travian.analysis — the pure dashboard series builders and the
moved region control rules (shared with the report embed, so the dashboard
and the daily report can never disagree on the ≥ 4,000 / > 50% rules).
"""

from travian.analysis import (
    REGION_ACTIVE_MIN,
    region_active,
    region_controlled,
    region_share_series,
    standings_series,
    summary_history,
    to50_needed,
)
from travian.models import AllianceDay, RegionDay, RegionStat, SummaryDay


def make_region_stat(
    region: str = "A",
    our_pop: int = 1000,
    total: int = 5000,
) -> RegionStat:
    return RegionStat(
        region=region,
        our_villages=1,
        our_pop=our_pop,
        region_total_pop=total,
        share=our_pop / total if total else 0.0,
        delta=1,
    )


class TestRegionControlRules:
    def test_active_threshold_is_4000(self) -> None:
        assert REGION_ACTIVE_MIN == 4000
        assert region_active(make_region_stat(total=3999)) is False
        assert region_active(make_region_stat(total=4000)) is True

    def test_to50_needed_none_when_inactive(self) -> None:
        assert to50_needed(make_region_stat(total=3000)) is None

    def test_to50_needed_is_first_pop_strictly_above_half(self) -> None:
        # Even total: 4001 of 8000 is the first strictly-above-half count.
        assert to50_needed(make_region_stat(our_pop=4000, total=8000)) == 1
        assert to50_needed(make_region_stat(our_pop=4001, total=8000)) == 0
        # Odd total: 4001 of 8001 (4000.5 rounds up).
        assert to50_needed(make_region_stat(our_pop=4000, total=8001)) == 1

    def test_exactly_50_percent_is_not_controlled(self) -> None:
        assert region_controlled(make_region_stat(our_pop=4000, total=8000)) is False
        assert region_controlled(make_region_stat(our_pop=4001, total=8000)) is True

    def test_inactive_region_never_controlled(self) -> None:
        assert region_controlled(make_region_stat(our_pop=3000, total=3000)) is False


class TestRegionShareSeries:
    def test_splits_by_region_with_dates_asc(self) -> None:
        days = [
            RegionDay(date="2026-08-07", region="North", our_pop=100, total_pop=200),
            RegionDay(date="2026-08-08", region="North", our_pop=150, total_pop=200),
            RegionDay(date="2026-08-07", region="South", our_pop=10, total_pop=100),
            RegionDay(date="2026-08-08", region="South", our_pop=20, total_pop=100),
        ]

        series = region_share_series(days)

        assert series == {
            "North": [("2026-08-07", 0.5), ("2026-08-08", 0.75)],
            "South": [("2026-08-07", 0.1), ("2026-08-08", 0.2)],
        }

    def test_division_by_zero_guard_yields_zero(self) -> None:
        days = [RegionDay(date="2026-08-08", region="Empty", our_pop=0, total_pop=0)]

        assert region_share_series(days) == {"Empty": [("2026-08-08", 0.0)]}

    def test_empty_input(self) -> None:
        assert region_share_series([]) == {}


class TestStandingsSeries:
    def test_rows_carry_points_and_ours_flag(self) -> None:
        days = [
            AllianceDay(date="2026-08-07", alliance_id=1, alliance_tag="WOLF", villages=10, population=1000, vp=90),
            AllianceDay(date="2026-08-08", alliance_id=1, alliance_tag="WOLF", villages=11, population=1100, vp=95),
            AllianceDay(date="2026-08-07", alliance_id=2, alliance_tag="ENEMY", villages=9, population=900, vp=80),
            AllianceDay(date="2026-08-08", alliance_id=2, alliance_tag="ENEMY", villages=9, population=900, vp=85),
        ]

        rows = standings_series(days, our_tags={"WOLF"})

        assert rows[0] == {
            "alliance_id": 1,
            "tag": "WOLF",
            "ours": True,
            "points": [("2026-08-07", 1000), ("2026-08-08", 1100)],
            "vp_points": [("2026-08-07", 90), ("2026-08-08", 95)],
        }
        assert rows[1]["ours"] is False
        assert [row["tag"] for row in rows] == ["WOLF", "ENEMY"]  # latest-pop desc

    def test_ordered_by_latest_population_desc(self) -> None:
        days = [
            AllianceDay(date="2026-08-08", alliance_id=1, alliance_tag="Small", villages=1, population=100, vp=1),
            AllianceDay(date="2026-08-08", alliance_id=2, alliance_tag="Big", villages=1, population=900, vp=1),
        ]

        rows = standings_series(days, our_tags=set())

        assert [row["tag"] for row in rows] == ["Big", "Small"]

    def test_tag_from_latest_day(self) -> None:
        days = [
            AllianceDay(date="2026-08-07", alliance_id=1, alliance_tag="OLD", villages=1, population=100, vp=1),
            AllianceDay(date="2026-08-08", alliance_id=1, alliance_tag="NEW", villages=1, population=110, vp=1),
        ]

        rows = standings_series(days, our_tags=set())

        assert rows[0]["tag"] == "NEW"

    def test_empty_input(self) -> None:
        assert standings_series([], set()) == []


class TestSummaryHistory:
    def test_deltas_vs_previous_date_none_on_first(self) -> None:
        days = [
            SummaryDay(date="2026-08-07", villages=10, population=1000, players=5, vp=90),
            SummaryDay(date="2026-08-08", villages=12, population=1100, players=5, vp=95),
            SummaryDay(date="2026-08-09", villages=11, population=1050, players=4, vp=90),
        ]

        rows = summary_history(days)

        assert rows == [
            {
                "date": "2026-08-07",
                "previous_date": None,
                "elapsed_days": None,
                "villages": 10,
                "population": 1000,
                "players": 5,
                "vp": 90,
                "villages_delta": None,
                "population_delta": None,
                "players_delta": None,
                "vp_delta": None,
            },
            {
                "date": "2026-08-08",
                "previous_date": "2026-08-07",
                "elapsed_days": 1,
                "villages": 12,
                "population": 1100,
                "players": 5,
                "vp": 95,
                "villages_delta": 2,
                "population_delta": 100,
                "players_delta": 0,
                "vp_delta": 5,
            },
            {
                "date": "2026-08-09",
                "previous_date": "2026-08-08",
                "elapsed_days": 1,
                "villages": 11,
                "population": 1050,
                "players": 4,
                "vp": 90,
                "villages_delta": -1,
                "population_delta": -50,
                "players_delta": -1,
                "vp_delta": -5,
            },
        ]

    def test_gap_carries_previous_date_and_elapsed_days(self) -> None:
        """A six-day gap is reported as context; the delta stays the plain
        subtraction (presentation change, not a metric change)."""
        days = [
            SummaryDay(date="2026-08-02", villages=10, population=1000, players=5, vp=90),
            SummaryDay(date="2026-08-08", villages=16, population=1600, players=6, vp=95),
        ]

        rows = summary_history(days)

        assert rows[0]["previous_date"] is None
        assert rows[0]["elapsed_days"] is None
        assert rows[1]["previous_date"] == "2026-08-02"
        assert rows[1]["elapsed_days"] == 6
        assert rows[1]["villages_delta"] == 6  # unchanged subtraction
        assert rows[1]["population_delta"] == 600
        assert rows[1]["players_delta"] == 1
        assert rows[1]["vp_delta"] == 5

    def test_empty_input(self) -> None:
        assert summary_history([]) == []
