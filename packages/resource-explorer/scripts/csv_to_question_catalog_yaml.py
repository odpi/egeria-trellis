"""
Generate resource_explorer/configdata/question_catalog.yaml from the same
Scouting questions CSV that scripts/csv_to_dr_egeria_questions.py consumes
(docs/dr-egeria/resource_questions.csv) — the CSV stays the single source
of truth for both outputs (Dr.Egeria markdown for Egeria itself, this YAML
for RE's own runtime "Questions" checklist).

Why a separate script rather than folding this into
csv_to_dr_egeria_questions.py: that script's whole job is producing
Dr.Egeria command blocks — this one produces a config file consumed by
Python at runtime (question_catalog_reader.py, matching
analysis_catalog_reader.py's loading convention). Different output,
different consumer, kept separate on purpose.

Parses the CSV's free-text "Answering Analysis" column into a light,
queryable structure (kind + analysis_ids + the original note text) rather
than requiring a second, redundant structured column in the CSV — the free
text already follows a consistent convention (GAP:/PARTIAL:/MIXED:/"N/A —"
prefixes, established when that column was authored) that a human keeps
writing naturally; this script just recognizes it.

Usage:
    uv run --package resource-explorer \\
        python packages/resource-explorer/scripts/csv_to_question_catalog_yaml.py \\
        docs/dr-egeria/resource_questions.csv \\
        [--output resource_explorer/configdata/question_catalog.yaml]

Rules added 2026-10-22 (REVIEW-QUESTIONS-AND-ANALYTICS-2026-10-22.md §5), after the owner's rulings:

  1. An `Answering Analysis` cell may name only analysis-catalog ids, `direct`/`N/A`, `human`, or GAP/PARTIAL/
     MIXED prose. A STEP name outside a parenthetical gloss (`postgres_column_profile`) fails the build: a step
     is not an analysis, and nothing in the question layer resolves it.
  2. A named Resource Type with no adapter (`dataset`, `model`) fails the build; `*` expands to the kinds that
     have one (repo, database, filesystem).
  3. A question whose Funnel Stage does not include the intent of an analysis it names WARNS, naming both.
  4. An analysis-catalog entry that no question names, and that does not declare `answers_questions: false`
     or a non-empty `provided_by.questions`, WARNS (the orphan check).
  5. A row whose Status is `retired <date>: ...` or `merged into Q-00NN <date>` is left out of the YAML (and out
     of the Dr.Egeria document); its id stays reserved: it counts for the next free id, and it is listed under
     `reserved_question_ids` so `question_catalog_reader.all_question_ids()` still returns it.

Gap-guard note (BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §C, 2026-09-27): a
`GAP:`/`PARTIAL:` note is now checked against KNOWN_REGISTERED_IDS (analysis
ids + step ids) at generation time -- see `_referenced_registered_ids()` and
its call sites in `_parse_answering()` below. This resolves id-shaped TOKENS
in the note's FREE TEXT, unmarked (no backtick convention required), against
the live registry. That is a deliberate choice, not an oversight:

  - The alternative (require a marking, e.g. backticks, before a token
    counts) would need every one of the CSV's existing 21 GAP:/PARTIAL: rows
    retrofitted with backticks to keep passing -- including the two rows
    corrected on 2026-09-27 whose PARTIAL: notes already name a real id in
    plain prose ("the postgres_column_profile step reads pg_stats...",
    "PARTIAL: nested_column_profile (design §5.4...) classifies..."). That
    churn was weighed and rejected: retrofitting unrelated rows' wording to
    satisfy a new lint rule is exactly the kind of edit `docs/dr-egeria/
    resource_questions.csv`'s own reviewers (and the multi-session
    coordination this repo runs under) would rather not see bundled into a
    guard-hardening change.
  - The false-positive risk a marking convention would guard against is an
    incidental snake_case word in a note coincidentally colliding with a
    REGISTERED id. KNOWN_REGISTERED_IDS is a small (~70), curated, domain-
    specific vocabulary of compound identifiers (`postgres_column_profile`,
    `db_fingerprint`, `grain_determination`, ...) engineered to be
    unambiguous -- not the kind of phrase a human writes by accident in
    descriptive prose. A collision requires BOTH an underscore-joined token
    (already a strong signal on its own -- ordinary English prose does not
    write "not_collected" for "not collected") AND that exact string being a
    real, currently-registered id. `TestFalsePositiveRegression` in
    tests/test_question_catalog_gap_guard.py checks two plausible near-miss
    candidates (`not_collected`, `primary_key`) against the live registry
    and confirms neither is registered today.
  - A false positive here is cheap to notice and cheap to fix: it fails this
    generator's own regeneration step, at CSV-authoring time, with the exact
    offending token named in the error -- not a silent, user-facing wrong
    answer. That is a different risk class from the runtime "confident wrong
    answer" bugs this codebase is otherwise careful about (see
    docs/design-notes/implemented/*-IMPLEMENTED.md and the `find-absence-as-answer`
    skill); a noisy, immediately-actionable build failure is the acceptable
    side of that tradeoff.
"""
from __future__ import annotations

import argparse
import copy
import csv
import re
import sys
from pathlib import Path

import yaml

# The resource-type vocabulary is the package's, not a copy — the whole point
# of resource_explorer/resource_types.py (design §13 Phase 0 item 4) is that
# there is one list. This script is normally run under the package's own
# interpreter (`uv run --package resource-explorer`), and the sys.path nudge
# covers the standalone case so the constant is never re-declared here.
if __package__ in (None, ""):  # running as a script, not imported as a module
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from resource_explorer.question_status import is_skipped_status
from resource_explorer.resource_types import (
    ALL_RESOURCE_TYPES_MARKER,
    RESOURCE_TYPES,
    RESOURCE_TYPES_SEPARATOR,
    SURVEYED_RESOURCE_TYPES,
    parse_resource_types,
)

_ANALYSIS_CATALOG_PATH = (
    Path(__file__).parent.parent / "resource_explorer" / "configdata" / "analysis_catalog.yaml"
)


_ANALYSES_KEY_SUFFIX = "_analyses"


