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

Task 7 (regions, top players) extends this module.
"""

import logging
import sqlite3

from travian.models import DeltaSummary, VillageEvent, VillageRow
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
