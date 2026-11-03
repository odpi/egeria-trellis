"""
Externalized analysis catalog — the menu of analyses available per resource
type — loaded from config/analysis_catalog.yaml, so adding, retagging, or
reclassifying an analysis is a config change, not a code change here.

See that file's header comment for the schema and the 7-intent scheme.

This module also owns the (optional, fail-soft) live merge with Egeria's own
Technology Type catalog: get_analyses() can append Egeria-native entries
(via EgeriaTechTypeCatalog.get_produced_annotation_types()) alongside the
local YAML ones. If Egeria is unreachable, only the local entries are
returned — never block or error the whole panel on a live-Egeria failure.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from trellis_analytics.declaration import Declaration
from trellis_analytics.declaration import refusal as declaration_refusal

log = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "configdata" / "analysis_catalog.yaml"

# resource_type key (as used by this catalog / the /api/analyses/{resource_type}
# route) -> Egeria Technology Type display name, for the live-merge lookup.
# Only resource types with a known, confirmed Egeria Technology Type are
# listed here — anything absent simply gets no live-merge entries.
_RESOURCE_TYPE_TO_TECH_TYPE: dict[str, str] = {
    "database": "PostgreSQL Relational Database",
}

# Outcome of the most recent _egeria_merge_entries() call per resource_type —
# "not_applicable" (no tech type mapping configured), "unavailable" (mapping
# exists but the live call failed/errored), or "ok" (call succeeded, even if
# it produced zero entries). Read via get_egeria_merge_status(); lets the UI
# show "live Egeria data unavailable" only when a merge was actually
# attempted and failed, not for resource types that were never wired up.
_last_merge_status: dict[str, str] = {}


def _derive_availability(run_time: str) -> str:
    """"inline" | "queued" from `run_time` — a FALLBACK only, since 2026-08-30.

    `availability` is now a declared field on `AnalysisCatalogEntry`. This
    survives for entries that cannot declare one: `_egeria_merge_entries`'
    hand-built live-Egeria dicts are never `AnalysisCatalogEntry` instances and
    have no catalog row to carry the value.

    It stays conservative for the reason the declared default is conservative —
    only "fast" is inline; everything else, including anything unrecognised, is
    queued, because guessing cheap is the dangerous direction."""
    return "inline" if run_time == "fast" else "queued"


@dataclass(frozen=True)
class AnalysisCatalogEntry:
    id: str
    name: str
    description: str
    resource_types: list[str]
    intent: str
    perspectives: list[str]
    annotation_types: list[str]
    source: str
    run_time: str
    action: str
    recommended: bool
    egeria_registration: dict = field(default_factory=dict)
    # repo scope-narrowing funnel plan (docs/repo-scope-narrowing-funnel.md),
    # D5 — "corpus" | "single_container" | "single_leaf" | "whole_resource_only".
    # Defaults to the conservative "whole_resource_only" for any entry that
    # doesn't declare one (e.g. live-Egeria-merged entries, whose actual
    # shape isn't known) — never assume scopability without confirming it.
    target_shape: str = "whole_resource_only"

    #: "inline" | "queued" — may a context compile run this on the hot path if
    #: no stored result exists, or must it emit a gap and queue?
    #:
    #: DECLARED as of 2026-08-30 (Dan's ruling), no longer derived from
    #: `run_time`.
    #:
    #: §20 of context-compilation-design.md argued for deriving it, and was
    #: right at the time: `run_time` already carried a cost signal, and a
    #: second hand-maintained column is one more thing to keep consistent with
    #: the first. That holds only while the two always agree.
    #:
    #: `architecture_recovery` is the counterexample §20 did not have. Its
    #: COMPUTE is 5.9s, so `run_time: fast` is honest, and `intent: analysis`
    #: is a separate ruling on separate grounds — yet a compile that ran it
    #: inline would also pay ACQUISITION: 14.4s warm, ~30s cold, inside a
    #: packer whose §20 says in bold that it "must never trigger a survey".
    #:
    #: So `run_time` was carrying two loads that came apart — *how expensive is
    #: the compute* and *may a compiler run this inline* — and only the second
    #: has a hard safety requirement. It is now its own answer.
    #:
    #: Unset means **queued**, deliberately. Guessing cheap is the dangerous
    #: direction, and an entry nobody has considered should not license itself
    #: onto a hot path by its own silence — the same conservative default
    #: `target_shape` takes above, for the same reason.
    availability: str = "queued"

    #: ENRICHMENT-E1-CONTEXT-TAB (2026-09-29 amendment, project owner):
    #: analyses whose prerequisite is a HUMAN INPUT rather than a survey read
    #: run at the Enrichment stage — the designer's reply (§0.2) said no
    #: analysis runs there; the owner's correction is that a class does,
    #: gated on something a person supplies rather than something a survey
    #: measures. `requires_input` names that field/source kind in words
    #: ("lens", "documentation_source", "ingested_documentation",
    #: "confirmed_glossary_term") — empty for every analysis unaffected by
    #: this ruling. The prerequisite resolver's new human-input precondition
    #: kind (`surveyors/step_preconditions.py`) reads this value indirectly,
    #: through the catalog id -> requires_input mapping it declares itself,
    #: rather than this field driving resolution directly — see that
    #: module's own comment for why the two are declared in two places on
    #: purpose (the catalog says WHAT is required in words for display; the
    #: resolver says HOW to check it).
    requires_input: str = ""

    #: Extensibility brief 2c: set only on an entry GENERATED from an
    #: installable step's manifest (scripts/generate_external_step_entries.py)
    #: — the package, entry point, step key, version and the manifest's
    #: declarations. Empty for every built-in analysis. RE's host
    #: (`survey_step_host`) reads it to attach the step beside the step
    #: registry, installed or not, so a card whose package is absent says so.
    provided_by: dict = field(default_factory=dict)

    #: What this analysis declares about its results (design note
    #: DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE §1): result
    #: kind, measures with units and as-ofs, absence sentences, Chat extents,
    #: confidence form, answers and scope. The same model a library step's
    #: manifest carries (`trellis_analytics.declaration.Declaration`), so a
    #: built-in analysis and a user step are read by one set of rules. None
    #: when the entry declares nothing, or when what it declared was refused.
    declaration: Declaration | None = None

    #: Why the entry's declaration was refused, in words naming the field and
    #: value; "" when it was accepted or there was none. A refused declaration
    #: does not take the entry, or the catalog, down with it: the analysis is
    #: still listed and runnable, and says why it has no declaration.
    declaration_refused: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "resource_types": self.resource_types,
            "intent": self.intent,
            "perspectives": self.perspectives,
            "annotation_types": self.annotation_types,
            "source": self.source,
            "run_time": self.run_time,
            "availability": self.availability,
            "action": self.action,
            "recommended": self.recommended,
            "egeria_registration": self.egeria_registration,
            "target_shape": self.target_shape,
            "requires_input": self.requires_input,
            **({"provided_by": self.provided_by} if self.provided_by else {}),
            **({"declaration": self.declaration.model_dump(mode="json")} if self.declaration else {}),
            **({"declaration_refused": self.declaration_refused} if self.declaration_refused else {}),
            # The orchestrator step keys this analysis actually runs. Present so
            # a card can offer a scoped Publish: _publishScopedSteps needs the
            # step keys, not the analysis id, and without them the local
            # Analyses cards had no Publish button at all while the Survey
            # Definition cards beside them did. Empty for non-repo entries and
            # for live-Egeria-merged ones, which have no local steps to publish.
            "re_analysis_steps": _re_analysis_steps_for(self.id),
        }


def _re_analysis_steps_for(analysis_id: str) -> list[str]:
    """REPO_ANALYSIS_SOURCE_STEPS lookup, imported lazily.

    repo_survey_definition_adapter imports this module at import time, so a
    module-level import here would close the cycle.

    SOURCE steps, not owned ones. Consumers ask "a survey ran these steps —
    which analyses can it now show results for" (web/routes/survey_definitions
    .py's analysis_ids/perspectives union) and "which steps can this card
    publish". Both are answered by the steps that PRODUCE the data. Off the
    ownership map a survey running repo_arch_detect would not offer
    architecture_diagram's Results view — the analysis computed, reachable from
    nowhere, which is the exact failure this whole area keeps producing.
    """
    try:
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            REPO_ANALYSIS_SOURCE_STEPS,
        )
    except Exception:  # pragma: no cover - defensive
        return []
    return list(REPO_ANALYSIS_SOURCE_STEPS.get(analysis_id, []))


@functools.lru_cache(maxsize=1)
def question_binding() -> dict[str, tuple[str, ...]]:
    """{analysis id: the question ids whose `answering.analysis_ids` name it}, in catalogue order, from every
    `<type>_questions` section of the question catalogue, retired rows excluded.

    This is where a built-in analysis's declared `answers` come from (project owner, 2026-10-28: "generate the
    declaration's answers from the question catalogue's analysis_ids"; one place to edit)."""
    from resource_explorer.surveyors.question_catalog_reader import (
        _DEFAULT_CONFIG_PATH as questions_path,
    )

    if not questions_path.exists():
        return {}
    with open(questions_path) as f:
        raw = yaml.safe_load(f) or {}
    bound: dict[str, list[str]] = {}
    for key, items in raw.items():
        if not key.endswith("_questions") or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("retired"):
                continue
            for a in (item.get("answering") or {}).get("analysis_ids") or []:
                if item.get("id") and item["id"] not in bound.setdefault(a, []):
                    bound[a].append(item["id"])
    return {a: tuple(qs) for a, qs in bound.items()}


