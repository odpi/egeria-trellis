"""Where a finding belongs — on the resource, or on the analysis.

The designer's note (scratchpad SPEC-ACTIONABLE-AND-HONEST.md §3, "Making it
actionable — the missing half is whose absence it is"): the honesty rules this
codebase already applies (result_status.py, facts.py) are scrupulous about
what a finding cannot say and silent about what to do with it. Every finding
about the repository names one of three destinations — `judgement`, `task`,
`context` — and a finding about the ANALYSIS (a disagreement between two
measures, or a check that could not be run) belongs to neither: it goes in the
gaps collection (see gaps.py), marked `ours`.

    judgement  a scored/evaluative finding with no single correct action —
               it becomes an Enrichment field, and the link goes to the
               judgement it informs (e.g. elephant factor — sole; is that a
               risk HERE depends on what the repo is for).
    task       an absence someone can fix — raise an RFA against the repo.
    context    a fact. Not a failure, and not pretending otherwise: a finding
               that implies no action is not a chore.
    ours       computed, never declared — the analysis, not the repository,
               owns this finding (a disagreement between two measures, or a
               check that could not be established). See gaps.py.

`resolve_destination` is the one place that decides this, so a check's
declared default and a label override cannot each answer differently from
what the gaps collection independently detects. It reads
configdata/check_registry.yaml, which is the existing per-check vocabulary
(see that file's own header for `findings_kind` vs. analysis id and the
instance-keyed exclusions) extended with an optional `destinations:` map
alongside each analysis's `checks:` list, plus a `whole_analysis_destinations:`
top-level section for the analyses that answer as a whole (check_registry.yaml
`whole_analysis_only`).
"""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

from resource_explorer.surveyors.result_status import NOT_ESTABLISHED

CONFIGDATA = Path(__file__).parent / "configdata"
REGISTRY_PATH = CONFIGDATA / "check_registry.yaml"

#: The three destinations a check may DECLARE. `ours` is deliberately absent
#: from this set — see module docstring and `resolve_destination` rule (1).
JUDGEMENT = "judgement"
TASK = "task"
CONTEXT = "context"
DECLARABLE = {JUDGEMENT, TASK, CONTEXT}

#: The destination `resolve_destination` computes rather than ever reads from
#: the registry — a finding about the analysis, not the repository.
OURS = "ours"

#: A caller's `state` value meaning "this finding is a disagreement between
#: two measures" — not one of result_status's vocabulary (that describes a
#: single analysis's own run), because a disagreement is a relationship
#: between two analyses' facts and only the caller comparing them can know it.
DISAGREEMENT = "disagreement"

#: Bases `resolve_destination` returns, named so a caller (or a test) can
#: assert on the reason without parsing prose.
BASIS_DISAGREEMENT = "computed: disagreement"
BASIS_NOT_ESTABLISHED = "computed: not established"
BASIS_LABEL_OVERRIDE = "label override"
BASIS_REGISTRY_DEFAULT = "registry default"
BASIS_UNDECLARED = "no destination declared"


@functools.lru_cache(maxsize=1)
def _registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text())


def clear_cache() -> None:
    """Tests that edit the on-disk registry mid-run need this; production
    never rewrites the file, so this is never called outside tests."""
    _registry.cache_clear()


def _check_declaration(kind: str, check_name: str) -> dict | None:
    reg = _registry()
    spec = (reg.get("analyses") or {}).get(kind)
    if spec is not None:
        return (spec.get("destinations") or {}).get(check_name)
    # Whole-analysis-only analyses have no per-check breakdown; their single
    # implicit "check" is the analysis id itself, declared separately so the
    # per-check `analyses` section's shape stays exactly what
    # tests/test_check_registry.py already asserts on.
    return (reg.get("whole_analysis_destinations") or {}).get(kind)


