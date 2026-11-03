"""Quality slice Q5: accepted checks as Egeria `DataQualityRule`s (DESIGN-QUALITY-Q5-DATA-QUALITY-RULE.md).

One accepted check -> one `DataQualityRule`, written only by Publish (accepting stays local):

    qualifiedName   <databaseAssetQN>::Quality::<check_key>          (Bitol's `<elementQN>::Quality::<id>`)
    anchor          its OWN (`isOwnAnchor`): a reusable rule (owner, 2026-10-28). A rule tied to a data contract
                    is anchored there; that is a later slice, not this one
    GovernedBy      from the RelationalColumn (a column check) or RelationalTable (a table check), AND from the
                    database Asset, each linked once: the relationship, not the anchor, says what the rule checks

Through pyegeria's GENERIC governance-definition calls, wrapped here so a later dedicated API
(`SPEC-PYEGERIA-DATA-QUALITY-RULE.md`) is a change to `PyegeriaRuleGateway` only. The rules the design sets:

- **Never fall back.** The create sends `DataQualityRuleProperties` and nothing else. A refusal ends that check's
  publish with the server's sentence; no second create, never a base `GovernanceDefinition` (Dr.Egeria's processor
  retries with the base class and would silently lose every quality attribute).
- **Name first.** An existing rule of that name is adopted; two, or an element of another type holding the name,
  stop that check: someone else's state, not RE's to resolve.
- **Read back by GUID** after every write; a proof row is the read-back's, never the call's answer.
- **Egeria wins.** When a rule's compared fields (operator, threshold, severity) differ from what RE last sent, it
  was edited in Egeria: RE does not overwrite it, and the run step uses Egeria's values.
- **Reject after publish** -> `contentStatus` DEPRECATED and the GovernedBy link detached; never deleted (the
  annotations that evaluated it keep their links to it).

`plan_rules` and `rule_properties` are pure; `publish_rules` talks only to a `RuleGateway`.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from resource_explorer.surveyors.database.quality.catalogue import (
    MUST_BE,
    SEVERITY,
    ParameterError,
    TestType,
    egeria_dimension,
    original_dimension,
    published_threshold,
)

log = logging.getLogger(__name__)

TYPE_NAME = "DataQualityRule"
PROPERTIES_CLASS = "DataQualityRuleProperties"
QUALITY_SEGMENT = "Quality"
QUALITY_ENGINE = "resource-explorer"
ACTIVE, DEPRECATED = "ACTIVE", "DEPRECATED"
#: The rule's end of GovernedBy (end 1 is the governed element; probe of 2026-10-28, call 10).
RULE_END = 2

#: The fields that decide "edited in Egeria": what the run step takes from the rule.
COMPARED_FIELDS = ("comparisonOperator", "thresholdValues", "severity")
#: What a read-back must show equal to what was sent before a write counts.
VERIFIED_FIELDS = ("method", "comparisonOperator", "thresholdValues", "severity", "contentStatus")

#: TestGen test types an ODCS standard metric means exactly (checkType `library`); the rest are `custom`.
ODCS_METRIC = {"Required": "nullValues", "Unique": "duplicateValues", "Required_No_Placeholder": "missingValues"}

# Proof-row outcomes. PUBLISHED_OUTCOMES are the ones that mean "a rule of RE's is live in Egeria".
CREATED, ADOPTED, UPDATED, UNCHANGED, REACTIVATED = "created", "adopted", "updated", "unchanged", "reactivated"
EDITED_IN_EGERIA = "edited_in_egeria"
RETIRED = "retired"
UNVERIFIED, REFUSED, DUPLICATE, NAME_TAKEN = "unverified", "refused", "duplicate", "name_taken"
MISSING_IN_EGERIA, NOT_IN_EGERIA, UNREADABLE = "missing_in_egeria", "not_in_egeria", "unreadable"
PUBLISHED_OUTCOMES = frozenset({CREATED, ADOPTED, UPDATED, UNCHANGED, REACTIVATED, EDITED_IN_EGERIA})

#: The words a person reads for each outcome (the Curate state line).
OUTCOME_WORDS = {
    CREATED: "in Egeria · DataQualityRule · read back", ADOPTED: "in Egeria · DataQualityRule · read back",
    UPDATED: "in Egeria · updated · read back", UNCHANGED: "in Egeria · DataQualityRule · read back",
    REACTIVATED: "in Egeria · reactivated · read back",
    EDITED_IN_EGERIA: "edited in Egeria · Egeria's threshold is used",
    RETIRED: "retired in Egeria", UNVERIFIED: "unverified", REFUSED: "refused by Egeria",
    DUPLICATE: "not published · two rules hold this name in Egeria",
    NAME_TAKEN: "not published · another element holds this name in Egeria",
    MISSING_IN_EGERIA: "no longer in Egeria · will be re-created on the next publish",
    NOT_IN_EGERIA: "not in Egeria · commit its schema in Contents",
    UNREADABLE: "not published · Egeria could not be read",
}


def outcome_words(outcome: str, detail: dict | None = None) -> str:
    """What one check's press did, in words: created / unchanged / not in Egeria / refused with Egeria's own sentence.
    A press that left a check unverified or unreadable says that, with the reason where there is one."""
    detail = detail or {}
    said = {CREATED: "published · created in Egeria", ADOPTED: "published · adopted the rule already in Egeria",
            UPDATED: "published · updated in Egeria", UNCHANGED: "published · unchanged",
            REACTIVATED: "published · reactivated in Egeria"}.get(outcome)
    if said:
        return said
    why = str(detail.get("error") or detail.get("why") or "")[:200]
    if outcome == REFUSED:
        return f"refused by Egeria · {why}" if why else "refused by Egeria"
    if outcome in (UNVERIFIED, UNREADABLE) and why:
        return f"{OUTCOME_WORDS[outcome]} · {why}"
    return OUTCOME_WORDS.get(outcome, outcome)


# ── names and properties (pure) ──────────────────────────────────────────────

def rule_qualified_name(asset_qualified_name: str, check_key: str) -> str:
    return f"{asset_qualified_name}::{QUALITY_SEGMENT}::{check_key}"


def check_path(check_key: str) -> str:
    """`schema.table[.column]` from `schema.table[.column]::Test_Type`."""
    return check_key.rpartition("::")[0]


def rule_properties(check_key: str, test_type: TestType, params: dict, *, verdict: dict | None = None,
                    proposal: dict | None = None) -> dict:
    """The `DataQualityRuleProperties` for one accepted check (DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md §7's
    table), WITHOUT qualifiedName (the caller roots it at the asset). Raises ParameterError when the accepted
    parameters do not give a threshold or a severity, so a rule is never written with a guessed one."""
    severity = params.get("severity")
    if severity not in SEVERITY.values():
        raise ParameterError(f"severity must be one of {sorted(SEVERITY.values())}, got {severity!r}")
    values = published_threshold(test_type, params)
    path = check_path(check_key)
    metric = ODCS_METRIC.get(test_type.test_type)
    extra = {
        "checkKey": check_key,
        "catalogueRef": (proposal or {}).get("catalogue_ref") or "",
        "origin": (proposal or {}).get("origin") or "",
        "profileSurveyedAt": str(params.get("profile_surveyed_at") or (proposal or {}).get("profile_surveyed_at")
                                 or ""),
        "acceptedBy": (verdict or {}).get("decided_by") or "",
        "acceptedAt": (verdict or {}).get("created_at") or "",
        "originalDimension": original_dimension(test_type.dimension),
    }
    for key, name in (("baseline_ct", "baselineCt"), ("baseline_value_ct", "baselineValueCt"),
                      ("baseline_value", "baselineValue"), ("baseline_unique_ct", "baselineUniqueCt"),
                      ("baseline_sum", "baselineSum"), ("baseline_sd", "baselineSd"),
                      ("upper_tolerance", "upperTolerance"), ("baseline_avg", "baselineAvg")):
        if params.get(key) not in (None, ""):
            extra[name] = str(params[key])
    props = {
        "class": PROPERTIES_CLASS,
        "typeName": TYPE_NAME,
        "displayName": f"{test_type.name_short}: {path}",
        "summary": test_type.description or "",
        "description": _described(test_type, proposal),
        "scope": path,
        "qualityDimension": egeria_dimension(test_type.dimension),
        "checkType": "library" if metric else "custom",
        "metric": metric,
        "method": test_type.test_type,
        "qualityEngine": QUALITY_ENGINE,
        "comparisonOperator": MUST_BE.get(test_type.test_operator, test_type.test_operator),
        "thresholdValues": values,
        "severity": severity,
        "units": test_type.measure_uom or "",
        "contentStatus": ACTIVE,
        "additionalProperties": {k: v for k, v in extra.items() if v not in (None, "")},
    }
    return {k: v for k, v in props.items() if v not in (None, "")}


def _described(test_type: TestType, proposal: dict | None) -> str:
    """The rule's `description`: TestGen's usage notes (or description), then, as its own paragraph, where the check
    comes from. The name, summary and every headline stay as they are (owner, 2026-10-30)."""
    from resource_explorer.quality_words import attribution_of

    base = test_type.usage_notes or test_type.description or ""
    said = attribution_of(test_type, (proposal or {}).get("origin") or "", (proposal or {}).get("catalogue_ref") or "")
    return f"{base}\n\n{said}" if base and said else (said or base)


def compared(props: dict) -> dict:
    return {k: props.get(k) for k in COMPARED_FIELDS}


def proof_hash(props: dict) -> str:
    """sha256 of the canonical properties RE sends, less who accepted and when (a re-accept of the same parameters
    is not a change). Decides "unchanged" from RE's own proof row, not from a re-read."""
    canon = {k: v for k, v in props.items() if k != "additionalProperties"}
    canon["additionalProperties"] = {k: v for k, v in (props.get("additionalProperties") or {}).items()
                                     if k not in ("acceptedBy", "acceptedAt")}
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


