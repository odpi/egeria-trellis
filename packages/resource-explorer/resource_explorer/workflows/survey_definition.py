"""Running an authored Survey Definition, off any web framework.

Unlike the other three workflows, this one was never really web-only: the CLI
and `web/routes/survey_definitions.py` already called the same core
`run_survey_definition` (plan §3 names that as the evidence the core is
separable). What lived only in the route was the small amount *around* it — the
portal-URL decoration, and the "write the terminal status back onto the activity
entry" wrapper the frontend's poll depends on.

That wrapper is what the run queue needs, so it moves here rather than being
reimplemented: a queued Survey Definition run must leave exactly the same
activity row a route-spawned thread left, or the run modal reads a different
shape depending on which process happened to execute it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class SurveyDefinitionRunParams:
    """The run's inputs, as a plain dataclass rather than the route's pydantic
    body — so the queue can round-trip them through the `runs.target` JSON
    column and hand them back to the same function."""

    survey_definition_ref: str = ""
    refresh_definition: bool = False
    db_user: str = ""
    db_pwd: str = ""
    #: The per-run choice ("wait" | "background" | None) — see
    #: SurveyDefinitionExecutor.run's `publish` parameter.
    publish: str | None = None
    #: The per-run engine choice ("resource-explorer" | "prefect" | None) —
    #: see SurveyDefinitionExecutor.run's `engine_override` parameter. Named
    #: `engine_override` here (rather than `engine`, as the route's own
    #: request body spells it) so it round-trips through the run queue's JSON
    #: `target` column under the same name the executor itself uses.
    engine_override: str | None = None
    #: "this run" when `db_user`/`db_pwd` are a credential override for this
    #: one run (parity G2, PI-016). A run carrying one is NEVER put on the run
    #: queue (the queue persists its payload in the registry) — see
    #: `web/routes/survey_definitions.py`. "" for every other run.
    credential_scope: str = ""
    #: True = "do not try Egeria first": an egeria-adaptive step runs its
    #: local scan only (parity G2, PI-018). False leaves every step as it was.
    force_custom: bool = False
    #: Engine host case 3, decision 5: who coordinates the run. RE's own Run is "re" (RE's executor runs every step
    #: and creates no engine action); "coordinated by Egeria" never comes through here. `started_by_person` is who
    #: pressed Run. Both land in the run's activity detail, which Understanding reads ("started in RE by <person>").
    started_by: str = "re"          # engine_host.STARTED_BY_RE
    started_by_person: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> SurveyDefinitionRunParams:
        return cls(**{k: v for k, v in (data or {}).items()
                      if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return {
            "survey_definition_ref": self.survey_definition_ref,
            "refresh_definition": self.refresh_definition,
            "db_user": self.db_user,
            "db_pwd": self.db_pwd,
            "publish": self.publish,
            "engine_override": self.engine_override,
            "credential_scope": self.credential_scope,
            "force_custom": self.force_custom,
            "started_by": self.started_by,
            "started_by_person": self.started_by_person,
        }


@dataclass
class SurveyDefinitionRunResult:
    status: str  # "ok" | "error"
    summary: str = ""
    errors: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)


def run_definition(entity_type: str, slug: str, params: SurveyDefinitionRunParams,
                   *, registry=None) -> dict:
    """Execute one Survey Definition and return the executor's own result dict.

    Raises SurveyDefinitionExecutorError / SurveyDefinitionReaderError on a
    reader/executor-level failure — the caller decides how to surface that (an
    HTTP status for the route, an errored activity entry and a failed `runs`
    row for the queue).
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.survey_definition_executor import (
        run_survey_definition,
    )

    registry = registry or ProjectRegistry()
    extra = {"force_custom": True} if params.force_custom else {}
    result = run_survey_definition(
        entity_type, slug, registry=registry,
        survey_definition_ref=params.survey_definition_ref,
        refresh_definition=params.refresh_definition,
        db_user=params.db_user,
        db_pwd=params.db_pwd,
        publish=params.publish,
        engine_override=params.engine_override,
        credential_scope=params.credential_scope,
        **extra,
    )

    report_guid = result.get("egeria_report_guid", "")
    if report_guid:
        from resource_explorer.config import get_config

        portal_url = get_config().egeria.portal_url.rstrip("/")
        result["egeria_portal_report_url"] = f"{portal_url}/tech-catalog?guid={report_guid}"
    return result


