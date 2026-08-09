"""Tests for the map.sql fetch + parse pipeline (task 3).

Covers: real-server fixture parsing (entities, unicode, NULL/'' region,
TRUE/FALSE booleans, VP>0, '' quoting, comma inside strings), malformed-line
skip with warning, field-count warning, pydantic ValidationError skip, dedupe
last-wins, non-INSERT line ignoring, and fetch_map_sql retry/backoff via a
mocked httpx.Client.
"""

import logging
from pathlib import Path

import httpx
import pytest

from travian.map_sql import MapSqlFetchError, fetch_map_sql, parse_map_sql

FIXTURE = Path(__file__).parent / "fixtures" / "map_sql_sample.txt"


def read_fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# parse_map_sql — real fixture
# --------------------------------------------------------------------------


def test_parse_fixture_returns_expected_rows():
    rows = parse_map_sql(read_fixture())

    # 9 real + 4 valid synthetic rows; the malformed line is skipped and the
    # duplicate village_id 1 collapses into one row (last occurrence wins).
    assert len(rows) == 13
    assert len({row.village_id for row in rows}) == 13


def test_parse_king_s_landing_row():
    row = next(r for r in parse_map_sql(read_fixture()) if r.village_id == 20989)

    assert row.name == "King's Landing"
    assert row.x == 164
    assert row.y == 185
    assert row.tribe == 8
    assert row.player_id == 601
    assert row.player_name == "Kingslayer"
    assert row.alliance_id == 19
    assert row.alliance_tag == "NULL1"
    assert row.population == 500
    assert row.region == "Hyperborea"
    assert row.is_capital is True
    assert row.is_city is False
    assert row.is_harbor is False
    assert row.victory_points == 331


def test_parse_unicode_names_pass_through():
    rows = {row.village_id: row for row in parse_map_sql(read_fixture())}

    # Polish unicode
    assert rows[22444].name == "06"
    assert rows[22444].player_name == "Poruś"
    assert rows[22444].victory_points == 93
    assert rows[15109].name == "01.PłockiePolis"
    # Arabic unicode
    assert rows[23305].name == "قرية جديدة"
    # U+2019 curly apostrophe preserved as-is
    assert rows[6256].name == "MPTrav’s landsby (i)"


def test_parse_natars_row_empty_alliance_tag():
    row = parse_map_sql(read_fixture())[0]

    # village_id 1 comes from the synthetic duplicate line (last wins)
    assert row.village_id == 1
    assert row.name == "Natars Last"
    assert row.alliance_tag == "TAG"
    assert row.is_capital is False
    assert row.is_city is True
    assert row.is_harbor is True
    assert row.victory_points == 3


def test_parse_doubled_quote_inside_string():
    row = next(r for r in parse_map_sql(read_fixture()) if r.village_id == 900001)

    assert row.name == "O'Brien"
    assert row.is_harbor is True
    assert row.region == "Syntheticia"


def test_parse_null_and_empty_region_become_none():
    rows = {row.village_id: row for row in parse_map_sql(read_fixture())}

    # NULL region -> None
    assert rows[900002].region is None
    assert rows[900002].is_capital is True
    # '' region -> None
    assert rows[900003].region is None
    assert rows[900003].is_city is True
    assert rows[900003].victory_points == 1


def test_parse_comma_inside_quoted_string():
    row = next(r for r in parse_map_sql(read_fixture()) if r.village_id == 900004)

    assert row.name == "Village, 12"


def test_parse_parens_inside_name():
    row = next(r for r in parse_map_sql(read_fixture()) if r.village_id == 20602)

    assert row.name == ":)"
    assert row.population == 441


def test_parse_true_false_bool_fields_on_real_lines():
    rows = {row.village_id: row for row in parse_map_sql(read_fixture())}

    # Real cw.x2 lines: is_capital TRUE/FALSE, is_city/is_harbor NULL -> False
    assert rows[22992].is_capital is False
    assert rows[23174].is_capital is False
    assert rows[23174].is_city is False
    assert rows[23174].is_harbor is False


# --------------------------------------------------------------------------
# parse_map_sql — malformed lines, warnings, dedupe, non-INSERT lines
# --------------------------------------------------------------------------


def test_parse_malformed_line_warns_and_skips(caplog):
    text = read_fixture()
    with caplog.at_level(logging.WARNING):
        rows = parse_map_sql(text)

    # only the synthetic malformed line (6 values) is warned + skipped
    assert len(rows) == 13
    warning_texts = [r.message for r in caplog.records]
    assert len(warning_texts) == 1
    assert "16" in warning_texts[0]
    assert "INSERT INTO `x_world` VALUES (1,2,3,4,5)" in warning_texts[0]