# ── the plan (pure) ──────────────────────────────────────────────────────────

@dataclass
class RuleAction:
    """What Publish will do for one check_key. `kind`: publish (create, adopt, update or confirm), retire, or
    not_in_egeria (no element to govern: nothing is written), skip (cannot be published this time)."""
    check_key: str
    kind: str
    properties: dict = field(default_factory=dict)
    element_guid: str = ""
    element_kind: str = ""          # column | table
    proof: dict | None = None       # RE's latest proof row for this key
    reason: str = ""
    outcome: str = ""               # for a skip: the outcome its row records
    asset_guid: str = ""            # the database Asset, also linked by GovernedBy (set by publish_rules)


def plan_rules(verdicts: dict[str, dict], proofs: dict[str, dict], properties: dict[str, dict],
               elements: dict[str, str], skipped: dict[str, tuple[str, str]] | None = None) -> list[RuleAction]:
    """One action per check that has something to do, in check-key order.

    `verdicts`: latest verdict per key. `proofs`: RE's latest proof row per key. `properties`: the rule properties
    of each accepted check that has them (`rule_properties`). `elements`: path -> element GUID for the paths
    Egeria holds an element for. `skipped`: accepted keys that cannot be published this time, as (outcome, why):
    parameters that do not build a rule (UNVERIFIED), an element look-up that could not be made (UNREADABLE)."""
    actions: list[RuleAction] = []
    skipped = skipped or {}
    for key in sorted(verdicts):
        verdict = verdicts[key].get("verdict")
        proof = proofs.get(key)
        published = bool(proof and proof.get("outcome") in PUBLISHED_OUTCOMES and proof.get("rule_guid"))
        path = check_path(key)
        kind = "column" if path.count(".") == 2 else "table"
        if verdict == "accepted":
            if key in skipped:
                outcome, why = skipped[key]
                actions.append(RuleAction(key, "skip", proof=proof, reason=why, outcome=outcome))
            elif key not in properties:
                continue
            elif not elements.get(path):
                actions.append(RuleAction(key, NOT_IN_EGERIA, properties=properties[key], element_kind=kind,
                                          proof=proof))
            else:
                actions.append(RuleAction(key, "publish", properties=properties[key], element_guid=elements[path],
                                          element_kind=kind, proof=proof))
        elif verdict in ("rejected", "retired") and published:
            actions.append(RuleAction(key, "retire", element_guid=elements.get(path, "") or
                                      (proof or {}).get("governed_guid", ""), element_kind=kind, proof=proof))
    return actions


