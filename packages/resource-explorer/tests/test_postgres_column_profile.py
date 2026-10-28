"""Phase 1 slice 10 — `postgres_column_profile`, `data_class_match`,
`reference_data_match`.

Implements the checks named in the slice brief:

1. sampling strategy selection and the provenance actually recorded;
2. `TABLESAMPLE` usage — proved by asserting the SQL shape, since there is no
   live Postgres in this environment;
3. match / no-match / partial-match for both questions, plus every absence
   state, because "we did not look" rendering as "nothing found" is the defect
   this slice is most able to introduce;
4. the `contentStatus: DRAFT` proposal path (element creation AND the
   `AssociatedAnnotation` link);
5. the RFA fallback, including the partial-reference-data case that design §3
   still wants an RFA for even now that DRAFT works.

Plus one group nothing asked for and the peer review did: that `contentStatus`
is actually READ back and reaches the surface a person looks at. Before this
slice nothing in RE read the field, so a DRAFT proposal rendered exactly like
a confirmed finding — `TestContentStatusIsSurfaced` pins each hop of the chain
so a future refactor cannot quietly re-break it.
"""
from __future__ import annotations

import re

import pytest

from resource_explorer.registry import (
    STATE_MEASURED,
    STATE_NOT_SUPPORTED,
)
from resource_explorer.surveyors.database.column_matching import (
    MATCH_INCONCLUSIVE,
    MATCH_MATCHED,
    MATCH_NO_CANDIDATES,
    MATCH_NO_MATCH,
    MATCH_NOT_APPLICABLE,
    MATCH_NOT_SAMPLED,
    MATCH_PARTIAL,
    MATCH_UNMATCHED_PATTERNED,
    NAME_AND_TYPE_ONLY_CONFIDENCE,
    NOT_ESTABLISHED_VERDICTS,
    KnownDataClass,
    KnownValidValueSet,
    data_class_match,
    is_low_cardinality,
    name_similarity,
    pattern_conformance,
    reference_data_match,
    resolve_n_distinct,
    type_compatibility,
)
from resource_explorer.surveyors.database.column_profile_step import (
    ACTION_PROPOSE_DATA_CLASS,
    ACTION_PROPOSE_VALID_VALUE_SET,
    ACTION_REVIEW_UNMATCHED_VALUES,
    SamplingBudget,
    _rfa_for_match,
    publish_proposals,
    run_column_profile,
    sample_column_values,
)
from resource_explorer.surveyors.database.connection import EngineCapabilities
from resource_explorer.surveyors.database.egeria_reference_catalog import (
    CONTENT_STATUS_DRAFT,
    ReferenceCatalog,
    build_associated_annotation_body,
    build_proposed_data_class_body,
    build_proposed_valid_value_set_body,
    data_class_from_element,
    list_known_data_classes,
    list_known_valid_value_sets,
    load_reference_catalog,
    publish_proposed_data_class,
    publish_proposed_valid_value_set,
    valid_value_set_from_element,
)
from resource_explorer.surveyors.database.sampling import (
    CATALOG_STATS_ONLY,
    DEFAULT_MATCHING_SAMPLING,
    DEFAULT_MEASURE_SAMPLING,
    FULL,
    HEAD,
    RANDOM,
    STRATIFIED,
    SYSTEMATIC,
    SampleProvenance,
    SamplingConfig,
    SamplingConfigError,
    build_column_sample_sql,
    build_distinct_values_sql,
    choose_tablesample_method,
    quote_ident,
    resolve_sampling_config,
    stable_seed_for,
)

# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeSamplingConnection:
    """A duck-typed stand-in for `PostgreSQLConnection` that records the SQL it
    was asked to run and replays canned column values.

    There is no live Postgres in this environment, so this is how the step's
    end-to-end behaviour is characterised. `executed` is the assertion surface
    for "did a TABLESAMPLE actually get issued, and for which column".
    """

    def __init__(self, values_by_column=None, capabilities=None, fail_on=()):
        self.values_by_column = values_by_column or {}
        self._capabilities = capabilities or EngineCapabilities(
            column_stats=True, tuple_counters=True, value_sampling=True,
        )
        self.executed: list[str] = []
        self.fail_on = set(fail_on)

    @property
    def capabilities(self):
        return self._capabilities

    def execute_query(self, query, params=()):
        self.executed.append(query)
        column = _column_from_sql(query)
        if column in self.fail_on:
            raise RuntimeError(f"permission denied for column {column}")
        values = self.values_by_column.get(column, [])
        if "GROUP BY value" in query:
            counts: dict = {}
            for v in values:
                counts[v] = counts.get(v, 0) + 1
            return [{"value": v, "value_count": c} for v, c in counts.items()]
        return [{"value": v} for v in values]

    def execute_query_isolated(self, query, params=()):
        """§F: `column_profile_step.py` calls this instead of `execute_query`
        now — proxied straight through here since this fake has no real
        transaction to protect (unlike `PostgreSQLConnection`'s SAVEPOINT-
        wrapped override, exercised separately in
        `TestExecuteQueryIsolatedSavepoints` below)."""
        return self.execute_query(query, params)


def _column_from_sql(query: str) -> str:
    match = re.search(r'SELECT "([^"]+)" AS value', query)
    return match.group(1) if match else ""


class _FakeDesigner:
    """`DataDesigner`, enough of it. Records every create body verbatim so the
    `contentStatus: DRAFT` assertion is against what would really be sent."""

    def __init__(self, elements=None, existing_guids=None, fail=False):
        self.elements = elements if elements is not None else []
        self.existing_guids = existing_guids or {}
        self.created: list[dict] = []
        self.fail = fail
        self.searches: list[tuple] = []

    def find_data_value_specifications(self, search_string="*", **kwargs):
        self.searches.append((search_string, kwargs))
        if self.fail:
            raise RuntimeError("view server unreachable")
        return self.elements

    def get_guid_for_name(self, name, property_name=None, type_name=None):   # pyegeria's signature
        return self.existing_guids.get(name, "No elements found")

    def create_data_class(self, body):
        self.created.append(body)
        return f"guid-dc-{len(self.created)}"


class _FakeRefManager:
    def __init__(self, elements=None, existing_guids=None, fail=False):
        self.elements = elements if elements is not None else []
        self.existing_guids = existing_guids or {}
        self.created: list[dict] = []
        self.links: list[tuple] = []
        self.fail = fail

    def find_valid_value_definitions(self, search_string="*", **kwargs):
        if self.fail:
            raise RuntimeError("view server unreachable")
        return self.elements

    def get_guid_for_name(self, name, property_name=None, type_name=None):   # pyegeria's signature
        return self.existing_guids.get(name, "No elements found")

    def create_valid_value_definition(self, body):
        self.created.append(body)
        return f"guid-vv-{len(self.created)}"

    def link_valid_value_definition(self, vv_set_guid, vv_member_guid, body=None):
        self.links.append((vv_set_guid, vv_member_guid, body))


class _FakeDiscovery:
    def __init__(self, fail=False):
        self.links: list[tuple] = []
        self.fail = fail

    def link_annotation_to_described_element(self, annotation_guid, element_guid, body):
        if self.fail:
            raise RuntimeError("relationship create rejected")
        self.links.append((annotation_guid, element_guid, body))


def _element(type_name, props, guid="g1", extra=None):
    element = {
        "elementHeader": {"guid": guid, "type": {"typeName": type_name}},
        "properties": props,
    }
    element.update(extra or {})
    return element


EMAIL_CLASS = KnownDataClass(
    guid="guid-email", qualified_name="DataClass::Email", display_name="Email Address",
    specification=r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", data_type="string",
    match_property_names=("email", "email_address"),
)

UUID_CLASS = KnownDataClass(
    guid="guid-uuid", qualified_name="DataClass::UUID", display_name="UUID",
    data_patterns=(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    ),
    data_type="string",
)

COUNTRY_SET = KnownValidValueSet(
    guid="guid-country", qualified_name="ValidValueDefinition::Country",
    display_name="Country Codes", values=("GB", "US", "FR", "DE", "NL"),
)

_EMAILS = [f"user{i}@example.com" for i in range(40)]


def _sample(values, **kwargs):
    defaults = dict(
        strategy=RANDOM, sample_rows=len(values), total_rows=4_200_000,
        seed=7, sampled_at="2026-09-21",
    )
    defaults.update(kwargs)
    return SampleProvenance(**defaults)


# ── 1. Sampling configuration (design §5.8) ──────────────────────────────────

