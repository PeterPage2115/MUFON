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
    ours, gone from the map (no new owner).
    """

    village_id: int
    village_name: str
    x: int
    y: int
    event: Literal["gained", "lost_conquered", "lost_deleted"]
    new_owner_tag: str | None
    new_owner_player: str | None
    old_player: str | None


class PlayerStat(BaseModel):
    """One player's standing in a top ranking (population/growth/new_villages).

    ``growth`` is ``None`` when there is no previous snapshot; ``gains`` is
    the strict-gained village count (village absent from prev with ANY
    owner), ``None`` when not provided/unknown.
    """

    player_id: int
    player_name: str
    population: int
    villages: int
    growth: int | None
    gains: int | None = None


class RegionStat(BaseModel):
    """Per-region numbers for our alliance(s) plus day-over-day population delta.

    ``share`` is our_pop / region_total_pop as a fraction (0.0-1.0); ``delta``
    is our_pop minus the previous day's, ``None`` when no previous snapshot.
    """

    region: str
    our_villages: int
    our_pop: int
    region_total_pop: int
    share: float
    delta: int | None


class ReportData(BaseModel):
    """Everything the daily report embed needs — one block per embed field.

    ``new_villages``/``lost_villages`` are split lists so the embed builder
    (task 8) renders the two "New Villages"/"Lost Villages" fields without
    filtering. ``top_players`` keys are exactly ``population`` | ``growth`` |
    ``new_villages`` (three separate embed fields, each capped at 5).
    """

    snapshot_date: str | None
    server: str
    alliance_tags: list[str]
    summary: DeltaSummary
    new_villages: list[VillageEvent]
    lost_villages: list[VillageEvent]
    top_players: dict[str, list[PlayerStat]]
    regions: list[RegionStat]
    vp_total: int
    vp_delta: int | None
