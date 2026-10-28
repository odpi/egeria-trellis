"""pyegeria 6.2.2 audit (docs/design-notes/AUDIT-PYEGERIA-6.2.2.md §1): a None from a pyegeria read is
"could not read", never "none there".

Up to 6.2.1, `@dynamic_catch` with PYEGERIA_ENABLE_LOGGER_CATCH on turned every failure into None (ISSUE-134).
No read RE calls returns None for a miss (a miss is the "No elements found" string), so each place below that
used to read None as absent now says it could not read. Each test also pins the real miss, which stays a miss.
"""
import pytest

from resource_explorer import catalogue_gateway as gw

_DUPE = ("OMAG-COMMON-409-001 The unique identifier (qualifiedName) of Annotation::x::y::1 "
         "is not available for use")


# ── catalogue gateway ────────────────────────────────────────────────────

@pytest.mark.parametrize("parse", [gw.parse_element_answer, gw.parse_elements_answer, gw.parse_related_answer])
def test_gateway_reads_none_as_unreadable(parse):
    with pytest.raises(gw.GatewayError, match="unreadable, not absent"):
        parse(None)


def test_gateway_still_reads_the_miss_string_as_a_miss():
    assert gw.parse_element_answer("No elements found") is None
    assert gw.parse_elements_answer("No elements found") == []
    assert gw.parse_related_answer("No elements found") == []


# ── resync ───────────────────────────────────────────────────────────────

def test_resync_resolves_none_as_could_not_determine():
    from resource_explorer.egeria_resync import EgeriaResync

    assert EgeriaResync._resolves(lambda g: None, "g") is None          # was False ("gone")
    assert EgeriaResync._resolves(lambda g: "No elements found", "g") is False
    assert EgeriaResync._resolves(lambda g: {"elementHeader": {"guid": "g"}}, "g") is True


# ── outbox adoption after a duplicate-name rejection ─────────────────────

def _row():
    return {"id": 1, "egeria_guid": "", "qualified_name": "Annotation::x::y::1",
            "payload_json": "{}", "element_kind": "annotation"}


def test_outbox_a_failed_look_again_is_not_cannot_be_found(monkeypatch):
    from resource_explorer import egeria_outbox as ob

    monkeypatch.setitem(ob._CREATORS, "annotation",
                        lambda clients, payload: (_ for _ in ()).throw(RuntimeError(_DUPE)))
    calls = []

    def find(qn):
        calls.append(qn)
        if len(calls) == 1:
            return ""
        raise TimeoutError("view server did not answer")

    with pytest.raises(ob.OutboxApplyError, match="look-up for it then failed") as info:
        ob.apply_element(_row(), None, find)
    assert "invisible to our lookup" not in str(info.value)
    assert "TimeoutError" in str(info.value)


# ── by-name lookups that guard a create ──────────────────────────────────

def test_publisher_lookup_none_raises_not_create():
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    pub = EgeriaPublisher.__new__(EgeriaPublisher)

    class AC:
        def __init__(self, answer):
            self.answer = answer

        def get_guid_for_name(self, qn):
            return self.answer

    pub._automated_curation = AC(None)
    with pytest.raises(RuntimeError, match="no answer"):
        pub._find_element_guid("Q::n")
    pub._automated_curation = AC("No elements found")
    assert pub._find_element_guid("Q::n") == ""


def test_database_surveyor_lookup_none_raises():
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
    with pytest.raises(RuntimeError, match="no answer"):
        s._guid_from_lookup(None)
    assert s._guid_from_lookup("No elements found") == ""


# ── native survey: the report read ───────────────────────────────────────

def test_report_guid_for_action_none_is_not_no_report_yet():
    from resource_explorer import native_survey_run as nsr

    port = nsr.PyegeriaSurveyPort.__new__(nsr.PyegeriaSurveyPort)
    port._loop = None

    class Expert:
        def __init__(self, answer):
            self.answer = answer

        def get_related_metadata_elements(self, guid, rel, body=None, starting_at_end=0, **kw):
            return self.answer

    port._enter = lambda: None
    port._get_expert = lambda: Expert(None)
    with pytest.raises(nsr.NativeSurveyError, match="no answer"):
        port.report_guid_for_action("ea-1")
    port._get_expert = lambda: Expert("No elements found")
    assert port.report_guid_for_action("ea-1") is None