def _load_known_analysis_ids() -> list[str]:
    """Valid analysis ids, read live from configdata/analysis_catalog.yaml.

    **Every `*_analyses` section, not just `repo_analyses`** (2026-09-20,
    design §1.1 item 1). Reading only the repo section meant a database
    question naming `schema_inventory` — a real entry in `database_analyses`
    — produced no `analysis_ids` and `kind: unknown`, which reads as "nobody
    has classified this question" rather than "answered, by an analysis this
    generator declined to look for". Sections are discovered rather than
    named, so a `dataset_analyses` section added later needs no change here.

    This was a hand-synced literal of 15 ids until 2026-08-28, when the real
    catalog had 29. The 14 it had never been re-synced with — `cve_scan`,
    `foss_scorecard`, `chaoss_metrics`, `cii_badge`, `community_support`,
    `interface_surface` among them — were unrecognizable to `_parse_answering`,
    so a CSV row naming one was silently emptied of its `analysis_ids` and
    tagged `kind: unknown`. A question could not be wired to a real analysis
    that the generator did not know existed, and nothing said so.

    Read live for the same reason `_load_known_checks` below is: a stale copy
    drops refs silently, where reading the real file fails loudly if it moves.
    The catalog is still parsed as YAML rather than imported through the
    package's own reader (the module-level import of
    `resource_explorer.resource_types` is the one exception, and it is a
    vocabulary constant, not a loader).
    """
    if not _ANALYSIS_CATALOG_PATH.exists():
        raise FileNotFoundError(
            f"analysis catalog not found at {_ANALYSIS_CATALOG_PATH}; the "
            f"question catalog cannot be generated without it."
        )
    cat = yaml.safe_load(_ANALYSIS_CATALOG_PATH.read_text()) or {}
    # `action: publish` entries are excluded. They write to Egeria and produce
    # no findings, so naming one as answering a question can only ever yield a
    # permanent gap asserting a result that will never exist. `egeria_publish`
    # reached the compile exactly this way: "Does it fit into our governance
    # frameworks?" mentions it in PROSE — "plus Curate zone/catalog membership
    # (egeria_publish)" — and this extraction matches any known id anywhere in
    # the note, so an explanatory aside became a dispatch target.
    #
    # `profile` and `ingest` actions stay: they refresh real stored results.
    # Only `publish` is write-only.
    #
    # Deduped across sections while preserving first-seen order: the same
    # analysis id legitimately appears under more than one resource type
    # (`sql_analysis`, `data_class_match` and the rest are shared by design —
    # see design §5.4 and §6.3), and `_is_pure_analysis_list` does whole-string
    # membership tests that must not care which section an id came from.
    ids: list[str] = []
    for key, section in cat.items():
        if not key.endswith(_ANALYSES_KEY_SUFFIX) or key == _ANALYSES_KEY_SUFFIX:
            continue
        for a in section or []:
            if a.get("id") and a.get("action") != "publish" and a["id"] not in ids:
                ids.append(a["id"])
    return ids


def _load_known_analysis_ids_by_type() -> dict[str, set[str]]:
    """Per-resource-type analysis ids, keyed by resource type (not by the
    `<type>_analyses` section name), read from the same catalog as
    `_load_known_analysis_ids()` above.

    That function answers "is this id known to ANY resource type" — needed so
    `_parse_answering()` classifies `kind` correctly no matter which type a row
    ends up applying to (design §1.1 item 1: reading only `repo_analyses` made
    a real `database_analyses` id like `schema_inventory` classify as
    `kind: unknown`). But it was *also* the check `generate()` used when
    stamping a `*`/multi-type row's already-computed `analysis_ids` into each
    type's own section — which answers a different question ("is this id known
    to database specifically") and the union cannot answer it. That gap is
    what let `repository_health`/`chaoss_metrics` (repo-only: they read git
    contributor history) "validate" straight into `database_questions`,
    `filesystem_questions`, `dataset_questions` and `model_questions`, none of
    which have git history to read — found 2026-09-23 auditing the generated
    YAML against `get_analyses(resource_type)`'s real per-type ids. This is
    the per-type index `generate()` needs to catch that: an id absent from
    `resource_type`'s own section is not valid for `resource_type`, full stop,
    regardless of how many other sections it appears in.

    `dataset`/`model` have no section in analysis_catalog.yaml yet (§13 Phase
    0 item 4 added them to the vocabulary with no surveyor behind them) —
    they simply get an empty set here, which is correct: no analysis exists
    for either type today, so every `analysis`-kind row lands as `gap` for
    them until one is built.
    """
    if not _ANALYSIS_CATALOG_PATH.exists():
        raise FileNotFoundError(
            f"analysis catalog not found at {_ANALYSIS_CATALOG_PATH}; the "
            f"question catalog cannot be generated without it."
        )
    cat = yaml.safe_load(_ANALYSIS_CATALOG_PATH.read_text()) or {}
    by_type: dict[str, set[str]] = {}
    for key, section in cat.items():
        if not key.endswith(_ANALYSES_KEY_SUFFIX) or key == _ANALYSES_KEY_SUFFIX:
            continue
        resource_type = key[: -len(_ANALYSES_KEY_SUFFIX)]
        by_type[resource_type] = {
            a["id"] for a in (section or []) if a.get("id") and a.get("action") != "publish"
        }
    return by_type


KNOWN_ANALYSIS_IDS = _load_known_analysis_ids()
KNOWN_ANALYSIS_IDS_BY_TYPE = _load_known_analysis_ids_by_type()


def _load_known_step_ids() -> set[str]:
    """Every `re_analysis_step` key registered across the three resource-type
    adapters -- the ids `survey_definition_executor.py`'s `StepInfo` maps
    declare, read live the same way `_load_known_analysis_ids()` reads
    `analysis_catalog.yaml` above.

    A step is not the same thing as an analysis: `postgres_column_profile`
    and `postgres_nested_columns` are real, executable steps (Phase 1 slices
    10/11) that run and write rows today, but only `nested_column_profile`
    has an `analysis_catalog.yaml` entry wrapping its output as a
    standalone answer -- column profiling itself is consumed by
    `data_class_match`/`reference_data_match` rather than surfaced under its
    own id. `_load_known_analysis_ids()` alone is blind to this: a `GAP:`
    note naming `postgres_column_profile` read as a permanent gap because
    the id genuinely is not in `analysis_catalog.yaml`, even after the step
    existed and ran for weeks. BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §C is
    exactly this -- three CSV rows claimed gaps the code had already closed,
    and the guard that should have caught it only ever consulted the
    analysis catalog.

    Imports the adapter modules directly rather than re-declaring their step
    keys here, for the same reason `_load_known_analysis_ids()` reads the
    YAML live instead of keeping a hand-synced list: a copy silently drops a
    newly-added step, where importing the real registry fails loudly if the
    module itself breaks. This does pull in the full `resource_explorer`
    dependency set (this script is only ever run via
    `uv run --package resource-explorer`, same as `_load_known_checks()`'s
    sibling functions and every test that imports these adapters already
    does)."""
    import resource_explorer.surveyors.filesystem.survey_definition_adapter  # noqa: F401
    from resource_explorer.surveyors.database.survey_definition_adapter import (
        DATABASE_STEP_REGISTRY as _DATABASE_STEPS,
    )
    from resource_explorer.surveyors.repo_survey_definition_adapter import (
        STEP_REGISTRY as _REPO_STEPS,
    )
    from resource_explorer.surveyors.survey_definition_executor import get_adapter

    ids: set[str] = set(_REPO_STEPS) | set(_DATABASE_STEPS)
    filesystem_adapter = get_adapter("filesystem")
    if filesystem_adapter is not None:
        ids |= set(filesystem_adapter.re_analysis_step_info or {})
    return ids


KNOWN_STEP_IDS = _load_known_step_ids()