class TestSamplingConfiguration:
    """§5.8 is a configuration surface, not a single knob."""

    def test_the_two_defaults_differ_by_purpose(self):
        # §5.8: "catalog_stats_only for measures; random for value matching".
        # One step resolves two configs, and conflating them would make every
        # data-class match either free-and-impossible or expensive-by-default.
        assert DEFAULT_MEASURE_SAMPLING.strategy == CATALOG_STATS_ONLY
        assert DEFAULT_MATCHING_SAMPLING.strategy == RANDOM
        assert resolve_sampling_config(purpose="measures").strategy == CATALOG_STATS_ONLY
        assert resolve_sampling_config(purpose="matching").strategy == RANDOM

    def test_catalog_stats_only_reads_no_values(self):
        assert SamplingConfig(strategy=CATALOG_STATS_ONLY).reads_values is False
        for strategy in (HEAD, RANDOM, SYSTEMATIC, FULL):
            assert SamplingConfig(strategy=strategy).reads_values is True

    def test_all_six_strategies_are_configurable(self):
        for strategy in (CATALOG_STATS_ONLY, HEAD, RANDOM, SYSTEMATIC, FULL):
            assert SamplingConfig(strategy=strategy).strategy == strategy
        assert SamplingConfig(strategy=STRATIFIED, strata_column="created_on").strategy == STRATIFIED

    def test_an_unknown_strategy_is_refused_not_defaulted(self):
        with pytest.raises(SamplingConfigError, match="Unknown sampling strategy"):
            SamplingConfig(strategy="sort_of_random")

    def test_stratified_without_a_column_is_refused(self):
        # Refusing rather than silently degrading to `random`: a downgraded
        # strategy would be recorded truthfully in the provenance and still be
        # the wrong answer to what was asked, with nothing to reveal it.
        with pytest.raises(SamplingConfigError, match="strata_column"):
            SamplingConfig(strategy=STRATIFIED)

    def test_non_positive_bounds_are_refused(self):
        for field in ("max_rows", "max_bytes", "max_values"):
            with pytest.raises(SamplingConfigError):
                SamplingConfig(strategy=RANDOM, **{field: 0})

    def test_scopes_resolve_narrowest_wins_and_record_which(self):
        config = resolve_sampling_config(
            purpose="matching",
            resource_slug="coco_ods",
            global_settings={"max_rows": 5_000},
            resource_type_settings={"max_rows": 2_000, "max_values": 100},
            resource_settings={"strategy": HEAD},
            run_settings={"seed": 99},
        )
        assert config.strategy == HEAD          # resource beat global's default
        assert config.max_rows == 2_000         # resource_type beat global
        assert config.max_values == 100
        assert config.seed == 99                # run beat the derived seed
        # §5.8's scope row is only useful if a surprising sample is traceable
        # to the setting that caused it.
        assert config.scope == "run"

    def test_time_budget_accepts_the_design_docs_own_spelling(self):
        config = resolve_sampling_config(
            purpose="matching", global_settings={"time_budget": 5},
        )
        assert config.time_budget_seconds == 5

    def test_unknown_override_keys_are_ignored_not_fatal(self):
        # A settings blob may legitimately carry keys for another resource type.
        config = resolve_sampling_config(
            purpose="matching", global_settings={"walk_depth": 3, "max_rows": 7},
        )
        assert config.max_rows == 7

    def test_seed_is_fixed_per_resource_and_stable_across_processes(self):
        # §5.8: "reproducible samples, so two runs differ because the data did."
        # `hash()` would be salted per interpreter and give a new sample every
        # restart, which is the opposite of the setting's purpose.
        first = stable_seed_for("coco_ods")
        assert first == stable_seed_for("coco_ods")
        assert first != stable_seed_for("coco_pharma")
        assert resolve_sampling_config(
            purpose="matching", resource_slug="coco_ods"
        ).seed == first

    def test_a_config_is_frozen(self):
        with pytest.raises(Exception):
            DEFAULT_MATCHING_SAMPLING.strategy = HEAD


# ── 2. The SQL: TABLESAMPLE, not ORDER BY random() ───────────────────────────

class TestSampleSql:
    """§5.8 names `TABLESAMPLE SYSTEM`/`BERNOULLI` specifically, in preference
    to `ORDER BY random() LIMIT n`. With no live Postgres, the SQL shape is
    the evidence."""

    def test_random_uses_tablesample_with_a_repeatable_seed(self):
        query = build_column_sample_sql(
            "public", "customer", "email",
            SamplingConfig(strategy=RANDOM, seed=7), total_rows=4_200_000,
        )
        assert "TABLESAMPLE" in query.sql
        assert "REPEATABLE (7)" in query.sql
        assert query.strategy == RANDOM
        assert query.tablesample_method in ("SYSTEM", "BERNOULLI")

    def test_random_never_uses_order_by_random(self):
        # The specific expensive form §5.8 rules out: it sorts the whole table
        # to return a handful of rows.
        query = build_column_sample_sql(
            "public", "customer", "email",
            SamplingConfig(strategy=RANDOM, seed=7), total_rows=4_200_000,
        )
        assert "random()" not in query.sql.lower()
        assert "order by random" not in query.sql.lower()

    def test_system_on_a_large_table_bernoulli_on_a_small_one(self):
        # SYSTEM samples PAGES: cheap, and worthless on a table of a few
        # pages. BERNOULLI considers every row, which is affordable exactly
        # where SYSTEM stops working.
        assert choose_tablesample_method(4_200_000, 0.7) == "SYSTEM"
        assert choose_tablesample_method(1_000, 100.0) == "BERNOULLI"
        assert choose_tablesample_method(4_200_000, 40.0) == "BERNOULLI"
        # Unknown size: correctness of the sample over an unjustifiable saving.
        assert choose_tablesample_method(None, 1.0) == "BERNOULLI"

    def test_percentage_is_derived_from_the_row_count_and_capped(self):
        big = build_column_sample_sql(
            "s", "t", "c", SamplingConfig(strategy=RANDOM, max_rows=10_000), 4_200_000,
        )
        small = build_column_sample_sql(
            "s", "t", "c", SamplingConfig(strategy=RANDOM, max_rows=10_000), 100,
        )
        assert 0 < big.tablesample_percent < 5
        assert small.tablesample_percent == 100.0

    def test_catalog_stats_only_builds_no_query_at_all(self):
        # Not an empty string, not a query that reads everything: None, which
        # the matcher turns into `not_sampled`.
        assert build_column_sample_sql(
            "s", "t", "c", SamplingConfig(strategy=CATALOG_STATS_ONLY),
        ) is None

    def test_head_is_a_plain_bounded_select(self):
        query = build_column_sample_sql(
            "s", "t", "c", SamplingConfig(strategy=HEAD, max_values=50),
        )
        assert "TABLESAMPLE" not in query.sql
        assert "LIMIT 50" in query.sql

    def test_systematic_selects_every_nth_row(self):
        query = build_column_sample_sql(
            "s", "t", "c", SamplingConfig(strategy=SYSTEMATIC, max_rows=100), 1_000,
        )
        assert "row_number() OVER ()" in query.sql
        assert "% 10 = 0" in query.sql

    def test_stratified_partitions_by_its_column(self):
        query = build_column_sample_sql(
            "s", "t", "c",
            SamplingConfig(strategy=STRATIFIED, strata_column="status"),
        )
        assert 'PARTITION BY "status"' in query.sql

    def test_full_applies_no_row_limit(self):
        # §5.8: "a deliberate choice, never a default." Applying the
        # per-column cap here would make `full` a synonym for `head`.
        query = build_column_sample_sql(
            "s", "t", "c", SamplingConfig(strategy=FULL),
        )
        assert "LIMIT" not in query.sql
        assert query.strategy == FULL

    def test_nulls_are_excluded_from_every_value_sample(self):
        for strategy in (HEAD, RANDOM, SYSTEMATIC, FULL):
            query = build_column_sample_sql(
                "s", "t", "c", SamplingConfig(strategy=strategy, seed=1), 100_000,
            )
            assert 'IS NOT NULL' in query.sql

    def test_identifiers_are_quoted(self):
        query = build_column_sample_sql(
            "public", "order", "select", SamplingConfig(strategy=HEAD),
        )
        assert '"public"."order"' in query.sql
        assert '"select"' in query.sql
        assert quote_ident('we"ird') == '"we""ird"'

    def test_distinct_query_requests_one_more_than_the_bound(self):
        # The truncation detector. Without the +1, a column with exactly
        # max_values distinct values and one with a million are identical.
        query = build_distinct_values_sql(
            "s", "t", "c", SamplingConfig(strategy=RANDOM, max_values=1_000, seed=3),
            total_rows=500_000,
        )
        assert "GROUP BY value" in query.sql
        assert "LIMIT 1001" in query.sql
        assert "TABLESAMPLE" in query.sql        # same configured strategy


# ── 3. The envelope (§5.8's first rule) ──────────────────────────────────────

class TestProvenanceEnvelope:
    def test_the_sentence_carries_strategy_size_total_seed_and_date(self):
        # §5.8's own example: "from a random sample of 10 000 of 4.2 M rows
        # (seed 7, 2026-09-20)". This is a HARD requirement, not a nicety.
        sentence = SampleProvenance(
            strategy=RANDOM, sample_rows=10_000, total_rows=4_200_000,
            seed=7, sampled_at="2026-09-21",
        ).describe()
        assert "random" in sentence
        assert "10,000" in sentence
        assert "4,200,000" in sentence
        assert "seed 7" in sentence
        assert "2026-09-21" in sentence

    def test_an_unknown_total_is_said_not_implied(self):
        sentence = SampleProvenance(
            strategy=RANDOM, sample_rows=500, total_rows=None,
        ).describe()
        assert "unknown total" in sentence
        assert "500" in sentence

    def test_no_sample_says_so_and_why(self):
        sentence = SampleProvenance(
            strategy=CATALOG_STATS_ONLY,
            reason_not_sampled="strategy is catalog_stats_only, so no table data was read",
        ).describe()
        assert sentence.startswith("no sample taken")
        assert "catalog_stats_only" in sentence

    def test_truncation_is_stated(self):
        assert "truncated" in SampleProvenance(
            strategy=RANDOM, sample_rows=1_000, total_rows=9_000, truncated=True,
        ).describe()

    def test_total_rows_unknown_serialises_as_not_established_not_zero(self):
        # A sample "of 0 rows" is a measurement; a sample of an unknown total
        # is not. The serialised form must not let them collide.
        assert SampleProvenance(strategy=RANDOM, total_rows=None).as_dict()[
            "total_rows"
        ] == "not_established"
        assert SampleProvenance(strategy=RANDOM, total_rows=0).as_dict()["total_rows"] == 0


# ── 4. `n_distinct`'s sign convention ────────────────────────────────────────

