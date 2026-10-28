import json

from trellis_analytics.envelope import ResultState
from trellis_analytics.readers.ods import OdsReader
from trellis_analytics.routines import curation_coverage


def els(*tables):
    return json.dumps({"tables": list(tables)})


def test_counts_published_not_yet_blocked(make_ods):
    url = make_ods(
        schemas=["sales", "hr", "ops", "fin", "arch", "dev", "blk", "lone"],
        proofs=[
            ("schema", "sales", "elements_read_back", "2026-10-05T10:00:00Z", els("a", "b", "c")),
            ("schema", "hr", "elements_read_back", "2026-10-05T10:01:00Z", els("p", "q")),
            ("database", "", "report_published", "2026-10-05T10:02:00Z", json.dumps({"annotation_count": 76})),
            ("schema", "blk", "leave_out_blocked", "2026-10-05T11:00:00Z", "{}"),
        ],
        events=[("sales", "catalogue", "person"), ("hr", "catalogue", "person"),
                ("ops", "catalogue", "person"), ("fin", "catalogue", "rule-1"),
                ("arch", "leave_out", "person"), ("dev", "leave_out", "rule-2"), ("blk", "leave_out", "person")],
        outbox=["done", "superseded", "superseded"])
    r = curation_coverage(OdsReader(url), "database", "demo")
    assert r.envelope.result_state is ResultState.MEASURED
    assert r.count("published", "schemas") == 2 and r.count("published", "tables") == 5
    assert r.count("published", "report") == 1
    assert r.as_of == "2026-10-05T10:02:00Z"
    ny = {l.key: l for l in r.group("not_yet").lines}
    assert ny["undecided"].names == ["lone"]
    assert ny["chosen"].names == ["ops"]
    assert ny["chosen_confirmed"].names == ["fin"]
    assert ny["left_out"].names == ["arch"]
    assert ny["left_out_confirmed"].names == ["dev"]
    # the blocked schema is counted once, in blocked, not also as left out
    assert r.count("blocked", "blocked") == 1 and r.count("blocked", "superseded") == 2
    # every known schema lands in exactly one group
    assert 2 + sum(l.count for l in ny.values()) + 1 == r.known_schemas == 8
    # every count carries the ids it counts
    assert all(l.rows or l.count == 0 for g in r.groups for l in g.lines)
    assert r.group("published").lines[0].rows["catalogue_commit_proofs"]
    assert "read back 2026-10-05T10:02:00Z" in r.group("published").words


def test_a_later_removal_unpublishes_and_a_reset_forgets(make_ods):
    url = make_ods(
        schemas=["s1", "s2"],
        proofs=[("schema", "s1", "elements_read_back", "2026-10-01T00:00:00Z", els("a")),
                ("schema", "s1", "removed", "2026-10-02T00:00:00Z", "{}"),
                ("schema", "s2", "elements_read_back", "2026-10-01T00:00:00Z", els("b")),
                ("database", "", "egeria_reset", "2026-10-03T00:00:00Z", "{}")],
        events=[("s1", "catalogue", "person")])
    r = curation_coverage(OdsReader(url), "database", "demo")
    # s2's proof predates the reset, so it is not published; s1 was removed
    assert r.count("published", "schemas") == 0
    assert r.count("not_yet", "chosen") == 1 and r.count("not_yet", "undecided") == 1


def test_nothing_found_is_not_not_established(make_ods):
    empty = curation_coverage(OdsReader(make_ods(schemas=["a", "b"])), "database", "demo")
    assert empty.envelope.result_state is ResultState.NOTHING_FOUND
    assert "nothing published yet · 2 known to RE" == empty.summary

    broken = curation_coverage(OdsReader("sqlite:////nonexistent/dir/x.sqlite"), "database", "demo")
    assert broken.envelope.result_state is ResultState.NOT_ESTABLISHED
    assert broken.envelope.result_state_reason and broken.groups == []
    assert empty.envelope.result_state != broken.envelope.result_state


def test_missing_table_is_not_established_not_nothing(tmp_path):
    import sqlite3
    p = tmp_path / "bare.sqlite"
    sqlite3.connect(p).close()
    r = curation_coverage(OdsReader(f"sqlite:///{p}"), "database", "demo")
    assert r.envelope.result_state is ResultState.NOT_ESTABLISHED