def generated_answers(raw: dict) -> list[str]:
    """The `answers` an entry's declaration carries, generated rather than written: a library step's are its
    manifest's questions (`provided_by.questions`, copied by the generator); infrastructure that answers no question
    by declaration (`answers_questions: false`) answers none; every other entry's are the question catalogue's
    binding (`question_binding`)."""
    if raw.get("provided_by"):
        return list((raw.get("provided_by") or {}).get("questions") or [])
    if raw.get("answers_questions") is False:
        return []
    return list(question_binding().get(raw.get("id", ""), ()))


def check_declaration(raw: dict) -> tuple[Declaration | None, str]:
    """The entry's `declaration:` validated, or None and the sentence saying
    why it was refused. No declaration at all is (None, "") — absent is not
    refused; `test_analysis_declarations` is what requires one.

    Beyond the model's own rules, one the catalog adds: a measure that names
    the annotation it writes as must name one of the entry's own
    `annotation_types`, so the two lists cannot drift apart."""
    body = raw.get("declaration")
    if body is None:
        return None, ""
    where = f"analysis catalog entry '{raw.get('id', '?')}'"
    if not isinstance(body, dict):
        return None, f"{where}: declaration is a {type(body).__name__}, not a mapping of declaration fields"
    if "answers" in body and not raw.get("provided_by"):
        return None, (f"{where}: declaration refused: answers is written by hand; they are generated from the "
                      "question catalog's analysis_ids, so remove them here and bind the question there")
    body = {**body, "answers": generated_answers(raw)} if "answers" not in body else body
    try:
        declared = Declaration.model_validate(body)
    except ValueError as exc:
        return None, f"{where}: declaration refused: {declaration_refusal(exc)}"
    types = list(raw.get("annotation_types") or [])
    foreign = [f"{m.name} ({m.annotation.type})" for m in declared.measures
               if m.annotation and m.annotation.type not in types]
    if foreign:
        return None, (f"{where}: declaration refused: measure {', '.join(foreign)} writes as an annotation type "
                      f"the entry does not list in annotation_types ({', '.join(types) or 'none'})")
    return declared, ""