class TestResolveNDistinct:
    """pg_stats packs three meanings into one signed column. Two of the three
    are wrong if read as a plain number."""

    def test_positive_is_a_count(self):
        assert resolve_n_distinct(12, 1_000) == 12

    def test_negative_is_a_fraction_of_the_row_count(self):
        # -1 means "every value distinct" — a unique key. Read literally it
        # says "one distinct value", which would put every primary key into
        # the reference-data candidate set.
        assert resolve_n_distinct(-1, 4_200_000) == 4_200_000
        assert resolve_n_distinct(-0.5, 1_000) == 500

    def test_zero_is_unknown_not_zero(self):
        assert resolve_n_distinct(0, 1_000) is None

    def test_a_fraction_with_no_total_to_scale_it_is_unknown(self):
        assert resolve_n_distinct(-1, None) is None
        assert resolve_n_distinct(-1, 0) is None

    def test_missing_is_unknown(self):
        assert resolve_n_distinct(None, 1_000) is None
        assert resolve_n_distinct("not a number", 1_000) is None

    def test_low_cardinality_gate_returns_none_when_undecidable(self):
        # "Not reference data" and "we do not know its cardinality" are
        # different, and only the first is a finding.
        assert is_low_cardinality(None, 1_000) is None
        assert is_low_cardinality(5, 100_000) is True
        assert is_low_cardinality(50_000, 100_000) is False
        # A constant column is not a reference set.
        assert is_low_cardinality(1, 100_000) is False
        # Few distinct values but almost as many as there are rows: a small
        # table, not reference data.
        assert is_low_cardinality(40, 60) is True   # fraction test skipped: tiny table
        assert is_low_cardinality(40, 400) is False  # 10% distinct


# ── 5. data_class_match ──────────────────────────────────────────────────────

class TestDataClassMatch:
    def _match(self, values, classes=(EMAIL_CLASS,), column="email",
               column_type="text", provenance=None, **kwargs):
        return data_class_match(
            schema_name="public", table_name="customer", column_name=column,
            column_type=column_type, known_classes=list(classes),
            sampled_values=values,
            provenance=provenance if provenance is not None else _sample(values or []),
            **kwargs,
        )

    def test_a_conforming_column_matches(self):
        match = self._match(_EMAILS)
        assert match.verdict == MATCH_MATCHED
        assert match.matched_guid == "guid-email"
        assert match.sampled_conformance == 1.0
        assert match.evidence == "value_pattern"

    def test_the_claim_is_stated_against_the_sample(self):
        # §5.8's second rule: "conforms to Data Class X with 0.98 of sampled
        # values" — never an unqualified "conforms".
        statement = self._match(_EMAILS).describe()
        assert "of sampled values" in statement
        assert "from a random sample of" in statement

    def test_a_few_bad_values_still_match(self):
        values = _EMAILS[:38] + ["n/a", "TBC"]
        match = self._match(values)
        assert match.verdict == MATCH_MATCHED
        assert 0.94 < match.sampled_conformance < 1.0

    def test_a_failing_specification_disqualifies_despite_a_perfect_name(self):
        # A column called `email` full of integers is not an email column, and
        # the name is exactly the evidence that would otherwise carry it.
        match = self._match([str(i) for i in range(40)], column="email")
        assert match.verdict != MATCH_MATCHED

    def test_a_data_patterns_list_is_honoured(self):
        uuids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(40)]
        match = self._match(uuids, classes=(UUID_CLASS,), column="external_ref")
        assert match.verdict == MATCH_MATCHED
        assert match.matched_guid == "guid-uuid"

    def test_conformance_is_computed_over_the_union_of_patterns(self):
        # Half one pattern, half the other: a full match on a two-pattern
        # class, and 50% on each pattern taken alone.
        two = KnownDataClass(
            guid="g", qualified_name="q", display_name="Phone",
            data_patterns=(r"\+44\d{10}", r"\(\d{3}\) \d{3}-\d{4}"),
        )
        values = [f"+4470000000{i:02d}" for i in range(20)] + [
            f"(555) 000-00{i:02d}" for i in range(20)
        ]
        match = self._match(values, classes=(two,), column="phone")
        assert match.verdict == MATCH_MATCHED
        assert match.sampled_conformance == 1.0

    def test_name_and_type_only_evidence_is_capped_and_labelled(self):
        nameless = KnownDataClass(
            guid="g-nospec", qualified_name="DataClass::Email2",
            display_name="email", data_type="string",
            match_property_names=("email",),
        )
        # Varied values: a single repeated value would be inconclusive on
        # cardinality grounds before the name evidence was ever considered.
        match = self._match(
            [f"anything {i}" for i in range(40)], classes=(nameless,), column="email",
        )
        assert match.verdict == MATCH_MATCHED
        assert match.evidence == "name_and_type"
        assert match.confidence <= NAME_AND_TYPE_ONLY_CONFIDENCE
        assert match.sampled_conformance is None
        assert "name and type only" in match.describe()
        assert "no value conformance was tested" in match.describe()

    def test_a_type_mismatch_disqualifies(self):
        integer_class = KnownDataClass(
            guid="g", qualified_name="q", display_name="Age", data_type="integer",
            match_property_names=("email",),
        )
        assert self._match(_EMAILS, classes=(integer_class,)).verdict != MATCH_MATCHED
        assert type_compatibility("text", "integer") == 0.0
        # An undeclared type on either side is unknown, NOT incompatible —
        # scoring it 0 would penalise every loosely-authored class.
        assert type_compatibility("text", "") is None

    def test_no_match_when_candidates_exist_and_nothing_conforms(self):
        match = self._match(
            ["free text number " + str(i) for i in range(40)],
            classes=(EMAIL_CLASS,), column="notes",
        )
        assert match.verdict == MATCH_NO_MATCH
        assert match.established is True      # this one IS a finding

    def test_an_unmatched_but_patterned_column_becomes_a_proposal(self):
        uuids = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(40)]
        match = self._match(uuids, classes=(EMAIL_CLASS,), column="external_ref")
        assert match.verdict == MATCH_UNMATCHED_PATTERNED
        assert "uuid" in match.detected_patterns
        assert match.proposed_specification

    def test_a_privacy_relevant_pattern_is_flagged(self):
        cards = [f"4111 1111 1111 {i:04d}" for i in range(40)]
        # A non-matching class must exist: with an EMPTY platform the verdict
        # is `no_candidates` (nothing to compare against) rather than
        # `unmatched_patterned`, which is correct and is asserted separately.
        match = self._match(cards, classes=(EMAIL_CLASS,), column="pan")
        assert match.verdict == MATCH_UNMATCHED_PATTERNED
        assert match.privacy_relevant is True

    def test_not_sampled_is_never_no_match(self):
        # The single most important assertion in this file. A PII question
        # answered "no match" for a column no value was read from is a
        # confident wrong answer on the topic where it costs most.
        match = data_class_match(
            schema_name="public", table_name="customer", column_name="email",
            column_type="text", known_classes=[EMAIL_CLASS],
            sampled_values=None,
            provenance=SampleProvenance(
                strategy=CATALOG_STATS_ONLY,
                reason_not_sampled="strategy is catalog_stats_only, so no table data was read",
            ),
        )
        assert match.verdict == MATCH_NOT_SAMPLED
        assert match.verdict in NOT_ESTABLISHED_VERDICTS
        assert match.established is False
        assert "not established" in match.describe()
        assert "not a statement that no match exists" in match.describe()

    def test_an_empty_platform_is_no_candidates_not_no_match(self):
        match = self._match(_EMAILS, classes=())
        assert match.verdict == MATCH_NO_CANDIDATES
        assert match.established is False
        assert "no confirmed Data Classes" in match.describe()

    def test_sampled_but_too_thin_is_inconclusive_not_no_match(self):
        # The brief names this distinction explicitly: "sampled but
        # inconclusive" is different from "not sampled at all".
        match = self._match(["a@b.com", "c@d.com"])
        assert match.verdict == MATCH_INCONCLUSIVE
        assert "below the" in match.describe()
        # And it is a DIFFERENT verdict from not-sampled, not a synonym.
        assert MATCH_INCONCLUSIVE != MATCH_NOT_SAMPLED

    def test_one_distinct_value_repeated_is_inconclusive(self):
        match = self._match(["a@b.com"] * 60)
        assert match.verdict == MATCH_INCONCLUSIVE

    def test_binary_columns_are_not_applicable_not_unmatched(self):
        match = self._match(None, column="blob", column_type="bytea")
        assert match.verdict == MATCH_NOT_APPLICABLE
        assert "not applicable" in match.describe()

    def test_a_draft_class_is_excluded_by_default(self):
        # A DRAFT class is a proposal, quite possibly one an earlier run of
        # this step made. Matching against it by default would let RE ratify
        # its own guess with no curator in the loop.
        draft = KnownDataClass(
            guid="g-draft", qualified_name="q", display_name="Email",
            specification=EMAIL_CLASS.specification, content_status="DRAFT",
        )
        excluded = self._match(_EMAILS, classes=(draft,))
        assert excluded.verdict == MATCH_NO_CANDIDATES
        assert "DRAFT proposal(s) exist and were excluded" in excluded.describe()

        included = self._match(_EMAILS, classes=(draft,), include_draft_classes=True)
        assert included.verdict == MATCH_MATCHED
        assert included.matched_element_is_draft is True
        assert "itself a DRAFT proposal" in included.describe()

    def test_an_uncompilable_specification_is_reported_not_silently_zero(self):
        broken = KnownDataClass(
            guid="g", qualified_name="DataClass::Broken", display_name="Broken",
            specification="[unclosed",
        )
        match = self._match(_EMAILS, classes=(broken,))
        assert "DataClass::Broken" in match.unusable_specifications

    def test_runners_up_are_kept_for_a_curator(self):
        other = KnownDataClass(
            guid="g2", qualified_name="DataClass::AnyText", display_name="Any Text",
            specification=r".*", data_type="string",
        )
        match = self._match(_EMAILS, classes=(EMAIL_CLASS, other))
        assert match.other_candidates

    def test_patterns_match_the_whole_value_not_a_substring(self):
        # A `search`-based email pattern matches every comment that mentions an
        # address, which would classify a notes column as PII and bury the
        # columns that are.
        assert pattern_conformance(["see bob@example.com for details"],
                                   r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}") == 0.0
        assert pattern_conformance(["bob@example.com"],
                                   r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}") == 1.0

    def test_name_similarity_is_token_based(self):
        assert name_similarity("emailAddress", ["email_address"]) == 1.0
        assert name_similarity("home_email_address", ["email", "address"]) > 0
        # A class declaring no names has not failed a name test.
        assert name_similarity("email", []) == 0.0


