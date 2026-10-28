"""`data_class_match` and `reference_data_match` — the value-based half of
`postgres_column_profile` (design §5.4, Phase 1 slice 10).

Two questions, from `docs/multi-resource-questions-design.md` §5.4:

- **`data_class_match`** — "Which columns conform to a known Data Class (PII
  and otherwise), with what confidence — and which look like a class we do not
  have yet?"
- **`reference_data_match`** — "Which low-cardinality columns conform to a
  known reference-data set, and which should become one?"

Pure logic: no pyegeria, no psycopg2, no registry. The Egeria elements arrive
as `KnownDataClass` / `KnownValidValueSet` (built by `egeria_reference_catalog.py`
from what the platform actually holds) and the column values arrive as a list
plus the `SampleProvenance` that says where they came from. That split is what
makes every branch below testable with no live Postgres and no live Egeria —
neither of which this build environment has.

**Absence discipline.** The reason this module has a nine-value verdict
vocabulary rather than a boolean is that "no match" has at least four
genuinely different causes, and three of them are not answers:

- nothing was sampled (`catalog_stats_only`, or the engine cannot sample) —
  `MATCH_NOT_SAMPLED`;
- the platform holds no Data Classes / no Valid Value Sets to compare against
  — `MATCH_NO_CANDIDATES`;
- too few distinct values came back to support any claim, or the value list
  was truncated at the configured bound — `MATCH_INCONCLUSIVE`;
- values were read, candidates existed, and none conformed — `MATCH_NO_MATCH`,
  which is the only one of the four that is a *finding*.

Collapsing those into "no match found" is the defect
`DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md` and the
`find-absence-as-answer` skill are both about, in the one place a column-level
PII claim would be read as reassurance.

**Every conformance claim is stated against the sample** (§5.8's second rule).
`ColumnMatch.describe()` refuses to produce an unqualified "conforms": the
fraction and the sample envelope are in the same sentence, always.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from resource_explorer.surveyors.database.sampling import (
    SampleProvenance,
    SamplingConfig,
)

# ── Verdict vocabulary ────────────────────────────────────────────────────────
#
# Extends slice 7's `STATE_MEASURED`/`STATE_NOT_COLLECTED`/`STATE_NOT_SUPPORTED`
# rather than replacing it. Those three answer "did this measurement happen",
# and they are still what `database_column_profiles.state` carries. These
# answer "what did the comparison conclude", which slice 7 had no need for and
# which genuinely has more shapes: a comparison can happen and still establish
# nothing (`MATCH_INCONCLUSIVE`), and it can be impossible for a reason on the
# *Egeria* side rather than the database side (`MATCH_NO_CANDIDATES`). The task
# brief named "sampled but inconclusive" vs "not sampled at all" specifically;
# these are the two constants for it.

#: Values were read, a candidate conformed at or above threshold.
MATCH_MATCHED = "matched"
#: Reference data only: the sampled distinct values are covered by a known set,
#: but not all of them — some values are outside it.
MATCH_PARTIAL = "partial_match"
#: Values were read, candidates existed, nothing conformed. A real finding.
MATCH_NO_MATCH = "no_match"
#: Nothing known conformed, but the values follow a recognisable pattern (or,
#: for reference data, form a small closed set) — the "a class we do not have
#: yet" half of §5.4's question. This is what produces a proposal.
MATCH_UNMATCHED_PATTERNED = "unmatched_patterned"
#: Compared, and the sample cannot support a verdict either way — too few
#: distinct values, or a value list truncated at `max_values`.
MATCH_INCONCLUSIVE = "inconclusive"
#: No value was read at all: `catalog_stats_only`, an absent capability, or a
#: column the sampler could not reach. `not_established`, never "no match".
MATCH_NOT_SAMPLED = "not_sampled"
#: Values were read, but the platform holds nothing to compare them against.
#: Also `not_established`: an empty catalogue cannot produce a negative.
MATCH_NO_CANDIDATES = "no_candidates"
#: The column's type rules it out of this question entirely (reference-data
#: matching against a `bytea`, say). A deliberate exclusion, not a failure.
MATCH_NOT_APPLICABLE = "not_applicable"

#: The verdicts that mean "nothing was established" — the set a UI must render
#: as absence rather than as a negative answer. Grouped here so a consumer gets
#: the distinction by importing one name instead of by remembering four.
NOT_ESTABLISHED_VERDICTS: frozenset[str] = frozenset(
    {MATCH_NOT_SAMPLED, MATCH_NO_CANDIDATES, MATCH_INCONCLUSIVE, MATCH_NOT_APPLICABLE}
)

#: The verdicts that are a positive statement about the data.
ESTABLISHED_VERDICTS: frozenset[str] = frozenset(
    {MATCH_MATCHED, MATCH_PARTIAL, MATCH_NO_MATCH, MATCH_UNMATCHED_PATTERNED}
)


# ── Thresholds: this slice's judgement calls, named and in one place ──────────

#: A value-pattern conformance at or above this fraction of *sampled* values
#: is a match. 0.95 rather than 1.0 because real columns carry a handful of
#: legacy or test rows, and a single malformed email should not prevent a
#: column being recognised as email; rather than 0.80, because a Data Class
#: match drives a PII decision and a fifth of the column disagreeing is not a
#: conformance, it is two populations in one column.
VALUE_MATCH_THRESHOLD = 0.95

#: Below this many distinct sampled values, no value-pattern verdict is
#: attempted: with three values, any regex that fits them fits by luck.
MIN_DISTINCT_VALUES_FOR_VERDICT = 8

#: Below this many sampled values, likewise — a 5-row sample of a 4M-row table
#: says nothing, whatever fraction of it matches.
MIN_SAMPLE_VALUES_FOR_VERDICT = 20

#: Confidence ceiling for a match resting only on the column's NAME and TYPE,
#: because the Data Class carries no value specification to test against.
#: Deliberately below `VALUE_MATCH_THRESHOLD * 100`: name-and-type evidence is
#: a real signal (it is how `pii_scan` worked) and it is not conformance, so it
#: must never be reported at the same confidence as a tested match. §5.8's
#: "never an unqualified 'conforms'" applies to this case most of all — the
#: sample was taken and simply had nothing to be compared with.
NAME_AND_TYPE_ONLY_CONFIDENCE = 60

#: Weighting for the three signals §5.4 names: "column name + type +
#: value-pattern sampling". Value evidence dominates because it is the only one
#: that observes the data; name is the next most specific; type alone barely
#: narrows anything (half a schema is `text`).
WEIGHT_VALUE = 0.70
WEIGHT_NAME = 0.25
WEIGHT_TYPE = 0.05

#: `reference_data_match`'s low-cardinality gate (§5.4: "`n_distinct` ≤
#: threshold"). Both conditions, not either: 40 distinct values is
#: reference-data-shaped in a million-row table and is just "a small table" in
#: a 60-row one.
REFERENCE_DATA_MAX_DISTINCT = 64
REFERENCE_DATA_MAX_DISTINCT_FRACTION = 0.05

#: Coverage at or above this, but below 1.0, is a PARTIAL reference-data match
#: (§5.4: "partial → RFA listing the unmatched values"). Below it, the set is
#: simply not this column's set.
REFERENCE_DATA_PARTIAL_THRESHOLD = 0.60

#: A column whose distinct values are this few and this closed is proposable as
#: a new Valid Value Set even with no pattern to speak of — the values ARE the
#: specification.
MIN_DISTINCT_FOR_SET_PROPOSAL = 2


# ── `n_distinct`: the sign convention ─────────────────────────────────────────

def resolve_n_distinct(
    distinct_count: float | None, total_rows: int | None
) -> int | None:
    """Turn `pg_stats.n_distinct` into an actual distinct-value count.

    Postgres encodes three different things in one signed column, and reading
    it as a plain number is wrong in two of the three cases:

    - **> 0** — the estimated number of distinct values. Use as-is.
    - **< 0** — the negative of the distinct values' *fraction of the row
      count*, which Postgres uses when distinctness scales with table size (a
      unique key's `n_distinct` is `-1`). The real count is
      `-n_distinct * total_rows`, so reading `-1` as a count says "one distinct
      value" — the exact inverse of "every value is distinct", and it would put
      every primary key in the reference-data candidate set.
    - **0** — unknown. `None` here, never 0: a column with zero distinct values
      is an all-NULL column, which is a measurement, and this is the absence of
      one.

    A negative `n_distinct` with no `total_rows` to scale it by is also `None`:
    the fraction is known and the thing it is a fraction OF is not.

    **Provenance note.** Phase 1 slice 9 (`db_derived`) was to publish this as
    `_resolve_n_distinct`, and the brief for this slice says to reuse it rather
    than re-derive it. Slice 9 had not merged when this slice was built
    (`main` was at the probes-4/5 merge), so there was nothing to import — this
    is the same correction, written here as a public name so slice 9 and slice
    11 can import it instead of adding a third copy. If slice 9 lands its own,
    the two must be reconciled to one before both are in the tree; see
    `POSTGRES-COLUMN-PROFILE-IMPLEMENTED.md`.

    **Reconciliation note (round 3 design review, `stats_reset` fix).**
    `database_surveyor._resolve_n_distinct` gained a `stats_reset` parameter
    and a `reason` on its result so the "reltuples == 0, never analyzed"
    ambiguity can be told apart from "reltuples == 0, stats were reset"
    (see its docstring). This function does not need the same fix: its only
    caller (`column_profile_step.run_column_profile`) feeds it
    `profile["distinct_count"]` — the value `_resolve_n_distinct` already
    produced upstream, which is either `None` or a non-negative resolved
    count, never a raw negative-ratio `n_distinct`. The `value < 0` branch
    below never fires in practice; `reltuples`/`ever_analyzed`/`stats_reset`
    are not in scope here because the ambiguity they resolve was already
    settled (or deliberately left `None`) before this function ever sees the
    number. The two are still not reconciled to one (per the note above),
    but that gap and this one are independent — reconciling them is not
    required to close the `stats_reset` gap.
    """
    if distinct_count is None:
        return None
    try:
        value = float(distinct_count)
    except (TypeError, ValueError):
        return None
    if value > 0:
        return int(round(value))
    if value == 0:
        return None
    if not total_rows or total_rows <= 0:
        return None
    return int(round(-value * total_rows))


def is_low_cardinality(
    distinct_values: int | None,
    total_rows: int | None,
    max_distinct: int = REFERENCE_DATA_MAX_DISTINCT,
    max_fraction: float = REFERENCE_DATA_MAX_DISTINCT_FRACTION,
) -> bool | None:
    """§5.4's low-cardinality gate, or `None` when it cannot be decided.

    `None` rather than `False` when the distinct count is unknown: "this
    column is not reference data" and "we do not know how many distinct values
    it has" are different, and only the first is a finding.

    The fraction test is skipped when `total_rows` is unknown or small enough
    that the fraction is meaningless — a 20-row table's every column is
    trivially "5% distinct" and none of them are reference data.
    """
    if distinct_values is None:
        return None
    if distinct_values > max_distinct:
        return False
    if distinct_values < MIN_DISTINCT_FOR_SET_PROPOSAL:
        # One distinct value is a constant column, not a reference set.
        return False
    if total_rows and total_rows >= max_distinct * 4:
        return (distinct_values / float(total_rows)) <= max_fraction
    return True


# ── The pattern library, for "patterned but unmatched" ────────────────────────
#
# §5.4 asks which columns "look like a class we do not have yet". Answering
# that needs a notion of "looks like something" that does not depend on the
# class existing — otherwise the answer is always no. These are the shapes
# common enough to be worth proposing a Data Class for, and each is anchored
# (`fullmatch` semantics) so a pattern cannot match on a substring.

@dataclass(frozen=True)
class ValuePattern:
    """One recognisable value shape."""

    name: str
    regex: str
    #: Prose for the proposal's `specification` / RFA body — a curator reading
    #: the proposal should not have to read the regex to know what was seen.
    description: str
    #: True where a match is a privacy signal in its own right, so a proposal
    #: can be routed to the Privacy perspective rather than only to Steward.
    privacy_relevant: bool = False


VALUE_PATTERNS: tuple[ValuePattern, ...] = (
    ValuePattern(
        "email", r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}",
        "an email address: local part, '@', domain with a dot-separated TLD",
        privacy_relevant=True,
    ),
    ValuePattern(
        "uuid",
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "a hyphenated 36-character UUID",
    ),
    ValuePattern(
        "ipv4", r"(\d{1,3}\.){3}\d{1,3}",
        "a dotted-quad IPv4 address", privacy_relevant=True,
    ),
    ValuePattern(
        "url", r"https?://\S+",
        "an http or https URL",
    ),
    ValuePattern(
        "iso_date", r"\d{4}-\d{2}-\d{2}",
        "an ISO-8601 calendar date (YYYY-MM-DD) held as text",
    ),
    ValuePattern(
        "iso_timestamp", r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?",
        "an ISO-8601 timestamp held as text",
    ),
    ValuePattern(
        "e164_phone", r"\+?\d[\d\s().-]{6,20}\d",
        "a phone-number-shaped string: 8–22 digits with optional +, spaces, "
        "brackets, dots or hyphens",
        privacy_relevant=True,
    ),
    ValuePattern(
        "payment_card", r"(\d[ -]?){12,18}\d",
        "13–19 digits, optionally grouped — a payment-card-shaped number "
        "(Luhn not checked here)",
        privacy_relevant=True,
    ),
    ValuePattern(
        "iban", r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}",
        "an IBAN: two-letter country code, two check digits, up to 30 "
        "alphanumerics",
        privacy_relevant=True,
    ),
    ValuePattern(
        "currency_code", r"[A-Z]{3}",
        "a three-letter uppercase code, ISO-4217 currency shaped",
    ),
    ValuePattern(
        "country_code_alpha2", r"[A-Z]{2}",
        "a two-letter uppercase code, ISO-3166 alpha-2 shaped",
    ),
    ValuePattern(
        "postcode_uk", r"[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}",
        "a UK postcode", privacy_relevant=True,
    ),
    ValuePattern(
        "zip_us", r"\d{5}(-\d{4})?",
        "a US ZIP code, optionally ZIP+4", privacy_relevant=True,
    ),
    ValuePattern(
        "semver", r"\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?",
        "a semantic version string",
    ),
    ValuePattern(
        "hex_digest", r"[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64}",
        "a lowercase hex digest of MD5/SHA-1/SHA-256 length",
    ),
    ValuePattern(
        "prefixed_identifier", r"[A-Za-z]{2,10}[-_]?\d{3,20}",
        "a prefixed identifier: a short alphabetic prefix followed by digits",
    ),
)


def _compiled(regex: str) -> re.Pattern:
    return _PATTERN_CACHE.setdefault(regex, re.compile(regex))


_PATTERN_CACHE: dict[str, re.Pattern] = {}


def pattern_conformance(values: Sequence[Any], regex: str) -> float:
    """Fraction of `values` that fully match `regex`.

    `fullmatch`, not `search`: "conforms to a pattern" means the value IS the
    thing, not that it contains one. A `search`-based email pattern matches
    every free-text comment that happens to mention an address, which would
    classify a notes column as PII and bury the columns that actually are.

    An invalid regex (they arrive from Egeria Data Class specifications, which
    are authored by people) yields 0.0 rather than raising — one malformed
    class must not take out the whole column's matching. The caller reports it
    via `unusable_specifications`.
    """
    if not values:
        return 0.0
    try:
        compiled = _compiled(regex)
    except re.error:
        return 0.0
    hits = 0
    for value in values:
        if value is None:
            continue
        if compiled.fullmatch(str(value).strip()):
            hits += 1
    return hits / float(len(values))


def pattern_conformance_any(
    values: Sequence[Any], regexes: Sequence[str]
) -> tuple[float, list[str]]:
    """Fraction of `values` matching AT LEAST ONE of `regexes`, plus the
    regexes that would not compile.

    Egeria's `dataPatterns` is a list of alternatives, so a class declaring
    three patterns is conformed to by a value matching any of them. Computing
    the max of the per-pattern fractions would be wrong: a column half
    `+44...` and half `(555) ...` conforms fully to a two-pattern phone class
    and only 50% to each pattern individually.
    """
    usable: list[re.Pattern] = []
    unusable: list[str] = []
    for regex in regexes:
        if not regex:
            continue
        try:
            usable.append(_compiled(regex))
        except re.error:
            unusable.append(regex)
    if not usable or not values:
        return 0.0, unusable
    hits = 0
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if any(p.fullmatch(text) for p in usable):
            hits += 1
    return hits / float(len(values)), unusable


def detect_patterns(values: Sequence[Any]) -> list[tuple[ValuePattern, float]]:
    """Every library pattern the sample conforms to, best first.

    Several can match at once and that is information, not noise: a column of
    `+441234567890` matches `e164_phone` and, depending on grouping,
    `payment_card`. Returning both, ranked, lets the proposal say what was seen
    and lets a curator disambiguate — picking one silently is how a phone
    column becomes a "card number" finding.
    """
    scored = [
        (pattern, pattern_conformance(values, pattern.regex))
        for pattern in VALUE_PATTERNS
    ]
    hits = [(p, s) for p, s in scored if s >= VALUE_MATCH_THRESHOLD]
    hits.sort(key=lambda pair: (-pair[1], pair[0].name))
    return hits


# ── What a known Egeria Data Class looks like, to this module ─────────────────

@dataclass(frozen=True)
class KnownDataClass:
    """An Egeria `DataClass` already in the platform, reduced to what matching
    needs. Built by `egeria_reference_catalog.data_classes_from_egeria`.
    """

    guid: str
    qualified_name: str
    display_name: str
    #: `DataClassProperties.namespacePath` — the field is `namespacePath`, not
    #: `namespace` (verified against pyegeria's own REST reference,
    #: `Egeria-api-data-designer.http`'s `createDataClass`).
    namespace_path: str = ""
    #: `DataClassProperties.specification` — a regex, where the class has one.
    specification: str = ""
    #: `DataClassProperties.dataPatterns` — a LIST of acceptable patterns. The
    #: real field name is `dataPatterns`; there is no `valuePattern` on this
    #: type. A value conforming to any one of them conforms to the class.
    data_patterns: tuple[str, ...] = ()
    #: `DataClassProperties.dataType` — the type the class expects.
    data_type: str = ""
    #: `DataClassProperties.matchPropertyNames` — column names this class
    #: expects to be called. The name half of §5.4's "name + type + value".
    match_property_names: tuple[str, ...] = ()
    #: `DataClassProperties.valueList` / `sampleValues` — an enumeration, where
    #: the class is defined by its values rather than by a pattern.
    value_list: tuple[str, ...] = ()
    #: The class's own `contentStatus`. A DRAFT class is a *proposal*, possibly
    #: one RE itself made on an earlier run, and matching against it as though
    #: it were confirmed would let a guess ratify itself. See
    #: `data_class_match`'s `include_draft_classes`.
    content_status: str = ""

    @property
    def is_draft(self) -> bool:
        return (self.content_status or "").upper() == "DRAFT"

    @property
    def value_regexes(self) -> tuple[str, ...]:
        """Every regex this class declares, `specification` first.

        Egeria allows both a single `specification` and a list of
        `dataPatterns`, and a value conforming to any one of them conforms to
        the class — so conformance is computed over the union, not over the
        first one found.
        """
        return tuple(
            r for r in (self.specification, *self.data_patterns) if r
        )


@dataclass(frozen=True)
class KnownValidValueSet:
    """An Egeria `ValidValueSet` and its members' values."""

    guid: str
    qualified_name: str
    display_name: str
    #: The member `ValidValueDefinition`s' `preferredValue`s.
    values: tuple[str, ...] = ()
    content_status: str = ""

    @property
    def is_draft(self) -> bool:
        return (self.content_status or "").upper() == "DRAFT"


# ── Name and type signals ─────────────────────────────────────────────────────

_NAME_SPLIT_RE = re.compile(r"[^a-z0-9]+")
#: camelCase / PascalCase boundaries, split BEFORE lower-casing.
#:
#: Without this, `emailAddress` lower-cases to the single token
#: `emailaddress`, which shares nothing with `email_address`'s two tokens —
#: so a Data Class authored as `emailAddress` scored 0.0 against a column
#: called `email_address` and vice versa. Both spellings are ordinary
#: (Egeria's own property names are camelCase; Postgres columns are
#: snake_case), so this was the common case rather than an edge one.
#: Handles the acronym run too: `customerGUIDRef` → `customer GUID Ref`.
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _name_tokens(name: str) -> set[str]:
    split = _CAMEL_BOUNDARY_RE.sub(" ", name or "")
    return {t for t in _NAME_SPLIT_RE.split(split.lower()) if t}


def name_similarity(column_name: str, candidate_names: Iterable[str]) -> float:
    """How well a column's name matches any of a class's expected names.

    Token-set based rather than a string distance: `home_email_address` and
    `emailAddress` share the tokens that matter and share almost no characters
    in the same positions. 1.0 for an exact token-set match, otherwise the best
    Jaccard overlap, 0.0 when the class declares no names at all (which is the
    honest answer — an absent expectation is not a failed one, and the caller
    weights it accordingly).
    """
    column_tokens = _name_tokens(column_name)
    if not column_tokens:
        return 0.0
    best = 0.0
    for candidate in candidate_names:
        tokens = _name_tokens(candidate)
        if not tokens:
            continue
        if tokens == column_tokens:
            return 1.0
        union = tokens | column_tokens
        if not union:
            continue
        best = max(best, len(tokens & column_tokens) / float(len(union)))
    return best


#: Postgres type names grouped to the families a Data Class's `dataType`
#: realistically names. Deliberately coarse: `varchar`/`text`/`char` are one
#: family because a Data Class authored as `string` must match all three.
_TYPE_FAMILIES: dict[str, str] = {
    "text": "string", "varchar": "string", "character varying": "string",
    "char": "string", "character": "string", "citext": "string", "name": "string",
    "bpchar": "string",
    "int": "integer", "int2": "integer", "int4": "integer", "int8": "integer",
    "smallint": "integer", "integer": "integer", "bigint": "integer",
    "serial": "integer", "bigserial": "integer",
    "numeric": "decimal", "decimal": "decimal", "real": "decimal",
    "double precision": "decimal", "float4": "decimal", "float8": "decimal",
    "money": "decimal",
    "bool": "boolean", "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp", "timestamptz": "timestamp",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamp",
    "time": "time", "timetz": "time",
    "uuid": "uuid",
    "json": "json", "jsonb": "json", "xml": "xml",
    "bytea": "binary",
    "inet": "string", "cidr": "string", "macaddr": "string",
}

#: Types no value-pattern question applies to. A `bytea` column is not "not
#: matched" — the question does not apply to it (`MATCH_NOT_APPLICABLE`).
UNMATCHABLE_TYPE_FAMILIES: frozenset[str] = frozenset({"binary"})


def type_family(data_type: str) -> str:
    """Normalise a column or Data Class type to a coarse family, or "" when
    unrecognised. "" is not a family: it means the type was not understood,
    and a caller must not read it as a mismatch.
    """
    raw = (data_type or "").strip().lower()
    if not raw:
        return ""
    # Strip a length/precision suffix and any array marker.
    raw = re.sub(r"\(.*?\)", "", raw).replace("[]", "").strip()
    return _TYPE_FAMILIES.get(raw, "")


def type_compatibility(column_type: str, class_type: str) -> float | None:
    """1.0 same family, 0.0 different, `None` when either side is unknown.

    `None` matters: a Data Class with no declared `dataType` has not declared
    an incompatible one, and scoring that as 0.0 would penalise every
    loosely-authored class in the platform.
    """
    column_family = type_family(column_type)
    class_family = type_family(class_type)
    if not column_family or not class_family:
        return None
    return 1.0 if column_family == class_family else 0.0


# ── A match result ────────────────────────────────────────────────────────────

@dataclass
class ColumnMatch:
    """One column's verdict for one of the two questions.

    `provenance` is not optional in practice: `describe()` uses it to qualify
    every claim, and a `ColumnMatch` built without one describes itself as
    unqualified — which `test_column_matching.py` pins, because an
    unqualified "conforms" is the specific output §5.8's second rule forbids.
    """

    schema_name: str
    table_name: str
    column_name: str
    question: str                       # "data_class_match" | "reference_data_match"
    verdict: str
    confidence: int = 0
    #: The matched element's GUID / qualifiedName / display name, when matched.
    matched_guid: str = ""
    matched_qualified_name: str = ""
    matched_display_name: str = ""
    #: Fraction of SAMPLED values that conformed. `None` where no value test
    #: ran (a name-and-type-only match, or an unestablished verdict).
    sampled_conformance: float | None = None
    #: Reference data: fraction of the sampled DISTINCT values the set covers.
    value_coverage: float | None = None
    #: Reference data, partial match: the sampled values the set does not hold.
    unmatched_values: list[str] = field(default_factory=list)
    #: Proposal payload, when the verdict is `MATCH_UNMATCHED_PATTERNED`.
    proposed_specification: str = ""
    proposed_specification_prose: str = ""
    proposed_values: list[str] = field(default_factory=list)
    detected_patterns: list[str] = field(default_factory=list)
    privacy_relevant: bool = False
    #: Runners-up, for a curator deciding between two plausible classes.
    other_candidates: list[dict[str, Any]] = field(default_factory=list)
    #: Why nothing was established, in prose, for an unestablished verdict.
    not_established_reason: str = ""
    #: The evidence the verdict rests on: "value_pattern" / "name_and_type" /
    #: "value_enumeration" / "" — so a reader can tell a tested conformance
    #: from a naming convention at a glance, without comparing confidences.
    evidence: str = ""
    provenance: SampleProvenance | None = None
    #: Data Class specifications that would not compile, named so a bad class
    #: in the platform is visible rather than silently scoring 0.
    unusable_specifications: list[str] = field(default_factory=list)
    #: True when the matched element itself carries `contentStatus: DRAFT` —
    #: i.e. this column was matched against a PROPOSAL, not a confirmed class.
    matched_element_is_draft: bool = False

    @property
    def column_path(self) -> str:
        return f"{self.schema_name}.{self.table_name}.{self.column_name}"

    @property
    def established(self) -> bool:
        return self.verdict in ESTABLISHED_VERDICTS

    def describe(self) -> str:
        """The finding, with §5.8's qualification built in.

        There is no code path through this method that produces the word
        "conforms" without a fraction and a sample envelope beside it.
        """
        envelope = self.provenance.describe() if self.provenance else (
            "with no sample provenance recorded — this claim is unqualified "
            "and should not be relied on"
        )
        if self.verdict == MATCH_NOT_SAMPLED:
            return (
                f"{self.column_path}: not established — "
                f"{self.not_established_reason or 'no values were sampled'}. "
                f"This is not a statement that no match exists."
            )
        if self.verdict == MATCH_NO_CANDIDATES:
            return (
                f"{self.column_path}: not established — "
                f"{self.not_established_reason or 'nothing to compare against'}. "
                f"{envelope}."
            )
        if self.verdict == MATCH_INCONCLUSIVE:
            return (
                f"{self.column_path}: inconclusive — "
                f"{self.not_established_reason or 'the sample cannot support a verdict'}. "
                f"{envelope}."
            )
        if self.verdict == MATCH_NOT_APPLICABLE:
            return (
                f"{self.column_path}: not applicable — "
                f"{self.not_established_reason or 'this question does not apply to this column'}."
            )
        if self.verdict == MATCH_NO_MATCH:
            return (
                f"{self.column_path}: no known "
                f"{'Data Class' if self.question == 'data_class_match' else 'Valid Value Set'} "
                f"matched, {envelope}."
            )
        if self.verdict == MATCH_UNMATCHED_PATTERNED:
            what = (
                f"follows a recognisable pattern ({', '.join(self.detected_patterns)})"
                if self.detected_patterns
                else "forms a small closed set of values"
            )
            return (
                f"{self.column_path}: no known match, but the column {what} "
                f"— proposing a new "
                f"{'Data Class' if self.question == 'data_class_match' else 'Valid Value Set'}. "
                f"{envelope}."
            )
        if self.verdict == MATCH_PARTIAL:
            coverage = f"{self.value_coverage:.2f}" if self.value_coverage is not None else "an unrecorded fraction"
            return (
                f"{self.column_path}: partially conforms to Valid Value Set "
                f"'{self.matched_display_name}' — the set covers {coverage} of "
                f"the sampled distinct values; {len(self.unmatched_values)} "
                f"are outside it. {envelope}."
            )
        # MATCH_MATCHED
        draft_note = (
            " (NOTE: the matched element is itself a DRAFT proposal, not "
            "confirmed content)" if self.matched_element_is_draft else ""
        )
        if self.evidence == "name_and_type":
            return (
                f"{self.column_path}: matches "
                f"'{self.matched_display_name}' on column name and type only — "
                f"that class carries no value specification, so no value "
                f"conformance was tested. {envelope}{draft_note}."
            )
        if self.sampled_conformance is not None:
            return (
                f"{self.column_path}: conforms to "
                f"'{self.matched_display_name}' with "
                f"{self.sampled_conformance:.2f} of sampled values, {envelope}"
                f"{draft_note}."
            )
        coverage = f"{self.value_coverage:.2f}" if self.value_coverage is not None else "an unrecorded fraction"
        return (
            f"{self.column_path}: conforms to Valid Value Set "
            f"'{self.matched_display_name}' — the set covers {coverage} of the "
            f"sampled distinct values, {envelope}{draft_note}."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema_name,
            "table": self.table_name,
            "column": self.column_name,
            #: The fully-qualified column, so a consumer can key on one field
            #: rather than rebuilding it from three and having to know the
            #: separator. It is also what `item_key` on every annotation from
            #: this step carries, so the two join without a transformation.
            "column_path": self.column_path,
            "question": self.question,
            "verdict": self.verdict,
            "established": self.established,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "matched_guid": self.matched_guid,
            "matched_qualified_name": self.matched_qualified_name,
            "matched_display_name": self.matched_display_name,
            "matched_element_is_draft": self.matched_element_is_draft,
            "sampled_conformance": self.sampled_conformance,
            "value_coverage": self.value_coverage,
            "unmatched_values": list(self.unmatched_values),
            "detected_patterns": list(self.detected_patterns),
            "privacy_relevant": self.privacy_relevant,
            "proposed_specification": self.proposed_specification,
            "proposed_values": list(self.proposed_values),
            "other_candidates": list(self.other_candidates),
            "not_established_reason": self.not_established_reason,
            "unusable_specifications": list(self.unusable_specifications),
            "sample": self.provenance.as_dict() if self.provenance else None,
            "statement": self.describe(),
        }


# ── data_class_match ──────────────────────────────────────────────────────────

def data_class_match(
    *,
    schema_name: str,
    table_name: str,
    column_name: str,
    column_type: str,
    known_classes: Sequence[KnownDataClass],
    sampled_values: Sequence[Any] | None,
    provenance: SampleProvenance,
    include_draft_classes: bool = False,
) -> ColumnMatch:
    """§5.4: which columns conform to a known Data Class, with what confidence
    — and which look like a class we do not have yet?

    `sampled_values is None` means no sample was taken (the caller had
    `catalog_stats_only`, or the engine could not sample, or the byte budget
    ran out). That is `MATCH_NOT_SAMPLED` and it is emphatically not
    `MATCH_NO_MATCH`: a PII question answered "no match" when no value was
    ever read is a confident wrong answer on the one topic where a confident
    wrong answer costs the most.

    An EMPTY list is different again and is handled honestly too: the sample
    ran and returned nothing (an all-NULL column), which cannot support a
    verdict — `MATCH_INCONCLUSIVE`.

    `include_draft_classes` defaults False. A `contentStatus: DRAFT` Data
    Class in the platform is a proposal, quite possibly one an earlier run of
    this very step made. Matching against it by default would let RE ratify its
    own guess: run one proposes `email_like`, run two reports the column
    "conforms to email_like", and the proposal has become a finding with no
    curator in the loop. When it IS enabled, the match records
    `matched_element_is_draft` and `describe()` says so in the sentence.
    """
    base = dict(
        schema_name=schema_name, table_name=table_name, column_name=column_name,
        question="data_class_match", provenance=provenance,
    )

    if type_family(column_type) in UNMATCHABLE_TYPE_FAMILIES:
        return ColumnMatch(
            **base, verdict=MATCH_NOT_APPLICABLE,
            not_established_reason=(
                f"column type {column_type!r} holds opaque binary data, which "
                f"no value-pattern Data Class describes"
            ),
        )

    if sampled_values is None:
        return ColumnMatch(
            **base, verdict=MATCH_NOT_SAMPLED,
            not_established_reason=(
                provenance.reason_not_sampled
                or "no values were read from this column"
            ),
        )

    values = [v for v in sampled_values if v is not None]
    distinct = {str(v).strip() for v in values}

    candidates = [
        c for c in known_classes if include_draft_classes or not c.is_draft
    ]
    if not candidates:
        draft_note = ""
        drafts = [c for c in known_classes if c.is_draft]
        if drafts and not include_draft_classes:
            draft_note = (
                f" ({len(drafts)} DRAFT proposal(s) exist and were excluded — "
                f"a proposal is not a confirmed class to match against)"
            )
        return ColumnMatch(
            **base, verdict=MATCH_NO_CANDIDATES,
            not_established_reason=(
                "the Egeria platform holds no confirmed Data Classes to "
                f"compare against{draft_note}, so 'no match' cannot be "
                "concluded from this run"
            ),
        )

    if len(values) < MIN_SAMPLE_VALUES_FOR_VERDICT or len(distinct) < MIN_DISTINCT_VALUES_FOR_VERDICT:
        return ColumnMatch(
            **base, verdict=MATCH_INCONCLUSIVE,
            not_established_reason=(
                f"the sample returned {len(values)} value(s) and "
                f"{len(distinct)} distinct value(s) — below the "
                f"{MIN_SAMPLE_VALUES_FOR_VERDICT}-value / "
                f"{MIN_DISTINCT_VALUES_FOR_VERDICT}-distinct floor below which "
                f"any pattern fits by chance"
            ),
        )

    # Score every candidate on §5.4's three signals.
    unusable: list[str] = []
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        name_score = name_similarity(
            column_name, list(candidate.match_property_names) + [candidate.display_name]
        )
        type_score = type_compatibility(column_type, candidate.data_type)
        value_score: float | None = None
        regexes = candidate.value_regexes
        if regexes:
            value_score, bad = pattern_conformance_any(values, regexes)
            if bad:
                unusable.append(candidate.qualified_name or candidate.display_name)
            if len(bad) == len(regexes):
                # Every pattern this class declares is unusable, so nothing was
                # actually tested. Fall back to the class's value list if it has
                # one, else to name-and-type evidence — but never report a 0.0
                # conformance, which would read as "tested and failed".
                value_score = None
        if value_score is None and candidate.value_list:
            allowed = {str(v).strip().lower() for v in candidate.value_list}
            matched = sum(1 for v in values if str(v).strip().lower() in allowed)
            value_score = matched / float(len(values))

        # A declared, tested, FAILING value specification is disqualifying
        # whatever the name says. A column called `email` full of integers is
        # not an email column, and the name is exactly the evidence that would
        # otherwise carry it over the line.
        if value_score is not None and value_score < VALUE_MATCH_THRESHOLD:
            scored.append({
                "candidate": candidate, "eligible": False,
                "value_score": value_score, "name_score": name_score,
                "type_score": type_score,
            })
            continue
        # A declared, tested, FAILING type is disqualifying for the same reason.
        if type_score == 0.0:
            scored.append({
                "candidate": candidate, "eligible": False,
                "value_score": value_score, "name_score": name_score,
                "type_score": type_score,
            })
            continue

        if value_score is None:
            # Name-and-type-only evidence. Real, and capped — see
            # NAME_AND_TYPE_ONLY_CONFIDENCE. Requires a genuine name signal:
            # without one there is no evidence at all, only a type that half
            # the schema shares.
            if name_score < 0.5:
                scored.append({
                    "candidate": candidate, "eligible": False,
                    "value_score": None, "name_score": name_score,
                    "type_score": type_score,
                })
                continue
            confidence = int(round(NAME_AND_TYPE_ONLY_CONFIDENCE * name_score))
            scored.append({
                "candidate": candidate, "eligible": True, "evidence": "name_and_type",
                "value_score": None, "name_score": name_score,
                "type_score": type_score, "confidence": confidence,
            })
            continue

        weighted = (
            WEIGHT_VALUE * value_score
            + WEIGHT_NAME * name_score
            + WEIGHT_TYPE * (1.0 if type_score is None else type_score)
        )
        scored.append({
            "candidate": candidate, "eligible": True,
            "evidence": "value_pattern" if candidate.value_regexes else "value_enumeration",
            "value_score": value_score, "name_score": name_score,
            "type_score": type_score, "confidence": int(round(100 * weighted)),
        })

    eligible = [s for s in scored if s.get("eligible")]
    eligible.sort(key=lambda s: (-s["confidence"], s["candidate"].display_name))

    if eligible:
        best = eligible[0]
        candidate: KnownDataClass = best["candidate"]
        return ColumnMatch(
            **base, verdict=MATCH_MATCHED,
            confidence=best["confidence"],
            evidence=best["evidence"],
            matched_guid=candidate.guid,
            matched_qualified_name=candidate.qualified_name,
            matched_display_name=candidate.display_name,
            matched_element_is_draft=candidate.is_draft,
            sampled_conformance=best["value_score"],
            other_candidates=[
                {
                    "display_name": s["candidate"].display_name,
                    "qualified_name": s["candidate"].qualified_name,
                    "confidence": s["confidence"],
                    "sampled_conformance": s["value_score"],
                }
                for s in eligible[1:4]
            ],
            unusable_specifications=unusable,
        )

    # Nothing known matched. Does it look like a class we do not have?
    detected = detect_patterns(values)
    if detected:
        pattern, score = detected[0]
        return ColumnMatch(
            **base, verdict=MATCH_UNMATCHED_PATTERNED,
            confidence=int(round(100 * score)),
            evidence="value_pattern",
            sampled_conformance=score,
            detected_patterns=[p.name for p, _ in detected],
            privacy_relevant=any(p.privacy_relevant for p, _ in detected),
            proposed_specification=pattern.regex,
            proposed_specification_prose=pattern.description,
            proposed_values=sorted(distinct)[:20],
            unusable_specifications=unusable,
        )

    return ColumnMatch(
        **base, verdict=MATCH_NO_MATCH,
        confidence=100,
        evidence="value_pattern",
        not_established_reason="",
        unusable_specifications=unusable,
    )


# ── reference_data_match ──────────────────────────────────────────────────────

def reference_data_match(
    *,
    schema_name: str,
    table_name: str,
    column_name: str,
    column_type: str,
    known_sets: Sequence[KnownValidValueSet],
    distinct_values: Sequence[Any] | None,
    distinct_count: int | None,
    total_rows: int | None,
    provenance: SampleProvenance,
    include_draft_sets: bool = False,
) -> ColumnMatch:
    """§5.4: which low-cardinality columns conform to a known reference-data
    set, and which should become one?

    `distinct_count` is the resolved distinct-value count — pass
    `resolve_n_distinct(profile["distinct_count"], total_rows)`, reusing slice
    7's stored `database_column_profiles.distinct_count` rather than counting
    a second way. `None` means the count is unknown, which gates the question
    rather than answering it: a column whose cardinality nobody measured is
    not "not reference data".

    Four outcomes, per §5.4: a full match → a `ValidValuesAssignment` advice;
    a partial match → the unmatched values named, for an RFA; no matching set
    → a proposal for a new set; and, over all of them, the absence states.
    """
    base = dict(
        schema_name=schema_name, table_name=table_name, column_name=column_name,
        question="reference_data_match", provenance=provenance,
    )

    if type_family(column_type) in UNMATCHABLE_TYPE_FAMILIES:
        return ColumnMatch(
            **base, verdict=MATCH_NOT_APPLICABLE,
            not_established_reason=(
                f"column type {column_type!r} holds opaque binary data, which "
                f"cannot be a reference-data set's member value"
            ),
        )

    low = is_low_cardinality(distinct_count, total_rows)
    if low is None:
        return ColumnMatch(
            **base, verdict=MATCH_NOT_SAMPLED,
            not_established_reason=(
                "this column's distinct-value count is not known — pg_stats "
                "has no n_distinct for it (ANALYZE may never have run) and no "
                "sample established one, so whether it is low-cardinality is "
                "undetermined"
            ),
        )
    if low is False:
        return ColumnMatch(
            **base, verdict=MATCH_NOT_APPLICABLE,
            not_established_reason=(
                f"{distinct_count} distinct values is outside the "
                f"reference-data gate (at most {REFERENCE_DATA_MAX_DISTINCT} "
                f"distinct, and at most "
                f"{REFERENCE_DATA_MAX_DISTINCT_FRACTION:.0%} of rows) — this "
                f"column is not reference-data shaped"
            ),
        )

    if distinct_values is None:
        return ColumnMatch(
            **base, verdict=MATCH_NOT_SAMPLED,
            not_established_reason=(
                provenance.reason_not_sampled
                or "the column is low-cardinality, but no values were read, so "
                "there is nothing to compare against a Valid Value Set"
            ),
        )

    sampled = sorted({str(v).strip() for v in distinct_values if v is not None})
    if not sampled:
        return ColumnMatch(
            **base, verdict=MATCH_INCONCLUSIVE,
            not_established_reason=(
                "the sample returned no non-NULL values, so the column's value "
                "set is unknown even though its cardinality is low"
            ),
        )

    if provenance.truncated:
        # A truncated distinct list cannot support a COVERAGE claim in either
        # direction: values outside the set may simply not have been fetched,
        # and a "no matching set" conclusion may be about the wrong values.
        return ColumnMatch(
            **base, verdict=MATCH_INCONCLUSIVE,
            not_established_reason=(
                f"the distinct-value list hit the configured per-column bound "
                f"and was truncated at {len(sampled)} values — a coverage "
                f"fraction computed from an incomplete list would be wrong in "
                f"an unknown direction"
            ),
        )

    candidates = [s for s in known_sets if include_draft_sets or not s.is_draft]
    if not candidates:
        drafts = [s for s in known_sets if s.is_draft]
        draft_note = (
            f" ({len(drafts)} DRAFT proposal(s) exist and were excluded)"
            if drafts and not include_draft_sets else ""
        )
        # Still worth proposing: the column IS reference-data shaped, and the
        # reason nothing matched is that the platform has nothing, which is
        # exactly §5.4's "which should become one?".
        return ColumnMatch(
            **base, verdict=MATCH_UNMATCHED_PATTERNED,
            confidence=100,
            evidence="value_enumeration",
            not_established_reason=(
                "the Egeria platform holds no confirmed Valid Value Sets to "
                f"compare against{draft_note} — so this is a proposal, not a "
                "finding that no set fits"
            ),
            proposed_values=sampled,
            value_coverage=None,
        )

    best: dict[str, Any] | None = None
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        allowed = {str(v).strip().lower() for v in candidate.values}
        if not allowed:
            continue
        covered = [v for v in sampled if v.lower() in allowed]
        coverage = len(covered) / float(len(sampled))
        entry = {
            "candidate": candidate,
            "coverage": coverage,
            "unmatched": [v for v in sampled if v.lower() not in allowed],
        }
        ranked.append(entry)
        if best is None or coverage > best["coverage"]:
            best = entry

    if best is None:
        return ColumnMatch(
            **base, verdict=MATCH_NO_CANDIDATES,
            not_established_reason=(
                f"{len(candidates)} Valid Value Set(s) exist but none has any "
                f"member values readable from this platform, so there is "
                f"nothing to compare the column's values against"
            ),
        )

    ranked.sort(key=lambda e: (-e["coverage"], e["candidate"].display_name))
    others = [
        {
            "display_name": e["candidate"].display_name,
            "qualified_name": e["candidate"].qualified_name,
            "value_coverage": e["coverage"],
        }
        for e in ranked[1:4]
    ]
    candidate = best["candidate"]

    if best["coverage"] >= 1.0:
        return ColumnMatch(
            **base, verdict=MATCH_MATCHED,
            confidence=100,
            evidence="value_enumeration",
            matched_guid=candidate.guid,
            matched_qualified_name=candidate.qualified_name,
            matched_display_name=candidate.display_name,
            matched_element_is_draft=candidate.is_draft,
            value_coverage=1.0,
            other_candidates=others,
        )

    if best["coverage"] >= REFERENCE_DATA_PARTIAL_THRESHOLD:
        return ColumnMatch(
            **base, verdict=MATCH_PARTIAL,
            confidence=int(round(100 * best["coverage"])),
            evidence="value_enumeration",
            matched_guid=candidate.guid,
            matched_qualified_name=candidate.qualified_name,
            matched_display_name=candidate.display_name,
            matched_element_is_draft=candidate.is_draft,
            value_coverage=best["coverage"],
            unmatched_values=best["unmatched"],
            other_candidates=others,
        )

    # Low-cardinality, closed, and no set fits: propose one (§5.4).
    return ColumnMatch(
        **base, verdict=MATCH_UNMATCHED_PATTERNED,
        confidence=int(round(100 * (1.0 - best["coverage"]))),
        evidence="value_enumeration",
        proposed_values=sampled,
        value_coverage=best["coverage"],
        other_candidates=others,
        not_established_reason="",
    )


def sampling_config_summary(config: SamplingConfig) -> dict[str, Any]:
    """The config, for an annotation's properties.

    Here rather than on `SamplingConfig` so `sampling.py` stays free of any
    notion of what an annotation is.
    """
    return config.as_dict()
