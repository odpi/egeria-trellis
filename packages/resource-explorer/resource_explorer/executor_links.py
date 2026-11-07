"""Executor-link reconciler: exactly ONE `Link Action to Action Executor` to RE's engine per GovernanceActionType the
Dr.Egeria documents declare (RE-HEAL-LEDGER-TRUST decision 5, 2026-11-07).

Why it exists. Dr.Egeria's "Link Action to Action Executor" does not look for an existing relationship, so a repeat
makes a second `GovernanceActionExecutor`. A reset makes the opposite gap: a GovernanceActionType re-created (by a heal
or by hand) after its engine was gone has NO executor, and a document run while the engine is absent "succeeds" with
every link failing ("Referenced element 'ResourceExplorerSurvey' for attribute 'Governance Engine' not found", 15 of
them on 2026-11-07, finished by hand from a 15-block subset document). The Architect's 2026-11-04 Q2 ruling ("no
post_heal reconciler") assumed a heal never meets types without links; the reset is exactly that case.

What it does. The DECLARED links are read from the documents (each `## Link Action to Action Executor` block's
Governance Action Type / Governance Engine / Request Type). For each one whose engine is RE's (`ResourceExplorerSurvey`)
it reads the type's relationships (`MetadataExpert.get_all_related_elements(type guid, graph_query_depth=1)`, the same
read `survey_definition_reconciler.executors_from_related` parses) and classifies:

* ``ok``                 — exactly one executor to RE's engine, with the declared request type;
* ``missing``            — none: Egeria cannot dispatch it. Added with ``apply``;
* ``several``            — more than one to RE's engine (a repeated link). The oldest is kept, the rest removed with
                           ``apply`` (generic relationship delete, SOFT_DELETE, as the step-link reconciler does);
* ``wrong_request_type`` — one, but not the declared request type. Reported, never changed (which is right is not
                           this script's to guess);
* ``type_absent``        — the GovernanceActionType is not in Egeria: nothing to link. Reported (its document did not
                           run; the heal, not this script, makes types);
* ``unreadable``         — a lookup could not answer. Reported; never treated as missing, so nothing is written for it.

Reads first, writes only with ``apply``, and then re-reads each changed type to say what is there now. The engine
itself absent is a refusal of the whole pass (every link would fail): nothing is written.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ENGINE_QUALIFIED_NAME = "ResourceExplorerSurvey"
LINK_HEADING = "## Link Action to Action Executor"
OPERATION = "reconcile_executor_links"

OK, MISSING, SEVERAL, WRONG_REQUEST_TYPE, TYPE_ABSENT, UNREADABLE = (
    "ok", "missing", "several", "wrong_request_type", "type_absent", "unreadable")
#: Problems `apply` repairs. The others are reported only.
REPAIRABLE = (MISSING, SEVERAL)
_ABSENT = ("no element found", "no elements found")


@dataclass(frozen=True)
class DeclaredLink:
    governance_action_type: str
    governance_engine: str
    request_type: str
    document: str = ""


@dataclass
class LinkState:
    declared: DeclaredLink
    problem: str
    detail: str = ""
    type_guid: str = ""
    #: relationship GUIDs of the executors to RE's engine, oldest first
    executor_guids: list[str] = field(default_factory=list)
    action: str = ""             # what `apply` did: "added", "removed N", "" (nothing), or "failed: …"

    def item(self) -> dict:
        out = {"governance_action_type": self.declared.governance_action_type,
               "request_type": self.declared.request_type, "document": self.declared.document,
               "status": self.problem, "detail": self.detail}
        if self.action:
            out["action"] = self.action
        return out


@dataclass
class Report:
    engine_guid: str = ""
    refused: str = ""            # non-empty: nothing was looked at past the engine (e.g. the engine is absent)
    states: list[LinkState] = field(default_factory=list)
    applied: bool = False

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.states:
            out[s.problem] = out.get(s.problem, 0) + 1
        return out

    @property
    def drift(self) -> int:
        return sum(1 for s in self.states if s.problem != OK)

    def words(self) -> str:
        if self.refused:
            return f"executor links not reconciled: {self.refused}"
        counts = self.counts()
        tally = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(counts.items())) or "nothing declared"
        did = [f"{s.declared.governance_action_type}: {s.action}" for s in self.states if s.action]
        head = (f"executor links: {len(self.states)} declared to {ENGINE_QUALIFIED_NAME} · {tally} · "
                f"drift {self.drift}")
        return head + (f" · {'applied' if self.applied else 'dry run'}: " + "; ".join(did) if did else "")


# ── the declared links (pure) ─────────────────────────────────────────────────

def _sections(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    lines: list[str] = []
    for line in block.splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                out[current] = "\n".join(lines).strip()
            current, lines = m.group(1).strip(), []
        elif current is not None:
            lines.append(line)
    if current is not None:
        out[current] = "\n".join(lines).strip()
    return out


def declared_links(text: str, document: str = "") -> list[DeclaredLink]:
    """Pure: every `## Link Action to Action Executor` block of a Dr.Egeria document, in order. A block ends at the
    next `## ` heading or a `___` rule. Blocks inside HTML comments are ignored."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    out = []
    parts = re.split(r"(?m)^(?=## )", text)
    for part in parts:
        if not part.startswith(LINK_HEADING):
            continue
        body = re.split(r"(?m)^___\s*$", part, maxsplit=1)[0]
        sec = _sections(body)
        gat = sec.get("Governance Action Type", "").strip()
        if not gat:
            continue
        out.append(DeclaredLink(gat, sec.get("Governance Engine", "").strip(), sec.get("Request Type", "").strip(),
                                document))
    return out