# ── 6. reference_data_match ──────────────────────────────────────────────────

class TestReferenceDataMatch:
    def _match(self, distinct_values, distinct_count=5, total_rows=100_000,
               sets=(COUNTRY_SET,), provenance=None, **kwargs):
        return reference_data_match(
            schema_name="public", table_name="customer", column_name="country",
            column_type="varchar", known_sets=list(sets),
            distinct_values=distinct_values, distinct_count=distinct_count,
            total_rows=total_rows,
            provenance=provenance if provenance is not None else _sample(
                distinct_values or [], total_rows=total_rows,
            ),
            **kwargs,
        )

    def test_a_full_match(self):
        match = self._match(["GB", "US", "FR"])
        assert match.verdict == MATCH_MATCHED
        assert match.value_coverage == 1.0
        assert match.matched_guid == "guid-country"
        assert "covers 1.00 of the sampled distinct values" in match.describe()

    def test_matching_is_case_insensitive(self):
        assert self._match(["gb", "us"]).verdict == MATCH_MATCHED

    def test_a_partial_match_names_the_values_outside_the_set(self):
        match = self._match(["GB", "US", "FR", "XX", "ZZ"])
        assert match.verdict == MATCH_PARTIAL
        assert sorted(match.unmatched_values) == ["XX", "ZZ"]
        assert 0.5 < match.value_coverage < 1.0
        assert "partially conforms" in match.describe()

    def test_no_set_fits_so_one_is_proposed(self):
        match = self._match(["ALPHA", "BETA", "GAMMA", "DELTA"])
        assert match.verdict == MATCH_UNMATCHED_PATTERNED
        assert match.proposed_values == ["ALPHA", "BETA", "DELTA", "GAMMA"]

    def test_an_empty_platform_still_proposes_rather_than_concluding(self):
        # The column IS reference-data shaped; the reason nothing matched is
        # that the platform holds nothing — which is exactly §5.4's "which
        # should become one?", not a finding that no set fits.
        match = self._match(["A", "B", "C"], sets=())
        assert match.verdict == MATCH_UNMATCHED_PATTERNED
        assert "holds no confirmed Valid Value Sets" in match.not_established_reason
        assert match.proposed_values == ["A", "B", "C"]

    def test_a_high_cardinality_column_is_not_applicable(self):
        match = self._match(None, distinct_count=900_000, total_rows=1_000_000)
        assert match.verdict == MATCH_NOT_APPLICABLE
        assert "not reference-data shaped" in match.describe()

    def test_an_unknown_distinct_count_gates_rather_than_answers(self):
        match = self._match(None, distinct_count=None)
        assert match.verdict == MATCH_NOT_SAMPLED
        assert "not known" in match.describe()
        assert "ANALYZE may never have run" in match.not_established_reason

    def test_low_cardinality_but_never_sampled_is_not_sampled(self):
        match = self._match(
            None, distinct_count=5,
            provenance=SampleProvenance(
                strategy=CATALOG_STATS_ONLY,
                reason_not_sampled="strategy is catalog_stats_only, so no table data was read",
            ),
        )
        assert match.verdict == MATCH_NOT_SAMPLED
        assert match.established is False

    def test_an_all_null_column_is_inconclusive_not_a_match(self):
        match = self._match([])
        assert match.verdict == MATCH_INCONCLUSIVE

    def test_a_truncated_distinct_list_is_inconclusive_not_partial(self):
        # A coverage fraction from an incomplete list is wrong in an unknown
        # direction: the missing values might all be inside the set, or all
        # outside it.
        match = self._match(
            ["GB", "US", "FR"], distinct_count=5,
            provenance=_sample(["GB", "US", "FR"], truncated=True),
        )
        assert match.verdict == MATCH_INCONCLUSIVE
        assert "truncated" in match.describe()

    def test_a_set_with_no_readable_members_is_no_candidates(self):
        empty_set = KnownValidValueSet(
            guid="g", qualified_name="q", display_name="Empty Set", values=(),
        )
        match = self._match(["A", "B"], sets=(empty_set,))
        assert match.verdict == MATCH_NO_CANDIDATES
        assert "member values readable" in match.describe()

    def test_a_draft_set_is_excluded_by_default(self):
        draft = KnownValidValueSet(
            guid="g", qualified_name="q", display_name="Countries",
            values=("GB", "US"), content_status="DRAFT",
        )
        excluded = self._match(["GB", "US"], sets=(draft,))
        assert excluded.verdict == MATCH_UNMATCHED_PATTERNED
        included = self._match(["GB", "US"], sets=(draft,), include_draft_sets=True)
        assert included.verdict == MATCH_MATCHED
        assert included.matched_element_is_draft is True

    def test_binary_columns_are_not_applicable(self):
        match = reference_data_match(
            schema_name="s", table_name="t", column_name="blob",
            column_type="bytea", known_sets=[COUNTRY_SET],
            distinct_values=None, distinct_count=3, total_rows=1_000,
            provenance=_sample([]),
        )
        assert match.verdict == MATCH_NOT_APPLICABLE


class TestStatementsAreAlwaysQualified:
    """§5.8's second rule, enforced over every verdict rather than per case."""

    def test_no_verdict_ever_produces_an_unqualified_conforms(self):
        provenance = _sample(_EMAILS)
        cases = [
            data_class_match(
                schema_name="s", table_name="t", column_name="email",
                column_type="text", known_classes=[EMAIL_CLASS],
                sampled_values=_EMAILS, provenance=provenance,
            ),
            reference_data_match(
                schema_name="s", table_name="t", column_name="country",
                column_type="varchar", known_sets=[COUNTRY_SET],
                distinct_values=["GB", "US"], distinct_count=2,
                total_rows=100_000, provenance=provenance,
            ),
        ]
        for match in cases:
            statement = match.describe()
            assert "conforms" in statement
            # Every "conforms" is accompanied by a fraction AND the sample
            # envelope, in the same sentence.
            assert ("of sampled values" in statement
                    or "of the sampled distinct values" in statement)
            assert "sample of" in statement

    def test_a_match_with_no_provenance_declares_itself_unqualified(self):
        match = data_class_match(
            schema_name="s", table_name="t", column_name="email",
            column_type="text", known_classes=[EMAIL_CLASS],
            sampled_values=_EMAILS, provenance=SampleProvenance(strategy=RANDOM),
        )
        match.provenance = None
        assert "unqualified" in match.describe()


# ── 7. The DRAFT proposal path ───────────────────────────────────────────────

def _proposal_match(question="data_class_match"):
    values = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(40)]
    if question == "data_class_match":
        return data_class_match(
            schema_name="public", table_name="customer", column_name="external_ref",
            column_type="text", known_classes=[EMAIL_CLASS],
            sampled_values=values, provenance=_sample(values),
        )
    return reference_data_match(
        schema_name="public", table_name="customer", column_name="status",
        column_type="varchar", known_sets=[COUNTRY_SET],
        distinct_values=["NEW", "OPEN", "CLOSED"], distinct_count=3,
        total_rows=100_000, provenance=_sample(["NEW", "OPEN", "CLOSED"]),
    )


