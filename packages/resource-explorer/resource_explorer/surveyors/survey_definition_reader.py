"""
Reads "Survey Definitions" from Egeria — GovernanceActionProcess elements chained
to GovernanceActionType/GovernanceActionProcessStep steps via first/next-step
relationships — and parses them into a simple, ordered structure RE can execute
against.

Survey Definitions are authored *outside* RE entirely (as Dr.Egeria plans, see
docs/egeria-collaboration-and-survey-model.md section 6). This module is
read-only for authoring — it never creates a Survey Definition or its steps.
One narrow exception: reconcile_step_links() (docs/survey-question-context-plan.md
follow-up) deletes stale/duplicate step-to-step *link relationships* Dr.Egeria's
non-idempotent "Link Next Process Step" command can leave behind on a re-run —
cleanup of a known Dr.Egeria footgun, not new authoring. See
survey_definition_reconciler.py for the diff logic and why this exists.

Fully generic across resource types — this module has no knowledge of database,
repo, or filesystem specifics. Each step's Additional Properties convention keys
(executes_at, supported_technology_type, re_analysis_step) are parsed but not
interpreted here; interpretation is the executor's job.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

log = logging.getLogger(__name__)

_DEFAULT_PLATFORM_URL = "https://localhost:9443"
_DEFAULT_VIEW_SERVER = "qs-view-server"
_DEFAULT_USER = "erinoverview"

# ── D2/D3 (docs/survey-question-context-plan.md): short-TTL, module-level
# caches — SurveyDefinitionReader is constructed fresh per HTTP request
# (see web/routes/survey_definitions.py's _do()), so instance-level caching
# would never actually persist across requests; these live at module scope
# instead. Two different TTLs on purpose: a Question's Egeria GUID almost
# never changes once authored (long TTL is safe and saves a real lookup
# round-trip), while which Survey Definitions are scoped to it can change
# as Survey Definitions are authored/re-authored (short TTL, minutes not
# hours — matches D3's "Survey Definitions change rarely but not never").
_QUESTION_GUID_CACHE_TTL_SECONDS = 3600
_CANDIDATES_CACHE_TTL_SECONDS = 300
# 2026-09-04, found live via the new SIGUSR1 thread dump (cli/main.py) while
# chasing the stuck-server incident: get_guid_for_name() is a SYNC pyegeria
# call that internally does asyncio.get_event_loop().run_until_complete(...)
# — but resolve_question_guid() is itself called from a background
# ThreadPoolExecutor worker (warm_question_guid_cache() at startup, and
# find_candidate_process_guids_by_questions()'s own pool), a thread with no
# running loop of its own. Caught, live, 6 worker threads still blocked in
# selectors.select() 15+ seconds after starting, all in the exact same
# get_guid_for_name -> nest_asyncio -> select() frame — consistent with a
# cross-thread/cross-event-loop asyncio hazard (a client/connection object
# created against one loop, used from a different one), not simple Egeria
# slowness — a direct curl to the platform's own origin endpoint at the same
# moment returned in 18ms. pyegeria's own httpx.Timeout (30s) evidently
# doesn't bound this path either. That deeper cross-loop issue is NOT fixed
# here — it needs its own investigation, likely in how pyegeria's async
# client is constructed/reused across threads, out of scope for tonight's
# instrumentation pass. (Since pyegeria 6.2.16, #513, there is no nest_asyncio
# and pyegeria keeps one loop and one httpx session per thread itself; the
# timeout below stays as containment.) What IS fixed: the call is now wrapped in its own
# single-purpose thread with a hard join timeout, so a hang like this
# degrades to resolve_question_guid's existing "not found" contract (already
# every other failure mode's behavior) instead of blocking the calling
# thread — and everything that pools these calls (warm_question_guid_cache,
# find_candidate_process_guids_by_questions) — forever.
_QUESTION_GUID_CALL_TIMEOUT_SECONDS = 15
_question_guid_cache: dict[str, tuple[float, str | None]] = {}
#: A `None` (not found, or not visible to THIS reader) is kept only briefly: the cache is shared, so one
#: reader who cannot see a term must not take scoping away from everyone for an hour (caller identity round 3).
_QUESTION_GUID_MISS_TTL_SECONDS = 60


def _question_guid_fresh(cached, now: float) -> bool:
    if cached is None:
        return False
    ttl = _QUESTION_GUID_CACHE_TTL_SECONDS if cached[1] else _QUESTION_GUID_MISS_TTL_SECONDS
    return now - cached[0] < ttl
# Keyed PER READER (caller identity, 2026-10-14): the first element of every candidates key, and of every
# fetch / process-GUID key, is `SurveyDefinitionReader._reader_key()` (who Egeria answered). The full scan
# lists every GovernanceActionProcess, including user-authored ones a deployment may zone, so one person's
# answer is never served to another. The question-GUID cache stays shared: it holds only the GUIDs of RE's
# own question glossary terms, seeded from RE's Dr.Egeria documents and never zoned.
_candidates_cache: dict[tuple, tuple[float, list]] = {}
_fetch_cache: dict[tuple, tuple[float, object]] = {}  # (reader, process_guid) -> (cached_at, SurveyDefinition)
# qualified_name -> (cached_at, guid or None). A definition's GUID changes only
# when it is re-published, and a None is cached too: "not published" is an
# answer, and re-asking Egeria for it on every candidates call would put the
# round trips back that the local lookup below removed.
_process_guid_cache: dict[tuple, tuple[float, str | None]] = {}   # (reader, qualified_name) -> ...
_PROCESS_GUID_CACHE_TTL_SECONDS = 3600


def _bust_fetch_cache(process_guid: str) -> None:
    """Drop every reader's cached graph for `process_guid` (and a bare-GUID key, if one was set)."""
    for key in [k for k in list(_fetch_cache) if k == process_guid
                or (isinstance(k, tuple) and len(k) == 2 and k[1] == process_guid)]:
        _fetch_cache.pop(key, None)


def clear_caches() -> None:
    """Testing hook — all three module-level caches are otherwise process-lifetime."""
    _question_guid_cache.clear()
    _candidates_cache.clear()
    _fetch_cache.clear()
    _process_guid_cache.clear()


# pyegeria's lookup helpers signal "nothing matched" by returning a human
# readable *string* ("No elements found") rather than None/"" — a truthy value
# that sails straight through an `or None` guard. Left unchecked it comes back
# as if it were a real GUID: resolve_question_guid() did exactly that, and the
# sentence then reached ClassificationExplorer.get_scoped_elements() as a URL
# path segment, producing a 404 that the caller's broad `except Exception`
# swallowed — so D2's scoped fast path silently degraded to the
# search_string="*" full scan on *every* call, with no error surfaced anywhere.
# Found by profiling the Survey tab, not by a failing test: the test fake
# returned None for a miss, i.e. it was better behaved than the real library.
#
# The rule is deliberately "contains no whitespace" rather than a full
# UUID-shape match. Every one of these sentinels is a human sentence, so
# whitespace catches the whole class; a stricter UUID regex would additionally
# risk rejecting a valid-but-unusually-formatted GUID, and that failure would
# be silent in exactly the same way this one was. Prefer the narrow rule that
# can only reject things a GUID can never be.
def _as_guid(value) -> str | None:
    """Return value if it can be a real Egeria GUID, else None."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or any(ch.isspace() for ch in candidate):
        return None
    return candidate

# Real response shape from GovernanceOfficer.get_governance_action_process_graph
# (renamed from get_governance_process_graph in an upcoming pyegeria release),
# confirmed against a live qs-view-server for both a single-step and a two-step
# chained Survey Definition (2026-07-07/08). This is a genuine graph
# representation — a flat node list plus a separate flat edge list — not a nested tree:
#
#   {
#     "governanceActionProcess": {"elementHeader": {...}, "properties": {...}},
#     "firstProcessStep": {
#       "element": {"elementHeader": {...}, "processStepProperties": {...}},
#       "linkGUID": "..."
#     },
#     "nextProcessSteps": [
#       {"elementHeader": {...}, "processStepProperties": {...}},   # flat, no "element" wrapper
#       ...
#     ],
#     "processStepLinks": [
#       {
#         "previousProcessStep": {"guid": "...", "uniqueName": "...", ...},  # ElementStub
#         "nextProcessStep": {"guid": "...", "uniqueName": "...", ...},      # ElementStub
#         "nextProcessStepLinkGUID": "...",
#         "mandatoryGuard": false,
#         "guard": "..."   # present only when a guard value was actually set
#       },
#       ...
#     ],
#     "governanceActionProcessMermaidGraph": "..."
#   }
#
# Notes this corrects vs. earlier, unconfirmed guesses: there is no
# "relationshipHeader"/"type"/"typeName" wrapper anywhere in this response; a
# step's own properties live under "processStepProperties", not "properties"
# (only the process element itself uses "properties"); "nextProcessSteps" is a
# flat list of every non-first step in the whole process (not nested per
# predecessor, and not wrapped in "element" the way firstProcessStep is); and
# the actual step-to-step topology (including branching) is only in
# "processStepLinks", keyed by GUID via "previousProcessStep"/"nextProcessStep".
_PROCESS_KEY = "governanceActionProcess"
_FIRST_STEP_KEY = "firstProcessStep"
_NEXT_STEPS_KEY = "nextProcessSteps"
_LINKS_KEY = "processStepLinks"


def _split_perspectives(raw) -> list:
    """Additional Properties values arrive as a string; accept a list too.

    Egeria stores Additional Properties as strings, so a multi-valued one is a
    separated list by convention. Splits on comma and semicolon, trims, and
    drops blanks -- an author writing "Security, Steward" and one writing
    "Security;Steward" mean the same thing and must not produce a perspective
    literally named " Steward" that matches no filter.
    """
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        values = str(raw).replace(";", ",").split(",")
    seen: list = []
    for v in values:
        v = str(v).strip()
        if v and v not in seen:
            seen.append(v)
    return seen


def qualified_process_name(reference: str) -> str:
    """The qualifiedName of a Survey Definition from a reference that may be its bare name."""
    from resource_explorer.surveyors.survey_definition_docs import _PROCESS_PREFIX

    return reference if "::" in reference else f"{_PROCESS_PREFIX}{reference}"


@dataclass
class ProcessLookup:
    """What `SurveyDefinitionReader.lookup_process` found: the GUID (None when it did not resolve), the names tried, and
    the calls that raised. `guid is None and errors` means "could not look up", not "no such definition"."""

    guid: str | None
    tried: list[str]
    errors: list[str]


class SurveyDefinitionReaderError(RuntimeError):
    """Raised when a Survey Definition can't be read, or is missing required data."""


