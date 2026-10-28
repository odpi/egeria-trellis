"""Survey Definition step-link reconciliation — docs/survey-question-context-plan.md
follow-up, born from a real live incident (2026-08-13).

Dr.Egeria's "Link First/Next Process Step" commands are NOT idempotent —
unlike "Create/Update Governance Action Process(Step)" (which merge-update
by qualified_name), re-running a Survey Definition's generated markdown
against an already-linked process creates a *brand new* NextProcessStep
relationship alongside the existing one, rather than replacing it. Left
unreconciled, a step ends up with more than one outgoing edge, and
SurveyDefinitionReader._parse_graph() correctly refuses to guess which one
is real — it raises UnsupportedSurveyDefinitionError ("branching ... not
supported"). That's the exact incident that surfaced this: regenerating
docs/dr-egeria/repo-survey-definition-*.md to add D1's ScopedBy Question
links (docs/survey-question-context-plan.md) meant re-running the *whole*
doc, including the pre-existing Link Next Process Step commands, which
duplicated every existing edge across all three repo Survey Definitions (1,
2, and 27 duplicate edges respectively). Repo Full Survey also had one
genuinely stale edge — repo_ci_quality -> repo_api_structure — left over
from before repo_maturity/repo_conventions were inserted into the chain by
an earlier change; a duplicate and a stale edge look identical to the
reader (a step with >1 outgoing edge), so both need reconciling the same
way.

This module is the pure, side-effect-free diff logic (the unit-testable
seam, matching survey_definition_reader.py's own _parse_graph convention)
— it decides *what* to delete. SurveyDefinitionReader.reconcile_step_links()
does the actual fetch + delete against live Egeria.

## Scope-link reconciliation (docs/Backlog.md, "Superseded Question term",
2026-09-13) — a sibling pass to the step-edge one above, same shape (pure
diff here, live fetch+write in the script/reader), but a different
relationship: `ScopedBy`, not `NextGovernanceActionProcessStep`. Two live
incidents motivate it:

  * 2026-08-19 — the questions batch ran after the survey-definitions batch
    once, so every `Link Element To Scope` command reported success against
    Question terms that did not exist yet, and created nothing. The
    definition's own canary was present throughout; only the scoped
    candidate lookup silently returned empty.
  * 2026-09-13 — "What is its internal architecture...?" was split into four
    questions in the CSV; the old term's `ScopedBy` links from
    RepoFullSurvey and RepoArchitectureDiscovery were never removed when the
    replacement links were added, so both definitions stayed scoped to a
    term the authored document no longer names.

Neither incident produces a "branching" symptom the step-edge reconciler can
see — `ScopedBy` isn't `NextGovernanceActionProcessStep`, and nothing about a
missing or extra scope link makes `SurveyDefinitionReader.fetch()` raise.
The definition just quietly answers the wrong (or fewer) questions. See
`expected_scopes_from_document()` / `diff_scopes()` below and
`scripts/reconcile_survey_definition_scopes.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


#: The guard meaning "always follow this edge" — what the generator emits.
UNCONDITIONAL_GUARD = "Any"


def _qn(survey_group: str, key: str) -> str:
    return f"GovActionProcessStep::{survey_group}::{key}"


def compute_expected_edges(survey_group: str, step_keys: list[str]) -> set:
    """The single linear chain of (prev, next, guard) triples a Survey
    Definition's step_keys, in order, should produce — same qualified_name
    convention dr_egeria_survey_publisher.py's _step_qualified_name() uses.
    A 0- or 1-step chain has no edges at all.

    Every edge carries the unconditional guard, because a step list cannot
    express anything else. Use expected_edges_from_document() for a definition
    whose real edges are authored, which is all of them — this remains for the
    case where no document can be found, and is a linear approximation, not a
    definition.
    """
    return {(_qn(survey_group, a), _qn(survey_group, b), UNCONDITIONAL_GUARD)
            for a, b in zip(step_keys, step_keys[1:])}


def expected_edges_from_document(survey_group: str, doc) -> set:
    """The (prev, next, guard) triples a definition's DOCUMENT authors.

    This is what makes branching survivable. A step list yields one linear
    chain; the document yields whatever was actually authored, including two
    edges out of one step under different guards — which is how Egeria
    expresses branching, `NextGovernanceActionProcessStep` being MULTI_LINK by
    design.
    """
    return {(_qn(survey_group, prev), _qn(survey_group, nxt),
             guard or UNCONDITIONAL_GUARD)
            for prev, nxt, guard in getattr(doc, "links", [])}


@dataclass
class LinkToRemove:
    link_guid: str
    prev_qualified_name: str | None
    next_qualified_name: str | None
    reason: str  # "duplicate" | "stale"
    guard: str = ""


@dataclass
class ReconcileResult:
    process_qualified_name: str
    kept: int = 0
    to_remove: list[LinkToRemove] = field(default_factory=list)
    error: str = ""

    @property
    def removed_duplicate(self) -> int:
        return sum(1 for r in self.to_remove if r.reason == "duplicate")

    @property
    def removed_stale(self) -> int:
        return sum(1 for r in self.to_remove if r.reason == "stale")

    @property
    def removed_total(self) -> int:
        return len(self.to_remove)


def diff_links(links: list[dict], expected_edges: set, process_qualified_name: str) -> ReconcileResult:
    """Pure diff — no network calls. Given the raw `processStepLinks` list
    from GovernanceOfficer.get_governance_process_graph()'s response and the
    edges a Survey Definition's document authors, decide which live link
    relationships to keep vs. remove.

    For each expected edge, exactly the *first* matching live link is kept;
    any further live link with the same (prev, next, guard) triple is a
    duplicate. Any live link whose triple isn't expected is stale. Idempotent
    — a fully-reconciled graph produces an empty to_remove list.

    **The guard is part of the identity, and leaving it out was destructive.**
    Keyed on (prev, next) alone, `A -> B guard=passed` and `A -> B
    guard=failed` read as one edge duplicated, and the second is deleted.
    Those are two different edges: the pair of them IS the branch. This
    reconciler exists to strip the copies Dr.Egeria's non-idempotent Link
    commands leave behind, and a copy is identical in all three values — so
    matching on all three removes exactly what it should and nothing more.
    """
    result = ReconcileResult(process_qualified_name=process_qualified_name)
    seen_edges: set = set()

    for link in links:
        prev = (link.get("previousProcessStep") or {}).get("uniqueName")
        nxt = (link.get("nextProcessStep") or {}).get("uniqueName")
        link_guid = link.get("nextProcessStepLinkGUID")
        # An edge with no recorded guard is unconditional, which is what an
        # absent value has always meant here — not a distinct fourth state.
        guard = link.get("guard") or UNCONDITIONAL_GUARD
        edge = (prev, nxt, guard)

        if edge not in expected_edges:
            result.to_remove.append(LinkToRemove(link_guid, prev, nxt, "stale", guard))
        elif edge in seen_edges:
            result.to_remove.append(LinkToRemove(link_guid, prev, nxt, "duplicate", guard))
        else:
            seen_edges.add(edge)
            result.kept += 1

    return result


# ── scope-link reconciliation (ScopedBy vs. an authored document) ──────────

LINK_SCOPE_HEADING = "## Link Element To Scope"


def expected_scopes_from_document(doc_text: str) -> set[str]:
    """The Question display names a Survey Definition document's own
    `## Link Element To Scope` blocks name as `### Scope Reference` — i.e.
    the ScopedBy links the document *authors*, independent of whatever
    `document_for()`/`DefinitionDoc.scoped_by` already parses (this takes raw
    text, not a parsed doc, so it stays usable directly against a fixture
    string or a file read by the caller, and never assumes anything about
    Target Element — every block's Scope Reference counts, matching
    dr_egeria_survey_publisher.render_scope_link_block(), which always
    targets this document's own Survey Definition).

    Every other command heading in the document (`## Create Governance
    Action Process Step`, `## Link Next Process Step`, etc.) is ignored —
    this function answers exactly one question: which Question display
    names does this document say this definition is ScopedBy.
    """
    lines = doc_text.splitlines()
    expected: set[str] = set()
    for i, raw in enumerate(lines):
        if raw.strip() != LINK_SCOPE_HEADING:
            continue
        for j in range(i + 1, len(lines)):
            stripped = lines[j].strip()
            if stripped.startswith("## ") or stripped == "___":
                break
            if stripped != "### Scope Reference":
                continue
            for k in range(j + 1, len(lines)):
                candidate = lines[k].strip()
                if candidate.startswith(("###", "## ")) or candidate == "___":
                    break
                if candidate:
                    expected.add(candidate)
                    break
            break
    return expected


@dataclass
class UnresolvableScope:
    """A live ScopedBy'd element that cannot be matched against `expected` at
    all — not a GlossaryTerm, or a GlossaryTerm with no displayName. Reported
    separately from `extra`: an element like this might be exactly the
    Question the document expects, just unreadable by this diff, and
    counting it as "extra" would recommend removing a link that is actually
    fine. See `find-absence-as-answer`: "could not tell" must never render as
    "measured, and there was nothing" or as "measured, and it's wrong.\""""
    guid: str | None
    type_name: str | None
    qualified_name: str | None
    reason: str


@dataclass
class ExtraScope:
    """One live ScopedBy link the document does not name — carries the term's
    GUID and qualifiedName (not just its displayName) so a removal via
    `ClassificationExplorer.clear_scope_from_element` can target the exact
    relationship, never a name-based re-resolution that could hit a
    different term of the same display name."""
    guid: str
    qualified_name: str | None
    display_name: str


@dataclass
class ScopeReconcileResult:
    process_qualified_name: str
    kept: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[ExtraScope] = field(default_factory=list)
    unresolvable: list[UnresolvableScope] = field(default_factory=list)
    error: str = ""


def diff_scopes(live: list[dict], expected: set[str]) -> ScopeReconcileResult:
    """Pure diff — no network calls. `live` is the raw list
    `ClassificationExplorer.get_scopes(element_guid, page_size=...)` returns
    (each item: `elementHeader.type.typeName`/`elementHeader.guid`,
    `properties.qualifiedName`/`properties.displayName`); `expected` is
    `expected_scopes_from_document()`'s output for this definition's own
    document.

    Matched on **displayName, exact** — never qualifiedName. Dr.Egeria's term
    qualified names are `<Org>::Term::<Hyphenated-Display-Name>::<version>`
    (see docs/dr-egeria/questions/_batch.json's canary-choice comment): the
    org prefix and version are deployment-specific, so a qualifiedName
    comparison would report every Question as missing on any deployment
    other than the one that authored this diff's expectations, which is
    exactly backwards for a check meant to run anywhere. displayName is also
    what `render_scope_link_block()`'s `Scope Reference` names and what
    Dr.Egeria resolves a `Link Element To Scope` command's Scope Reference
    by, so it is the same identity the write path itself uses.

    A live scope element that is not a GlossaryTerm, or is a GlossaryTerm
    with no displayName, goes to `unresolvable` — never to `extra`. Reporting
    it as extra would recommend removing a link this diff cannot actually
    evaluate; `unresolvable` says "look at this by hand" instead of guessing.
    """
    result = ScopeReconcileResult(process_qualified_name="")
    seen_display_names: set[str] = set()

    for item in live:
        if not isinstance(item, dict):
            result.unresolvable.append(
                UnresolvableScope(None, None, None, f"not a dict: {item!r}")
            )
            continue
        header = item.get("elementHeader") or {}
        props = item.get("properties") or {}
        type_name = (header.get("type") or {}).get("typeName")
        guid = header.get("guid")
        qualified_name = props.get("qualifiedName")
        display_name = props.get("displayName")

        if type_name != "GlossaryTerm":
            result.unresolvable.append(
                UnresolvableScope(guid, type_name, qualified_name,
                                  f"not a GlossaryTerm (type={type_name!r})")
            )
            continue
        if not display_name:
            result.unresolvable.append(
                UnresolvableScope(guid, type_name, qualified_name, "GlossaryTerm has no displayName")
            )
            continue

        if display_name in expected:
            result.kept.append(display_name)
            seen_display_names.add(display_name)
        else:
            result.extra.append(ExtraScope(guid, qualified_name, display_name))

    result.missing = sorted(expected - seen_display_names)
    return result


# ── executor reconciliation (engine host case 3, decision 2) ─────────────────
#
# docs/design-notes/SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md: every step of a published
# Survey Definition that RE runs (`executes_at: resource-explorer`) carries exactly ONE `GovernanceActionExecutor`,
# to RE's engine, with the request type of its step key. "Link Action to Action Executor" is not idempotent, so a
# republish can leave two; an old definition has none. Both, a wrong request type, a wrong engine, and an executor to
# RE's engine on a step that says it runs elsewhere (`executes_at` and the executor disagree, design §4.1) are DRIFT:
# reported, never repaired silently (no write path here at all).

EXECUTOR_RELATIONSHIP = "GovernanceActionExecutor"


@dataclass
class StepExecutor:
    """One `GovernanceActionExecutor` of a process step, as Egeria answered it."""
    relationship_guid: str = ""
    engine_guid: str = ""
    engine_qualified_name: str = ""
    request_type: str = ""


@dataclass
class ExecutorDrift:
    step_qualified_name: str
    step_key: str
    #: "missing" | "several" | "wrong_engine" | "wrong_request_type" | "executes_at_disagrees"
    problem: str
    detail: str


def _prop(element: dict, name: str) -> str:
    props = (element or {}).get("elementProperties") or {}
    value = (props.get("propertiesAsStrings") or {}).get(name)
    if value is None:
        entry = (props.get("propertyValueMap") or {}).get(name) or {}
        value = entry.get("primitiveValue") if isinstance(entry, dict) else None
    return str(value or "")


def executors_from_related(answer) -> list[StepExecutor]:
    """Pure: the executors of a process step from `MetadataExpert.get_all_related_elements(step guid,
    graph_query_depth=1)` (the step is end 1 of `GovernanceActionExecutor`, the engine end 2). Egeria's exact "No
    elements found" for a step with no relationships is no executor; any other non-graph answer raises."""
    if isinstance(answer, str):
        if " ".join(answer.split()).lower().rstrip(".") == "no elements found":
            return []
        raise ValueError(f"Egeria answered the step's relationships with {answer[:200]!r}")
    if not isinstance(answer, dict):
        raise TypeError(f"Egeria answered the step's relationships with {type(answer).__name__}")
    out = []
    for entry in answer.get("elementList") or []:
        if ((entry.get("type") or {}).get("typeName")) != EXECUTOR_RELATIONSHIP or entry.get("elementAtEnd1"):
            continue
        engine = entry.get("element") or {}
        rp = ((entry.get("relationshipProperties") or {}).get("propertiesAsStrings") or {})
        out.append(StepExecutor(relationship_guid=str(entry.get("relationshipGUID") or ""),
                                engine_guid=str(engine.get("elementGUID") or ""),
                                engine_qualified_name=_prop(engine, "qualifiedName"),
                                request_type=str(rp.get("requestType") or "")))
    return out


def diff_executors(step_qualified_name: str, step_key: str, executes_at: str, executors: list[StepExecutor], *,
                   engine_qualified_name: str, engine_guid: str = "", expected_request_type: str) -> list[ExecutorDrift]:
    """Pure: the drift of ONE step's executors against decision 2. Empty when the step is as published.

    A step RE runs (`executes_at == "resource-explorer"`): exactly one executor, to RE's engine (by qualifiedName,
    or by GUID when the engine element answered without one), with `expected_request_type`. A step that runs
    elsewhere: no executor to RE's engine (its own engines are not RE's to check)."""
    def is_re(e: StepExecutor) -> bool:
        return bool((e.engine_qualified_name and e.engine_qualified_name == engine_qualified_name)
                    or (engine_guid and e.engine_guid == engine_guid))

    out: list[ExecutorDrift] = []
    if executes_at != "resource-explorer":
        if any(is_re(e) for e in executors):
            out.append(ExecutorDrift(step_qualified_name, step_key, "executes_at_disagrees",
                                     f"executes_at is {executes_at!r} but the step has an executor to "
                                     f"{engine_qualified_name}"))
        return out
    if not executors:
        return [ExecutorDrift(step_qualified_name, step_key, "missing",
                              f"no executor: Egeria cannot dispatch it (expected {engine_qualified_name}, "
                              f"{expected_request_type})")]
    if len(executors) > 1:
        out.append(ExecutorDrift(step_qualified_name, step_key, "several",
                                 f"{len(executors)} executors, expected one (a repeated 'Link Action to Action "
                                 "Executor'): " + "; ".join(f"{e.engine_qualified_name or e.engine_guid} "
                                                            f"{e.request_type}" for e in executors)))
    for e in executors:
        if not is_re(e):
            out.append(ExecutorDrift(step_qualified_name, step_key, "wrong_engine",
                                     f"executor to {e.engine_qualified_name or e.engine_guid or 'an unnamed engine'}"
                                     f", not {engine_qualified_name}"))
        elif e.request_type != expected_request_type:
            out.append(ExecutorDrift(step_qualified_name, step_key, "wrong_request_type",
                                     f"request type {e.request_type or '(none)'}, expected {expected_request_type}"))
    return out