# ── Egeria, behind one seam ──────────────────────────────────────────────────

class RuleGatewayError(Exception):
    """A call to Egeria failed; the message is the server's sentence (or the client's)."""


@dataclass
class RuleRead:
    guid: str
    type_name: str
    properties: dict
    updated_by: str = ""
    update_time: str = ""


class RuleGateway(Protocol):
    def find_by_name(self, qualified_name: str) -> list[RuleRead]: ...
    def element_guid(self, qualified_name: str, type_name: str) -> str: ...
    # optional (D1-a): def endpoint_resolution(self, db_entity) -> dict -- other families at the database's endpoint
    def qualified_name_of(self, guid: str) -> str: ...
    def create(self, properties: dict) -> str: ...
    def read(self, guid: str) -> RuleRead | None: ...
    def update(self, guid: str, properties: dict) -> None: ...
    def link(self, element_guid: str, rule_guid: str) -> tuple[bool, str]: ...
    def unlink(self, element_guid: str, rule_guid: str) -> None: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _differs(read: RuleRead, props: dict, fields: Iterable[str]) -> list[str]:
    out = []
    for f in fields:
        want, got = props.get(f), read.properties.get(f)
        if f == "thresholdValues":
            want, got = [str(v) for v in (want or [])], [str(v) for v in (got or [])]
        if want != got:
            out.append(f)
    return out


def publish_rules(actions: list[RuleAction], gateway: RuleGateway, *, slug: str, asset_guid: str,
                  asset_qualified_name: str, recorded_by: str) -> list[dict]:
    """Carry out the plan; return one proof row per action (the caller stores them). Never raises for one check's
    failure: it becomes that check's row. A row's `outcome` is decided by a read-back by GUID wherever a rule was
    written."""
    rows: list[dict] = []
    for action in actions:
        action.asset_guid = asset_guid
        qn = rule_qualified_name(asset_qualified_name, action.check_key)
        row = {"database_slug": slug, "check_key": action.check_key, "rule_guid": "", "qualified_name": qn,
               "governed_guid": action.element_guid, "governed_kind": action.element_kind, "outcome": "",
               "re_proof_hash": proof_hash(action.properties) if action.properties else "",
               "detail_json": {}, "recorded_by": recorded_by, "read_at": ""}
        try:
            if action.kind == "skip":
                row.update(outcome=action.outcome, detail_json={"error": action.reason})
            elif action.kind == NOT_IN_EGERIA:
                row.update(outcome=NOT_IN_EGERIA,
                           detail_json={"why": f"no {action.element_kind} element in Egeria for "
                                               f"{check_path(action.check_key)}: commit its schema in Contents"})
            elif action.kind == "retire":
                _retire(action, gateway, row)
            else:
                _publish_one(action, gateway, row, qn)
        except RuleGatewayError as exc:
            row.update(outcome=UNREADABLE if not row["outcome"] else row["outcome"],
                       detail_json={**row["detail_json"], "error": str(exc)[:400]})
        rows.append(row)
    return rows


