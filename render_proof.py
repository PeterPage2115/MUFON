"""Render proof for the report trim (plan verification step 2).

Runs against a sqlite DB (live /data/travian.db inside the container, or a
local copy) with the NEW sources on the import path. Asserts:

1. Daily build (sections=DAILY_SECTIONS, region_limit=8): exactly 3 embeds
   (Daily Report / Regions / Standings), ≤ 9 table rows in the fence (8
   shown + more-line), more-line count == regions − shown, legend present,
   "Biggest moves:" line with two Δ% tokens, no 🏗️/🏆 titles, limits ok.
2. sections={"villages"}: 1 villages embed with region/founder/conqueror
   line formats.
3. sections={"regions"}: the FULL table (every region row, 58-char lines,
   "Δ %" header).
"""

import os
import re
import sys

sys.path.insert(0, "/proof/src")  # the NEW sources (docker cp'd); no-op locally

from travian import strings
from travian.bot.main import _build_report_data, _resolved_tags, load_merged_config
from travian.bot.report_embed import DAILY_SECTIONS, build_report_embed
from travian.metrics import resolve_alliance_ids
from travian.store import connect, list_dates, load_latest, load_villages

DB = os.environ.get("PROOF_DB", "/data/travian.db")


def total_len(embed) -> int:
    return len(embed.description or "") + len(embed.footer.text or "") + sum(
        len(f.name or "") + len(f.value or "") for f in embed.fields
    )


def load():
    conn = connect(DB)
    try:
        cfg = load_merged_config(conn, os.environ)
        latest = load_latest(conn)
        assert latest is not None, "no snapshot in DB"
        dates = list_dates(conn)
        prev = max((d for d in dates if d < latest.snapshot_date), default=None)
        curr = load_villages(conn, latest.snapshot_date)
        prev_rows = load_villages(conn, prev) if prev is not None else None
        resolved, unresolved = resolve_alliance_ids(curr, cfg.alliance_tags, conn)
        assert resolved, "no resolved alliance"
        data = _build_report_data(cfg, latest.snapshot_date, curr, prev_rows, resolved)
        return data, _resolved_tags(cfg.alliance_tags, unresolved), latest.snapshot_date, cfg
    finally:
        conn.close()


def fenced_lines(description: str) -> list[str]:
    return description.split("```")[1].strip("\n").split("\n")


def main() -> int:
    data, tags, date, cfg = load()
    active = sum(1 for r in data.regions if r.region_total_pop >= 4000)
    print(f"snapshot {date}; tags {tags}")
    print(f"regions: {len(data.regions)} (active {active}); events: new {len(data.new_villages)} lost {len(data.lost_villages)}")

    # --- 1) daily build -------------------------------------------------------
    daily = build_report_embed(
        data, tags, date, color=cfg.report_embed_color, sections=DAILY_SECTIONS, region_limit=8
    )
    assert [e.title for e in daily] == [
        strings.EMBED_TITLE_REPORT,
        strings.EMBED_TITLE_REGIONS,
        strings.EMBED_TITLE_STANDINGS,
    ], [e.title for e in daily]
    assert all("🏗️" not in (e.title or "") and "🏆" not in (e.title or "") for e in daily)
    for e in daily:
        assert len(e.description or "") <= 4096, "description over 4096"
        assert total_len(e) <= 6000, "embed over 6000"
    print("daily: 3 embeds, titles ok, no villages/top-players titles, limits ok")

    lines = fenced_lines(daily[1].description or "")
    table_rows = [l for l in lines if l not in (strings.REGION_TABLE_HEADER, strings.REGION_TABLE_DIVIDER)]
    shown = min(8, active)
    assert len(table_rows) <= 9, f"{len(table_rows)} table rows"
    more_lines = [l for l in lines if l.startswith("…and ")]
    assert more_lines, "no more-line inside the fence"
    n = int(re.match(r"…and (\d+) more", more_lines[0]).group(1))
    assert n == len(data.regions) - shown, (n, len(data.regions) - shown)
    print(f"daily regions fence: {len(table_rows)} table rows (8 shown + more-line '{more_lines[0]}')")
    assert strings.REGION_LEGEND in daily[1].description
    movers = re.search(r"Biggest moves: ([+−±]\d\.\d% [^·]+) · ([+−±]\d\.\d% [^·]+)", daily[1].description)
    assert movers, "no Biggest moves line"
    print(f"movers line: {movers.group(0)}")

    # --- 2) villages-only build -------------------------------------------------
    villages = build_report_embed(data, tags, date, sections={"villages"})
    print(f"villages embeds: {len(villages)}")
    if villages:
        assert len(villages) == 1 and villages[0].title == strings.EMBED_TITLE_VILLAGES
        vdesc = villages[0].description or ""
        if data.new_villages:
            assert re.search(r"\(-?\d+\|-?\d+\) — .+ — by .+", vdesc), vdesc
        if data.lost_villages:
            assert "conquered by" in vdesc or "deleted" in vdesc, vdesc
        print("villages: 1 embed, region/founder + conqueror lines ok")

    # --- 3) regions-only build (full table) -------------------------------------
    regions = build_report_embed(data, tags, date, sections={"regions"})
    assert len(regions) == 1 and regions[0].title == strings.EMBED_TITLE_REGIONS
    rlines = fenced_lines(regions[0].description or "")
    rows = [l for l in rlines if l not in (strings.REGION_TABLE_HEADER, strings.REGION_TABLE_DIVIDER)]
    assert len(rows) == len(data.regions), (len(rows), len(data.regions))
    assert all(len(l) == 58 for l in rows), [len(l) for l in rows[:5]]
    assert "Δ %" in rlines[0]
    print(f"regions: full table ({len(rows)} rows, all 58 chars, 'Δ %' header)")

    print("ALL PROOF CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
