""""🔔 Notify me" attached to a question row in /next's Questions-checklist
engine (re/next-automate-create-subscription).

Classic creates a subscription from a "🔔 Notify me" button on an Assessment/
Analysis CARD (index.html's `_createSubscriptionFromCard` /
"Cross-stage definitions") -- a card names exactly one analysis_id, so
classic's button fires the POST directly with no form. /next's Assessment
and Analysis stages are built (item 11) through the generic Questions-
checklist engine -- QUESTION ROWS, not a card grid -- and that card grid was
never going to get a /next equivalent (see stages/automate.js's own comment
block, corrected by this same change). So this attaches the action to the
question row instead: app.js's `provenanceLine()` grows a "🔔 notify me"
action wherever a row carries `analysis_ids`, wired (in `bindRowActions`) to
`openNotifyDialog()`, which opens a small dialog (worklist.js's
`openDialog()`) rather than firing directly, because a question's
`analysis_ids` is not always 1:1 with one analysis (a MIXED:/PARTIAL: answer
can name several) -- unlike a card, which always names exactly one.

No browser verification happened for THIS file's assertions with a signed-in
session -- those were checked live against the running dev server (see the
session's final report). These tests grep/slice function bodies out of the
concatenated source, the established pattern for /next JS modules without a
browser -- see test_next_automate_pane.py / test_next_discovery_import_search.py's
identical `_app()` helper, reproduced here.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


def _reapi_src():
    return (NEXT.parent / "re-api.js").read_text(encoding="utf-8")


def _notify_dialog_body():
    app = _app()
    start = app.index("async function openNotifyDialog(")
    end = app.index("async function toggleMeasurementsInPlace(", start)
    return app[start:end]


class TestReApiCreateSubscriptionWrapper:
    """re-api.js gets a real wrapper, matching listSubscriptions'/
    setSubscriptionActive's existing shape and hitting the same route
    classic's `_createSubscriptionFromCard` posts to."""

    def test_wrapper_exists_and_posts_to_the_real_route(self):
        api = _reapi_src()
        assert "export const createSubscription = (" in api
        body = api[api.index("export const createSubscription"):]
        body = body[:body.index(";", body.index("post("))]
        assert "post('/api/automate/subscriptions'" in body

    def test_wrapper_sends_the_same_four_fields_the_backend_model_declares(self):
        # web/routes/automate.py's CreateSubscriptionRequest: entity_type,
        # entity_slug, analysis_id, label.
        api = _reapi_src()
        body = api[api.index("export const createSubscription"):]
        body = body[:body.index(";", body.index("post("))]
        assert "entity_type:" in body
        assert "entity_slug:" in body
        assert "analysis_id:" in body
        assert "label" in body


class TestNotifyActionAttachedToQuestionRows:
    """The actual attachment point this task had to find: a question row,
    not a card (there is no card grid in /next for Assessment/Analysis)."""

    def test_provenance_line_offers_notify_me_when_the_row_names_an_analysis(self):
        app = _app()
        fn = app[app.index("function provenanceLine("):app.index("function updateAnsweredCount(")]
        assert "data-notify=" in fn
        assert "notify me" in fn.lower()
        # Gated on analysis_ids, not on a specific answered state -- a
        # subscription is a standing watch, not tied to today's answer.
        assert "entry.analysis_ids || []).length" in fn

    def test_it_does_not_offer_notify_while_the_row_is_mid_run(self):
        app = _app()
        fn = app[app.index("function provenanceLine("):app.index("function updateAnsweredCount(")]
        notify_guard = fn[fn.index("data-notify=") - 400:fn.index("data-notify=")]
        assert "st !== 'running'" in notify_guard

    def test_bind_row_actions_wires_the_click_to_open_notify_dialog(self):
        app = _app()
        fn = app[app.index("function bindRowActions("):app.index("async function openNotifyDialog(")]
        assert "data-notify" in fn
        assert "openNotifyDialog(entry)" in fn


