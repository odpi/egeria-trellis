"""Rehearsal 2026-10-05 defects D1, D3, D4, D7 (D2 is `test_catalogue_live_shapes.py`, D6 is in
`test_catalogue_scope.py`). Everything runs against the fake Egeria; nothing touches a network."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
from tests.qn_lookup_bridge import expert_from_legacy

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import (  # noqa: E402,F401  (fixtures are re-used by name)
    ME, SOURCE, _zones, choose, derived, entity, fake, press, registry, step, view, world)

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer import catalogue_gateway as gw  # noqa: E402
from resource_explorer.curate_plan import Curations  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import grant_all, open_access

    fake = open_access(monkeypatch)
    fake.controls.update(grant_all("zone-a", "zone-b"))   # the configured zones admit everyone (start-up zone check)
    return fake

# Brief I: a catalog commit is a person's own action; it runs as a signed-in caller.
pytestmark = pytest.mark.usefixtures("signed_in_caller")


@pytest.fixture
def no_zone_config(monkeypatch):
    """A deployment that never configured publish zones (the autouse fixture in the commit tests
    sets EXPLORER_PUBLISH_ZONES; this removes it and RE's config fallback)."""
    monkeypatch.delenv("EXPLORER_PUBLISH_ZONES", raising=False)
    from resource_explorer.config import get_config
    monkeypatch.setattr(get_config().egeria, "default_catalog_zones", [])


def zone_writes(fake):
    return fake.ops("set_zone_membership")


# ── D1: the commit does not write a zone unless the deployment configured one ──

def test_no_configured_zones_means_no_zone_write_at_all(world, fake, no_zone_config):
    choose(world, "sales", "catalogue")
    out, rec = press(world, fake)
    assert zone_writes(fake) == []                                   # the fake saw ZERO zone writes
    z = step(rec, "zone_membership")
    assert z["state"] == "skipped" and z["detail"].startswith("zones left to Egeria")
    for name in ("publish_elements", "owner", "schema_targets", "read_back"):
        assert step(rec, name)["state"] in ("done", "skipped"), name
    assert len(fake.targets) == 1                                    # the old order failed here (rehearsal)
    assert cc.P_ZONES not in {p["proof"] for p in world["registry"].list_catalogue_commit_proofs("db")}


def test_the_manifest_says_zones_are_left_to_egeria_when_none_is_configured(world, fake, no_zone_config):
    choose(world, "sales", "catalogue")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    first = next(ln["text"] for ln in p["manifest"]["lines"] if ln["id"] == "re_publishes")
    assert "zones left to Egeria" in first and "joins" not in first and "before any target" not in first
    assert p["manifest"]["zones"] == [] and p["manifest"]["zones_written"] is False


def test_the_database_row_shows_zones_set_by_egeria_when_the_read_back_has_them(world, fake, no_zone_config):
    fake.default_zones = ["egeria-runtime"]          # a build that DOES assign a default zone
    choose(world, "sales", "catalogue")
    press(world, fake)
    d = derived(world)
    assert "zones: egeria-runtime · set by Egeria" in d["header"]["text"]
    assert d["database"]["zones"] == ["egeria-runtime"] and d["database"]["zones_text"] == \
        "zones: egeria-runtime · set by Egeria"


def test_configured_zones_are_written_last_after_targets_survey_refresh_and_owner(world, fake):
    world["registry"].save_context("database", "db", {"enrichment": {"owner": {"value": "erin", "author": ME}}})
    choose(world, "sales", "catalogue")
    out, rec = press(world, fake, refresh=True)
    names = [c[0] for c in fake.calls]
    z = names.index("set_zone_membership")
    for earlier in ("publish_database", "set_owner", "create_schema_element", "initiate_catalog_action",
                    "initiate_survey", "refresh_connector"):
        assert max(i for i, n in enumerate(names) if n == earlier) < z, earlier
    assert zone_writes(fake)[0][2] == ("zone-a", "zone-b") and len(zone_writes(fake)) == 1
    assert step(rec, "zone_membership")["state"] == "done"
    order = [s["name"] for s in rec["steps"]]
    assert order.index("zone_membership") > order.index("survey") and order.index("zone_membership") > order.index("refresh")
    # with the rehearsal's lockout in force, every earlier step still succeeded
    for name in ("owner", "schema_targets", "survey", "refresh"):
        assert step(rec, name)["state"] not in ("failed", "skipped"), name
    assert fake.owners == {fake.db_guid: "erin"} and len(fake.targets) == 1 and len(fake.surveys) == 1
    d = derived(world)
    assert d["database"]["zones"] == ["zone-a", "zone-b"] and "set by RE" in d["database"]["zones_text"]


def test_the_fake_refuses_writes_after_a_zone_so_the_ordering_test_is_not_vacuous(world, fake):
    """Pins the rehearsal's behaviour in the fake: zone first, then owner/create/survey all refused."""
    choose(world, "sales", "catalogue")
    pub = fake.publish_database(entity(world), "u", "p")
    fake.set_zone_membership(pub.database_guid, ["zone-a"])
    with pytest.raises(gw.GatewayError, match="OPEN-METADATA-SECURITY-0011"):
        fake.set_owner(pub.database_guid, "erin")
    with pytest.raises(gw.GatewayError):
        fake.create_schema_element(entity(world), "sales", pub.database_guid)
    with pytest.raises(gw.GatewayError):
        fake.initiate_survey(pub.database_guid, {})


def test_a_zone_refusal_afterwards_is_egeria_s_word_and_is_not_retried(world, fake):
    fake.zones_ok = False
    choose(world, "sales", "catalogue")
    out, rec = press(world, fake)
    z = step(rec, "zone_membership")
    assert z["state"] == "failed" and "OPEN-METADATA-SECURITY-0011" in z["detail"]
    assert len(zone_writes(fake)) == 1                                # never retried blindly
    assert step(rec, "schema_targets")["state"] == "done" and len(fake.targets) == 1   # nothing was undone
    assert step(rec, "read_back")["state"] == "done"


def test_an_earlier_failure_means_no_zone_is_written_at_all(world, fake):
    fake.fail["initiate_survey"] = "500 survey engine is down"
    choose(world, "sales", "catalogue")
    out, rec = press(world, fake)
    assert zone_writes(fake) == []
    z = step(rec, "zone_membership")
    assert z["state"] == "skipped" and "earlier step failed" in z["detail"]


def test_publish_failure_skips_the_zone_step_with_its_reason(world, fake):
    fake.fail["publish_database"] = "500 Egeria is down"
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    assert step(rec, "zone_membership")["state"] == "skipped" and zone_writes(fake) == []


# ── D3: status words come from read-backs, never from initiation ──────────────

def test_the_survey_step_reads_submitted_with_a_time_not_done(world, fake):
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    s = step(rec, "survey")
    assert s["state"] == "submitted" and s["detail"].startswith("submitted · ")
    # the commit's own read-back looked (the survey was still running): initiation alone is not "done"
    assert len(fake.ops("survey_outcome")) == 1 and "running in Egeria · IN_PROGRESS" in s["detail"]
    assert rec["state"] != "failed"


def test_the_survey_step_becomes_done_only_when_a_read_back_shows_a_report_with_annotations(world, fake):
    choose(world, "sales", "catalogue")
    out, rec = press(world, fake)
    cid = out["curation"]["id"]
    fake.survey_behaviour = "empty"                                  # completed, report holds 0 annotations
    cc.read_back(world["registry"], fake, "db", ["sales"])
    s = step(Curations(world["registry"]).get(cid), "survey")
    assert s["state"] == "submitted" and "0 annotations" in s["detail"]
    fake.survey_behaviour = "pending"
    cc.read_back(world["registry"], fake, "db", ["sales"])
    assert step(Curations(world["registry"]).get(cid), "survey")["state"] == "submitted"
    fake.survey_behaviour = "annotated"
    cc.read_back(world["registry"], fake, "db", ["sales"])
    rec2 = Curations(world["registry"]).get(cid)
    s = step(rec2, "survey")
    assert s["state"] == "done" and "23 annotations" in s["detail"]
    assert rec2["state"] == rec["state"]                              # settling a step does not reopen the record


def test_a_failed_engine_action_reads_egeria_s_word_and_never_done(world, fake):
    choose(world, "sales", "catalogue")
    out, _ = press(world, fake)
    fake.survey_behaviour = "failed"
    cc.read_back(world["registry"], fake, "db", ["sales"])
    s = step(Curations(world["registry"]).get(out["curation"]["id"]), "survey")
    assert s["state"] == "failed" and "OMES-SURVEY-ACTION-0018" in s["detail"]


def test_a_settled_survey_is_not_read_again(world, fake):
    choose(world, "sales", "catalogue")
    press(world, fake)
    fake.survey_behaviour = "annotated"
    cc.read_back(world["registry"], fake, "db", ["sales"])
    n = len(fake.ops("survey_outcome"))
    cc.read_back(world["registry"], fake, "db", ["sales"])
    assert len(fake.ops("survey_outcome")) == n


def test_the_refresh_step_reads_refreshed_only_when_the_connector_time_moved(world, fake):
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=True)
    r = step(rec, "refresh")
    assert r["state"] == "done" and r["detail"].startswith("refreshed · connector time moved")


