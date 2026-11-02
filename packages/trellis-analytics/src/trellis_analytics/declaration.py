"""The declaration: what an analysis says about its own results, so a screen, a chat answer and an Egeria publish can
use them with no code written for that analysis (design note DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE,
§1 "what a declaration carries", §2 result kinds, §4 confidence forms).

One model, two carriers. A library step carries it as `StepManifest.declaration`; Resource Explorer's own analyses
carry the same fields under `declaration:` in `analysis_catalog.yaml`, validated by this same class
(`analysis_catalog_reader`). The generator copies a step's declaration into its catalogue entry unchanged.

Two levels (owner's answer 2, §1): the **minimum** an experiment runs with is `result_kind`, `measures` and
`absence`, and this class refuses anything less. A repeatable analysis is promoted to a **full** declaration by adding
`extents`, `confidence`, `answers` and `scope`; `missing()` names what a minimum declaration still lacks.

Every refusal is a sentence naming the field and the value refused, as the manifest's own validators word theirs.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: §2: the fixed vocabulary. A ninth kind is a design change, not a value a declaration may invent.
RESULT_KINDS = ("measure", "series", "distribution", "list", "graph", "finding", "proposal", "table")

#: §1's four absences, each a sentence the analysis declares, never one a renderer improvises. How they meet the
#: envelope's states (`envelope.ResultState`): `measured_nothing` is `nothing_found`; `not_measured` and
#: `not_permitted` are both `not_established`, told apart by the reason (could not look, versus not allowed to);
#: `not_applicable` is `not_applicable`.
ABSENCE_STATES = ("not_measured", "not_permitted", "not_applicable", "measured_nothing")

#: §4: a result's confidence is one of five forms, and a score never travels without its basis.
CONFIDENCE_FORMS = ("measured", "estimated", "inferred", "judged", "generated")

#: The forms from strongest to weakest, for §4's "weakest link": a declaration's headline confidence is the weakest
#: of its measures' (project owner, 2026-10-28). A measured read is exact; a judged result carries a person's
#: signature on its evidence; an estimate has a known error; an inferred match a rule or model score; a generated
#: answer is only as good as what it drew on.
CONFIDENCE_STRENGTH = ("measured", "judged", "estimated", "inferred", "generated")

#: §1 `scope`: the level a result is about.
SCOPES = ("resource", "container", "member", "field")

#: The words an extent's truncation sentence must carry for the compiler to fill: "{shown} of {total} ... shown".
TRUNCATION_PLACEHOLDERS = ("{shown}", "{total}")

#: The kinds whose result is its measures: they declare at least one. The other four kinds (finding, proposal, list,
#: table) are told by the block each declares, and a count of what they hold is welcome but not required.
MEASURED_KINDS = ("measure", "series", "distribution", "graph")

#: The kinds that declare how their categories add up (`CategoriesDeclaration`).
CATEGORY_KINDS = ("graph", "distribution")

#: Why a proposal is never decided automatically (owner's rule, 2026-10-15): a proposal becomes a fact only through a
#: person's decision in Curate. `decided_by` keeps "automatic" in its vocabulary so a later ruling can admit it without a
#: model change; until then every annotation type refuses it.
AUTOMATIC_REFUSAL = ("proposals are decided by a person in Curate (ruling 2026-10-15); no automatic path exists, so "
                     "declare decided_by 'curator'")

#: The words a lower bound travels with wherever a figure would be derived from it.
LOWER_BOUND_WORDS = "not derived: the count is a lower bound"

#: The fields a full declaration adds to the minimum, in the order `missing()` names them.
FULL_FIELDS = ("extents", "confidence", "answers", "scope")


def _words(v: str) -> str:
    return " ".join(str(v or "").split())


class _Strict(BaseModel):
    # An unknown key is refused, so a misspelt field is named rather than silently dropped.
    model_config = ConfigDict(extra="forbid")


class AnnotationDeclaration(_Strict):
    """The Egeria annotation a measure writes as (§1 `annotation`), or absent: "generic measure, no native type
    declared" (owner's answer 1). `properties` maps a result field to the Egeria property it is written to; empty
    means the type is declared and its property map is not."""
    type: str
    properties: dict[str, str] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if not _words(v):
            raise ValueError("annotation type is empty: name the Egeria annotation type, or leave annotation out "
                             "for a generic measure")
        return _words(v)


class BasisDeclaration(_Strict):
    """The whole a counted measure is a part of: "456 of 1,236 columns" is derived from the measure's value and this
    denominator, so no reader writes the "of" in prose. Legal only on a count; a fraction's whole is in its unit."""
    #: The result field holding the whole ("column_count"); a dotted path or a trailing `[]`, as `value_field`.
    denominator_field: str
    #: The whole in words, plural: "columns", "tables in the schema", "rows read".
    denominator_label: str

    @model_validator(mode="after")
    def _filled(self) -> "BasisDeclaration":
        if not _words(self.denominator_field):
            raise ValueError("a basis names no denominator_field: say which result field holds the whole")
        if not _words(self.denominator_label):
            raise ValueError("a basis declares no words for the whole (denominator_label): say what is counted, "
                             "plural, such as 'columns'")
        return self


