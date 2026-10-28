"""A question may not advertise a gap that the analysis catalog contradicts.

`kind: gap` is a load-bearing claim: the UI renders "No mechanism exists for
this yet" and offers no Run button (`can_run` is empty). The guide is explicit
that this is the honest answer more often than it feels like it is -- but only
while it is true.

It stopped being true for the database work. The rows were authored on
2026-09-20 with `GAP: <id> (proposed)` where no analysis existed yet, which was
correct then. The slices that followed built those analyses and registered them
in `analysis_catalog.yaml`, but each slice is forbidden from editing the CSV
(see COORDINATOR-BRIEF-MULTI-RESOURCE.md stream 4, and
POSTGRES-NESTED-COLUMNS-IMPLEMENTED.md section 5, which observes that
restriction "to the letter" and refreshes only the generated YAML). So the
regeneration picks up `analysis_ids: [<id>]` while `kind` stays `gap`, because
kind comes from the `GAP:` prefix in the CSV that the slice may not touch.

`test_a_clean_tree_regenerates_to_nothing` cannot see this: the YAML is exactly
what the CSV generates. Both artifacts agree, and both are wrong about the code.
This guard closes that loop by checking the claim against the built analyses
instead of against the CSV.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "resource_explorer" / "configdata" / "question_catalog.yaml"
ANALYSES = ROOT / "resource_explorer" / "configdata" / "analysis_catalog.yaml"
SCRIPT = ROOT / "scripts" / "csv_to_question_catalog_yaml.py"


def _built_analysis_ids() -> set[str]:
    """Every id registered in analysis_catalog.yaml, across all `*_analyses`
    sections -- the ids the fact layer can actually resolve."""
    catalog = yaml.safe_load(ANALYSES.read_text(encoding="utf-8")) or {}
    ids: set[str] = set()
    for section, entries in catalog.items():
        if not section.endswith("_analyses") or not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id"):
                ids.add(entry["id"])
    return ids


def test_no_question_claims_a_gap_for_an_analysis_that_exists():
    built = _built_analysis_ids()
    assert built, f"{ANALYSES.name} yielded no analysis ids -- the loader is wrong, not the catalog"

    offenders: list[str] = []
    for section, questions in (yaml.safe_load(QUESTIONS.read_text(encoding="utf-8")) or {}).items():
        if not isinstance(questions, list):
            continue
        for question in questions:
            answering = question.get("answering") or {}
            if answering.get("kind") != "gap":
                continue
            live = sorted(set(answering.get("analysis_ids") or []) & built)
            if live:
                offenders.append(
                    f"  [{section}] {question.get('question', '')[:78]}\n"
                    f"      claims a gap, but {', '.join(live)} "
                    f"{'is' if len(live) == 1 else 'are'} in {ANALYSES.name}"
                )

    assert not offenders, (
        f"{len(offenders)} question(s) advertise 'No mechanism exists for this yet' "
        f"for an analysis that is built and registered:\n\n"
        + "\n".join(offenders)
        + "\n\nThe UI hides a working analysis behind each of these, and offers no Run "
        "button. Fix the CSV row -- replace `GAP: <id> (proposed) ...` in "
        "`Answering Analysis` with the bare id (or MIXED:/PARTIAL: if it only answers "
        "part of the question) -- then regenerate:\n"
        "  python scripts/csv_to_question_catalog_yaml.py "
        "docs/dr-egeria/resource_questions.csv "
        "--output resource_explorer/configdata/question_catalog.yaml\n"
        "Never edit the YAML by hand."
    )


# ---------------------------------------------------------------------------
# BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §C, "the gap guard tests nothing"
# ---------------------------------------------------------------------------
#
# The guard above only ever checked a GAP note against analysis_catalog.yaml,
# and only for the ids `_parse_answering()` happened to find in the note's
# free text -- both a real gap (a claim naming a STEP that has no analysis-
# catalog entry reads as a permanent gap, e.g. `postgres_column_profile`) and
# a dead end (a prose-only GAP naming no id at all cannot be checked against
# anything, by either guard). Three rows were hand-corrected on 2026-09-27
# (`re/questions-false-gaps-and-hygiene`) precisely because nothing caught
# them being wrong. These tests exercise the fix, which now lives in
# scripts/csv_to_question_catalog_yaml.py's `_parse_answering()` itself (so
# a bad row fails CSV regeneration directly, not only a separate test) --
# reused here rather than re-implemented, per the "don't build a second
# parser in the test" rule the coordinating session set for this section.


@pytest.fixture(scope="module")
def generator():
    """The real generator module, imported by path -- the same pattern
    test_question_catalog_multi_type.py uses for the same reason: calling
    the real `_parse_answering()` is the point, not a second copy of its
    logic that could silently drift from what actually generates the YAML."""
    spec = importlib.util.spec_from_file_location("csv_to_question_catalog_yaml_gap_guard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


class TestTheRegisteredIdVocabularyIsRealAndNonEmpty:
    """Pins the inputs the rest of this file's assertions depend on -- if
    the step-registry loader silently returned nothing, every test below
    that expects a GAP naming a step id to fail would instead pass for the
    wrong reason (nothing to match against), the same "green because the
    check didn't fire" trap the old guard fell into."""

    def test_step_ids_were_actually_found(self, generator):
        assert generator.KNOWN_STEP_IDS, (
            "the step registry loader found nothing -- the loader broke, "
            "not the registries"
        )
        assert "postgres_column_profile" in generator.KNOWN_STEP_IDS
        assert "postgres_nested_columns" in generator.KNOWN_STEP_IDS

    def test_registered_ids_is_the_union_of_analyses_and_steps(self, generator):
        assert generator.KNOWN_REGISTERED_IDS == (
            set(generator.KNOWN_ANALYSIS_IDS) | generator.KNOWN_STEP_IDS
        )