def test_the_refresh_step_says_requested_when_the_time_did_not_move(world, fake):
    fake.refresh_moves_time = False
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=True)
    r = step(rec, "refresh")
    assert r["state"] == "requested" and r["detail"].startswith("refresh requested")
    assert "refreshed" not in r["detail"].split(" · ")[0]


def test_the_refresh_step_says_requested_when_the_status_cannot_be_read(world, fake):
    fake.fail["connector_status"] = "500 daemon unreachable"
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=True)
    r = step(rec, "refresh")
    assert r["state"] == "requested" and "could not be read" in r["detail"]


# ── D4: a failed row reads Egeria's first sentence, the rest is under details ──

RAW = ("=> AUTHORIZATION_ERROR_401 => User not authorized received for user - ``. * Context: "
       "* class name=`BaseServerClient` * caller method=`_async_create_element_from_template` "
       "* Egeria error information: relatedHTTPCode=403")


def test_first_sentence_splits_egeria_s_message_from_its_context_block():
    first, rest = cc.egeria_first_sentence("GatewayError: " + RAW)
    assert first == "AUTHORIZATION_ERROR_401 => User not authorized received for user - ``."
    assert rest.startswith("* Context:") and "relatedHTTPCode=403" in rest
    assert cc.egeria_first_sentence("500 Egeria says no") == ("500 Egeria says no", "")
    assert cc.egeria_first_sentence("") == ("no reason recorded", "")


