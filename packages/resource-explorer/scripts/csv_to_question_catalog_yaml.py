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
from resource_explorer.resource_types import (
    RESOURCE_TYPES,
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


# Purpose vocabulary — the controlled kinds from
# docs/investigation-framing-design.md §2. Mirrors Egeria's ProjectCharter
# `purposes`, which is a valid metadata set: controlled so dispatch can key on
# it, extensible without a rebuild. Extend here when the charter vocabulary
# does.
KNOWN_PURPOSES = [
    "Explore",
    "Select",
    "Assess",
    "Maintain",
    "Share",
    "Learn",
    "Certify",
    "Remediate",
    "Attest",
    "Deploy",
]

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
    "Question", "Funnel Stage", "Why is this important?", "Rationale/Source",
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


def _parse_purposes(raw: str) -> list[str]:
    """Parse the semicolon-separated Purposes column.

    Purposes get one shared column rather than a column each (the shape the
    Perspective columns use) precisely because of the by-elimination problem
    above: ten more columns would be ten more chances for a typo'd header to
    become a phantom Perspective. One column, validated, fails loudly instead."""
    values = [v.strip() for v in (raw or "").split(";") if v.strip()]
    unknown = [v for v in values if v not in KNOWN_PURPOSES]
    if unknown:
        raise ValueError(
            f"unknown Purpose(s) {unknown}; valid values are {KNOWN_PURPOSES}. "
            f"Fix the CSV, or add the purpose to KNOWN_PURPOSES if the "
            f"ProjectCharter vocabulary genuinely gained one."
        )
    return list(dict.fromkeys(values))


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


def generate(rows: list[dict]) -> str:
    known_checks = _load_known_checks()
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
        perspective_cols = [c for c in row if c not in NON_PERSPECTIVE_COLUMNS]
        perspectives = [c for c in perspective_cols if (row.get(c) or "").strip()]
        resource_types = parse_resource_types(row.get("Resource Types", ""))

        entry = {
            "question": question,
            "stage": (row.get("Funnel Stage") or "").strip(),
            "perspectives": perspectives,
            "purposes": _parse_purposes(row.get("Purposes", "")),
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
            by_type.setdefault(resource_type, []).append(type_entry)

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
    body = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=100)
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
    n_questions = sum(1 for r in rows if (r.get("Question") or "").strip())
    print(f"Wrote {args.output} ({n_questions} questions from {len(rows)} CSV rows)")


if __name__ == "__main__":
    main()
