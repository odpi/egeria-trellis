"""Tests for the Dr.Egeria bootstrap (resource_explorer/bootstrap.py).

Weighted toward the failure modes actually hit live on 2026-08-19 rather than
the happy path: a present batch must never be re-run (re-running the survey
definitions duplicated 22 step links and took them out of service), an
unreachable Egeria must not look like a reset, and a canary that can never
resolve must not drive an infinite heal loop.
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from resource_explorer import bootstrap as bs


def _write_batch(root, name, *, canary=None, files=("a.md",), extra=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = {"display_name": name.title(), "files": list(files)}
    if canary is not None:
        manifest["canary"] = canary
    manifest.update(extra or {})
    (d / bs.BATCH_MANIFEST_FILE).write_text(json.dumps(manifest))
    for f in files:
        (d / f).write_text(f"# {f}\n")
    return d


@pytest.fixture(autouse=True)
def _clear_state():
    with bs._status_lock:
        bs._status.clear()
    yield
    with bs._status_lock:
        bs._status.clear()


class TestDiscovery:
    def test_folder_order_drives_execution_order(self, tmp_path):
        for n in ("charlie", "alpha", "bravo"):
            _write_batch(tmp_path, n, canary={"qualified_name": f"Q::{n}"})
        (tmp_path / bs.FOLDER_ORDER_FILE).write_text(json.dumps({"folders": ["charlie", "alpha"]}))
        assert [b.batch_id for b in bs.discover_batches(tmp_path)] == ["charlie", "alpha", "bravo"]

    def test_bare_array_folder_order_also_parses(self, tmp_path):
        """The Portal's _folder_order.json is a bare array; RE's is an object so
        it can carry comments. Both must work so a Portal-style file drops in."""
        for n in ("alpha", "bravo"):
            _write_batch(tmp_path, n, canary={"qualified_name": f"Q::{n}"})
        (tmp_path / bs.FOLDER_ORDER_FILE).write_text(json.dumps(["bravo", "alpha"]))
        assert [b.batch_id for b in bs.discover_batches(tmp_path)] == ["bravo", "alpha"]

    def test_folder_without_manifest_is_not_a_batch(self, tmp_path):
        """Dropping a directory into docs/dr-egeria/ must not silently make it
        execute against Egeria."""
        _write_batch(tmp_path, "real", canary={"qualified_name": "Q::real"})
        (tmp_path / "notabatch").mkdir()
        (tmp_path / "notabatch" / "stray.md").write_text("# stray")
        assert [b.batch_id for b in bs.discover_batches(tmp_path)] == ["real"]

    def test_unlisted_files_run_after_declared_ones(self, tmp_path):
        d = _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"}, files=("second.md", "first.md"))
        (d / "zz_extra.md").write_text("# extra")
        batch = bs.discover_batches(tmp_path)[0]
        assert batch.files == ["second.md", "first.md", "zz_extra.md"]

    def test_declared_file_that_does_not_exist_is_dropped(self, tmp_path):
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"},
                     files=("real.md",), extra={"files": ["ghost.md", "real.md"]})
        (tmp_path / "b" / "real.md").write_text("# real")
        assert bs.discover_batches(tmp_path)[0].files == ["real.md"]

    def test_unreadable_manifest_skips_batch_without_raising(self, tmp_path):
        _write_batch(tmp_path, "good", canary={"qualified_name": "Q::good"})
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / bs.BATCH_MANIFEST_FILE).write_text("{not json")
        assert [b.batch_id for b in bs.discover_batches(tmp_path)] == ["good"]


class _FakeExpert:
    """Mimics MetadataExpert.get_metadata_element_by_unique_name: the element when held, pyegeria's
    "No elements found" sentence when not, a raise when Egeria cannot be asked."""

    def __init__(self, present=(), raises=False):
        self._present = set(present)
        self._raises = raises
        self.calls = []

    def get_metadata_element_by_unique_name(self, name=None, property_name="qualifiedName", body=None, **kw):
        self.calls.append((name, property_name, kw))
        if self._raises:
            raise RuntimeError("egeria unreachable")
        if name in self._present:
            return {"elementGUID": "6a3014a3-de4d-4909-8dd0-511dae0e08b2", "type": {"typeName": "Referenceable"}}
        return "No elements found"


class TestCanary:
    def test_present_canary_detected(self, tmp_path):
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        batch = bs.discover_batches(tmp_path)[0]
        assert bs.canary_present(batch, expert=_FakeExpert(present=["Q::b"])) is True

    def test_missing_canary_detected(self, tmp_path):
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        batch = bs.discover_batches(tmp_path)[0]
        assert bs.canary_present(batch, expert=_FakeExpert()) is False

    def test_sentinel_string_is_not_mistaken_for_a_guid(self, tmp_path):
        """pyegeria returns "No elements found" — truthy. If that were taken as
        a GUID the batch would read as present and never heal."""
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        batch = bs.discover_batches(tmp_path)[0]
        assert bs.canary_present(batch, expert=_FakeExpert(present=[])) is False

    def test_lookup_error_returns_unknown_not_present(self, tmp_path):
        """An unreachable Egeria is not evidence of a reset (so: no heal), but it
        is also not evidence of health. Returning True here is what let a wiped
        catalog report present=True across the board after a redeploy."""
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        batch = bs.discover_batches(tmp_path)[0]
        assert bs.canary_present(batch, expert=_FakeExpert(raises=True)) is None

    def test_no_canary_means_never_auto_healed(self, tmp_path):
        _write_batch(tmp_path, "b", canary=None)
        batch = bs.discover_batches(tmp_path)[0]
        assert batch.has_canary is False
        assert bs.canary_present(batch) is True

    def test_a_qualified_name_canary_is_the_unique_name_read_with_no_type_invented(self, tmp_path):
        """A wrong type_name would read as 'missing' and trigger a heal, and the unique-name read takes no type:
        a declared type is not asked, qualifiedName alone is global."""
        _write_batch(tmp_path, "b", canary={"type": "Perspective", "qualified_name": "Q::b"})
        expert = _FakeExpert(present=["Q::b"])
        assert bs.canary_present(bs.discover_batches(tmp_path)[0], expert=expert) is True
        assert expert.calls == [("Q::b", "qualifiedName", {})]

    def test_display_name_canary_uses_display_name_property(self, tmp_path):
        """Glossary-term qualified names are generated with a deployment-specific
        org prefix and version, so those batches key on display name instead: a typed
        GlossaryTerm string search, exact, kept to the displayName property."""
        from unittest.mock import create_autospec

        from pyegeria import MetadataExpert

        _write_batch(tmp_path, "b", canary={"display_name": "Some Question?"})
        expert = create_autospec(MetadataExpert, instance=True, spec_set=True)
        expert.find_metadata_elements_with_string.return_value = [{
            "elementGUID": "6a3014a3-de4d-4909-8dd0-511dae0e08b2",
            "type": {"typeName": "GlossaryTerm", "superTypeNames": ["Referenceable"]},
            "elementProperties": {"propertiesAsStrings": {"displayName": "Some Question?"}}}]
        assert bs.canary_present(bs.discover_batches(tmp_path)[0], expert=expert) is True
        kw = expert.find_metadata_elements_with_string.call_args.kwargs
        assert kw["search_string"] == "Some Question?" and kw["metadata_element_type"] == "GlossaryTerm"


class TestCheckAndHeal:
    def test_present_batch_is_never_re_run(self, tmp_path, monkeypatch):
        """THE critical guarantee. Re-running an already-present survey-definition
        batch duplicates every step link and takes it out of service."""
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: True)
        called = []
        monkeypatch.setattr(bs, "heal_batch", lambda b: called.append(b) or (True, "ok"))

        res = bs.check_and_heal(tmp_path)
        assert called == []
        assert res["batches"]["b"]["action"] == "ok"

    def test_missing_batch_is_healed(self, tmp_path, monkeypatch):
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: False)
        called = []
        monkeypatch.setattr(bs, "heal_batch", lambda b: called.append(b.batch_id) or (True, "ok"))

        res = bs.check_and_heal(tmp_path)
        assert called == ["b"]
        assert res["batches"]["b"]["action"] == "healed"

    def test_heals_in_declared_order(self, tmp_path, monkeypatch):
        """Order is load-bearing: a survey definition processed before the
        Question terms exist silently creates no ScopedBy links."""
        for n in ("foundations", "questions", "survey-definitions"):
            _write_batch(tmp_path, n, canary={"qualified_name": f"Q::{n}"})
        (tmp_path / bs.FOLDER_ORDER_FILE).write_text(
            json.dumps({"folders": ["foundations", "questions", "survey-definitions"]}))
        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: False)
        order = []
        monkeypatch.setattr(bs, "heal_batch", lambda b: order.append(b.batch_id) or (True, "ok"))

        bs.check_and_heal(tmp_path)
        assert order == ["foundations", "questions", "survey-definitions"]

    def test_repeated_failures_stop_retrying(self, tmp_path, monkeypatch):
        """A canary that can never resolve would otherwise re-heal forever. Two
        real Question terms behave exactly this way, so the guard is not
        hypothetical."""
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::never"})
        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: False)
        attempts = []
        monkeypatch.setattr(bs, "heal_batch", lambda b: attempts.append(1) or (False, "boom"))

        for _ in range(bs.MAX_CONSECUTIVE_FAILURES + 3):
            bs.check_and_heal(tmp_path)
        assert len(attempts) == bs.MAX_CONSECUTIVE_FAILURES

    def test_a_heal_that_succeeds_but_never_resolves_still_stops_retrying(self, tmp_path, monkeypatch):
        """The gap found live 2026-09-07: heal_batch() reporting ok=True (dr_egeria
        exited zero) is a different question from the canary actually being
        present afterward. Confirmed live on a deployment where
        foundations/survey-definitions/governance-metrics re-healed every ~10
        minutes for 6+ hours straight — heal_batch succeeded every cycle, so a
        guard keyed on that never fired, even though the post-heal recheck
        found the canary missing every single time. Each cycle fully re-ran
        every document in the (idempotent=false) batch, relying entirely on
        post_heal dedup to clean up the resulting duplicate links.

        Keying the counter on the verified recheck instead — as opposed to
        heal_batch's exit status — is what actually stops this."""
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::never"})
        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: False)
        attempts = []
        monkeypatch.setattr(bs, "heal_batch", lambda b: attempts.append(1) or (True, "ok"))

        for _ in range(bs.MAX_CONSECUTIVE_FAILURES + 3):
            bs.check_and_heal(tmp_path)
        assert len(attempts) == bs.MAX_CONSECUTIVE_FAILURES

    def test_unreachable_egeria_does_not_heal(self, tmp_path, monkeypatch):
        """The regression that matters most: None is falsy, so a bare
        `if present:` would have healed during an outage — re-running every
        batch, including the non-idempotent one, against a server that is
        merely restarting."""
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: None)
        called = []
        monkeypatch.setattr(bs, "heal_batch", lambda b: called.append(b.batch_id) or (True, "ok"))

        res = bs.check_and_heal(tmp_path)
        assert called == []
        assert res["batches"]["b"]["action"] == "unknown"

    def test_unreachable_check_does_not_overwrite_last_verified_answer(self, tmp_path, monkeypatch):
        """A stale-but-real answer beats a guess. Overwriting `present` on an
        unreachable check is how the status came to claim health it had never
        verified."""
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        monkeypatch.setattr(bs, "heal_batch", lambda b: (True, "ok"))

        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: True)
        bs.check_and_heal(tmp_path)
        assert bs.get_status(tmp_path)["batches"]["b"]["present"] is True
        verified_at = bs.get_status(tmp_path)["batches"]["b"]["last_verified_at"]
        assert verified_at

        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: None)
        bs.check_and_heal(tmp_path)
        st = bs.get_status(tmp_path)["batches"]["b"]
        assert st["present"] is True                       # retained, not guessed
        assert st["reachable"] is False                    # but flagged as unverified
        assert st["last_verified_at"] == verified_at       # staleness is visible
        assert bs.get_status(tmp_path)["egeria_reachable"] is False

    def test_reachable_check_clears_a_previous_unreachable_flag(self, tmp_path, monkeypatch):
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        monkeypatch.setattr(bs, "heal_batch", lambda b: (True, "ok"))
        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: None)
        bs.check_and_heal(tmp_path)
        assert bs.get_status(tmp_path)["batches"]["b"]["reachable"] is False

        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: True)
        bs.check_and_heal(tmp_path)
        st = bs.get_status(tmp_path)["batches"]["b"]
        assert st["reachable"] is True and st["present"] is True

    def test_force_reruns_even_when_present(self, tmp_path, monkeypatch):
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        monkeypatch.setattr(bs, "canary_present", lambda b, client=None: True)
        called = []
        monkeypatch.setattr(bs, "heal_batch", lambda b: called.append(b.batch_id) or (True, "ok"))

        bs.check_and_heal(tmp_path, force=True)
        assert called == ["b"]


