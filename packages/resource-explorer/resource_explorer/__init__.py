"""Resource Explorer — multi-agent RAG reference implementation for GitHub projects."""

import os

# Guard against the ephemeral-Prefect-server leak found 2026-09-04 (13
# orphaned `prefect.server.api.server:create_app` subprocess servers, days
# old, reparented to launchd). Root cause: Prefect's own client starts an
# ephemeral in-process subprocess server whenever
# PREFECT_SERVER_EPHEMERAL_ENABLED is true (Prefect's shipped default
# profile sets it true) and no PREFECT_API_URL is reachable — and nothing
# shuts that subprocess down. Set here, at package import time, rather than
# only in surveyors/prefect_adapter.py, because more than one module under
# this package imports `prefect` directly (e.g. web/routes/prefect_status.py
# imports `prefect.client.orchestration` on its own, independent of
# prefect_adapter.py's import order) — this is the one place guaranteed to
# run before any of them. See PrefectConfig.enabled's docstring in config.py
# and prefect_adapter.py for the full explanation.
# setdefault() so an operator who deliberately wants ephemeral-server
# behavior can still override it via their own environment.
os.environ.setdefault("PREFECT_SERVER_EPHEMERAL_ENABLED", "false")

# Prefect dispatch honesty (2026-09-28,
# PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md). A SECOND,
# distinct instance of the same "ambient settings, not RE's config" bug the
# rest of that doc fixes — found live running the whole-definition Prefect
# path (`_run_via_prefect` -> `re_survey_definition_flow`, a real `@flow`
# invoked directly in-process): every one of RE's OWN Prefect REST calls now
# goes through `prefect_adapter.re_prefect_client()`, which passes RE's
# configured URL explicitly and does not depend on this env var at all — but
# Prefect's OWN flow/task engine, when a `@flow`/`@task` actually executes,
# constructs its OWN internal API client to record flow-run/task-run state,
# and that internal construction is Prefect's code, not RE's, so
# `re_prefect_client()` cannot reach it. It reads `PREFECT_API_URL` the same
# ambient way `get_client()` does. With no PREFECT_API_URL in the process
# environment, every whole-definition Prefect run degraded to
# "Prefect orchestration unavailable ... No Prefect API URL provided" and
# silently fell back to the local loop — despite `config.prefect.api_url`
# being correctly set the entire time. Setting the env var here, at package
# import (same reasoning as the guard above: more than one module imports
# `prefect` directly, and this is the one place guaranteed to run first)
# makes RE's own configured URL and Prefect's ambient settings agree, so
# BOTH the explicit-client path (`re_prefect_client`) and Prefect's own
# internal flow/task-engine client see the same server. setdefault() so an
# operator's own PREFECT_API_URL (e.g. pointing at a different environment)
# is never overridden.
try:
    from resource_explorer.config import get_config as _get_config

    os.environ.setdefault("PREFECT_API_URL", _get_config().prefect.api_url)
except Exception:  # pragma: no cover - defensive; must never block import
    pass

__version__ = "0.1.0"
