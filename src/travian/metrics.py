"""Day-over-day metrics: deltas, village events, and alliance tag resolution.

Alliance identity is tracked by ``alliance_id`` throughout — configured tags
are resolved to ids ONCE against the CURRENT snapshot, so a tag rename between
days produces no spurious gained/lost events. Documented decisions:

- **Tag matching**: exact, case-sensitive match on ``alliance_tag`` after
  ``strip()``; tags are deduped preserving first-occurrence order; tags that
  become empty after ``strip()`` are dropped silently. Tag-rename resilience
  comes from tracking ``alliance_id`` across days, not from fuzzy matching.
- **Multiple matches**: a tag matching several alliances (id reuse) resolves
  to the union of those ids.
- **Unresolved tags**: per-tag ``logging.warning``; with ``conn`` given, one
  ``append_log(conn, 'config', 'warning', ...)`` listing all of them. The
  "no alliance configured" warning for an EMPTY ``ALLIANCE_TAGS`` lives in the
  bot (run_report), not here — this module never logs it.
- **``gained``** strictly: village_id in curr-ours AND absent from prev with
  ANY owner. A village conquered from another alliance between snapshots is
  therefore NOT an event.
- **``lost_conquered``**: was ours in prev, still exists in curr, and curr
  owner's alliance is NOT in ``alliance_ids``. A same-alliance player change is
  NOT an event (a v1.1 roster could report it).
- **``lost_deleted``**: was ours in prev, absent from curr entirely.
- **``prev_rows=None``** = no previous snapshot: deltas are ``None`` and there
  are no events (nothing to compare against). ``prev_rows=[]`` = a snapshot
  exists but has no such alliance (alliance founded yesterday): deltas are
  curr - 0 and every curr-ours village counts as gained.
- Events are sorted by village_id for stable embeds.

Task 7 (regions, top players) extends this module:

- **Regions** (``region_stats``): regions of interest = where OUR alliance has
  villages in curr OR prev (a region we left stays listed with zeros and a
  negative delta — losses stay visible). ``region`` ``None`` (map NULL) groups
  as ``""``. ``share`` guards division by zero → 0.0. ``delta`` is ``None``
  only when ``prev_rows`` is ``None``; a region absent from prev yields
  curr − 0 (same semantics as above). Sorted by ``our_pop`` desc, region name
  asc as tiebreak.
- **Top players** (``top_players``): three separate rankings capped at ``n``.
  Player universe = curr-ours ∪ prev-ours (departed players still rank with
  negative growth). ``growth`` is ``None`` only when ``prev_rows`` is ``None``;
  ``prev_rows=[]`` means growth = curr − 0. With no previous snapshot all
  rankings degenerate to population order. ``new_villages`` reuses the STRICT
  gained definition. Ties break by ``player_id``.
- **Top-5 alliances per region** (``region_alliance_totals``): v2 input
  computed here per plan; ``RegionStat`` (models contract) carries no such
  field, so the v1 embed ignores it and v2 rebuilds from these rows.

This module is the plan-mandated single home for day-over-day metrics (deltas,
events, regions, top players); T6/T7 are append-only by plan, so the 250-line
budget is exceeded by design — SIZE_OK, splitting deferred past T8.
"""

import logging
import sqlite3

from travian.models import DeltaSummary, PlayerStat, RegionStat, VillageEvent, VillageRow
from travian.store import append_log

logger = logging.getLogger(__name__)


