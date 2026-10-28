"""context_pack: the first context pack (docs/context-pack-first-instance-coco-pharma.md), over a faked reader.

The routine is pure given its reader. These tests build a small Egeria graph in memory (one database, two schemas,
two tables, one of them with a survey annotation) and check the shapes, the state words, the provenance and the
manifest counts. Nothing here reads Egeria or RE.
"""
import json

import pytest

from trellis_analytics.envelope import ResultState
from trellis_analytics.readers.egeria import Element, PackReaderError, Related
from trellis_analytics.routines.context_pack import FactState, ReHeld, context_pack

DB = "db-1"
IDENTITY = "analyst1"


def el(guid, type_name, name, qn="", props=None, created="2026-10-09T01:58:40.842+00:00", by="dbcatnpa", cls=None):
    return Element(guid=guid, type_name=type_name, name=name, qualified_name=qn or name, properties=props or {},
                   create_time=created, created_by=by, classifications=cls or {})


class FakeReader:
    """A graph: element guid -> Element, guid -> [Related]. `identity` is who the reads are made as."""

    def __init__(self, identity=IDENTITY, with_term=False, with_annotation=True, extra_table=False):
        self.identity = identity
        self.calls = []
        self.e, self.r = {}, {}
        db_qn = "PostgreSQL Relational Database::h:5442::coco"
        self.db = el(DB, "RelationalDatabase", "coco", db_qn, {"description": "PostgreSQL database coco"},
                     cls={"Ownership": {"owner": "Jules"}})
        self.e[DB] = self.db
        self.schemas = {}
        spec = {"us_sales": ["forecast"], "ods": ["orders", "lines"]}
        if extra_table:
            spec["us_sales"].append("pipeline")
        rels_db = []
        for s, tables in spec.items():
            sg, st = f"sch-{s}", f"typ-{s}"
            sqn = f"PostgreSQL Relational Database Schema::h:5442::coco.{s}"
            self.e[sg] = el(sg, "DeployedDatabaseSchema", f"coco.{s}", sqn)
            self.e[st] = el(st, "RelationalDBSchemaType", f"Schema Type for coco.{s}")
            self.r[sg] = [Related("Schema", self.e[st])]
            rels_db.append(Related("DataSetContent", self.e[sg]))
            self.r[st] = []
            for t in tables:
                tg = f"tab-{s}-{t}"
                self.e[tg] = el(tg, "RelationalTable", t, f"{sqn}::{t}")
                self.r[st].append(Related("AttributeForSchema", self.e[tg]))
                cols = []
                for c in ("id", "amount"):
                    cg = f"col-{s}-{t}-{c}"
                    self.e[cg] = el(cg, "RelationalColumn", c, f"{sqn}::{t}::{c}")
                    self.r[cg] = []
                    cols.append(Related("NestedSchemaAttribute", self.e[cg]))
                self.r[tg] = cols
        if with_term:
            term = el("term-1", "GlossaryTerm", "Sales Forecast", "term-qn")
            self.r["tab-us_sales-forecast"].append(Related("SemanticAssignment", term))
        anns = []
        if with_annotation:
            for g, kind, js, when in [
                ("ann-old", "Capture Database Table Measurements",
                 {"qualifiedTableName": "coco.us_sales.forecast", "tableSize": 1, "columnCount": 2,
                  "isPopulated": True}, "2026-10-09T01:45:36.790+00:00"),
                ("ann-new", "Capture Database Table Measurements",
                 {"qualifiedTableName": "coco.us_sales.forecast", "tableSize": 147456, "columnCount": 2,
                  "isPopulated": True, "numberOfRowsInserted": 0}, "2026-10-13T00:44:37.220+00:00"),
                ("ann-col", "Capture Database Column Measurements",
                 {"qualifiedColumnName": "coco.us_sales.forecast.amount", "columnDataType": "numeric",
                  "numberOfDistinctValues": 975}, "2026-10-13T00:44:38.000+00:00")]:
                anns.append(Related("AssociatedAnnotation", el(
                    g, "ResourceMeasureAnnotation", f"Annotation::{kind}::{js.get('qualifiedTableName') or js['qualifiedColumnName']}::{g}",
                    props={"annotationType": kind, "jsonProperties": json.dumps(js)}, created=when, by="postgresqlsurveyengine")))
        self.r[DB] = rels_db + anns + [Related("ReportSubject", el("rep-1", "SurveyReport", "Survey Report 1",
                                                                      created="2026-10-12T15:47:44+00:00", by="postgresqlsurveyengine"))]

    def get_subject(self, subject):
        self.calls.append(("subject", subject))
        return self.db if subject in ("coco", DB) else None

    def related(self, guid):
        self.calls.append(("related", guid))
        return list(self.r.get(guid, []))

    def elements_under(self, prefix):
        self.calls.append(("under", prefix))
        return [e for e in self.e.values() if e.type_name == "DeployedDatabaseSchema" and e.qualified_name.startswith(prefix)]