def _entry_from_yaml(raw: dict) -> AnalysisCatalogEntry:
    declared, refused = check_declaration(raw)
    if refused:
        log.error("%s", refused)
    return AnalysisCatalogEntry(
        id=raw["id"],
        name=raw["name"],
        description=raw.get("description", ""),
        resource_types=list(raw.get("resource_types") or []),
        intent=raw.get("intent", ""),
        perspectives=list(raw.get("perspectives") or []),
        annotation_types=list(raw.get("annotation_types") or []),
        source=raw.get("source", "local"),
        run_time=raw.get("run_time", "fast"),
        action=raw.get("action", "survey"),
        recommended=bool(raw.get("recommended", False)),
        egeria_registration=dict(raw.get("egeria_registration") or {}),
        target_shape=raw.get("target_shape", "whole_resource_only"),
        # Unset means "queued" — see the field's own comment. An entry that
        # nobody has considered must not license itself onto a compiler's hot
        # path by staying silent.
        availability=raw.get("availability", "queued"),
        requires_input=raw.get("requires_input", ""),
        provided_by=dict(raw.get("provided_by") or {}),
        declaration=declared,
        declaration_refused=refused,
    )


def installed_step_entries(entity_type: str) -> list[dict]:
    """The catalog entries (raw, the YAML's vocabulary) of every Analytic Library step installed here that measures
    `entity_type`, built from its manifest at read time -- the same `catalog_entry` the generator writes, so the two
    cannot differ in shape. [] when no step is installed, or the library is not (a broken entry point is logged and
    recorded by `survey_step_host.discover_steps`, which this reads through)."""
    from trellis_analytics.discovery import origin

    from resource_explorer.surveyors.survey_step_host import catalog_entry, discover_steps

    out = []
    for name, manifest in sorted(discover_steps(entity_type).items()):
        try:
            where = origin(name)
        except LookupError as exc:
            log.warning("installed step '%s' not merged into the analysis catalog: %s", name, exc)
            continue
        out.append(catalog_entry(manifest, where))
    return out