class TestHealBatch:
    def test_stops_at_first_failing_document(self, tmp_path, monkeypatch):
        """Within a batch, file order encodes dependency order — continuing past
        a failure runs commands whose prerequisites are missing."""
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"}, files=("one.md", "two.md", "three.md"))
        batch = bs.discover_batches(tmp_path)[0]
        seen = []

        def fake(doc):
            seen.append(doc.name)
            return (False, "bad") if doc.name == "two.md" else (True, "ok")

        monkeypatch.setattr(bs, "_run_dr_egeria", fake)
        ok, detail = bs.heal_batch(batch)
        assert ok is False
        assert seen == ["one.md", "two.md"]
        assert "two.md" in detail

    def test_post_heal_runs_after_documents(self, tmp_path, monkeypatch):
        """Survey definitions need the reconciler after a heal: a legitimate
        heal still duplicates step links."""
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"},
                     extra={"post_heal": {"script": "scripts/reconcile.py"}})
        batch = bs.discover_batches(tmp_path)[0]
        monkeypatch.setattr(bs, "_run_dr_egeria", lambda doc: (True, "ok"))
        ran = []
        monkeypatch.setattr(bs, "_run_post_heal", lambda b: ran.append(b.batch_id) or (True, "post_heal ok"))
        ok, _ = bs.heal_batch(batch)
        assert ok is True and ran == ["b"]

    def test_post_heal_failure_fails_the_batch(self, tmp_path, monkeypatch):
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"},
                     extra={"post_heal": {"script": "scripts/reconcile.py"}})
        batch = bs.discover_batches(tmp_path)[0]
        monkeypatch.setattr(bs, "_run_dr_egeria", lambda doc: (True, "ok"))
        monkeypatch.setattr(bs, "_run_post_heal", lambda b: (False, "reconciler exploded"))
        ok, detail = bs.heal_batch(batch)
        assert ok is False and "reconciler" in detail

    def test_post_heal_checks_run_after_post_heal(self, tmp_path, monkeypatch):
        """post_heal_checks (e.g. the scope-link reconciler's report mode)
        run after post_heal, on top of it."""
        _write_batch(
            tmp_path, "b", canary={"qualified_name": "Q::b"},
            extra={
                "post_heal": {"script": "scripts/reconcile.py"},
                "post_heal_checks": [{"script": "scripts/reconcile_scopes.py"}],
            },
        )
        batch = bs.discover_batches(tmp_path)[0]
        assert batch.post_heal_checks == [{"script": "scripts/reconcile_scopes.py"}]
        monkeypatch.setattr(bs, "_run_dr_egeria", lambda doc: (True, "ok"))
        monkeypatch.setattr(bs, "_run_post_heal", lambda b: (True, "post_heal ok"))
        ran = []
        monkeypatch.setattr(
            bs, "_run_post_heal_checks",
            lambda b: ran.append(b.batch_id) or ["scripts/reconcile_scopes.py: reconciled"],
        )
        ok, detail = bs.heal_batch(batch)
        assert ok is True
        assert ran == ["b"]
        assert "reconciled" in detail

    def test_post_heal_check_drift_does_not_fail_the_batch(self, tmp_path, monkeypatch):
        """A check finding drift (non-zero exit) is a report, not a heal
        failure — it never writes to Egeria on this path, so it must not be
        conflated with post_heal actually erroring."""
        _write_batch(
            tmp_path, "b", canary={"qualified_name": "Q::b"},
            extra={
                "post_heal": {"script": "scripts/reconcile.py"},
                "post_heal_checks": [{"script": "scripts/reconcile_scopes.py"}],
            },
        )
        batch = bs.discover_batches(tmp_path)[0]
        monkeypatch.setattr(bs, "_run_dr_egeria", lambda doc: (True, "ok"))
        monkeypatch.setattr(bs, "_run_post_heal", lambda b: (True, "post_heal ok"))

        def fake_check(_batch):
            class _Proc:
                returncode = 1
                stdout = "missing: some question\n"
                stderr = ""

            return [f"scripts/reconcile_scopes.py: drift found (exit 1) — {_Proc.stdout.strip()}"]

        monkeypatch.setattr(bs, "_run_post_heal_checks", fake_check)
        ok, detail = bs.heal_batch(batch)
        assert ok is True
        assert "drift found" in detail

    def test_batch_with_no_post_heal_checks_defaults_to_empty(self, tmp_path):
        _write_batch(tmp_path, "b", canary={"qualified_name": "Q::b"})
        batch = bs.discover_batches(tmp_path)[0]
        assert batch.post_heal_checks == []


