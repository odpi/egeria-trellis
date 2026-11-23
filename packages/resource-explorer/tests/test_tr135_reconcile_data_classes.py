"""TR-135: reconcile RE's seed-owned DataClasses.

DataClasses RE seeded before TR-127 carry the column-name keywords as `dataPatterns` and no `specification`; the
bootstrap never updates a class it finds. `reconcile_data_classes` lists, per seed class, the seed-owned fields that
differ from Egeria (dry run, the default) and with apply=True updates only those fields: matchPropertyNames, specification and the
requiresNameMatch / valueCheck keys of specificationDetails (never displayName or description). Never a delete, never a
class the seed does not name, other specificationDetails keys are kept, and a second apply changes nothing and says so.
Emptying a list or removing a key needs a replace update (Egeria's merge update does not send an empty list or map): that is
reported as "needs a replace update; not applied" and never sent.

A fake Egeria holds each class's properties, answers the unique-name read and the class read, and applies a
merge update the way Egeria does (only the properties sent change).
"""
from __future__ import annotations

import uuid

import pytest

from resource_explorer.surveyors.database import bootstrap_data_classes as bdc

NO_ELEMENTS_FOUND = "No elements found"


def _g(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


class _Egeria:
    """One object that is both the MetadataExpert and the DataDesigner of a fake repository."""

    def __init__(self):
        self.classes: dict[str, dict] = {}        # qualifiedName -> properties
        self.reads: list[str] = []
        self.updates: list[tuple[str, dict]] = []
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.fail_read_for: set[str] = set()
        self.ignore_updates = False

    def add(self, qualified_name: str, **props):
        self.classes[qualified_name] = {"class": "DataClassProperties", "qualifiedName": qualified_name, **props}

    # MetadataExpert
    def get_metadata_element_by_unique_name(self, name=None, property_name="qualifiedName", body=None, **kwargs):
        if name not in self.classes:
            return NO_ELEMENTS_FOUND
        return {"elementGUID": _g(name), "type": {"typeName": "DataClass", "superTypeNames": ["Referenceable"]}}

    # DataDesigner
    def get_data_class_by_guid(self, guid=None, output_format="JSON", **kwargs):
        for qn, props in self.classes.items():
            if _g(qn) == guid:
                self.reads.append(qn)
                if qn in self.fail_read_for:
                    raise RuntimeError("Egeria is not answering")
                return [{"elementHeader": {"guid": guid}, "properties": dict(props)}]
        return NO_ELEMENTS_FOUND

    def update_data_value_specification(self, guid, body):
        """Egeria's merge update (6.4 PropertyHelper): a property is replaced as a whole; a null string is skipped, and an
        empty list or an empty map is skipped too (the old value stays); "" is sent."""
        qn = next(q for q in self.classes if _g(q) == guid)
        self.updates.append((qn, body))
        if not self.ignore_updates:
            assert body["mergeUpdate"] is True
            for k, v in body["properties"].items():
                if k == "class" or v is None or v == [] or v == {}:
                    continue
                self.classes[qn][k] = v

    def create_data_class(self, body):
        self.created.append(body)

    def delete_data_class(self, *args, **kwargs):
        self.deleted.append(str(args))


def _seed():
    return bdc.load_data_class_seed()


def _by_name(name):
    return next(dc for dc in _seed() if dc["name"] == name)


def _old_shape(egeria, name):
    """What the pre-TR-127 bootstrap created: the keywords as dataPatterns, no matchPropertyNames, no specification."""
    dc = _by_name(name)
    egeria.add(bdc.data_class_qn(name), displayName=dc["displayName"], description=dc["description"],
               dataPatterns=list(dc["dataPatterns"]))


def _seed_shape(egeria, name):
    dc = _by_name(name)
    egeria.add(bdc.data_class_qn(name), displayName=dc["displayName"], description=dc["description"],
               matchPropertyNames=list(dc["dataPatterns"]),
               **({"specification": dc["specification"]} if dc.get("specification") else {}),
               **({"specificationDetails": bdc._spec_details(dc)} if bdc._spec_details(dc) else {}))


def _reconcile(egeria, apply=False):
    return bdc.reconcile_data_classes(egeria, egeria, apply=apply)


def test_an_old_shape_class_gives_a_diff_and_a_dry_run_changes_nothing():
    e = _Egeria()
    _old_shape(e, "EmailAddress")
    before = {k: dict(v) for k, v in e.classes.items()}
    report = _reconcile(e)
    [one] = [c for c in report.classes if c.name == "EmailAddress"]
    assert one.status == bdc.DIFFERS
    assert set(one.changes) == {"matchPropertyNames", "specification"}
    assert one.changes["matchPropertyNames"][1] == _by_name("EmailAddress")["dataPatterns"]
    assert one.changes["specification"][1] == _by_name("EmailAddress")["specification"]
    assert set(one.not_applied) == {"dataPatterns"}           # the old keywords: emptying a list is not sent
    assert e.updates == [] and e.classes == before          # dry run: nothing written
    assert "EmailAddress" in report.text() and "--apply" in report.text()


def test_apply_gives_the_seed_shape_and_only_the_differing_fields_are_sent():
    e = _Egeria()
    _old_shape(e, "EmailAddress")
    report = _reconcile(e, apply=True)
    [(qn, body)] = e.updates
    assert qn == bdc.data_class_qn("EmailAddress")
    assert body["class"] == "UpdateElementRequestBody" and body["mergeUpdate"] is True
    assert set(body["properties"]) == {"class", "matchPropertyNames", "specification"}
    props = e.classes[qn]
    assert props["matchPropertyNames"] == _by_name("EmailAddress")["dataPatterns"]
    assert props["specification"] == _by_name("EmailAddress")["specification"]
    assert props["displayName"] == _by_name("EmailAddress")["displayName"]      # untouched fields stay
    assert [c.status for c in report.classes if c.name == "EmailAddress"] == [bdc.UPDATED]


def test_the_markers_are_diffed_and_written_into_specification_details():
    e = _Egeria()
    dc = _by_name("CreditCardNumber")
    e.add(bdc.data_class_qn("CreditCardNumber"), displayName=dc["displayName"], description=dc["description"],
          matchPropertyNames=list(dc["dataPatterns"]), specification=dc["specification"])      # TR-127 shape, no markers
    [one] = [c for c in _reconcile(e).classes if c.name == "CreditCardNumber"]
    assert set(one.changes) == {"specificationDetails.requiresNameMatch", "specificationDetails.valueCheck"}
    assert one.changes["specificationDetails.valueCheck"] == (None, "luhn")
    _reconcile(e, apply=True)
    assert e.classes[bdc.data_class_qn("CreditCardNumber")]["specificationDetails"] == {
        "requiresNameMatch": "true", "valueCheck": "luhn"}


def test_other_keys_in_specification_details_are_kept():
    e = _Egeria()
    dc = _by_name("CreditCardNumber")
    e.add(bdc.data_class_qn("CreditCardNumber"), displayName=dc["displayName"], description=dc["description"],
          matchPropertyNames=list(dc["dataPatterns"]), specification=dc["specification"],
          specificationDetails={"someoneElsesKey": "keep me", "valueCheck": "old"})
    _reconcile(e, apply=True)
    [(_, body)] = e.updates
    assert body["properties"]["specificationDetails"] == {
        "someoneElsesKey": "keep me", "valueCheck": "luhn", "requiresNameMatch": "true"}
    assert e.classes[bdc.data_class_qn("CreditCardNumber")]["specificationDetails"]["someoneElsesKey"] == "keep me"


def test_a_display_name_or_description_edited_in_egeria_is_not_touched_or_listed():
    e = _Egeria()
    _seed_shape(e, "EmailAddress")
    e.classes[bdc.data_class_qn("EmailAddress")]["displayName"] = "Mail, as our team calls it"
    e.classes[bdc.data_class_qn("EmailAddress")]["description"] = "edited by a person"
    report = _reconcile(e, apply=True)
    assert e.updates == []
    assert {c.name: c.status for c in report.classes}["EmailAddress"] == bdc.IN_STEP
    assert "displayName" not in report.text() and "description" not in report.text()


def test_emptying_a_list_or_removing_a_key_is_reported_and_never_applied():
    e = _Egeria()
    dc = _by_name("EmailAddress")        # the seed wants no dataPatterns and no markers
    e.add(bdc.data_class_qn("EmailAddress"), displayName=dc["displayName"], description=dc["description"],
          matchPropertyNames=list(dc["dataPatterns"]), specification=dc["specification"],
          dataPatterns=["old_keyword"], specificationDetails={"requiresNameMatch": "true", "other": "x"})
    for apply in (False, True):
        report = _reconcile(e, apply=apply)
        [one] = [c for c in report.classes if c.name == "EmailAddress"]
        assert set(one.not_applied) == {"dataPatterns", "specificationDetails.requiresNameMatch"}
        assert one.changes == {}
        assert report.text().count("needs a replace update; not applied") >= 2
    assert e.updates == []                                        # nothing was sent
    assert e.classes[bdc.data_class_qn("EmailAddress")]["dataPatterns"] == ["old_keyword"]
    assert e.classes[bdc.data_class_qn("EmailAddress")]["specificationDetails"] == {"requiresNameMatch": "true", "other": "x"}


def test_a_blank_specification_is_a_difference_that_is_sent_and_read_back_correctly():
    e = _Egeria()
    dc = _by_name("PhoneNumber")       # the seed has a specification here
    e.add(bdc.data_class_qn("PhoneNumber"), displayName=dc["displayName"], description=dc["description"],
          matchPropertyNames=list(dc["dataPatterns"]), specification="", specificationDetails=bdc._spec_details(dc))
    report = _reconcile(e, apply=True)
    assert [c.status for c in report.classes if c.name == "PhoneNumber"] == [bdc.UPDATED]
    # and a seed that wants "" (no specification) against a stored non-blank one is sent as "" (a string is not skipped)
    e2 = _Egeria()
    pw = _by_name("Password")
    assert not pw.get("specification")
    e2.add(bdc.data_class_qn("Password"), displayName=pw["displayName"], description=pw["description"],
           matchPropertyNames=list(pw["dataPatterns"]), specification="stale")
    report2 = _reconcile(e2, apply=True)
    assert e2.updates[0][1]["properties"]["specification"] == ""
    assert e2.classes[bdc.data_class_qn("Password")]["specification"] == ""
    assert [c.status for c in report2.classes if c.name == "Password"] == [bdc.UPDATED]


def test_a_second_apply_changes_nothing_and_says_so():
    e = _Egeria()
    _old_shape(e, "EmailAddress")
    _reconcile(e, apply=True)
    e.updates.clear()
    report = _reconcile(e, apply=True)
    assert e.updates == []
    assert all(c.status in (bdc.IN_STEP, bdc.ABSENT, bdc.NEEDS_REPLACE) for c in report.classes)
    assert "no changes" in report.text().lower()


def test_a_class_already_in_seed_shape_is_in_step():
    e = _Egeria()
    for name in ("EmailAddress", "PhoneNumber", "CreditCardNumber"):
        _seed_shape(e, name)
    report = _reconcile(e, apply=True)
    assert e.updates == []
    assert {c.name: c.status for c in report.classes if c.name in ("EmailAddress", "PhoneNumber", "CreditCardNumber")} == {
        "EmailAddress": bdc.IN_STEP, "PhoneNumber": bdc.IN_STEP, "CreditCardNumber": bdc.IN_STEP}


def test_a_class_the_seed_does_not_name_is_never_read_or_touched():
    e = _Egeria()
    _old_shape(e, "EmailAddress")
    e.add("DataClass::SomeoneElsesClass", displayName="Other", dataPatterns=["x"], specification="x+")
    other_before = dict(e.classes["DataClass::SomeoneElsesClass"])
    _reconcile(e, apply=True)
    assert e.classes["DataClass::SomeoneElsesClass"] == other_before
    assert "DataClass::SomeoneElsesClass" not in e.reads and all(q != "DataClass::SomeoneElsesClass" for q, _ in e.updates)


def test_a_seed_class_not_in_egeria_is_reported_not_created_and_nothing_is_deleted():
    e = _Egeria()
    report = _reconcile(e, apply=True)
    assert e.created == [] and e.deleted == [] and e.updates == []
    assert all(c.status == bdc.ABSENT for c in report.classes)


def test_a_class_that_cannot_be_read_is_reported_and_not_updated_and_the_others_go_on():
    e = _Egeria()
    _old_shape(e, "EmailAddress")
    _old_shape(e, "PhoneNumber")
    e.fail_read_for.add(bdc.data_class_qn("EmailAddress"))
    report = _reconcile(e, apply=True)
    status = {c.name: c for c in report.classes}
    assert status["EmailAddress"].status == bdc.COULD_NOT_CHECK and "not answering" in status["EmailAddress"].reason
    assert all(q != bdc.data_class_qn("EmailAddress") for q, _ in e.updates)
    assert status["PhoneNumber"].status == bdc.UPDATED
    assert not report.ok


def test_an_update_that_does_not_take_is_said_so_not_reported_as_done():
    e = _Egeria()
    _old_shape(e, "EmailAddress")
    e.ignore_updates = True
    report = _reconcile(e, apply=True)
    [one] = [c for c in report.classes if c.name == "EmailAddress"]
    assert one.status == bdc.DID_NOT_TAKE and one.changes
    assert not report.ok


def test_none_and_blank_and_list_order_are_not_differences():
    e = _Egeria()
    dc = _by_name("Password")
    e.add(bdc.data_class_qn("Password"), displayName=dc["displayName"], description=dc["description"],
          matchPropertyNames=list(reversed(dc["dataPatterns"])), specification=None, dataPatterns=None,
          specificationDetails=None)
    [one] = [c for c in _reconcile(e).classes if c.name == "Password"]
    assert one.status == bdc.IN_STEP and one.changes == {} and one.not_applied == {}


def test_the_manual_run_is_a_dry_run_unless_apply_is_given(monkeypatch, capsys):
    e = _Egeria()
    _old_shape(e, "EmailAddress")
    monkeypatch.setattr(bdc, "_clients", lambda identity, purpose: (e, None, e))
    assert bdc.reconcile_command(apply=False) == 0
    assert e.updates == [] and "dry run" in capsys.readouterr().out.lower()
    assert bdc.reconcile_command(apply=True) == 0
    assert len(e.updates) == 1


# ── W-135-1: matchPropertyNames is a union, never a replacement ──

def _email_with(egeria, keywords):
    dc = _by_name("EmailAddress")
    egeria.add(bdc.data_class_qn("EmailAddress"), displayName=dc["displayName"], description=dc["description"],
               matchPropertyNames=list(keywords), specification=dc["specification"])
    return dc


def test_keywords_someone_added_in_egeria_are_kept_and_the_seeds_missing_ones_are_appended():
    e = _Egeria()
    dc = _email_with(e, ["email", "email_address", "Email", "emailaddress"])      # hand-updated on laz, 2026-11-22
    report = _reconcile(e, apply=True)
    [(qn, body)] = e.updates
    sent = body["properties"]["matchPropertyNames"]
    assert sent[:4] == ["email", "email_address", "Email", "emailaddress"]          # Egeria's order, none dropped
    assert sent[4:] == [k for k in dc["dataPatterns"] if k not in ("email", "email_address", "Email", "emailaddress")]
    assert set(sent) == {"email", "email_address", "Email", "emailaddress", *dc["dataPatterns"]}
    assert e.classes[qn]["matchPropertyNames"] == sent
    assert set(body["properties"]) == {"class", "matchPropertyNames"}               # nothing else differed


def test_the_dry_run_reports_only_the_additions():
    e = _Egeria()
    _email_with(e, ["email", "email_address", "Email", "emailaddress"])
    report = _reconcile(e)
    [one] = [c for c in report.classes if c.name == "EmailAddress"]
    assert "+ ['mail_addr', 'emailaddr']" in one.line()
    assert "Email'" not in one.line().split("+")[1]           # the extra keywords are not listed as changes
    assert e.updates == []


def test_a_list_that_already_covers_the_seed_is_no_changes_even_with_extra_keywords():
    e = _Egeria()
    dc = _by_name("EmailAddress")
    _email_with(e, [*dc["dataPatterns"], "Email", "extra_one"])
    report = _reconcile(e, apply=True)
    assert e.updates == []
    assert {c.name: c.status for c in report.classes}["EmailAddress"] == bdc.IN_STEP
    assert "no changes" in report.text().lower()


def test_an_empty_list_gets_the_whole_seed_in_seed_order():
    e = _Egeria()
    dc = _email_with(e, [])
    _reconcile(e, apply=True)
    assert e.classes[bdc.data_class_qn("EmailAddress")]["matchPropertyNames"] == dc["dataPatterns"]


def test_a_second_apply_after_the_union_sends_nothing():
    e = _Egeria()
    _email_with(e, ["email", "Email"])
    _reconcile(e, apply=True)
    e.updates.clear()
    _reconcile(e, apply=True)
    assert e.updates == []