class QuestionTermUnreadable(SurveyDefinitionReaderError):
    """A Question term's GUID could not be read: Egeria raised, gave no answer or an odd one, the lookup timed out,
    or two live terms hold the display name. Never "no such question": nothing caches it and nothing scopes by it."""


class UnsupportedSurveyDefinitionError(SurveyDefinitionReaderError):
    """Raised when a fetched Survey Definition has a shape RE's executor can't run yet
    (e.g. guard-based branching — v1 only supports linear step sequences)."""


@dataclass
class StepLink:
    """One `NextGovernanceActionProcessStep` edge, with the routing values that
    were previously read and thrown away.

    Egeria's model routes on guards: a completing governance service *"optionally
    supplies one or more guards and a list of action targets for the subsequent
    governance action(s) to process"*. The coordinator of the run — a real engine
    host, or RE acting as a pseudo one — reads the step graph, runs a step, takes
    the guard it produced, and picks the next viable step. **Nothing needs to be
    persisted for that**; the guard is a routing signal held for the duration of
    the run, not an artifact recorded against the process.

    That reframing (Dan, 2026-08-24) retires the open question the backlog
    carried as "whether a locally-produced guard can be recorded against the
    process at all, given RE acts as its own engine host". It was the wrong
    question: recording is optional, coordinating is the point.
    """
    previous_guid: str
    next_guid: str
    guard: str = ""
    mandatory_guard: bool = False


@dataclass
class SurveyStep:
    guid: str
    display_name: str
    qualified_name: str
    additional_properties: dict = field(default_factory=dict)
    executes_at: str = ""
    re_analysis_step: str | None = None
    supported_technology_type: str | None = None
    description: str = ""  # author-provided, from Egeria's own Description attribute
    # 0462 attributes on GovernanceActionProcessStep itself. Present in the
    # payload and previously dropped on the floor — `producedGuards` is the
    # authored declaration of what this step can emit, which is what a
    # coordinator needs in order to know a guard is expected at all.
    produced_guards: list = field(default_factory=list)
    wait_time: int | None = None
    ignore_multiple_triggers: bool = False
    # Data flowing INTO this step. §0462 puts these on the
    # `GovernanceActionExecutor` relationship (`requestParameters`,
    # `requestParameterMap`/`Filter`, `actionTargetMap`/`Filter`) rather than on
    # the step entity, so whether they arrive depends on what the process-graph
    # API returns. Populated when present; left empty when not — and
    # `executor_present` says which, so an empty dict is never mistaken for
    # "this step declares no parameters".
    request_parameters: dict = field(default_factory=dict)
    action_targets: list = field(default_factory=list)
    executor_present: bool = False


@dataclass
class SurveyDefinition:
    process_guid: str
    display_name: str
    qualified_name: str
    supported_technology_type: str | None
    #: list[SurveyStep] in execution order. For a branching definition this is a
    #: topological order — every step precedes its dependents — which is an
    #: order to READ them in, not a sequence to run: which branch actually runs
    #: is decided at execution time from the guards in `links`.
    steps: list = field(default_factory=list)
    # list[StepLink] — the step-to-step edges WITH their guards. `steps` is the
    # linear execution order v1 runs; `links` is the authored topology, kept
    # whole so a coordinator can route on guards without re-fetching. Populated
    # even while the executor only walks a line.
    links: list = field(default_factory=list)
    #: Steps the process declares that no path from its first step reaches.
    #: Surfaced rather than dropped: such a step is in the definition, absent
    #: from every run, and otherwise indistinguishable from one never authored.
    unreachable_step_guids: list = field(default_factory=list)
    description: str = ""  # author-provided, from Egeria's own Description attribute
    # Which UI surface this Survey Definition is meant for — "scouting" |
    # "discovery" | "automate_full" | ... — Additional Properties convention,
    # see dr_egeria_survey_publisher.render_process_block's docstring and
    # docs/discovery-automate-project-context-plan.md Part 1. None for
    # Survey Definitions authored before this convention existed.
    survey_kind: str | None = None
    #: Perspectives this survey is authored FOR, as declared in Additional
    #: Properties. Retained only as an escape hatch for a definition that has
    #: no ScopedBy links yet — the real association is
    #: Survey --ScopedBy--> Question --> Perspective, read by the candidates
    #: route, because Questions already carry Perspectives and scoping a Survey
    #: to the Questions it answers is the same statement without a second tag
    #: to keep in sync (D7, docs/unified-survey-execution-model-plan.md).
    perspectives: list = field(default_factory=list)