# The union a GAP:/PARTIAL: note is checked against -- everything that is
# REGISTERED and REAL today, whether or not it is surfaced through the
# question layer as its own analysis. Kept separate from KNOWN_ANALYSIS_IDS
# (which still drives `kind: analysis` classification via
# `_is_pure_analysis_list` below): naming a step id is not the same claim as
# naming an analysis id that fully answers a question on its own, but it is
# just as real a thing to claim does not exist.
KNOWN_REGISTERED_IDS = set(KNOWN_ANALYSIS_IDS) | KNOWN_STEP_IDS


def _load_catalogue_ids_including_publish() -> set[str]:
    """Every analysis-catalog id in every section, `action: publish` entries included.

    `KNOWN_ANALYSIS_IDS` leaves publish entries out on purpose (naming one as the answer to a question yields a
    permanent gap), but the bare-step-name rule below must still treat `egeria_publish` and `egeria_db_survey` as
    catalogue ids, not as steps: both are also step keys, and a note that mentions one in passing names a
    catalogue entry."""
    cat = yaml.safe_load(_ANALYSIS_CATALOG_PATH.read_text()) or {}
    return {
        a["id"]
        for key, section in cat.items()
        if key.endswith(_ANALYSES_KEY_SUFFIX) and key != _ANALYSES_KEY_SUFFIX
        for a in (section or [])
        if a.get("id")
    }


ALL_CATALOGUE_IDS = _load_catalogue_ids_including_publish()

# Matches an id-shaped token: lowercase, snake_case, at least one underscore
# -- e.g. `postgres_column_profile`, `db_derived`. Deliberately free-text
# (unmarked, no backtick requirement) rather than restricted to a marked
# convention -- see the module docstring addendum and
# BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §C / the ...-GAP-GUARD-IMPLEMENTED.md
# doc's "Free-text matching, not backticks" section for the false-positive
# analysis this decision rests on.
_ID_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _referenced_registered_ids(note: str) -> list[str]:
    """Id-shaped tokens in `note` that match a real, currently-registered
    analysis or step id -- sorted, deduped. Empty if the note names nothing
    the registry recognizes (a prose-only gap, or a gap naming something
    genuinely unbuilt, e.g. `db_hub_tables (proposed)`)."""
    tokens = set(_ID_TOKEN_RE.findall(note))
    return sorted(tokens & KNOWN_REGISTERED_IDS)


# Purpose vocabulary: NOT a list held here. Decision (project owner, 2026-10-13): "Egeria holds the set of
# record" (resource_explorer/investigation_purposes.py) and "warn only - suggestive everywhere". A Purposes
# value outside the list is kept as written and warned about, never refused. The generator checks against
# RE's list as `known_purpose_names` gives it: the shipped seed here (no registry is opened from a build
# script), or a caller's list via `generate(rows, known_purposes=...)`.


def _known_purposes() -> list[str]:
    from resource_explorer.investigation_purposes import known_purpose_names

    return known_purpose_names()

# Level vocabulary — the resource granularity at which a question's answer is a
# single value (docs/multi-resource-questions-design.md §18.3, 2026-09-25).
# Engine-neutral on purpose: "schema" does not exist on MySQL and the level
# names come from each engine's containment declaration
# (REPLY-SCHEMA-AS-SUB-RESOURCE.md §5). Per resource type: database =
# database/schema/table/column; filesystem = root/folder/file/field; dataset =
# dataset/distribution/file/field; repo = repository/component/file/symbol.
# Asked above its level, a question answers as a ranked distribution.
KNOWN_LEVELS = ["resource", "container", "member", "field"]

# Columns that are NOT perspectives. Perspectives are identified by
# elimination, so anything missing here silently becomes a phantom Perspective
# on every row — keep in sync with csv_to_dr_egeria_questions.py's
# OPTIONAL_LEAD_COLUMNS, which does the same by-elimination trick.
NON_PERSPECTIVE_COLUMNS = (
    # "Question ID" (2026-10-13): the stable id, assigned once and never derived from the text. Must stay
    # here for the by-elimination reason below, or it becomes a phantom Perspective on every question.
    "Question ID", "Question", "Funnel Stage", "Why is this important?", "Rationale/Source",
    "Answering Analysis", "Answering Mechanism", "Purposes", "Catalog History",
    # "Level" (added 2026-09-25, design §18.3): the granularity the answer is a
    # single value at — one or more of KNOWN_LEVELS, `;`-separated, blank means
    # "resource". By-elimination trap as for every other name here.
    "Level",
    # "Status" (added 2026-09-20, SPEC-ADMIN-THE-FOUR-GAPS.md §4) carries the
    # append-only catalog's retirement marker ("Retired", or empty for
    # active) — see question_catalog_writer.py. Must stay in this list for
    # the same by-elimination reason as its neighbors: any column missing
    # here silently becomes a phantom Perspective on every question.
    "Status",
    # "Resource Types" (added 2026-09-20, docs/multi-resource-questions-design.md
    # §1.1's **Decision (project owner, 2026-09-20)**) carries the
    # `;`-separated resource types a question applies to, `*` for all. Same
    # by-elimination trap as every other name in this tuple — and the trap has
    # fired before: "Catalog History" was added to the CSV without being added
    # here and the next regeneration emitted 17 links to a Perspective that
    # does not exist (see tests/test_question_catalog_generator_guard.py's
    # test_every_perspective_the_document_links_exists_in_the_foundations).
    "Resource Types",
)

_CHECK_REGISTRY_PATH = (
    Path(__file__).parent.parent / "resource_explorer" / "configdata" / "check_registry.yaml"
)


def _load_known_checks() -> set[str]:
    """Valid `analysis_id:check_name` refs, from configdata/check_registry.yaml.

    Read live (unlike KNOWN_ANALYSIS_IDS above, which is a hand-synced literal)
    because the check vocabulary is ~28 entries and still moving — a stale copy
    here would silently drop refs rather than fail. Still no import dependency
    on the resource_explorer package: this reads the YAML directly."""
    if not _CHECK_REGISTRY_PATH.exists():
        return set()
    reg = yaml.safe_load(_CHECK_REGISTRY_PATH.read_text()) or {}
    return {
        f"{analysis_id}:{check}"
        for analysis_id, spec in (reg.get("analyses") or {}).items()
        for check in (spec.get("checks") or [])
    }


_CHECKS_SUFFIX_RE = re.compile(r"\s*\[checks:[^\]]*\]")


def _strip_checks_suffix(note: str) -> str:
    """Remove the `[checks: ...]` block before prose classification.

    The suffix is structured metadata bolted onto a free-text column. Leaving
    it in breaks `kind` detection for every row whose note is exactly an
    analysis id (that test is a whole-string equality), silently downgrading
    e.g. "license_classification" from kind=analysis to kind=unknown."""
    return _CHECKS_SUFFIX_RE.sub("", note).strip()


def _parse_checks(note: str, known: set[str]) -> tuple[list[str], list[str]]:
    """Extract `analysis_id:check_name` refs from the CSV note.

    Returns (valid_refs, unknown_refs). Unknown refs are returned rather than
    dropped so the caller can fail loudly — a typo'd check name that silently
    vanished would look identical to a question nobody has tagged yet, which is
    the failure mode this whole join exists to remove."""
    found = re.findall(r"\b([a-z_]+:[a-z0-9_\-]+)\b", note)
    valid = [r for r in dict.fromkeys(found) if r in known]
    unknown = [r for r in dict.fromkeys(found) if r not in known]
    return valid, unknown