def test_parse_pydantic_validation_error_warns_and_skips(caplog):
    # population = NULL -> None -> pydantic rejects non-optional int
    text = (
        "INSERT INTO `x_world` VALUES (500001,1,1,1,500001,'A',1,'B',0,'',NULL,'R',FALSE,FALSE,FALSE,0);\n"
        "INSERT INTO `x_world` VALUES (500002,2,2,1,500002,'B',1,'B',0,'',1,'R',FALSE,FALSE,FALSE,0);\n"
    )
    with caplog.at_level(logging.WARNING):
        rows = parse_map_sql(text)

    assert [row.village_id for row in rows] == [500002]
    assert len(caplog.records) == 1
    assert "population" in caplog.records[0].message
    assert "500001" in caplog.records[0].message


def test_parse_unterminated_string_warns_and_skips(caplog):
    text = (
        "INSERT INTO `x_world` VALUES (600001,1,1,1,600001,'broken,2,'B',0,'',1,'R',FALSE,FALSE,FALSE,0);\n"
        "INSERT INTO `x_world` VALUES (600002,3,3,1,600002,'ok',1,'B',0,'',1,'R',FALSE,FALSE,FALSE,0);\n"
    )
    with caplog.at_level(logging.WARNING):
        rows = parse_map_sql(text)

    assert [row.village_id for row in rows] == [600002]
    assert len(caplog.records) == 1


def test_parse_ignores_non_insert_lines(caplog):
    text = (
        "-- SQL comment line\n"
        "INSERT INTO `allies` VALUES (1,2);\n"
        "INSERT INTO `x_world` VALUES (700001,1,1,1,700001,'A',1,'B',0,'',1,'R',FALSE,FALSE,FALSE,0);\n"
        "\n"
        "INSERT INTO `x_world` VALUES (700002,2,2,1,700002,'B',1,'B',0,'',1,'R',FALSE,FALSE,FALSE,0);\n"
    )
    with caplog.at_level(logging.WARNING):
        rows = parse_map_sql(text)

    assert [row.village_id for row in rows] == [700001, 700002]
    assert caplog.records == []


def test_parse_dedupe_last_occurrence_wins():
    text = (
        "INSERT INTO `x_world` VALUES (1,10,10,1,77,'first',1,'B',0,'',1,'R',FALSE,FALSE,FALSE,0);\n"
        "INSERT INTO `x_world` VALUES (2,20,20,1,77,'second',2,'C',1,'T',2,'R',TRUE,FALSE,FALSE,7);\n"
        "INSERT INTO `x_world` VALUES (3,30,30,1,78,'other',3,'D',0,'',3,'R',FALSE,FALSE,FALSE,0);\n"
    )
    rows = parse_map_sql(text)

    assert len(rows) == 2
    second = next(r for r in rows if r.village_id == 77)
    assert second.name == "second"
    assert second.player_id == 2
    assert second.is_capital is True
    assert second.victory_points == 7


def test_parse_empty_text_returns_no_rows():
    assert parse_map_sql("") == []


# --------------------------------------------------------------------------
# fetch_map_sql — mocked httpx.Client
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def make_fake_client(get_impl):
    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def get(self, url):
            return get_impl(url)

    return FakeClient


def test_fetch_success(monkeypatch):
    monkeypatch.setattr("travian.map_sql.httpx.Client", make_fake_client(lambda url: FakeResponse("OK")))
    monkeypatch.setattr("travian.map_sql.time.sleep", lambda _: None)

    assert fetch_map_sql("http://example.test/map.sql") == "OK"


def test_fetch_retries_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("travian.map_sql.time.sleep", sleeps.append)
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
        return FakeResponse("OK")

    monkeypatch.setattr("travian.map_sql.httpx.Client", make_fake_client(flaky))

    assert fetch_map_sql("http://example.test/map.sql") == "OK"
    assert calls["n"] == 3
    assert sleeps == [2.0, 5.0]


def test_fetch_raises_after_three_retries(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("travian.map_sql.time.sleep", sleeps.append)
    calls = {"n": 0}

    def always_fail(url):
        calls["n"] += 1
        raise httpx.ConnectError("down forever")

    monkeypatch.setattr("travian.map_sql.httpx.Client", make_fake_client(always_fail))

    with pytest.raises(MapSqlFetchError, match="down forever"):
        fetch_map_sql("http://example.test/map.sql")

    assert calls["n"] == 4
    assert sleeps == [2.0, 5.0, 10.0]


def test_fetch_retries_on_http_status_error(monkeypatch):
    class StatusErrorResponse(FakeResponse):
        def raise_for_status(self) -> None:
            req = httpx.Request("GET", "http://example.test/map.sql")
            raise httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))

    calls = {"n": 0}
    monkeypatch.setattr("travian.map_sql.time.sleep", lambda _: None)

    def flaky(url):
        calls["n"] += 1
        if calls["n"] < 3:
            return StatusErrorResponse("err")
        return FakeResponse("OK")

    monkeypatch.setattr("travian.map_sql.httpx.Client", make_fake_client(flaky))

    assert fetch_map_sql("http://example.test/map.sql") == "OK"
    assert calls["n"] == 3