def resolve_alliance_ids(
    curr_rows: list[VillageRow],
    tags: list[str],
    conn: sqlite3.Connection | None = None,
) -> tuple[set[int], list[str]]:
    """Resolve configured alliance tags to ``alliance_id``s from the CURRENT snapshot.

    Tags are normalized with ``strip()`` + dedupe (first occurrence wins) and
    matched case-sensitively against ``alliance_tag``. Returns ``(resolved_ids,
    unresolved_tags)`` — the union of ids of every matching tag. Never raises;
    an empty ``tags`` list or a snapshot without matches yields ``(set(), [...])``.

    Every unresolved tag is reported via ``logging.warning``; when ``conn`` is
    provided (optional, so pure tests need no sqlite) one ``append_log`` row is
    written with the full list.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = raw.strip()
        if tag and tag not in seen:
            seen.add(tag)
            normalized.append(tag)

    tag_to_ids: dict[str, set[int]] = {}
    for row in curr_rows:
        ids = tag_to_ids.get(row.alliance_tag)
        if ids is None:
            tag_to_ids[row.alliance_tag] = {row.alliance_id}
        else:
            ids.add(row.alliance_id)

    resolved: set[int] = set()
    unresolved: list[str] = []
    for tag in normalized:
        ids = tag_to_ids.get(tag)
        if ids is None:
            unresolved.append(tag)
            logger.warning("alliance tag %r from ALLIANCE_TAGS not found in current snapshot", tag)
        else:
            resolved.update(ids)
    if unresolved and conn is not None:
        append_log(conn, "config", "warning", f"unresolved alliance tags: {unresolved}")
    return resolved, unresolved


def _aggregate(rows: list[VillageRow], alliance_ids: set[int]) -> tuple[int, int, int, int]:
    """(villages, population, players, victory_points) restricted to ``alliance_ids``."""
    villages = 0
    population = 0
    vp = 0
    players: set[int] = set()
    for row in rows:
        if row.alliance_id in alliance_ids:
            villages += 1
            population += row.population
            vp += row.victory_points
            players.add(row.player_id)
    return villages, population, len(players), vp


def compute_deltas(
    prev_rows: list[VillageRow] | None,
    curr_rows: list[VillageRow],
    alliance_ids: set[int],
) -> DeltaSummary:
    """Headline aggregates for ``alliance_ids`` plus day-over-day deltas.

    Deltas are ``None`` only when ``prev_rows`` is ``None`` (no previous
    snapshot). A non-None prev snapshot that merely lacks the alliance
    (founded yesterday) yields deltas of curr - 0 — correct behavior.
    """
    curr = _aggregate(curr_rows, alliance_ids)
    if prev_rows is None:
        return DeltaSummary(
            villages=curr[0],
            population=curr[1],
            players=curr[2],
            vp=curr[3],
            villages_delta=None,
            population_delta=None,
            players_delta=None,
            vp_delta=None,
        )
    prev = _aggregate(prev_rows, alliance_ids)
    return DeltaSummary(
        villages=curr[0],
        population=curr[1],
        players=curr[2],
        vp=curr[3],
        villages_delta=curr[0] - prev[0],
        population_delta=curr[1] - prev[1],
        players_delta=curr[2] - prev[2],
        vp_delta=curr[3] - prev[3],
    )


def village_events(
    prev_rows: list[VillageRow] | None,
    curr_rows: list[VillageRow],
    alliance_ids: set[int],
) -> tuple[list[VillageEvent], list[VillageEvent]]:
    """(gained, lost) village events between two snapshots, sorted by village_id.

    ``prev_rows=None`` (no previous snapshot) yields no events at all. Names
    and coordinates come from the snapshot where the village still exists.
    """
    if prev_rows is None:
        return [], []
    prev_all = {row.village_id: row for row in prev_rows}
    prev_ours = {row.village_id: row for row in prev_rows if row.alliance_id in alliance_ids}
    curr_ours = {row.village_id: row for row in curr_rows if row.alliance_id in alliance_ids}

    gained: list[VillageEvent] = [
        VillageEvent(
            village_id=row.village_id,
            village_name=row.name,
            x=row.x,
            y=row.y,
            event="gained",
            new_owner_tag=None,
            new_owner_player=None,
            old_player=None,
        )
        for row in curr_ours.values()
        if row.village_id not in prev_all
    ]

    curr_all = {row.village_id: row for row in curr_rows}
    lost: list[VillageEvent] = []
    for village_id, prev_row in prev_ours.items():
        curr_row = curr_all.get(village_id)
        if curr_row is None:
            lost.append(
                VillageEvent(
                    village_id=village_id,
                    village_name=prev_row.name,
                    x=prev_row.x,
                    y=prev_row.y,
                    event="lost_deleted",
                    new_owner_tag=None,
                    new_owner_player=None,
                    old_player=prev_row.player_name,
                )
            )
        elif curr_row.alliance_id not in alliance_ids:
            lost.append(
                VillageEvent(
                    village_id=village_id,
                    village_name=curr_row.name,
                    x=curr_row.x,
                    y=curr_row.y,
                    event="lost_conquered",
                    new_owner_tag=curr_row.alliance_tag,
                    new_owner_player=curr_row.player_name,
                    old_player=prev_row.player_name,
                )
            )

    gained.sort(key=lambda event: event.village_id)
    lost.sort(key=lambda event: event.village_id)
    return gained, lost


def region_alliance_totals(curr_rows: list[VillageRow]) -> dict[str, list[tuple[str, int]]]:
    """Top-5 alliances by population per region — v2 data, computed here per plan.

    ``RegionStat`` (models contract) has no field for this, so the v1 embed
    ignores it and the v2 embed rebuilds from these rows. Returns ``{region:
    [(alliance_tag, total_population), ...]}`` — population desc, tag asc
    tiebreak, capped at 5. Regions with no villages in ``curr_rows`` are
    absent; ``None`` regions group as ``""``.
    """
    totals: dict[str, dict[str, int]] = {}
    for row in curr_rows:
        region = row.region or ""
        alliance_totals = totals.setdefault(region, {})
        alliance_totals[row.alliance_tag] = alliance_totals.get(row.alliance_tag, 0) + row.population
    return {
        region: sorted(by_tag.items(), key=lambda item: (-item[1], item[0]))[:5]
        for region, by_tag in totals.items()
    }


def region_stats(
    prev_rows: list[VillageRow] | None,
    curr_rows: list[VillageRow],
    alliance_ids: set[int],
) -> list[RegionStat]:
    """Per-region numbers for ``alliance_ids``, sorted by our_pop desc.

    Regions of interest = any region where WE have a village in ``curr_rows``
    OR in ``prev_rows`` — a region we left is still listed (our_villages=0,
    our_pop=0, share=0, delta negative) so losses stay visible. ``None``
    region values group as ``""``. ``share`` is our_pop / region_total_pop
    (population of ALL villages in the region in curr) with a division-by-zero
    guard → 0.0. ``delta`` is our_pop minus the previous day's, ``None`` only
    when ``prev_rows`` is ``None``; a region absent from prev yields curr − 0
    (same semantics as ``compute_deltas``). Tiebreak: region name ascending.
    """
    curr_ours: dict[str, list[VillageRow]] = {}
    region_total: dict[str, int] = {}
    for row in curr_rows:
        region = row.region or ""
        if row.alliance_id in alliance_ids:
            curr_ours.setdefault(region, []).append(row)
        region_total[region] = region_total.get(region, 0) + row.population

    prev_ours: dict[str, list[VillageRow]] = {}
    if prev_rows is not None:
        for row in prev_rows:
            if row.alliance_id in alliance_ids:
                prev_ours.setdefault(row.region or "", []).append(row)

    stats: list[RegionStat] = []
    for region in curr_ours.keys() | prev_ours.keys():
        our_villages = len(curr_ours.get(region, []))
        our_pop = sum(row.population for row in curr_ours.get(region, []))
        region_total_pop = region_total.get(region, 0)
        share = our_pop / region_total_pop if region_total_pop else 0.0
        if prev_rows is None:
            delta: int | None = None
        else:
            delta = our_pop - sum(row.population for row in prev_ours.get(region, []))
        stats.append(
            RegionStat(
                region=region,
                our_villages=our_villages,
                our_pop=our_pop,
                region_total_pop=region_total_pop,
                share=share,
                delta=delta,
            )
        )
    stats.sort(key=lambda s: (-s.our_pop, s.region))
    return stats


def top_players(
    curr_rows: list[VillageRow],
    prev_rows: list[VillageRow] | None,
    alliance_ids: set[int],
    n: int = 5,
) -> dict[str, list[PlayerStat]]:
    """Three separate top-player rankings for ``alliance_ids``, each capped at n.

    Keys: ``population`` (curr population desc), ``growth`` (curr − prev pop
    desc; negative growth is a real ranking position), ``new_villages``
    (STRICT gained count desc — village absent from prev with ANY owner, same
    definition as ``village_events``).

    The player universe is curr-ours ∪ prev-ours: a player who left the
    alliance still ranks (population 0, negative growth). ``growth`` is
    ``None`` only when ``prev_rows`` is ``None``; ``prev_rows=[]`` (snapshot
    exists, alliance absent) means growth = curr − 0 and every curr-ours
    village counts as gained. With no previous snapshot all three rankings
    degenerate to population-desc order (growth ``None``, zero gains). Ties
    break by ``player_id`` ascending; order is fully deterministic.
    """
    curr_ours = [row for row in curr_rows if row.alliance_id in alliance_ids]
    prev_ours = [row for row in prev_rows if row.alliance_id in alliance_ids] if prev_rows is not None else []

    population: dict[int, int] = {}
    villages: dict[int, int] = {}
    names: dict[int, str] = {}
    for row in curr_ours:
        names[row.player_id] = row.player_name
        population[row.player_id] = population.get(row.player_id, 0) + row.population
        villages[row.player_id] = villages.get(row.player_id, 0) + 1

    prev_pop: dict[int, int] = {}
    for row in prev_ours:
        if row.player_id not in names:
            names[row.player_id] = row.player_name
        prev_pop[row.player_id] = prev_pop.get(row.player_id, 0) + row.population

    gains: dict[int, int] = {player: 0 for player in names}
    if prev_rows is not None:
        prev_any_owner = {row.village_id for row in prev_rows}
        for row in curr_ours:
            if row.village_id not in prev_any_owner:
                gains[row.player_id] = gains[row.player_id] + 1

    stats = [
        PlayerStat(
            player_id=player,
            player_name=names[player],
            population=population.get(player, 0),
            villages=villages.get(player, 0),
            growth=None if prev_rows is None else population.get(player, 0) - prev_pop.get(player, 0),
        )
        for player in names
    ]

    by_pop = sorted(stats, key=lambda s: (-s.population, s.player_id))[:n]
    if prev_rows is None:
        # No previous snapshot: growth is None and gains are 0 for everyone,
        # so the growth/new_villages rankings degenerate to population order.
        by_growth = by_pop
        by_gains = by_pop
    else:
        by_growth = sorted(stats, key=lambda s: (-(s.growth or 0), s.player_id))[:n]
        by_gains = sorted(stats, key=lambda s: (-gains[s.player_id], s.player_id))[:n]
    return {"population": by_pop, "growth": by_growth, "new_villages": by_gains}