def is_fraction(unit: str) -> bool:
    """Does this unit say a fraction, ratio or percentage, rather than a count of things? (Its whole is in its words.)"""
    u = " ".join(str(unit or "").lower().split())
    return u.startswith(("fraction", "ratio", "percent", "share", "proportion")) or "(0-1)" in u or "%" in u


class MeasureDeclaration(_Strict):
    """One number the analysis reports. A number never travels without its unit and its as-of (§1)."""
    name: str
    #: In words, plural where it counts things: "tables", "rows per day", "days", "fraction of rows (0-1)".
    unit: str
    #: The result field that says when this was measured ("asOf", "surveyed_at", "toAsOf"). A dotted path names a
    #: nested field ("temporal.as_of"); `run.<field>` names a field of the run that produced the result rather than
    #: of the result itself (`run.read_snapshot`: the newest stored row a zero-fetch derivation read).
    as_of: str
    #: The result field holding the value; a dotted path names a nested one ("counts.stale"), and a trailing `[]`
    #: declares the value as the number of items in a list field ("columns_added[]": columns added, counted).
    value_field: str
    #: What it writes as in Egeria; absent is the generic measure route, labelled as such.
    annotation: AnnotationDeclaration | None = None
    #: This measure's own confidence, where it differs from the declaration's (project owner, 2026-10-28: confidence
    #: is per measure). Absent means the declaration's confidence is this measure's.
    confidence: "ConfidenceDeclaration | None" = None
    #: The level THIS measure is about, where it differs from the declaration's (a member-scoped step whose summary
    #: is one value for the whole resource). Absent means the declaration's scope is this measure's.
    scope: Literal["resource", "container", "member", "field"] | None = None
    #: The whole this count is a part of; absent for a count with no stated whole.
    basis: BasisDeclaration | None = None
    #: The result field that is true when the value is a lower bound ("capped", "paging.capped", "run.truncated").
    #: Declared on the measure, not the declaration, because one step may cap one measure (a paged list) and not
    #: another (a COUNT(*)). A capped value reads "at least N <unit>" and every figure derived from it is withheld.
    capped_field: str = ""

    @field_validator("scope", mode="before")
    @classmethod
    def _scope(cls, v):
        if v is not None and v not in SCOPES:
            raise ValueError(f"scope '{v}' is not one of the levels ({', '.join(SCOPES)})")
        return v

    @model_validator(mode="after")
    def _basis_is_on_a_count(self) -> "MeasureDeclaration":
        if self.basis is not None and is_fraction(self.unit):
            raise ValueError(f"measure '{self.name}' declares a basis on a fraction ('{self.unit}'): the "
                             "denominator is in the unit, so a basis belongs on a count only")
        return self

    @model_validator(mode="after")
    def _filled(self) -> "MeasureDeclaration":
        empty = [f for f in ("name", "unit", "as_of", "value_field") if not _words(getattr(self, f))]
        if empty:
            label = f"measure '{self.name}'" if _words(self.name) else "a measure"
            raise ValueError(f"{label} declares no {', '.join(empty)}: a number never travels without its unit "
                             "and its as-of")
        return self


class AbsenceSentences(_Strict):
    """§1 `absence`: the sentence shown for each way a result can be absent. All four are declared."""
    not_measured: str
    not_permitted: str
    not_applicable: str
    measured_nothing: str

    @model_validator(mode="after")
    def _filled(self) -> "AbsenceSentences":
        empty = [s for s in ABSENCE_STATES if not _words(getattr(self, s))]
        if empty:
            raise ValueError(f"absence sentence for {', '.join(empty)} is empty: an absence is declared, never "
                             "improvised")
        return self

    def sentence(self, state: str) -> str:
        return getattr(self, state)