def resolve_destination(kind: str, check_name: str, label: str, state: str) -> tuple[str, str]:
    """Where this one finding belongs, and why.

    `kind` is the analysis id (check_registry.yaml's `analyses` key, or a
    `whole_analysis_only` id). `check_name` is the check within it — pass the
    analysis id again (or "") for a whole-analysis-only finding. `label` is
    the finding's own value/classification (e.g. "sole", "not_established").
    `state` is the caller's result_status state for THIS finding, or
    `DISAGREEMENT` when the caller has independently determined this finding
    is a cross-analysis disagreement — resolve_destination never detects a
    disagreement itself, it only reacts to being told one.

    Resolution order (never revisited once a rule matches):

    1. `state == DISAGREEMENT`, or `state`/`label` is NOT_ESTABLISHED (ran,
       but nothing shows this analysis produced the result — result_status's
       own vocabulary) -> `ours`, computed. This is the one destination a
       check must never declare statically: whether a finding is a
       disagreement or unestablished is discovered per-resource, per-run, and
       a static registry entry could not know it in advance.
    2. A label-specific override in the check's `destinations.labels` map.
    3. The check's `destinations.default`.
    4. `context`, "no destination declared" — a real, queryable gap (see
       tests/test_destinations.py's registry-coverage report) rather than a
       silent default that looks the same as a considered choice.
    """
    if state == DISAGREEMENT:
        return (OURS, BASIS_DISAGREEMENT)
    if state == NOT_ESTABLISHED or label == NOT_ESTABLISHED:
        return (OURS, BASIS_NOT_ESTABLISHED)

    decl = _check_declaration(kind, check_name) or {}
    labels = decl.get("labels") or {}
    if label in labels and labels[label] in DECLARABLE:
        return (labels[label], BASIS_LABEL_OVERRIDE)
    default = decl.get("default")
    if default in DECLARABLE:
        return (default, BASIS_REGISTRY_DEFAULT)
    return (CONTEXT, BASIS_UNDECLARED)


def judgement_field_for(kind: str, check_name: str) -> str | None:
    """The Enrichment field a `judgement`-destination check informs, or None.

    Read from the same per-check declaration as the destination itself
    (`destinations.<check>.judgement_field`) rather than a second table, so a
    check cannot declare a judgement destination without also being asked
    what it links to. None is a real, distinct answer — "this maps to no
    Enrichment field yet" — from "" or a missing key, and callers must not
    invent a field name to fill it.
    """
    decl = _check_declaration(kind, check_name) or {}
    field = decl.get("judgement_field")
    return field if field else None


def all_check_refs() -> list[tuple[str, str]]:
    """Every (analysis_id, check_name) check_registry.yaml declares — the
    per-check `analyses` section's checks, plus one synthetic ref per
    `whole_analysis_only` id (check_name == analysis id, matching how
    `_check_declaration` looks those up)."""
    reg = _registry()
    out = [
        (aid, check)
        for aid, spec in (reg.get("analyses") or {}).items()
        for check in spec.get("checks", [])
    ]
    out += [(aid, aid) for aid in (reg.get("whole_analysis_only") or [])]
    return out


def analyses_with_checks() -> dict:
    """The check_registry.yaml `analyses` section verbatim — every analysis
    with a per-check breakdown, keyed by analysis id. Used by gaps.py to find
    each analysis's `findings_kind` (not always the analysis id — see the
    registry's own header) and its check list, without a second YAML load."""
    return dict(_registry().get("analyses") or {})


def checks_per_analysis() -> dict[str, int]:
    """How many checks (or the one implicit whole-analysis check) each
    analysis has — the "of 11" in "3 of 11 community measures cannot be
    computed" (SPEC-ACTIONABLE-AND-HONEST.md §3). `whole_analysis_only` ids
    count as 1: they answer as a whole, so there is exactly one thing to
    measure, not zero."""
    reg = _registry()
    out = {aid: len(spec.get("checks", [])) for aid, spec in (reg.get("analyses") or {}).items()}
    out.update({aid: 1 for aid in (reg.get("whole_analysis_only") or [])})
    return out


def annotate_checks(kind: str, rows: list, *, whole_state: str = "") -> list:
    """Add `destination`/`destination_basis` (and `judgement_field` when the
    destination is `judgement`) to a list of {check_name, label, ...} finding
    dicts, in place of each dict — the uniform shape most of
    repo_survey_definition_adapter.py's results readers already return under
    a `checks` key.

    `whole_state`, when given, is the analysis's own result_status state
    (Fact.state) — used for rule (1) (NOT_ESTABLISHED/disagreement) ahead of
    each row's own label, so a whole-analysis-level "we could not read this"
    is never masked by a per-row label that happens to look fine.

    Returns the SAME list (mutated), so a caller that already holds a
    reference to it sees the annotation without reassigning."""
    for row in rows:
        if not isinstance(row, dict) or "check_name" not in row:
            continue
        check_name = row.get("check_name", "")
        label = row.get("label", "")
        dest, basis = resolve_destination(kind, check_name, label, whole_state or "")
        row["destination"] = dest
        row["destination_basis"] = basis
        if dest == JUDGEMENT:
            row["judgement_field"] = judgement_field_for(kind, check_name)
    return rows


def checks_lacking_declared_destination() -> list[tuple[str, str]]:
    """Every (analysis_id, check_name) with no `destinations` entry at all —
    the registry-coverage report tests/test_destinations.py asserts against a
    threshold rather than failing outright (see that test's own docstring for
    why: this is meant to be a visible, shrinking number, not a merge-blocker
    the day this lands with most checks undeclared)."""
    return [
        (aid, check) for aid, check in all_check_refs()
        if _check_declaration(aid, check) is None
    ]