def _merge_installed(entity_type: str, entries: list[AnalysisCatalogEntry]) -> list[AnalysisCatalogEntry]:
    """The YAML's entries with the installed steps' manifests merged in. An installed step's manifest REPLACES its
    committed generated entry in place (the manifest is the source; the committed entry is only the fallback that
    keeps a step whose package is absent listed, with its "not installed here" reason) and a step with no committed
    entry is appended. A manifest that takes a built-in analysis's id is refused, as `attach` refuses it."""
    merged = list(entries)
    index = {e.id: i for i, e in enumerate(merged)}
    for raw in installed_step_entries(entity_type):
        entry = _entry_from_yaml(raw)
        i = index.get(entry.id)
        if i is None:
            index[entry.id] = len(merged)
            merged.append(entry)
        elif merged[i].provided_by:
            merged[i] = entry
        else:
            log.error("installed step '%s' has a built-in analysis's id; not merged into the catalog", entry.id)
    return merged


@functools.lru_cache(maxsize=1)
def _load(config_path: Path = _DEFAULT_CONFIG_PATH) -> dict[str, list[AnalysisCatalogEntry]]:
    if not config_path.exists():
        return {"repo": [], "database": [], "filesystem": [], "open_data": []}
    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    loaded = {
        "repo": [_entry_from_yaml(e) for e in raw.get("repo_analyses") or []],
        "database": [_entry_from_yaml(e) for e in raw.get("database_analyses") or []],
        "filesystem": [_entry_from_yaml(e) for e in raw.get("filesystem_analyses") or []],
        "open_data": [_entry_from_yaml(e) for e in raw.get("open_data_analyses") or []],
    }
    if config_path == _DEFAULT_CONFIG_PATH:
        # Installed steps join the catalog when it is read: adding a step needs no commit here (F1, 2026-10-29).
        for entity_type in ("database", "filesystem"):
            loaded[entity_type] = _merge_installed(entity_type, loaded[entity_type])
    return loaded


def declaration_refusals() -> list[str]:
    """Every refused declaration in the catalog, one sentence each, naming
    the entry and what was refused. [] when every declaration was accepted."""
    return [e.declaration_refused for entries in _load().values() for e in entries if e.declaration_refused]


