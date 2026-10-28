"""scripts/backfill_classification_annotations.py: the proposals Egeria dropped, republished in place from the outbox.

The only fake is the Egeria client boundary: `DataDiscovery` itself (`create_autospec(spec_set=True)`), storing what
Egeria 6.2 keeps (tests/egeria_annotation_beans.py) and merging an update the way Egeria's builder does (every
primitive of the bean is written, so one left out of the body becomes 0). The registry is a real one on a temp SQLite
file, its outbox rows written by the registry's own enqueue/done calls.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
from pyegeria import DataDiscovery

from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors.annotation_props import CANDIDATES_KEY, classification_fields
from tests.egeria_annotation_beans import egeria_keeps

_spec = importlib.util.spec_from_file_location(
    "backfill_classification_annotations",
    pathlib.Path(__file__).parent.parent / "scripts" / "backfill_classification_annotations.py",
)
bf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bf      # @dataclass looks its module up
_spec.loader.exec_module(bf)

PLATFORM = "https://egeria.test:9443"
CLS = "ClassificationAnnotationProperties"
#: Primitive fields of ClassificationAnnotationProperties' bean: Egeria's builder writes each on every merge update.
PRIMITIVES = ("confidence", "sampleSize", "samplePercent", "absoluteUncertainty", "relativeUncertainty",
              "matchingValues", "nonMatchingValues")


def old_body(qn: str, candidates: list[str], *, step: str = "security_hygiene", confidence: int = 80) -> dict:
    """The body RE enqueued before 2026-10-15 (pre-#704 build_annotation_props): the proposal in a field Egeria drops."""
    props = {"class": CLS, "qualifiedName": qn, "annotationType": step, "summary": f"{step} result",
             "analysisStep": step, "confidence": confidence}
    if candidates:
        props["candidateClassifications"] = candidates
    return {"class": "NewElementRequestBody", "parentGUID": "report-1",
            "parentRelationshipTypeName": "ReportedAnnotation", "isOwnAnchor": False, "anchorGUID": "report-1",
            "properties": props}


class FakeEgeria:
    """What Egeria holds, behind an autospec'd DataDiscovery."""

    def __init__(self, monkeypatch):
        self.elements: dict[str, dict] = {}
        self.updates: list[tuple[str, dict]] = []
        self.fail_guids: set[str] = set()
        self.ignore_updates = False
        self.zero_on_update: set[str] = set()     # primitives Egeria (wrongly) resets whatever the body says
        # Every instance carries these (pyegeria's ServerClient.__init__ sets them); the class does not, so autospec
        # cannot see them. Declared on the class for the spec only.
        monkeypatch.setattr(DataDiscovery, "platform_url", PLATFORM, raising=False)
        monkeypatch.setattr(DataDiscovery, "view_server", "qs-view-server", raising=False)
        d = create_autospec(DataDiscovery, spec_set=True, instance=True)
        d.platform_url, d.view_server = PLATFORM, "qs-view-server"
        d.find_annotations.side_effect = self.find_annotations
        d.get_annotation_by_guid.side_effect = self.get_annotation_by_guid
        d._async_update_element_body_request.side_effect = self.update
        self.discovery = d

    def create(self, guid: str, body: dict, type_name: str = "ClassificationAnnotation") -> None:
        kept, _dropped = egeria_keeps(body["properties"]) if type_name == "ClassificationAnnotation" else (
            dict(body["properties"]), [])
        kept.pop("class", None)
        for k in PRIMITIVES:
            kept.setdefault(k, 0)
        self.elements[guid] = {"elementHeader": {"guid": guid, "type": {"typeName": type_name}}, "properties": kept}

    def find_annotations(self, search_string="*", body=None, starts_with=True, ends_with=False, ignore_case=False,
                         start_from=0, page_size=100, graph_query_depth=3, output_format="JSON",
                         report_spec="Referenceable", **kwargs):
        want = kwargs.get("metadata_element_type")
        hits = sorted((e for e in self.elements.values()
                       if e["properties"].get("qualifiedName", "").startswith(search_string)
                       and (not want or e["elementHeader"]["type"]["typeName"] == want)),
                      key=lambda e: e["elementHeader"]["guid"])
        if kwargs.get("sequencing_order"):
            hits = hits[::2]      # measured 2026-10-15 (398 of 598): an ordered search silently drops elements
        page = [json.loads(json.dumps(e)) for e in hits[start_from:start_from + page_size]]
        return page or "No elements found"

    def get_annotation_by_guid(self, guid, element_type="Annotation", body=None, graph_query_depth=3,
                               output_format="JSON", report_spec="Annotations", **kwargs):
        e = self.elements.get(guid)
        return json.loads(json.dumps(e)) if e else "No elements found"

    async def update(self, url, prop=None, body=None):
        if body is None:
            raise AttributeError("'NoneType' object has no attribute 'model_dump_json'")   # pyegeria's own failure
        assert url.startswith(f"{PLATFORM}/servers/qs-view-server/api/open-metadata/data-discovery/annotations/")
        guid = url.rsplit("/", 2)[-2]
        assert url.endswith(f"/annotations/{guid}/update")
        if guid in self.fail_guids:
            raise RuntimeError("Egeria said no (500)")
        assert body["class"] == "UpdateElementRequestBody" and body["mergeUpdate"] is True
        kept, dropped = egeria_keeps(body["properties"])
        assert dropped == [], f"Egeria would drop {dropped}"
        self.updates.append((guid, body))
        if self.ignore_updates:
            return
        props = self.elements[guid]["properties"]
        for k in PRIMITIVES:                     # OpenMetadataElementBuilder writes every primitive on a merge
            props[k] = 0 if k in self.zero_on_update else kept.get(k, 0)
        for k, v in kept.items():
            if k != "class" and k not in PRIMITIVES and v is not None:
                props[k] = v
        return

@pytest.fixture()
def egeria(monkeypatch):
    return FakeEgeria(monkeypatch)


@pytest.fixture()
def registry(tmp_path):
    return ProjectRegistry(database_url=f"sqlite:///{tmp_path / 'reg.db'}")


def published(registry, egeria, guid, qn, candidates, *, record_guid=True, status_done=True, **kw):
    """One annotation as RE published it before #704: outbox row with the old body, and Egeria's damaged copy."""
    body = old_body(qn, candidates, **kw)
    row = registry.enqueue_outbox_element("repo", "p", "annotation", qn, body, run_id="run")
    if status_done:
        registry.mark_outbox_done(row, guid if record_guid else "")
    egeria.create(guid, body)
    return row


def run(egeria, registry, tmp_path, *, apply, out=None, page_size=200, limit=None):
    lines = []
    code = bf.run(egeria.discovery, registry, apply=apply, log_path=tmp_path / "log.jsonl",
                  out=(out or lines.append), page_size=page_size, limit=limit)
    return code, "\n".join(lines)


def log_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


QN = "Annotation::egeria_git::2026-10-12T10:00:00::{}"


class TestEndToEnd:
    def test_enumerate_plan_apply_then_rerun_is_a_no_op(self, egeria, registry, tmp_path):
        published(registry, egeria, "g-ranked", QN.format(1), ["oltp", "warehouse"], step="db_classification")
        published(registry, egeria, "g-single", QN.format(2), ["HasSecurityPolicy"], confidence=55)
        published(registry, egeria, "g-none", QN.format(3), [])
        # Landed in Egeria; the row never recorded the GUID (the create's reply was lost): keyed on qualifiedName.
        published(registry, egeria, "g-byqn", QN.format(4), ["MIT"], record_guid=False, status_done=False)
        # Published directly, no outbox row: reported, never guessed.
        egeria.create("g-db", old_body("Annotation::PostgreSQL::coco::2026-10-12::0", ["oltp"]))
        # Not RE's, and not a ClassificationAnnotation: never enumerated.
        egeria.create("g-other", old_body("SomeoneElse::1", ["x"]))
        egeria.create("g-measure", {"properties": {"class": "ResourceMeasureAnnotationProperties",
                                                   "qualifiedName": QN.format(9)}}, type_name="ResourceMeasureAnnotation")

        code, text = run(egeria, registry, tmp_path, apply=False)
        assert code == 0 and egeria.updates == []
        assert "with qualifiedName prefix Annotation:: (RE and others): 5 ===" in text
        s = bf.summarize(bf.make_plan(bf.enumerate_annotations(egeria.discovery),
                                      registry.list_outbox_payloads("annotation", CLS)))
        assert s["by_outcome"] == {bf.REPAIR: 3, bf.NO_PROPOSAL: 1, bf.NO_LOCAL_SOURCE: 1}
        assert s["by_source"] == {"outbox:egeria_guid": 3, "outbox:qualified_name": 1, "(none)": 1}
        assert "g-db" in text and "no_local_source" in text

        code, text = run(egeria, registry, tmp_path, apply=True)
        assert code == 0, text
        assert sorted(g for g, _ in egeria.updates) == ["g-byqn", "g-ranked", "g-single"]
        ranked = egeria.elements["g-ranked"]["properties"]
        assert ranked["classificationName"] == "oltp"
        assert json.loads(ranked["properties"][CANDIDATES_KEY]) == ["oltp", "warehouse"]
        assert ranked["confidence"] == 80                       # carried: Egeria's merge would have zeroed it
        assert egeria.elements["g-single"]["properties"]["confidence"] == 55
        assert egeria.elements["g-single"]["properties"]["classificationName"] == "HasSecurityPolicy"
        assert "properties" not in egeria.elements["g-single"]["properties"]   # one proposal: no list to keep
        assert "classificationName" not in egeria.elements["g-none"]["properties"]
        assert "classificationName" not in egeria.elements["g-db"]["properties"]
        assert ranked["summary"] == "db_classification result" and ranked["qualifiedName"] == QN.format(1)
        assert len(egeria.elements) == 7                          # nothing created, nothing deleted
        written = [e for e in log_lines(tmp_path) if e.get("outcome") == "written"]
        assert {e["guid"] for e in written} == {"g-byqn", "g-ranked", "g-single"}
        assert all(e["before"] == {} and e["after"]["classificationName"] for e in written)

        egeria.updates.clear()
        code, text = run(egeria, registry, tmp_path, apply=True)
        assert code == 0 and egeria.updates == []
        assert "3  already_current" in text

    def test_the_plan_is_the_publishers_own_function(self, egeria, registry, tmp_path):
        """The planned fields are classification_fields' output for what RE sent, and each body is one Egeria keeps
        whole; the number written is the number planned."""
        cases = {"g1": ["a", "b", "c"], "g2": ["only"], "g3": ["x", "y"]}
        for i, (g, c) in enumerate(cases.items()):
            published(registry, egeria, g, QN.format(i), c)
        plan = bf.make_plan(bf.enumerate_annotations(egeria.discovery),
                            registry.list_outbox_payloads("annotation", CLS))
        for item in plan:
            want = classification_fields(SimpleNamespace(candidate_classifications=cases[item.element.guid],
                                                         classification_name="", classification_properties={}))
            assert item.after == want
            assert egeria_keeps(item.body["properties"])[1] == []
            assert {k: item.body["properties"][k] for k in bf.FIELDS if k in item.body["properties"]} == want
        planned = bf.summarize(plan)["by_outcome"][bf.REPAIR]
        run(egeria, registry, tmp_path, apply=True)
        assert len(egeria.updates) == planned == 3


class TestNeverGuessed:
    def test_annotation_with_no_local_source_is_reported_not_written(self, egeria, registry, tmp_path):
        egeria.create("g-orphan", old_body(QN.format(1), ["oltp"]))
        code, text = run(egeria, registry, tmp_path, apply=True)
        assert code == 0 and egeria.updates == []
        assert "reported, not written (1)" in text and "g-orphan" in text
        assert [e["outcome"] for e in log_lines(tmp_path)] == [bf.NO_LOCAL_SOURCE]

    def test_disagreeing_rows_are_ambiguous_not_written(self, egeria, registry, tmp_path):
        published(registry, egeria, "g-a", QN.format(1), ["one"], record_guid=False, status_done=False)
        registry.enqueue_outbox_element("repo", "p", "annotation", QN.format(1), old_body(QN.format(1), ["two"]))
        _code, text = run(egeria, registry, tmp_path, apply=True)
        assert egeria.updates == [] and bf.AMBIGUOUS_SOURCE in text

    def test_a_different_name_already_in_egeria_is_left_alone(self, egeria, registry, tmp_path):
        published(registry, egeria, "g-a", QN.format(1), ["oltp"])
        egeria.elements["g-a"]["properties"]["classificationName"] = "warehouse"
        _code, text = run(egeria, registry, tmp_path, apply=True)
        assert egeria.updates == [] and bf.DIFFERS in text


class TestFailures:
    def test_a_failure_mid_batch_does_not_stop_it(self, egeria, registry, tmp_path):
        for i, g in enumerate(("g1", "g2", "g3")):
            published(registry, egeria, g, QN.format(i), [f"c{i}"])
        egeria.fail_guids = {"g2"}
        code, text = run(egeria, registry, tmp_path, apply=True)
        assert code == 1
        assert egeria.elements["g1"]["properties"]["classificationName"] == "c0"
        assert egeria.elements["g3"]["properties"]["classificationName"] == "c2"
        assert "classificationName" not in egeria.elements["g2"]["properties"]
        assert "FAILED g2" in text and "written (read back and verified): 2   failed: 1" in text
        assert [e["guid"] for e in log_lines(tmp_path) if e.get("outcome") == "failed"] == ["g2"]
        # The re-run writes only what is left.
        egeria.fail_guids, egeria.updates = set(), []
        assert run(egeria, registry, tmp_path, apply=True)[0] == 0
        assert [g for g, _ in egeria.updates] == ["g2"]

    def test_a_write_egeria_did_not_keep_is_a_failure_not_a_success(self, egeria, registry, tmp_path):
        published(registry, egeria, "g1", QN.format(1), ["c"])
        egeria.ignore_updates = True
        code, text = run(egeria, registry, tmp_path, apply=True)
        assert code == 1 and "read-back differs" in text

    def test_an_unreadable_search_stops_before_any_write(self, egeria, registry, tmp_path):
        published(registry, egeria, "g1", QN.format(1), ["c"])
        egeria.discovery.find_annotations.side_effect = lambda **kw: "Error: 401 not authorized"
        with pytest.raises(RuntimeError, match="did not answer with a list"):
            run(egeria, registry, tmp_path, apply=True)
        assert egeria.updates == []

    def test_enumeration_pages_through_everything(self, egeria, registry, tmp_path):
        for i in range(5):
            published(registry, egeria, f"g{i}", QN.format(i), [f"c{i}"])
        assert len(bf.enumerate_annotations(egeria.discovery, page_size=2)) == 5

    def test_enumeration_finds_all_though_an_ordered_search_would_drop_some(self, egeria, registry, tmp_path):
        for i in range(6):
            published(registry, egeria, f"g{i}", QN.format(i), [f"c{i}"])
        ordered = egeria.find_annotations(search_string=bf.RE_PREFIX, page_size=50,
                                          sequencing_order="CREATION_DATE_OLDEST")
        assert len(ordered) < 6                                  # the fake models the drop
        assert len(bf.enumerate_annotations(egeria.discovery, page_size=2)) == 6
        for call in egeria.discovery.find_annotations.call_args_list:
            assert "sequencing_order" not in call.kwargs

    def test_two_passes_that_differ_warn_and_use_the_union(self, egeria, registry, tmp_path):
        for i in range(3):
            published(registry, egeria, f"g{i}", QN.format(i), [f"c{i}"])
        real = egeria.find_annotations
        calls = {"n": 0}

        def flaky(**kw):
            calls["n"] += 1
            page = real(**kw)
            return page[:-1] if calls["n"] == 1 and isinstance(page, list) else page   # pass 1 misses one

        egeria.discovery.find_annotations.side_effect = flaky
        warnings = []
        assert len(bf.enumerate_annotations(egeria.discovery, page_size=50, warn=warnings.append)) == 3
        assert len(warnings) == 1 and "pass 1 found 2" in warnings[0] and "pass 2 found 3" in warnings[0]
        warnings.clear()
        egeria.discovery.find_annotations.side_effect = real
        bf.enumerate_annotations(egeria.discovery, page_size=50, warn=warnings.append)
        assert warnings == []


def test_pyegerias_update_annotation_sends_nothing(monkeypatch):
    """Why the script does not call DataDiscovery.update_annotation (pyegeria as installed): the wrapper passes the
    body positionally into the helper's `prop` slot, so it raises and no request is made. When this fails, pyegeria
    has been fixed and the script can call the public method."""
    sent = []

    async def make_request(self, method, url, payload=None, *a, **k):
        sent.append(url)
        return SimpleNamespace(json=lambda: {"class": "BooleanResponse", "relatedHTTPCode": 200})

    monkeypatch.setattr("pyegeria.core._server_client.ServerClient._async_make_request", make_request)
    client = DataDiscovery.__new__(DataDiscovery)
    client.platform_url, client.view_server = PLATFORM, "vs"
    body = {"class": "UpdateElementRequestBody", "mergeUpdate": True,
            "properties": {"class": CLS, "classificationName": "oltp"}}
    with pytest.raises(AttributeError):
        client.update_annotation("g", body)
    assert sent == []
    # The helper the script calls, with the body where it belongs, does send it.
    asyncio.get_event_loop().run_until_complete(
        client._async_update_element_body_request(f"{PLATFORM}/x/annotations/g/update", body=body))
    assert sent == [f"{PLATFORM}/x/annotations/g/update"]


def test_main_runs_as_the_resync_daemon_and_dry_runs_by_default(egeria, registry, tmp_path, monkeypatch):
    import resource_explorer.egeria_clients as ec
    import resource_explorer.registry as reg_mod

    published(registry, egeria, "g1", QN.format(1), ["c"])
    seen = {}

    def factory(identity, *, purpose, **_):
        seen["identity"] = identity
        return SimpleNamespace(of=lambda cls: egeria.discovery if cls is DataDiscovery else None)

    monkeypatch.setattr(ec, "egeria_client", factory)
    monkeypatch.setattr(reg_mod, "ProjectRegistry", lambda: registry)
    assert bf.main(["--log", str(tmp_path / "log.jsonl")]) == 0
    assert seen["identity"].reason == ec.DaemonReason.RESYNC.value
    assert egeria.updates == []


def test_limit_writes_a_canary_and_a_rerun_continues(egeria, registry, tmp_path):
    for i in range(4):
        published(registry, egeria, f"g{i}", QN.format(i), [f"c{i}"])
    assert bf.run(egeria.discovery, registry, apply=True, log_path=tmp_path / "l", out=lambda _: None, limit=1) == 0
    assert len(egeria.updates) == 1
    assert bf.run(egeria.discovery, registry, apply=True, log_path=tmp_path / "l", out=lambda _: None) == 0
    assert len(egeria.updates) == 4



class TestReviewRound:
    def test_a_guid_row_naming_another_qualified_name_is_ambiguous(self, egeria, registry, tmp_path):
        """A row's recorded GUID can name a peer element: its body is not this annotation's."""
        row = registry.enqueue_outbox_element("repo", "p", "annotation", QN.format(7), old_body(QN.format(7), ["x"]))
        registry.mark_outbox_done(row, "g-a")
        egeria.create("g-a", old_body(QN.format(1), ["oltp"]))
        _code, text = run(egeria, registry, tmp_path, apply=True)
        assert egeria.updates == [] and bf.AMBIGUOUS_SOURCE in text and QN.format(7) in text

    def test_a_full_page_with_nothing_new_stops_the_enumeration(self, egeria, registry, tmp_path):
        for i in range(3):
            published(registry, egeria, f"g{i}", QN.format(i), [f"c{i}"])
        first = egeria.find_annotations(search_string=bf.RE_PREFIX, page_size=2)
        egeria.discovery.find_annotations.side_effect = lambda **kw: json.loads(json.dumps(first))
        with pytest.raises(RuntimeError, match="paging is not advancing"):
            bf.enumerate_annotations(egeria.discovery, page_size=2)

    def test_limit_is_validated_and_said_to_be_ignored_in_a_dry_run(self, egeria, registry, tmp_path):
        with pytest.raises(SystemExit):
            bf.main(["--limit", "-1"])
        published(registry, egeria, "g1", QN.format(1), ["c"])
        _code, text = run(egeria, registry, tmp_path, apply=False, limit=2)
        assert "--limit 2 is ignored in a dry run" in text and egeria.updates == []

    def test_primitives_are_read_again_just_before_the_write(self, egeria, registry, tmp_path):
        published(registry, egeria, "g1", QN.format(1), ["c"], confidence=40)
        plan = bf.make_plan(bf.enumerate_annotations(egeria.discovery),
                            registry.list_outbox_payloads("annotation", CLS))
        egeria.elements["g1"]["properties"].update(confidence=41, sampleSize=9)   # changed after the plan
        written, failures = bf.apply_plan(egeria.discovery, plan, lambda e: None)
        assert (written, failures) == (1, [])
        sent = egeria.updates[0][1]["properties"]
        assert (sent["confidence"], sent["sampleSize"]) == (41, 9)
        assert egeria.elements["g1"]["properties"]["sampleSize"] == 9

    def test_read_back_checks_every_primitive(self, egeria, registry, tmp_path):
        published(registry, egeria, "g1", QN.format(1), ["c"])
        egeria.elements["g1"]["properties"]["sampleSize"] = 9
        egeria.zero_on_update = {"sampleSize"}
        code, text = run(egeria, registry, tmp_path, apply=True)
        assert code == 1 and "sampleSize" in text

    def test_a_resource_with_a_private_owner_is_reported_not_written(self, egeria, registry, tmp_path, monkeypatch):
        published(registry, egeria, "g1", QN.format(1), ["c"])
        monkeypatch.setattr(registry, "private_owner_for_entity", lambda et, slug: "alice" if slug == "p" else "")
        code, text = run(egeria, registry, tmp_path, apply=True)
        assert code == 0 and egeria.updates == []
        assert bf.PRIVATE_OWNER in text and "alice" in text

    def test_an_unreadable_private_owner_fails_closed(self, egeria, registry, tmp_path, monkeypatch):
        published(registry, egeria, "g1", QN.format(1), ["c"])

        def boom(et, slug):
            raise RuntimeError("registry down")

        monkeypatch.setattr(registry, "private_owner_for_entity", boom)
        _code, text = run(egeria, registry, tmp_path, apply=True)
        assert egeria.updates == [] and bf.PRIVATE_OWNER in text

    def test_outbox_payload_filter_is_literal(self, registry):
        registry.enqueue_outbox_element("repo", "p", "annotation", "Q::1", {"k": "aXb"})
        registry.enqueue_outbox_element("repo", "p", "annotation", "Q::2", {"k": "a_b"})
        assert [r["qualified_name"] for r in registry.list_outbox_payloads("annotation", "a_b")] == ["Q::2"]
        assert registry.list_outbox_payloads("annotation", "%") == []
