"""C2 (2026-10-30): one commit preview reads each thing from Egeria once, and concurrent previews share builds.

On coco_pharma the preview took about 45 s alone and 110-120 s when the page had started six at once. Two causes
on RE's side are pinned here: the build read a leave-out's whole walk twice (once for the access check, once for
its form), and every click started a fresh build beside the ones still running. Never reaches Egeria: the
`FakeEgeria` of `test_catalogue_commit` stands behind the gateway."""
from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from tests.test_catalogue_commit import (  # noqa: F401 -- fixtures
    _catalogued, _curation_access_reads, _zones, choose, fake, registry, view, world,
)
from resource_explorer import catalogue_commit as cc
from resource_explorer.web.routes import catalogue_scope as route

pytestmark = pytest.mark.usefixtures("signed_in_caller")


def _reads(fake, since):
    return Counter(c for c in fake.calls[since:] if c[0] in cc.PreviewReads._READS)


def test_a_preview_reads_each_element_once_and_still_names_each_form(world, fake):
    _catalogued(world, fake, "sales", "archive", "cm,ma")
    choose(world, "sales", "leave_out")
    choose(world, "archive", "leave_out")
    since = len(fake.calls)
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    reads = _reads(fake, since)
    assert reads and max(reads.values()) == 1, {k: v for k, v in reads.items() if v > 1}
    # the walk is read for the access check AND for the form, from the same answers
    assert {r["schema"]: r["form"] for r in p["leave_out"]} == {"sales": "archive", "archive": "archive"}
    assert p["attach"] == ["cm,ma"]


def test_a_failed_read_is_not_kept_the_next_asker_reads_again():
    class Gw:
        n = 0

        def relationships(self, guid):
            self.n += 1
            if self.n == 1:
                raise cc.GatewayError("503")
            return ["r"]

    g = cc.PreviewReads(Gw())
    with pytest.raises(cc.GatewayError):
        g.relationships("x")
    assert g.relationships("x") == ["r"] and g.relationships("x") == ["r"] and g._gateway.n == 2


def test_concurrent_previews_share_builds_and_each_answer_started_after_its_request():
    async def scenario():
        started: list[int] = []
        gate = asyncio.Event()

        async def make():
            started.append(len(started) + 1)
            n = len(started)
            if n == 1:
                await gate.wait()
            return {"build": n}

        first = asyncio.ensure_future(route.coalesced("k", make))
        await asyncio.sleep(0)
        later = [asyncio.ensure_future(route.coalesced("k", make)) for _ in range(4)]   # clicks while #1 runs
        await asyncio.sleep(0)
        gate.set()
        out = await asyncio.gather(first, *later)
        return started, out

    started, out = asyncio.run(scenario())
    assert started == [1, 2]                                   # five requests, two builds
    assert out[0] == {"build": 1} and all(o == {"build": 2} for o in out[1:])
    assert route._preview_flights["k"] == {"running": None, "queued": None}


def test_a_failed_build_reaches_every_request_waiting_on_it_and_the_next_one_runs_afresh():
    async def scenario():
        calls = []

        async def boom():
            calls.append(1)
            raise RuntimeError("Egeria down")

        res = await asyncio.gather(route.coalesced("e", boom), return_exceptions=True)
        ok = await route.coalesced("e", lambda: asyncio.sleep(0, result="fine"))
        return calls, res, ok

    calls, res, ok = asyncio.run(scenario())
    assert isinstance(res[0], RuntimeError) and ok == "fine"


# ── F29, F28, F31, F32 (the Architect's coco_pharma investigation, 2026-10-30) ──────────────────────────────


def test_f29_a_read_back_never_lists_every_target_of_the_cataloguer(world, fake):
    _catalogued(world, fake, "sales", "archive")
    since = len(fake.calls)
    s = cc.read_back(world["registry"], fake, "db", ["sales", "archive"])
    assert [c for c in fake.calls[since:] if c[0] == "list_catalog_targets"] == []
    assert s["catalogued"] == 2 and s["schemas"] == {"sales": "catalogued", "archive": "catalogued"}