class ExtentDeclaration(_Strict):
    """§3 step 2: one shape the Chat compiler may take from this analysis, and no other."""
    name: str
    fields: list[str]
    #: How many rows the extent carries at most.
    rows: int
    #: The order rows are taken in, in words ("PUBLIC grantees first, then by table"), for an order no `sort` can
    #: say. Not declared beside `sort`: the order is said in one place.
    order: str = ""
    #: The order as keys the compiler applies, each "<field> asc" or "<field> desc" on one of `fields`, first key
    #: first ("row_count desc"); the words are derived from it (`order_words`). Project owner, 2026-10-28.
    sort: list[str] = Field(default_factory=list)
    #: Shown when rows were dropped; carries `{shown}` and `{total}` for the compiler to fill.
    truncation: str
    #: The extent's cost at `rows` rows, in tokens, for the compiler's budget (§3 step 4).
    token_cost: int
    #: How an answer cites a row of this extent ("{analysis} · {scope} · as of {as_of}").
    citation: str
    #: Where the rows are in the result, when that is not a field named like the extent: each a dotted path to a
    #: list (each item a row) or to a mapping (one row), "." the result itself as one row. Several are joined in
    #: order. Empty means the extent's name is the path.
    source: list[str] = Field(default_factory=list)
    #: With several sources: the row field that carries which source a row came from (the path's last part:
    #: "columns_added"), so joined rows stay told apart. Empty for none.
    tag: str = ""

    @field_validator("source", "sort", mode="before")
    @classmethod
    def _one_item(cls, v):
        return [v] if isinstance(v, str) else v

    def sort_keys(self) -> list[tuple[str, bool]]:
        """`sort` as (field, descending) pairs, first key first."""
        out = []
        for key in self.sort:
            field, _, direction = " ".join(key.split()).rpartition(" ")
            out.append((field, direction == "desc"))
        return out

    def order_words(self) -> str:
        """The order in words: derived from `sort` when it is declared, else the declared sentence."""
        if not self.sort:
            return self.order
        return "by " + ", then ".join(f"{f}, {'descending' if d else 'ascending'}" for f, d in self.sort_keys())

    @model_validator(mode="after")
    def _shape(self) -> "ExtentDeclaration":
        label = f"extent '{self.name}'" if _words(self.name) else "an extent"
        if not _words(self.name):
            raise ValueError("an extent declares no name")
        if any(not _words(s) for s in self.source):
            raise ValueError(f"{label} declares an empty source path")
        if _words(self.tag) and _words(self.tag) not in self.fields:
            raise ValueError(f"{label} tags its rows with '{self.tag}', which is not one of its fields: the tag is "
                             "how the rows are told apart, so the compiler must show it")
        if not [f for f in self.fields if _words(f)]:
            raise ValueError(f"{label} declares no fields: the compiler takes the declared fields and no others")
        if self.rows < 1:
            raise ValueError(f"{label} declares rows {self.rows}: an extent carries at least one row")
        if self.token_cost < 1:
            raise ValueError(f"{label} declares token_cost {self.token_cost}: the compiler budgets by it, so it "
                             "must be a positive estimate")
        if not _words(self.citation):
            raise ValueError(f"{label} declares no citation")
        if self.sort and _words(self.order):
            raise ValueError(f"{label} declares both sort and order: the order is said in one place, so leave the "
                             "order sentence out and it is derived from sort")
        if not self.sort and not _words(self.order):
            raise ValueError(f"{label} declares no order: declare sort keys, or the order in words when no key "
                             "can say it")
        for key in self.sort:
            field, _, direction = " ".join(key.split()).rpartition(" ")
            if direction not in ("asc", "desc") or not field:
                raise ValueError(f"{label} sort key '{key}' is not '<field> asc' or '<field> desc'")
            if field not in self.fields:
                raise ValueError(f"{label} sorts by '{field}', which is not one of its fields")
        lacking = [p for p in TRUNCATION_PLACEHOLDERS if p not in self.truncation]
        if lacking:
            raise ValueError(f"{label} truncation sentence '{self.truncation}' lacks {', '.join(lacking)}: it says "
                             "how many rows were shown of how many")
        return self


