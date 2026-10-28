"""RE's keyword sets are plain `ValidValueDefinition`s; Egeria's Reference Data view defaults a find to
`ReferenceDataValue` (egeria `ReferenceDataInstance.java:65-69`), so an untyped
`find_valid_value_definitions` never returns them. Every RE find passes
`metadata_element_type="ValidValueDefinition"`, and both keyword readers (the Curate data-class rules
and the survey's PII detection) say which keyword source they used, from the one function that loaded
the keywords.

The fake binds pyegeria's REAL signature and REFUSES an untyped find, the way the live view hides plain
definitions from one. Fakes only; nothing reaches Egeria.
"""
from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from pyegeria.omvs.reference_data import ReferenceDataManager as _RealRef

from resource_explorer.registry import DatabaseEntity, ProjectRegistry

_FIND_SIG = inspect.signature(_RealRef.find_valid_value_definitions)
VVD = "ValidValueDefinition"
NONE_FOUND = "No elements found"
BLOCKED = "https://egeria-blocked.invalid:9443"


class UntypedFind(AssertionError):
    """The Reference Data view would hide plain ValidValueDefinitions from this call."""


class TypedOnlyRef:
    """`ReferenceDataManager` stand-in. `keywords` maps a data-class name to the keyword values its
    plain-VVD set holds. `fail` makes every find raise (Egeria could not be read)."""

    keywords: dict[str, list[str]] = {}
    fail: bool = False
    fail_names: tuple = ()
    refused: list[str] = []
    asked: list[str] = []

    def __init__(self, *a, **k):
        pass

    def create_egeria_bearer_token(self, *_a, **_k):
        return "fake-token"

    def set_bearer_token(self, *_a, **_k):
        pass

    def close_session(self):
        pass

    def find_valid_value_definitions(self, *args, **kwargs):
        bound = _FIND_SIG.bind(self, *args, **kwargs)           # a wrong keyword is a TypeError here
        args_ = bound.arguments
        extra = args_.get("kwargs", {})
        search = args_.get("search_string", "*")
        type(self).asked.append(search)
        if extra.get("metadata_element_type") != VVD:
            type(self).refused.append(search)
            raise UntypedFind(f"untyped find for {search!r}")
        if type(self).fail or any(search == f"ValidValueDefinition::{n}Keyword::" for n in type(self).fail_names):
            raise RuntimeError("platform not answering")
        if search == "*":
            return [{"elementHeader": {"guid": "g-set", "type": {"typeName": VVD}},
                     "properties": {"qualifiedName": "ValidValuesSet::EmailAddressKeywords"}}]
        for name, values in type(self).keywords.items():
            if search == f"ValidValueDefinition::{name}Keyword::":
                return [{"properties": {"qualifiedName": f"{search}{v}", "preferredValue": v}} for v in values]
        return NONE_FOUND


def _ref(keywords=None, fail=False, fail_names=()):
    return type("Ref", (TypedOnlyRef,), {"keywords": dict(keywords or {}), "fail": fail,
                                          "fail_names": tuple(fail_names), "refused": [], "asked": []})


def test_the_fake_refuses_an_untyped_find_and_binds_the_real_signature():
    ref = _ref()()
    with pytest.raises(UntypedFind):
        ref.find_valid_value_definitions(search_string="x", starts_with=True, output_format="JSON")
    with pytest.raises(TypeError):                              # one positional too many
        ref.find_valid_value_definitions("a", None, True, False, False, 0, 0, 3, "JSON", None, "extra")
    assert ref.find_valid_value_definitions(search_string="x", metadata_element_type=VVD) == NONE_FOUND


# ── 1: the three finds are typed ──────────────────────────────────────────────

def test_the_reference_catalog_find_is_typed():
    from resource_explorer.surveyors.database.egeria_reference_catalog import list_known_valid_value_sets

    Ref = _ref()
    cat = list_known_valid_value_sets(Ref())
    assert Ref.refused == []
    assert cat.available is True and len(cat.valid_value_sets) == 1


@pytest.fixture
def daemon(monkeypatch):
    monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: ("re-daemon", "pw"))
    monkeypatch.setenv("EGERIA_PLATFORM_URL", BLOCKED)
    monkeypatch.setenv("EGERIA_VIEW_SERVER", "v")


@pytest.fixture
def run_as_daemon(daemon):
    """A survey run's identity: the daemon (Brief I), as the scheduler/worker sets it."""
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as

    with acting_as(Daemon(DaemonReason.SCHEDULER)):
        yield