def build(reader=None, **kw):
    return context_pack(reader or FakeReader(), "coco", "data scientist", IDENTITY, **kw)


def table(pack, name):
    return next(t for t in pack.tabular if t.table == name)


# ── shapes ──

def test_tabular_shape_one_row_per_table_with_every_fact_a_state_and_a_value_or_a_reason():
    pack = build()
    assert pack.envelope.result_state == ResultState.MEASURED
    assert sorted((t.schema_name, t.table) for t in pack.tabular) == [("ods", "lines"), ("ods", "orders"), ("us_sales", "forecast")]
    for t in pack.tabular:
        for fact in (t.columns, t.rows, t.size_bytes, t.populated, t.term, t.owner, t.licence, t.lineage):
            assert fact.state in FactState
            if fact.state in (FactState.RECORDED, FactState.MEASURED):
                assert fact.value is not None and fact.provenance is not None
            else:
                assert fact.value is None and fact.reason, f"{t.table}: {fact.state} with no reason"


def test_recorded_columns_come_from_the_catalogue_with_the_cataloguer_as_provenance():
    t = table(build(), "forecast")
    assert t.columns.state == FactState.RECORDED and t.columns.value == 2
    assert t.column_names == ["amount", "id"]
    assert t.columns.provenance.element_guid == "tab-us_sales-forecast"
    assert t.columns.provenance.source == "dbcatnpa"
    assert t.columns.provenance.as_of == "2026-10-09T01:58:40.842+00:00"


def test_survey_annotation_is_measured_newest_wins_and_names_the_annotation():
    t = table(build(), "forecast")
    assert t.size_bytes.state == FactState.MEASURED and t.size_bytes.value == 147456
    assert t.size_bytes.provenance.element_guid == "ann-new"
    assert t.size_bytes.provenance.as_of == "2026-10-13T00:44:37.220+00:00"
    assert t.populated.value is True


def test_a_table_no_survey_measured_says_not_established_not_zero():
    t = table(build(), "orders")
    assert t.size_bytes.state == FactState.NOT_ESTABLISHED and t.size_bytes.value is None
    assert "survey" in t.size_bytes.reason


def test_column_detail_data_type_from_survey_else_not_established():
    t = table(build(), "forecast")
    amount = next(c for c in t.column_detail if c.name == "amount")
    ident = next(c for c in t.column_detail if c.name == "id")
    assert amount.data_type.state == FactState.MEASURED and amount.data_type.value == "numeric"
    assert amount.distinct_values.value == 975
    assert ident.data_type.state == FactState.NOT_ESTABLISHED and ident.data_type.value is None


def test_term_assignment_nothing_found_by_default_and_recorded_when_present():
    assert table(build(), "forecast").term.state == FactState.NOTHING_FOUND
    t = table(build(FakeReader(with_term=True)), "forecast")
    assert t.term.state == FactState.RECORDED and t.term.value == ["Sales Forecast"]
    assert t.term.provenance.element_guid == "tab-us_sales-forecast"


def test_owner_is_the_databases_and_says_so():
    t = table(build(), "forecast")
    assert t.owner.state == FactState.RECORDED and t.owner.value == "Jules"
    assert "database" in t.owner.note
    assert t.owner.provenance.element_guid == DB


def test_licence_and_lineage_are_not_established_with_reasons():
    t = table(build(), "forecast")
    assert t.licence.state == FactState.NOT_ESTABLISHED and "licence" in t.licence.reason.lower()
    assert t.lineage.state == FactState.NOT_ESTABLISHED


def test_document_shape_one_per_table_plus_the_database_with_facts_and_metadata():
    pack = build()
    docs = {d.element_guid: d for d in pack.documents}
    assert set(docs) == {DB, "tab-us_sales-forecast", "tab-ods-orders", "tab-ods-lines"}
    d = docs["tab-us_sales-forecast"]
    for phrase in ("us_sales.forecast", "tab-us_sales-forecast", "147456", "row count: not established", "licence: not established",
                   "term assignment: nothing found", "Jules"):
        assert phrase.lower() in d.text.lower(), phrase
    assert d.metadata["element_guid"] == "tab-us_sales-forecast"
    assert d.metadata["as_of"] and d.metadata["states"]["rows"] == "not established"
    assert "numberOfRows" not in d.text