def _publish_one(action: RuleAction, gateway: RuleGateway, row: dict, qn: str) -> None:
    props = {**action.properties, "qualifiedName": qn}
    proof = action.proof or {}
    sent_before = (proof.get("detail_json") or {}).get("sent") if isinstance(proof.get("detail_json"), dict) else None
    guid = proof.get("rule_guid") if proof.get("outcome") in PUBLISHED_OUTCOMES else ""
    current: RuleRead | None = None
    if guid:
        current = gateway.read(guid)
        if current is None:
            row.update(rule_guid=guid, outcome=MISSING_IN_EGERIA, read_at=_now(),
                       detail_json={"why": "the rule's GUID no longer resolves in Egeria"})
            return
    else:
        hits = gateway.find_by_name(qn)
        rules = [h for h in hits if h.type_name == TYPE_NAME]
        others = [h for h in hits if h.type_name != TYPE_NAME]
        if others:
            row.update(outcome=NAME_TAKEN, read_at=_now(),
                       detail_json={"held_by": [{"guid": h.guid, "type": h.type_name} for h in others]})
            return
        if len(rules) > 1:
            row.update(outcome=DUPLICATE, read_at=_now(), detail_json={"guids": [h.guid for h in rules]})
            return
        current = rules[0] if rules else None

    if current is None:
        try:
            guid = gateway.create(props)
        except RuleGatewayError as exc:
            # No fallback: the subtype refused is the end of this check's publish.
            row.update(outcome=REFUSED, detail_json={"error": str(exc)[:400]})
            return
        outcome = CREATED
    else:
        guid = current.guid
        edited = _differs(current, sent_before, COMPARED_FIELDS) if sent_before else []
        if edited and current.properties.get("contentStatus") != DEPRECATED:
            row.update(rule_guid=guid, outcome=EDITED_IN_EGERIA, read_at=_now(),
                       detail_json={"sent": sent_before, "fields": edited, "egeria": compared(current.properties),
                                    "updated_by": current.updated_by, "update_time": current.update_time})
            _link(gateway, action, guid, row)
            return
        reactivate = current.properties.get("contentStatus") == DEPRECATED
        if reactivate or _differs(current, props, VERIFIED_FIELDS) or \
                (proof.get("re_proof_hash") and proof.get("re_proof_hash") != row["re_proof_hash"]):
            gateway.update(guid, props)
            outcome = REACTIVATED if reactivate else (UPDATED if proof.get("rule_guid") else ADOPTED)
        else:
            outcome = UNCHANGED if proof.get("rule_guid") == guid else ADOPTED

    read = gateway.read(guid)
    row.update(rule_guid=guid, read_at=_now())
    if read is None or read.type_name != TYPE_NAME or _differs(read, props, VERIFIED_FIELDS):
        row.update(outcome=UNVERIFIED, detail_json={
            "why": "the rule did not read back as sent",
            "type": read.type_name if read else "", "fields": _differs(read, props, VERIFIED_FIELDS) if read else []})
        return
    row.update(outcome=outcome, detail_json={"sent": compared(props), "updated_by": read.updated_by,
                                             "update_time": read.update_time})
    _link(gateway, action, guid, row)


def _link(gateway: RuleGateway, action: RuleAction, guid: str, row: dict) -> None:
    """GovernedBy from the column or table, then from the database Asset, each once."""
    for key, element in (("link", action.element_guid), ("asset_link", action.asset_guid)):
        if not element:
            continue
        linked, words = gateway.link(element, guid)
        row["detail_json"][key] = words
        if not linked:
            row["detail_json"][f"{key}_error"] = words


def _retire(action: RuleAction, gateway: RuleGateway, row: dict) -> None:
    proof = action.proof or {}
    guid = proof.get("rule_guid", "")
    row["rule_guid"] = guid
    current = gateway.read(guid)
    if current is None:
        row.update(outcome=MISSING_IN_EGERIA, read_at=_now(), detail_json={"why": "already gone from Egeria"})
        return
    if current.properties.get("contentStatus") != DEPRECATED:
        gateway.update(guid, {"class": PROPERTIES_CLASS, "typeName": TYPE_NAME, "contentStatus": DEPRECATED})
    unlinked = []
    for element in (action.element_guid, action.asset_guid):
        if not element:
            continue
        try:
            gateway.unlink(element, guid)
            unlinked.append(element)
        except RuleGatewayError as exc:
            row["detail_json"]["unlink_error"] = str(exc)[:400]
    read = gateway.read(guid)
    row["read_at"] = _now()
    if read is None or read.properties.get("contentStatus") != DEPRECATED:
        row.update(outcome=UNVERIFIED, detail_json={**row["detail_json"], "why": "the retire did not read back"})
        return
    row.update(outcome=RETIRED, detail_json={**row["detail_json"], "unlinked_from": unlinked})


# ── the run step's view of published rules ───────────────────────────────────