class ConfidenceDeclaration(_Strict):
    """§4: the form, and the result field that carries its basis. A score never travels without its basis."""
    form: str
    #: The result field carrying the basis: the method or sample ratio (estimated), the rules that matched
    #: (inferred), who and on what evidence (judged), the sources drawn on (generated). Empty for `measured`, whose
    #: basis is its as-of and scope.
    basis: str = ""
    #: The basis in words, which is what a person reads beside the form. Required whenever `basis` names a result
    #: field (a field path is for the code, not the reader); alone it stands for a basis no result field carries: an estimate's
    #: source ("pg_stat_user_tables.n_live_tup, the statistics collector's live-row estimate"). An estimated or
    #: generated result declares a basis field, a method, or both; an inferred or judged one needs its basis field,
    #: because its basis differs per result.
    method: str = ""
    #: The result field carrying the score, for an inferred result; empty where there is none.
    score: str = ""
    #: Inferred only, and required there (§4 "calibration is stated"): is the score a calibrated probability, or a
    #: rule count?
    calibrated: bool | None = None

    @model_validator(mode="after")
    def _form(self) -> "ConfidenceDeclaration":
        if self.form not in CONFIDENCE_FORMS:
            raise ValueError(f"confidence form '{self.form}' is not one of the five forms "
                             f"({', '.join(CONFIDENCE_FORMS)})")
        if self.form in ("inferred", "judged") and not _words(self.basis):
            raise ValueError(f"confidence form '{self.form}' declares no basis: a score never travels without "
                             "its basis, and this form's basis differs per result, so name the field carrying it")
        if self.form in ("estimated", "generated") and not (_words(self.basis) or _words(self.method)):
            raise ValueError(f"confidence form '{self.form}' declares no basis and no method: say how the number "
                             "was arrived at")
        if self.form == "measured" and (_words(self.score) or self.calibrated is not None):
            raise ValueError("confidence form 'measured' declares a score: an exact read has none, because none "
                             "is meaningful")
        if self.form == "inferred" and self.calibrated is None:
            raise ValueError("confidence form 'inferred' does not say whether its score is calibrated: state "
                             "calibrated true (a probability) or false (a rule count)")
        if self.form != "inferred" and self.calibrated is not None:
            raise ValueError(f"confidence form '{self.form}' declares calibrated: only an inferred score is "
                             "calibrated or not")
        if _words(self.basis) and not _words(self.method):
            raise ValueError(f"confidence basis '{self.basis}' names a result field and no method: a person reads "
                             "the method, never a field path, so say the basis in words")
        return self


class CategoriesDeclaration(_Strict):
    """How a graph's or distribution's categories relate (the #603 lesson: categories that overlap must not be
    stacked or summed, and a stated total is read, never added up on the client)."""
    #: True: one item falls in exactly one category. False: an item may fall in several, so a renderer must not stack
    #: or sum the categories and the headline may not say "of N".
    exclusive: bool
    #: The result field holding the stated total the categories add up to; "" for no stated total.
    sums_to: str = ""

    @model_validator(mode="after")
    def _sums(self) -> "CategoriesDeclaration":
        if _words(self.sums_to) and not self.exclusive:
            raise ValueError("categories declare a total (sums_to) and are not exclusive: overlapping categories "
                             "cannot sum to a total, so leave sums_to out or declare them exclusive")
        return self


class FindingDeclaration(_Strict):
    """A finding: a sentence a person reads, about a place, with a severity and perhaps an action."""
    #: The field holding the sentence a person reads ("summary").
    what_field: str
    #: The field naming the place (scope, column, path); "" for a finding about the resource as a whole.
    where_field: str = ""
    severity_field: str = ""
    #: The closed vocabulary `severity_field` may hold, in words, lowest to highest.
    severities: list[str] = Field(default_factory=list)
    #: The field saying what to do, when the finding proposes an action (published as a RequestForAction).
    action_field: str = ""

    @model_validator(mode="after")
    def _shape(self) -> "FindingDeclaration":
        if not _words(self.what_field):
            raise ValueError("a finding declares no what_field: name the result field holding the sentence a "
                             "person reads")
        if _words(self.severity_field) and not [s for s in self.severities if _words(s)]:
            raise ValueError("a finding declares a severity_field and no severities: list the words it may hold, "
                             "lowest to highest")
        if [s for s in self.severities if _words(s)] and not _words(self.severity_field):
            raise ValueError("a finding declares severities and no severity_field: name the field that holds them")
        return self


class ProposalDeclaration(_Strict):
    """A proposal: a candidate value someone decides on. A proposal is never a fact until it is decided."""
    #: The field holding the proposed value(s).
    candidate_field: str
    #: Who turns it into a fact (owner rule: facts only through Curate).
    decided_by: Literal["curator", "automatic"]
    decision_states: list[str] = Field(default_factory=lambda: ["proposed", "accepted", "rejected"])
    #: The field of each candidate row holding its decision state, when rows carry one; "" means every candidate
    #: is still a proposal.
    state_field: str = ""

    @model_validator(mode="after")
    def _shape(self) -> "ProposalDeclaration":
        if not _words(self.candidate_field):
            raise ValueError("a proposal declares no candidate_field: name the result field holding what is proposed")
        states = [_words(s) for s in self.decision_states]
        if not states or any(not s for s in states):
            raise ValueError("a proposal declares no decision_states, or an empty one: say the states a decision "
                             "moves through, starting with 'proposed'")
        if states[0] != "proposed":
            raise ValueError("a proposal's first decision state is not 'proposed': a candidate starts as a proposal")
        return self


