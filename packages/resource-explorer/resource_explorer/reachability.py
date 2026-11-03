"""Filesystem reachability probe — Phase 1 slice #13
(COORDINATOR-BRIEF-MULTI-RESOURCE.md, egeria-support-for-multi-resource.md
§5/§9/§10).

Deferred by the project owner on 2026-09-21 ("defer the resource_
reachability table until further tests -- do not build it yet"); un-deferred
by the project owner on 2026-09-22, who asked for this slice to proceed
now. See `RESOURCE-REACHABILITY-IMPLEMENTED.md` for the
full write-up, including the live probe 7/8 results this module's mapping
is built from -- not guessed at.

**Scope: filesystem/folder resources only.** Probe 7 confirmed the
mechanism (`AutomatedCuration.initiate_gov_action_type("FileSurvey::
survey-folder", ..., request_parameters={"finalAnalysisStep": "CHECK_ASSET"})`
against a folder Asset) but ALSO surfaced a real, previously-unknown gap:
`EgeriaFileSystemSurveyor.catalog_and_survey`'s own `create_folder_element_
from_template()` call (the FileFolder template) attaches NO Connection to
the folder Asset it creates -- confirmed live, not inferred from the docs'
existing "no attached Connection subgraph" comment. Every filesystem RE has
ever cataloged this way will report `no_connection` from this check, not a
genuine reachability answer, until something gives the Asset a real
Connection (out of scope for this slice -- see the design notes doc's
"Not built" section). Database reachability was not probed here at all: the
design doc's own gap analysis (§5) is specifically about the folder-survey
CHECK_ASSET mechanism, and `survey-postgres-database` is a different
governance action type with a different failure shape (secrets-store
resolution, per PROBES-2026-09-21.md's probe 9 write-up) -- extending this
module to databases is future work, not assumed to work identically.

Live-verified request/response shapes (2026-09-22, scratch elements
cleaned up, `PROBES-2026-09-21.md`'s "Probes 7 and 8, run live" section):

  * No Connection at all on the folder Asset -> terminal status `INVALID`,
    completion message starting `OPEN-SURVEY-0009 ... has no connection,
    so there is no way to reach the resource`. Fast (~6s including the
    initial connect/poll overhead).
  * A Connection present but pointing at a path the *engine host's own*
    filesystem cannot see (e.g. a macOS host path when the engine host is
    a Docker container) -> terminal status `FAILED`, completion message
    `OMES-SURVEY-ACTION-0018 ... FileException ... BASIC-FILE-CONNECTOR-
    404-001 The folder named <path> ... does not exist`. Fast (~3s).
  * A Connection present and pointing at a path the engine host CAN see
    -> terminal status `COMPLETED`, completion message
    `OMES-SURVEY-ACTION-0019 ... completed the analysis ... in <N>
    milliseconds`. Fast (~3.4s in the live run, an empty folder).

All three completed in single-digit seconds -- confirms the design doc's
"that probe is cheap" claim for every outcome, not just the reachable one.

**Correction, read from Egeria's source (Files step 2, 2026-10-20).** The three live runs were an empty folder, a missing
folder and no connection. `FolderSurveyService` does not read `finalAnalysisStep` (only the Atlas and Unity Catalog
services do), so on a folder Egeria CAN read this call is a full Folder Survey: it walks and classifies the whole tree,
writes a SurveyReport and its CSV logs, and a large folder will not finish inside `DEFAULT_TIMEOUT_SECONDS` (the result
is then `unknown`, "could not tell"). It is still the one call that asks Egeria's engine host about a path, and the
missing-folder and no-connection answers stay fast; `file_reach.probe_filesystem` takes it through a seam so a cheaper
stat can replace it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from resource_explorer.registry import REACHABILITY_OUTCOMES

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

#: The GovernanceActionType probe 7/8 confirmed live. A qualified name, not a
#: bare request type -- matches `initiate_gov_action_type`'s
#: `action_type_qualified_name` parameter (pyegeria automated_curation.py),
#: which resolves its executor (the FileSurvey engine) server-side from this
#: GovernanceActionType's own GovernanceActionExecutor link.
FILE_SURVEY_FOLDER_ACTION_TYPE = "FileSurvey::survey-folder"

#: request_parameters value that made CHECK_ASSET-only behavior real in the
#: live probe (as opposed to a full recursive folder survey).
CHECK_ASSET_REQUEST_PARAMETERS = {"finalAnalysisStep": "CHECK_ASSET"}

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_POLL_INTERVAL_SECONDS = 3.0


class ReachabilityCheckScopeError(ValueError):
    """Raised when a reachability check is asked to do something this
    slice deliberately does not support -- e.g. a resource type other than
    'filesystem'. Kept as a distinct exception type rather than a plain
    ValueError so callers (and tests) can tell "unsupported by design" apart
    from "the call itself failed," which is instead reported as the
    'unknown' outcome per the three-state discipline, not raised."""


@dataclass(frozen=True)
class ReachabilityResult:
    """The outcome of one reachability check, ready to persist via
    ProjectRegistry.record_reachability_check()."""

    outcome: str  # one of REACHABILITY_OUTCOMES
    error_code: str
    error_detail: str
    latency_ms: int | None
    engine_action_guid: str

    def __post_init__(self) -> None:
        if self.outcome not in REACHABILITY_OUTCOMES:
            raise ValueError(
                f"outcome={self.outcome!r} is not one of {sorted(REACHABILITY_OUTCOMES)}"
            )


# Ordered so a more specific code is matched before a more generic one where
# messages could plausibly contain both substrings (none currently do, but
# order is chosen defensively rather than by accident).
_ERROR_CODE_TO_OUTCOME: tuple[tuple[str, str], ...] = (
    # OPEN-SURVEY-0009: confirmed live (probe 7, first pass) -- "has no
    # connection, so there is no way to reach the resource."
    ("OPEN-SURVEY-0009", "no_connection"),
    # BASIC-FILE-CONNECTOR-404-001: confirmed live (probe 7, second pass) --
    # the Connection exists but the engine host's own filesystem cannot see
    # the path it names. This is the folder-survey analogue of the design
    # doc's `network_unreachable` outcome (written for the database case,
    # "genuinely fails to reach the resource at all") -- same shape, "the
    # engine host cannot get to what the Connection points at," different
    # connector.
    ("BASIC-FILE-CONNECTOR-404-001", "network_unreachable"),
    # Not live-confirmed for the folder-survey path (§5's vocabulary is
    # written primarily for the database/secrets-store case) -- kept here so
    # a secrets-related failure message occurring on a future, extended
    # (e.g. templated) folder Connection is still classified rather than
    # falling through to 'unknown'. Safe to keep unconfirmed: it only ever
    # fires on a message this specific, never as a silent default.
    ("secretsCollectionName", "unresolvable_secret"),
    ("SCRAM", "unresolvable_secret"),
    ("UNAUTHORIZED", "auth_rejected"),
    ("not authorized", "auth_rejected"),
)

_SUCCESS_STATUSES = {"COMPLETED", "ACTIONED"}
_ACTIVE_STATUSES = {"REQUESTED", "APPROVED", "WAITING", "ACTIVATING", "IN_PROGRESS"}


def classify_check_asset_result(
    *,
    initiated: bool,
    final_status: str | None,
    completion_message: str,
    raised_exception: BaseException | None = None,
) -> ReachabilityResult:
    """Map a CHECK_ASSET engine-action's raw outcome onto the reachability
    outcome vocabulary, implementing the three-state absence discipline:

      1. **Never checked** -- not this function's concern at all; it is the
         absence of any `resource_reachability` row (see
         `ProjectRegistry.get_latest_reachability`).
      2. **Checked, and Egeria gave a determinate answer** -- `reachable`,
         `no_connection`, `unresolvable_secret`, `network_unreachable`, or
         `auth_rejected`. The engine action reached a real terminal status
         and its completion message was recognized.
      3. **Checked, but could not be determined** -- `outcome='unknown'`.
         This covers: the call raised (connection error, bad request, etc.),
         the action was never initiated, the action timed out without
         reaching a terminal status, OR it reached a terminal status whose
         message this function does not recognize. All of these mean "we
         don't know," not "it's unreachable" -- collapsing them into
         `no_connection`/`network_unreachable` would be a wrong, confident
         answer of exactly the kind `find-absence-as-answer` warns against.

    This function is pure and takes no network/registry dependencies, so it
    is fully unit-testable without a live Egeria platform -- see
    `tests/test_reachability.py`.
    """
    if raised_exception is not None:
        return ReachabilityResult(
            outcome="unknown",
            error_code=type(raised_exception).__name__,
            error_detail=str(raised_exception),
            latency_ms=None,
            engine_action_guid="",
        )

    if not initiated:
        return ReachabilityResult(
            outcome="unknown",
            error_code="NOT_INITIATED",
            error_detail="Egeria did not initiate the CHECK_ASSET engine action.",
            latency_ms=None,
            engine_action_guid="",
        )

    status = (final_status or "UNKNOWN").upper()

    if status in _ACTIVE_STATUSES or status in ("TIMEOUT", "UNKNOWN"):
        return ReachabilityResult(
            outcome="unknown",
            error_code=status,
            error_detail=(
                completion_message
                or f"CHECK_ASSET engine action did not reach a terminal status "
                   f"(last observed: {status})."
            ),
            latency_ms=None,
            engine_action_guid="",
        )

    if status in _SUCCESS_STATUSES:
        return ReachabilityResult(
            outcome="reachable",
            error_code="",
            error_detail=completion_message,
            latency_ms=None,
            engine_action_guid="",
        )

    # Terminal, but not success (INVALID/FAILED/IGNORED/...): classify by
    # the completion message's error code, verbatim -- never paraphrased,
    # per the peer review's note that the raw code must survive into
    # error_detail so a later reader can re-derive the classification.
    for needle, outcome in _ERROR_CODE_TO_OUTCOME:
        if needle in completion_message:
            return ReachabilityResult(
                outcome=outcome,
                error_code=needle,
                error_detail=completion_message,
                latency_ms=None,
                engine_action_guid="",
            )

    return ReachabilityResult(
        outcome="unknown",
        error_code=status,
        error_detail=completion_message or f"Unrecognized terminal status {status!r}.",
        latency_ms=None,
        engine_action_guid="",
    )


def _get_clients():
    """Construct (AutomatedCuration, MetadataExpert) clients, bearer-tokened
    -- same construction pattern as
    surveyors/egeria_delegated_step.py::_get_clients() and
    rfa_egeria_sync.py::_get_clients(). Kept as its own local helper (not
    imported from either) since this module has no other coupling to
    either -- it only needs the same two clients for the same reason
    (trigger + poll a single engine action by GUID)."""
    from pyegeria import AutomatedCuration, MetadataExpert

    from resource_explorer.egeria_clients import Daemon, DaemonReason, egeria_client

    # Who (Brief I): reachability is a named system job — the CHECK_ASSET probe runs as the
    # service account whether the page or a pass asked for it.
    clients = egeria_client(Daemon(DaemonReason.REACHABILITY), purpose="reachability probe")
    return clients.of(AutomatedCuration), clients.of(MetadataExpert)


def _poll_action_status(
    metadata_expert, engine_action_guid: str, poll_interval: float, timeout: float
) -> tuple[str, str]:
    """Same polling shape as egeria_delegated_step._poll_action_status --
    duplicated rather than imported because that module's version raises
    EgeriaEngineActionTimeoutError on timeout, and this caller wants a
    result value (mapped to `unknown`) instead of an exception to catch."""
    deadline = time.monotonic() + timeout
    status = "UNKNOWN"
    while True:
        element = metadata_expert.get_metadata_element_by_guid(engine_action_guid)
        prop_map: dict = {}
        if isinstance(element, dict):
            prop_map = (element.get("elementProperties") or {}).get("propertyValueMap") or {}
        status = (prop_map.get("activityStatus") or {}).get("symbolicName", "UNKNOWN")
        if status not in _ACTIVE_STATUSES:
            completion_message = (prop_map.get("completionMessage") or {}).get("primitiveValue", "")
            return status, completion_message
        if time.monotonic() >= deadline:
            return "TIMEOUT", ""
        time.sleep(poll_interval)


def check_filesystem_reachability(
    fs_slug: str,
    registry: "ProjectRegistry",
    *,
    probed_from: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> ReachabilityResult:
    """Trigger the fast CHECK_ASSET reachability probe for a registered,
    Egeria-cataloged filesystem, record the result, and return it.

    Requires the filesystem to already have an `egeria_asset_guid` (i.e. it
    has been cataloged -- see `EgeriaFileSystemSurveyor.catalog_and_survey`).
    A filesystem that has never been cataloged in Egeria cannot be checked
    for reachability at all -- there is no Asset to target -- and this is
    reported as `unknown`/`NOT_CATALOGED` rather than raised, since it is a
    legitimate (if unhelpful) answer to "is this reachable," not a bug in
    the caller.

    Never raises for an Egeria-side failure (bad request, timeout, network
    error, disconnected platform, ...) -- every failure mode is captured as
    a ReachabilityResult with `outcome='unknown'` and recorded like any
    other result, per the three-state discipline. It DOES raise
    ReachabilityCheckScopeError immediately, before any network call, if
    asked to do something this module does not support (currently: nothing
    else is exposed, so this is future-proofing for a caller that tries to
    pass a non-filesystem resource type through this same entry point).
    """
    from resource_explorer.registry import ProjectRegistry  # noqa: F401 (type doc only)

    fs = registry.get_filesystem(fs_slug)
    if fs is None:
        raise ReachabilityCheckScopeError(f"No filesystem registered with slug {fs_slug!r}.")

    probed_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()

    if not fs.egeria_asset_guid:
        result = ReachabilityResult(
            outcome="unknown",
            error_code="NOT_CATALOGED",
            error_detail=(
                "This filesystem has not been published to Egeria yet (no "
                "egeria_asset_guid) -- there is no Asset to run CHECK_ASSET "
                "against."
            ),
            latency_ms=None,
            engine_action_guid="",
        )
    else:
        try:
            automated_curation, metadata_expert = _get_clients()
            action_targets = [
                {"actionTargetName": "sourceFile", "actionTargetGUID": fs.egeria_asset_guid}
            ]
            guid = automated_curation.initiate_gov_action_type(
                action_type_qualified_name=FILE_SURVEY_FOLDER_ACTION_TYPE,
                request_source_guids=[],
                action_targets=action_targets,
                request_parameters=dict(CHECK_ASSET_REQUEST_PARAMETERS),
            )
            initiated = bool(guid) and guid != "Action not initiated"
            final_status = None
            completion_message = ""
            if initiated:
                final_status, completion_message = _poll_action_status(
                    metadata_expert, guid, poll_interval, timeout
                )
            result = classify_check_asset_result(
                initiated=initiated,
                final_status=final_status,
                completion_message=completion_message,
            )
            if initiated:
                result = ReachabilityResult(
                    outcome=result.outcome,
                    error_code=result.error_code,
                    error_detail=result.error_detail,
                    latency_ms=result.latency_ms,
                    engine_action_guid=guid,
                )
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
            # Egeria-side failure becomes 'unknown', not a raised exception,
            # per this function's documented never-raise-for-Egeria-failures
            # contract.
            log.warning("Reachability check for filesystem %r failed: %r", fs_slug, exc)
            result = classify_check_asset_result(
                initiated=False, final_status=None, completion_message="", raised_exception=exc
            )

    latency_ms = int((time.monotonic() - t0) * 1000)
    result = ReachabilityResult(
        outcome=result.outcome,
        error_code=result.error_code,
        error_detail=result.error_detail,
        latency_ms=latency_ms,
        engine_action_guid=result.engine_action_guid,
    )

    registry.record_reachability_check(
        fs_slug,
        probed_at=probed_at,
        outcome=result.outcome,
        probed_from=probed_from,
        error_code=result.error_code,
        error_detail=result.error_detail,
        latency_ms=result.latency_ms,
        engine_action_guid=result.engine_action_guid,
    )
    return result