class TestRealManifests:
    """The shipped docs/dr-egeria manifests must stay parseable and coherent —
    a broken manifest here disables healing silently."""

    def test_real_manifests_discover_in_dependency_order(self):
        batches = bs.discover_batches(bs.DOCS_DIR)
        ids = [b.batch_id for b in batches]
        assert ids[:3] == ["foundations", "questions", "survey-definitions"]

    def test_every_real_batch_has_a_usable_canary_and_files(self):
        for b in bs.discover_batches(bs.DOCS_DIR):
            assert b.has_canary, f"{b.batch_id} has no canary — would never self-heal"
            assert b.files, f"{b.batch_id} has no documents"

    def test_survey_definitions_declares_non_idempotent_and_post_heal(self):
        sd = {b.batch_id: b for b in bs.discover_batches(bs.DOCS_DIR)}["survey-definitions"]
        assert sd.idempotent is False
        assert (sd.post_heal or {}).get("script")

    def test_survey_definitions_declares_the_scope_reconciler_check(self):
        """docs/Backlog.md, 'Superseded Question term': the step-edge
        reconciler above can't see ScopedBy drift, so the scope reconciler's
        report mode must be wired in alongside it, or a heal comes back with
        the same 2026-08-19/2026-09-13 class of gap unreported."""
        sd = {b.batch_id: b for b in bs.discover_batches(bs.DOCS_DIR)}["survey-definitions"]
        scripts = [c.get("script") for c in sd.post_heal_checks]
        assert "scripts/reconcile_survey_definition_scopes.py" in scripts


class TestRunDrEgeriaRunsTheChildOnThisInterpreter:
    """A bootstrap auto-heal runs `python -m resource_explorer.dr_egeria_child` on the interpreter RE itself runs on
    (never a `dr_egeria` from PATH: a pipx copy on a different Python died at bearer-token creation, 2026-09-12),
    and puts no secret on argv."""

    def test_uses_sys_executable_and_no_secret_on_argv(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "executable", "/venv/bin/python")
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"], captured["env"] = argv, kwargs.get("env") or {}
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(bs.subprocess, "run", fake_run)
        doc = tmp_path / "one.md"
        doc.write_text("# one\n")
        ok, _detail = bs._run_dr_egeria(doc)

        assert ok is True
        assert captured["argv"][:3] == ["/venv/bin/python", "-m", "resource_explorer.dr_egeria_child"]
        from resource_explorer.config import get_config

        assert get_config().egeria.user_password not in captured["argv"]
        assert captured["env"]["RE_DR_EGERIA_CHILD_PASSWORD"] == get_config().egeria.user_password