class ListDeclaration(_Strict):
    """A list: items in an order, each told apart by a key."""
    #: The fields a row shows, in order.
    item_fields: list[str]
    #: In words: "largest first".
    order: str
    #: What makes an item one item.
    key_field: str
    #: What the items are, plural, in words ("tables needing attention"), where the key field's name reads badly as
    #: one; empty means the key field's name, spaced.
    item_label: str = ""

    @model_validator(mode="after")
    def _shape(self) -> "ListDeclaration":
        if not [f for f in self.item_fields if _words(f)]:
            raise ValueError("a list declares no item_fields: name the fields a row shows, in order")
        if not _words(self.order):
            raise ValueError("a list declares no order: say it in words, such as 'largest first'")
        if not _words(self.key_field):
            raise ValueError("a list declares no key_field: name what makes an item one item")
        return self


#: B1 (DESIGN-B1-MEMBER-SCOPED-TABLE-RESULT §2): the types a table column may hold. `state` is a closed vocabulary the
#: column declares (`states`); `bytes` and `duration` are numbers whose unit is said.
TABLE_COLUMN_TYPES = ("text", "integer", "number", "boolean", "date", "datetime", "bytes", "duration", "state")

#: The column types that carry a unit, because a number never travels without one.
UNIT_COLUMN_TYPES = ("number", "bytes", "duration")

#: The owner's cap on the rows a table shows (ruling 2026-11-02, "1,000"): a declaration may lower it, never raise it.
#: Every row is stored; the cap is for showing.
TABLE_MAX_ROWS = 1000

#: The tokens one cell of a table costs a chat compile, for the extent a table derives (`TableDeclaration.extent`):
#: the catalogue's hand-priced extents run 7-9 per cell.
TOKENS_PER_CELL = 8

#: The scopes at which a table is about MEMBERS of a resource, so its rows need a key (§2 rule 1).
KEYED_SCOPES = ("member", "field")


class TableColumn(_Strict):
    """One column of a table result: a name, a type, and the word a NULL reads as (§2 rule 2: "not measured" is not
    "none found" is not "not permitted", so each column says its own)."""
    name: str
    type: str = "text"
    #: In words, for a number, bytes or duration column: "rows", "bytes", "seconds".
    unit: str = ""
    #: The word an empty cell reads as ("not counted"). Required on every column that is not a key (checked by the
    #: table, which knows its keys).
    absence: str = ""
    #: A `state` column's closed vocabulary, in words.
    states: list[str] = Field(default_factory=list)
    #: The measure this column is the per-member value of ("nulls_total"), so the summary and the rows agree by
    #: construction; checked against the declaration's measures.
    measure_ref: str = ""

    @model_validator(mode="after")
    def _shape(self) -> "TableColumn":
        if not _words(self.name):
            raise ValueError("a table column declares no name")
        label = f"table column '{self.name}'"
        if self.type not in TABLE_COLUMN_TYPES:
            raise ValueError(f"{label} type '{self.type}' is not one of the column types "
                             f"({', '.join(TABLE_COLUMN_TYPES)})")
        if self.type in UNIT_COLUMN_TYPES and not _words(self.unit):
            raise ValueError(f"{label} is a {self.type} column with no unit: a number never travels without its unit")
        states = [s for s in self.states if _words(s)]
        if self.type == "state" and not states:
            raise ValueError(f"{label} is a state column and declares no states: list the words it may hold")
        if self.type != "state" and self.states:
            raise ValueError(f"{label} declares states and type '{self.type}': states belong to a state column")
        if len(states) != len(set(states)):
            raise ValueError(f"{label} declares a state more than once")
        return self