def _parse_purposes(raw: str, known: list[str] | None = None,
                    warnings: list[str] | None = None) -> list[str]:
    """Parse the semicolon-separated Purposes column.

    Purposes get one shared column rather than a column each (the shape the
    Perspective columns use) precisely because of the by-elimination problem
    above: ten more columns would be ten more chances for a typo'd header to
    become a phantom Perspective. One column, checked: a value outside the
    purposes list is KEPT and named in `warnings` (and on stderr), never
    refused (owner ruling 2026-10-13, suggestive everywhere)."""
    from resource_explorer.investigation_purposes import unknown_purpose_warnings

    values = list(dict.fromkeys(v.strip() for v in (raw or "").split(";") if v.strip()))
    said = unknown_purpose_warnings(values, known if known is not None else _known_purposes())
    if warnings is not None:
        warnings.extend(w for w in said if w not in warnings)
    return values


def _parse_levels(raw: str) -> list[str]:
    """Parse the semicolon-separated Level column; blank means ["resource"].

    Validated like Purposes so a typo stops the build instead of creating a
    phantom level. A row may carry several levels when its answer is a single
    value at more than one (e.g. "resource;container" for a size question that
    is natural at the database and breaks down per schema)."""
    values = [v.strip().lower() for v in (raw or "").split(";") if v.strip()]
    if not values:
        return ["resource"]
    unknown = [v for v in values if v not in KNOWN_LEVELS]
    if unknown:
        raise ValueError(
            f"unknown Level(s) {unknown}; valid values are {KNOWN_LEVELS} "
            f"(design §18.3). Fix the CSV."
        )
    return list(dict.fromkeys(values))


_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")


def _is_pure_analysis_list(prose: str) -> bool:
    """Whether the note names only known analyses, joined by `+`.

    A single bare id was the only shape recognized as `kind: analysis` until
    2026-08-28. A question answered by two analyses together — "repository_health
    + chaoss_metrics" — fell through to `unknown`, which reads as "nobody has
    classified this" rather than "answered, by two things". Eight rows sat in
    that state.

    A trailing `(parenthetical)` gloss is stripped before the test, so an author
    can say which part each analysis contributes without losing the
    classification. Anything else in the prose — a dash, a "RAG read?", a
    GAP/PARTIAL/MIXED prefix (already handled above) — leaves this False, so
    genuinely mixed notes still surface as such rather than being flattened
    into a confident `analysis`.
    """
    stripped = _PARENTHETICAL_RE.sub("", prose).strip()
    if not stripped:
        return False
    parts = [p.strip() for p in stripped.split("+")]
    return all(p in KNOWN_ANALYSIS_IDS for p in parts if p)


def _bare_step_names(note: str) -> list[str]:
    """Step ids the note names outside a `(parenthetical gloss)` that are not analysis-catalog ids.

    A step is how something runs; an analysis is what the question layer can resolve and show. A cell that names
    a step as if it were the answer (`the postgres_column_profile step ...`) points at nothing the question layer
    can read, and the row reads as answered or as a gap for the wrong reason (Q-0071 and Q-0079 did, until
    2026-10-22). A parenthetical gloss may still say which step an analysis rides on, so it is not scanned, and a
    step id that is ALSO a catalogue id (`sql_analysis` since 2026-10-22) is an analysis name and passes."""
    text = _PARENTHETICAL_RE.sub(" ", _strip_checks_suffix(note))
    named = set(_ID_TOKEN_RE.findall(text))
    return sorted((named & KNOWN_STEP_IDS) - ALL_CATALOGUE_IDS)


def _parse_answering(note: str, known_checks: set[str] | None = None) -> dict:
    note = (note or "").strip()
    prose = _strip_checks_suffix(note)
    upper = prose.upper()
    if upper.startswith("GAP:"):
        kind = "gap"
    elif upper.startswith("PARTIAL:"):
        kind = "partial"
    elif upper.startswith("MIXED:"):
        kind = "mixed"
    elif "human-supplied" in prose.lower():
        kind = "human"
    elif "trend chart" in prose.lower():
        kind = "chart"
    elif prose.upper().startswith("N/A"):
        kind = "direct"
    elif _is_pure_analysis_list(prose):
        kind = "analysis"
    else:
        # Free text that doesn't match any known convention — surfaced as
        # "unknown" rather than silently mis-tagged; a real gap in the CSV
        # authoring convention, not something to guess past.
        kind = "unknown"

    analysis_ids = [
        aid for aid in KNOWN_ANALYSIS_IDS
        if re.search(rf"\b{re.escape(aid)}\b", note)
    ]

    checks, unknown = _parse_checks(note, known_checks or set())
    if unknown:
        raise ValueError(
            f"unknown check ref(s) {unknown} in Answering Analysis text: {note!r}\n"
            f"Valid refs are declared in {_CHECK_REGISTRY_PATH.name} "
            f"(analysis_id:check_name). Fix the CSV or add the check to the registry."
        )

    # A check ref implies its analysis, so authors don't have to write both.
    for ref in checks:
        aid = ref.split(":", 1)[0]
        if aid not in analysis_ids:
            analysis_ids.append(aid)

    # The gap guard (BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §C): a GAP: note is a
    # claim that NOTHING answers this, and a PARTIAL: note is a claim that
    # SOMETHING named does. Both are checkable against KNOWN_REGISTERED_IDS
    # (analysis ids + step ids), and both failed silently before this --
    # three rows claimed gaps for postgres_column_profile/nested_column_
    # profile/the filesystem reachability probe, all of which existed, and
    # the old guard (analysis_catalog.yaml only, and only for `kind: gap`
    # rows whose `analysis_ids` happened to be populated) could not see any
    # of the three.
    if kind == "gap":
        named = _referenced_registered_ids(note)
        if named:
            plural = "is" if len(named) == 1 else "are"
            raise ValueError(
                f"this gap names something that exists: {named} {plural} a "
                f"registered analysis or step id today, in a GAP: note: {note!r}\n"
                "Re-word as PARTIAL: (if it answers part of the question) or "
                "name the missing piece instead of something already built."
            )
    elif kind == "partial":
        named = _referenced_registered_ids(note)
        if not named:
            raise ValueError(
                f"PARTIAL: note names no registered analysis or step id, so "
                f"there is nothing to check its claim against: {note!r}\n"
                "Name the analysis or step id that answers part of the "
                "question (or re-word as GAP: if nothing does)."
            )

    # After the gap guard on purpose: a GAP naming a step keeps that guard's more specific message ("this gap names
    # something that exists"); this rule is what stops a PARTIAL, MIXED or analysis cell naming a bare step.
    bare_steps = _bare_step_names(note)
    if bare_steps:
        plural = "is a step" if len(bare_steps) == 1 else "are steps"
        raise ValueError(
            f"Answering Analysis names {bare_steps}, which {plural}, not an analysis-catalog id: {note!r}\n"
            "A cell may name only analysis-catalog ids, direct, human, or GAP/PARTIAL/MIXED prose. Name the "
            "catalogue analysis that wraps the step (or give the step a catalogue entry), or move the step name "
            "into a parenthetical gloss."
        )

    return {"kind": kind, "analysis_ids": analysis_ids, "checks": checks, "note": note}