# ── P2: a fact RE holds and never published ──

def test_a_fact_re_holds_but_never_published_reads_not_published_never_a_value():
    held = ReHeld(fact_kinds={"row_count": {"us_sales.forecast"}})
    pack = build(re_held=held)
    t = table(pack, "forecast")
    assert t.rows.state == FactState.NOT_PUBLISHED
    assert t.rows.value is None
    assert "RE" in t.rows.reason and "not published" in t.rows.reason
    assert "row count: not published" in next(d for d in pack.documents if d.element_guid == t.element_guid).text.lower()
    # a table RE holds nothing for stays "not established", and the two words never swap
    assert table(pack, "orders").rows.state == FactState.NOT_ESTABLISHED


def test_fact_with_a_value_cannot_be_built_in_a_non_value_state():
    from trellis_analytics.routines.context_pack import Fact
    with pytest.raises(ValueError):
        Fact(state=FactState.NOT_PUBLISHED, value=12, reason="x")
    with pytest.raises(ValueError):
        Fact(state=FactState.NOT_PUBLISHED, value=None, reason="")


def test_a_row_count_in_an_egeria_annotation_is_measured_not_published():
    r = FakeReader()
    ann = next(x.element for x in r.r[DB] if x.element.guid == "ann-new")
    ann.properties["jsonProperties"] = json.dumps({"qualifiedTableName": "coco.us_sales.forecast", "tableSize": 5, "rowCount": 321})
    t = table(build(r, re_held=ReHeld(fact_kinds={"row_count": {"us_sales.forecast"}})), "forecast")
    assert t.rows.state == FactState.MEASURED and t.rows.value == 321


# ── the manifest ──

def test_manifest_counts_come_from_the_items_and_move_with_them():
    for extra in (False, True):
        pack = build(FakeReader(extra_table=extra))
        m = pack.manifest
        inc = {l.what: l.count for l in m.included}
        assert inc["tables"] == len(pack.tabular)
        assert inc["schemas"] == len({t.schema_name for t in pack.tabular})
        assert inc["columns"] == sum(len(t.column_detail) for t in pack.tabular)
        assert inc["documents"] == len(pack.documents)
        assert inc["databases"] == 1
        assert inc["survey reports"] == 1
        assert inc["survey annotations used"] == 3 - 1      # two newest of three; the older table one is superseded
    assert len(build(FakeReader(extra_table=True)).tabular) == len(build().tabular) + 1


def test_manifest_left_out_names_what_and_why_with_a_state_word():
    held = ReHeld(fact_kinds={"row_count": {"us_sales.forecast", "ods.orders"}},
                  schemas_not_in_egeria={"eu_sales": "left out of the scope by a person", "tmp1": "undecided in the scope",
                                         "tmp2": "undecided in the scope"})
    pack = build(re_held=held)
    lo = {l.what: l for l in pack.manifest.left_out}
    assert lo["row counts"].count == 2 and lo["row counts"].state == "not published"
    assert "measured by RE" in lo["row counts"].why
    assert lo["licence"].state == "not established" and lo["licence"].count == 3
    assert lo["lineage and dependencies"].state == "not established"
    assert lo["schemas not in Egeria: undecided in the scope"].count == 2
    assert lo["schemas not in Egeria: left out of the scope by a person"].count == 1
    assert lo["superseded survey annotations"].count == 1
    text = pack.manifest.text
    assert "Pack: coco" in text and "data scientist" in text and IDENTITY in text
    assert "row counts" in text and "not published" in text
    assert all(l.why for l in pack.manifest.left_out)           # P6: no field is blank


def test_scope_filter_leaves_other_schemas_out_with_a_count_not_silence():
    pack = build(schemas=["us_sales"])
    assert [t.table for t in pack.tabular] == ["forecast"]
    lo = {l.what: l for l in pack.manifest.left_out}
    assert lo["schemas outside this pack's scope"].count == 1
    assert lo["schemas outside this pack's scope"].names == ["ods"]


# ── provenance, identity, envelope ──

def test_every_fact_with_a_value_names_an_element_and_a_time():
    pack = build()
    for t in pack.tabular:
        for fact in (t.columns, t.rows, t.size_bytes, t.populated, t.term, t.owner):
            if fact.value is not None:
                assert fact.provenance.element_guid and fact.provenance.as_of and fact.provenance.source