class TableDeclaration(_Strict):
    """A table: rows about MEMBERS of a resource (columns, files, datasets), each row keyed by what makes the member one
    member, with typed columns, a size the declaration caps and a summary it states (DESIGN-B1 §2). A table with no key
    is a table of one row (the resource as a whole).

    Every row is stored; `max_rows` is how many are SHOWN, and `truncation` says how many were not. The summary measures
    are computed by the step over all rows and declared beside the table, never added up from the shown slice."""
    #: A bare string is a `text` column with no absence word yet (`missing()` names it), which keeps the earliest table
    #: declarations valid.
    columns: list[TableColumn | str]
    #: The columns that make a row one row; required at member or field scope, empty for a table of one row.
    key_fields: list[str] = Field(default_factory=list)
    #: How many rows are shown, at most: the owner's cap, `TABLE_MAX_ROWS`, which a declaration may only lower.
    max_rows: int = TABLE_MAX_ROWS
    #: The order rows are shown in, in words ("schema, table, column as the catalog lists them").
    order: str = ""
    #: Shown with the table; carries `{shown}` and `{total}` ("the first {shown} of {total} columns; every column is
    #: stored and the summary counts them all").
    truncation: str = ""
    #: The measures, declared beside the table, that say the whole of it in one sentence; required at member scope.
    summary: list[str] = Field(default_factory=list)

    @field_validator("columns", mode="before")
    @classmethod
    def _bare_is_text(cls, v):
        return [{"name": c} if isinstance(c, str) else c for c in v] if isinstance(v, list) else v

    @model_validator(mode="after")
    def _shape(self) -> "TableDeclaration":
        names = self.column_names()
        if not names:
            raise ValueError("a table declares no columns")
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:
            raise ValueError(f"a table declares column {', '.join(twice)} more than once")
        missing = [k for k in self.key_fields if k not in names]
        if missing:
            raise ValueError(f"a table's key_fields {', '.join(missing)} are not among its columns")
        if self.max_rows < 1:
            raise ValueError(f"a table declares max_rows {self.max_rows}: a table shows at least one row")
        if self.max_rows > TABLE_MAX_ROWS:
            raise ValueError(f"a table declares max_rows {self.max_rows}: a table shows at most {TABLE_MAX_ROWS:,} "
                             "rows (owner's cap, 2026-11-02); every row is stored, so lower it or leave it out")
        lacking = [p for p in TRUNCATION_PLACEHOLDERS if p not in self.truncation]
        if _words(self.truncation) and lacking:
            raise ValueError(f"a table's truncation sentence '{self.truncation}' lacks {', '.join(lacking)}: it says "
                             "how many rows were shown of how many")
        if len(self.summary) != len(set(self.summary)) or any(not _words(s) for s in self.summary):
            raise ValueError("a table's summary names a measure twice, or an empty one")
        return self

    def is_result(self) -> bool:
        """Is this a table result (B1), the shown slice of stored rows? Said by its truncation sentence; a table without
        one is the earlier shape, which carries its own extents."""
        return bool(_words(self.truncation))

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> TableColumn | None:
        return next((c for c in self.columns if c.name == name), None)

    def unworded(self) -> list[str]:
        """The non-key columns that declare no absence word, in column order."""
        return [c.name for c in self.columns if c.name not in self.key_fields and not _words(c.absence)]

    def fields(self) -> list[str]:
        """The key, then the other columns, in declared order: a row's fields as a reader shows them."""
        return list(self.key_fields) + [n for n in self.column_names() if n not in self.key_fields]

    def extent(self) -> ExtentDeclaration | None:
        """The extent a table is (§5: "a table result's extent IS its table"): its fields, its cap, its order and
        truncation words, priced at `TOKENS_PER_CELL`. None until the table declares an order and a truncation."""
        if not (_words(self.order) and _words(self.truncation)):
            return None
        fields = self.fields()
        return ExtentDeclaration(name="rows", fields=fields, rows=self.max_rows, order=self.order,
                                 truncation=self.truncation, token_cost=self.max_rows * len(fields) * TOKENS_PER_CELL,
                                 citation="{analysis} · {scope} · as of {as_of}")


#: Each non-numeric kind -> the Declaration field that holds its block.
KIND_BLOCKS = {"finding": "finding", "proposal": "proposal", "list": "listing", "table": "table"}