class TestNotifyDialogCallsTheRealEndpoint:
    def test_it_reuses_the_shared_dialog_shell(self):
        body = _notify_dialog_body()
        assert "openDialog('🔔 Notify me'" in body

    def test_submit_calls_the_real_create_subscription_wrapper(self):
        body = _notify_dialog_body()
        assert "await createSubscription(" in body

    def test_entity_type_is_translated_not_hardcoded_to_repo(self):
        # The repo-only Questions-engine gate this dialog used to sit behind
        # (loadPane()'s old "Repos only, in /next" branch) is gone as of the
        # database/filesystem generalization -- a question row can now be
        # open on any of the three resource types. Found live 2026-09-28:
        # this dialog still hardcoded 'repo' in the createSubscription() call
        # after that gate lifted, so subscribing from a database's own notify
        # dialog 404'd ("Repo 'laz_local_adventureworks' not found") against
        # the server's repo-only lookup. It must send the real, translated
        # entity_type instead.
        fn = _notify_dialog_body()
        assert "const entityType = apiEntityType(state.resourceType);" in fn
        call = fn[fn.index("await createSubscription("):]
        call = call[:call.index(")") + 1]
        assert "entityType" in call
        assert "'repo'" not in call

    def test_a_failed_create_shows_the_error_and_re_enables_the_button(self):
        body = _notify_dialog_body()
        catch_block = body[body.index("} catch (err) {", body.index("await createSubscription(")):]
        catch_block = catch_block[:catch_block.index("}\n  });")]
        assert "btn.disabled = false" in catch_block
        assert "errEl.textContent = err.message" in catch_block

    def test_success_closes_the_dialog_so_automate_shows_it_on_next_visit(self):
        # Automate's own renderSubscriptions() re-fetches listSubscriptions()
        # on every render rather than caching (see stages/automate.js) -- so
        # there is nothing to push into from here; closing the dialog is
        # enough for the new row to appear the next time that pane renders.
        body = _notify_dialog_body()
        submit = body[body.index("#notify-submit').addEventListener"):]
        try_block = submit[submit.index("try {"):submit.index("} catch")]
        assert "closeCellDetail()" in try_block


class TestNonOneToOneQuestionToAnalysisIsHandledHonestly:
    """The real finding from investigating the Questions engine: a
    question's `analysis_ids` is not always length 1 (MIXED:/PARTIAL:
    answers can name several analyses). A subscription watches ONE analysis,
    so silently picking analysis_ids[0] -- the convention `rerun`/
    `openRunChoice` already use elsewhere in this file for re-running --
    would risk subscribing to the wrong one. This must never happen here."""

    def test_multiple_analysis_ids_render_a_picker_not_a_silent_default(self):
        body = _notify_dialog_body()
        assert "ids.length > 1" in body
        assert 'name="notify-analysis"' in body
        # It must actually let the reader choose -- radio inputs, not just a
        # list of names with no way to select one.
        assert 'type="radio"' in body

    def test_submit_reads_the_checked_radio_when_there_is_a_choice(self):
        # Submit and the inline schedule action share one `currentAnalysisId()`
        # helper (added alongside the inline schedule action) rather than each
        # re-reading the picker separately -- assert on the shared helper
        # itself, and that submit actually calls it.
        body = _notify_dialog_body()
        helper = body[body.index("const currentAnalysisId = () =>"):]
        helper = helper[:helper.index(";\n")]
        assert 'notify-analysis"]:checked' in helper
        submit = body[body.index("#notify-submit').addEventListener"):]
        assert "currentAnalysisId()" in submit

    def test_a_single_analysis_id_skips_the_picker_but_still_confirms_which_one(self):
        body = _notify_dialog_body()
        single_branch = body[body.index(": `<input type=\"hidden\""):body.index(
            "<label class=\"mb-[3px] block text-caveat text-ink-muted\">Label")]
        assert "notify-analysis-only" in single_branch

    def test_it_never_reaches_for_analysis_ids_zero_the_way_rerun_does(self):
        # rerun()/openRunChoice() elsewhere in app.js legitimately pick
        # analysis_ids[0] (re-running is idempotent and safe to under-target).
        # openNotifyDialog must not borrow that shortcut for a standing watch
        # -- it only ever reads `ids[0]` as the DEFAULT for a single-id row
        # (already known to be the only choice), never to skip a real pick
        # among several.
        body = _notify_dialog_body()
        assert "entry.analysis_ids || [])[0]" not in body


class TestReApiSaveScheduleAndGetSchedulesWrappers:
    """re-api.js gets the two wrappers the inline schedule action needs --
    matching listAllSchedules'/deleteSchedule'/runScheduleNow's existing
    shape and hitting the same routes classic's saveSchedule()/the Schedules
    editor already post/read."""

    def test_get_schedules_reads_the_per_resource_route(self):
        api = _reapi_src()
        assert "export const getSchedules = (" in api
        body = api[api.index("export const getSchedules"):]
        body = body[:body.index(";", body.index("get("))]
        assert "get(`/api/schedules/" in body

    def test_save_schedule_posts_to_the_same_route_the_per_card_action_uses(self):
        api = _reapi_src()
        assert "export const saveSchedule = (" in api
        body = api[api.index("export const saveSchedule"):]
        body = body[:body.index(";", body.index("post("))]
        assert "post(`/api/schedules/" in body
        assert "analysis_id:" in body
        assert "schedule" in body
        assert "enabled" in body