def rules_for_run(proofs: dict[str, dict], read) -> dict[str, dict]:
    """{check_key: {"rule_guid", "source", "properties"|None, "note"}} for every check whose latest proof row says
    a rule of RE's is live. `read(guid)` returns a RuleRead, None (gone) or raises RuleGatewayError (unreadable).
    `source`: `egeria` (read now), `local_cache` (Egeria could not be read: the run uses the verdict and says so),
    or `missing` (the rule is gone: the run does not run that check)."""
    out: dict[str, dict] = {}
    for key, proof in proofs.items():
        if proof.get("outcome") not in PUBLISHED_OUTCOMES or not proof.get("rule_guid"):
            continue
        guid = proof["rule_guid"]
        try:
            got = read(guid)
        except RuleGatewayError as exc:
            out[key] = {"rule_guid": guid, "source": "local_cache", "properties": None,
                        "note": f"rules read from local cache: {exc}"[:300]}
            continue
        if got is None or got.type_name != TYPE_NAME:
            out[key] = {"rule_guid": guid, "source": "missing", "properties": None,
                        "note": "rule not found in Egeria"}
            continue
        sent = (proof.get("detail_json") or {}).get("sent") if isinstance(proof.get("detail_json"), dict) else None
        edited = _differs(got, sent, COMPARED_FIELDS) if sent else []
        out[key] = {"rule_guid": guid, "source": "egeria", "properties": got.properties,
                    "note": "rule edited in Egeria since acceptance" if edited else ""}
    return out


# ── pyegeria ─────────────────────────────────────────────────────────────────

def _rule_read(element) -> RuleRead | None:
    if not isinstance(element, dict):
        return None
    header = element.get("elementHeader") or {}
    versions = header.get("versions") or {}
    return RuleRead(guid=str(header.get("guid") or ""),
                    type_name=str((header.get("type") or {}).get("typeName") or ""),
                    properties=dict(element.get("properties") or {}),
                    updated_by=str(versions.get("updatedBy") or versions.get("createdBy") or ""),
                    update_time=str(versions.get("updateTime") or versions.get("createTime") or ""))


def _elements(answer) -> list:
    if isinstance(answer, dict):
        answer = answer.get("elementList") or answer.get("elements") or [answer]
    return answer if isinstance(answer, list) else []