def declared_links_of(paths: Iterable[Path], root: Path | None = None) -> list[DeclaredLink]:
    """The declared links of several documents, to RE's engine only, first declaration of a type winning (a type
    declared twice with the same engine is still ONE expected link)."""
    out: list[DeclaredLink] = []
    seen: set[str] = set()
    for p in paths:
        p = Path(p)
        rel = str(p.relative_to(root)) if root and p.is_relative_to(root) else p.name
        for d in declared_links(p.read_text(), rel):
            if d.governance_engine != ENGINE_QUALIFIED_NAME or d.governance_action_type in seen:
                continue
            seen.add(d.governance_action_type)
            out.append(d)
    return out


def batch_documents(batch_ids: Iterable[str] = (), docs_dir: Path | None = None) -> list[Path]:
    """The documents of the named bootstrap batches (default: engine-host and every batch that requires it)."""
    from resource_explorer import bootstrap

    batches = bootstrap.discover_batches(docs_dir or bootstrap.DOCS_DIR)
    wanted = set(batch_ids) or {b.batch_id for b in batches
                                if b.batch_id == bootstrap.ENGINE_HOST_BATCH
                                or bootstrap.ENGINE_HOST_BATCH in b.requires}
    return [b.path / f for b in batches if b.batch_id in wanted for f in b.files]


# ── the live read (pure classification over Egeria's answers) ─────────────────

def _is_absent(answer) -> bool:
    return isinstance(answer, str) and " ".join(answer.split()).lower().rstrip(".") in _ABSENT


def _guid(expert, qualified_name: str) -> str | None:
    """GUID by qualifiedName; None when Egeria says there is none; raises when it cannot answer."""
    from resource_explorer.egeria_lookup import guid_by_qualified_name

    return guid_by_qualified_name(expert, qualified_name)


def _re_executors(answer, engine_guid: str) -> list[dict]:
    """The `GovernanceActionExecutor` entries of a type's relationships that go to RE's engine, oldest first. Raises
    on an answer that is neither a graph nor Egeria's exact "No elements found"."""
    from resource_explorer.surveyors.survey_definition_reconciler import (
        EXECUTOR_RELATIONSHIP,
        _prop,
    )

    if _is_absent(answer):
        return []
    if not isinstance(answer, dict):
        raise ValueError(f"Egeria answered the type's relationships with {str(answer)[:160]!r}")
    out = []
    for entry in answer.get("elementList") or []:
        if ((entry.get("type") or {}).get("typeName")) != EXECUTOR_RELATIONSHIP or entry.get("elementAtEnd1"):
            continue
        engine = entry.get("element") or {}
        eg = str(engine.get("elementGUID") or "")
        if eg != engine_guid and _prop(engine, "qualifiedName") != ENGINE_QUALIFIED_NAME:
            continue
        out.append(entry)
    return sorted(out, key=lambda e: str((e.get("versions") or {}).get("createTime") or ""))


def _request_type(entry: dict) -> str:
    rp = entry.get("relationshipProperties") or {}
    value = (rp.get("propertiesAsStrings") or {}).get("requestType")
    if value is None:
        value = ((rp.get("propertyValueMap") or {}).get("requestType") or {}).get("primitiveValue")
    return str(value or "")