def _restrict_answering_to_type(answering: dict, resource_type: str) -> None:
    """Drop analysis ids/checks from `answering` that are not real for
    `resource_type`, downgrading `kind: analysis` to `kind: gap` if that
    empties it.

    Mutates `answering` in place; caller passes a deep copy so this cannot
    leak across a cross-type row's other stamped entries. `analysis_ids` and
    `checks` were computed once per CSV row against the catalog-wide union
    (`KNOWN_ANALYSIS_IDS`) so `kind` classifies correctly regardless of which
    types the row applies to — that computation is still correct and is left
    alone. What was missing is this second, per-type pass at stamping time:
    an id valid for one type is not automatically valid for another, and a
    `*`/multi-type row was copying the SAME list into every type's section
    unchecked.

    Other kinds (`human`, `mixed`, `partial`, `gap`, `direct`, `chart`,
    `unknown`) never claimed the analysis alone answers the question, so
    there is nothing to downgrade for them — they just lose the inapplicable
    id from the structured `analysis_ids`/`checks` lists. `note` (the CSV's
    verbatim prose) is left untouched in that case; a `gap` note already
    reads as "not built", and a `human`/`mixed` note mentioning an
    out-of-type analysis as context (e.g. "informed by security_scan
    findings") is not a false claim of an answer, just informational text
    that stops being backed by a structured id.
    """
    valid = KNOWN_ANALYSIS_IDS_BY_TYPE.get(resource_type, set())
    original_ids = answering["analysis_ids"]
    dropped = [aid for aid in original_ids if aid not in valid]
    if not dropped:
        return
    answering["analysis_ids"] = [aid for aid in original_ids if aid in valid]
    answering["checks"] = [
        c for c in answering["checks"] if c.split(":", 1)[0] in valid
    ]
    if answering["kind"] == "analysis" and not answering["analysis_ids"]:
        answering["kind"] = "gap"
        plural = "is" if len(dropped) == 1 else "are"
        answering["note"] = (
            f"GAP: {answering['note']} -- {' + '.join(dropped)} {plural} not a "
            f"real analysis for {resource_type} resources (absent from "
            f"analysis_catalog.yaml's {resource_type}_analyses section)."
        )


#: Per-resource-type answering for a row whose one CSV line is shared by several
#: types but whose answer is NOT the same mechanism for each
#: (RULING-DB-QUESTION-CATALOG-CONSISTENCY.md §1, "It's a schema decision").
#:
#: The ruling forbids splitting such a row into a repo copy and a non-repo copy
#: (the writer assumes one row per question text: `add_question` rejects a
#: duplicate and `retire_question` retires only the first match), and leaves
#: "the exact shape to the implementer". This is that shape: the row stays one
#: CSV line, and for the named (question, resource type) the generator replaces
#: the answering note, mechanism, rationale and history with the override's, run
#: through the SAME `_parse_answering` so every guard (gap guard, check refs,
#: kind prefixes) still applies. The CSV text remains the repo's answer and the
#: default for any type not named here.
#:
#: Keys are the CSV's own column names. `tests/test_per_type_answering_overrides.py`
#: fails if a key names a question the CSV does not have, or a type the row does
#: not apply to, so an override cannot outlive its row silently.
_OWNER_Q = "Who owns this resource (accountable owner), and who administers it?"
_LICENSE_Q = "What explicit license does this resource use, and are there non-standard or copyleft terms?"
_CHANGED_Q = "How much has changed since the last time this was surveyed — is it worth re-running now?"
_ESTATE_Q = "Does it fit our estate — the platforms we run, our monitoring, our security controls?"

_OWNER_CLAUSE = (
    "who owns it and who administers it are recorded in Enrichment; no survey "
    "reads either"
)
_OWNER_HISTORY = (
    "2026-10-02: answering reworded for this resource type per "
    "RULING-DB-QUESTION-CATALOG-CONSISTENCY.md section 1; the CSV row is the "
    "repository's answer and was being shown unchanged here."
)


def _human_only(question: str, noun: str, what: str, mech_rationale: str) -> dict:
    # `what` is the whole clause ("the license is recorded in Enrichment; no
    # survey reads it"), so subject/verb agreement is the caller's, not a
    # template's.
    return {
        "Answering Analysis": f"Human-supplied: {what} for a {noun}.",
        "Answering Mechanism": "Human-Supplied",
        "Rationale/Source": mech_rationale,
        "Catalog History": _OWNER_HISTORY if question == _OWNER_Q else (
            "2026-10-02: answering reworded for this resource type per "
            "RULING-DB-QUESTION-CATALOG-CONSISTENCY.md section 1 (license is "
            "the same case as ownership); the CSV row is the repository's "
            "answer and was being shown unchanged here."
        ),
    }