def test_f28_a_failed_read_back_says_it_could_not_read_and_never_leads_with_a_count(world, fake):
    _catalogued(world, fake, "sales")
    fake.fail["connector_status"] = "TIMEOUT_ERROR_408 after 30 s"
    s = cc.read_back(world["registry"], fake, "db", ["sales"])
    line = cc.read_back_line(s, "published by RE 2 ✓ · attached 1 schema ✓ · cataloged by Egeria 0 ◌")
    assert line == ("could not read Egeria · the cataloger's connector: TIMEOUT_ERROR_408 after 30 s · "
                    "nothing changed: the counts are from the last read that worked")
    assert "0" not in line.replace("408", "").replace("30 s", "")


def test_f28_a_partial_read_leads_with_what_was_read_and_names_each_failure(world, fake):
    s = {"catalogued": 1, "attached_waiting": 0, "read_failed": 1, "errors": ["archive: 500 from the view server"]}
    assert cc.read_back_line(s, "cataloged by Egeria 1 schema ✓") == (
        "read back: cataloged by Egeria 1 schema ✓ · 1 read failed: archive: 500 from the view server")
    assert cc.read_back_line({"read_failed": 0}, "") == "read back: nothing read back from Egeria"


def test_f32_a_schema_found_by_the_paged_find_is_not_read_by_name_again(world, fake):
    _catalogued(world, fake, "sales", "archive")
    since = len(fake.calls)
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    by_name = [c for c in fake.calls[since:] if c[0] == "read_element" and "Schema::" in c[1]]
    assert p["attach"] == ["sales", "archive"] and by_name == []


def test_f31_the_preview_logs_its_phases_and_its_egeria_reads(world, fake, caplog):
    _catalogued(world, fake, "sales", "archive")
    choose(world, "sales", "leave_out")
    with caplog.at_level("INFO", logger="resource_explorer.catalogue_commit"):
        cc.build_preview(world["registry"], "db", view(world), fake)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("preview_timing")][-1]
    for part in ("slug=db", "attach=1", "leave_out=1", "derive_ms=", "reinclude_ms=", "database_ms=", "access_ms=",
                 "leave_out_walk_ms=", "manifest_ms=", "declared_measures_ms=", "egeria_reads=", "relationships:"):
        assert part in line, (part, line)


def test_f37_a_not_checked_count_says_no_number():
    from resource_explorer.catalogue_commit import read_back_words
    chips = {"chips": [
        {"key": "attached", "word": "attached", "count": 4, "unit": "schemas", "cue": "read_back", "not_checked_since": ""},
        {"key": "catalogued", "word": "cataloged by Egeria", "count": 0, "unit": "schemas", "cue": "pending",
         "not_checked_since": "2026-10-30T00:54:10"}]}
    words = read_back_words(chips)
    assert words == "attached 4 schemas ✓ · cataloged by Egeria ◌ not checked since 10-30 00:54"
    assert words.count("◌") == 1 and "0" not in words.split("·")[1].split("since")[0]


def test_f38_the_preview_reads_the_publish_table_inside_one_derived_read(world, fake, monkeypatch):
    """The table's derived measures each recompute `run_db_derived` unless one read is open (17 recomputes, 36-48 s
    of coco_pharma's preview, 2026-10-30): the preview opens it around the table it counts."""
    from resource_explorer import publish_table
    from resource_explorer.surveyors.database import db_derived
    seen = []
    monkeypatch.setattr(publish_table, "chosen_rows", lambda *a, **k: seen.append(db_derived._READ_MEMO.get()) or [])
    cc.build_preview(world["registry"], "db", view(world), fake)
    assert seen and seen[0] is not None


def test_f38_the_publish_table_route_derives_once_and_logs_what_it_cost(monkeypatch, caplog):
    from resource_explorer.surveyors.database import db_derived
    from resource_explorer.web.routes import publish_table as pt_route
    runs = []

    def table(registry, entity_type, slug):        # stands in for build_rows: three derived measures read
        for _ in range(3):
            db_derived.run_db_derived(registry, slug)
        return {"rows": []}

    monkeypatch.setattr(db_derived, "_run_db_derived_once", lambda *a, **k: runs.append(1) or {"derived": {}})
    with caplog.at_level("INFO", logger="resource_explorer.web.routes.publish_table"):
        pt_route._one_read("read", "db", table, object(), "database", "db")
    assert len(runs) == 1
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("publish_table_timing")][-1]
    assert "what=read slug=db" in line and "total_ms=" in line and "derived_runs=1" in line
