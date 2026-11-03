"""Quality slice Q5 (DESIGN-QUALITY-Q5-DATA-QUALITY-RULE.md): accepted checks as Egeria DataQualityRules.

- the property mapping, the qualified name, Validity -> accuracy with originalDimension (owner, 2026-10-20);
- Publish against an in-memory gateway: create (its own anchor), read back, link column and asset once; unchanged on a re-publish; update in place on a
  parameter change; Egeria wins over a rule edited there; reject -> DEPRECATED and unlinked, never deleted;
- the NO-FALLBACK rule: a refused subtype create ends that check, one create call, never a base definition;
- proof rows in the registry, and the reset cutoff;
- the run step: a published rule's severity and threshold override the verdict's; unreadable, missing, deprecated;
- the pyegeria gateway, faked only with `create_autospec(<real class>, spec_set=True)`.

Nothing here reaches Egeria or a database.
"""
from __future__ import annotations

import dataclasses
from unittest.mock import create_autospec

import pytest

from resource_explorer.surveyors.database.quality import egeria_rules as er
from resource_explorer.surveyors.database.quality.catalogue import (
    egeria_dimension,
    load_catalogue,
    original_dimension,
)
from resource_explorer.surveyors.database.quality.run_checks import (
    apply_rule,
    build_quality_annotations,
    run_quality_checks,
)
from tests.test_value_profile_quality_checks import (  # noqa: F401 - fixtures and helpers reused
    BOUNDS,
    SLUG,
    FakeConn,
    _accept,
    _by_key,
    _connect,
    _orders_measure,
    _profile,
    _propose,
    entity,
    registry,
)

ASSET_GUID = "aaaaaaaa-0000-0000-0000-000000000001"
ASSET_QN = "PostgreSQLDatabase::localhost::coco_ods"
COLUMN_GUID = "cccccccc-0000-0000-0000-000000000001"
TABLE_GUID = "tttttttt-0000-0000-0000-000000000001"
UNIQUE = "sales.orders.order_id::Unique"
ROWS = "sales.orders::Row_Ct_Pct"


@pytest.fixture(autouse=True)
def _no_live_database(monkeypatch):
    import psycopg2

    def refuse(*a, **k):
        raise AssertionError("a test opened a real PostgreSQL connection")

    monkeypatch.setattr(psycopg2, "connect", refuse)


class FakeGateway:
    """Egeria in memory: rules by GUID, links as (element, rule) pairs, every call recorded."""

    def __init__(self, elements=None):
        self.rules: dict[str, er.RuleRead] = {}
        self.links: set[tuple[str, str]] = set()
        self.calls: list[tuple] = []
        self.elements = elements if elements is not None else {}
        self.refuse_create: str = ""
        self.read_as_type: str = ""
        self.unreadable = False
        self.name_holders: dict[str, list[er.RuleRead]] = {}

    def qualified_name_of(self, guid):
        self.calls.append(("qualified_name_of", guid))
        return ASSET_QN

    def element_guid(self, qualified_name, type_name):
        self.calls.append(("element_guid", qualified_name, type_name))
        return self.elements.get(qualified_name, "")

    def find_by_name(self, qualified_name):
        self.calls.append(("find_by_name", qualified_name))
        if qualified_name in self.name_holders:
            return self.name_holders[qualified_name]
        return [r for r in self.rules.values() if r.properties.get("qualifiedName") == qualified_name]

    def create(self, properties):
        self.calls.append(("create", dict(properties)))
        if self.refuse_create:
            raise er.RuleGatewayError(self.refuse_create)
        guid = f"rule-{len(self.rules) + 1}"
        self.rules[guid] = er.RuleRead(guid, self.read_as_type or er.TYPE_NAME, dict(properties), "steward",
                                       "2026-10-28T10:00:00")
        return guid

    def read(self, guid):
        self.calls.append(("read", guid))
        if self.unreadable:
            raise er.RuleGatewayError("Egeria could not be reached")
        rule = self.rules.get(guid)
        return dataclasses.replace(rule, properties=dict(rule.properties)) if rule else None

    def update(self, guid, properties):
        self.calls.append(("update", guid, dict(properties)))
        rule = self.rules[guid]
        rule.properties.update({k: v for k, v in properties.items() if k not in ("class", "typeName")})

    def link(self, element_guid, rule_guid):
        self.calls.append(("link", element_guid, rule_guid))
        if (element_guid, rule_guid) in self.links:
            return True, "already linked"
        self.links.add((element_guid, rule_guid))
        return True, "linked"

    def unlink(self, element_guid, rule_guid):
        self.calls.append(("unlink", element_guid, rule_guid))
        self.links.discard((element_guid, rule_guid))

    def made(self, kind):
        return [c for c in self.calls if c[0] == kind]