class TestAGapNamingARegisteredIdFailsTheBuild:
    """The core fix: a GAP: note is a claim that NOTHING answers this
    question. If the note itself names something that exists today -- an
    analysis id or a step id -- that claim is false, and regenerating the
    catalog from a CSV row worded this way must fail loudly rather than
    silently produce a permanent "No mechanism exists" gap in the UI."""

    def test_a_gap_naming_a_registered_step_id_fails(self, generator):
        # The step-named case blind spot #2 describes: postgres_column_profile
        # is a real, running step (Phase 1 slice 10) with no analysis_catalog.yaml
        # entry of its own -- the old guard (analysis ids only) could not see it.
        note = "GAP: postgres_column_profile has not been built; pg_stats is never read."
        with pytest.raises(ValueError, match="this gap names something that exists"):
            generator._parse_answering(note)

    def test_a_gap_naming_a_registered_analysis_id_fails(self, generator):
        note = "GAP: schema_inventory does not exist for this resource type."
        with pytest.raises(ValueError, match="this gap names something that exists"):
            generator._parse_answering(note)

    def test_the_failure_names_the_offending_id(self, generator):
        note = "GAP: postgres_nested_columns is unbuilt; nothing samples these columns."
        with pytest.raises(ValueError, match=r"postgres_nested_columns"):
            generator._parse_answering(note)


class TestAPartialMustNameSomethingRegistered:
    """The mirror rule: a PARTIAL: note is a claim that SOMETHING named DOES
    answer part of the question. A PARTIAL note naming nothing checkable is
    exactly as unverifiable as a prose-only GAP, but PARTIAL does not get the
    same pass -- it is actively claiming an answer exists, so it must say
    which one."""

    def test_a_partial_naming_no_registered_id_fails(self, generator):
        note = "PARTIAL: something answers part of this question, informally."
        with pytest.raises(ValueError, match="names no registered analysis or step id"):
            generator._parse_answering(note)

    def test_a_partial_naming_a_real_analysis_id_passes(self, generator):
        result = generator._parse_answering(
            "PARTIAL: nested_column_profile covers part of this; the rest is unmeasured."
        )
        assert result["kind"] == "partial"
        assert result["analysis_ids"] == ["nested_column_profile"]

    def test_a_partial_naming_a_real_step_id_passes(self, generator):
        # postgres_column_profile is a STEP, not an analysis_catalog.yaml id --
        # this is the real current wording of one of the three 2026-09-27
        # corrections (see TestTheThreeCorrectedRowsRestoredToOldWording below).
        result = generator._parse_answering(
            "PARTIAL: the postgres_column_profile step reads pg_stats but samples nothing else."
        )
        assert result["kind"] == "partial"


class TestAProseOnlyGapCannotBeCheckedAndIsAllowedToStand:
    """Blind spot #1 from the brief, addressed by name rather than silently:
    a GAP note naming no id-shaped token at all has nothing in it to resolve
    against the registry. This guard says so rather than guessing -- it is
    not the same failure as a PARTIAL naming nothing, because GAP is not
    claiming an answer exists. Catching a prose-only false gap needs
    something that can judge the CLAIM against the codebase (a human, or an
    LLM prompted with the codebase) -- a materially larger problem than a
    token-registry lookup, and out of this section's scope."""

    def test_a_prose_only_gap_passes_with_no_ids(self, generator):
        result = generator._parse_answering(
            "GAP: nothing derives this signal from what is already collected."
        )
        assert result["kind"] == "gap"
        assert result["analysis_ids"] == []


