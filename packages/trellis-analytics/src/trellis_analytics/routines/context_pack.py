"""context_pack: package what Egeria holds about one subject, for one kind of consumer, as a context pack.

Build step 1 of docs/context-pack-first-instance-coco-pharma.md (section 6); the requirements are P1 to P13 of
docs/context-as-product-requirements.md. The routine reads Egeria only, as the identity it is given, through a
`PackReader`; it is pure given that reader. It never reads RE's registry: what RE holds and has not published is
told to it as names only (`ReHeld`: which facts RE holds for which tables, never their values), so the pack can
say "not published" (P2) and never carry a number Egeria does not.

Three outputs, from one inventory so no count can disagree with what it counts (P6):
- the tabular shape: one `TableRow` per table, one `Fact` per column of facts;
- the document shape: one `Document` per element, prose with the same facts and the provenance as metadata;
- the manifest: what is included, and what is left out and why, with the state word for each.

Every `Fact` has a state. A fact in `recorded` or `measured` carries a value and its provenance (element GUID,
who made it, when: P4). In any other state it carries no value and always a reason, so a blank never stands for
"not published" or "not established".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from trellis_analytics import __version__
from trellis_analytics.envelope import Envelope, ResultState
from trellis_analytics.readers.egeria import Element, PackReader, PackReaderError, Related

PACK_VERSION = "1"
_ROW_KEYS = ("rowCount", "numberOfRows", "tableRowCount")      # none of Egeria's survey annotations carries one today
_DB_SCHEMA_QN = "PostgreSQL Relational Database Schema::"
_DB_QN = "PostgreSQL Relational Database::"


class FactState(str, Enum):
    RECORDED = "recorded"               # Egeria holds it as an element, relationship or classification
    MEASURED = "measured"               # a survey annotation in Egeria carries the number
    NOT_PUBLISHED = "not published"     # RE holds it, Egeria does not (P2): never a value
    NOT_ESTABLISHED = "not established"  # could not look, or could not tell
    NOTHING_FOUND = "nothing found"     # looked, there was nothing
    NOT_APPLICABLE = "not applicable"


_HAS_VALUE = (FactState.RECORDED, FactState.MEASURED)


class Provenance(BaseModel):
    element_guid: str
    source: str            # who made it: the cataloguer, the survey engine, a person
    as_of: str             # when Egeria says it was made


class Fact(BaseModel):
    state: FactState
    value: Any = None
    reason: str = ""        # why there is no value; empty only when there is one
    note: str = ""
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def _honest(self):
        if self.state in _HAS_VALUE:
            if self.value is None or self.provenance is None:
                raise ValueError(f"a {self.state.value} fact needs a value and its provenance")
        else:
            if self.value is not None:
                raise ValueError(f"a {self.state.value} fact never carries a value (P2)")
            if not self.reason:
                raise ValueError(f"a {self.state.value} fact needs a reason: no field is blank (P6)")
        return self

    @property
    def words(self) -> str:
        return self.state.value


class ColumnRow(BaseModel):
    name: str
    element_guid: str
    data_type: Fact
    distinct_values: Fact


class TableRow(BaseModel):
    schema_name: str
    table: str
    qualified_name: str
    element_guid: str
    columns: Fact
    column_names: list[str] = Field(default_factory=list)
    column_detail: list[ColumnRow] = Field(default_factory=list)
    rows: Fact
    size_bytes: Fact
    populated: Fact
    term: Fact
    owner: Fact
    licence: Fact
    lineage: Fact
    notes: list[str] = Field(default_factory=list)

    def facts(self) -> dict[str, Fact]:
        return {"columns": self.columns, "rows": self.rows, "size_bytes": self.size_bytes, "populated": self.populated,
                "term": self.term, "owner": self.owner, "licence": self.licence, "lineage": self.lineage}


class Document(BaseModel):
    doc_id: str
    element_guid: str
    kind: str                      # database | table
    title: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ManifestLine(BaseModel):
    what: str
    count: int


class LeftOut(BaseModel):
    what: str
    count: int
    why: str
    state: str
    names: list[str] = Field(default_factory=list)


class PackManifest(BaseModel):
    name: str
    consumer_kind: str
    version: str
    as_of: str
    built_for: str
    included: list[ManifestLine] = Field(default_factory=list)
    left_out: list[LeftOut] = Field(default_factory=list)
    identity_note: str = ""
    text: str = ""


class ReHeld(BaseModel):
    """What RE holds and has not published, by NAME only. Never a value: the pack must not carry one (P2).

    `fact_kinds`: fact kind ("row_count", "column_profile") -> the "schema.table" names RE holds it for.
    `schemas_not_in_egeria`: schema name -> why it is not in Egeria's record (RE's own scope words)."""
    fact_kinds: dict[str, set[str]] = Field(default_factory=dict)
    schemas_not_in_egeria: dict[str, str] = Field(default_factory=dict)


class ContextPack(BaseModel):
    subject: str
    subject_guid: str = ""
    consumer_kind: str
    identity: str
    version: str = PACK_VERSION
    as_of: str = ""
    envelope: Envelope
    tabular: list[TableRow] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    manifest: PackManifest | None = None
    notes: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


# ── small helpers ──

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _t(value: str):
    try:
        d = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _prov(e: Element) -> Provenance:
    return Provenance(element_guid=e.guid, source=e.created_by or "unknown", as_of=e.create_time or "unknown")


def _gap(state: FactState, reason: str, note: str = "") -> Fact:
    return Fact(state=state, reason=reason, note=note)


def _json(e: Element) -> dict:
    try:
        v = json.loads(e.properties.get("jsonProperties") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _short_schema(db_name: str, schema_el: Element) -> str:
    n = schema_el.name or schema_el.qualified_name.rsplit(".", 1)[-1]
    return n[len(db_name) + 1:] if n.startswith(db_name + ".") else n.rsplit(".", 1)[-1]


def _schema_prefix(db_qn: str) -> str:
    """The qualifiedName prefix the schema template gives a database's schemas, or '' when the database is not one
    this knows (PostgreSQL's template: `<type> Schema::<server>::<db>.<schema>`)."""
    if db_qn.startswith(_DB_QN):
        return _DB_SCHEMA_QN + db_qn[len(_DB_QN):] + "."
    return ""


# ── the routine ──

def context_pack(reader: PackReader, subject: str, consumer_kind: str, identity: str, *,
                 schemas: list[str] | None = None, re_held: ReHeld | None = None,
                 built_at: str | None = None) -> ContextPack:
    """Build the pack for the database `subject` (name, qualified name or GUID) for `consumer_kind`, as `identity`.

    `schemas` limits the pack to those schemas (the rest are counted in the manifest, not silently dropped).
    `re_held` is what RE holds and has not published, by name (see `ReHeld`); without it nothing is claimed about RE."""
    as_of = built_at or _now()
    reader_identity = getattr(reader, "identity", None)
    if reader_identity is not None and reader_identity != identity:
        raise ValueError(f"the reader reads as {reader_identity!r} but the pack is built for {identity!r}: "
                         "a pack must be read as the identity it is for")

    def result(state, reason="", **kw):
        env = Envelope(result_state=state, result_state_reason=reason, producer_version=__version__,
                       scope=f"database:{subject}", measured_at=as_of)
        return ContextPack(subject=subject, consumer_kind=consumer_kind, identity=identity, as_of=as_of, envelope=env, **kw)

    try:
        inv = _collect(reader, subject, schemas, re_held or ReHeld())
    except PackReaderError as exc:
        return result(ResultState.NOT_ESTABLISHED, f"Egeria could not be read as {identity}: {exc}")
    if inv is None:
        return result(ResultState.NOTHING_FOUND, f"Egeria, read as {identity}, holds no database called {subject!r}")
    manifest = _manifest(inv, consumer_kind, identity, as_of)
    return result(ResultState.MEASURED, subject_guid=inv.db.guid, tabular=inv.tables, documents=inv.documents,
                  manifest=manifest, notes=inv.notes)


class _Inventory:
    """Everything built, once. The manifest is derived from these lists and nothing else."""
    def __init__(self, db: Element):
        self.db = db
        self.tables: list[TableRow] = []
        self.documents: list[Document] = []
        self.schemas_in: list[str] = []
        self.schemas_out: list[str] = []
        self.reports: list[Element] = []
        self.annotations_used: int = 0
        self.annotations_superseded: int = 0
        self.notes: list[str] = []
        self.owner: Fact | None = None
        self.licence: Fact | None = None
        self.zones: Fact | None = None
        self.held: ReHeld = ReHeld()


def _collect(reader: PackReader, subject: str, scope: list[str] | None, held: ReHeld) -> _Inventory | None:
    db = reader.get_subject(subject)
    if db is None:
        return None
    inv = _Inventory(db)
    inv.held = held
    rels = reader.related(db.guid)
    db_name = db.name or subject

    # subject-level facts
    inv.owner = _owner_fact(db)
    inv.licence = _licence_fact(db)
    inv.zones = _zones_fact(db)

    # schemas: the relationship list, plus a qualified-name search for any the list missed
    found: dict[str, Element] = {r.element.guid: r.element for r in rels
                                 if r.type_name == "DataSetContent" and r.element.type_name == "DeployedDatabaseSchema"}
    prefix = _schema_prefix(db.qualified_name)
    if prefix:
        for e in reader.elements_under(prefix):
            if e.type_name == "DeployedDatabaseSchema" and e.guid not in found and "." not in e.qualified_name[len(prefix):]:
                found[e.guid] = e
                inv.notes.append(f"schema {_short_schema(db_name, e)} was not in the database's relationship list; "
                                 "found by name search of its qualified name")
    inv.reports = [r.element for r in rels if r.type_name == "ReportSubject" and r.element.type_name == "SurveyReport"]

    # survey annotations: the newest per table and per column; older ones are counted as superseded
    tab_ann: dict[str, tuple[Element, dict]] = {}
    col_ann: dict[str, tuple[Element, dict]] = {}
    seen: dict[str, int] = {}
    for r in rels:
        if r.type_name != "AssociatedAnnotation":
            continue
        a, js = r.element, _json(r.element)
        kind = a.properties.get("annotationType", "")
        key, bucket = (js.get("qualifiedTableName"), tab_ann) if kind == "Capture Database Table Measurements" else \
                      (js.get("qualifiedColumnName"), col_ann) if kind == "Capture Database Column Measurements" else (None, None)
        if not key:
            continue
        seen[key] = seen.get(key, 0) + 1
        if key not in bucket or _t(a.create_time) > _t(bucket[key][0].create_time):
            bucket[key] = (a, js)
    for sg in sorted(found.values(), key=lambda e: e.name):
        short = _short_schema(db_name, sg)
        if scope is not None and short not in scope:
            inv.schemas_out.append(short)
            continue
        inv.schemas_in.append(short)
        for st in (r.element for r in reader.related(sg.guid) if r.type_name == "Schema"):
            for tb in sorted((r.element for r in reader.related(st.guid) if r.type_name == "AttributeForSchema"),
                             key=lambda e: e.name):
                row = _table_row(reader, db, db_name, short, tb, tab_ann, col_ann, inv)
                inv.tables.append(row)
                inv.documents.append(_table_document(db_name, row))
    # the annotations this pack's tables and columns actually draw on, and the older ones they superseded
    mine = tuple(f"{db_name}.{s}." for s in inv.schemas_in)
    keys = [k for k in {**tab_ann, **col_ann} if k.startswith(mine)] if mine else []
    inv.annotations_used = len(keys)
    inv.annotations_superseded = sum(seen[k] - 1 for k in keys)
    inv.documents.insert(0, _db_document(db_name, inv))
    return inv


def _owner_fact(db: Element) -> Fact:
    own = db.classifications.get("Ownership")
    if own and own.get("owner"):
        return Fact(state=FactState.RECORDED, value=own["owner"], provenance=_prov(db),
                    note="the owner of the database; the table carries no ownership of its own")
    return _gap(FactState.NOTHING_FOUND, "no Ownership classification on the database element")


def _licence_fact(db: Element) -> Fact:
    lic = next((p for n, p in db.classifications.items() if "licen" in n.lower()), None)
    if lic:
        return Fact(state=FactState.RECORDED, value=json.dumps(lic, sort_keys=True), provenance=_prov(db))
    return _gap(FactState.NOT_ESTABLISHED, "no licence classification on the database element; "
                "that no licence is recorded does not show that none applies")


def _zones_fact(db: Element) -> Fact:
    z = db.classifications.get("ZoneMembership")
    if z:
        return Fact(state=FactState.RECORDED, value=str(z.get("zoneMembership", z)), provenance=_prov(db))
    return _gap(FactState.NOTHING_FOUND, "no ZoneMembership classification on the database element (visible to everyone Egeria admits)")


def _row_fact(schema: str, table: str, ann_js: dict | None, ann: Element | None, held: ReHeld) -> Fact:
    if ann_js is not None:
        for k in _ROW_KEYS:
            if ann_js.get(k) is not None:
                return Fact(state=FactState.MEASURED, value=ann_js[k], provenance=_prov(ann),
                            note="from a survey annotation in Egeria")
    if f"{schema}.{table}" in held.fact_kinds.get("row_count", set()):
        return _gap(FactState.NOT_PUBLISHED, "RE measured the row count and it is not published to Egeria, "
                    "so this pack does not quote it")
    if "row_count" in held.fact_kinds:
        return _gap(FactState.NOT_ESTABLISHED, "no Egeria survey annotation carries a row count for this table "
                    "and RE holds none for it either")
    return _gap(FactState.NOT_ESTABLISHED, "no Egeria survey annotation carries a row count for this table; "
                "whether RE holds one was not asked")


def _table_row(reader, db: Element, db_name: str, schema: str, tb: Element, tab_ann, col_ann, inv: _Inventory) -> TableRow:
    path = f"{db_name}.{schema}.{tb.name}"
    cols_rel = reader.related(tb.guid)
    cols = sorted((r.element for r in cols_rel if r.type_name == "NestedSchemaAttribute"), key=lambda e: e.name)
    terms = [r.element for r in cols_rel if r.type_name == "SemanticAssignment"]
    prov_t = _prov(tb)
    columns = Fact(state=FactState.RECORDED, value=len(cols), provenance=prov_t,
                   note="columns the cataloguer recorded under the table") if cols else \
        _gap(FactState.NOTHING_FOUND, "the table has no column elements in Egeria's record")

    ann, js = tab_ann.get(path, (None, None))
    size = populated = None
    notes: list[str] = []
    if ann is not None:
        pa = _prov(ann)
        size = Fact(state=FactState.MEASURED, value=js["tableSize"], provenance=pa, note="bytes, from Egeria's survey annotation") \
            if js.get("tableSize") is not None else None
        populated = Fact(state=FactState.MEASURED, value=js["isPopulated"], provenance=pa) \
            if js.get("isPopulated") is not None else None
        if js.get("columnCount") is not None and cols and js["columnCount"] != len(cols):
            notes.append(f"the survey annotation reports {js['columnCount']} columns where the catalogue records {len(cols)}: "
                         "the two do not agree, so the measured size is questionable")
            if size is not None:
                size.note += "; the survey's column count disagrees with the catalogue's"
    no_survey = "no Egeria survey annotation measures this table"
    size = size or _gap(FactState.NOT_ESTABLISHED, no_survey)
    populated = populated or _gap(FactState.NOT_ESTABLISHED, no_survey)

    if terms:
        term = Fact(state=FactState.RECORDED, value=sorted(t.name for t in terms), provenance=prov_t,
                    note="SemanticAssignment on the table")
    else:
        term = _gap(FactState.NOTHING_FOUND, "no SemanticAssignment on the table")

    column_detail = []
    for c in cols:
        cann, cjs = col_ann.get(f"{path}.{c.name}", (None, None))
        if cann is not None and cjs.get("columnDataType"):
            dt = Fact(state=FactState.MEASURED, value=cjs["columnDataType"], provenance=_prov(cann))
        else:
            dt = _gap(FactState.NOT_ESTABLISHED, "no Egeria survey annotation profiled this column")
        if cann is not None and cjs.get("numberOfDistinctValues") is not None:
            dv = Fact(state=FactState.MEASURED, value=cjs["numberOfDistinctValues"], provenance=_prov(cann))
        else:
            dv = _gap(FactState.NOT_ESTABLISHED, "no Egeria survey annotation profiled this column")
        column_detail.append(ColumnRow(name=c.name, element_guid=c.guid, data_type=dt, distinct_values=dv))

    return TableRow(
        schema_name=schema, table=tb.name, qualified_name=tb.qualified_name, element_guid=tb.guid,
        columns=columns, column_names=[c.name for c in cols], column_detail=column_detail,
        rows=_row_fact(schema, tb.name, js, ann, inv.held), size_bytes=size, populated=populated, term=term,
        owner=inv.owner, licence=inv.licence,
        lineage=_gap(FactState.NOT_ESTABLISHED, "no lineage or dependency is recorded for this table; "
                     "none confirmed is not none existing"),
        notes=notes)


# ── document shape ──

def _say(label: str, f: Fact) -> str:
    if f.state in _HAS_VALUE:
        v = ", ".join(map(str, f.value)) if isinstance(f.value, list) else f.value
        p = f.provenance
        extra = f"; {f.note}" if f.note else ""
        return f"{label}: {v} ({f.state.value}; element {p.element_guid}; {p.source}; {p.as_of}{extra})"
    return f"{label}: {f.state.value} ({f.reason})"


def _table_document(db_name: str, t: TableRow) -> Document:
    lines = [f"Table {db_name}.{t.schema_name}.{t.table} (element GUID {t.element_guid}).",
             _say("columns", t.columns) + (f" named {', '.join(t.column_names)}" if t.column_names else ""),
             _say("row count", t.rows), _say("size in bytes", t.size_bytes), _say("populated", t.populated),
             _say("term assignment", t.term), _say("owner", t.owner), _say("licence", t.licence), _say("lineage", t.lineage)]
    lines += [f"note: {n}" for n in t.notes]
    for c in t.column_detail:
        lines.append(f"column {c.name} (element GUID {c.element_guid}): " + _say("data type", c.data_type)
                     + "; " + _say("distinct values", c.distinct_values))
    states = {k: f.state.value for k, f in t.facts().items()}
    as_ofs = [f.provenance.as_of for f in t.facts().values() if f.provenance]
    return Document(doc_id=f"table:{t.element_guid}", element_guid=t.element_guid, kind="table",
                    title=f"{db_name}.{t.schema_name}.{t.table}", text="\n".join(lines),
                    metadata={"element_guid": t.element_guid, "schema": t.schema_name, "table": t.table, "states": states,
                              "as_of": max(as_ofs, default="unknown"), "sources": sorted({f.provenance.source for f in t.facts().values() if f.provenance})})


def _db_document(db_name: str, inv: _Inventory) -> Document:
    db = inv.db
    lines = [f"Database {db_name} (element GUID {db.guid}, qualified name {db.qualified_name}).",
             f"description: {db.properties.get('description', 'not recorded')}",
             _say("owner", inv.owner), _say("licence", inv.licence), _say("zones", inv.zones),
             f"schemas in this pack: {', '.join(inv.schemas_in) or 'none'}"]
    if inv.schemas_out:
        lines.append(f"schemas outside this pack's scope: {', '.join(inv.schemas_out)}")
    lines.append(f"survey reports: {len(inv.reports)}" + "".join(
        f"; {r.name} (element {r.guid}, {r.create_time})" for r in inv.reports))
    return Document(doc_id=f"database:{db.guid}", element_guid=db.guid, kind="database", title=db_name,
                    text="\n".join(lines),
                    metadata={"element_guid": db.guid, "states": {"owner": inv.owner.state.value, "licence": inv.licence.state.value,
                                                                   "zones": inv.zones.state.value}, "as_of": db.create_time or "unknown",
                              "sources": [db.created_by]})


# ── the manifest: derived from the inventory, nothing else ──

def _manifest(inv: _Inventory, consumer_kind: str, identity: str, as_of: str) -> PackManifest:
    t = inv.tables
    count = lambda pred: sum(1 for r in t if pred(r))    # noqa: E731
    included = [ManifestLine(what="databases", count=1), ManifestLine(what="schemas", count=len(inv.schemas_in)),
                ManifestLine(what="tables", count=len(t)), ManifestLine(what="columns", count=sum(len(r.column_detail) for r in t)),
                ManifestLine(what="term assignments", count=count(lambda r: r.term.state == FactState.RECORDED)),
                ManifestLine(what="owners", count=1 if inv.owner.state == FactState.RECORDED else 0),
                ManifestLine(what="survey reports", count=len(inv.reports)),
                ManifestLine(what="survey annotations used", count=inv.annotations_used),
                ManifestLine(what="documents", count=len(inv.documents))]
    left: list[LeftOut] = []
    if inv.schemas_out:
        left.append(LeftOut(what="schemas outside this pack's scope", count=len(inv.schemas_out), state="left out",
                            why="Egeria records them; this pack was asked for other schemas", names=sorted(inv.schemas_out)))
    by_reason: dict[str, list[str]] = {}
    for name, why in inv.held.schemas_not_in_egeria.items():
        by_reason.setdefault(why, []).append(name)
    for why, names in sorted(by_reason.items()):
        left.append(LeftOut(what=f"schemas not in Egeria: {why}", count=len(names), state="not published",
                            why=f"RE knows these schemas; Egeria's record does not hold them ({why})", names=sorted(names)))
    np_rows = count(lambda r: r.rows.state == FactState.NOT_PUBLISHED)
    ne_rows = count(lambda r: r.rows.state == FactState.NOT_ESTABLISHED)
    if np_rows:
        left.append(LeftOut(what="row counts", count=np_rows, state="not published",
                            why="measured by RE, not published to Egeria; the pack quotes none"))
    if ne_rows:
        left.append(LeftOut(what="row counts with no source", count=ne_rows, state="not established",
                            why="no Egeria survey annotation carries a row count for these tables"))
    prof = [n for n in inv.held.fact_kinds.get("column_profile", set())
            if any(f"{r.schema_name}.{r.table}" == n for r in t)]
    if prof:
        left.append(LeftOut(what="column profiles", count=len(prof), state="not published",
                            why="measured by RE, not published to Egeria", names=sorted(prof)))
    no_dtype = sum(1 for r in t for c in r.column_detail if c.data_type.state != FactState.MEASURED)
    if no_dtype:
        left.append(LeftOut(what="column data types", count=no_dtype, state="not established",
                            why="the cataloguer records no data type; only columns an Egeria survey profiled have one"))
    if t:
        left.append(LeftOut(what="licence", count=len(t), state="not established", why=inv.licence.reason))
        left.append(LeftOut(what="lineage and dependencies", count=len(t), state="not established", why=t[0].lineage.reason))
    nt = count(lambda r: r.term.state == FactState.NOTHING_FOUND)
    if nt:
        left.append(LeftOut(what="term assignments", count=nt, state="nothing found", why="no SemanticAssignment on these tables"))
    if inv.annotations_superseded:
        left.append(LeftOut(what="superseded survey annotations", count=inv.annotations_superseded, state="not applicable",
                            why="an older annotation for a table or column Egeria has measured again; the newest is used"))
    lines = [f"Pack: {inv.db.name} · {consumer_kind} · v{PACK_VERSION} · as of {as_of} · built for {identity}",
             "Included: " + " · ".join(f"{l.count} {l.what}" for l in included if l.count),
             "Left out and why:"]
    lines += [f"  {l.count} {l.what} · {l.state} · {l.why}" for l in left]
    ident = f"read as {identity}; zones applied by Egeria: {inv.zones.state.value if inv.zones.state != FactState.RECORDED else inv.zones.value}"
    lines.append("Identity: " + ident)
    return PackManifest(name=inv.db.name, consumer_kind=consumer_kind, version=PACK_VERSION, as_of=as_of, built_for=identity,
                        included=included, left_out=left, identity_note=ident, text="\n".join(lines))