def test_built_for_the_given_identity_and_a_reader_for_someone_else_is_refused():
    assert build().manifest.built_for == IDENTITY
    with pytest.raises(ValueError):
        context_pack(FakeReader(identity="someone-else"), "coco", "data scientist", IDENTITY)


def test_unknown_subject_is_nothing_found_not_an_empty_measured_pack():
    pack = context_pack(FakeReader(), "no-such-db", "data scientist", IDENTITY)
    assert pack.envelope.result_state == ResultState.NOTHING_FOUND
    assert pack.tabular == [] and pack.documents == []


def test_reader_failure_is_not_established_with_the_reason():
    class Broken(FakeReader):
        def related(self, guid):
            raise PackReaderError("Egeria refused: 401")
    pack = context_pack(Broken(), "coco", "data scientist", IDENTITY)
    assert pack.envelope.result_state == ResultState.NOT_ESTABLISHED
    assert "401" in pack.envelope.result_state_reason
    assert pack.tabular == []


def test_routine_only_reads_through_the_reader_interface():
    r = FakeReader()
    build(r)
    assert {c[0] for c in r.calls} <= {"subject", "related", "under"}


def test_schema_missed_by_the_relationship_list_is_found_by_name_and_says_so():
    r = FakeReader()
    r.r[DB] = [x for x in r.r[DB] if x.element.guid != "sch-us_sales"]
    pack = build(r)
    assert "forecast" in [t.table for t in pack.tabular]
    assert any("name search" in n for n in pack.notes)


def test_registered_manifest_is_discoverable(entry_point_dist):
    from trellis_analytics.discovery import discover
    found = discover(known_purposes=[])
    assert found["context_pack"].function.endswith("context_pack")
    assert found["context_pack"].source == "egeria"


def test_survey_that_disagrees_with_the_catalogue_is_flagged_on_the_row():
    r = FakeReader()
    ann = next(x.element for x in r.r[DB] if x.element.guid == "ann-new")
    ann.properties["jsonProperties"] = json.dumps({"qualifiedTableName": "coco.us_sales.forecast", "tableSize": 0, "columnCount": 0})
    t = table(build(r), "forecast")
    assert t.size_bytes.value == 0 and "disagrees" in t.size_bytes.note
    assert any("0 columns" in n and "2" in n for n in t.notes)


# ── the real reader, over a canned client (no network) ──

def raw(guid, type_name, **props):
    return {"elementGUID": guid, "type": {"typeName": type_name}, "elementProperties": {"propertiesAsStrings": props},
            "versions": {"createTime": "2026-10-09T01:00:00+00:00", "createdBy": "dbcatnpa", "version": 1},
            "classifications": [{"classificationName": "Ownership", "classificationProperties": {"propertiesAsStrings": {"owner": "Jules"}}}]}


class CannedClient:
    def get_metadata_element_by_guid(self, guid, body=None):
        return raw(guid, "RelationalDatabase", displayName="coco", qualifiedName="q")

    def get_metadata_element_by_unique_name(self, name, property_name, body=None):
        return "No elements found"

    def find_metadata_elements_with_string(self, search_string, starts_with=False, body=None):
        return [raw("g-1", "RelationalDatabase", displayName="coco", qualifiedName="q")] if not starts_with else "No elements found"

    def get_all_related_elements(self, guid, body=None):
        return {"elementList": [{"type": {"typeName": "Schema"}, "relationshipGUID": "rel-1",
                                 "element": raw("s-1", "RelationalDBSchemaType", displayName="st")}]}


def test_egeria_pack_reader_parses_raw_elements_and_never_writes():
    from trellis_analytics.readers.egeria import EgeriaPackReader
    rd = EgeriaPackReader(CannedClient(), IDENTITY)
    db = rd.get_subject("coco")                       # by name: unique-name miss, then a display-name search
    assert db.guid == "g-1" and db.classifications["Ownership"]["owner"] == "Jules" and db.created_by == "dbcatnpa"
    rel = rd.related("g-1")[0]
    assert (rel.type_name, rel.element.guid, rel.relationship_guid) == ("Schema", "s-1", "rel-1")
    assert rd.elements_under("zzz") == []
    assert not [m for m in dir(rd) if m.startswith(("create", "update", "delete", "set_", "add", "remove"))]


def test_a_none_answer_is_unreadable_not_absent():
    from trellis_analytics.readers.egeria import EgeriaPackReader

    class Silent(CannedClient):
        def get_all_related_elements(self, guid, body=None):
            return None
    with pytest.raises(PackReaderError):
        EgeriaPackReader(Silent(), IDENTITY).related("g-1")
