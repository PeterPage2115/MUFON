"""Pure analysis helpers for the dashboard (region/alliance/headline series).

Shared home of the region control rules (moved verbatim from
``report_embed.py``) and the series builders consumed by the dashboard's
``/api/analysis/*`` endpoints. No IO, no settings: the store aggregators
(``store.region_days``/``alliance_days``/``summary_days``) produce the day
lists, and these functions derive series from them — the report embed and
the dashboard therefore can never disagree on the ≥ 4,000 / > 50% rules or
on how deltas are computed.

Series semantics reuse the established metrics formulas: shares are
``our_pop / total_pop`` with a division-by-zero guard → 0.0; deltas are
value minus the previous date's, ``None`` for the first date (the same
semantics as ``metrics.compute_deltas``).
"""

from datetime import date
from typing import cast

from travian.models import AllianceDay, RegionDay, RegionStat, SummaryDay

REGION_ACTIVE_MIN = 4000  # game rule: a region is active with ≥ 4,000 total population


def region_active(r: RegionStat) -> bool:
    """Game rule: a region is active (control possible) with ≥ 4,000 total population."""
    return r.region_total_pop >= REGION_ACTIVE_MIN


def to50_needed(r: RegionStat) -> int | None:
    """Population still needed to exceed 50% — None when the region is inactive.

    ``(total // 2) + 1`` is the first population count strictly above half
    (the old ``ceil(total * 0.5)`` rendered "+0" at exactly 50% of an even
    total).
    """
    if not region_active(r):
        return None
    return (r.region_total_pop // 2) + 1 - r.our_pop


def region_controlled(r: RegionStat) -> bool:
    """Control = active region with strictly more than half the population."""
    needed = to50_needed(r)
    return needed is not None and needed <= 0


def region_share_series(days: list[RegionDay]) -> dict[str, list[tuple[str, float]]]:
    """``region → [(date, our_pop/total_pop), ...]`` — dates ASC per region.

    Division-by-zero guard → 0.0 (a region whose total population is 0).
    """
    by_region: dict[str, list[tuple[str, float]]] = {}
    for day in days:
        share = day.our_pop / day.total_pop if day.total_pop else 0.0
        by_region.setdefault(day.region, []).append((day.date, share))
    return by_region


def standings_series(days: list[AllianceDay], our_tags: set[str]) -> list[dict[str, object]]:
    """Per-alliance series for the standings chart.

    Each row: ``{alliance_id, tag, ours, points: [(date, population)],
    vp_points: [(date, vp)]}`` — ``ours`` = the alliance's tag ∈ ``our_tags``
    (the UI highlights our alliances without duplicating config knowledge).
    Ordered by population desc at the alliance's latest date, alliance_id asc
    as tiebreak.
    """
    by_id: dict[int, list[AllianceDay]] = {}
    for day in days:
        by_id.setdefault(day.alliance_id, []).append(day)

    rows: list[dict[str, object]] = []
    for alliance_id, day_rows in by_id.items():
        latest = day_rows[-1]  # days arrive date-ASC
        rows.append(
            {
                "alliance_id": alliance_id,
                "tag": latest.alliance_tag,
                "ours": latest.alliance_tag in our_tags,
                "points": [(d.date, d.population) for d in day_rows],
                "vp_points": [(d.date, d.vp) for d in day_rows],
            }
        )

    def latest_pop(row: dict[str, object]) -> int:
        points = cast(list[tuple[str, int]], row["points"])
        return points[-1][1] if points else 0

    rows.sort(key=lambda r: (-latest_pop(r), cast(int, r["alliance_id"])))
    return rows


def summary_history(days: list[SummaryDay]) -> list[dict[str, object]]:
    """Per-date headline history with day-over-day deltas.

    Each row: ``{date, previous_date, elapsed_days, villages, population,
    players, vp, villages_delta, population_delta, players_delta,
    vp_delta}`` — deltas vs the previous date, ``None`` for the first date
    (``compute_deltas`` semantics). ``previous_date``/``elapsed_days`` carry
    the ACTUAL comparison horizon (the calendar difference between the two
    snapshot dates, even when it is 1), so the UI can honestly mark deltas
    computed across a gap — the delta values themselves are unchanged.
    """
    rows: list[dict[str, object]] = []
    prev: SummaryDay | None = None
    for day in days:
        previous_date: str | None = None
        elapsed_days: int | None = None
        if prev is not None:
            previous_date = prev.date
            elapsed_days = (date.fromisoformat(day.date) - date.fromisoformat(prev.date)).days
        rows.append(
            {
                "date": day.date,
                "previous_date": previous_date,
                "elapsed_days": elapsed_days,
                "villages": day.villages,
                "population": day.population,
                "players": day.players,
                "vp": day.vp,
                "villages_delta": None if prev is None else day.villages - prev.villages,
                "population_delta": None if prev is None else day.population - prev.population,
                "players_delta": None if prev is None else day.players - prev.players,
                "vp_delta": None if prev is None else day.vp - prev.vp,
            }
        )
        prev = day
    return rows