def test_a_failed_schema_row_shows_the_first_sentence_with_the_rest_as_details(world, fake):
    fake.fail["create_schema_element"] = RAW
    choose(world, "sales", "catalogue")
    press(world, fake)
    s = derived(world)["schemas"]["sales"]
    assert s["state"] == "failed"
    assert s["words"] == "failed · AUTHORIZATION_ERROR_401 => User not authorized received for user - ``."
    assert "Context" not in s["words"] and "Context" not in s["second"]
    assert "* Context:" in s["details"] and "BaseServerClient" in s["details"]


def test_a_failed_step_shows_the_first_sentence_with_the_rest_under_more_and_no_attachd(world, fake):
    fake.fail["create_schema_element"] = RAW
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake)
    st = step(rec, "schema_targets")
    assert st["state"] == "failed" and "* Context:" not in st["detail"] and "attachd" not in st["detail"]
    assert "AUTHORIZATION_ERROR_401" in st["detail"] and "* Context:" in st.get("more", "")
    assert st["detail"].startswith("0 of 1 attached")


def test_a_clean_run_says_attached_and_removed_in_full_words(world, fake):
    choose(world, "sales", "catalogue")
    _, rec = press(world, fake, refresh=False)
    assert step(rec, "schema_targets")["detail"].startswith("1 of 1 attached")
    choose(world, "sales", "leave_out")
    _, rec2 = press(world, fake)
    assert step(rec2, "leave_outs")["detail"].startswith("1 of 1 deleted in Egeria")
    assert "attachd" not in str(rec) + str(rec2) and "removed" not in step(rec2, "leave_outs")["detail"]


# ── D7: the server template gets its own description and a version ────────────

def _surveyor_calls():
    from resource_explorer.registry import DatabaseEntity
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
    ent = DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                         egeria_host="host.docker.internal", port=5442, database_name="shop",
                         description="the shop's own description")
    s = EgeriaDatabaseSurveyor(platform_url="http://x")
    s._automated_curation = MagicMock()
    # A real miss is pyegeria's "No elements found"; a bare MagicMock answer is not one (round 3: odd answers are
    # could-not-check, never "create").
    s._automated_curation.get_guid_for_name.return_value = "No elements found"
    s.connect = lambda: None
    # S19: no archived copy of the database, so a create is a plain create (a real 6.2 miss).
    from pyegeria import MetadataExpert
    from unittest.mock import create_autospec
    s._metadata_expert = create_autospec(MetadataExpert, spec_set=True, instance=True)
    expert_from_legacy(s._automated_curation, s._metadata_expert)
    s._metadata_expert.find_metadata_elements_with_string.return_value = "No elements found"
    s._find_element_guid = lambda name: ""
    s._save_database_secret = lambda *a, **k: ("coll", "/path")
    calls = []
    s._create_postgres_element_from_template = lambda tech, ph: (calls.append((tech, ph)) or f"guid-{len(calls)}")
    s._warn_if_database_has_no_connection = lambda *a, **k: None
    s._catalog_and_survey(ent, "u", "p", registry=None, survey_after_catalog=False)
    return dict(calls), ent


#: What the server template's placeholders are, as far as the rehearsal showed them.
SERVER_TEMPLATE = {"description": "~{description}~", "versionIdentifier": "~{versionIdentifier}~",
                   "serverName": "~{serverName}~", "hostIdentifier": "~{hostIdentifier}~",
                   "portNumber": "~{portNumber}~"}


def test_the_server_element_gets_its_own_description_and_a_version_so_no_placeholder_remains():
    calls, ent = _surveyor_calls()
    ph = calls["PostgreSQL Server"]
    assert ph["versionIdentifier"]
    assert ph["description"] and ph["description"] != ent.description     # the server is not the database
    assert ent.egeria_host in ph["description"] and str(ent.port) in ph["description"]
    # Egeria substitutes `~{key}~` from this map: nothing the template carries may stay unresolved
    for key in SERVER_TEMPLATE:
        assert key in ph and "~{" not in str(ph[key]), key
    assert "~{" not in str(ph)
    assert calls["PostgreSQL Relational Database"]["description"] == ent.description   # database unchanged