def test_other_kinds_are_not_applicable(make_ods):
    r = curation_coverage(OdsReader(make_ods()), "repository", "demo")
    assert r.envelope.result_state is ResultState.NOT_APPLICABLE and r.envelope.result_state_reason


def test_slug_with_hyphens_is_normalised(make_ods):
    url = make_ods(slug="coco_pharma", schemas=["x"])
    assert curation_coverage(OdsReader(url), "database", "coco-pharma").known_schemas == 1


def test_notebook_import_path():
    from trellis_analytics.routines import curation_coverage as fn
    assert callable(fn)


def test_mixed_timestamp_shapes_order_correctly_around_a_reset(make_ods):
    # '20261006T2100Z' sorts AFTER '2026-10-08...' as a string, but is earlier in time: it predates the reset
    url = make_ods(schemas=["s1"],
                   proofs=[("schema", "s1", "restored", "20261006T2100Z", "{}"),
                           ("database", "", "egeria_reset", "2026-10-08T22:07:59", "{}")])
    r = curation_coverage(OdsReader(url), "database", "demo")
    assert r.count("published", "schemas") == 0


# ── one source for the schema list: the universe is Curate's node set ──

def _curate_schemas(url, slug="demo"):
    """What Curate shows: the non-system schemas of resolve_node_set over a REAL registry on the same file."""
    from resource_explorer.catalogue_scope import resolve_node_set
    from resource_explorer.registry import ProjectRegistry
    node_set = resolve_node_set(ProjectRegistry(db_path=url.removeprefix("sqlite:///")), slug)
    return sorted(s["schema"] for s in node_set["schemas"] if s.get("schema") and s.get("classification") != "system"), node_set


def test_universe_is_curates_node_set_when_a_native_survey_and_a_local_tree_disagree(make_registry):
    from conftest import add_curation_rows, write_local_survey, write_native_survey
    reg, url = make_registry("demo")
    # local (older): 3 schemas + a system one; native (newer, fuller): 5 schemas, one only it knows
    write_local_survey(reg, "demo", ["loc_a", "loc_b", "shared", "information_schema"], at="2026-10-01T00:00:00Z")
    write_native_survey(reg, "demo", ["shared", "nat_a", "nat_b", "nat_c", "nat_d"], tables_per=3, at="2026-10-04T06:00:00")
    add_curation_rows(url, "demo", proofs=[("schema", "shared", "elements_read_back", "2026-10-05T10:00:00Z", els("t0"))],
                      events=[("nat_a", "catalogue", "person")])
    curate, node_set = _curate_schemas(url)
    old_universe = {"loc_a", "loc_b", "shared"}          # what the routine used to count: the local survey alone
    assert set(curate) != old_universe and "nat_a" in curate and node_set["chosen"]["kind"] == "egeria"
    r = curation_coverage(OdsReader(url), "database", "demo")
    assert r.envelope.result_state is ResultState.MEASURED
    assert r.known == curate and r.known_schemas == len(curate)
    assert r.counted_against["chosen"]["kind"] == "egeria" and r.counted_against["disagree"] is True
    assert r.count("published", "schemas") == 1 and r.count("not_yet", "chosen") == 1
    assert "information_schema" not in r.known
    # every Curate schema lands in exactly one group
    ny = sum(l.count for l in r.group("not_yet").lines)
    assert r.count("published", "schemas") + ny + r.count("blocked", "blocked") == len(curate)


def test_universe_is_curates_node_set_when_only_a_local_tree_exists(make_registry):
    from conftest import write_local_survey
    reg, url = make_registry("demo")
    write_local_survey(reg, "demo", ["a", "b", "pg_catalog"])
    curate, _ = _curate_schemas(url)
    r = curation_coverage(OdsReader(url), "database", "demo")
    assert r.known == curate == ["a", "b"] and r.counted_against["chosen"]["kind"] == "local"