class PyegeriaRuleGateway:
    """The generic Governance Officer calls (pyegeria 6.2.13), and `egeria_link_once` for GovernedBy.
    `governance_officer` is a `GovernanceOfficer`, `metadata_expert` a `MetadataExpert`, both built for the person
    publishing."""

    def __init__(self, governance_officer, metadata_expert):
        self._go = governance_officer
        self._expert = metadata_expert

    @staticmethod
    def _guard(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except Exception as exc:
            raise RuleGatewayError(str(exc) or type(exc).__name__) from exc

    def find_by_name(self, qualified_name: str) -> list[RuleRead]:
        """The element whose qualifiedName is EXACTLY this one (`egeria_lookup.guid_by_qualified_name`, the
        unique-name read). Two holders come back as two reads (DUPLICATE); a holder of another type as one read
        of that type (NAME_TAKEN)."""
        from resource_explorer.egeria_lookup import (
            QualifiedNameAmbiguous,
            QualifiedNameTypeMismatch,
            guid_by_qualified_name,
        )

        try:
            guid = guid_by_qualified_name(self._expert, qualified_name, type_name=TYPE_NAME)
        except QualifiedNameAmbiguous:
            return [RuleRead("", TYPE_NAME, {}), RuleRead("", TYPE_NAME, {})]
        except QualifiedNameTypeMismatch as exc:
            return [RuleRead("", "another type", {"why": str(exc)[:200]})]
        except Exception as exc:
            raise RuleGatewayError(f"could not look up {qualified_name!r}: {exc}"[:400]) from exc
        if not guid:
            return []
        read = self.read(guid)
        if read is None:
            raise RuleGatewayError(f"{qualified_name!r} resolves to {guid} but it does not read back")
        return [read]

    def element_guid(self, qualified_name: str, type_name: str) -> str:
        """The GUID of the column or table element with this qualifiedName; "" when Egeria holds none."""
        from resource_explorer.egeria_lookup import guid_by_qualified_name

        try:
            return guid_by_qualified_name(self._expert, qualified_name, type_name=type_name) or ""
        except Exception as exc:
            raise RuleGatewayError(f"could not look up {qualified_name!r}: {exc}"[:400]) from exc

    def endpoint_resolution(self, db_entity) -> dict:
        """D1-a: every family Egeria holds at `db_entity`'s endpoint (`endpoint_identity.resolve`, cached a minute),
        read as the person pressing (`current_principal`)."""
        from resource_explorer import endpoint_identity
        from resource_explorer.catalogue_commit import make_gateway

        return endpoint_identity.cached_resolve(make_gateway(db_entity), db_entity)

    def qualified_name_of(self, guid: str) -> str:
        from resource_explorer.catalogue_gateway import _qn_of

        answer = self._guard(self._expert.get_metadata_element_by_guid, guid)
        qn = _qn_of(answer)
        if not qn:
            raise RuleGatewayError(f"no qualifiedName on the answer for {guid}")
        return str(qn)

    def create(self, properties: dict) -> str:
        """A reusable rule: its own anchor (owner, 2026-10-28)."""
        body = {"class": "NewElementRequestBody", "isOwnAnchor": True, "properties": properties}
        guid = self._guard(self._go.create_governance_definition, body)
        if not guid or not isinstance(guid, str):
            raise RuleGatewayError(f"the create returned no GUID ({guid!r})")
        return guid

    def read(self, guid: str) -> RuleRead | None:
        """The rule by GUID; None ONLY when Egeria says there is no such element (`egeria_absence.is_absent`); any
        other failure is RuleGatewayError, never "gone"."""
        from resource_explorer.egeria_absence import ABSENT, is_absent

        try:
            answer = self._go.get_governance_definition_by_guid(guid, output_format="JSON")
        except Exception as exc:
            if is_absent(exc) == ABSENT:
                return None
            raise RuleGatewayError(str(exc) or type(exc).__name__) from exc
        if answer is None:
            raise RuleGatewayError("no answer to a read by GUID")
        if isinstance(answer, str):
            if is_absent(answer) == ABSENT:
                return None
            raise RuleGatewayError(f"unreadable answer to a read by GUID: {answer[:200]}")
        elements = _elements(answer)
        return _rule_read(elements[0]) if elements else None

    def update(self, guid: str, properties: dict) -> None:
        body = {"class": "UpdateElementRequestBody", "mergeUpdate": True, "properties": properties}
        self._guard(self._go.update_governance_definition, guid, body)

    def link(self, element_guid: str, rule_guid: str) -> tuple[bool, str]:
        from resource_explorer.egeria_link_once import link_once

        body = {"class": "NewRelationshipRequestBody",
                "properties": {"class": "GovernedByProperties", "label": "quality check"}}
        outcome = link_once(
            self._expert, relationship_type="GovernedBy", read_guid=rule_guid, read_end=RULE_END,
            far_guid=element_guid, what=f"from {element_guid} to rule {rule_guid}",
            create=lambda: self._go.attach_governed_by_definition(element_guid, rule_guid, body))
        return outcome.linked, outcome.error or outcome.outcome

    def unlink(self, element_guid: str, rule_guid: str) -> None:
        self._guard(self._go.detach_governed_by_definition, element_guid, rule_guid)


def egeria_gateway(identity=None) -> PyegeriaRuleGateway:
    """The gateway as `identity` (default: the current principal: the signed-in person on a route, the declared
    daemon in a job)."""
    from pyegeria import MetadataExpert
    from pyegeria.omvs.governance_officer import GovernanceOfficer

    from resource_explorer.egeria_clients import current_principal, egeria_client

    clients = egeria_client(identity or current_principal(), purpose="quality rules")
    return PyegeriaRuleGateway(clients.of(GovernanceOfficer), clients.of(MetadataExpert))


# ── Publish, end to end ──────────────────────────────────────────────────────

def element_qualified_name(db_entity, path: str, schema_qn: str = "") -> str:
    """The JDBC cataloguer's name for the table or column `path` names: `<schema QN>::<table>[::<column>]` (the
    column form confirmed live 2026-10-20; `_link_annotations_to_elements` uses the same). `schema_qn` is the schema
    element's own name when another family holds it (D1-a; the content pack's tables follow the same form, read live
    2026-10-30); RE's own template name otherwise."""
    from resource_explorer.catalogue_gateway import schema_qualified_name, server_name_for

    schema, table, column = (path.split(".", 2) + ["", "", ""])[:3]
    name = f"{schema_qn or schema_qualified_name(server_name_for(db_entity), db_entity.database_name, schema)}::{table}"
    return f"{name}::{column}" if column else name


def _other_schema_names(db_entity, schema: str, resolution) -> list[str]:
    """The schema qualifiedNames OTHER than RE's own that Egeria holds for `schema` at this database's endpoint."""
    from resource_explorer import endpoint_identity
    from resource_explorer.catalogue_gateway import schema_qualified_name, server_name_for

    own = schema_qualified_name(server_name_for(db_entity), db_entity.database_name, schema)
    return [q for q in endpoint_identity.element_qualified_name_prefixes(resolution, schema) if q != own]


def summarize(rows: list[dict]) -> dict:
    """Counts by outcome and the band's one line ("quality rules: 2 new, 1 changed, 1 retired; ...")."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    parts = [f"{counts[k]} {w}" for k, w in (
        (CREATED, "new"), (ADOPTED, "adopted"), (UPDATED, "changed"), (REACTIVATED, "reactivated"),
        (UNCHANGED, "unchanged"), (RETIRED, "retired"), (EDITED_IN_EGERIA, "edited in Egeria, left as Egeria has it"),
    ) if counts.get(k)]
    trouble = [f"{counts[k]} {OUTCOME_WORDS[k]}" for k in (
        NOT_IN_EGERIA, MISSING_IN_EGERIA, UNVERIFIED, REFUSED, DUPLICATE, NAME_TAKEN, UNREADABLE) if counts.get(k)]
    line = "quality rules: " + (", ".join(parts) or "nothing to write")
    return {"counts": counts, "line": line + ("; " + "; ".join(trouble) if trouble else "")}


#: The outcomes that are neither a rule live in Egeria nor waiting for its schema: a publish that went wrong.
_TROUBLE = (MISSING_IN_EGERIA, UNVERIFIED, REFUSED, DUPLICATE, NAME_TAKEN, UNREADABLE)


def publish_state(counts: dict[str, int]) -> dict:
    """The state of a publish, from the counts and nothing else (the Architect's rule, 2026-10-30): a press that wrote
    nothing must not read "published". `waiting` is the checks whose schema is not in Egeria yet (Contents), `live` the
    rules of RE's that are in Egeria (created, adopted, updated, reactivated, unchanged, or edited there).

    - state `published`: some are live and nothing waits ("published");
    - `nothing_published`: nothing is live and some wait ("nothing published · N waiting for Contents");
    - `partly_published`: some are live and some wait ("N published · M waiting");
    - `nothing_to_publish`: no check had anything to do;
    - `retired`: only retirements happened.
    Checks that went wrong (refused, unverified, unreadable, ...) are added to the words, never hidden."""
    live = sum(counts.get(k, 0) for k in PUBLISHED_OUTCOMES)
    waiting = counts.get(NOT_IN_EGERIA, 0)
    trouble = sum(counts.get(k, 0) for k in _TROUBLE)
    retired = counts.get(RETIRED, 0)
    if live and not waiting:
        state, words = "published", "published"
    elif live:
        state, words = "partly_published", f"{live} published · {waiting} waiting"
    elif waiting:
        state, words = "nothing_published", f"nothing published · {waiting} waiting for Contents"
    elif retired:
        state, words = "retired", f"{retired} retired"
    elif trouble:
        state, words = "nothing_published", "nothing published"
    else:
        state, words = "nothing_to_publish", "nothing to publish"
    if trouble:
        words += f" · {trouble} could not be published"
    return {"state": state, "state_words": words}


def publish_database_rules(registry, db_entity, gateway: RuleGateway, *, recorded_by: str) -> dict:
    """Publish, for one database: every accepted check to a DataQualityRule, every rejected or retired one that was
    published to DEPRECATED. Writes one proof row per check acted on (`database_quality_rule_proofs`) and returns
    {state, line, counts, rows}. A database not in Egeria, or whose asset cannot be read, writes nothing.

    D1-a: a table or column not found under RE's own schema name is looked up under every other family's schema
    element for the same endpoint (the gateway's `endpoint_resolution`, read once, only on a miss), so a check on a
    content pack's schema finds the pack's element. A resolution that cannot be read makes those checks UNREADABLE,
    never "not in Egeria". A gateway without the method looks under RE's own name only."""
    from resource_explorer.surveyors.database.quality.catalogue import load_catalogue
    from resource_explorer.surveyors.database.quality.run_checks import _resolve

    slug = db_entity.slug
    asset_guid = getattr(db_entity, "egeria_asset_guid", "") or ""
    if not asset_guid:
        return {"state": "not_published", "line": "quality rules: the database is not in Egeria; commit it first",
                "counts": {}, "rows": []}
    try:
        asset_qn = gateway.qualified_name_of(asset_guid)
    except RuleGatewayError as exc:
        return {"state": "unreadable", "line": f"quality rules: the database asset could not be read ({exc})",
                "counts": {}, "rows": []}

    verdicts = registry.get_quality_verdicts(slug)
    proofs = registry.latest_quality_rule_proofs(slug)
    catalogue = load_catalogue()
    properties: dict[str, dict] = {}
    skipped: dict[str, tuple[str, str]] = {}
    for key, verdict in verdicts.items():
        if verdict.get("verdict") != "accepted":
            continue
        resolved = _resolve(registry, key, verdict)
        test_type = catalogue.get(resolved[3]) if resolved else None
        params = verdict.get("params_json")
        if resolved is None or test_type is None or not isinstance(params, dict):
            skipped[key] = (UNVERIFIED, f"cannot build a rule for {key!r}: unknown check or parameters")
            continue
        try:
            properties[key] = rule_properties(key, test_type, params, verdict=verdict,
                                              proposal=registry.get_quality_proposal(verdict.get("proposal_id")))
        except ParameterError as exc:
            skipped[key] = (UNVERIFIED, str(exc))

    elements: dict[str, str] = {}
    wanted = {check_path(k) for k in properties} | {
        check_path(k) for k, v in verdicts.items()
        if v.get("verdict") in ("rejected", "retired") and (proofs.get(k) or {}).get("outcome") in PUBLISHED_OUTCOMES}
    resolution: dict | None = None
    for path in sorted(wanted):
        type_name = "RelationalColumn" if path.count(".") == 2 else "RelationalTable"
        try:
            elements[path] = gateway.element_guid(element_qualified_name(db_entity, path), type_name)
            resolve = getattr(gateway, "endpoint_resolution", None)
            if not elements[path] and resolve is not None:
                if resolution is None:
                    try:
                        resolution = resolve(db_entity)
                    except Exception as exc:  # noqa: BLE001 - no client or no read: said as unreadable, never absent
                        resolution = {"error": str(exc)[:300]}
                if resolution.get("error"):
                    raise RuleGatewayError("could not read which Egeria elements describe this database's endpoint: "
                                           f"{resolution['error']}"[:400])
                for schema_qn in _other_schema_names(db_entity, path.split(".")[0], resolution):
                    elements[path] = gateway.element_guid(element_qualified_name(db_entity, path, schema_qn),
                                                          type_name)
                    if elements[path]:
                        break
        except RuleGatewayError as exc:
            for key in [k for k in properties if check_path(k) == path]:
                skipped[key] = (UNREADABLE, str(exc))

    actions = plan_rules(verdicts, proofs, properties, elements, skipped)
    rows = publish_rules(actions, gateway, slug=slug, asset_guid=asset_guid, asset_qualified_name=asset_qn,
                         recorded_by=recorded_by)
    registry.record_quality_rule_proofs(rows)
    summary = summarize(rows)
    outcomes = {r["check_key"]: {"state": r["outcome"], "words": outcome_words(r["outcome"], r.get("detail_json"))}
                for r in rows}
    return {**publish_state(summary["counts"]), **summary, "outcomes": outcomes, "rows": rows}


def read_rules_for_run(registry, slug: str, gateway_factory=egeria_gateway) -> dict | None:
    """The run step's rules: None when no check has a published rule (every check runs `unpublished`). When the
    gateway cannot even be built, every published check runs from its verdict as `local_cache`, labelled."""
    proofs = registry.latest_quality_rule_proofs(slug)
    if not any(p.get("outcome") in PUBLISHED_OUTCOMES and p.get("rule_guid") for p in proofs.values()):
        return None
    try:
        gateway = gateway_factory()
        read = gateway.read
    except Exception as exc:  # noqa: BLE001 - no client is "Egeria could not be read", never "no rules"
        reason = f"no Egeria client ({exc})"[:200]

        def read(_guid, _why=reason):
            raise RuleGatewayError(_why)
    return rules_for_run(proofs, read)


def rule_states(registry, slug: str) -> dict:
    """The Curate state line per check with a verdict: {check_key: {state, words, rule_guid, read_at}}, from the
    verdict and RE's latest proof row. An accepted check with no row is "local · not published"; one whose
    parameters changed since the row is "in Egeria · changed here · publish to update".

    Also `preview` (what a press would do, from RE's own rows only: whether a check's schema is in Egeria is known
    only from the last press, never guessed) and `in_egeria` (the database has an asset to publish under)."""
    verdicts = registry.get_quality_verdicts(slug)
    proofs = registry.latest_quality_rule_proofs(slug)
    out: dict[str, dict] = {}
    tally = {"accepted": 0, "to_publish": 0, "to_retire": 0, "waiting": 0, "unchanged": 0, "edited": 0, "retry": 0}
    for key, verdict in sorted(verdicts.items()):
        proof = proofs.get(key)
        state = (proof or {}).get("outcome") or ""
        accepted = verdict.get("verdict") == "accepted"
        if not accepted and state not in PUBLISHED_OUTCOMES | {RETIRED}:
            continue
        tally["accepted"] += accepted
        if not proof:
            words = "local · not published"
            tally["to_publish"] += 1
        elif accepted and state in PUBLISHED_OUTCOMES and state != EDITED_IN_EGERIA and _accepted_after(verdict, proof):
            words = "in Egeria · changed here · publish to update"
            tally["to_publish"] += 1
        elif not accepted and state in PUBLISHED_OUTCOMES:
            words = "in Egeria · rejected here · publish to retire"
            tally["to_retire"] += 1
        else:
            words = OUTCOME_WORDS.get(state, state)
            detail = proof.get("detail_json") or {}
            if state == EDITED_IN_EGERIA and detail.get("updated_by"):
                words = f"edited in Egeria · {detail['updated_by']} · {detail.get('update_time', '')[:16]}" \
                        " · Egeria's threshold is used"
            if accepted:
                if state == NOT_IN_EGERIA:
                    tally["waiting"] += 1
                elif state == EDITED_IN_EGERIA:
                    tally["edited"] += 1
                elif state in PUBLISHED_OUTCOMES:
                    tally["unchanged"] += 1
                elif state == MISSING_IN_EGERIA:
                    tally["to_publish"] += 1
                elif state != RETIRED:
                    tally["retry"] += 1
        out[key] = {"state": state or "unpublished", "words": words,
                    "rule_guid": (proof or {}).get("rule_guid", ""), "read_at": (proof or {}).get("read_at", "")}
    entity = registry.get_database(slug)
    pressed = [p for p in proofs.values() if p.get("outcome") in PUBLISHED_OUTCOMES and p.get("read_at")]
    newest = max(pressed, key=lambda p: p["read_at"], default=None)
    last = {"at": newest["read_at"], "by": newest.get("recorded_by") or ""} if newest else None
    return {"rules": out, "preview": {**tally, "words": preview_words(tally)}, "last_published": last,
            "in_egeria": bool(getattr(entity, "egeria_asset_guid", "") or "")}


def preview_words(tally: dict) -> str:
    """What a press would do, before it: "2 to publish · 3 waiting for Contents (schema not committed) · 1 unchanged".
    Nothing accepted says so. A check whose schema Egeria might not hold is counted as waiting only once a press found
    that out."""
    parts = [(tally["to_publish"], "to publish"), (tally["to_retire"], "to retire"),
             (tally["waiting"], "waiting for Contents (schema not committed)"), (tally["unchanged"], "unchanged"),
             (tally["edited"], "edited in Egeria, left as Egeria has it"),
             (tally["retry"], "not published last time, will be tried again")]
    said = " · ".join(f"{n} {w}" for n, w in parts if n)
    if not tally["accepted"] and not said:
        return "nothing is accepted yet"
    return said or "nothing to publish"


def _accepted_after(verdict: dict, proof: dict) -> bool:
    from resource_explorer.catalogue_commit import _ts

    return bool(_ts(verdict.get("created_at")) and _ts(proof.get("created_at"))
                and _ts(verdict.get("created_at")) > _ts(proof.get("created_at")))