def _element_qn(entity, path):
    return er.element_qualified_name(entity, path)


def _published(entity):
    return dataclasses.replace(entity, egeria_asset_guid=ASSET_GUID)


def _gateway(entity):
    return FakeGateway({_element_qn(entity, "sales.orders.order_id"): COLUMN_GUID,
                        _element_qn(entity, "sales.orders"): TABLE_GUID})


def _publish(registry, entity, gateway, by="steward"):
    return er.publish_database_rules(registry, _published(entity), gateway, recorded_by=by)


# ── the mapping ───────────────────────────────────────────────────────────────────────────────────────────

def test_an_accepted_unique_check_maps_to_the_odcs_library_rule():
    tt = load_catalogue()["Unique"]
    props = er.rule_properties(UNIQUE, tt, {"threshold_value": 0, "severity": "error",
                                            "profile_surveyed_at": "2026-10-20T10:00:00+00:00"},
                               verdict={"decided_by": "steward", "created_at": "2026-10-21"},
                               proposal={"origin": "vendored", "catalogue_ref": "testgen@3606859c:Unique"})
    assert props["class"] == "DataQualityRuleProperties" and props["typeName"] == "DataQualityRule"
    assert props["method"] == "Unique" and props["checkType"] == "library" and props["metric"] == "duplicateValues"
    assert props["qualityEngine"] == "resource-explorer" and props["contentStatus"] == "ACTIVE"
    assert props["comparisonOperator"] == "mustBeLessOrEqualTo" and props["thresholdValues"] == ["0"]
    assert props["severity"] == "error" and props["scope"] == "sales.orders.order_id"
    assert props["qualityDimension"] == "uniqueness"
    assert "expression" not in props and "qualifiedName" not in props        # RE never stores runnable SQL
    extra = props["additionalProperties"]
    assert extra["checkKey"] == UNIQUE and extra["origin"] == "vendored" and extra["acceptedBy"] == "steward"
    assert "originalDimension" not in extra


def test_validity_is_accuracy_by_the_owners_ruling_and_keeps_its_name():
    assert egeria_dimension("Validity") == "accuracy" and original_dimension("Validity") == "Validity"
    assert original_dimension("Uniqueness") == "" and original_dimension("") == ""
    tt = load_catalogue()["Min_Date"]
    props = er.rule_properties("sales.orders.placed::Min_Date", tt,
                               {"baseline_value": "2020-01-01", "threshold_value": 0, "severity": "error"})
    assert props["qualityDimension"] == "accuracy"
    assert props["additionalProperties"]["originalDimension"] == "Validity"


def test_a_table_check_is_custom_with_no_metric_and_a_bad_severity_writes_no_rule():
    tt = load_catalogue()["Row_Ct_Pct"]
    props = er.rule_properties(ROWS, tt, {"baseline_ct": 1000, "threshold_value": 10, "severity": "warning"})
    assert props["checkType"] == "custom" and "metric" not in props and props["scope"] == "sales.orders"
    with pytest.raises(er.ParameterError):
        er.rule_properties(ROWS, tt, {"baseline_ct": 1000, "threshold_value": 10, "severity": "Fail"})


