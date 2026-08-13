"""Pydantic v2 data models shared across the travian report pipeline.

Field names ARE the cross-module contract: ``map_sql`` (task 3) produces
``VillageRow``, ``metrics`` (tasks 6-7) produces ``DeltaSummary``,
``VillageEvent``, ``PlayerStat`` and ``RegionStat``, and the embed builder
(task 8) consumes ``ReportData``. Names follow the SQLite DDL in the plan
(snake_case: ``is_capital``/``is_city``/``is_harbor``), not the camelCase
map.sql source.
"""

from typing import Literal

from pydantic import BaseModel


class VillageRow(BaseModel):
    """One village from a map.sql snapshot line.

    Mirrors the ``villages`` DDL: the map.sql row ``id`` field is dropped
    (``village_id`` is the identity) and camelCase map.sql booleans become
    snake_case. ``region`` is ``None`` when the source has NULL (``""`` after
    raw parsing) — the map_sql parser normalizes that to ``None`` or keeps a
    string; this model accepts both.
    """

    village_id: int
    x: int
    y: int
    tribe: int
    name: str
    player_id: int
    player_name: str
    alliance_id: int
    alliance_tag: str
    population: int
    region: str | None
    is_capital: bool
    is_city: bool
    is_harbor: bool
    victory_points: int

class VillageHistoryPoint(BaseModel):
    """One stored observation of a village (the village explorer's history).

    Not part of ``VillageRow`` on purpose: that stays the single-row contract
    of one map.sql snapshot line; this model adds the snapshot date that
    makes a point an observation.
    """

    snapshot_date: str
    name: str
    x: int
    y: int
    player_name: str
    alliance_tag: str
    population: int


class SnapshotDates(BaseModel):
    """Latest and previous snapshot dates (ISO ``YYYY-MM-DD``), if any."""

    latest: str | None
    previous: str | None


class DeltaSummary(BaseModel):
    """Headline numbers for our alliance(s) plus day-over-day deltas.

    Deltas are ``None`` when there is no previous snapshot (first day); the
    embed builder renders those as "—".
    """

    villages: int
    population: int
    players: int
    vp: int
    villages_delta: int | None
    population_delta: int | None
    players_delta: int | None
    vp_delta: int | None


class VillageEvent(BaseModel):
    """A village that appeared, was conquered, or disappeared between snapshots.

    ``gained``: only in current snapshot. ``lost_conquered``: was ours, now has
    a new owner (``new_owner_tag``/``new_owner_player``). ``lost_deleted``: was
    ours, gone from the map (no new owner). ``region`` is the village's region
    from the snapshot where it still exists; ``None`` only when the source
    region was NULL. For ``gained`` events ``new_owner_player`` is the founder
    (the village appeared this day, so its current owner founded it).
    """

    village_id: int
    village_name: str
    x: int
    y: int
    event: Literal["gained", "lost_conquered", "lost_deleted"]
    new_owner_tag: str | None
    new_owner_player: str | None
    old_player: str | None
    region: str | None = None


class ConquestEvent(BaseModel):
    """A village that switched from one tracked alliance to another tracked alliance.

    Produced by ``metrics.conquests_between``: both the old and the new
    alliance must be in the tracked universe and differ. ``region`` and
    ``population`` come from the current snapshot (the village still exists).
    """

    village_id: int
    village_name: str
    x: int
    y: int
    from_tag: str
    from_player: str
    to_tag: str
    to_player: str
    region: str | None = None
    population: int


class DeletedVillageEvent(BaseModel):
    """A tracked alliance's village that disappeared from the map between snapshots.

    Produced by ``metrics.conquests_between``. ``region`` and ``population``
    come from the previous snapshot (the village is gone in the current one).
    """

    village_id: int
    village_name: str
    x: int
    y: int
    from_tag: str
    from_player: str
    region: str | None = None
    population: int


class PlayerStat(BaseModel):
    """One player's standing in a top ranking (population/growth/new_villages/vp).

    ``growth`` is ``None`` when there is no previous snapshot; ``gains`` is
    the strict-gained village count (village absent from prev with ANY
    owner), ``None`` when not provided/unknown. ``vp`` is the summed
    ``victory_points`` over the player's current villages (0 for a departed
    player).
    """

    player_id: int
    player_name: str
    population: int
    villages: int
    growth: int | None
    vp: int
    gains: int | None = None

class RegionStat(BaseModel):
    """Per-region numbers for our alliance(s) plus day-over-day deltas.

    ``share`` is our_pop / region_total_pop as a fraction (0.0-1.0); ``delta``
    is our_pop minus the previous day's, ``None`` when no previous snapshot.
    ``our_vp`` is the sum of our villages' victory_points in the current
    snapshot and ``vp_delta`` is ``our_vp`` minus the previous day's, ``None``
    when no previous snapshot (a region absent from prev yields curr − 0).
    ``share_delta`` is our share minus the previous day's share as a fraction
    (e.g. 0.021), following ``delta``'s semantics — ``None`` only when there
    is no previous snapshot; a region absent from prev yields curr share − 0.
    """

    region: str
    our_villages: int
    our_pop: int
    region_total_pop: int
    share: float
    delta: int | None
    our_vp: int = 0
    vp_delta: int | None = None
    share_delta: float | None = None


class RegionDay(BaseModel):
    """One region's per-day aggregate for the analysis dashboard.

    ``our_pop`` is the population of our alliance(s) in the region that day;
    ``total_pop`` is the population of ALL villages in the region. Produced
    by ``store.region_days`` (GROUP BY snapshot_date, region).
    """

    date: str
    region: str
    our_pop: int
    total_pop: int


class AllianceDay(BaseModel):
    """One alliance's per-day aggregate for the analysis dashboard.

    ``alliance_tag`` is the lexicographically greatest tag of that
    alliance_id that day (map.sql is tag-consistent per alliance_id within a
    snapshot, so this is the tag). Produced by ``store.alliance_days``.
    """

    date: str
    alliance_id: int
    alliance_tag: str
    villages: int
    population: int
    vp: int


class SummaryDay(BaseModel):
    """One day's headline aggregates for our alliance(s) (analysis dashboard).

    Produced by ``store.summary_days``; ``analysis.summary_history`` derives
    the day-over-day deltas from a run of these.
    """

    date: str
    villages: int
    population: int
    players: int
    vp: int


class AllianceStat(BaseModel):
    """One tracked alliance's row in the report's Standings comparison.

    ``tag`` is the configured tag the row was resolved from (a tag matching
    several alliances unions them, per the metrics resolution semantics).
    Deltas are ``None`` when there is no previous snapshot (first day); a
    tracked alliance absent from the previous snapshot yields curr − 0.
    """

    tag: str
    villages: int
    population: int
    players: int
    vp: int
    villages_delta: int | None
    population_delta: int | None
    players_delta: int | None
    vp_delta: int | None


class ReportData(BaseModel):
    """Everything the report embeds need — one block per embed field.

    ``new_villages``/``lost_villages`` are split lists so the embed builder
    renders the two "New Villages"/"Lost Villages" sections without
    filtering. ``regions`` feeds both the Regions embed and the KPI grid.
    """

    snapshot_date: str | None
    server: str
    alliance_tags: list[str]
    summary: DeltaSummary
    standings: list[AllianceStat] = []
    new_villages: list[VillageEvent]
    lost_villages: list[VillageEvent]
    regions: list[RegionStat]