def test_a_newer_local_node_the_native_survey_never_saw_is_counted_because_curate_shows_it(make_registry):
    from conftest import write_local_survey, write_native_survey
    reg, url = make_registry("demo")
    write_native_survey(reg, "demo", ["n1", "n2", "n3"], tables_per=3, at="2026-10-01T06:00:00")
    write_local_survey(reg, "demo", ["n1", "fresh"], at="2026-10-05T00:00:00Z")
    curate, _ = _curate_schemas(url)
    assert "fresh" in curate
    assert curation_coverage(OdsReader(url), "database", "demo").known == curate


def test_a_choice_for_a_schema_curate_does_not_show_is_reported_not_counted(make_registry):
    from conftest import add_curation_rows, write_local_survey
    reg, url = make_registry("demo")
    write_local_survey(reg, "demo", ["a"])
    add_curation_rows(url, "demo", events=[("ghost", "catalogue", "person")])
    r = curation_coverage(OdsReader(url), "database", "demo")
    assert r.known == ["a"] and r.outside_node_set == ["ghost"]
    assert r.count("not_yet", "chosen") == 0


def test_without_resource_explorer_the_result_is_not_applicable_not_a_private_universe(make_ods, monkeypatch):
    url = make_ods(schemas=["a", "b"])
    import sys
    monkeypatch.setitem(sys.modules, "resource_explorer", None)
    monkeypatch.setitem(sys.modules, "resource_explorer.registry", None)
    monkeypatch.setitem(sys.modules, "resource_explorer.catalogue_scope", None)
    r = curation_coverage(OdsReader(url), "database", "demo")
    assert r.envelope.result_state is ResultState.NOT_APPLICABLE
    assert "trellis-analytics[re]" in r.envelope.result_state_reason and r.known == [] and r.groups == []


def test_removal_alone_unpublishes_and_restoring_republishes(make_ods):
    # no reset here: the earlier removal test is masked by its reset, so this pins `removed` / `archived` / `restored`
    url = make_ods(schemas=["s1", "s2", "s3"],
                   proofs=[("schema", "s1", "elements_read_back", "2026-10-01T00:00:00Z", els("a")),
                           ("schema", "s1", "removed", "2026-10-02T00:00:00Z", "{}"),
                           ("schema", "s2", "elements_read_back", "2026-10-01T00:00:00Z", els("b")),
                           ("schema", "s2", "archived", "2026-10-02T00:00:00Z", "{}"),
                           ("schema", "s3", "removed", "2026-10-01T00:00:00Z", "{}"),
                           ("schema", "s3", "restored", "2026-10-02T00:00:00Z", "{}")])
    r = curation_coverage(OdsReader(url), "database", "demo")
    assert r.group("published").lines[0].names == ["s3"]
    assert r.count("not_yet", "undecided") == 2


def test_a_system_classified_node_is_never_counted_even_with_a_name(make_ods, monkeypatch):
    import resource_explorer.catalogue_scope as cs
    real = cs.resolve_node_set
    def with_named_system(reg, slug, tree=None):
        ns = real(reg, slug, tree)
        ns["schemas"].append({"schema": "odd_system", "classification": "system"})
        return ns
    monkeypatch.setattr(cs, "resolve_node_set", with_named_system)
    r = curation_coverage(OdsReader(make_ods(schemas=["a"])), "database", "demo")
    assert r.known == ["a"]


def test_an_unsurveyed_database_is_not_established_not_nothing_found(make_ods):
    # No survey told RE of any schema: nothing was looked at, so "nothing found" would be a confident guess.
    r = curation_coverage(OdsReader(make_ods()), "database", "demo")
    assert r.envelope.result_state is ResultState.NOT_ESTABLISHED
    assert "no survey" in r.envelope.result_state_reason
    assert r.summary == "not surveyed · no schemas known to RE" and r.known_schemas == 0


def test_not_yet_words_separate_the_total_from_its_parts(make_ods):
    url = make_ods(schemas=["a", "b", "c", "d"],
                   proofs=[("schema", "a", "elements_read_back", "2026-10-05T10:00:00Z", els("t"))],
                   events=[("a", "catalogue", "person"), ("d", "leave_out", "rule-1")])
    r = curation_coverage(OdsReader(url), "database", "demo")
    assert r.group("not_yet").words == "not yet published · 3: 2 undecided · 1 left out by a confirmed proposal"