def test_the_qualified_name_is_the_bitol_shape_rooted_at_the_asset():
    assert er.rule_qualified_name(ASSET_QN, UNIQUE) == f"{ASSET_QN}::Quality::{UNIQUE}"


# ── Publish ───────────────────────────────────────────────────────────────────────────────────────────────

def test_publish_creates_a_reusable_rule_reads_back_and_links_column_and_asset_and_a_republish_writes_nothing(
        registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    out = _publish(registry, entity, gw)
    assert out["counts"] == {"created": 1} and out["line"] == "quality rules: 1 new"
    (create,) = gw.made("create")
    assert create[1]["qualifiedName"] == f"{ASSET_QN}::Quality::{UNIQUE}"
    assert gw.links == {(COLUMN_GUID, "rule-1"), (ASSET_GUID, "rule-1")}
    proof = registry.latest_quality_rule_proofs(SLUG)[UNIQUE]
    assert proof["outcome"] == "created" and proof["rule_guid"] == "rule-1" and proof["read_at"]
    assert proof["detail_json"]["sent"] == {"comparisonOperator": "mustBeLessOrEqualTo", "thresholdValues": ["0"],
                                            "severity": "error"}

    gw.calls.clear()
    again = _publish(registry, entity, gw)
    assert again["counts"] == {"unchanged": 1}
    assert not gw.made("create") and not gw.made("update")
    assert gw.links == {(COLUMN_GUID, "rule-1"), (ASSET_GUID, "rule-1")}     # checked, never doubled


def test_a_parameter_edited_in_re_updates_the_same_rule(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    _publish(registry, entity, gw)
    _accept(registry, UNIQUE, "Unique", threshold_value=5, severity="warning")
    out = _publish(registry, entity, gw)
    assert out["counts"] == {"updated": 1} and len(gw.made("create")) == 1
    assert gw.rules["rule-1"].properties["thresholdValues"] == ["5"]
    assert gw.rules["rule-1"].properties["severity"] == "warning"


def test_a_rule_edited_in_egeria_is_not_overwritten(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    _publish(registry, entity, gw)
    gw.rules["rule-1"].properties["thresholdValues"] = ["3"]           # someone edited it in Egeria
    _accept(registry, UNIQUE, "Unique", threshold_value=9)              # and RE changed it too
    out = _publish(registry, entity, gw)
    assert out["counts"] == {"edited_in_egeria": 1} and not gw.made("update")
    assert gw.rules["rule-1"].properties["thresholdValues"] == ["3"]
    proof = registry.latest_quality_rule_proofs(SLUG)[UNIQUE]
    assert proof["detail_json"]["fields"] == ["thresholdValues"]
    assert er.rule_states(registry, SLUG)["rules"][UNIQUE]["words"].startswith("edited in Egeria · steward")


def test_reject_after_publish_deprecates_and_unlinks_never_deletes(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    _publish(registry, entity, gw)
    registry.record_quality_verdict(SLUG, UNIQUE, "rejected", {}, decided_by="steward")
    assert er.rule_states(registry, SLUG)["rules"][UNIQUE]["words"] == "in Egeria · rejected here · publish to retire"
    out = _publish(registry, entity, gw)
    assert out["counts"] == {"retired": 1}
    assert gw.rules["rule-1"].properties["contentStatus"] == "DEPRECATED" and "rule-1" in gw.rules
    assert gw.links == set()
    assert gw.made("unlink") == [("unlink", COLUMN_GUID, "rule-1"), ("unlink", ASSET_GUID, "rule-1")]
    # re-accepting brings the same rule back, by name
    _accept(registry, UNIQUE, "Unique")
    back = _publish(registry, entity, gw)
    assert back["counts"] == {"reactivated": 1} and len(gw.made("create")) == 1
    assert gw.rules["rule-1"].properties["contentStatus"] == "ACTIVE"
    assert gw.links == {(COLUMN_GUID, "rule-1"), (ASSET_GUID, "rule-1")}


def test_a_refused_subtype_create_never_falls_back(registry, entity):
    """The no-fallback rule (DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md 2.6, condition 1): the server's refusal ends the
    check with its sentence; exactly one create, of the subtype, and no proof of a rule."""
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    gw.refuse_create = "OMAG-COMMON-400-029 unknown properties class DataQualityRuleProperties"
    out = _publish(registry, entity, gw)
    assert out["counts"] == {"refused": 1}
    (create,) = gw.made("create")
    assert create[1]["class"] == "DataQualityRuleProperties"
    assert not gw.made("link") and not gw.rules
    proof = registry.latest_quality_rule_proofs(SLUG)[UNIQUE]
    assert proof["outcome"] == "refused" and proof["rule_guid"] == "" and "400-029" in proof["detail_json"]["error"]


def test_a_rule_that_reads_back_as_another_type_is_unverified_and_unlinked(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    gw.read_as_type = "GovernanceDefinition"
    out = _publish(registry, entity, gw)
    assert out["counts"] == {"unverified": 1} and not gw.made("link")


def test_two_rules_or_another_type_holding_the_name_stop_the_write(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    qn = f"{ASSET_QN}::Quality::{UNIQUE}"
    gw = _gateway(entity)
    gw.name_holders[qn] = [er.RuleRead("", er.TYPE_NAME, {}), er.RuleRead("", er.TYPE_NAME, {})]
    assert _publish(registry, entity, gw)["counts"] == {"duplicate": 1}
    gw.name_holders[qn] = [er.RuleRead("", "another type", {})]
    assert _publish(registry, entity, gw)["counts"] == {"name_taken": 1}
    assert not gw.made("create") and not gw.made("update")


def test_a_check_whose_column_egeria_does_not_hold_gets_no_rule(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = FakeGateway({})
    out = _publish(registry, entity, gw)
    assert out["counts"] == {"not_in_egeria": 1} and not gw.made("create")
    assert "not in Egeria · commit its schema in Contents" in out["line"]


def test_a_table_check_governs_the_table(registry, entity):
    _accept(registry, ROWS, "Row_Ct_Pct", baseline_ct=1000, threshold_value=10)
    gw = _gateway(entity)
    assert _publish(registry, entity, gw)["counts"] == {"created": 1}
    assert gw.links == {(TABLE_GUID, "rule-1"), (ASSET_GUID, "rule-1")}
    assert ("element_guid", _element_qn(entity, "sales.orders"), "RelationalTable") in gw.calls


def test_a_rule_gone_from_egeria_is_said_and_recreated_next_time(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    _publish(registry, entity, gw)
    del gw.rules["rule-1"]
    assert _publish(registry, entity, gw)["counts"] == {"missing_in_egeria": 1}
    assert _publish(registry, entity, gw)["counts"] == {"created": 1}


def test_a_database_not_in_egeria_writes_nothing(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    out = er.publish_database_rules(registry, entity, gw, recorded_by="steward")
    assert out["state"] == "not_published" and gw.calls == []
    assert registry.latest_quality_rule_proofs(SLUG) == {}


def test_proof_rows_before_an_egeria_reset_are_not_latest(registry, entity, monkeypatch):
    _accept(registry, UNIQUE, "Unique")
    _publish(registry, entity, _gateway(entity))
    assert registry.latest_quality_rule_proofs(SLUG)
    monkeypatch.setattr(type(registry), "get_egeria_reset_at", lambda self, slug: "2999-01-01T00:00:00+00:00")
    assert registry.latest_quality_rule_proofs(SLUG) == {}


# ── the run step ──────────────────────────────────────────────────────────────────────────────────────────

def _rule(source, **props):
    return {"rule_guid": "rule-1", "source": source, "properties": props or None, "note": ""}


def test_egerias_threshold_and_severity_override_the_verdicts():
    tt = load_catalogue()["Unique"]
    row: dict = {}
    params, refused = apply_rule(_rule("egeria", comparisonOperator="mustBeLessOrEqualTo", thresholdValues=["4"],
                                       severity="warning"), tt, {"threshold_value": 0, "severity": "error"}, row)
    assert refused == "" and params["threshold_value"] == "4" and params["severity"] == "warning"
    assert row["rule_source"] == "egeria" and row["rule_guid"] == "rule-1"


@pytest.mark.parametrize("rule,words", [
    (_rule("missing"), "rule not found in Egeria"),
    (_rule("egeria", contentStatus="DEPRECATED"), "rule deprecated in Egeria"),
    (_rule("egeria", comparisonOperator="mustBe"), "operator edited in Egeria to mustBe"),
    (_rule("egeria", checkType="sql"), "did not write"),
])
def test_a_rule_the_run_cannot_honour_is_not_run(rule, words):
    _, refused = apply_rule(rule, load_catalogue()["Unique"], {"threshold_value": 0, "severity": "error"}, {})
    assert words in refused


def test_rules_for_run_tells_unreadable_from_gone_and_notes_an_edit():
    proofs = {
        "a::X": {"outcome": "created", "rule_guid": "g1", "detail_json": {"sent": {"thresholdValues": ["0"]}}},
        "b::X": {"outcome": "created", "rule_guid": "g2", "detail_json": {}},
        "c::X": {"outcome": "retired", "rule_guid": "g3", "detail_json": {}},
    }
    reads = {"g1": er.RuleRead("g1", er.TYPE_NAME, {"thresholdValues": ["7"]}), "g2": None}
    got = er.rules_for_run(proofs, lambda g: reads[g])
    assert set(got) == {"a::X", "b::X"}                          # a retired rule is not a published one
    assert got["a::X"]["source"] == "egeria" and got["a::X"]["note"] == "rule edited in Egeria since acceptance"
    assert got["b::X"]["source"] == "missing"

    def unreadable(_g):
        raise er.RuleGatewayError("timeout")

    cached = er.rules_for_run(proofs, unreadable)
    assert {v["source"] for v in cached.values()} == {"local_cache"}
    assert "timeout" in cached["a::X"]["note"]


def test_no_published_rule_leaves_the_run_as_it_was(registry, entity):
    assert er.read_rules_for_run(registry, SLUG, gateway_factory=lambda: pytest.fail("no Egeria read")) is None


def test_a_run_under_a_published_rule_uses_egerias_threshold_and_names_the_rule(registry, entity):
    _accept(registry, UNIQUE, "Unique")
    gw = _gateway(entity)
    _publish(registry, entity, gw)
    gw.rules["rule-1"].properties["severity"] = "warning"
    rules = er.read_rules_for_run(registry, SLUG, gateway_factory=lambda: gw)
    conn = FakeConn(checks={("sales", "orders"): {"record_ct": 1000, "measure": _orders_measure}})
    out = run_quality_checks(entity, registry, _connect(conn), bounds=BOUNDS, surveyed_at="2026-10-28T12:00:00+00:00",
                             rules=rules)
    row = _by_key(registry, "database_quality_results")[UNIQUE]
    assert row["rule_source"] == "egeria" and row["rule_guid"] == "rule-1" and row["severity"] == "warning"
    (ann,) = [a for a in out["quality_annotations"] if a.item_key == UNIQUE]
    assert ann.additional_properties["ruleSource"] == "egeria" and ann.additional_properties["ruleGuid"] == "rule-1"
    assert ann.additional_properties["ruleNote"] == "rule edited in Egeria since acceptance"


def test_the_annotation_says_validitys_original_name():
    row = {"check_key": "s.t.c::Min_Date", "schema_name": "s", "table_name": "t", "column_name": "c",
           "test_type": "Min_Date", "measure": 0.0, "record_ct": 10, "status": "pass", "reason": "ok",
           "severity": "error", "comparison_operator": "mustBeLessOrEqualTo", "threshold_values_json": [0],
           "params_json": None, "baseline_profile_surveyed_at": "", "rule_source": "unpublished", "rule_guid": "",
           "ran_as": "", "state": "measured"}
    (ann,) = build_quality_annotations([row])
    assert ann.quality_dimension == "accuracy" and ann.additional_properties["originalDimension"] == "Validity"


# ── the pyegeria gateway ──────────────────────────────────────────────────────────────────────────────────

def _real_gateway():
    from pyegeria import MetadataExpert
    from pyegeria.omvs.governance_officer import GovernanceOfficer

    go = create_autospec(GovernanceOfficer, spec_set=True, instance=True)
    expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    return er.PyegeriaRuleGateway(go, expert), go, expert


def test_the_create_body_is_the_subtype_as_its_own_anchor():
    """A reusable rule is its own anchor (owner, 2026-10-28); what it checks is said by GovernedBy, not the anchor."""
    gw, go, _ = _real_gateway()
    go.create_governance_definition.return_value = "rule-guid"
    assert gw.create({"class": "DataQualityRuleProperties", "typeName": "DataQualityRule"}) == "rule-guid"
    (body,) = go.create_governance_definition.call_args.args
    assert body == {"class": "NewElementRequestBody", "isOwnAnchor": True,
                    "properties": {"class": "DataQualityRuleProperties", "typeName": "DataQualityRule"}}
    # pyegeria's own model keeps the anchor choice and the subtype as sent
    from pyegeria.models.models import NewElementRequestBody

    dumped = NewElementRequestBody.model_validate(body).model_dump(by_alias=True, exclude_none=True)
    assert dumped["isOwnAnchor"] is True and "anchorGUID" not in dumped
    assert dumped["properties"]["class"] == "DataQualityRuleProperties"


def test_a_create_that_raises_is_one_call_and_a_gateway_error():
    gw, go, _ = _real_gateway()
    go.create_governance_definition.side_effect = RuntimeError("OMAG-COMMON-400-029")
    with pytest.raises(er.RuleGatewayError, match="400-029"):
        gw.create({"class": "DataQualityRuleProperties"})
    assert go.create_governance_definition.call_count == 1


def test_a_read_is_gone_only_when_egeria_says_so():
    gw, go, _ = _real_gateway()
    go.get_governance_definition_by_guid.return_value = "No elements found"
    assert gw.read("g") is None
    go.get_governance_definition_by_guid.return_value = None
    with pytest.raises(er.RuleGatewayError):
        gw.read("g")
    go.get_governance_definition_by_guid.side_effect = TimeoutError("read timed out")
    with pytest.raises(er.RuleGatewayError):
        gw.read("g")
    go.get_governance_definition_by_guid.side_effect = None
    go.get_governance_definition_by_guid.return_value = {
        "elementHeader": {"guid": "g", "type": {"typeName": "DataQualityRule"},
                          "versions": {"updatedBy": "erinoverview", "updateTime": "2026-10-28T09:00:00"}},
        "properties": {"thresholdValues": ["0"], "method": "Unique"}}
    read = gw.read("g")
    assert (read.type_name, read.updated_by, read.properties["method"]) == ("DataQualityRule", "erinoverview", "Unique")


def test_the_link_reads_from_the_rule_at_end_two_and_links_once(monkeypatch):
    gw, go, _expert = _real_gateway()
    seen = {}

    def fake_link_once(metadata_expert, **kw):
        from resource_explorer.egeria_link_once import LINKED, LinkOutcome

        seen.update(kw)
        kw["create"]()
        return LinkOutcome(LINKED, relationship_guid="rel-1")

    monkeypatch.setattr("resource_explorer.egeria_link_once.link_once", fake_link_once)
    assert gw.link(COLUMN_GUID, "rule-1") == (True, "linked")
    assert seen["relationship_type"] == "GovernedBy" and seen["read_guid"] == "rule-1" and seen["read_end"] == 2
    assert seen["far_guid"] == COLUMN_GUID
    args = go.attach_governed_by_definition.call_args.args
    assert args[:2] == (COLUMN_GUID, "rule-1") and args[2]["properties"]["class"] == "GovernedByProperties"
