"""A filesystem survey is evidence whether or not Egeria hears about it.

`_run_filesystem_inventory` used to walk the filesystem, return the inventory,
and persist nothing. The only `add_filesystem_survey` call on the Survey
Definition path lived inside `_publish_step_annotations`, which raises when the
filesystem has no Egeria asset — and `SurveyDefinitionExecutor` catches that
into `errors`. So a run that did real work discarded it, left
`last_surveyed_at` untouched, and gave the next run nothing to compare against.

The local route has always persisted unconditionally, and the database adapter
does the same inside `DatabaseSurveyor.survey()`. Filesystem was the only path
where the write was conditional on cataloguing succeeding.
"""
from __future__ import annotations

import pytest

from resource_explorer import file_reach
from resource_explorer.surveyors.filesystem.survey_definition_adapter import (
    _run_filesystem_inventory,
)


#: Resource Explorer leads (the walk runs); the state is handed in, so this fake registry needs no probe rows.
_RE_LEADS = file_reach.ForkState(state=file_reach.RE_ONLY, surveyor=file_reach.SURVEYOR_RE)


class _Entity:
    slug = "fs-under-test"
    root_path = "/tmp"


class _Registry:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def add_filesystem_survey(self, **kw):
        if self.fail:
            raise RuntimeError("registry unavailable")
        self.calls.append(kw)


@pytest.fixture
def surveyor(monkeypatch):
    import resource_explorer.surveyors.filesystem.local_filesystem_surveyor as mod

    class _Fake:
        def __init__(self, entity, registry):
            pass

        def run(self, *, profile_contents=True, walk_extensions=False):
            # Files step 2: the folder survey's walk opens no data-file contents. Files build step 1: it also measures the
            # extensions (owner, mode, hash under the limit).
            assert profile_contents is False and walk_extensions is True
            return {"surveyed_at": "2026-08-25T00:00:00", "file_count": 42}

    monkeypatch.setattr(mod, "LocalFileSystemSurveyor", _Fake)
    return _Fake


def test_the_inventory_is_persisted_without_needing_egeria(surveyor):
    """The whole point: no Egeria asset, no publish, and the survey still lands."""
    reg = _Registry()
    out = _run_filesystem_inventory(_Entity(), reg, fork=_RE_LEADS)

    assert out["survey_data"]["file_count"] == 42
    assert out["persisted"] is True
    assert len(reg.calls) == 1
    assert reg.calls[0]["fs_slug"] == "fs-under-test"
    assert reg.calls[0]["surveyed_at"] == "2026-08-25T00:00:00"


def test_a_persistence_failure_does_not_also_discard_the_inventory(surveyor):
    """Two ways to lose the work, and fixing one must not introduce the other.

    If the write fails, the freshly-walked data must still reach the caller —
    and the failure must be visible rather than inferred from a missing row.
    """
    reg = _Registry(fail=True)
    out = _run_filesystem_inventory(_Entity(), reg, fork=_RE_LEADS)

    assert out["survey_data"]["file_count"] == 42, "inventory was discarded on a write failure"
    assert out["persisted"] is False
    assert "persist_error" in out and "RuntimeError" in out["persist_error"]


def test_source_marks_which_path_wrote_it(surveyor):
    """The local route writes source='local'; this path must be distinguishable,
    so a stored survey can be traced to how it was produced."""
    reg = _Registry()
    _run_filesystem_inventory(_Entity(), reg, fork=_RE_LEADS)
    assert reg.calls[0]["source"] == "survey-definition"