def clear_cache() -> None:
    """Testing hook — the loader result is cached via lru_cache."""
    _load.cache_clear()
    question_binding.cache_clear()
    _last_merge_status.clear()


def get_egeria_merge_status(resource_type: str) -> str:
    """Outcome of the most recent live-Egeria merge attempt for this
    resource_type: "not_applicable" | "unavailable" | "ok" | "unknown" (no
    get_analyses() call has run yet this process). Call get_analyses() first
    — this reads the status it left behind, it doesn't trigger a call."""
    return _last_merge_status.get(resource_type, "unknown")


#: The one Perspective vocabulary — Egeria's, authored in
#: docs/dr-egeria/foundations/foundations.md and live, with 41 questions already
#: scoped against it. RE previously carried a parallel set of four
#: (dba/data_scientist/security/steward) whose values could not be compared with
#: the Egeria ones the Questions checklist filters by, so the same word meant
#: different things on two screens and the other ten had no local meaning at all.
#:
#: The four were mapped by DESCRIPTION rather than name: `dba` -> Admin
#: ("operational/infrastructure administration" — and all four dba entries were
#: database operations), `data_scientist` -> Data Expert ("works with, moves and
#: shapes data"). `security`/`steward` had exact namesakes.
EGERIA_PERSPECTIVES = (
    "Admin", "App/AI Builder", "Architecture", "Community", "Consumer",
    "Data Expert", "Data Owner", "Financial", "Governance", "Privacy",
    "Security", "Steward",
)


def list_perspectives() -> list[str]:
    """The perspectives that can actually narrow something, sorted.

    A subset of EGERIA_PERSPECTIVES, not the whole vocabulary: this backs the
    UI's perspective row, and offering a lens nothing is tagged with would be a
    chip that can only ever empty the list.

    The union of two sources, because the row filters two kinds of card. Local
    analyses carry their own tags. Survey Definitions get theirs off the
    ScopedBy graph (Survey -> Question -> Perspective), so every perspective
    the QUESTION catalog uses is reachable by a survey — and those cover eight
    lenses no analysis is tagged with today (Consumer, Data Owner, Community,
    Architecture, ...). Reading only the analyses would have hidden them, and a
    survey tagged Consumer would sit behind a chip that was never offered.
    """
    values: set[str] = set()
    for entries in _load().values():
        for entry in entries:
            values.update(p for p in entry.perspectives if p != "all")
    # Narrow deliberately: a missing/unreadable question catalog is the one
    # expected failure and must not take the analyses half of the row down with
    # it. Anything else is a bug and should surface rather than quietly
    # shrinking the vocabulary to whatever the analyses happen to use.
    try:
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        for q in get_questions():
            values.update(p for p in (q.get("perspectives") or []) if p and p != "all")
    except OSError as exc:
        log.warning("question catalog unreadable, perspective row limited to "
                    "analysis tags: %s", exc)
    return sorted(values)