class _Designer:
    """DataDesigner stand-in: every standard DataClass exists."""

    def __init__(self, *a, **k):
        pass

    def set_bearer_token(self, *_a, **_k):
        pass

    def close_session(self):
        pass

    def get_guid_for_name(self, name, property_name=None, type_name=None):
        return f"guid-{name.replace('::', '-')}"


def _rules(monkeypatch, Ref, designer=_Designer):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web import app as web_app

    monkeypatch.setattr("pyegeria.omvs.data_designer.DataDesigner", designer)
    monkeypatch.setattr("pyegeria.omvs.reference_data.ReferenceDataManager", Ref)
    hdr = {"Authorization": "Bearer " + create_access_token(user_id="dan", egeria_token="tok-dan")}
    r = TestClient(web_app.app).get("/api/egeria/rules/dataclasses", headers=hdr)
    assert r.status_code == 200
    return {x["name"]: x for x in r.json()}


def test_the_rules_route_find_is_typed(daemon, monkeypatch):
    Ref = _ref({"EmailAddress": ["mail", "e_mail"]})
    rules = _rules(monkeypatch, Ref)
    assert Ref.refused == []
    assert rules["EmailAddress"]["source"] == "Egeria (Active)"
    assert sorted(rules["EmailAddress"]["keywords"]) == ["e_mail", "mail"]
    assert rules["Password"]["source"] == "Local Fallback"


_VIEW = {
    "name": "contacts", "qualified_name": "public.contacts", "dependencies": ["people"],
    "complexity": {"complexity_score": 1, "portability_rating": 100, "node_count": 1, "join_count": 0,
                   "cte_count": 0, "subquery_count": 0},
    "lineage": {"contact_mail": {"name": "contact_mail", "source": "people", "downstream": []}},
}


def _db():
    return DatabaseEntity(slug="kwdb", display_name="kwdb", db_type="postgresql", host="localhost",
                          port=5432, database_name="kwdb", db_user="a", db_password="b")


def _dc_annotations(annotations):
    from resource_explorer.surveyors.survey_report import AnnotationType
    return [a for a in annotations if a.annotation_type == AnnotationType.DATA_CLASS]


def test_the_survey_pii_find_is_typed(run_as_daemon):
    """`contact_mail` matches only the Egeria keyword `mail`, never the built-in list."""
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

    Ref = _ref({"EmailAddress": ["mail"]})
    with patch("pyegeria.omvs.reference_data.ReferenceDataManager", Ref):
        anns = DatabaseSurveyor(_db(), {}, None)._create_views_annotations([_VIEW])
    assert Ref.refused == []
    assert len(_dc_annotations(anns)) == 1


# ── 3: the keyword source is named on every run ───────────────────────────────

@pytest.mark.parametrize("keywords, fail, label", [
    ({"EmailAddress": ["mail"], "Password": ["pw"]}, False, "keywords: Egeria, 2 sets"),
    ({}, False, "keywords: built-in list · Egeria had none"),
    ({}, True, "keywords: built-in list · could not read Egeria"),
])
def test_the_rules_route_names_its_keyword_source(daemon, monkeypatch, keywords, fail, label):
    rules = _rules(monkeypatch, _ref(keywords, fail))
    assert {r["keyword_source"] for r in rules.values()} == {label}


def test_a_failed_data_class_lookup_is_could_not_read_not_had_none(daemon, monkeypatch):
    class Broken(_Designer):
        def get_guid_for_name(self, *a, **k):
            raise RuntimeError("401")

    rules = _rules(monkeypatch, _ref({"EmailAddress": ["mail"]}), designer=Broken)
    assert {r["keyword_source"] for r in rules.values()} == {"keywords: built-in list · could not read Egeria"}
    assert {r["source"] for r in rules.values()} == {"Local Fallback"}


def test_a_missing_data_class_is_had_none_not_read(daemon, monkeypatch):
    class Missing(_Designer):
        def get_guid_for_name(self, *a, **k):
            return NONE_FOUND                       # pyegeria's miss sentinel, truthy

    Ref = _ref({"EmailAddress": ["mail"]})
    rules = _rules(monkeypatch, Ref, designer=Missing)
    assert {r["keyword_source"] for r in rules.values()} == {"keywords: built-in list · Egeria had none"}
    assert Ref.asked == [], "no keyword find for a DataClass Egeria does not hold"


def test_the_rules_route_names_the_source_to_nobody_signed_in(daemon):
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import app

    rules = TestClient(app).get("/api/egeria/rules/dataclasses").json()
    assert {r["keyword_source"] for r in rules} == {"keywords: built-in list · could not read Egeria (not signed in)"}


