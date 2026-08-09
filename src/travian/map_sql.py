"""Fetch and parse a Travian ``map.sql`` snapshot.

``fetch_map_sql`` downloads the snapshot text (httpx, 60s timeout, up to 3
retries with 2/5/10s backoff) and ``parse_map_sql`` turns it into
:class:`travian.models.VillageRow` objects, one per ``INSERT INTO `x_world```
line, deduplicated by ``village_id`` (last occurrence wins).

Line shape (verified live against cw.x2 on 2026-08-08/09)::

    INSERT INTO `x_world` VALUES (id,x,y,tribe,village_id,'name',player_id,
    'player_name',alliance_id,'alliance_tag',population,'region',
    capital,city,harbor,vp);

The leading ``id`` is dropped — ``village_id`` is the identity (see the
``villages`` DDL in the plan / models docstring).

Conversion rules:

* strings: SQL doubled quotes (``''``) unescaped, then ``html.unescape``;
  unicode passes through untouched.
* ``TRUE``/``FALSE`` -> bool. ``NULL`` in a boolean field -> ``False``:
  cw.x2 emits ``NULL`` for ``is_city``/``is_harbor`` on every line and the
  DDL declares those columns ``INTEGER 0/1`` (non-nullable), so ``NULL``
  means "no" — a pydantic ``bool`` cannot hold ``None``.
* ``NULL`` elsewhere -> ``None`` (``region`` is the real-world case; ``''``
  region also -> ``None`` per the plan). ``None`` in an int field fails
  pydantic validation -> warning + skip (defensive; should never happen).
* numeric fields -> int.

Any line that fails to parse (wrong field count, unbalanced quotes, non-int
where an int is expected, pydantic validation) is logged with a ``warning``
and skipped — one bad line never crashes the snapshot. Lines not starting
with ``INSERT INTO `x_world``` are ignored silently.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Iterator

import httpx
from pydantic import ValidationError

from travian.models import VillageRow

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 60.0
_RETRY_BACKOFFS = (2.0, 5.0, 10.0)
_EXPECTED_FIELDS = 16
_LINE_RE = re.compile(r"INSERT INTO `x_world` VALUES \((.*)\);\s*\Z")

# 15 values after the leading `id` is dropped; order matches VillageRow fields.
_FIELD_NAMES = (
    "x",
    "y",
    "tribe",
    "village_id",
    "name",
    "player_id",
    "player_name",
    "alliance_id",
    "alliance_tag",
    "population",
    "region",
    "is_capital",
    "is_city",
    "is_harbor",
    "victory_points",
)
_BOOL_FIELDS = frozenset({"is_capital", "is_city", "is_harbor"})


class MapSqlFetchError(RuntimeError):
    """Raised when a map.sql download fails after all retries."""


def fetch_map_sql(url: str, *, timeout: float = _TIMEOUT_SECONDS) -> str:
    """Download ``url`` with 3 retries (backoff 2/5/10s) on httpx errors.

    Raises :class:`MapSqlFetchError` when all 4 attempts fail.
    """
    last_error: Exception | None = None
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(len(_RETRY_BACKOFFS) + 1):
            try:
                response = client.get(url)
                _ = response.raise_for_status()
                return response.text
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < len(_RETRY_BACKOFFS):
                    time.sleep(_RETRY_BACKOFFS[attempt])
    raise MapSqlFetchError(f"failed to fetch {url} after 4 attempts: {last_error}") from last_error


def parse_map_sql(text: str) -> list[VillageRow]:
    """Parse map.sql text into deduplicated :class:`VillageRow` list.

    Only ``INSERT INTO `x_world``` lines are processed. Malformed lines are
    warned about and skipped; duplicates of the same ``village_id`` collapse
    to the last occurrence.
    """
    rows: dict[int, VillageRow] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("INSERT INTO `x_world`"):
            continue
        row = _parse_line(line, line_number)
        if row is not None:
            rows[row.village_id] = row
    return list(rows.values())


def _parse_line(line: str, line_number: int) -> VillageRow | None:
    match = _LINE_RE.fullmatch(line)
    if match is None:
        _warn(line_number, "unrecognized line structure", line)
        return None
    try:
        tokens = _split_sql_values(match.group(1))
    except ValueError as exc:
        _warn(line_number, str(exc), line)
        return None
    if len(tokens) != _EXPECTED_FIELDS:
        _warn(
            line_number,
            f"expected {_EXPECTED_FIELDS} values, got {len(tokens)}",
            line,
        )
        return None
    try:
        values = _convert(tokens)
    except ValueError as exc:
        _warn(line_number, str(exc), line)
        return None
    try:
        return VillageRow.model_validate(dict(zip(_FIELD_NAMES, values, strict=True)))
    except ValidationError as exc:
        _warn(line_number, f"pydantic validation failed: {exc}", line)
        return None


def _split_sql_values(body: str) -> list[str]:
    """Split ``(v1,v2,...)`` body on top-level commas, honoring quotes.

    Inside a quoted string ``''`` is an escaped quote and ``'`` alone closes
    the string. Raises ``ValueError`` on an unterminated string.
    """
    tokens: list[str] = []
    current: list[str] = []
    in_string = False
    i = 0
    while i < len(body):
        ch = body[i]
        if in_string:
            if ch == "'":
                if i + 1 < len(body) and body[i + 1] == "'":
                    current.append("''")
                    i += 2
                    continue
                in_string = False
                current.append(ch)
            else:
                current.append(ch)
        elif ch == "'":
            in_string = True
            current.append(ch)
        elif ch == ",":
            tokens.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if in_string:
        raise ValueError("unterminated quoted string")
    tokens.append("".join(current))
    return tokens


def _convert(tokens: list[str]) -> Iterator[object]:
    """Yield typed values for the 15 kept fields (leading id dropped)."""
    for field, raw in zip(_FIELD_NAMES, tokens[1:], strict=True):
        yield _convert_one(field, raw.strip())


def _convert_one(field: str, raw: str) -> object:
    if raw == "NULL":
        if field in _BOOL_FIELDS:
            return False  # DDL: is_capital/is_city/is_harbor are 0/1, never NULL
        return None
    if raw == "TRUE":
        return True
    if raw == "FALSE":
        return False
    if raw.startswith("'"):
        if not raw.endswith("'") or len(raw) < 2:
            raise ValueError(f"unbalanced quotes in {raw!r}")
        value = html.unescape(raw[1:-1].replace("''", "'"))
        if field == "region" and value == "":
            return None
        return value
    if field in _BOOL_FIELDS:
        raise ValueError(f"expected TRUE/FALSE/NULL, got {raw!r}")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"expected quoted string, NULL or integer for {field}, got {raw!r}") from exc


def _warn(line_number: int, reason: str, line: str) -> None:
    snippet = line.strip()[:80]
    logger.warning("map_sql line %d (%s): %s", line_number, reason, snippet)