class TestNotifyDialogInlineScheduleAction:
    """Item 3 of the notify-me-entity-type fix: the dialog's own "set a
    Schedule ... or this never fires" line used to send the reader to a
    different stage (Automate) to do anything about it. The schedule action
    is now inline, in the same dialog, and reuses the real schedules route
    rather than firing a new one -- same pattern as chat's own inline
    scheduling form (`_chatSubmitSchedule()`, index.html), which reuses
    saveSchedule() rather than inventing a second scheduling code path."""

    def test_the_dialog_offers_every_cadence_inline(self):
        # PI-099: manual and monthly joined daily and weekly.
        body = _notify_dialog_body()
        assert 'id="notify-schedule-cadence"' in body
        for cadence in ("manual", "daily", "weekly", "monthly"):
            assert f'<option value="{cadence}"' in body

    def test_it_calls_the_real_save_schedule_wrapper_with_the_translated_entity_type(self):
        body = _notify_dialog_body()
        handler = body[body.index("#notify-schedule-save').addEventListener"):]
        handler = handler[:handler.index("});")]
        assert "await saveSchedule(entityType, slug, analysisId, cadence, true)" in handler
        assert "'repo'" not in handler

    def test_it_reads_back_what_was_actually_stored_rather_than_trusting_the_post_body(self):
        # The whole point: the button must not just fire-and-forget. Reading
        # back via getSchedules() shows the reader the cadence/next_run the
        # SERVER actually stored, not merely the value the client selected.
        body = _notify_dialog_body()
        handler = body[body.index("#notify-schedule-save').addEventListener"):]
        handler = handler[:handler.index("});")]
        assert "await getSchedules(entityType, slug)" in handler
        assert "saved?.next_run" in handler
        assert "statusEl.textContent" in handler

    def test_a_failed_schedule_save_shows_the_error_not_a_silent_no_op(self):
        body = _notify_dialog_body()
        handler = body[body.index("#notify-schedule-save').addEventListener"):]
        handler = handler[:handler.index("});")]
        assert "catch (err) {" in handler
        assert "could not schedule" in handler

    def test_the_schedule_action_targets_whichever_analysis_is_currently_chosen(self):
        # Not always ids[0] -- for a multi-analysis row, the schedule button
        # must follow the same picker the Subscribe button reads, via the
        # shared currentAnalysisId() helper, not a separate/stale selection.
        body = _notify_dialog_body()
        assert "const currentAnalysisId = () =>" in body
        handler = body[body.index("#notify-schedule-save').addEventListener"):]
        handler = handler[:handler.index("});")]
        assert "currentAnalysisId()" in handler


class TestNotifyDialogDeliveryHonesty:
    """Item 4: the dialog must state BOTH halves of how delivery actually
    works, verified against the code rather than assumed -- change detection
    and the RFA write are local (scheduler.py's _check_subscriptions ->
    activity_logger.log_rfa); pushing that RFA to Egeria as a ToDo is a
    SEPARATE, asynchronous step (rfa_egeria_sync.sync_rfa_action, driven by
    scheduler.py's own background loop) that is NOT gated on this resource's
    publish state -- confirmed by reading rfa_egeria_sync.py in full: the
    ToDo it creates carries no element_guid/resource linkage at all."""

    def test_it_states_detection_and_the_local_rfa_write_are_local(self):
        body = _notify_dialog_body()
        assert "Detection runs locally" in body
        assert "scheduled" in body

    def test_it_states_the_separate_asynchronous_egeria_todo_push(self):
        body = _notify_dialog_body()
        assert "background loop" in body
        assert "Egeria ToDo" in body

    def test_it_does_not_claim_egeria_delivery_depends_on_publish_state(self):
        # The dialog must not say "only once published" -- that claim does
        # not match rfa_egeria_sync.py (verified 2026-09-28: sync_rfa_action
        # calls MyProfile.create_my_todo() unconditionally, with no
        # element_guid tying the ToDo to the resource, and no publish check
        # anywhere in the reconciliation pass). It says the opposite,
        # honestly: this happens regardless of publish state.
        body = _notify_dialog_body()
        assert "whatever this" in body and "publish state" in body
        assert "only when published" not in body
        assert "not published, so local only" not in body