PER_TYPE_ANSWERING_OVERRIDES: dict[str, dict[str, dict[str, str]]] = {
    _OWNER_Q: {
        "database": {
            "Answering Analysis": (
                "MIXED: two halves, and neither is an analysis result on its "
                "own. Who administers the database is measured: a survey reads "
                "the owner role Postgres records for it (pg_database.datdba, "
                "the database_owner fact shown in the Context tab). Who is "
                "accountable for it is human-supplied: it is recorded in "
                "Enrichment, not read from any survey."
            ),
            "Answering Mechanism": "Database Catalog Query + Human-Supplied",
            "Rationale/Source": (
                "Two halves. The administering role is a measured fact about "
                "the database (the owner role in the Postgres catalog). The "
                "accountable owner is a judgement no catalog holds, so it comes "
                "from a person through Enrichment."
            ),
            "Catalog History": _OWNER_HISTORY,
        },
        "filesystem": _human_only(
            _OWNER_Q, "file system", _OWNER_CLAUSE,
            "Nothing in a survey of a file system reads who owns or administers "
            "it, so the answer comes from a person through Enrichment.",
        ),
    },
    _LICENSE_Q: {
        rt: _human_only(
            _LICENSE_Q, noun,
            "the license is recorded in Enrichment; no survey reads it",
            f"Nothing in a survey of a {noun} reads a license, so the answer "
            "comes from a person through Enrichment, which is also where the "
            "license is recorded when the resource is curated into Egeria.",
        )
        # `dataset` and `model` carried overrides here until 2026-10-22; no row may name a kind with no adapter now
        # (expand_resource_types), so there is nothing for them to override.
        for rt, noun in (("database", "database"), ("filesystem", "file system"))
    },
    # Q-0041, "How much has changed since the last time this was surveyed". The CSV row is the repository's answer
    # (refresh_plan); a database or a file system shows the analysis that measures change for it, so a database row
    # does not name a repository analysis (tests/test_db_question_wording.py holds the line). 2026-10-22.
    _CHANGED_Q: {
        "database": {
            "Answering Analysis": (
                "N/A — Automate subscription (notification_detector's change comparison), which surfaces the change; "
                "db_change_rates measures rows changed per table and schema churn between two stored surveys, and "
                "schema_diff the column-level drift"
            ),
            "Answering Mechanism": "Automate Change Detection",
            "Rationale/Source": (
                "notification_detector.py's latest-two-runs comparison, surfaced via an Automate subscription once a "
                "schedule exists for the analysis; db_change_rates and schema_diff are the analyses that measure what "
                "changed in a database."
            ),
            "Catalog History": (
                "2026-10-22: answering names the database's own change analyses instead of the repository's "
                "refresh_plan (review §2)."
            ),
        },
        "filesystem": {
            "Answering Analysis": (
                "N/A — Automate subscription (notification_detector's change comparison), which surfaces the change; "
                "fs_change_rates measures the files added, removed and modified between two walks"
            ),
            "Answering Mechanism": "Automate Change Detection",
            "Rationale/Source": (
                "notification_detector.py's latest-two-runs comparison, surfaced via an Automate subscription once a "
                "schedule exists for the analysis; fs_change_rates is the analysis that differences two walks."
            ),
            "Catalog History": (
                "2026-10-22: answering names fs_change_rates (F5) as the gap instead of the repository's "
                "refresh_plan (review §2). | 2026-10-28: fs_change_rates is built (Files build step 6)."
            ),
        },
    },
    # E1, estate fit. The pre-fill from dependency_support reads a repository's dependency list; a database or a file
    # system has none, so it is a plain Enrichment question there. 2026-10-22.
    _ESTATE_Q: {
        rt: {
            "Answering Analysis": (
                f"N/A — human-supplied via the Enrichment Context form: a person confirms that this {noun} fits the "
                "platforms, monitoring and security controls we run; no survey reads it"
            ),
            "Answering Mechanism": "Human-Supplied",
            "Rationale/Source": (
                f"Nothing in a survey of a {noun} reads the estate it must fit, so the answer comes from a person "
                "through Enrichment; the dependency-support pre-fill exists for repositories only."
            ),
            "Catalog History": (
                "2026-10-22: answering reworded for this resource type, so it does not show a repository's "
                "dependency pre-fill (review §1.5)."
            ),
        }
        for rt, noun in (("database", "database"), ("filesystem", "file system"))
    },
}


def _apply_type_override(entry: dict, resource_type: str, known_checks: set[str]) -> None:
    """Replace `entry`'s answering fields for `resource_type` when
    `PER_TYPE_ANSWERING_OVERRIDES` names this question for this type. Runs
    after `_restrict_answering_to_type`, so an override wins over the
    automatic downgrade it exists to correct."""
    override = PER_TYPE_ANSWERING_OVERRIDES.get(entry["question"], {}).get(resource_type)
    if not override:
        return
    entry["answering"] = _parse_answering(override["Answering Analysis"], known_checks)
    _restrict_answering_to_type(entry["answering"], resource_type)
    entry["answering_mechanism"] = override["Answering Mechanism"]
    entry["rationale"] = override["Rationale/Source"]
    entry["catalog_history"] = override["Catalog History"]


#: A question id is `Q-` and at least four digits, assigned ONCE and stored in the CSV's `Question ID`
#: column. It is deliberately opaque: it names no resource type and no wording, because both change (a
#: shared question gains a type; the text is edited) and an id that encoded either would stop being true.
#: A manifest names the question it answers by this id, so it must never be derived from the text at
#: generation time and never reused (retired rows keep theirs, so "max + 1" cannot collide).
QUESTION_ID_RE = re.compile(r"^Q-\d{4,}$")