@pytest.mark.parametrize("keywords, fail, label, n_dc", [
    ({"EmailAddress": ["mail"]}, False, "keywords: Egeria, 1 set", 1),
    ({}, False, "keywords: built-in list · Egeria had none", 0),
    ({}, True, "keywords: built-in list · could not read Egeria", 0),
])
def test_the_survey_stores_its_keyword_source_and_the_views_route_reads_it(
        run_as_daemon, tmp_path, monkeypatch, keywords, fail, label, n_dc):
    from fastapi.testclient import TestClient

    from resource_explorer.surveyors.database.connection import EngineCapabilities
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

    registry = ProjectRegistry(db_path=str(tmp_path / "kw.db"))
    registry.register_database(_db())

    class Conn:
        capabilities = EngineCapabilities(column_stats=True, tuple_counters=True, index_stats=True)

        def get_schema_info(self):
            return {"schemas": [], "total_tables": 0, "total_columns": 0}

    @contextmanager
    def fake_connection(*_a, **_k):
        yield Conn()

    Ref = _ref(keywords, fail)
    with patch("resource_explorer.surveyors.database.database_surveyor.database_connection", fake_connection), \
            patch.object(DatabaseSurveyor, "_survey_views", return_value=[_VIEW]), \
            patch("pyegeria.omvs.reference_data.ReferenceDataManager", Ref):
        results = DatabaseSurveyor(registry.get_database("kwdb"), {"user": "a", "password": "b"},
                                   registry).survey(steps=["schema", "views"])
    assert Ref.refused == []
    assert len(_dc_annotations(results["annotations"])) == n_dc
    stored = json.loads(registry.get_latest_database_survey("kwdb")["survey_data"])
    assert stored["pii_keyword_source"]["label"] == label

    monkeypatch.setattr(
        "resource_explorer.registry.ProjectRegistry.__init__",
        lambda self, db_path=None, database_url=None: setattr(self, "__dict__", registry.__dict__) or None)
    from resource_explorer.web.app import app
    assert TestClient(app).get("/api/databases/kwdb/views").json()["keyword_source"] == label


def test_the_survey_says_egeria_not_configured_without_a_platform(monkeypatch):
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

    monkeypatch.setattr("resource_explorer.surveyors.database.database_surveyor.egeria_platform_url", lambda: "")
    load = DatabaseSurveyor(_db(), {}, None)._load_pii_keywords()
    assert load.label == "keywords: built-in list · Egeria not configured"


def test_a_survey_with_nobody_to_act_as_says_could_not_read(daemon):
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

    load = DatabaseSurveyor(_db(), {}, None)._load_pii_keywords()
    assert load.label == "keywords: built-in list · could not read Egeria (not signed in)"


# ── review MED-2: a partial read is named, and never narrows detection ────────

def _view(*cols):
    return {**_VIEW, "lineage": {c: {"name": c, "source": "people", "downstream": []} for c in cols}}


def _flagged(anns):
    dc = _dc_annotations(anns)
    return {p["column"] for a in dc for p in a.json_properties["pii_propagations"]}


def test_a_partial_read_is_labelled_and_the_unread_classes_use_the_seed(run_as_daemon):
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

    Ref = _ref({"EmailAddress": ["mail"]}, fail_names=("SocialSecurityNumber", "Password"))
    with patch("pyegeria.omvs.reference_data.ReferenceDataManager", Ref):
        s = DatabaseSurveyor(_db(), {}, None)
        load = s._load_pii_keywords()
        anns = s._create_views_annotations([_view("contact_mail", "ssn_num", "pwd", "email_address")], keywords=load)
    assert load.label == "keywords: Egeria, 1 of 6 sets · 2 could not be read"
    # ssn_num / pwd: the seed answers for the two unread classes. contact_mail / email_address:
    # Egeria's "mail" answers for EmailAddress.
    assert {"contact_mail", "ssn_num", "pwd", "email_address"} == _flagged(anns)


def test_the_whole_list_fallback_is_the_seed(run_as_daemon):
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

    with patch("pyegeria.omvs.reference_data.ReferenceDataManager", _ref({})):
        anns = DatabaseSurveyor(_db(), {}, None)._create_views_annotations([_view("mobile", "telephone", "amount")])
    assert _flagged(anns) == {"mobile", "telephone"}, "the seed's dataPatterns, not the old 8-word list"


def test_the_rules_route_labels_a_partial_read(daemon, monkeypatch):
    rules = _rules(monkeypatch, _ref({"EmailAddress": ["mail"]}, fail_names=("Password",)))
    assert {r["keyword_source"] for r in rules.values()} == {"keywords: Egeria, 1 of 6 sets · 1 could not be read"}
    assert rules["Password"]["source"] == "Local Fallback" and "pwd" in rules["Password"]["keywords"]