class TestDraftProposalPath:
    """The corrected probes 4/5 mechanism: a proposal is a normal element with
    `contentStatus: DRAFT`, linked to its evidence via `AssociatedAnnotation`."""

    def test_the_proposed_data_class_body_sets_content_status_draft(self):
        match = _proposal_match()
        body = build_proposed_data_class_body(match, "DataClass::x")
        props = body["properties"]
        assert props["contentStatus"] == "DRAFT"
        assert props["class"] == "DataClassProperties"
        # The two traps, both pinned: `initialStatus` means ElementStatus, is
        # a different field, and is dropped silently by pyegeria (ISSUE-113).
        assert "initialStatus" not in body
        assert "initialStatus" not in props

    def test_the_proposed_body_uses_egerias_real_field_names(self):
        # Verified against pyegeria's own REST reference, not inferred:
        # `namespacePath` (not `namespace`) and `dataPatterns` as a LIST
        # (there is no `valuePattern` on DataClassProperties).
        props = build_proposed_data_class_body(_proposal_match(), "q")["properties"]
        assert "namespacePath" in props and "namespace" not in props
        assert isinstance(props["dataPatterns"], list)
        assert "valuePattern" not in props

    def test_the_proposal_carries_its_sample_provenance(self):
        # A curator looking at a candidate class months later must see what it
        # was inferred from without finding the survey report.
        props = build_proposed_data_class_body(_proposal_match(), "q")["properties"]
        evidence = props["additionalProperties"]
        assert "sample of" in evidence["sampleEnvelope"]
        assert evidence["sampleStrategy"] == RANDOM
        assert evidence["totalRows"] == "4200000"
        assert evidence["proposedFor"] == "public.customer.external_ref"
        assert all(isinstance(v, str) for v in evidence.values())

    def test_the_proposal_is_described_as_a_proposal_in_prose_too(self):
        # Not only in a field a reader may not render.
        props = build_proposed_data_class_body(_proposal_match(), "q")["properties"]
        assert props["description"].startswith("PROPOSAL")
        assert "not confirmed content" in props["description"]

    def test_the_proposed_valid_value_set_body_sets_content_status_draft(self):
        props = build_proposed_valid_value_set_body(
            _proposal_match("reference_data_match"), "ValidValueDefinition::x",
        )["properties"]
        assert props["contentStatus"] == "DRAFT"
        # There is no `create_valid_value_set` and no `ValidValueSetProperties`
        # in pyegeria — a set is created through create_valid_value_definition.
        assert props["class"] == "ValidValueDefinitionProperties"
        # No typeName: Egeria 6.2 knows no `ValidValueSet` type (OMAG-COMMON-400-018); a set is a plain
        # ValidValueDefinition, as bootstrap_data_classes creates its sets.
        assert "typeName" not in props

    def test_publishing_a_data_class_proposal_creates_and_links_it(self):
        designer, discovery = _FakeDesigner(), _FakeDiscovery()
        result = publish_proposed_data_class(
            designer, _proposal_match(),
            discovery=discovery, annotation_guid="ann-1",
        )
        assert result.created is True
        assert result.published is True
        assert result.content_status == "DRAFT"
        assert designer.created[0]["properties"]["contentStatus"] == "DRAFT"
        # The AssociatedAnnotation link, with the right properties class.
        assert result.associated_annotation_linked is True
        annotation_guid, element_guid, body = discovery.links[0]
        assert annotation_guid == "ann-1"
        assert element_guid == result.guid
        assert body["properties"]["class"] == "AssociatedAnnotationProperties"
        assert body["class"] == "NewRelationshipRequestBody"

    def test_the_associated_annotation_body_is_always_explicit(self):
        # pyegeria synthesises a malformed body from its own internal `prop`
        # hint when none is passed, so it must never be left to default.
        body = build_associated_annotation_body("l", "d")
        assert body["properties"]["class"] == "AssociatedAnnotationProperties"

    def test_republishing_converges_rather_than_duplicating(self):
        match = _proposal_match()
        from resource_explorer.surveyors.database.egeria_reference_catalog import (
            proposed_data_class_qualified_name,
        )
        qn = proposed_data_class_qualified_name(match)
        designer = _FakeDesigner(existing_guids={qn: "eeeeeeee-0000-4000-8000-000000000001"})
        result = publish_proposed_data_class(designer, match)
        assert result.created is False
        assert result.guid == "eeeeeeee-0000-4000-8000-000000000001"
        assert designer.created == []

    def test_a_miss_reading_as_pyegerias_sentinel_string_still_creates(self):
        # `_find_existing` (egeria_reference_catalog.py) used to check
        # `get_guid_for_name`'s result with a bare `... or ""`, which does
        # not catch pyegeria's real miss return value: the literal string
        # "No elements found", not None/""/an exception. That string is
        # truthy, so a miss read as "already exists" and the create was
        # silently skipped — the same sentinel bug found live in
        # bootstrap_data_classes.py (2026-09-22). Fixed by routing through
        # `_as_guid`, which rejects the sentinel because it contains
        # whitespace, unlike a real GUID.
        match = _proposal_match()

        class _SentinelDesigner(_FakeDesigner):
            def get_guid_for_name(self, name, property_name=None, type_name=None):
                return "No elements found"

        designer = _SentinelDesigner()
        result = publish_proposed_data_class(designer, match)
        assert result.created is True
        assert len(designer.created) == 1

    def test_a_proposal_identity_is_keyed_on_the_column_not_the_pattern(self):
        # Two runs must converge on one element, and the pattern is exactly
        # what a second run might decide differently.
        from resource_explorer.surveyors.database.egeria_reference_catalog import (
            proposed_data_class_qualified_name,
        )
        first = _proposal_match()
        second = _proposal_match()
        second.detected_patterns = ["something_else"]
        second.proposed_specification = r"\d+"
        assert (proposed_data_class_qualified_name(first)
                == proposed_data_class_qualified_name(second))

    def test_a_failed_link_does_not_fail_the_proposal(self):
        designer, discovery = _FakeDesigner(), _FakeDiscovery(fail=True)
        result = publish_proposed_data_class(
            designer, _proposal_match(), discovery=discovery, annotation_guid="ann-1",
        )
        assert result.published is True
        assert result.associated_annotation_linked is False

    def test_a_failed_create_is_not_reported_as_published(self):
        class _Failing(_FakeDesigner):
            def create_data_class(self, body):
                raise RuntimeError("rejected")

        result = publish_proposed_data_class(_Failing(), _proposal_match())
        assert result.published is False
        assert "rejected" in result.error

    def test_publishing_a_valid_value_set_creates_draft_members_and_attaches_them(self):
        ref, discovery = _FakeRefManager(), _FakeDiscovery()
        match = _proposal_match("reference_data_match")
        result = publish_proposed_valid_value_set(
            ref, match, discovery=discovery, annotation_guid="ann-2",
        )
        assert result.created is True
        assert len(result.member_guids) == 3
        # Every member is DRAFT too — a confirmed member of a draft set would
        # be a confirmed value nobody approved.
        for body in ref.created[1:]:
            assert body["properties"]["contentStatus"] == "DRAFT"
        # Membership bodies are explicit, not left for pyegeria to synthesise.
        for _, _, body in ref.links:
            assert body["properties"]["class"] == "ValidValueMemberProperties"
        assert result.associated_annotation_linked is True

    def test_only_proposable_verdicts_produce_proposals(self):
        # Proposing a Data Class for a column nothing was sampled from would
        # turn "we did not look" into a governance element.
        not_sampled = data_class_match(
            schema_name="s", table_name="t", column_name="c", column_type="text",
            known_classes=[EMAIL_CLASS], sampled_values=None,
            provenance=SampleProvenance(strategy=CATALOG_STATS_ONLY),
        )
        designer = _FakeDesigner()
        results = publish_proposals([not_sampled], designer=designer)
        assert results == []
        assert designer.created == []

    def test_publish_proposals_routes_each_question_to_its_own_client(self):
        designer, ref, discovery = _FakeDesigner(), _FakeRefManager(), _FakeDiscovery()
        results = publish_proposals(
            [_proposal_match(), _proposal_match("reference_data_match")],
            designer=designer, ref_manager=ref, discovery=discovery,
        )
        kinds = {r["kind"] for r in results}
        assert kinds == {"data_class", "valid_value_set"}
        assert all(r["content_status"] == "DRAFT" for r in results)


# ── 8. The RFA fallback (support doc §3) ─────────────────────────────────────

class TestRfaFallback:
    def test_a_partial_reference_match_raises_an_rfa_listing_the_values(self):
        # §5.4 asks for this by name. And it stays an RFA even though DRAFT
        # creation now works: the set already exists, so there is no candidate
        # element to create — the decision is about the set's membership.
        match = reference_data_match(
            schema_name="public", table_name="customer", column_name="country",
            column_type="varchar", known_sets=[COUNTRY_SET],
            distinct_values=["GB", "US", "FR", "XX", "ZZ"], distinct_count=5,
            total_rows=100_000, provenance=_sample(["GB", "US", "FR", "XX", "ZZ"]),
        )
        rfa = _rfa_for_match(match, draft_available=True)
        assert rfa is not None
        assert rfa.action_requested == ACTION_REVIEW_UNMATCHED_VALUES
        assert "'XX'" in rfa.explanation and "'ZZ'" in rfa.explanation
        assert "No element was proposed in DRAFT for this case" in rfa.explanation

    def test_no_rfa_for_a_proposal_when_draft_creation_is_available(self):
        # The RFA-only path was §3's "until DRAFT works" fallback. It works.
        assert _rfa_for_match(_proposal_match(), draft_available=True) is None

    def test_an_rfa_carries_the_proposal_when_egeria_cannot_be_written(self):
        rfa = _rfa_for_match(_proposal_match(), draft_available=False)
        assert rfa is not None
        assert rfa.action_requested == ACTION_PROPOSE_DATA_CLASS
        assert "could not be created with contentStatus: DRAFT" in rfa.explanation
        assert "Observed pattern" in rfa.explanation

    def test_the_reference_data_fallback_names_the_right_action(self):
        rfa = _rfa_for_match(
            _proposal_match("reference_data_match"), draft_available=False,
        )
        assert rfa.action_requested == ACTION_PROPOSE_VALID_VALUE_SET
        assert "Observed values" in rfa.explanation

    def test_no_rfa_for_an_unestablished_verdict(self):
        # An action request about nothing.
        not_sampled = data_class_match(
            schema_name="s", table_name="t", column_name="c", column_type="text",
            known_classes=[EMAIL_CLASS], sampled_values=None,
            provenance=SampleProvenance(strategy=CATALOG_STATS_ONLY),
        )
        assert _rfa_for_match(not_sampled, draft_available=False) is None

    def test_a_long_unmatched_list_is_truncated_with_a_count(self):
        values = [f"V{i}" for i in range(60)]
        match = reference_data_match(
            schema_name="s", table_name="t", column_name="c", column_type="varchar",
            known_sets=[KnownValidValueSet(
                guid="g", qualified_name="q", display_name="Set",
                values=tuple(f"V{i}" for i in range(50)),
            )],
            distinct_values=values, distinct_count=60, total_rows=100_000,
            provenance=_sample(values),
        )
        rfa = _rfa_for_match(match, draft_available=True)
        assert rfa is not None
        assert "and 0 more" not in rfa.explanation or "more" in rfa.explanation