def validate_question_ids(rows: list[dict]) -> None:
    """Refuse a catalog whose questions do not each carry one well-formed, unique id. Raises ValueError
    naming every offending question, so one run shows the whole problem."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for row in rows:
        question = (row.get("Question") or "").strip()
        if not question:
            continue
        qid = (row.get("Question ID") or "").strip()
        if not qid:
            problems.append(f"missing Question ID: {question!r}")
        elif not QUESTION_ID_RE.match(qid):
            problems.append(f"malformed Question ID {qid!r} (want Q-0001 style) for {question!r}")
        elif qid in seen:
            problems.append(f"duplicate Question ID {qid!r}: {seen[qid]!r} and {question!r}")
        else:
            seen[qid] = question
    if problems:
        raise ValueError(
            "the question catalog CSV needs one stable, unique `Question ID` per question:\n  - "
            + "\n  - ".join(problems)
            + "\nAssign an id once and keep it when the text changes; never derive it from the text."
        )


def next_question_id(rows: list[dict]) -> str:
    """The next free id: one past the highest numeric id in `rows` (retired rows count, so an id is never
    reused)."""
    top = 0
    for row in rows:
        m = QUESTION_ID_RE.match((row.get("Question ID") or "").strip())
        if m:
            top = max(top, int(m.group(0)[2:]))
    return f"Q-{top + 1:04d}"


def expand_resource_types(raw: str, label: str = "") -> list[str]:
    """The resource types a `Resource Types` cell applies to; refuses a named type that has no adapter.

    `*` expands to the kinds with an adapter (`SURVEYED_RESOURCE_TYPES`). A NAMED kind with no adapter
    (`dataset`, `model`) fails the build instead of emitting a section nothing can answer (owner ruling
    2026-10-22, review §1.1); an unknown name still fails inside `parse_resource_types`."""
    types = parse_resource_types(raw)
    named = [v.strip() for v in (raw or "").split(RESOURCE_TYPES_SEPARATOR) if v.strip()]
    no_adapter = [v for v in named if v != ALL_RESOURCE_TYPES_MARKER and v not in SURVEYED_RESOURCE_TYPES]
    if no_adapter:
        raise ValueError(
            f"{label or 'a row'} names Resource Type(s) {no_adapter} with no adapter; only "
            f"{list(SURVEYED_RESOURCE_TYPES)} can be surveyed today. Drop them from the row (`*` already means "
            f"the kinds that have an adapter), or register an adapter first."
        )
    return types


#: Funnel Stage names the catalogue's `intent` values map onto (case-insensitive). `understanding` and `curate`
#: are intents with no funnel stage, so the stage/intent comparison leaves them alone.
_STAGE_INTENTS = frozenset({"scouting", "discovery", "analysis", "assessment", "enrichment"})


def _catalogue_sections(catalog: dict) -> dict[str, list[dict]]:
    """`{resource_type: [entry, ...]}` for every `<type>_analyses` section, in file order."""
    return {
        key[: -len(_ANALYSES_KEY_SUFFIX)]: list(section or [])
        for key, section in catalog.items()
        if key.endswith(_ANALYSES_KEY_SUFFIX) and key != _ANALYSES_KEY_SUFFIX
    }


def stage_intent_warnings(by_type: dict[str, list[dict]], catalog: dict) -> list[str]:
    """One warning per (question, analysis) whose Funnel Stage does not include the analysis's catalogue intent.

    Warns, never refuses (review §1.3): an analysis may serve two stages (`db_resilience` is a Scouting analysis
    that also answers an Assessment question). The warning names both so the author decides whether the question
    or the catalogue is wrong."""
    intents = {
        rt: {e["id"]: (e.get("intent") or "").strip().lower() for e in entries if e.get("id")}
        for rt, entries in _catalogue_sections(catalog).items()
    }
    said: dict[tuple[str, str, str], str] = {}
    for rt, entries in by_type.items():
        for entry in entries:
            stages = {t.strip().lower() for t in entry["stage"].split("/") if t.strip()}
            for aid in entry["answering"]["analysis_ids"]:
                intent = intents.get(rt, {}).get(aid, "")
                if intent in _STAGE_INTENTS and intent not in stages:
                    said.setdefault((entry["id"], aid, intent), (
                        f"{entry['id']}: stage {entry['stage']!r} does not include the intent {intent!r} of "
                        f"the analysis {aid} it names"))
    return list(said.values())


def orphan_warnings(by_type: dict[str, list[dict]], catalog: dict) -> list[str]:
    """One warning per catalogue entry no question names and that does not declare `answers_questions: false`.

    "Names" is per resource type: a question counts for an entry only in the section of a type the question
    applies to, after the per-type restriction, so `data_class_match` named by a database question does not
    excuse the same id under another section. Infrastructure (ingestion, parsing, publishing) declares
    `answers_questions: false` and is not listed; everything else that answers nothing is, so the list cannot
    regrow silently (review §1.2, §5 rule 4)."""
    named: dict[str, set[str]] = {
        rt: {aid for entry in entries for aid in entry["answering"]["analysis_ids"]}
        for rt, entries in by_type.items()
    }
    out: list[str] = []
    for rt, entries in _catalogue_sections(catalog).items():
        for e in entries:
            if not e.get("id") or e.get("answers_questions") is False:
                continue
            # A library step's declaration names the questions it answers (`provided_by.questions`); that is a
            # question link, so the step is not an orphan (architect ruling 2026-10-22).
            if (e.get("provided_by") or {}).get("questions"):
                continue
            if e["id"] not in named.get(rt, set()):
                out.append(f"analysis {e['id']} ({rt}_analyses) is named by no question and does not declare "
                           f"answers_questions: false")
    return out


def generate(rows: list[dict], known_purposes: list[str] | None = None,
             warnings: list[str] | None = None, catalog: dict | None = None) -> str:
    """The YAML for `rows`. Purposes outside the list are kept; each is named once in `warnings` (when
    given) and on stderr. So are the stage/intent mismatches and the orphan analyses.

    `catalog` is the parsed analysis catalogue the two catalogue-reading warnings compare against; default is
    the shipped `analysis_catalog.yaml`."""
    validate_question_ids(rows)
    known_checks = _load_known_checks()
    known_purposes = known_purposes if known_purposes is not None else _known_purposes()
    purpose_warnings: list[str] = []
    reserved_ids: list[str] = []
    # resource_type -> entries, in first-seen order. A type appears as a key
    # ONLY if a CSV row named it: question_catalog_reader treats a missing key
    # as "not authored for this type", which is the distinction design §1.1
    # item 3 asks for, so emitting an empty list for every known type would
    # reinstate exactly the bug this generator change exists to fix.
    by_type: dict[str, list[dict]] = {}
    for row in rows:
        question = (row.get("Question") or "").strip()
        if not question:
            continue
        if is_skipped_status(row.get("Status")):
            # Retired or merged: no question in any section, but the id stays reserved (it was validated and
            # counted above; the reader returns it from `reserved_question_ids`).
            reserved_ids.append((row.get("Question ID") or "").strip())
            continue
        perspective_cols = [c for c in row if c not in NON_PERSPECTIVE_COLUMNS]
        perspectives = [c for c in perspective_cols if (row.get(c) or "").strip()]
        resource_types = expand_resource_types(
            row.get("Resource Types", ""), f"{(row.get('Question ID') or '').strip()} {question!r}")

        entry = {
            "id": (row.get("Question ID") or "").strip(),
            "question": question,
            "stage": (row.get("Funnel Stage") or "").strip(),
            "perspectives": perspectives,
            "purposes": _parse_purposes(row.get("Purposes", ""), known_purposes, purpose_warnings),
            "levels": _parse_levels(row.get("Level", "")),
            "answering": _parse_answering(row.get("Answering Analysis", ""), known_checks),
            "answering_mechanism": (row.get("Answering Mechanism") or "").strip(),
            "rationale": (row.get("Rationale/Source") or "").strip(),
            "catalog_history": (row.get("Catalog History") or "").strip(),
            "retired": (row.get("Status") or "").strip().lower() == "retired",
        }
        for resource_type in resource_types:
            # A DEEP copy per type, not one shared object and not a shallow
            # copy: yaml.safe_dump emits a `&id001` anchor and an `*id001`
            # alias for any repeated object, and a shallow copy still shares
            # the nested `perspectives`/`purposes`/`answering` objects — which
            # is exactly what the first version of this loop did, caught by
            # test_a_cross_type_row_is_not_a_shared_yaml_anchor. Two resource
            # types would then hand their readers the same mutable entry. A
            # cross-type question is authored once and rendered per type; the
            # YAML should read that way too.
            type_entry = copy.deepcopy(entry)
            _restrict_answering_to_type(type_entry["answering"], resource_type)
            _apply_type_override(type_entry, resource_type, known_checks)
            by_type.setdefault(resource_type, []).append(type_entry)

    if catalog is None:
        catalog = yaml.safe_load(_ANALYSIS_CATALOG_PATH.read_text()) or {}
    catalog_warnings = stage_intent_warnings(by_type, catalog) + orphan_warnings(by_type, catalog)

    header = (
        "# Question checklist catalog — generated by\n"
        "# scripts/csv_to_question_catalog_yaml.py from\n"
        "# docs/dr-egeria/resource_questions.csv (the source of truth — edit that\n"
        "# CSV, then regenerate this file, don't hand-edit it directly). Backs the\n"
        "# Scouting \"Questions\" checklist tab (question_catalog_reader.py).\n"
        "#\n"
        "# One `<resource_type>_questions` key per resource type the CSV's\n"
        "# `Resource Types` column names (`;`-separated, `*` for all — see\n"
        "# docs/multi-resource-questions-design.md §1.1). A resource type with NO\n"
        "# key here has no authored questions, which question_catalog_reader.py\n"
        "# reports as `not_authored` rather than as an empty list: \"nobody has\n"
        "# written database questions yet\" and \"there are database questions and\n"
        "# your filters excluded them all\" are the same length and opposite\n"
        "# answers. Do not add empty sections to make the file look complete.\n"
        "#\n"
        "# Each entry:\n"
        "#   id            - the question's stable id (`Q-0001`), stored in the CSV's\n"
        "#                   `Question ID` column. Assigned once, never derived from the\n"
        "#                   text, never reused; a shared question carries ONE id in every\n"
        "#                   type's section. A manifest names a question by this id, so\n"
        "#                   editing the wording does not orphan it.\n"
        "#   question      - display text; matches the Egeria GlossaryTerm Display\n"
        "#                   Name exactly (case+punctuation-sensitive) since it's\n"
        "#                   also the join key back to Egeria's own Question\n"
        "#                   elements, if/when RE ever queries them live.\n"
        "#   stage         - single Funnel Stage this question belongs to (collapsed\n"
        "#                   from the earlier separate Asked At/Answered At columns\n"
        "#                   2026-08-14 — see docs/survey-question-context-plan.md).\n"
        "#                   May be a slash-combined value (e.g. \"Analysis/Enrichment\")\n"
        "#                   signaling an Analysis-first, Enrichment-fallback pattern\n"
        "#                   (see docs/confidence-gated-validation-plan.md) — treated\n"
        "#                   as a literal string match, not split, until that plan is\n"
        "#                   built.\n"
        "#   perspectives  - Perspective names this question is linked to.\n"
        "#   purposes      - Purpose kinds this question serves (added 2026-08-24).\n"
        "#                   Purpose is the PRIMARY dispatch axis and Perspective the\n"
        "#                   secondary one: Perspective was measured and cannot\n"
        "#                   discriminate (no perspective reaches an analysis another\n"
        "#                   doesn't also reach). Purpose ORDERS what runs by default;\n"
        "#                   it never excludes. See docs/investigation-framing-design.md.\n"
        "#   answering:\n"
        "#     kind        - \"analysis\" | \"direct\" | \"registry\" | \"human\" |\n"
        "#                   \"chart\" | \"gap\" | \"partial\" | \"mixed\" | \"unknown\" —\n"
        "#                   how (or whether) RE can answer this today.\n"
        "#     analysis_ids - analysis_catalog.yaml ids (from ANY `*_analyses`\n"
        "#                   section, not just repo_analyses) that answer\n"
        "#                   (fully or partially) this question; may be empty.\n"
        "#     checks       - finer `analysis_id:check_name` refs, validated against\n"
        "#                   configdata/check_registry.yaml. Added 2026-08-24 because\n"
        "#                   analysis-level joins are too coarse to dispatch on (one\n"
        "#                   analysis, repo_conventions, answers 7 of the 16\n"
        "#                   analysis-answerable questions). Empty means \"no check\n"
        "#                   refs authored yet\" — fall back to analysis_ids, do not\n"
        "#                   read it as \"no checks apply\". A check ref implies its\n"
        "#                   analysis, which is added to analysis_ids automatically.\n"
        "#     note        - the CSV's \"Answering Analysis\" column, verbatim —\n"
        "#                   human-readable detail (candidate tooling for gaps,\n"
        "#                   which direct field/table, etc.).\n"
        "#   answering_mechanism - the CSV's \"Answering Mechanism\" column, verbatim —\n"
        "#                   which kind of engine answers this (Git Statistics /\n"
        "#                   Code Analysis / RAG Queries / Agent-Based Analysis /\n"
        "#                   Egeria Queries / Local Registry Query / Human-Supplied /\n"
        "#                   Direct Field / Trend Chart / Gap / Automate Change\n"
        "#                   Detection, or a \"+\"-joined combination) — orthogonal to\n"
        "#                   stage and to answering.kind, added 2026-08-14.\n"
        "#   rationale   - the CSV's \"Rationale/Source\" column, verbatim — what a\n"
        "#                   question's answer CAN and CANNOT claim. secret_scan\n"
        "#                   never claims \"no secrets\", only no matches against\n"
        "#                   this ruleset in this snapshot; cve_scan sees declared\n"
        "#                   dependencies only. These are the caveats that turn a\n"
        "#                   finding into a claim, so they are carried to the UI\n"
        "#                   rather than left in the CSV. Added 2026-09-11.\n"
        "#   catalog_history - the CSV's \"Catalog History\" column, verbatim — what\n"
        "#                   used to be wrong, which analysis landed when, what the\n"
        "#                   generator could not parse. Past tense, for whoever\n"
        "#                   maintains the catalog; split out of Rationale/Source on\n"
        "#                   2026-09-11 so the limit and the changelog stop sharing\n"
        "#                   one sentence and one colour on screen.\n"
        "#   reserved_question_ids (top-level key, after the sections) - the ids of CSV rows\n"
        "#                   whose Status reads `retired <date>: ...` or `merged into Q-00NN\n"
        "#                   <date>` (2026-10-22). Those rows are in no section; their ids are\n"
        "#                   listed here so an id is never reused and a manifest that names one\n"
        "#                   still names something real (all_question_ids() returns them).\n"
        "#   retired     - true if the CSV's Status column reads \"Retired\" (added\n"
        "#                   2026-09-20, SPEC-ADMIN-THE-FOUR-GAPS.md §4, alongside\n"
        "#                   question_catalog_writer.py's add/retire write path).\n"
        "#                   The catalog is append-only: a retired question is never\n"
        "#                   removed or reworded, only flagged, since a past survey\n"
        "#                   answer still refers to it exactly as it was asked.\n\n"
    )
    # One `<resource_type>_questions` key per type the CSV actually names,
    # ordered by the RESOURCE_TYPES vocabulary so the file's shape does not
    # depend on which row happened to be authored first.
    ordered = {
        f"{rt}_questions": by_type[rt]
        for rt in RESOURCE_TYPES
        if rt in by_type
    }
    # Any type outside the vocabulary cannot reach here — parse_resource_types
    # raises on an unknown value — but keep the assertion rather than silently
    # dropping a key if that ever stops being true.
    assert set(ordered) == {f"{rt}_questions" for rt in by_type}, (
        f"resource types {sorted(set(by_type) - set(RESOURCE_TYPES))} are not in "
        f"the RESOURCE_TYPES vocabulary and would be dropped from the catalog"
    )
    if reserved_ids:
        ordered["reserved_question_ids"] = sorted(reserved_ids)
    body = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=100)
    emitted = purpose_warnings + catalog_warnings
    for w in emitted:
        print(f"question catalog: {w}", file=sys.stderr)
    if warnings is not None:
        warnings.extend(emitted)
    return header + body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv_path", type=Path, help="Source CSV (see module docstring)")
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).parent.parent / "resource_explorer" / "configdata" / "question_catalog.yaml",
        help="Output YAML path (default: resource_explorer/configdata/question_catalog.yaml)",
    )
    args = parser.parse_args()

    if not args.csv_path.exists():
        parser.error(f"CSV not found: {args.csv_path}")

    with open(args.csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        parser.error(f"CSV has no data rows: {args.csv_path}")

    content = generate(rows)
    args.output.write_text(content)
    n_questions = sum(1 for r in rows if (r.get("Question") or "").strip()
                      and not is_skipped_status(r.get("Status")))
    print(f"Wrote {args.output} ({n_questions} active questions from {len(rows)} CSV rows)")


if __name__ == "__main__":
    main()