class SurveyDefinitionReader:
    """Fetches Survey Definitions from Egeria via pyegeria's GovernanceOfficer/AutomatedCuration clients."""

    def __init__(
        self,
        platform_url: str | None = None,
        view_server: str | None = None,
        user_id: str | None = None,
        user_password: str | None = None,
        *,
        identity=None,
    ) -> None:
        self.platform_url = platform_url or egeria_platform_url()
        self.view_server = view_server or egeria_view_server()
        self.user_id = user_id or os.getenv("EGERIA_USER", _DEFAULT_USER)
        # Not used to authenticate (Brief I): who acts is `self.principal()`, via the factory.
        self.user_password = user_password or ""
        self._identity = identity
        self._governance_officer = None
        self._automated_curation = None
        self._classification_explorer = None
        self._metadata_expert = None
        # pyegeria ISSUE-96 (docs/qguid-flake report, 2026-09-12): a
        # ClassificationExplorer's httpx.AsyncClient binds its connection
        # pool's asyncio primitives to whichever event loop first drives it,
        # and every calling thread drove it on its own loop (pyegeria applied
        # nest_asyncio then; since 6.2.16 its run_sync gives each thread one
        # persistent loop and session, which has the same effect) — so sharing ONE client across the shared
        # run_sync pool's worker threads fails deterministically (measured:
        # 52/52 names, 2 runs, 100% CLIENT_ERROR_400) while a client built
        # AND always called from the same single thread never fails (0/104).
        # This threading.local() gives each pool worker its own persistent
        # client instead: constructed once per thread (same URL/server/
        # credentials as _connect_classification_explorer), then reused by
        # that thread only, so the pool keeps its 8-way parallelism and no
        # thread ever touches another thread's client/loop.
        self._thread_local_explorer = threading.local()

    # ── connection ────────────────────────────────────────────────────────────

    def principal(self):
        """Who this reader acts as: the identity it was given, else `current_principal()`,
        resolved ONCE in the calling thread — the pooled lookups run on threads that carry no
        ContextVar, so they read it from here."""
        if self._identity is None:
            from resource_explorer.egeria_clients import current_principal

            self._identity = current_principal()
        return self._identity

    def _reader_key(self) -> tuple:
        """Who Egeria answers for this reader: (kind, the account the client is built for). The first
        element of every per-reader cache key (see the cache declarations)."""
        p = self.principal()
        return (p.kind, p.client_user or p.user_id)

    def _clients(self, *, shared: bool = True):
        from resource_explorer.egeria_clients import egeria_client

        return egeria_client(self.principal(), purpose="survey definitions",
                             view_server=self.view_server, platform_url=self.platform_url,
                             shared=shared)

    def connect(self) -> None:
        """Establish pyegeria client connections (lazy)."""
        if self._governance_officer is not None:
            return
        if not self.platform_url:
            raise SurveyDefinitionReaderError(
                "EGERIA_PLATFORM_URL is not set. "
                "Set it in .env or pass platform_url= to SurveyDefinitionReader."
            )
        try:
            from pyegeria import AutomatedCuration
            from pyegeria.omvs.governance_officer import GovernanceOfficer

            clients = self._clients()
            self._governance_officer = clients.of(GovernanceOfficer)
            self._automated_curation = clients.of(AutomatedCuration)
        except PermissionError:
            raise
        except ImportError as exc:
            raise SurveyDefinitionReaderError("pyegeria is not installed.") from exc
        except Exception as exc:
            raise SurveyDefinitionReaderError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc

    def _new_classification_explorer(self):
        """Construct one ClassificationExplorer, connected. The single
        construction seam both ``_connect_classification_explorer`` (cached
        on ``self``, main-thread callers) and ``_thread_local_classification_
        explorer`` (cached per-thread, question-GUID lookups) call — tests
        substitute this one method rather than either cache, so a fake works
        no matter which thread ends up constructing it."""
        from pyegeria.omvs.classification_explorer import ClassificationExplorer

        # Its own (unshared) client: one per thread, per pyegeria ISSUE-96.
        return self._clients(shared=False).of(ClassificationExplorer)

    def _connect_classification_explorer(self):
        """Lazy, separate from connect() — only the D2 scoped-query path
        needs ClassificationExplorer (add_scope_to_element/get_scoped_elements/
        get_guid_for_name), so every other reader method stays unaffected if
        this client fails to construct for any reason."""
        if self._classification_explorer is not None:
            return self._classification_explorer
        client = self._new_classification_explorer()
        self._classification_explorer = client
        return client

    def _new_question_lookup_client(self):
        """Construct one MetadataExpert, connected, for question-GUID lookups (the typed string search,
        `egeria_lookup.guid_by_exact_value`). Its own (unshared) client: one per thread, per pyegeria ISSUE-96.
        The single construction seam `_thread_local_question_lookup_client` calls; tests substitute it."""
        from pyegeria.omvs.metadata_expert import MetadataExpert

        return self._clients(shared=False).of(MetadataExpert)

    def _thread_local_question_lookup_client(self):
        """Per-thread MetadataExpert for question-GUID lookups only
        (see pyegeria ISSUE-96 note on ``self._thread_local_explorer`` in
        __init__). Built by ``_new_question_lookup_client`` (same URL/server/
        credentials/bearer token as every other client of this reader), but
        memoised on ``threading.local()`` instead of ``self`` — one client per
        pool worker thread, never shared, bounded by the shared pool's own size
        (``EXPLORER_SYNC_POOL_SIZE``, default 8; see concurrency.py), so this
        adds at most one client per already-existing worker thread, not one per
        call.

        Only ``_lookup_question_guid`` (and, through it, the pooled
        ``_resolve_one_pooled``) should call this. Every ClassificationExplorer
        caller keeps using the shared, main-thread-cached
        ``_connect_classification_explorer`` — those callers are never invoked
        from the shared pool, so they never hit the cross-loop bug.
        """
        from resource_explorer.egeria_clients import identity_key

        # Keyed by identity too: a pool thread serves many readers, and one person's client
        # must never answer for another (Brief I).
        key = identity_key(self.principal())
        cached = getattr(self._thread_local_explorer, "client", None)
        if cached is not None and getattr(self._thread_local_explorer, "key", None) == key:
            return cached
        client = self._new_question_lookup_client()
        self._thread_local_explorer.client = client
        self._thread_local_explorer.key = key
        return client

    def _connect_metadata_expert(self):
        """Lazy, separate from connect() — only reconcile_step_links() needs
        MetadataExpert.delete_related_elements(), the generic relationship-
        delete method (no dedicated "unlink process step" method exists in
        pyegeria — confirmed while building this)."""
        if self._metadata_expert is not None:
            return self._metadata_expert
        from pyegeria.omvs.metadata_expert import MetadataExpert

        client = self._clients().of(MetadataExpert)
        self._metadata_expert = client
        return client

    # ── discovery: find candidate Survey Definitions by Technology Type ────────

    def find_candidate_process_guids(self, technology_type: str, survey_kind: str | None = None) -> list:
        """Return every GovernanceActionProcess whose additionalProperties declare
        supported_technology_type == technology_type.

        survey_kind, when given, additionally filters to Survey Definitions
        whose Additional Properties declare that exact survey_kind — e.g. a
        Discovery-tier caller passes survey_kind="discovery" so an
        Automate-tier "run everything" bundle (survey_kind="automate_full")
        or a Scouting-tier coarse scan doesn't show up as a Discovery
        candidate. None (default) keeps the old behavior — every Survey
        Definition for the technology type, regardless of kind — for
        backward compatibility with callers that don't care (and with
        Survey Definitions authored before this convention existed, which
        have no survey_kind at all).

        Confirmed live (2026-07-08) that `AutomatedCuration.get_tech_type_detail`
        — the mechanism this originally used, copied from `EgeriaDatabaseSurveyor.
        _find_survey_process_name` — queries Egeria's real Technology Type /
        Deployed Implementation Type catalog. That catalog has no entry for
        user-authored Survey Definitions at all (Dr.Egeria's `Create Governance
        Action Process` only sets a plain `additionalProperties` string; it never
        links the process into that catalog), so `get_tech_type_detail` returned
        "No elements found" for a Survey Definition that demonstrably exists.
        `_find_survey_process_name` may still be the right tool for finding
        Egeria's own *native* survey processes (which genuinely are catalogued
        there) — this method is specifically for RE's own additionalProperties
        convention, which is a different, simpler mechanism: list every
        GovernanceActionProcess (via `GovernanceOfficer.find_governance_definitions`,
        `metadata_element_type="GovernanceActionProcess"`) and filter client-side
        by the additionalProperties value.

        Each item: {qualified_name, display_name, guid}. Does NOT assume there is
        exactly one — the caller decides what to do with zero/one/many.

        D3-cached (docs/survey-question-context-plan.md) — this is the full
        `search_string="*"` scan D2's scoped path exists to avoid on the hot
        path, but it's kept as an explicit fallback (D2's own docstring), so
        it still deserves the same short-TTL cache rather than re-scanning
        every GovernanceActionProcess on every call.
        """
        cache_key = (self._reader_key(), "full_scan", technology_type, survey_kind)
        now = time.monotonic()
        cached = _candidates_cache.get(cache_key)
        if cached is not None and now - cached[0] < _CANDIDATES_CACHE_TTL_SECONDS:
            return cached[1]

        self.connect()
        candidates = []
        try:
            # page_size is generous but not exhaustive — a server with more than
            # this many GovernanceActionProcess elements (most are unrelated
            # system processes, e.g. "Create Subscription" jobs) would need real
            # pagination here; not yet implemented.
            results = self._governance_officer.find_governance_definitions(
                search_string="*",
                metadata_element_type="GovernanceActionProcess",
                output_format="JSON",
                page_size=1000,
                # graph_query_depth=0 is the single biggest lever on Survey-tab
                # load time: pyegeria defaults it to 3, which makes Egeria expand
                # each returned element's related-element graph 3 hops deep. This
                # loop reads nothing but element-local fields
                # (additionalProperties/qualifiedName/displayName + the header
                # GUID), so every one of those traversals was pure waste.
                # Measured live against a real server, 406 GovernanceActionProcess
                # elements: depth 3 = 18.9s, depth 1 = 18.9s, depth 0 = 0.20s
                # (~94x) — and all three return byte-identical results, same 406
                # elements and same 4 matching candidates. Note depth 1 costs the
                # same as 3: the cliff is "any traversal at all," not its depth,
                # so this must stay 0 rather than merely being lowered.
                # Per-step detail still costs a graph query, but that's paid
                # per-candidate in fetch(), only for the handful that matched.
                graph_query_depth=0,
            )
            if isinstance(results, str):
                import json as _json
                results = _json.loads(results)
            if not isinstance(results, list):
                return []

            for el in results:
                props = el.get("properties", {}) or {}
                additional = props.get("additionalProperties", {}) or {}
                if additional.get("supported_technology_type") != technology_type:
                    continue
                if survey_kind is not None and additional.get("survey_kind") != survey_kind:
                    continue
                qn = props.get("qualifiedName")
                if not qn:
                    continue
                header = el.get("elementHeader", {}) or {}
                candidates.append(
                    {
                        "qualified_name": qn,
                        "display_name": props.get("displayName", qn),
                        "guid": header.get("guid", ""),
                    }
                )
        except Exception as exc:
            log.debug("find_candidate_process_guids failed for %s: %s", technology_type, exc)
            return candidates  # don't cache a failure — next call should retry live

        _candidates_cache[cache_key] = (now, candidates)
        return candidates

    # ── D2 (docs/survey-question-context-plan.md): scoped candidate lookup ──

    def resolve_question_guid(self, question_display_name: str) -> str | None:
        """Resolve a Question glossary term's GUID by its display name —
        long-TTL cached (Question terms are stable once authored via
        docs/dr-egeria/scouting-questions.md and siblings). None only when
        Egeria holds no live (non-DEPRECATED) term with that display name (the
        DEPRECATED filter covers the no-documents fallback and the warm-up only:
        see `_lookup_question_guid`);
        a lookup that could not be made raises `QuestionTermUnreadable` and
        is not cached (2026-10-23: "not found" licensed "contributes no
        scoping", so an outage or a duplicated term read as a fact)."""
        now = time.monotonic()
        cached = _question_guid_cache.get(question_display_name)
        if _question_guid_fresh(cached, now):
            return cached[1]

        guid = self._lookup_question_guid(question_display_name)
        _question_guid_cache[question_display_name] = (now, guid)
        return guid

    def _lookup_question_guid(self, question_display_name: str) -> str | None:
        """The uncached Egeria lookup, bounded: a GUID, None (no live term holds the name), or a raise of
        `QuestionTermUnreadable`.

        Split out of resolve_question_guid so the batch path
        (_resolve_question_guids) can submit THIS as leaf work to the
        shared pool without one pooled task opening a pool of its own.

        The lookup (architect ruling 2026-10-22): a TYPED string search on
        displayName, exact, GlossaryTerm only (`egeria_lookup.guid_by_exact_value`),
        measured instant where `get_guid_for_name` (by-exact-property-value) timed
        out at 30 s. DEPRECATED terms are excluded (2026-10-23): the term batch
        marks retired and merged questions DEPRECATED and keeps their links, so
        this filter is what stops them resolving ON THE PATHS THAT CALL THIS, and
        only those: the no-documents fallback (`find_candidate_process_guids_by_questions`
        -> `_find_candidates_via_egeria` when no definition document is readable)
        and the warm-up (`warm_question_guid_cache`). The main path reads the
        authored documents and never resolves a term here, so it applies no status
        filter: it relies on the YAML (`configdata/question_catalog.yaml`) carrying
        no retired or merged rows, pinned by
        test_question_catalog_reader.test_committed_catalog_has_no_retired_or_merged_rows_and_no_reserved_id_is_active
        (architect ruling (c)). Two live terms holding the name
        are UNREADABLE (`ExactValueAmbiguous`), never "pick one". No qualifiedName
        is tried first: a question term's qualifiedName is generated by Dr.Egeria
        and RE does not know it (csv_to_dr_egeria_questions.py).

        The bound stays (docs/process-model.md §1.3): pyegeria's sync calls run
        their coroutine with run_until_complete, and on 2026-09-03/04 six worker
        threads were caught, live, still blocked in that frame 15+ seconds in.
        run_sync abandons rather than joins the worker on timeout; a timeout is
        "could not tell", never "not found".

        Uses ``_thread_local_question_lookup_client`` — pyegeria ISSUE-96: a
        client shared across threads breaks deterministically here because it
        can run on a pool worker thread. The client is looked up INSIDE
        ``_call``: run_sync may run ``_call`` on a different thread than this
        one, and a client bound to the wrong thread's loop is the bug.
        """
        from concurrent.futures import TimeoutError as _FutureTimeoutError

        from resource_explorer.concurrency import run_sync
        from resource_explorer.egeria_lookup import guid_by_exact_value

        # Who, resolved HERE in the caller's thread (the pool thread has no ContextVar): no caller is a 401.
        self.principal()

        def _call():
            client = self._thread_local_question_lookup_client()
            return guid_by_exact_value(client, question_display_name, property_name="displayName",
                                       type_name="GlossaryTerm", exclude_deprecated=True)

        try:
            return run_sync(_call, timeout=_QUESTION_GUID_CALL_TIMEOUT_SECONDS)
        except PermissionError:
            raise
        except _FutureTimeoutError as exc:
            raise QuestionTermUnreadable(
                f"the question term {question_display_name!r} did not answer within "
                f"{_QUESTION_GUID_CALL_TIMEOUT_SECONDS}s: could not tell") from exc
        except Exception as exc:
            raise QuestionTermUnreadable(
                f"the question term {question_display_name!r} could not be read: "
                f"{' '.join(str(exc).split())[:200]}") from exc

    def warm_question_guid_cache(self, questions: list[str] | None = None) -> dict:
        """Resolve question GUIDs ahead of the first request. Never raises.

        The three caches in this module are plain in-process dicts, so they die
        with the process. Yesterday's fix (83381f1) made the cold path 6.8x
        faster by resolving GUIDs through a thread pool instead of in series —
        it did not remove the round trips, it stopped them queueing.

        So a restart still charges the FIRST person to open each phase for that
        phase's own uncached questions. Measured 2026-09-01, from the outside:
        every phase 0.1-0.2s once warm, and a cold phase reported by Dan at
        over ten seconds. Phases with disjoint question sets each pay
        separately, which is why the timings looked random — scouting quick,
        discovery slow, assessment quick again.

        Warming moves that work off the interactive path. It is the same work,
        done once, when nobody is waiting for it.

        **Best-effort by construction.** Every failure mode here is a reason to
        stop warming, never a reason to fail: Egeria unreachable at boot is
        normal (the platform may start after this process), and a warm that
        raised would take the server down for an optimisation. A question that
        does not resolve is cached as None by resolve_question_guid, exactly as
        it would be on the request path — this changes WHEN the lookup happens,
        never WHAT it concludes.
        """
        try:
            if questions is None:
                from resource_explorer.surveyors.question_catalog_reader import get_questions

                # Every repo question, unfiltered — 49 today. Unfiltered on
                # purpose: warming per phase would leave whichever phase the
                # user opens FIRST still paying, which is the behaviour being
                # removed. resolve_question_guid dedupes via the cache anyway,
                # so questions shared across phases cost one lookup.
                questions = [
                    q.get("question") or q.get("display_name") or q.get("name") or ""
                    for q in get_questions("repo")
                ]
                questions = [q for q in questions if q]
        except Exception as exc:
            log.debug("cache warm: could not read the question catalog: %s", exc)
            return {"warmed": 0, "resolved": 0, "skipped": "no question catalog"}

        try:
            pairs = self._resolve_question_guids(list(questions))
        except Exception as exc:
            log.info("cache warm skipped (%s) — the first request will resolve "
                     "these instead, exactly as before", type(exc).__name__)
            return {"warmed": 0, "resolved": 0, "skipped": type(exc).__name__}

        resolved = sum(1 for _q, g in pairs if g)

        # Second half of the cold cost, added after measuring the first fix.
        #
        # Warming question GUIDs alone took a cold phase from >10s to ~1s, and
        # the residual second was a DIFFERENT global cache: `_fetch_cache`,
        # which holds each definition's fetched steps. Ten definitions, each
        # paid for once by whichever phase happens to touch it first — so the
        # delay simply moved rather than going away, and it was still the first
        # user who paid it.
        #
        # Measured 2026-09-01 on a freshly restarted server: first open of a
        # phase 0.7-1.7s, the SAME phase again 0.14s. That gap is this.
        definitions, definitions_failed = self._warm_definition_fetches()

        # Failures logged at WARNING, not debug: a warm that silently managed
        # three of ten leaves the delay it exists to remove, and nobody would
        # know without being told.
        if definitions_failed:
            log.warning("survey-definition cache warm: %d definition(s) could NOT be "
                        "prefetched — the first request for those will pay the cost",
                        definitions_failed)
        log.info("survey-definition cache warmed: %d question(s) checked, %d resolved, "
                 "%d definition(s) prefetched, %d failed",
                 len(questions), resolved, definitions, definitions_failed)
        return {"warmed": len(questions), "resolved": resolved,
                "definitions": definitions, "definitions_failed": definitions_failed,
                "skipped": ""}

    def _warm_definition_fetches(self) -> tuple[int, int]:
        """Prefetch every authored definition into `_fetch_cache`. Never raises.

        Returns **(cached, failed)**, not just a success count.

        Reads the definition list LOCALLY (`documented_definitions`), the same
        source `/api/survey-definitions/definitions` uses, so discovering what
        to warm never depends on Egeria being reachable. Only the per-definition
        `fetch()` does, and each failure there is independent and survivable.

        `cached` counts what was actually cached, never what was attempted — a
        definition whose GUID does not resolve is not warm, and counting it
        would overstate what happened.

        **Why a failure count rather than only a success count.** The first
        version returned `cached` alone and `test_no_silent_success` correctly
        flagged it: "3 warmed" and "3 warmed, 7 failed" were the same value to
        every caller, so a mostly-broken warm was indistinguishable from a
        small catalog. The per-item `except` is deliberate — one unreachable
        definition must not stop the other nine — but deliberate swallowing
        still has to leave a trace the caller can branch on. That is the
        difference between best-effort and silent.
        """
        try:
            from resource_explorer.surveyors.survey_definition_docs import (
                _PROCESS_PREFIX, documented_definitions,
            )
            names = [f"{_PROCESS_PREFIX}{n}" for n in documented_definitions()]
        except Exception as exc:
            log.debug("definition warm: could not list definitions: %s", exc)
            return 0, 0

        done = failed = 0
        for qname in names:
            try:
                guid = self.find_process_guid_by_name(qname)
                if not guid:
                    continue
                self.fetch(guid)
                done += 1
            except Exception as exc:
                # One unreachable or malformed definition must not stop the
                # other nine warming — the same per-item isolation the repair
                # paths in egeria_resync use, for the same reason. Counted, so
                # the caller can tell a partial warm from a complete one.
                failed += 1
                log.debug("definition warm: %s skipped (%s)", qname, type(exc).__name__)
        return done, failed

    #: Concurrency for question-GUID resolution. Small on purpose: these are
    #: reads against one Egeria server, and the goal is to stop waiting in
    #: series, not to load-test someone else's platform.
    _GUID_RESOLVE_WORKERS = 8

    def _resolve_question_guids(self, questions: list[str]) -> list:
        """[(question, guid)] for those that resolve, in the input's order.

        Concurrent, but order-preserving: `find_candidate_process_guids_by_questions`
        builds a cache key from these, and a set built from an unordered result
        would still be stable, while any future consumer reading them positionally
        would not be. Cheap to keep, expensive to debug if it is ever lost.
        """
        if not questions:
            return []
        if len(questions) == 1:
            g = self.resolve_question_guid(questions[0])
            return [(questions[0], g)] if g else []

        from resource_explorer.concurrency import submit_all

        # Resolve the FIRST question on this thread, then pool the rest.
        #
        # This is the pre-warm: resolving one question first on this thread
        # resolves the caller and fails fast. A lookup that could not be made
        # RAISES (`QuestionTermUnreadable`, 2026-10-23): an unreachable Egeria or
        # a duplicated term is never "contributes no scoping", so this function
        # propagates it rather than dropping the question.
        first, rest = questions[0], questions[1:]
        guids = [self.resolve_question_guid(first)]

        # The ONE shared pool per process, not a private
        # ThreadPoolExecutor(max_workers=8) — and submitting the cached
        # wrapper's LEAF work rather than resolve_question_guid itself, so a
        # pooled task never re-enters the pool. Concurrency is now the shared
        # pool's size (EXPLORER_SYNC_POOL_SIZE, default 8, which is what
        # _GUID_RESOLVE_WORKERS was) rather than a per-call ceiling.
        self.principal()   # resolved in this thread; the pooled leaves read it from self
        futures = submit_all(self._resolve_one_pooled, rest)

        # Bounded overall, not per-future: the pool is shared and its size is
        # not len(rest), so a later future legitimately waits behind earlier
        # ones and a flat 15s each would time out healthy work. The budget is
        # the per-call bound plus one call's worth per queued batch of
        # pool_size — enough for the queue to drain at full speed, not enough
        # for a stuck worker to hold the caller indefinitely.
        from concurrent.futures import TimeoutError as _FutureTimeoutError

        from resource_explorer.concurrency import pool_size

        width = max(1, pool_size() or 1)
        budget = _QUESTION_GUID_CALL_TIMEOUT_SECONDS * (
            1 + (len(rest) + width - 1) // width)
        deadline = time.monotonic() + budget
        for fut in futures:
            try:
                guids.append(fut.result(timeout=max(0.0, deadline - time.monotonic())))
            except _FutureTimeoutError as exc:
                for pending in futures:
                    pending.cancel()
                raise QuestionTermUnreadable(
                    f"the question-GUID batch exceeded its {budget}s budget: could not tell") from exc
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
        return [(q, g) for q, g in zip(questions, guids) if g]

    def _resolve_one_pooled(self, question_display_name: str) -> str | None:
        """resolve_question_guid's cache handling, minus the pool hop.

        Runs ON a shared-pool thread (submitted by _resolve_question_guids),
        so it must be leaf work: it calls the Egeria lookup directly instead
        of going back through run_sync. The per-call timeout still applies —
        it is enforced by the caller's own wait in _resolve_question_guids,
        which is bounded by the same pool that would have been blocked
        anyway.
        """
        now = time.monotonic()
        cached = _question_guid_cache.get(question_display_name)
        if _question_guid_fresh(cached, now):
            return cached[1]
        guid = self._lookup_question_guid(question_display_name)
        _question_guid_cache[question_display_name] = (now, guid)
        return guid

    def find_candidate_process_guids_by_questions(
        self, questions: list[str], technology_type: str, survey_kind: str | None = None,
    ) -> list:
        """Which Survey Definitions answer any of these questions — read from
        the authored documents, not from Egeria.

        Every definition document declares its own ScopedBy links
        (`## Link Element To Scope` / `### Scope Reference`), and those blocks
        are what CREATE the relationships in Egeria. So the local file and the
        graph hold the same fact, and until 2026-09-11 this asked the graph
        for it one question at a time: the reader's own log said
        "45 sequential get_scoped_elements call(s) took 44.4s" — on a WARM
        cache, ~1s a round trip, every five minutes when the candidates cache
        expired. Pooling did not help, because pyegeria drives one shared
        httpx.AsyncClient from whichever thread calls it and concurrent use
        serialises or fails. The document read is microseconds.

        One Egeria call per MATCHED definition remains — resolving its GUID —
        because the document cannot say whether the definition is published,
        and an unpublished one cannot run. Cached for an hour, including the
        "not published" answer. At most ten definitions exist; the old path
        made forty-five calls before touching any of them.

        Falls back to the Egeria walk (`_find_candidates_via_egeria`) only
        when no documents are readable, so a deployment without the docs
        directory behaves as before rather than returning nothing.

        Returns the same shape as before: [{qualified_name, display_name,
        guid, matched_questions}], with `matched_questions` in the order the
        caller asked, which is what the cache key and the UI both rely on.
        """
        from resource_explorer.surveyors.survey_definition_docs import (
            _PROCESS_PREFIX, documented_definitions,
        )

        docs = documented_definitions()
        if not docs:
            return self._find_candidates_via_egeria(questions, technology_type, survey_kind)

        wanted = list(dict.fromkeys(q for q in questions if q))
        # No candidates-level cache on this path (caller identity, 2026-10-14): the list is local documents
        # filtered in memory, and the only Egeria answer in it (each definition's GUID) is already cached PER
        # READER by `_process_guid_cached`. A reader is needed only when a document matches.

        candidates: list = []
        for name, doc in sorted(docs.items()):
            if doc.technology_type != technology_type:
                continue
            if survey_kind is not None and doc.survey_kind != survey_kind:
                continue
            scoped = set(doc.scoped_by)
            matched = [q for q in wanted if q in scoped]
            if not matched:
                continue
            qualified_name = f"{_PROCESS_PREFIX}{name}"
            guid = self._process_guid_cached(qualified_name)
            if not guid:
                # Authored but not published: it cannot be run, so it is not
                # a candidate — and that is said in the log rather than
                # silently, because "the survey I wrote is not offered" is a
                # question someone will ask.
                log.info("survey definition %s is documented but not resolvable in Egeria; not offered", qualified_name)
                continue
            candidates.append({
                "qualified_name": qualified_name,
                "display_name": doc.display_name or name,
                "guid": guid,
                "matched_questions": matched,
            })
        return candidates

    def find_unscoped_process_guids(
        self, catalog_questions: list[str], technology_type: str, survey_kind: str | None = None,
    ) -> list:
        """Survey Definitions for this technology type that NO catalog question is scoped to: an absence of a
        question is a fact to show, not a reason to drop the definition from the list.

        `catalog_questions` is every question the catalog holds for the resource type, whatever its stage. A
        definition whose ScopedBy links name only questions outside that set (or none) is unscoped. Read from the
        authored documents, like `find_candidate_process_guids_by_questions`, and the same rule applies to a
        definition that is not published (it cannot run, so it is not offered). Returns the same shape with an
        empty `matched_questions`. Empty when no documents are readable: with nothing to read, "unscoped" is not
        something this can say."""
        from resource_explorer.surveyors.survey_definition_docs import (
            _PROCESS_PREFIX, documented_definitions,
        )

        docs = documented_definitions()
        known = {q for q in catalog_questions if q}
        out: list = []
        for name, doc in sorted(docs.items()):
            if doc.technology_type != technology_type:
                continue
            if survey_kind is not None and doc.survey_kind != survey_kind:
                continue
            if known & set(doc.scoped_by):
                continue
            qualified_name = f"{_PROCESS_PREFIX}{name}"
            guid = self._process_guid_cached(qualified_name)
            if not guid:
                log.info("survey definition %s is documented but not resolvable in Egeria; not offered", qualified_name)
                continue
            out.append({"qualified_name": qualified_name, "display_name": doc.display_name or name, "guid": guid,
                        "matched_questions": []})
        return out

    def _process_guid_cached(self, qualified_name: str) -> str | None:
        now = time.monotonic()
        key = (self._reader_key(), qualified_name)
        hit = _process_guid_cache.get(key)
        if hit is not None and now - hit[0] < _PROCESS_GUID_CACHE_TTL_SECONDS:
            return hit[1]
        guid = self.find_process_guid_by_name(qualified_name)
        _process_guid_cache[key] = (now, guid)
        return guid

    def _find_candidates_via_egeria(
        self, questions: list[str], technology_type: str, survey_kind: str | None = None,
    ) -> list:
        """D2's scoped alternative to find_candidate_process_guids()'s
        search_string="*" full-instance scan: given the Questions relevant
        to a phase/perspective (from question_catalog_reader.get_questions()),
        resolve each to its Egeria GUID and call
        ClassificationExplorer.get_scoped_elements(question_guid) — which
        returns every element linked via ScopedBy to that Question,
        constrained server-side to GovernanceActionProcess via
        metadataElementTypeName. Unions results across questions
        (a Survey Definition can answer more than one), dedupes by guid,
        then applies the same supported_technology_type/survey_kind filter
        find_candidate_process_guids() does — Additional Properties aren't
        part of the ScopedBy query itself, still need client-side filtering.

        Returns [] (not an error) if no question resolves to a real GUID —
        the caller decides whether to fall back to the full scan."""
        # Paired with their text, so a matched candidate can name the Questions
        # that scoped it rather than just how many there were.
        #
        # Resolved concurrently (2026-08-31). Each resolve_question_guid() is an
        # independent Egeria round trip, and doing them in a generator ran them
        # strictly one after another: measured ~0.2-1.4s each, against 10
        # questions for a stage and **49** for the unscoped `automate_full`
        # lookup the UI also issues. That serial chain was the bulk of a ~24s
        # cold stage load, and none of it was work — it was waiting.
        #
        # Order is restored afterwards so the result is deterministic; the
        # cache_key below is built from it. A question no live term holds
        # contributes no scoping; a lookup that could not be made RAISES
        # (`QuestionTermUnreadable`) rather than reading as one.
        question_guids = self._resolve_question_guids(questions)
        if not question_guids:
            return []

        cache_key = (self._reader_key(), tuple(sorted(g for _, g in question_guids)), technology_type, survey_kind)
        now = time.monotonic()
        cached = _candidates_cache.get(cache_key)
        if cached is not None and now - cached[0] < _CANDIDATES_CACHE_TTL_SECONDS:
            return cached[1]

        by_guid: dict[str, dict] = {}
        try:
            client = self._connect_classification_explorer()
            # Sequential, one Egeria round trip per question — unlike the GUID
            # resolution above, not pooled (2026-09-04: not changing that here,
            # it's a real perf project of its own, not a "minor" instrumentation
            # pass). What WAS missing: any visibility into whether a slow
            # candidates load is "Egeria is just slow today" or "something is
            # actually stuck" — this request has no distinguishing signal from
            # the outside; a hung server and a loaded one look identical from a
            # pending fetch(). get_scoped_elements() takes no timeout kwarg of
            # its own to override here (it forwards **kwargs, but pyegeria's own
            # request layer isn't verified to honor a shorter one reliably, so
            # not attempted) — logging the actual per-call and total duration is
            # the honest, low-risk fix: next time this is slow, the log says by
            # how much and which question, rather than a guess from wall-clock
            # browser timing.
            loop_started = time.monotonic()
            for question_text, question_guid in question_guids:
                call_started = time.monotonic()
                results = client.get_scoped_elements(
                    question_guid,
                    page_size=1000,
                    body={"class": "ResultsRequestBody", "metadataElementTypeName": "GovernanceActionProcess"},
                )
                call_elapsed = time.monotonic() - call_started
                if call_elapsed > 5.0:
                    log.warning(
                        "get_scoped_elements for question %r took %.1fs (guid=%s) — "
                        "slow Egeria response, not necessarily stuck",
                        question_text, call_elapsed, question_guid,
                    )
                if isinstance(results, str):
                    continue  # pyegeria returns a string ("No elements found") when empty
                for el in results or []:
                    props = el.get("properties", {}) or {}
                    additional = props.get("additionalProperties", {}) or {}
                    if additional.get("supported_technology_type") != technology_type:
                        continue
                    if survey_kind is not None and additional.get("survey_kind") != survey_kind:
                        continue
                    qn = props.get("qualifiedName")
                    if not qn:
                        continue
                    header = el.get("elementHeader", {}) or {}
                    guid = header.get("guid", "")
                    existing = by_guid.get(guid)
                    if existing is None:
                        by_guid[guid] = {
                            "qualified_name": qn,
                            "display_name": props.get("displayName", qn),
                            "guid": guid,
                            # The Questions this Survey Definition is ScopedBy,
                            # among those queried. A survey answers more than
                            # one, so this accumulates across the loop instead
                            # of being overwritten by the last match.
                            "matched_questions": [question_text],
                        }
                    elif question_text not in existing["matched_questions"]:
                        existing["matched_questions"].append(question_text)
            loop_elapsed = time.monotonic() - loop_started
            if loop_elapsed > 10.0:
                log.warning(
                    "find_candidate_process_guids_by_questions: %d sequential "
                    "get_scoped_elements call(s) took %.1fs total for technology_type=%r "
                    "survey_kind=%r",
                    len(question_guids), loop_elapsed, technology_type, survey_kind,
                )
        except Exception as exc:
            log.debug("find_candidate_process_guids_by_questions failed: %s", exc)
            return []

        candidates = list(by_guid.values())
        _candidates_cache[cache_key] = (now, candidates)
        return candidates

    def lookup_process(self, name_or_qualified_name: str) -> ProcessLookup:
        """Resolve a Survey Definition to a GUID by its qualified name, and say what happened when it does not resolve.

        One shared resolver, as the Files and database hosts use: `egeria_lookup.guid_by_qualified_name`, Egeria's
        unique-name read (typed `GovernanceActionProcess`). Never by display name, and not through
        `get_governance_definitions_by_name`: an exact-name read hydrates the whole candidate set and times out on a
        grown repository (see `egeria_lookup`'s module docstring).

        A reference without `::` is the definition's bare name; its qualified name is `GovActionProcess::<name>`
        (`qualified_process_name`). The bare name does not resolve in Egeria (verified live 2026-10-30).

        Absent is only Egeria's explicit "No elements found". A read that raised, or answered with an element of
        another type, is recorded in `errors`, never only logged: "could not look up" is a different answer from
        "no such definition", and the caller words them apart."""
        from resource_explorer.egeria_lookup import guid_by_qualified_name

        qualified = qualified_process_name(name_or_qualified_name)
        errors: list[str] = []
        try:
            guid = guid_by_qualified_name(
                self._thread_local_question_lookup_client(), qualified, type_name="GovernanceActionProcess")
        except Exception as exc:  # noqa: BLE001 - recorded and reported to the caller, not swallowed
            errors.append(f"{qualified!r}: {exc}")
            guid = None
        return ProcessLookup(guid, [qualified], errors)

    def find_process_guid_by_name(self, name_or_qualified_name: str) -> str | None:
        """Resolve an explicit Survey Definition reference (bare name or qualifiedName) to a GUID. None when it does
        not resolve; `last_lookup_errors` then holds a read that raised, so a caller can tell "could not look up" from
        "not found" (`lookup_process` returns both together)."""
        found = self.lookup_process(name_or_qualified_name)
        self.last_lookup_errors = found.errors
        return found.guid

    # ── fetch + parse the step graph ────────────────────────────────────────────

    def fetch(self, process_guid: str) -> SurveyDefinition:
        """Fetch a Survey Definition's full step graph from Egeria and parse it.

        Raises UnsupportedSurveyDefinitionError if the graph branches (v1 only
        supports linear step sequences), or SurveyDefinitionReaderError if any
        step is missing executes_at in its Additional Properties.

        D3-cached (docs/survey-question-context-plan.md) — same short TTL as
        the candidates caches, since this is the second live call per
        candidate the plan doc identified as part of the slow-Discovery-load
        root cause.
        """
        now = time.monotonic()
        fetch_key = (self._reader_key(), process_guid)
        cached = _fetch_cache.get(fetch_key)
        if cached is not None and now - cached[0] < _CANDIDATES_CACHE_TTL_SECONDS:
            return cached[1]

        self.connect()
        raw = self._governance_officer.get_governance_action_process_graph(
            guid=process_guid, output_format="JSON"
        )
        if isinstance(raw, str):
            import json as _json
            raw = _json.loads(raw)
        graph = raw.get("elementGraph") if isinstance(raw, dict) and "elementGraph" in raw else raw
        if not isinstance(graph, dict):
            raise SurveyDefinitionReaderError(
                f"Unexpected response fetching Survey Definition graph for {process_guid}"
            )
        survey_def = self._parse_graph(graph)
        _fetch_cache[fetch_key] = (now, survey_def)
        return survey_def

    # ── reconciliation: fix Dr.Egeria's non-idempotent Link commands ──────────

    def reconcile_step_links(self, process_guid: str, survey_group: str, step_keys: list[str], dry_run: bool = False):
        """Delete stale/duplicate step-to-step link relationships against
        live Egeria — see survey_definition_reconciler.py's module docstring
        for the full incident this exists to fix and prevent from
        recurring. Always fetches live (bypasses the fetch cache — reconciling
        against a stale cached graph would be pointless), and busts
        _fetch_cache for process_guid afterward so a subsequent .fetch()
        sees the reconciled graph rather than a pre-reconciliation cache hit.

        Returns a survey_definition_reconciler.ReconcileResult. Safe to call
        repeatedly — a fully-reconciled process is a no-op every time after
        the first."""
        from resource_explorer.surveyors.survey_definition_docs import document_for
        from resource_explorer.surveyors.survey_definition_reconciler import (
            compute_expected_edges,
            diff_links,
            expected_edges_from_document,
        )

        self.connect()
        process_qualified_name = f"GovActionProcess::{survey_group}"

        # Prefer the authored document. A step list can only describe a linear
        # chain, so reconciling against one deletes every branch as stale —
        # and the branch is precisely what a step list cannot express. The
        # linear fallback is for when no document can be found, and it is an
        # approximation, not a definition.
        doc = document_for(survey_group)
        if doc is not None and doc.links:
            expected_edges = expected_edges_from_document(survey_group, doc)
        else:
            log.warning(
                "no authored document found for %s — reconciling against a linear "
                "chain derived from its step list, which would treat any branch as "
                "stale", survey_group,
            )
            expected_edges = compute_expected_edges(survey_group, step_keys)

        try:
            raw = self._governance_officer.get_governance_action_process_graph(guid=process_guid, output_format="JSON")
            if isinstance(raw, str):
                import json as _json
                raw = _json.loads(raw)
            graph = raw.get("elementGraph") if isinstance(raw, dict) and "elementGraph" in raw else raw
            links = (graph or {}).get("processStepLinks") or []
        except Exception as exc:
            from resource_explorer.surveyors.survey_definition_reconciler import ReconcileResult
            return ReconcileResult(process_qualified_name=process_qualified_name, error=str(exc))

        result = diff_links(links, expected_edges, process_qualified_name)

        if not dry_run and result.to_remove:
            metadata_expert = self._connect_metadata_expert()
            # deleteMethod explicit and required — real pyegeria bug, tracked
            # as egeria-python's PYEGERIA_ISSUES.md ISSUE-63: unfixed,
            # metadata_expert.delete_related_elements() routed through
            # OpenMetadataDeleteRequestBody, which declared no deleteMethod
            # field at all, so a {"deleteMethod": "SOFT_DELETE"} key was
            # silently dropped by pydantic validation (PyegeriaModel's
            # extra='ignore') before the request ever reached the server —
            # every relationship this method deletes is a plain step-to-step
            # link with no lineage semantics, so the server's own default
            # (LookForLineage) was always rejected outright
            # (OMAG-COMMON-400-032) with no way to override it. Fixed
            # upstream (pyegeria >=6.0.18.x, confirmed live): the method now
            # routes through DeleteRelationshipRequestBody, which does
            # declare delete_method, so an explicit value here actually
            # reaches the server. The previous workaround here bypassed
            # pyegeria's model validation entirely via a raw
            # _async_make_request() call — no longer needed, removed.
            delete_body = {"class": "DeleteRelationshipRequestBody", "deleteMethod": "SOFT_DELETE"}
            for entry in result.to_remove:
                if not entry.link_guid:
                    continue
                try:
                    metadata_expert.delete_related_elements(entry.link_guid, body=delete_body)
                except Exception as exc:
                    log.warning(
                        "reconcile_step_links: failed to delete %s link %s -> %s (guid=%s): %s",
                        entry.reason, entry.prev_qualified_name, entry.next_qualified_name, entry.link_guid, exc,
                    )
            _bust_fetch_cache(process_guid)

        return result

    # ── reconciliation: each step's executor (engine host case 3, decision 2) ──

    def executor_drift(self, process_guid: str) -> list:
        """REPORT-ONLY: each step's `GovernanceActionExecutor` against decision 2 of the case-3 slice (a step RE runs
        has exactly one executor, to RE's engine, with its step key's request type). Reads live (bypasses the fetch
        cache), writes nothing, never repairs: drift is a finding for a person. A step whose relationships cannot be
        read is reported as such ("unreadable"), never as "missing"."""
        from resource_explorer import engine_host
        from resource_explorer.surveyors.survey_definition_reconciler import (
            ExecutorDrift,
            diff_executors,
            executors_from_related,
        )

        _bust_fetch_cache(process_guid)
        survey_def = self.fetch(process_guid)
        expert = self._connect_metadata_expert()
        out: list = []
        for step in survey_def.steps:
            key = step.re_analysis_step or step.qualified_name.rsplit("::", 1)[-1]
            try:
                executors = executors_from_related(expert.get_all_related_elements(step.guid, graph_query_depth=1))
            except Exception as exc:  # noqa: BLE001 -- "could not tell" is its own finding, never "missing"
                out.append(ExecutorDrift(step.qualified_name, key, "unreadable",
                                         " ".join(f"{type(exc).__name__}: {exc}".split())[:200]))
                continue
            out.extend(diff_executors(step.qualified_name, key, step.executes_at, executors,
                                      engine_qualified_name=engine_host.ENGINE_QUALIFIED_NAME,
                                      expected_request_type=engine_host.request_type_for_step(key)))
        return out

    # ── reconciliation: ScopedBy links vs. an authored document ─────────────

    def get_live_scopes(self, process_guid: str) -> list[dict]:
        """The raw `ClassificationExplorer.get_scopes()` result for one
        Survey Definition's process element — every element it is currently
        `ScopedBy`, unfiltered. Always fetches live; scope links are
        precisely what `scripts/reconcile_survey_definition_scopes.py` exists
        to catch drifting, so a cached answer would defeat the point.

        Must be called from the main thread — same
        `_connect_classification_explorer` client
        `find_candidate_process_guids_by_questions` uses, and pyegeria
        ISSUE-96 (see `__init__`'s note on `_thread_local_explorer`) means a
        `ClassificationExplorer` shared across threads/event loops fails
        deterministically. Raises whatever the client raises — this method
        does not catch and does not decide what a failure means; the caller
        (the script) is what turns "could not read scopes" into a
        per-definition report line rather than a silent empty result."""
        self.connect()
        client = self._connect_classification_explorer()
        results = client.get_scopes(process_guid, page_size=1000)
        if isinstance(results, str):
            return []  # pyegeria's "No elements found" convention
        return results or []

    def remove_scope(self, term_guid: str, process_guid: str) -> None:
        """Detach one ScopedBy link — `ClassificationExplorer.
        clear_scope_from_element(scoped_by_guid=term_guid,
        element_guid=process_guid)`, the same live call verified working on
        2026-09-13 (docs/Backlog.md, "Superseded Question term"). Main-thread
        only, same as `get_live_scopes`. Raises on failure; the caller counts
        it, it does not swallow it."""
        self.connect()
        client = self._connect_classification_explorer()
        client.clear_scope_from_element(term_guid, process_guid)

    def _parse_graph(self, graph: dict) -> SurveyDefinition:
        """Side-effect-free: turns a raw graph JSON dict into a SurveyDefinition.

        This is the unit-testable seam — exercise it directly with canned JSON,
        no live server needed.
        """
        process_element = graph.get(_PROCESS_KEY, {}) or {}
        process_header = process_element.get("elementHeader", {})
        process_props = process_element.get("properties", {})
        process_guid = process_header.get("guid", "")
        process_qn = process_props.get("qualifiedName", "")
        process_additional = process_props.get("additionalProperties", {}) or {}

        # Build a node index (guid -> raw step element) from firstProcessStep +
        # nextProcessSteps, and an edge index (guid -> [next guids]) from
        # processStepLinks — see the module-level shape comment above.
        nodes: dict = {}
        first_wrapper = graph.get(_FIRST_STEP_KEY)
        first_guid = None
        if first_wrapper is not None:
            first_element = first_wrapper.get("element") or {}
            first_guid = first_element.get("elementHeader", {}).get("guid") or None
            if first_guid:
                nodes[first_guid] = first_element

        for step_element in graph.get(_NEXT_STEPS_KEY) or []:
            guid = step_element.get("elementHeader", {}).get("guid")
            if guid:
                nodes[guid] = step_element

        # Guards were previously read and discarded here: the payload carries
        # `guard` and `mandatoryGuard` on every link, a live read of Analysis
        # Survey returns them on all 9, and only the two GUIDs were kept. They
        # are what a coordinator routes on, so they are kept now — see StepLink.
        edges: dict = {}
        links: list = []
        for link in graph.get(_LINKS_KEY) or []:
            prev_guid = (link.get("previousProcessStep") or {}).get("guid")
            next_guid = (link.get("nextProcessStep") or {}).get("guid")
            if prev_guid and next_guid:
                edges.setdefault(prev_guid, []).append(next_guid)
                links.append(StepLink(
                    previous_guid=prev_guid, next_guid=next_guid,
                    guard=link.get("guard") or "",
                    mandatory_guard=bool(link.get("mandatoryGuard") or False),
                ))

        ordered_guids, unreachable = _walk_graph(edges, nodes, first_guid, process_qn)
        steps: list = []
        for guid in ordered_guids:
            node = nodes.get(guid)
            if node is None:
                raise SurveyDefinitionReaderError(
                    f"Survey Definition {process_qn} references step guid {guid} "
                    "with no corresponding node in the graph"
                )
            steps.append(self._parse_step(node))

        if unreachable:
            # Declared in the process but with no path from its first step.
            # Previously these were dropped without a word, because the walk
            # simply never arrived at them — a step present in the definition,
            # absent from every report, and indistinguishable from one that had
            # never been authored.
            log.warning(
                "Survey Definition %s declares %d step(s) unreachable from its "
                "first step: %s — they are recorded on the definition and will "
                "not run",
                process_qn, len(unreachable),
                ", ".join(sorted(unreachable)),
            )

        return SurveyDefinition(
            process_guid=process_guid,
            display_name=process_props.get("displayName", process_qn),
            qualified_name=process_qn,
            supported_technology_type=process_additional.get("supported_technology_type"),
            steps=steps,
            links=links,
            unreachable_step_guids=sorted(unreachable),
            description=process_props.get("description", ""),
            survey_kind=process_additional.get("survey_kind"),
            perspectives=_split_perspectives(process_additional.get("perspectives")),
        )

    def _parse_step(self, element: dict) -> SurveyStep:
        header = element.get("elementHeader", {})
        # The process element uses "properties"; a process step's own properties
        # live under "processStepProperties" instead — confirmed live, see the
        # module-level shape comment above.
        props = element.get("processStepProperties") or element.get("properties") or {}
        qn = props.get("qualifiedName", "")
        additional = props.get("additionalProperties", {}) or {}

        executes_at = additional.get("executes_at")
        if not executes_at:
            raise SurveyDefinitionReaderError(
                f"Step {qn or header.get('guid', '<unknown>')} has no executes_at "
                "in its Additional Properties — ambiguous, refusing to guess"
            )

        executor = (element.get("governanceActionExecutor")
                    or props.get("governanceActionExecutor") or {})
        return SurveyStep(
            guid=header.get("guid", ""),
            display_name=props.get("displayName", qn),
            qualified_name=qn,
            additional_properties=additional,
            executes_at=executes_at,
            re_analysis_step=additional.get("re_analysis_step"),
            supported_technology_type=additional.get("supported_technology_type"),
            description=props.get("description", ""),
            produced_guards=list(props.get("producedGuards") or []),
            wait_time=props.get("waitTime"),
            ignore_multiple_triggers=bool(props.get("ignoreMultipleTriggers") or False),
            request_parameters=dict(executor.get("requestParameters") or {}),
            action_targets=list(executor.get("actionTargets")
                                or element.get("actionTargets") or []),
            executor_present=bool(executor),
        )


def _walk_graph(edges: dict, nodes: dict, first_guid, process_qn: str) -> tuple:
    """(ordered step guids, unreachable step guids) for a definition's graph.

    Replaces a walk that followed one outgoing edge and raised on a second.
    That refusal was honest while nothing could run a branch, but it made a
    branching definition unreadable as well as unrunnable — it could not be
    displayed, diffed against its document, or repaired.

    The order is topological with ties broken by discovery order from the first
    step, following edges as declared. For a linear chain the topological order
    is unique, so this returns exactly what the old walk did — verified against
    all eight live definitions.

    Cycles still raise. A cycle is not a shape RE declines to run; it is one
    that cannot be ordered at all, and any order would be a fiction.
    """
    reachable: list = []
    seen: set = set()
    queue = [first_guid] if first_guid else []
    while queue:
        guid = queue.pop(0)
        if guid in seen:
            continue
        seen.add(guid)
        reachable.append(guid)
        queue.extend(edges.get(guid, []))

    discovery = {guid: i for i, guid in enumerate(reachable)}
    incoming = {guid: [] for guid in reachable}
    for guid in reachable:
        for nxt in edges.get(guid, []):
            if nxt in incoming:
                incoming[nxt].append(guid)

    remaining = {guid: list(deps) for guid, deps in incoming.items()}
    ordered: list = []
    while remaining:
        ready = sorted((g for g, deps in remaining.items()
                        if not any(d in remaining for d in deps)),
                       key=lambda g: discovery[g])
        if not ready:
            raise UnsupportedSurveyDefinitionError(
                f"Survey Definition {process_qn} contains a cycle among step guid(s) "
                f"{sorted(remaining)} — no execution order exists"
            )
        for guid in ready:
            ordered.append(guid)
            del remaining[guid]

    unreachable = {g for g in nodes if g not in seen and g != first_guid}
    return ordered, unreachable