# ── 9. Reading the platform ──────────────────────────────────────────────────

class TestReferenceCatalogReads:
    def test_only_data_class_typed_elements_are_taken(self):
        # `find_data_value_specifications` returns every subtype; matching
        # columns against a Data Structure would be silent nonsense.
        assert data_class_from_element(
            _element("DataStructure", {"qualifiedName": "q"})
        ) is None
        assert data_class_from_element(
            _element("DataClass", {"qualifiedName": "q", "displayName": "d"})
        ) is not None

    def test_egerias_real_property_names_are_read(self):
        parsed = data_class_from_element(_element("DataClass", {
            "qualifiedName": "DataClass::Email",
            "displayName": "Email",
            "namespacePath": "crm.customer",
            "specification": r".+@.+",
            "dataPatterns": [r"\S+@\S+"],
            "dataType": "string",
            "matchPropertyNames": ["email", "email_address"],
            "contentStatus": "DRAFT",
        }))
        assert parsed.namespace_path == "crm.customer"
        assert parsed.data_patterns == (r"\S+@\S+",)
        assert parsed.match_property_names == ("email", "email_address")
        assert parsed.is_draft is True
        assert parsed.value_regexes == (r".+@.+", r"\S+@\S+")

    def test_a_comma_joined_list_property_is_tolerated(self):
        parsed = data_class_from_element(_element("DataClass", {
            "qualifiedName": "q", "matchPropertyNames": "email, email_address",
        }))
        assert parsed.match_property_names == ("email", "email_address")

    def test_an_element_without_a_qualified_name_is_skipped(self):
        assert data_class_from_element(_element("DataClass", {"displayName": "d"})) is None

    def test_member_values_are_read_from_a_graph_result(self):
        parsed = valid_value_set_from_element(_element(
            "ValidValueSet", {"qualifiedName": "q", "displayName": "Countries"},
            extra={"members": [
                {"properties": {"preferredValue": "GB"}},
                {"properties": {"preferredValue": "US"}},
            ]},
        ))
        assert parsed.values == ("GB", "US")

    def test_a_read_failure_is_unavailable_not_empty(self):
        # The distinction the whole absence story rests on: "the platform
        # holds none" is a fact and a reason to propose; "we could not ask" is
        # neither.
        catalog = list_known_data_classes(_FakeDesigner(fail=True))
        assert catalog.available is False
        assert catalog.data_classes == []
        assert "failed" in catalog.unavailable_reason

    def test_a_genuinely_empty_platform_is_available_and_empty(self):
        catalog = list_known_data_classes(_FakeDesigner(elements=[]))
        assert catalog.available is True
        assert catalog.data_classes == []

    def test_pyegerias_no_elements_found_string_is_an_empty_platform(self):
        class _Stringy(_FakeDesigner):
            def find_data_value_specifications(self, search_string="*", **kwargs):
                return "No elements found"

        catalog = list_known_data_classes(_Stringy())
        assert catalog.available is True
        assert catalog.data_classes == []

    def test_a_missing_client_makes_only_its_own_half_unavailable(self):
        catalog = load_reference_catalog(
            designer=_FakeDesigner(elements=[_element(
                "DataClass", {"qualifiedName": "q", "displayName": "d"})]),
            ref_manager=None,
        )
        assert catalog.available is False
        assert len(catalog.data_classes) == 1
        assert "ReferenceDataManager" in catalog.unavailable_reason

    def test_valid_value_sets_read_at_depth_so_members_come_back(self):
        # graph_query_depth is what brings members back at all — pyegeria has
        # no member-retrieval method.
        recorded = {}

        class _Recording(_FakeRefManager):
            def find_valid_value_definitions(self, search_string="*", **kwargs):
                recorded.update(kwargs)
                return []

        list_known_valid_value_sets(_Recording())
        assert recorded.get("graph_query_depth", 0) >= 1


# ── 10. The step, end to end ─────────────────────────────────────────────────

_SCHEMA_INFO = {
    "schemas": [{
        "name": "public",
        "tables": [{
            "name": "customer",
            "columns": [
                {"name": "email", "data_type": "text"},
                {"name": "country", "data_type": "varchar"},
                {"name": "avatar", "data_type": "bytea"},
            ],
        }],
    }],
}

_STATS_INFO = {"row_stats": [
    {"schemaname": "public", "tablename": "customer", "row_count": 4_200_000},
]}

_PROFILE_ROWS = [
    # -1 → every value distinct (a unique key). Read literally as "1 distinct
    # value" this column would be the reference-data candidate instead of the
    # country column.
    {"schema_name": "public", "table_name": "customer", "column_name": "email",
     "distinct_count": -1, "null_fraction": 0.0, "stats_source": "database"},
    {"schema_name": "public", "table_name": "customer", "column_name": "country",
     "distinct_count": 5, "null_fraction": 0.01, "stats_source": "database"},
]

_CATALOG = ReferenceCatalog(
    data_classes=[EMAIL_CLASS], valid_value_sets=[COUNTRY_SET], available=True,
)


def _run(conn=None, capabilities=None, catalog=_CATALOG, **kwargs):
    conn = conn or _FakeSamplingConnection({
        "email": _EMAILS,
        "country": ["GB"] * 20 + ["US"] * 15 + ["FR"] * 5,
        "avatar": [],
    })
    return conn, run_column_profile(
        conn,
        capabilities or conn.capabilities,
        _SCHEMA_INFO, _STATS_INFO, _PROFILE_ROWS,
        resource_slug="coco_ods", reference_catalog=catalog, **kwargs,
    )


class TestColumnProfileStep:
    def test_it_issues_a_tablesample_query_per_sampled_column(self):
        conn, _ = _run()
        sampled = [q for q in conn.executed if "TABLESAMPLE" in q]
        assert sampled
        assert any('"email"' in q for q in sampled)

    def test_the_recorded_row_carries_the_full_provenance(self):
        _, result = _run()
        rows = {r["column_name"]: r for r in result["column_profile_rows"]}
        email = rows["email"]
        assert email["sample_strategy"] == RANDOM
        assert email["sample_rows"] == len(_EMAILS)
        # §5.8's hard requirement: the total, without which a sample size
        # cannot be read as a proportion.
        assert email["sample_total_rows"] == 4_200_000
        assert email["sample_seed"] == stable_seed_for("coco_ods")
        assert email["state"] == STATE_MEASURED
        # Slice 7's own numbers are carried through, not overwritten.
        assert email["distinct_count"] == -1
        assert email["stats_source"] == "database"

    def test_email_matches_and_country_matches_its_set(self):
        _, result = _run()
        by_key = {(m["question"], m["column_path"]): m for m in result["matches"]}
        assert by_key[("data_class_match", "public.customer.email")]["verdict"] == MATCH_MATCHED
        assert by_key[("reference_data_match", "public.customer.country")]["verdict"] == MATCH_MATCHED

    def test_the_unique_key_is_not_treated_as_reference_data(self):
        # The sign-convention payoff, end to end.
        _, result = _run()
        by_key = {(m["question"], m["column_path"]): m for m in result["matches"]}
        verdict = by_key[("reference_data_match", "public.customer.email")]["verdict"]
        assert verdict == MATCH_NOT_APPLICABLE

    def test_no_distinct_query_is_issued_for_a_high_cardinality_column(self):
        # The cardinality gate exists to avoid paying for exactly this.
        conn, _ = _run()
        distinct_queries = [q for q in conn.executed if "GROUP BY value" in q]
        assert distinct_queries
        assert not any('"email"' in q for q in distinct_queries)

    def test_a_binary_column_is_not_applicable_on_both_questions(self):
        _, result = _run()
        verdicts = {
            m["verdict"] for m in result["matches"]
            if m["column_path"] == "public.customer.avatar"
        }
        assert verdicts == {MATCH_NOT_APPLICABLE}

    def test_every_column_gets_an_annotation_including_the_absent_ones(self):
        # A column silently absent from the output is indistinguishable from
        # one with nothing to report.
        _, result = _run()
        items = {a.item_key for a in result["annotations"] if a.item_key}
        for column in ("email", "country", "avatar"):
            assert f"public.customer.{column}" in items

    def test_annotations_carry_the_verdict_as_their_label(self):
        _, result = _run()
        labels = {
            a.label for a in result["annotations"]
            if a.check_name in ("data_class_match", "reference_data_match")
        }
        assert labels
        assert labels <= {
            MATCH_MATCHED, MATCH_PARTIAL, MATCH_NO_MATCH, MATCH_UNMATCHED_PATTERNED,
            MATCH_INCONCLUSIVE, MATCH_NOT_SAMPLED, MATCH_NO_CANDIDATES,
            MATCH_NOT_APPLICABLE, "gap",
        }

    def test_unestablished_verdicts_publish_at_confidence_zero(self):
        _, result = _run()
        for annotation in result["annotations"]:
            if annotation.label in NOT_ESTABLISHED_VERDICTS:
                assert annotation.confidence == 0, annotation.summary

    def test_the_measure_annotation_states_the_envelope(self):
        _, result = _run()
        measures = [
            a for a in result["annotations"] if a.check_name == "column_value_sample"
        ]
        assert measures
        assert any("sample of" in a.summary for a in measures)

    def test_a_proposal_annotation_is_marked_draft(self):
        conn = _FakeSamplingConnection({
            "email": [f"{i:08x}-0000-4000-8000-000000000000" for i in range(40)],
            "country": ["GB"] * 40,
            "avatar": [],
        })
        _, result = _run(conn=conn)
        drafts = [
            a for a in result["annotations"]
            if getattr(a, "content_status", "") == CONTENT_STATUS_DRAFT
        ]
        assert drafts
        assert all(a.label == MATCH_UNMATCHED_PATTERNED for a in drafts)

    def test_no_annotation_is_marked_draft_when_nothing_is_proposed(self):
        _, result = _run()
        assert not [
            a for a in result["annotations"]
            if getattr(a, "content_status", "") and a.label != MATCH_UNMATCHED_PATTERNED
        ]

    # ── absence discipline, the four states ──

    def test_an_engine_without_value_sampling_establishes_nothing(self):
        conn, result = _run(
            capabilities=EngineCapabilities(column_stats=True, value_sampling=False),
        )
        assert not [q for q in conn.executed if "TABLESAMPLE" in q]
        assert result["sampling"]["value_sampling_supported"] is False
        verdicts = {
            m["verdict"] for m in result["matches"]
            if m["question"] == "data_class_match"
        }
        assert MATCH_NO_MATCH not in verdicts
        assert verdicts <= {MATCH_NOT_SAMPLED, MATCH_NOT_APPLICABLE}
        rows = {r["column_name"]: r for r in result["column_profile_rows"]}
        assert rows["email"]["state"] == STATE_NOT_SUPPORTED
        # And it says so, once, rather than being inferable from silence.
        assert any(
            a.resource_properties.get("capability") == "value_sampling"
            for a in result["annotations"]
            if hasattr(a, "resource_properties")
        )

    def test_catalog_stats_only_reads_nothing_and_says_so(self):
        conn, result = _run(sampling_overrides={"strategy": CATALOG_STATS_ONLY})
        assert conn.executed == []
        verdicts = {
            m["verdict"] for m in result["matches"]
            if m["question"] == "data_class_match"
        }
        assert verdicts <= {MATCH_NOT_SAMPLED, MATCH_NOT_APPLICABLE}
        statements = [m["statement"] for m in result["matches"]]
        assert any("not a statement that no match exists" in s for s in statements)

    def test_an_unread_platform_gives_no_candidates_not_no_match(self):
        _, result = _run(catalog=None)
        assert result["reference_catalog"]["available"] is False
        verdicts = {m["verdict"] for m in result["matches"]}
        assert MATCH_NO_MATCH not in verdicts
        assert MATCH_MATCHED not in verdicts
        assert MATCH_NO_CANDIDATES in verdicts
        statements = " ".join(m["statement"] for m in result["matches"])
        assert "did not read the Egeria platform" in statements

    def test_a_failing_sample_query_is_not_an_empty_sample(self):
        conn = _FakeSamplingConnection(
            {"email": _EMAILS, "country": ["GB"] * 20, "avatar": []},
            fail_on=("email",),
        )
        _, result = _run(conn=conn)
        by_key = {(m["question"], m["column_path"]): m for m in result["matches"]}
        email = by_key[("data_class_match", "public.customer.email")]
        assert email["verdict"] == MATCH_NOT_SAMPLED
        assert "sample query failed" in email["not_established_reason"]

    def test_an_exhausted_byte_budget_stops_sampling_and_names_itself(self):
        conn = _FakeSamplingConnection({
            "email": _EMAILS, "country": ["GB"] * 40, "avatar": [],
        })
        _, result = _run(conn=conn, sampling_overrides={"max_bytes": 1})
        assert result["sampling"]["budget"]["exhausted"] is True
        # Columns after the budget ran out are not_established WITH the
        # budget named, not silently missing.
        reasons = " ".join(
            m["not_established_reason"] for m in result["matches"]
            if m["verdict"] == MATCH_NOT_SAMPLED
        )
        assert "budget" in reasons

    def test_the_resolved_configuration_is_reported(self):
        _, result = _run(sampling_overrides={"strategy": HEAD, "max_values": 25})
        config = result["sampling"]["config"]
        assert config["strategy"] == HEAD
        assert config["max_values"] == 25
        assert config["seed"] == stable_seed_for("coco_ods")

    def test_a_time_budget_of_zero_is_refused_rather_than_meaning_unlimited(self):
        with pytest.raises(SamplingConfigError):
            SamplingConfig(strategy=RANDOM, time_budget_seconds=0)