def _egeria_merge_entries(resource_type: str) -> list[dict]:
    """Live-merge entries sourced from Egeria's Technology Type catalog for
    this resource_type. Fail-soft: any error (no connection, no config,
    Egeria down) logs and returns [] rather than propagating — callers must
    never let this block or blank out the local catalog. Also records the
    outcome in _last_merge_status so callers can distinguish "merge ran and
    found nothing" from "merge couldn't run" — see get_egeria_merge_status()."""
    tech_type = _RESOURCE_TYPE_TO_TECH_TYPE.get(resource_type)
    if not tech_type:
        _last_merge_status[resource_type] = "not_applicable"
        return []
    try:
        from resource_explorer.surveyors.egeria_tech_type_catalog import EgeriaTechTypeCatalog

        catalog = EgeriaTechTypeCatalog()
        produced = catalog.get_produced_annotation_types(tech_type)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, fail-soft by design
        log.info("Egeria live-merge unavailable for %s (%s): %s", resource_type, tech_type, exc)
        _last_merge_status[resource_type] = "unavailable"
        return []

    _last_merge_status[resource_type] = "ok"
    if not produced:
        return []

    entries: list[dict] = []
    for pat in produced:
        annotation_type = pat.get("annotation_type", "")
        entries.append({
            "id": f"egeria::{resource_type}::{pat.get('name', annotation_type)}",
            "name": pat.get("name") or annotation_type or "Egeria Native Analysis",
            "description": pat.get("description") or pat.get("explanation") or "",
            "resource_types": [resource_type],
            "intent": "assessment",
            "perspectives": ["all"],
            "annotation_types": [annotation_type] if annotation_type else [],
            "source": "egeria",
            "run_time": "async",
            # Was missing entirely on this hand-built dict path (unlike
            # AnalysisCatalogEntry.to_dict()'s local entries), which meant any
            # caller indexing a["availability"] -- e.g.
            # test_only_fast_is_inline, or a context-compile packer deciding
            # inline-vs-queued -- hit a bare KeyError the moment Egeria was
            # actually reachable and this merge produced real entries.
            "availability": _derive_availability("async"),
            "action": "survey",
            "recommended": False,
            "egeria_registration": {"candidate": True, "native_process_ref": [resource_type, tech_type]},
            "live_from_egeria": True,
            # Real shape unknown for a live-Egeria-native process — never
            # assume scopability without confirming it (D5).
            "target_shape": "whole_resource_only",
        })
    return entries


def get_analyses(
    resource_type: str,
    intent: str | None = None,
    perspective: str | None = None,
    include_egeria_live: bool = True,
) -> list[dict]:
    """Return analyses for a resource type, optionally filtered by intent and
    perspective. Same signature and same list[dict] return shape as the
    original resource_explorer.analysis_catalog.get_analyses() — this is a
    drop-in replacement.

    When include_egeria_live is True (the default), attempts to merge in
    Egeria-native entries for this resource_type (fail-soft — see
    _egeria_merge_entries). Set False to get local-catalog-only results
    (e.g. for the scheduler, which should never depend on a live Egeria
    call to determine what's due to run)."""
    local = [e.to_dict() for e in _load().get(resource_type, [])]
    live = _egeria_merge_entries(resource_type) if include_egeria_live else []
    analyses = local + live

    if intent and intent != "all":
        analyses = [a for a in analyses if a["intent"] == intent]
    if perspective and perspective != "all":
        analyses = [a for a in analyses if "all" in a["perspectives"] or perspective in a["perspectives"]]
    return analyses


# ── D6 — target-shape compatibility gate ────────────────────────────────────
# A "container" kind (folder/schema) can host a corpus- or container-shaped
# analysis; a "leaf" kind (file/table/column) can host a corpus- or
# leaf-shaped analysis; whole_resource_only is never compatible with any
# selected sub-resource — it's only ever offered against the whole resource
# itself, never a narrower catalog selection.
_CONTAINER_KINDS = {"folder", "schema"}
_LEAF_KINDS = {"file", "table", "column"}


def is_shape_compatible(target_shape: str, selection_kind: str) -> bool:
    """Would an analysis declaring this target_shape produce a meaningful
    result if scoped to a sub-resource of this kind? Pure function over the
    D5 vocabulary — no I/O, safe to call from both the run-time gating route
    and any UI-facing "what can I run here" listing."""
    if target_shape == "corpus":
        return True
    if target_shape == "single_container":
        return selection_kind in _CONTAINER_KINDS
    if target_shape == "single_leaf":
        return selection_kind in _LEAF_KINDS
    return False  # whole_resource_only, or an unrecognized shape — never compatible


def compatible_analyses(resource_type: str, selection_kind: str, **get_analyses_kwargs) -> list[dict]:
    """The subset of get_analyses()'s catalog that could meaningfully run
    scoped to a sub-resource of this kind (D6). Thin filter over
    get_analyses() — same kwargs, same dict shape, so callers don't need a
    second code path for "all analyses" vs. "analyses I can run here"."""
    return [
        a for a in get_analyses(resource_type, **get_analyses_kwargs)
        if is_shape_compatible(a.get("target_shape", "whole_resource_only"), selection_kind)
    ]
