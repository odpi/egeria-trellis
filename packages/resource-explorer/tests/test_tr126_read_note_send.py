"""TR-126, Architect read-back: the "read for matching only" note must reach Curate's review proposal and the annotation Send
writes, not only the match statement: a confirmed match on an Egeria-profiled file otherwise goes to Egeria without saying
Resource Explorer read the values. Fakes only; the fixtures are the neighbouring Curate/Send tests'."""
from __future__ import annotations

import pytest

from resource_explorer import fs_review
from tests.fs_egeria_fake import FakeEgeria, install
from tests.library_step_install import install_steps  # noqa: F401
from tests.test_c3_fs_publish_and_curate import _class_run, _match, _walk_with_narratives
from tests.test_files_walk_extensions import _register, _tree, api, registry  # noqa: F401  (fixtures reused)

WORDS = "values read by Resource Explorer for matching only; Egeria profiled this file"
EGERIA_KEY = "lake/people.csv::email::DataClass::Email Address"
PLAIN_KEY = "hr/payroll.csv::email::DataClass::Email Address"


def _setup(registry, tmp_path):
    _walk_with_narratives(registry, tmp_path, [])
    _class_run(registry, [{**_match("lake/people.csv", "email"), "read_note": WORDS}, _match("hr/payroll.csv", "email")])


def test_the_review_proposal_carries_the_read_note(registry, tmp_path):
    _setup(registry, tmp_path)
    _run, proposals, _kept = fs_review.class_proposals(registry, "walkme")
    by = {p["file"]: p for p in proposals}
    assert by["lake/people.csv"]["read_note"] == WORDS
    assert by["hr/payroll.csv"]["read_note"] == ""            # RE's own file: nothing to say


@pytest.mark.usefixtures("signed_in_caller")
def test_send_says_in_the_annotation_that_resource_explorer_read_the_values(registry, tmp_path, monkeypatch):
    _setup(registry, tmp_path)
    for key in (EGERIA_KEY, PLAIN_KEY):
        fs_review.save_verdict(registry, "walkme", kind=fs_review.KIND_CLASS, key=key, verdict="confirm", author="dan")
    egeria = install(monkeypatch, FakeEgeria())
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor   # AFTER install

    run, confirmed = fs_review.confirmed_classes(registry, "walkme")
    EgeriaFileSystemSurveyor().publish_confirmed_data_classes(registry.get_filesystem("walkme"), run, confirmed, registry)
    import json

    props = {json.loads(p["body"]["properties"]["jsonProperties"])["file"]: p["body"]["properties"]
             for p in egeria.annotations.values()}
    got = {f: json.loads(v["jsonProperties"]) for f, v in props.items()}
    assert WORDS[:1].upper() + WORDS[1:] + "." in props["lake/people.csv"]["explanation"]
    assert got["lake/people.csv"]["readNote"] == WORDS
    assert WORDS not in props["hr/payroll.csv"]["explanation"] and "readNote" not in got["hr/payroll.csv"]