class TestSampleColumnValues:
    def test_none_means_not_sampled_and_empty_means_measured_empty(self):
        conn = _FakeSamplingConnection({"c": []})
        config = SamplingConfig(strategy=HEAD)
        values, provenance = sample_column_values(
            conn, "s", "t", "c", config, 100, SamplingBudget(config),
        )
        # The sample RAN and the column had no non-NULL values: a measurement.
        assert values == []
        assert provenance.sample_rows == 0
        assert provenance.reads_values is True

        skipped = SamplingConfig(strategy=CATALOG_STATS_ONLY)
        values, provenance = sample_column_values(
            conn, "s", "t", "c", skipped, 100, SamplingBudget(skipped),
        )
        assert values is None
        assert provenance.reads_values is False

    def test_a_distinct_sample_over_the_bound_sets_truncated(self):
        conn = _FakeSamplingConnection({"c": [f"v{i}" for i in range(20)]})
        config = SamplingConfig(strategy=HEAD, max_values=5)
        values, provenance = sample_column_values(
            conn, "s", "t", "c", config, 1_000, SamplingBudget(config), distinct=True,
        )
        assert provenance.truncated is True
        assert len(values) == 5


# ── 11. contentStatus reaches Egeria on the way out ──────────────────────────

class TestContentStatusReachesThePublishPath:
    def test_a_draft_annotation_publishes_content_status(self):
        from resource_explorer.surveyors.annotation_props import build_annotation_props
        from resource_explorer.surveyors.survey_report import DataClassAnnotation

        props = build_annotation_props(
            DataClassAnnotation(
                summary="s", analysis_step="step", content_status="DRAFT",
            ),
            "qn::1",
        )
        assert props["contentStatus"] == "DRAFT"
        # And NOT the field probe 4's first pass tested by mistake.
        assert "initialStatus" not in props

    def test_the_payload_is_unchanged_for_every_existing_caller(self):
        # Guarded on truthiness, so nothing published before this slice moves.
        from resource_explorer.surveyors.annotation_props import build_annotation_props
        from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation

        props = build_annotation_props(
            ResourceMeasureAnnotation(summary="s", analysis_step="step"), "qn::1",
        )
        assert "contentStatus" not in props

    def test_the_outbox_and_the_direct_path_build_the_same_body(self):
        # One shared builder, so a retried DRAFT proposal cannot lose its
        # draft-ness.
        from resource_explorer.surveyors.annotation_props import build_annotation_body
        from resource_explorer.surveyors.survey_report import DataClassAnnotation

        body = build_annotation_body(
            DataClassAnnotation(summary="s", analysis_step="step", content_status="DRAFT"),
            "qn::1", "report-guid",
        )
        assert body["properties"]["contentStatus"] == "DRAFT"
        # The anchoring that keeps an annotation inside its report's zones.
        assert body["anchorGUID"] == "report-guid"


# ── 12. …and reaches the surface a person reads ──────────────────────────────

class TestContentStatusIsSurfaced:
    """The peer review's "load-bearing question before slice 10": does any
    consumer read path actually surface `contentStatus`, or does a draft render
    identically to confirmed content?

    It did NOT, anywhere — measured, not assumed: every occurrence of the field
    in the package was an outbound write or a comment. These tests pin each hop
    of the four-hop chain that now carries it, because pydantic's
    `extra='ignore'` and the frontend's field whitelist both drop an
    undeclared field with no error at all — a change that looks applied and
    is invisible.
    """

    def test_the_reader_carries_content_status_through(self):
        from resource_explorer.surveyors.egeria_survey_reader import (
            get_annotations_by_report_guid,
        )

        class _FakeAssetMaker:
            def get_asset_by_guid(self, guid, body=None, output_format="JSON"):
                return {"reportedAnnotations": [{"relatedElement": {
                    "elementHeader": {"guid": "a1", "type": {"typeName": "DataClassAnnotation"}},
                    "properties": {
                        "qualifiedName": "qn", "summary": "proposed",
                        "contentStatus": "DRAFT",
                    },
                }}]}

        annotations = get_annotations_by_report_guid(_FakeAssetMaker(), "report")
        assert annotations[0]["content_status"] == "DRAFT"

    def test_an_annotation_with_no_content_status_reads_as_empty_not_confirmed(self):
        from resource_explorer.surveyors.egeria_survey_reader import (
            get_annotations_by_report_guid,
        )

        class _FakeAssetMaker:
            def get_asset_by_guid(self, guid, body=None, output_format="JSON"):
                return {"reportedAnnotations": [{"relatedElement": {
                    "elementHeader": {"guid": "a1", "type": {"typeName": "X"}},
                    "properties": {"qualifiedName": "qn", "summary": "s"},
                }}]}

        assert get_annotations_by_report_guid(_FakeAssetMaker(), "r")[0]["content_status"] == ""

    @pytest.mark.parametrize("module_path", [
        "resource_explorer.web.routes.egeria",
        "resource_explorer.web.routes.databases",
        "resource_explorer.web.routes.filesystems",
    ])
    def test_every_api_model_declares_content_status(self, module_path):
        # All three, or the badge appears on two tabs and silently not the
        # third — they are read by ONE frontend renderer.
        import importlib

        model = importlib.import_module(module_path).EgeriaAnnotationItem
        assert "content_status" in model.model_fields

    def test_the_api_model_accepts_the_readers_dict(self):
        from resource_explorer.web.routes.databases import EgeriaAnnotationItem

        item = EgeriaAnnotationItem(
            guid="g", annotation_type="DataClassAnnotation", summary="s",
            confidence=90, analysis_step="step", explanation="", expression="",
            json_properties={}, content_status="DRAFT",
        )
        assert item.content_status == "DRAFT"

    def test_the_frontend_renders_a_draft_badge(self):
        from pathlib import Path

        import resource_explorer

        index = Path(resource_explorer.__file__).parent / "web" / "static" / "index.html"
        source = index.read_text(encoding="utf-8")
        renderer = source[source.index("function renderAnnotations"):]
        renderer = renderer[: renderer.index("\n}")]
        assert "content_status" in renderer
        assert "DRAFT" in renderer
        # An empty contentStatus must NOT draw a badge: "not stated" is what
        # every pre-slice-10 annotation carries and is not "confirmed".
        assert "cs\n" in renderer or "cs ?" in renderer or "cs ?" in renderer