class TestFalsePositiveRegression:
    """The safety argument for free-text (unmarked) id matching, made
    concrete: an id-shaped word used descriptively in a note only misfires
    if it happens to collide with something ACTUALLY registered today.
    These two are the brief's own named near-miss candidates -- confirmed
    absent from the live registry, and confirmed not to raise."""

    def test_not_collected_is_not_a_registered_id(self, generator):
        assert "not_collected" not in generator.KNOWN_REGISTERED_IDS

    def test_primary_key_is_not_a_registered_id(self, generator):
        assert "primary_key" not in generator.KNOWN_REGISTERED_IDS

    def test_a_gap_using_an_unregistered_id_shaped_word_does_not_fire(self, generator):
        note = "GAP: these values are not_collected today; nothing populates this column."
        result = generator._parse_answering(note)  # must not raise
        assert result["kind"] == "gap"

    def test_a_partial_using_an_unregistered_id_shaped_word_still_fails(self, generator):
        # PARTIAL's own rule still applies -- an id-shaped word that is not
        # actually registered does not satisfy "must name something real".
        note = "PARTIAL: primary_key coverage looks fine on a skim, nothing measures it."
        with pytest.raises(ValueError, match="names no registered analysis or step id"):
            generator._parse_answering(note)


class TestTheThreeCorrectedRowsRestoredToOldWording:
    """The brief's own Tests section asks for this fixture: restore the
    three false-gap rows corrected in b0d678bc (2026-09-27,
    `re/questions-false-gaps-and-hygiene`) to their old wording and assert
    the new guard fails them.

    **Verified against the actual pre-fix CSV** (`git show
    b0d678bc^:packages/resource-explorer/docs/dr-egeria/resource_questions.csv`)
    rather than assumed -- this brief opens by pointing at a peer session
    that had to correct a wrong "truth" claim in this same brief's own
    Section A during live verification, and flags Section C as carrying the
    same risk. It does: the literal old wording does **not** fail this
    guard, for all three rows, because none of the three names a real,
    registered analysis or step id -- not even under a wrong spelling.

    Their OLD text, verbatim:
      reachability: "GAP: no reachability probe exists -- this needs a
        CHECK_ASSET-only engine action plus the observed-cost comparison
        step_cost_observer already collects." (`resource_reachability` is
        never mentioned; `step_cost_observer` is not a registered id.)
      semi-structured columns: "GAP: no analysis classifies a column as
        semi-structured ... no id in the database analysis catalog performs
        it either." (no id-shaped token appears anywhere in this note.)
      columns-actually-contain: "GAP: column_profile (proposed) -- pg_stats
        is never read, and no sampling fallback exists." (`column_profile`
        was never registered under that exact spelling, proposed or built --
        the real step is `postgres_column_profile`.)

    All three are blind spot #1 (a GAP naming no real id has nothing to
    check), not blind spot #2 (a GAP naming a real STEP id, which the old
    analysis-only guard could not see) -- despite the brief's evidence
    section filing all three under the same "three CSV rows" umbrella. A
    mechanical, token-based guard cannot know that "column_profile
    (proposed)" and "postgres_column_profile" name the same thing, or that
    "no reachability probe exists" went stale once resource_reachability
    shipped under a name this note never used. Closing that gap needs
    matching intent/synonyms, not tokens, and is out of this section's scope
    -- flagged here rather than silently claimed as covered.

    `TestAGapNamingARegisteredIdFailsTheBuild` above demonstrates the guard
    actually catching a GAP, using wording that names the real id the way a
    name-correct restatement of the same original bug would have to; that is
    the guard this section was asked to build, and it works. This class
    documents, instead, exactly where the literal historical text sits
    relative to it.
    """

    OLD_REACHABILITY = (
        "GAP: no reachability probe exists — this needs a CHECK_ASSET-only "
        "engine action plus the observed-cost comparison step_cost_observer "
        "already collects."
    )
    OLD_SEMI_STRUCTURED = (
        "GAP: no analysis classifies a column as semi-structured "
        "(JSON/JSONB/XML/array/hstore) or measures the share of stored bytes "
        "it accounts for -- the catalog reads that already capture column "
        "types perform no such derivation, and no id in the database "
        "analysis catalog performs it either."
    )
    OLD_COLUMN_PROFILE = (
        "GAP: column_profile (proposed) — pg_stats is never read, and no "
        "sampling fallback exists."
    )

    @pytest.mark.parametrize(
        "note",
        [OLD_REACHABILITY, OLD_SEMI_STRUCTURED, OLD_COLUMN_PROFILE],
        ids=["reachability", "semi_structured_columns", "columns_actually_contain"],
    )
    def test_the_literal_old_wording_names_no_registered_id_and_therefore_passes(
        self, generator, note,
    ):
        # If this ever starts raising, the old wording now DOES name
        # something registered (e.g. a step called exactly `column_profile`
        # or `step_cost_observer` got built) -- replace this class's
        # docstring finding and this test with a real "must fail" assertion
        # at that point, rather than leaving the stale claim standing.
        result = generator._parse_answering(note)
        assert result["kind"] == "gap"
        assert result["analysis_ids"] == []
