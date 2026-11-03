"""One shared answer to "is this element absent?" (PR/CI review of 02f0b8c6).

Absent only on Egeria's own no-elements answer, a not-found exception, or an API exception that carries
related_http_code 404. Unauthorised, transport and everything else are UNREADABLE: never a reason to create.
"""
from __future__ import annotations

import pytest

from resource_explorer.egeria_absence import ABSENT, PRESENT, UNREADABLE, is_absent


def _exc(cls, **attrs):
    e = Exception.__new__(cls)          # these classes need an HTTP response to construct
    Exception.__init__(e, "boom")
    for k, v in attrs.items():
        setattr(e, k, v)
    return e


def test_the_no_elements_answer_is_absent():
    assert is_absent("No elements found") == ABSENT


def test_other_text_is_unreadable_not_absent():
    assert is_absent("something went wrong") == UNREADABLE


def test_a_populated_answer_is_present():
    assert is_absent({"elementHeader": {"guid": "x"}}) == PRESENT
    assert is_absent([{"guid": "x"}]) == PRESENT


def test_nothing_or_empty_is_unreadable_never_a_guess():
    assert is_absent(None) == UNREADABLE
    assert is_absent({}) == UNREADABLE
    assert is_absent([]) == UNREADABLE


def test_a_not_found_exception_is_absent():
    from pyegeria.core._exceptions import PyegeriaNotFoundException
    assert is_absent(_exc(PyegeriaNotFoundException)) == ABSENT


def test_an_api_exception_with_related_code_404_is_absent():
    from pyegeria.core._exceptions import PyegeriaAPIException
    assert is_absent(_exc(PyegeriaAPIException, related_http_code=404)) == ABSENT


def test_an_api_exception_with_another_code_is_unreadable():
    from pyegeria.core._exceptions import PyegeriaAPIException
    assert is_absent(_exc(PyegeriaAPIException, related_http_code=500)) == UNREADABLE
    assert is_absent(_exc(PyegeriaAPIException, related_http_code=None)) == UNREADABLE


@pytest.mark.parametrize("name", ["PyegeriaUnauthorizedException", "PyegeriaClientException",
                                  "PyegeriaConnectionException", "PyegeriaTimeoutException",
                                  "PyegeriaInvalidParameterException", "PyegeriaUnknownException"])
def test_unauthorised_and_transport_failures_are_unreadable(name):
    import pyegeria.core._exceptions as ex
    assert is_absent(_exc(getattr(ex, name))) == UNREADABLE


def test_unauthorised_is_unreadable_even_though_it_subclasses_the_api_exception():
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException
    assert is_absent(_exc(PyegeriaUnauthorizedException, related_http_code=404)) == UNREADABLE


def test_a_guid_containing_404_in_a_timeout_message_is_unreadable():
    assert is_absent(TimeoutError("timeout reading 11404404-0000-0000-0000-000000000000 (504)")) == UNREADABLE


def test_not_found_words_in_a_plain_exception_are_not_absence():
    assert is_absent(RuntimeError("user erinoverview not found")) == UNREADABLE
    assert is_absent(RuntimeError("No elements found")) == UNREADABLE     # only the returned answer counts