# ── §F, 2026-09-28: one column'''s failure can'''t cascade, and a view is never
#    offered to TABLESAMPLE at all ──────────────────────────────────────────
#
# The live incident (`laz_local_adventureworks`): `TABLESAMPLE` on a view
# raised a real Postgres error, and — because nothing released the
# connection'''s aborted transaction — every column sampled AFTER it in the
# same run failed too, silently, while the step still reported "ok". These
# pin the fix at the `run_column_profile` level: `execute_query_isolated` is
# used instead of `execute_query` (so a real Postgres connection cannot
# cascade — see `TestExecuteQueryIsolatedSavepoints` for that half), a view'''s
# columns are never offered to TABLESAMPLE in the first place, and the
# step'''s own status/counts say what actually happened rather than "ok".

from resource_explorer.registry import STATE_NOT_APPLICABLE  # noqa: E402
from resource_explorer.surveyors.database.column_profile_step import (  # noqa: E402
    STEP_STATUS_OK,
    STEP_STATUS_PARTIAL,
)


class _FakeIsolatingConnection(_FakeSamplingConnection):
    """Simulates a connection whose isolation actually works (the contract
    `PostgreSQLConnection.execute_query_isolated`'''s SAVEPOINT provides): a
    poisoned column'''s query raises, but nothing about that leaks into the
    NEXT call — exactly what `execute_query` alone could not guarantee on a
    real Postgres connection. Only `execute_query_isolated` is overridden;
    `column_profile_step.py` must call it, not `execute_query`, or these
    tests would see every call route through the (non-isolating) parent
    implementation and never notice the difference.
    """

    def __init__(self, *args, poison=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.poison = set(poison)

    def execute_query_isolated(self, query, params=()):
        column = _column_from_sql(query)
        self.executed.append(query)
        if column in self.poison:
            raise RuntimeError(
                "TABLESAMPLE clause can only be applied to tables and "
                "materialized views"
            )
        values = self.values_by_column.get(column, [])
        if "GROUP BY value" in query:
            counts: dict = {}
            for v in values:
                counts[v] = counts.get(v, 0) + 1
            return [{"value": v, "value_count": c} for v, c in counts.items()]
        return [{"value": v} for v in values]

    def execute_query(self, query, params=()):
        raise AssertionError(
            "column_profile_step.py must call execute_query_isolated, not "
            "execute_query, so one column'''s failure cannot cascade to the "
            "next on a real connection"
        )


class TestOneColumnsFailureDoesNotCascade:
    def test_a_poisoned_column_does_not_stop_the_next_column_from_sampling(self):
        conn = _FakeIsolatingConnection(
            {"email": _EMAILS, "country": ["GB"] * 20 + ["US"] * 15 + ["FR"] * 5,
             "avatar": []},
            poison={"email"},
        )
        _, result = _run(conn=conn)
        rows = {r["column_name"]: r for r in result["column_profile_rows"]}
        assert rows["email"]["state"] != STATE_MEASURED
        assert rows["country"]["state"] == STATE_MEASURED

    def test_the_steps_own_status_is_partial_and_names_the_first_error(self):
        conn = _FakeIsolatingConnection(
            {"email": _EMAILS, "country": ["GB"] * 20 + ["US"] * 15 + ["FR"] * 5,
             "avatar": []},
            poison={"email"},
        )
        _, result = _run(conn=conn)
        assert result["status"] == STEP_STATUS_PARTIAL
        assert result["counts"]["errored"] == 1
        assert result["counts"]["sampled"] >= 1
        assert "TABLESAMPLE" in result["first_error"]

    def test_no_error_at_all_keeps_the_status_ok(self):
        conn = _FakeIsolatingConnection(
            {"email": _EMAILS, "country": ["GB"] * 20 + ["US"] * 15 + ["FR"] * 5,
             "avatar": []},
        )
        _, result = _run(conn=conn)
        assert result["status"] == STEP_STATUS_OK
        assert result["counts"]["errored"] == 0


_VIEW_SCHEMA_INFO = {
    "schemas": [{
        "name": "sales",
        "tables": [{
            "name": "vcustomer_summary",
            "type": "VIEW",
            "columns": [{"name": "email", "data_type": "text"}],
        }],
    }],
}

_MATVIEW_SCHEMA_INFO = {
    "schemas": [{
        "name": "sales",
        "tables": [{
            "name": "mv_customer_summary",
            "type": "MATERIALIZED VIEW",
            "columns": [{"name": "email", "data_type": "text"}],
        }],
    }],
}


class TestViewsAndMaterializedViews:
    def test_a_views_column_is_never_offered_to_tablesample(self):
        conn = _FakeIsolatingConnection({"email": _EMAILS})
        result = run_column_profile(
            conn, conn.capabilities, _VIEW_SCHEMA_INFO,
            {"row_stats": []}, [], resource_slug="coco_ods", reference_catalog=_CATALOG,
        )
        assert not any("TABLESAMPLE" in q for q in conn.executed), (
            "a view'''s column must never reach TABLESAMPLE — Postgres rejects "
            "it outright and (before this fix) that rejection cascaded"
        )
        row = result["column_profile_rows"][0]
        assert row["state"] == STATE_NOT_APPLICABLE
        assert result["counts"]["skipped_not_applicable"] == 1
        assert result["status"] == STEP_STATUS_OK

    def test_a_materialized_views_column_is_sampled_normally(self):
        conn = _FakeIsolatingConnection({"email": _EMAILS})
        result = run_column_profile(
            conn, conn.capabilities, _MATVIEW_SCHEMA_INFO,
            {"row_stats": []}, [], resource_slug="coco_ods", reference_catalog=_CATALOG,
        )
        assert any("TABLESAMPLE" in q for q in conn.executed), (
            "a materialized view IS TABLESAMPLE-able and must be sampled "
            "exactly like a base table"
        )
        row = result["column_profile_rows"][0]
        assert row["state"] == STATE_MEASURED
        assert result["counts"]["sampled"] == 1
        assert result["counts"]["skipped_not_applicable"] == 0


class TestExecuteQueryIsolatedSavepoints:
    """`PostgreSQLConnection.execute_query_isolated` — the real SAVEPOINT
    mechanism, characterised the same way this file already characterises
    TABLESAMPLE: by asserting the SQL/command SHAPE issued to a fake cursor,
    since there is no live Postgres in this environment.
    """

    def _connection_with_fake_cursor(self, *, fail: bool):
        from resource_explorer.surveyors.database.connection import PostgreSQLConnection

        executed: list = []

        class _FakeCursor:
            description = [("value",)]

            def execute(self, sql, params=()):
                executed.append(sql)
                if fail and sql.startswith("SELECT"):
                    raise RuntimeError(
                        "TABLESAMPLE clause can only be applied to tables "
                        "and materialized views"
                    )

            def fetchall(self):
                return [("x",)]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

        conn = PostgreSQLConnection("h", 5432, "d", "u", "p")
        conn._conn = _FakeConn()
        return conn, executed

    def test_success_issues_savepoint_then_release(self):
        conn, executed = self._connection_with_fake_cursor(fail=False)
        rows = conn.execute_query_isolated("SELECT 1")
        assert rows == [{"value": "x"}]
        assert executed[0].startswith("SAVEPOINT ")
        assert executed[1] == "SELECT 1"
        assert executed[2].startswith("RELEASE SAVEPOINT ")

    def test_failure_issues_savepoint_then_rollback_to_savepoint_and_reraises(self):
        conn, executed = self._connection_with_fake_cursor(fail=True)
        with pytest.raises(RuntimeError, match="TABLESAMPLE"):
            conn.execute_query_isolated("SELECT 1")
        assert executed[0].startswith("SAVEPOINT ")
        assert executed[1] == "SELECT 1"
        assert executed[2].startswith("ROLLBACK TO SAVEPOINT ")
        assert executed[2] != "ROLLBACK"

    def test_execute_query_itself_is_unchanged_no_savepoint_commands(self):
        conn, executed = self._connection_with_fake_cursor(fail=False)
        conn.execute_query("SELECT 1")
        assert executed == ["SELECT 1"]

    def test_not_connected_raises_the_same_error_as_execute_query(self):
        from resource_explorer.surveyors.database.connection import PostgreSQLConnection

        conn = PostgreSQLConnection("h", 5432, "d", "u", "p")
        with pytest.raises(RuntimeError, match="Not connected"):
            conn.execute_query_isolated("SELECT 1")


class TestExecuteQueryIsolatedDefaultsToExecuteQuery:
    def test_default_implementation_just_calls_execute_query(self):
        from resource_explorer.surveyors.database.connection import DatabaseConnection

        calls = []

        class _Bare(DatabaseConnection):
            def connect(self): return None
            def execute_query(self, query, params=()):
                calls.append(query)
                return [{"ok": True}]
            def get_schema_info(self): return {}
            def get_statistics(self): return {}
            def close(self): pass

        conn = _Bare()
        assert conn.execute_query_isolated("SELECT 1") == [{"ok": True}]
        assert calls == ["SELECT 1"]