def read_state(expert, declared: DeclaredLink, engine_guid: str) -> LinkState:
    try:
        type_guid = _guid(expert, declared.governance_action_type)
    except Exception as exc:  # noqa: BLE001 - not known, never missing
        return LinkState(declared, UNREADABLE, f"type lookup failed: {exc}")
    if not type_guid:
        return LinkState(declared, TYPE_ABSENT, "the GovernanceActionType is not in Egeria")
    try:
        execs = _re_executors(expert.get_all_related_elements(type_guid, graph_query_depth=1), engine_guid)
    except Exception as exc:  # noqa: BLE001
        return LinkState(declared, UNREADABLE, f"relationships not read: {exc}", type_guid)
    guids = [str(e.get("relationshipGUID") or "") for e in execs]
    if not execs:
        return LinkState(declared, MISSING, f"no executor to {ENGINE_QUALIFIED_NAME}", type_guid)
    if len(execs) > 1:
        return LinkState(declared, SEVERAL, f"{len(execs)} executors to {ENGINE_QUALIFIED_NAME}, expected one: "
                         + ", ".join(_request_type(e) or "(no request type)" for e in execs), type_guid, guids)
    if _request_type(execs[0]) != declared.request_type:
        return LinkState(declared, WRONG_REQUEST_TYPE, f"request type {_request_type(execs[0]) or '(none)'}, "
                         f"declared {declared.request_type}", type_guid, guids)
    return LinkState(declared, OK, "", type_guid, guids)


def reconcile(expert, declared: list[DeclaredLink], *, author=None, apply: bool = False) -> Report:
    """Read every declared link; with `apply` (and an `author`, an ActionAuthor client), add the missing ones and
    remove all but the oldest of several, then re-read each changed type. Without `apply`, nothing is written."""
    report = Report(applied=apply)
    try:
        engine_guid = _guid(expert, ENGINE_QUALIFIED_NAME)
    except Exception as exc:  # noqa: BLE001
        report.refused = f"the engine {ENGINE_QUALIFIED_NAME} could not be looked up ({exc}) · nothing written"
        return report
    if not engine_guid:
        report.refused = (f"the engine {ENGINE_QUALIFIED_NAME} is not in Egeria: every link would fail · heal the "
                          "engine-host batch first · nothing written")
        return report
    report.engine_guid = engine_guid
    report.states = [read_state(expert, d, engine_guid) for d in declared]
    if not apply:
        return report
    if author is None:
        raise ValueError("apply needs an ActionAuthor client")
    for st in report.states:
        if st.problem not in REPAIRABLE:
            continue
        try:
            if st.problem == MISSING:
                author.link_governance_action_executor(st.type_guid, engine_guid, {
                    "class": "NewRelationshipRequestBody",
                    "properties": {"class": "GovernanceActionExecutorProperties",
                                   "requestType": st.declared.request_type}})
            else:
                body = {"class": "DeleteRelationshipRequestBody", "deleteMethod": "SOFT_DELETE"}
                for rel in st.executor_guids[1:]:
                    if rel:
                        expert.delete_related_elements(rel, body=body)
        except Exception as exc:  # noqa: BLE001 - recorded on the item; the rest still run
            st.action = f"failed: {type(exc).__name__}: {exc}"
            continue
        after = read_state(expert, st.declared, engine_guid)
        st.action = (("added" if st.problem == MISSING else f"removed {len(st.executor_guids) - 1}")
                     + f" · now {after.problem}")
    return report


def record(report: Report, registry=None) -> str:
    """One activity row for an applied pass that wrote (rule 16: every operation is logged). Returns its id."""
    from resource_explorer.registry import ActivityEntry, ProjectRegistry

    registry = registry or ProjectRegistry()
    entry_id = str(uuid.uuid4())
    failed = any(s.action.startswith("failed") for s in report.states)
    registry.write_activity(ActivityEntry(
        id=entry_id, ts=datetime.now(UTC).isoformat(), operation=OPERATION, intent="enrichment",
        entity_type="governance_engine", entity_slug=ENGINE_QUALIFIED_NAME, entity_name=ENGINE_QUALIFIED_NAME,
        entity_location="", status="error" if failed or report.refused else "ok", summary=report.words(),
        detail=json.dumps({"engine_guid": report.engine_guid, "applied": report.applied, "counts": report.counts()}),
        items=[s.item() for s in report.states if s.action or s.problem != OK]))
    return entry_id