class Declaration(_Strict):
    """What an analysis declares about its results (§1). `result_kind`, `measures` and `absence` are the minimum."""
    result_kind: str
    #: Required when the kind is `table` (§1: "none fits -> table and say so"): why none of the other seven fits.
    result_kind_note: str = ""
    #: Required for the numeric kinds; optional for finding, proposal, list and table, which are told by their block
    #: ("4 findings" is a count that is still welcome).
    measures: list[MeasureDeclaration] = Field(default_factory=list)
    absence: AbsenceSentences
    extents: list[ExtentDeclaration] = Field(default_factory=list)
    confidence: ConfidenceDeclaration | None = None
    #: The question ids this answers. None means not yet declared; [] declares that it answers none, which keeps it
    #: runnable and rendered and out of every chat answer (§3 step 1). For Resource Explorer's own analyses it is
    #: generated from the question catalogue (project owner, 2026-10-28), never written by hand.
    answers: list[str] | None = None
    #: False for an analysis that is registered and declared but has no runner yet (project owner, 2026-10-28:
    #: declare them, marked not built, so they read as "planned, not measured" rather than as nothing).
    built: bool = True
    scope: Literal["resource", "container", "member", "field"] | None = None
    #: Required when `result_kind` is graph or distribution.
    categories: CategoriesDeclaration | None = None
    #: One block per non-numeric kind, required when `result_kind` is that kind and refused on any other.
    finding: FindingDeclaration | None = None
    proposal: ProposalDeclaration | None = None
    #: The `list` kind's block (named so it does not shadow the builtin inside this class).
    listing: ListDeclaration | None = None
    table: TableDeclaration | None = None

    @field_validator("result_kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in RESULT_KINDS:
            raise ValueError(f"result_kind '{v}' is not one of the {len(RESULT_KINDS)} kinds "
                             f"({', '.join(RESULT_KINDS)}); if none fits, declare 'table' and say why in "
                             "result_kind_note")
        return v

    @field_validator("scope", mode="before")
    @classmethod
    def _scope(cls, v):
        if v is not None and v not in SCOPES:
            raise ValueError(f"scope '{v}' is not one of the levels ({', '.join(SCOPES)})")
        return v

    @model_validator(mode="after")
    def _whole(self) -> "Declaration":
        if self.result_kind == "table" and not _words(self.result_kind_note):
            raise ValueError("result_kind 'table' declares no result_kind_note: say why none of the other kinds "
                             "fits")
        if not self.measures and self.result_kind in MEASURED_KINDS:
            raise ValueError(f"declares no measures: a '{self.result_kind}' reports at least one, with its unit and "
                             "as-of")
        self._kind_blocks()
        if self.table is not None:
            self._table()
        names = [m.name for m in self.measures]
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:
            raise ValueError(f"measure {', '.join(twice)} is declared more than once")
        extents = [e.name for e in self.extents]
        twice = sorted({n for n in extents if extents.count(n) > 1})
        if twice:
            raise ValueError(f"extent {', '.join(twice)} is declared more than once")
        if self.answers is not None:
            blank = [a for a in self.answers if not _words(a)]
            if blank:
                raise ValueError("answers carries an empty question id")
        generated = [m.name for m in self.measures if m.annotation and self.confidence_of(m)
                     and self.confidence_of(m).form == "generated"]
        if generated:
            raise ValueError("confidence form 'generated' declares an annotation: a generated answer is not "
                             f"published, so it writes as nothing (measure {', '.join(generated)})")
        own = [m.confidence for m in self.measures if m.confidence is not None]
        if own:
            forms = [c.form for c in own] + ([self.confidence.form] if self.confidence and
                                              len(own) < len(self.measures) else [])
            weakest = max(forms, key=CONFIDENCE_STRENGTH.index)
            if self.confidence is None:
                if len(own) < len(self.measures):
                    raise ValueError("a measure declares its own confidence and the declaration none: the measures "
                                     "without one have no confidence to inherit")
                self.confidence = next(c for c in own if c.form == weakest)
            elif self.confidence.form != weakest:
                raise ValueError(f"confidence form '{self.confidence.form}' is not the weakest of its measures' "
                                 f"('{weakest}'): the declaration's confidence is its weakest link")
        return self

    def _kind_blocks(self) -> None:
        """The block each kind declares: required for its own kind, refused on another, so a declaration says what its
        kind means in words and carries nothing it does not use. Categories likewise for graph and distribution."""
        for kind, attr in KIND_BLOCKS.items():
            block = getattr(self, attr)
            if self.result_kind == kind and block is None:
                need = {"finding": "what_field (and where_field, if it is about a place)", "proposal": "candidate_field and decided_by",
                        "list": "item_fields, order and key_field", "table": "columns and key_fields"}[kind]
                raise ValueError(f"result_kind '{kind}' declares no {attr} block: say {need} so the headline, the "
                                 "publish table and Chat read it from the declaration")
            if self.result_kind != kind and block is not None:
                raise ValueError(f"declares a {attr} block and result_kind '{self.result_kind}': the block belongs "
                                 f"to result_kind '{kind}'")
        if self.result_kind in CATEGORY_KINDS and self.categories is None:
            raise ValueError(f"result_kind '{self.result_kind}' declares no categories: add categories with "
                             "exclusive true (one item in exactly one category) or false (an item may be in several), "
                             "and sums_to naming the result field that holds the stated total, if there is one")
        if self.result_kind not in CATEGORY_KINDS and self.categories is not None:
            raise ValueError(f"declares categories and result_kind '{self.result_kind}': categories belong to "
                             f"{' and '.join(CATEGORY_KINDS)}")

    def _table(self) -> None:
        """A table's rules that need the rest of the declaration (DESIGN-B1 §2-3). At member or field scope its rows
        are keyed. A table that declares its `truncation` is a table RESULT (`is_result`): at member or field scope
        every non-key column says what an empty cell means and it says its order; at member scope it names the measures
        that summarise it; and its extent is derived from it, so one written beside it is refused.

        A table with no truncation is the shape the catalogue's first tables were declared in, before B1: it keeps its
        hand-written extents until it converts (convert-as-touched, project owner 2026-10-31)."""
        t = self.table
        declared = {m.name for m in self.measures}
        if self.scope in KEYED_SCOPES and not t.key_fields:
            raise ValueError(f"a {self.scope}-scoped table declares no key_fields: name the columns that make a row "
                             f"one {self.scope}")
        if self.scope == "member" and not t.is_result():
            raise ValueError("a member-scoped table declares no truncation: say how many rows were shown of how many, "
                             "with {shown} and {total}")
        if not t.is_result():
            return
        if self.scope in KEYED_SCOPES:
            label = f"a {self.scope}-scoped table"
            if t.unworded():
                raise ValueError(f"{label} declares no absence word for column {', '.join(t.unworded())}: say what an "
                                 "empty cell reads as ('not counted', 'not permitted'), never a blank")
            if not _words(t.order):
                raise ValueError(f"{label} declares no order: say in words the order its rows are shown in")
        if self.scope == "member" and not t.summary:
            raise ValueError("a member-scoped table declares no summary: name the measures, declared beside it, that "
                             "say the whole table in one sentence")
        unknown = [s for s in t.summary if s not in declared]
        if unknown:
            raise ValueError(f"a table's summary names {', '.join(unknown)}, which is not a declared measure")
        refs = [f"{c.name} -> {c.measure_ref}" for c in t.columns if _words(c.measure_ref) and c.measure_ref not in declared]
        if refs:
            raise ValueError(f"a table column's measure_ref names no declared measure ({', '.join(refs)})")
        derived = t.extent()
        if self.extents and (derived is None or self.extents != [derived]):
            raise ValueError("declares extents and result_kind 'table': a table's extent is its table, derived from "
                             "its fields, max_rows, order and truncation, so leave extents out")
        self.extents = [derived] if derived is not None else []

    def confidence_of(self, measure: MeasureDeclaration) -> ConfidenceDeclaration | None:
        """A measure's confidence: its own, else the declaration's."""
        return measure.confidence or self.confidence

    def scope_of(self, measure: MeasureDeclaration) -> str | None:
        """A measure's scope: its own, else the declaration's (None when neither is declared)."""
        return measure.scope or self.scope

    def measure(self, name: str) -> MeasureDeclaration | None:
        return next((m for m in self.measures if m.name == name), None)

    def missing(self) -> list[str]:
        """The full-declaration fields this one does not declare yet, in `FULL_FIELDS` order, then a table's own: each
        column with no absence word ("table.columns.b.absence"), its order and its truncation; [] when complete."""
        out = [f for f, absent in (("extents", not self.extents), ("confidence", self.confidence is None),
                                   ("answers", self.answers is None), ("scope", self.scope is None)) if absent]
        if self.table is not None and self.table.is_result():
            out += [f"table.columns.{n}.absence" for n in self.table.unworded()]
            out += ["table.order"] if not _words(self.table.order) else []
        return out

    def is_complete(self) -> bool:
        return not self.missing()


MeasureDeclaration.model_rebuild()


def refusal(exc: Exception) -> str:
    """A validation failure as one sentence per problem, without pydantic's framing ("1 validation error for ..."),
    naming where in the declaration each one is."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)
    parts = []
    for e in errors():
        where = ".".join(str(p) for p in e.get("loc", ()))
        msg = str(e.get("msg", "")).removeprefix("Value error, ")
        if e.get("type") == "extra_forbidden":
            msg = "is not a declaration field"
        elif e.get("type") == "missing":
            msg = "is required"
        parts.append(f"{where} {msg}" if where else msg)
    return "; ".join(parts)