def execute_and_record_definition(entity_type: str, slug: str,
                                  params: SurveyDefinitionRunParams,
                                  activity_id: str, *, registry=None
                                  ) -> SurveyDefinitionRunResult:
    """Run a Survey Definition and write its terminal status onto `activity_id`.

    The full structured result (steps report, egeria_report_guid, portal URL,
    errors — everything the run modal renders) is JSON-encoded into that same
    entry's `detail`. No separate result store, and — unlike an in-memory dict
    — it survives a restart like any other activity entry.

    `survey_definition_ref` is embedded in `detail` on every terminal path. The
    running row's `summary` does carry it, but this call always overwrites
    `summary`, so by the time anything queries a completed run the ref only
    survives if it is also in `detail` — which is what
    registry.get_survey_definition_last_activity() scans for.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
    )
    from resource_explorer.surveyors.survey_definition_reader import (
        SurveyDefinitionReaderError,
    )

    registry = registry or ProjectRegistry()
    ref = params.survey_definition_ref
    secret = params.db_pwd if params.credential_scope else ""
    ran_as = ({"user": params.db_user, "scope": params.credential_scope}
              if params.credential_scope and params.db_user else None)

    def _detail(body: dict) -> str:
        # An override's password never reaches a row: scrubbed from anything
        # stored, and the row says who the run was as (never with what).
        from resource_explorer.secret_redaction import scrub

        if ran_as:
            body = {**body, "ran_as": ran_as}
        # Case 3: where the run was started and by whom, read back by get_survey_definition_last_activity.
        body = {**body, "started_by": params.started_by, "started_by_person": params.started_by_person}
        return json.dumps(scrub(body, secret))

    def _say(text: str) -> str:
        from resource_explorer.secret_redaction import scrub

        return scrub(text, secret)

    try:
        result = run_definition(entity_type, slug, params)
    except (SurveyDefinitionExecutorError, SurveyDefinitionReaderError) as exc:
        msg = _say(str(exc))
        registry.update_activity_status(
            activity_id, "error", summary=msg,
            detail=_detail({"errors": [msg], "survey_definition_ref": ref}),
        )
        return SurveyDefinitionRunResult(status="error", summary=msg, errors=[msg])
    except Exception as exc:  # pragma: no cover — genuinely unexpected
        msg = _say(str(exc))
        if secret:
            # No traceback: its frames and message are the override's to leak.
            log.error("Survey Definition run crashed for %s/%s: %s", entity_type, slug, msg)
        else:
            log.exception("Survey Definition run crashed for %s/%s", entity_type, slug)
        registry.update_activity_status(
            activity_id, "error", summary=f"Survey Definition run crashed: {msg}",
            detail=_detail({"errors": [msg], "survey_definition_ref": ref}),
        )
        return SurveyDefinitionRunResult(
            status="error", summary=f"Survey Definition run crashed: {msg}", errors=[msg],
        )

    errors = result.get("errors") or []
    summary = (f"Completed with {len(errors)} error(s)" if errors
               else "Survey Definition run complete")
    result["survey_definition_ref"] = ref
    registry.update_activity_status(
        activity_id, "error" if errors else "ok", summary=summary,
        detail=_detail(result),
    )
    from resource_explorer.secret_redaction import scrub

    return SurveyDefinitionRunResult(
        status="error" if errors else "ok", summary=summary,
        errors=scrub(errors, secret), result=scrub(result, secret),
    )


def start_in_process(target, *args, name: str) -> None:
    """Run `target(*args)` on a daemon thread in THIS process.

    The one place a Survey Definition run is allowed off the run queue: a run
    carrying a credential typed for that run only (parity G2, PI-016). The queue
    persists its payload in the registry; an override must not be, so it runs
    where it was received and dies with the process. Everything else goes
    through `registry.enqueue_run`.
    """
    import contextvars
    import threading

    # Run under a COPY of the caller's context, not a bare thread: the signed-in
    # caller lives in a ContextVar and a plain Thread starts with an empty one,
    # so the run's Egeria publish and private-zone reads would be attributed to
    # '' (the shared bucket). Same mechanism as the chat turn in
    # web/routes/query.py (found 2026-09-08).
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(target, *args), name=name, daemon=True).start()
