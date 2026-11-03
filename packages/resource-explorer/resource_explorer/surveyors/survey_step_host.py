"""RE's host side of a user step (extensibility brief 2c, 2c'): `SurveyStepHost`, the brief's `ExternalStep`.

A user step is an installable package outside RE: a plain `run(inputs) -> results` (level 1) and a
`StepManifest` that the Analytic Library's `SurveyStep` wrapper runs (level 2), found through the
`pyegeria.analytics` entry point. A Survey Definition (level 3) names it by `re_analysis_step`, like any built-in.

This module puts such steps BESIDE a resource type's step registry, never in it by hand:

- `attach()` reads the catalog entries GENERATED from manifests (`provided_by` in analysis_catalog.yaml, written
  by scripts/generate_external_step_entries.py) and the installed manifests, and fills, per step, the adapter's
  `re_analysis_steps` (so the executor dispatches it exactly as a built-in, inside `step_cost_observer.observe`,
  where the cost vector and the OpenLineage run event are taken), the step registry (`StepInfo`), the
  analysis->step map, and the results/headline readers (the one generic reader per annotation type,
  `annotation_readers`).
- An entry whose package is not installed here still attaches, as `NotInstalledStep`: running it fails with the
  sentence naming the package, rather than the card saying "no mapped survey step".
- An installed step with no catalog entry is attached as a runnable step and logged: run the generator.

**The boundary, honestly.** A step is in-process, trusted code. RE enforces three things: no connection to the
resource; reads of RE's stored rows only through RE's TYPED read methods (`StepOds`: `row_count_snapshots`,
`commit_proofs`, ... with parameterised SQL RE owns; a step sends no SQL text and has no `query`), executed by a
separate thread on a connection that cannot write (one statement per rolled-back READ ONLY transaction on Postgres;
`mode=ro` + `query_only` + a read-only authorizer on SQLite), with no connection, registry or URL in the step's
hands; and no Egeria identity (its code runs inside `egeria_clients.no_identity()`, so `current_principal()`
raises). It is NOT a sandbox: the thread list, the call stack and the process environment still reach RE's
internals. Arbitrary SQL from a step would need a read-only database role (a follow-up for the owner: ops/DDL).
Install only steps you would run as Resource Explorer.

What the host gives the wrapper: the resource, that read-only ODS reader, and a store that
keeps the annotations with their envelope in `additionalProperties`, in their own table `step_annotations`
(`ProjectRegistry.record_step_annotations`).
It never gives a password: the runner kwargs (`db_user`, `db_pwd`) stop here. It opens no connection to the
resource yet, so a step declaring the `connection` input is refused by the wrapper with that sentence.

A run's publish, when the resource has an Egeria project, goes through the adapter's existing `publish` and its
refusals (`person_write_refusal`, and the publisher's own zone rules), with no publish code here.

**Dispatched by Egeria (engine host, step 3).** `library_steps()` is the seam the engine host dispatches through:
request type -> `LibraryStep`, built from the installed manifests at `attach()` (the same discovery that attached the
runners), never from a list in RE's code. Each step declares in its manifest its request type, the resource kind it
targets (`resource_types`: database, and since Files build step 6 filesystem), the annotation type it emits (Egeria's type name) and the survey
guard that applies when it has nothing to derive. A step may take no built-in step's name or request type
(`_BUILTIN`: every key of the resource type's step registry). A step is admitted only when this host can run and publish it
unattended: it opens no connection (no `connection` input: the engine host has no person's credential) and emits an
annotation type the report path can carry (`PUBLISHABLE_ANNOTATION_TYPES`); otherwise it is refused, with the
sentence, in `library_step_refusals()`. The engine host runs the step through the synthetic-step executor, so its
annotations land in `step_annotations` as for any run, then puts them on the SurveyReport with
`LibraryStep.report_annotations` and completes from the proof (engine_host.py, E4).

Adding a step that Egeria can dispatch, as its author:
  1. write `run(inputs)` and a `StepManifest` (with `request_type`, `nothing_to_derive_guard`) in its own package,
     exposed through the `pyegeria.analytics` entry point;
  2. install it where RE's worker runs, and run `scripts/generate_external_step_entries.py` (the catalog card) and
     `scripts/generate_engine_host_declaration.py` (its Egeria declaration, `docs/dr-egeria/engine-host/library/`);
  3. have the owner run that declaration (`resource-explorer dr-egeria run engine-host/library/<step>.md`).
Nothing in RE's code names the step.
"""
from __future__ import annotations

import contextlib
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

#: Problems found loading manifests, per resource type, for the honest sentence a page or the CLI can show.
LOAD_ERRORS: dict[str, str] = {}

#: The annotation types the engine host's report path can publish a library step's results as (with the envelope
#: in `additionalProperties`, the owner's interim carrier). Egeria's own type names. A ClassificationAnnotation is a
#: PROPOSAL (`contentStatus` DRAFT, its `candidateClassifications`), never a classification on the element: facts come
#: only through Curate (owner, 2026-10-15: the two channels stay separate).
PUBLISHABLE_ANNOTATION_TYPES = ("ResourceMeasureAnnotation", "ClassificationAnnotation")
#: Where the annotation types Egeria is wanted to support are tracked, named in the "not offered to Egeria" warning.
WANTED_TYPES_DOC = "docs/design-notes/EGERIA-ANNOTATION-TYPES-WANTED.md"

#: Egeria's `contentStatus` for a library step's proposal: the content asks a curator to decide.
PROPOSAL_CONTENT_STATUS = "DRAFT"

#: The result states that are a derivation. A step none of whose results is one of these had nothing to derive.
DERIVED_STATES = ("measured", "nothing_found")

#: Per resource type: the admitted library steps by request type, and the refused ones by step name.
_LIBRARY: dict[str, dict] = {}
_REFUSED: dict[str, dict[str, str]] = {}

#: Per resource type: RE's own step keys, captured at the FIRST attach, before any user step is added to the
#: registry. A user step may take none of these names (nor `re-<name>` as its request type): it would replace the
#: built-in's runner and its analysisStep.
_BUILTIN: dict[str, frozenset[str]] = {}

#: Per resource type: the user steps the last attach() attached, so the next one can remove those it no longer has.
_ATTACHED: dict[str, set[str]] = {}
#: The analysis ids `attach` put into the results/step/kinds maps, per resource type (so a later attach can take them out).
_ATTACHED_IDS: dict[str, set[str]] = {}
#: {entity type: {step key: provided_by}} for catalogued steps whose package is NOT installed (they attach as
#: `NotInstalledStep`). Set by `attach`, so the engine host and the cards ask one place.
_MISSING: dict[str, dict[str, dict]] = {}


@dataclass(frozen=True)
class LibraryStep:
    """One Analytic Library step as the engine host sees it: what to claim, what to run, what to publish."""
    request_type: str
    step_key: str
    entity_type: str
    annotation_type: str
    nothing_to_derive_guard: str
    description: str
    manifest: object = field(repr=False, compare=False)
    provided_by: dict = field(default_factory=dict, compare=False)

    @staticmethod
    def derived(records: list[dict]) -> bool:
        """At least one result is a derivation (`DERIVED_STATES`)."""
        return any(_state_of(r) in DERIVED_STATES for r in records)

    @staticmethod
    def nothing_to_derive_reason(records: list[dict]) -> str:
        """The step's own reasons, deduplicated, for the completion message."""
        reasons = []
        for r in records:
            why = str(r.get("explanation") or (r.get("additionalProperties") or {}).get("resultStateReason") or "")
            if why and why not in reasons:
                reasons.append(why)
        if not records:
            return "the step returned no results"
        return "; ".join(reasons[:3]) + (f" (and {len(reasons) - 3} more)" if len(reasons) > 3 else "")

    def report_annotations(self, records: list[dict]) -> list:
        """The step's results (`StepAnnotation.to_record()` dicts, as stored) as the annotations the SurveyReport
        carries: Egeria's annotation type, the step as `analysisStep`, the step's display name as `annotationType`
        (which result this is), the measured values in `resourceProperties` and the envelope in
        `additionalProperties`. Each is identified by step and scope (`check_name`, `item_key`), so a re-publish
        of the same run converges on the same elements."""
        from resource_explorer.surveyors.survey_report import ClassificationAnnotation, ResourceMeasureAnnotation

        m = self.manifest
        cannot = [t for t in _annotation_types(m) if t not in PUBLISHABLE_ANNOTATION_TYPES]
        if cannot:
            raise ValueError(f"step '{self.step_key}' emits {', '.join(cannot)}, which the engine host cannot "
                             "publish")
        name = getattr(m, "display_name", "") or self.step_key.replace("_", " ").title()
        out = []
        for r in records:
            scope = str(r.get("scope") or "")
            state = _state_of(r)
            measure = str(r.get("measure") or "")
            other = bool(measure) and measure != self.step_key
            summary = str(r.get("summary") or "") or f"{scope}: {state.replace('_', ' ')}"
            # Each declared measure is its own annotation: its own annotationType and its own identity (check_name),
            # never published under the step's name (engine host step 4 ruling). Egeria's type is the one the
            # manifest declares for the measure (`measure_annotation_types`); by default the step's own,
            # ResourceMeasureAnnotation, which the native survey itself uses for these counters.
            if _annotation_type_for(m, measure) == "ClassificationAnnotation":
                # A proposal, in Egeria's own fields: the classification in `classificationName` (one per annotation),
                # the measured values in `properties`, the envelope in additionalProperties. Never a classification
                # on the element.
                proposed = [str(c) for c in (r.get("candidateClassifications") or [])]
                if len(proposed) > 1:
                    raise ValueError(f"step '{self.step_key}', '{scope}': proposes {len(proposed)} classifications; "
                                     "Egeria's ClassificationAnnotation names one (classificationName)")
                out.append(ClassificationAnnotation(
                    summary=summary, analysis_step=self.step_key,
                    annotation_type_name=measure_display_name(measure) if other else name,
                    explanation=str(r.get("explanation") or ""),
                    classification_name=proposed[0] if proposed else "",
                    classification_properties=dict(r.get("resourceProperties") or {}),
                    additional_properties=dict(r.get("additionalProperties") or {}),
                    content_status=PROPOSAL_CONTENT_STATUS,
                    check_name=measure if other else self.step_key, item_key=scope))
                continue
            out.append(ResourceMeasureAnnotation(
                summary=summary, analysis_step=self.step_key,
                annotation_type_name=measure_display_name(measure) if other else name,
                explanation=str(r.get("explanation") or ""),
                resource_properties=dict(r.get("resourceProperties") or {}),
                additional_properties=dict(r.get("additionalProperties") or {}),
                check_name=measure if other else self.step_key, item_key=scope))
        return out


def _annotation_type_for(manifest, measure: str) -> str:
    """The Egeria type a measure's results are published as (`StepManifest.annotation_type_for`, read defensively: a
    manifest from an older library has no per-measure types)."""
    per = getattr(manifest, "measure_annotation_types", None) or {}
    return per.get(measure or manifest.name, manifest.annotation_type)


def _annotation_types(manifest) -> list[str]:
    """Every annotation type a step can emit, the step's own first."""
    out = [manifest.annotation_type]
    for t in (getattr(manifest, "measure_annotation_types", None) or {}).values():
        if t not in out:
            out.append(t)
    return out


def measure_step_key(step_key: str, measure: str) -> str:
    """Where a measure's results are stored: the step's own under the step key, any other as `<step>:<measure>`."""
    return step_key if not measure or measure == step_key else f"{step_key}:{measure}"


def measure_display_name(measure: str) -> str:
    """`writes_since_statistics_reset` -> `Writes Since Statistics Reset`, the annotationType it is published under."""
    return measure.replace("_", " ").title()


def _state_of(record: dict) -> str:
    return str((record.get("additionalProperties") or {}).get("resultState") or record.get("result_state") or "")


def _builtin_refusal(name: str, request_type: str, builtin: frozenset[str]) -> str:
    """Why a user step collides with a built-in step, or ""."""
    from resource_explorer.engine_host import request_type_for_step

    if name in builtin:
        return f"is named '{name}', which is one of Resource Explorer's built-in steps"
    taken = {request_type_for_step(k): k for k in builtin}
    if request_type in taken:
        return (f"declares request type '{request_type}', which belongs to Resource Explorer's built-in step "
                f"'{taken[request_type]}'")
    return ""


def _admit(entity_type: str, found: dict, provided_by: dict[str, dict]) -> tuple[dict, dict[str, str]]:
    builtin = _BUILTIN.get(entity_type, frozenset())
    admitted: dict[str, LibraryStep] = {}
    refused: dict[str, str] = {}
    by_type: dict[str, list[str]] = {}
    for name, m in sorted(found.items()):
        if why := _builtin_refusal(name, m.request_type, builtin):
            refused[name] = why
            continue
        if "connection" in (m.inputs or []):
            refused[name] = ("declares the 'connection' input; the engine host opens no connection to a resource "
                             "(it holds no person's credential), so Egeria is not offered this step")
            continue
        cannot = [t for t in _annotation_types(m) if t not in PUBLISHABLE_ANNOTATION_TYPES]
        if cannot:
            emits = _annotation_types(m)
            refused[name] = (f"emits {', '.join(emits)}, of which {', '.join(cannot)} cannot be published by the "
                             f"engine host (it publishes {', '.join(PUBLISHABLE_ANNOTATION_TYPES)} only); "
                             f"see {WANTED_TYPES_DOC}")
            continue
        rt = m.request_type
        by_type.setdefault(rt, []).append(name)
        admitted[rt] = LibraryStep(rt, name, entity_type, m.annotation_type, m.nothing_to_derive_guard,
                                   m.description, m, dict(provided_by.get(name) or {}))
    for rt, names in by_type.items():
        if len(names) > 1:
            admitted.pop(rt, None)
            for n in names:
                refused[n] = f"declares request type '{rt}', as do {', '.join(x for x in names if x != n)}"
    for name, why in refused.items():
        log.warning("engine host: library step '%s' not offered to Egeria: %s", name, why)
    return admitted, refused


def library_steps(entity_type: str = "database") -> dict[str, LibraryStep]:
    """{request type: LibraryStep} for every installed, admitted Analytic Library step that targets `entity_type`,
    from the discovery `attach()` last made (refresh() discovers again)."""
    # Importing the adapter attaches its user steps (once, at import).
    if entity_type == "database":
        from resource_explorer.surveyors.database import survey_definition_adapter  # noqa: F401
    elif entity_type == "filesystem":
        from resource_explorer.surveyors.filesystem import survey_definition_adapter  # noqa: F401,F811
    return dict(_LIBRARY.get(entity_type, {}))


def library_step_refusals(entity_type: str = "database") -> dict[str, str]:
    """{step name: why it is not offered to Egeria} for installed steps the engine host does not admit."""
    library_steps(entity_type)
    return dict(_REFUSED.get(entity_type, {}))


class StepNotInstalled(RuntimeError):
    """The catalog names a step whose package is not installed in this environment."""


#: What a step may read: RE's typed read methods of the Analytic Library's ODS reader, each a fixed, parameterised
#: SELECT that RE owns. A step sends no SQL text: free SQL from a step needs a read-only database role (a follow-up
#: for the owner: ops/DDL), and validating SQL by its text cannot be made safe. `query` is NOT here.
STEP_ODS_METHODS = ("row_count_snapshots", "commit_proofs", "catalogue_scope_events", "resource_scope_events",
                    "outbox_status_counts", "table_activity_snapshots", "table_keys",
                    # a file system's walk rows (Files build step 6: fs_change_rates, fs_duplicates, fs_activity,
                    # fs_permissions): names, sizes, times, owner, mode and hash, never content
                    "filesystem_walk_runs", "filesystem_walk_files", "filesystem_hash_matches",
                    # the noise directories a walk did not enter, by name (Files build step 7: fs_layout_signals)
                    "filesystem_walk_not_walked",
                    # the tables and columns of the latest column survey, with types (schema_comparison, CMP1)
                    "schema_inventory")

#: How long a step's read may wait for its answer.
READ_TIMEOUT_SECONDS = 120


def _plain(value):
    """`value` as an exact builtin `str` or `int`, or None. Anything else raises TypeError. Run on the STEP's side
    (a str or int subclass is converted there, so its methods run in the step's thread) and checked again, exact
    types only, on the reader thread's side."""
    if value is None or type(value) in (str, int):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        out = str(value)
    elif isinstance(value, int):
        out = int(value)
    else:
        raise TypeError(f"a step's read takes plain strings and integers, not {type(value).__name__}")
    if type(out) not in (str, int):
        raise TypeError(f"a step's read takes plain strings and integers, not {type(value).__name__}")
    return out


def _plain_request(item) -> tuple:
    """The reader thread's check of one request, exact types only and no method of the step's objects called:
    (name, args, kwargs, reply). Raises TypeError, or returns None when the reply object itself is not a plain
    queue (nothing can be answered then)."""
    import queue

    if type(item) is not tuple or len(item) != 4:
        return None
    name, args, kwargs, reply = item
    if type(reply) is not queue.Queue:
        return None
    if type(name) is not str or type(args) is not tuple or type(kwargs) is not dict:
        raise TypeError("a request is (name, tuple of plain values, dict of plain values)")
    for v in (*args, *dict.values(kwargs)):
        if not (v is None or type(v) in (str, int)):
            raise TypeError(f"a step's read takes plain strings and integers, not {type(v).__name__}")
    for k in dict.keys(kwargs):
        if type(k) is not str:
            raise TypeError("a request's keyword names are plain strings")
    return name, args, kwargs, reply


def _sqlite_authorizer(action, *_args):
    """Only reading is authorized on the reader's SQLite connection (ATTACH, PRAGMA and every write are denied)."""
    import sqlite3

    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
               getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def _open_read_only(database_url: str):
    """(connection, is_postgres) that cannot write. SQLite: opened `mode=ro`, autocommit, `query_only=ON`, and an
    authorizer that allows reading only. Postgres: a read-only session with autocommit OFF; `_execute_read_only`
    also runs each query in its own `READ ONLY` transaction, through a server-side cursor, and rolls it back."""
    if database_url.startswith("postgresql"):
        import psycopg2

        conn = psycopg2.connect(database_url)
        conn.set_session(readonly=True, autocommit=False)
        return conn, True
    import sqlite3

    path = database_url.removeprefix("sqlite:///")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_sqlite_authorizer)
    conn.row_factory = sqlite3.Row
    return conn, False


def _execute_read_only(conn, is_postgres: bool, sql: str, params: tuple) -> list[dict]:
    """Run ONE statement on a read-only connection (defence in depth under the typed methods, whose SQL RE owns).
    Postgres: its own transaction, `SET TRANSACTION READ ONLY` first, the query through a named (server-side)
    cursor, which Postgres declares as `DECLARE ... CURSOR FOR <one query>` and so refuses a second statement, and
    always rolled back. SQLite: `execute` runs one statement only."""
    if is_postgres:
        import uuid

        import psycopg2.extras

        try:
            with conn.cursor() as setup:
                setup.execute("SET TRANSACTION READ ONLY")
            with conn.cursor(name=f"re_step_{uuid.uuid4().hex}",
                             cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql.replace("?", "%s"), params)
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.rollback()
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _serve(database_url: str, requests, ready) -> None:
    """The reader's thread: owns the read-only connection and a reader over it, and answers one typed read at a
    time (`STEP_ODS_METHODS` only) with plain data: rows, or an error's words, never an exception object (whose
    traceback would lead back to this frame)."""
    import queue

    from trellis_analytics_re.ods import OdsReader

    try:
        conn, is_pg = _open_read_only(database_url)
    except Exception as exc:  # noqa: BLE001 - said to the host, which raises it
        ready.put(f"could not open the registry read-only: {type(exc).__name__}: {exc}")
        return

    class _ThreadReader(OdsReader):
        """RE's typed read methods over this thread's connection. Lives only in this frame."""

        def __init__(self):                 # deliberately not OdsReader.__init__: no `url`
            self._is_pg = is_pg

        def query(self, sql: str, params: tuple = ()) -> list[dict]:
            return _execute_read_only(conn, is_pg, sql, params)

    reader = _ThreadReader()
    ready.put("")
    try:
        while True:
            item = requests.get()
            if item is None:
                return
            try:
                checked = _plain_request(item)
            except TypeError as exc:
                reply = item[3] if type(item) is tuple and len(item) == 4 else None
                if type(reply) is queue.Queue:
                    reply.put(("error", f"TypeError: {exc}"))
                continue
            if checked is None:
                continue                    # no plain reply queue: nothing is run and nothing can be answered
            name, args, kwargs, reply = checked
            try:
                if name not in STEP_ODS_METHODS:
                    raise PermissionError(f"'{name}' is not one of the reads a step may make "
                                          f"({', '.join(STEP_ODS_METHODS)})")
                reply.put(("ok", getattr(reader, name)(*args, **kwargs)))
            except Exception as exc:  # noqa: BLE001
                reply.put(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


class StepOds:
    """What a step's `inputs.ods` is: RE's typed read methods (`STEP_ODS_METHODS`) and nothing else. No `query`,
    no registry, no URL, no connection: each method sends its name and arguments to the reader's own thread, which
    owns the read-only connection. Trusted in-process code can still reach RE internals (the thread list, the call
    stack): this narrows what a step is handed, it is not a sandbox."""
    __slots__ = STEP_ODS_METHODS

    def __init__(self, requests, allow_slug_lists: bool = False):
        for name in STEP_ODS_METHODS:
            object.__setattr__(self, name, _remote(requests, name, allow_slug_lists))

    def __setattr__(self, name, value):
        raise AttributeError("a step's ODS reader is read-only")

    def __repr__(self) -> str:
        return "<read-only ODS>"


def _slug_first(name: str) -> bool:
    """Is the first parameter of this typed read a resource's slug (so a set step may pass a list of them)?"""
    import inspect

    from trellis_analytics_re.ods import OdsReader

    params = list(inspect.signature(getattr(OdsReader, name)).parameters)
    return len(params) > 1 and params[1] == "slug"


#: The most members one list-of-slugs read may name.
MAX_SET_SLUGS = 500


def _remote(requests, name, allow_slug_lists: bool = False):
    import queue

    from trellis_analytics_re.ods import OdsReader, OdsUnreadable

    slug_first = allow_slug_lists and _slug_first(name)

    def call(*args, **kwargs):
        if slug_first and args and isinstance(args[0], (list, tuple)):
            # A SET step's read for every member (the only shape that is not a plain value): a bounded list of plain slug
            # strings, one plain read per slug, answered as {slug: rows}. The reader thread still sees only strings and
            # integers. Any other list, and any list for a step about one resource, is a TypeError as before.
            if len(args[0]) > MAX_SET_SLUGS:
                raise TypeError(f"a set step's read takes at most {MAX_SET_SLUGS} slugs, not {len(args[0])}")
            if not all(type(x) is str for x in args[0]):
                raise TypeError("a set step's list of members is plain slug strings")
            return {_plain(slug): call(slug, *args[1:], **kwargs) for slug in args[0]}
        args = tuple(_plain(a) for a in args)
        kwargs = {str(k): _plain(v) for k, v in kwargs.items()}
        reply: queue.Queue = queue.Queue(maxsize=1)
        if requests.closed:
            raise OdsUnreadable("the reader is closed: the step's run has ended")
        requests.put((name, args, kwargs, reply))
        try:
            status, payload = reply.get(timeout=READ_TIMEOUT_SECONDS)
        except queue.Empty:
            raise OdsUnreadable(f"the registry did not answer within {READ_TIMEOUT_SECONDS}s") from None
        if status != "ok":
            raise OdsUnreadable(f"the registry did not answer: {payload}")
        return payload

    call.__name__ = name
    call.__doc__ = getattr(OdsReader, name).__doc__
    return call


class _Requests:
    """The queue a step's reader sends its reads on, with a flag the host sets when the run ends."""
    __slots__ = ("_q", "closed")

    def __init__(self):
        import queue

        self._q, self.closed = queue.Queue(), False

    def put(self, item) -> None:
        self._q.put(item)

    def get(self):
        return self._q.get()


@contextmanager
def step_reader(database_url: str, *, allow_slug_lists: bool = False):
    """A step's read-only ODS (`StepOds`) for the duration of the block. The connection is opened and used only in
    the reader's own thread; the block's end stops the thread and closes it, and the reader refuses after that."""
    import queue
    import threading

    requests = _Requests()
    ready: queue.Queue = queue.Queue(maxsize=1)
    thread = threading.Thread(target=_serve, args=(database_url, requests, ready), name="re-step-reader",
                              daemon=True)
    thread.start()
    problem = ready.get(timeout=READ_TIMEOUT_SECONDS)
    try:
        if problem:
            from trellis_analytics_re.ods import OdsUnreadable

            raise OdsUnreadable(problem)
        yield StepOds(requests, allow_slug_lists)
    finally:
        requests.closed = True
        requests.put(None)
        thread.join(timeout=READ_TIMEOUT_SECONDS)


def _without_identity(code, done=None):
    """The step's own code, run with no Egeria identity (`egeria_clients.no_identity`). `done`, when given, is called
    as soon as the code returns (it closes the step's reader), before the host stores anything."""
    from resource_explorer.egeria_clients import no_identity

    def run(inputs):
        try:
            with no_identity():
                return code(inputs)
        finally:
            if done is not None:
                done()

    return run


class SurveyStepHost:
    """The runner the executor calls for a user step: `(entity, registry, **runner_kwargs) -> dict`."""

    def __init__(self, manifest, entity_type: str, provided_by: dict | None = None):
        self.manifest = manifest
        self.entity_type = entity_type
        self.provided_by = dict(provided_by or {})

    def __call__(self, entity, registry, **runner_kwargs) -> dict:
        from trellis_analytics.step import HostContext, SurveyStep

        step = self.manifest.name
        if getattr(self.manifest, "subject", "resource") == "set":
            raise StepNotInstalled(f"step '{step}' is about a set of resources: run it from a work list, not on one "
                                   f"{self.entity_type}")
        # The run's own time when the executor stamped one (a database scan's), so the annotations name the run.
        run = runner_kwargs.get("scan_surveyed_at") or datetime.utcnow().isoformat()

        def store(annotations) -> int:
            # One stored set per measure: the step's own under its name, each further declared measure under
            # `<step>:<measure>`, so a reader of the step's results never sees another measure's rows as its own.
            measured_at = annotations[0].envelope.measured_at if annotations else run
            groups: dict[str, list] = {step: []}
            for a in annotations:
                groups.setdefault(measure_step_key(step, getattr(a, "measure", "")), []).append(a)
            return sum(registry.record_step_annotations(
                self.entity_type, entity.slug, key, run=run, measured_at=measured_at,
                annotations=[a.to_record() for a in group],
                package=self.provided_by.get("package", ""), producer_version=self.manifest.version)
                for key, group in groups.items())

        with contextlib.ExitStack() as stack:
            ods = stack.enter_context(step_reader(registry.database_url))
            # `source_report`: the previous process step's report as plain data, read by the engine host (step 4).
            host = HostContext(resource_type=self.entity_type, slug=entity.slug, producing_run=run,
                               ods=ods, connect=None, store=store,
                               source_report=runner_kwargs.get("source_report"))
            # The step's code runs with no Egeria identity, and its reader is closed when the code returns; the
            # store and anything after it run as the host does.
            code = SurveyStep(self.manifest).code
            annotations = SurveyStep(self.manifest, code=_without_identity(code, stack.close)).run(host)
        counts: dict[str, int] = {}
        for a in annotations:
            counts[a.envelope.result_state.value] = counts.get(a.envelope.result_state.value, 0) + 1
        return {
            "step_annotations": [a.to_record() for a in annotations],
            "annotation_count": len(annotations),
            "state_counts": counts,
            "producer": {"step": step, "version": self.manifest.version,
                         "package": self.provided_by.get("package", "")},
        }


#: The brief's name for the adapter; `SurveyStepHost` is the name the three-levels framing gives it.
ExternalStep = SurveyStepHost


class NotInstalledStep:
    """The runner for a catalogued step whose package is absent: it says so, naming the package."""

    def __init__(self, provided_by: dict, step_key: str):
        self.provided_by = dict(provided_by)
        self.step_key = step_key

    def __call__(self, entity, registry, **_):
        raise StepNotInstalled(not_installed_sentence(self.step_key, self.provided_by))


def not_installed_sentence(step_key: str, provided_by: dict) -> str:
    """The one sentence for a catalogued step whose package is absent: the run's error and the card's reason."""
    p = provided_by or {}
    return (f"step not installed here: '{step_key}' comes from package {p.get('package') or '(unnamed)'} "
            f"{p.get('version', '')}; install it into this environment (and the Prefect worker's, when one runs "
            "steps): installing it is registering it")


def missing_library_steps(entity_type: str = "database") -> dict[str, dict]:
    """{step key: provided_by} for every catalogued Analytic Library step whose package is not installed here,
    from the discovery `attach()` last made."""
    library_steps(entity_type)       # makes sure the adapter has attached
    return {k: dict(v) for k, v in _MISSING.get(entity_type, {}).items()}


def installed_library_steps(entity_type: str = "database") -> list[str]:
    """Sorted step keys of the Analytic Library steps installed here that target `entity_type`: every step `attach()`
    attached except the catalogued ones whose package is absent (`missing_library_steps`)."""
    missing = missing_library_steps(entity_type)
    return sorted(k for k in _ATTACHED.get(entity_type, set()) if k not in missing)


def library_step_missing_reason(step_key: str, entity_type: str = "database") -> str:
    """The `not_installed_sentence` when `step_key` is a catalogued library step whose package is absent, else ""."""
    provided_by = missing_library_steps(entity_type).get(step_key)
    return not_installed_sentence(step_key, provided_by) if provided_by is not None else ""


def discover_steps(entity_type: str) -> dict:
    """name -> StepManifest for every installed step that measures `entity_type`. Empty when the Analytic
    Library is not installed; a broken entry point is logged with its name and recorded in LOAD_ERRORS."""
    try:
        from trellis_analytics.discovery import discover
    except ImportError:
        return {}
    try:
        found = discover()
    except Exception as exc:  # noqa: BLE001 - one broken package must not stop RE from starting
        LOAD_ERRORS[entity_type] = str(exc)
        log.error("user steps not loaded for %s: %s", entity_type, exc)
        return {}
    LOAD_ERRORS.pop(entity_type, None)
    return {n: m for n, m in found.items()
            if getattr(m, "kind", "") == "step" and entity_type in (m.resource_types or [])
            and getattr(m, "subject", "resource") != "set"}


def discover_set_steps(entity_type: str) -> dict:
    """name -> StepManifest for every installed step whose subject is a set of `entity_type` resources (a work list's
    members). They are not per-resource analyses, so `discover_steps` does not return them and the catalogue does not list
    them: the work list's page offers them (`set_step_offers`)."""
    try:
        from trellis_analytics.discovery import discover
        found = discover()
    except Exception as exc:  # noqa: BLE001 - one broken package must not stop RE from starting
        log.error("set steps not loaded for %s: %s", entity_type, exc)
        return {}
    return {n: m for n, m in found.items()
            if getattr(m, "kind", "") == "step" and getattr(m, "subject", "resource") == "set"
            and entity_type in (m.resource_types or [])}


def catalog_entry(manifest, origin: dict) -> dict:
    """The analysis_catalog.yaml entry for one step manifest, in the catalog's own vocabulary. The generator
    writes exactly this; a test holds the committed file to it."""
    m = manifest
    return {
        "id": m.name,
        "name": m.display_name or m.name.replace("_", " ").title(),
        "description": m.description,
        "resource_types": list(m.resource_types),
        "intent": m.stage,
        "perspectives": list(m.perspectives),
        "annotation_types": m.annotation_types(),
        # RE runs it (its own engine), so `local` in this catalog's sense; where it reads is provided_by.source.
        "source": "local",
        "run_time": "fast",
        # A step never runs in the web process (brief 3a): queued, through the run queue.
        "availability": "queued",
        "action": "survey",
        "target_shape": "whole_resource_only",
        "recommended": False,
        "egeria_registration": {"candidate": True, "native_process_ref": None},
        "provided_by": {
            "package": origin.get("package", ""), "entry_point": origin.get("entry_point", ""),
            "step": m.name, "version": m.version, "kind": m.kind, "source": m.source,
            "credential_tier": m.credential_tier, "questions": list(m.questions), "purposes": list(m.purposes),
        },
        # The step's declaration, unchanged: the catalog reads a library step's results by the same rules as a
        # built-in analysis's. Absent when the manifest declares none.
        **({"declaration": _declaration_entry(m.declaration)} if getattr(m, "declaration", None) else {}),
    }


def _declaration_entry(declaration) -> dict:
    """A declaration as the catalog writes it: every field the step declared, nothing defaulted left in, so the
    generated YAML reads like a hand-written entry."""
    return declaration.model_dump(mode="json", exclude_defaults=True)


def _step_info(step_key: str, description: str, annotation_type: str, source: str, tier: str):
    from resource_explorer.surveyors.repo_survey_definition_adapter import StepInfo

    return StepInfo(step_key, None, description, [annotation_type],
                    # An ODS step reads RE's own stored rows: no fetch from the resource.
                    fetch_cost="none" if source == "ods" else "api", compute_cost="low",
                    requires_capability="" if tier in ("", "none") else "read")


def attach(entity_type: str, adapter, step_registry: dict, step_map: dict, results_map: dict,
           kinds: dict, headline_map: dict) -> list[str]:
    """Attach every generated catalog entry's step (installed or not) and every installed step to one resource
    type's maps. Idempotent: a second call replaces what the first attached. Returns the step keys attached."""
    from resource_explorer.surveyors import annotation_readers
    from resource_explorer.surveyors.analysis_catalog_reader import _load
    from resource_explorer.surveyors.repo_survey_definition_adapter import AnalysisKind, AnalysisKindResults

    builtin = _BUILTIN.setdefault(entity_type, frozenset(step_registry) | frozenset(adapter.re_analysis_steps))
    found = discover_steps(entity_type)
    origins = {}
    attached: list[str] = []
    analysis_ids: set[str] = set()
    missing: dict[str, dict] = {}
    for entry in _load().get(entity_type, []):
        if not entry.provided_by:
            continue
        step_key = entry.provided_by.get("step") or entry.id
        if step_key in builtin:
            log.error("analysis catalog entry '%s' names user step '%s', which is a built-in step; not attached",
                      entry.id, step_key)
            continue
        annotation_type = (entry.annotation_types or ["ResourceMeasureAnnotation"])[0]
        manifest = found.get(step_key)
        origins[step_key] = entry.provided_by
        runner = (SurveyStepHost(manifest, entity_type, entry.provided_by) if manifest
                  else NotInstalledStep(entry.provided_by, step_key))
        if not manifest:
            missing[step_key] = dict(entry.provided_by)
        adapter.re_analysis_steps[step_key] = runner
        adapter.re_analysis_step_info[step_key] = {"description": entry.description,
                                                   "annotation_types": [annotation_type]}
        step_registry[step_key] = _step_info(step_key, entry.description, annotation_type,
                                             entry.provided_by.get("source", ""),
                                             entry.provided_by.get("credential_tier", ""))
        reader = annotation_readers.results_reader(entity_type, step_key, annotation_type)
        headline = annotation_readers.headline_reader(entity_type, step_key, annotation_type)
        step_map[entry.id] = [step_key]
        analysis_ids.add(entry.id)
        results_map[entry.id] = (reader, None)
        headline_map[entry.id] = headline
        kinds[entry.id] = AnalysisKind(entry.id, [step_key], results=AnalysisKindResults(
            reader, None, render="custom", headline_reader=headline, live_read=True))
        attached.append(step_key)
    for name, manifest in found.items():
        if name in attached:
            continue
        if name in builtin:
            log.error("installed user step '%s' has a built-in step's name; not attached (it would replace "
                      "Resource Explorer's own step)", name)
            continue
        log.warning("installed step '%s' has no analysis catalog entry; it can run from a Survey Definition but "
                    "shows no card: the catalog merges installed manifests when it is read, so this means the "
                    "entry was refused or the catalog was read before the package was installed", name)
        adapter.re_analysis_steps[name] = SurveyStepHost(manifest, entity_type)
        adapter.re_analysis_step_info[name] = {"description": manifest.description,
                                               "annotation_types": [manifest.annotation_type]}
        step_registry[name] = _step_info(name, manifest.description, manifest.annotation_type,
                                         manifest.source, manifest.credential_tier)
        attached.append(name)
    # A step attached by an earlier attach and not this one (uninstalled, or a test's) leaves the maps: a refresh
    # replaces what the last attach attached, it never accumulates.
    for gone in _ATTACHED.get(entity_type, set()) - set(attached) - builtin:
        adapter.re_analysis_steps.pop(gone, None)
        adapter.re_analysis_step_info.pop(gone, None)
        step_registry.pop(gone, None)
    # Its analyses leave the maps too, so a catalog entry that is gone (a package uninstalled, a test's fake) does not
    # keep a results reader behind.
    for gone_id in _ATTACHED_IDS.get(entity_type, set()) - analysis_ids:
        for mapping in (step_map, results_map, headline_map, kinds):
            mapping.pop(gone_id, None)
    _ATTACHED_IDS[entity_type] = analysis_ids
    _ATTACHED[entity_type] = set(attached)
    _MISSING[entity_type] = missing
    _LIBRARY[entity_type], _REFUSED[entity_type] = _admit(entity_type, found, origins)
    return attached


def attached_user_steps(entity_type: str = "database") -> frozenset[str]:
    """The user (Analytic Library) step keys `attach` put into this resource type's step registry. RE's engine host
    declares and claims those through their manifests' request types, never as built-in registry steps (case 3)."""
    return frozenset(_ATTACHED.get(entity_type, set()))


def refresh(entity_type: str = "database") -> list[str]:
    """Discover again and re-attach (after a package is installed into a running process, or in a test)."""
    from resource_explorer.surveyors import question_catalog_reader
    from resource_explorer.surveyors.analysis_catalog_reader import clear_cache

    question_catalog_reader.clear_cache()      # a step's own questions are merged into it when it is read
    if entity_type == "database":
        from resource_explorer.surveyors.database import survey_definition_adapter as db

        clear_cache()
        return attach("database", db._ADAPTER, db.DATABASE_STEP_REGISTRY, db.DATABASE_ANALYSIS_RE_STEP_MAP,
                      db.DATABASE_ANALYSIS_RESULTS_MAP, db.DATABASE_ANALYSIS_KINDS, db.DATABASE_ANALYSIS_HEADLINE_MAP)
    if entity_type == "filesystem":
        from resource_explorer.surveyors.filesystem import survey_definition_adapter as fs

        clear_cache()
        return attach("filesystem", fs._ADAPTER, fs.FILESYSTEM_STEP_REGISTRY, fs.FILESYSTEM_ANALYSIS_STEP_MAP,
                      fs.FILESYSTEM_ANALYSIS_RESULTS_MAP, fs.FILESYSTEM_ANALYSIS_KINDS,
                      fs.FILESYSTEM_ANALYSIS_HEADLINE_MAP)
    raise ValueError(f"user steps attach to databases and file systems only so far, not {entity_type}")


# ── A set as the subject (brief 2026-10-30, Part C) ─────────────────────────────────────────────────────────────

#: The entity type the roll-up of a set run is stored under (its slug is the work list's).
SET_ENTITY = "work_list"


def run_set_step(manifest, registry, work_list_slug: str, members: list[tuple[str, str]], *,
                 provided_by: dict | None = None) -> dict:
    """Run a set step once for a work list's members: ONE producing run, a stored result per member (under the member's
    own type and slug, as a run of the step on that member would be read) and one roll-up (under `work_list`/<the list's
    slug>). A member of a type the step does not measure is stored as a reason row and counted, never dropped.

    The price of the run is the sum of its members' (`set_price`); it is returned beside the outcome so the page that
    started the run can say what it cost to ask."""
    from trellis_analytics.step import HostContext, SurveyStep

    step = manifest.name
    provided_by = dict(provided_by or {})
    run = datetime.utcnow().isoformat()
    where = {slug: etype for etype, slug in members}
    stored: dict[tuple[str, str, str], int] = {}

    def store(annotations) -> int:
        measured_at = annotations[0].envelope.measured_at if annotations else run
        groups: dict[tuple[str, str, str], list] = {}
        for a in annotations:
            etype = SET_ENTITY if a.scope == work_list_slug else where[a.scope]
            groups.setdefault((etype, a.scope, measure_step_key(step, getattr(a, "measure", ""))), []).append(a)
        for (etype, slug, key), group in groups.items():
            stored[(etype, slug, key)] = registry.record_step_annotations(
                etype, slug, key, run=run, measured_at=measured_at, annotations=[a.to_record() for a in group],
                package=provided_by.get("package", ""), producer_version=manifest.version)
        return sum(len(g) for g in groups.values())

    with contextlib.ExitStack() as stack:
        ods = stack.enter_context(step_reader(registry.database_url, allow_slug_lists=True))
        host = HostContext(resource_type=SET_ENTITY, slug=work_list_slug, producing_run=run, ods=ods, store=store,
                           members=tuple(members), set_slug=work_list_slug)
        code = SurveyStep(manifest).code
        annotations = SurveyStep(manifest, code=_without_identity(code, stack.close)).run(host)
    counts: dict[str, int] = {}
    for a in annotations:
        counts[a.envelope.result_state.value] = counts.get(a.envelope.result_state.value, 0) + 1
    return {"run": run, "step": step, "set": work_list_slug, "annotation_count": len(annotations),
            "per_member": sum(1 for a in annotations if a.scope != work_list_slug),
            "rollups": sum(1 for a in annotations if a.scope == work_list_slug),
            "state_counts": counts, "step_annotations": [a.to_record() for a in annotations],
            "price": set_price(registry, manifest, members)}


def set_price(registry, manifest, members: list[tuple[str, str]]) -> dict:
    """The priced line of a set run: one row per member (tables and size from RE's stored inventory, the time from this step's
    earlier runs), and a total that is the SUM of the members'. A member the step does not measure is a row that says so and
    adds nothing; a member with no measured time is counted, and with any such member there is no total time, only the words
    saying how many are not measured yet (an absence is never a zero)."""
    from resource_explorer import quality_batch

    rows, measured, unmeasured, skipped = [], [], 0, 0
    for etype, slug in members:
        if etype not in manifest.resource_types:
            skipped += 1
            rows.append({"entity_slug": slug, "entity_type": etype, "can_run": False, "time": None,
                         "words": f"skipped · {slug} is a {etype}; this step measures {', '.join(manifest.resource_types)}"})
            continue
        row = quality_batch.price_row(registry, slug, definition=manifest.name)
        rows.append({**row, "entity_type": etype})
        secs = (row.get("time") or {}).get("seconds")
        if secs is None:
            unmeasured += 1
        else:
            measured.append(secs)
    priced = len(rows) - skipped
    total_tables = sum((r.get("tables") or {}).get("count") or 0 for r in rows)
    if priced and not unmeasured:
        total = {"seconds": sum(measured), "words": f"about {quality_batch._duration_words(sum(measured))} in all · "
                                                    "the sum of its members' measured times"}
    elif measured:
        total = {"seconds": None, "words": f"{unmeasured} of {priced} members not measured yet · no total until each has "
                                           "run once"}
    else:
        total = {"seconds": None, "words": "not measured yet · the first run sets the price" if priced else
                 "no member this step measures"}
    return {"step": manifest.name, "members": len(members), "priced": priced, "skipped": skipped,
            "tables": total_tables, "rows": rows, "total": total,
            "words": f"{priced} member(s) · {total['words']}" + (f" · {skipped} skipped" if skipped else "")}


def set_members(work_list: dict) -> list[tuple[str, str]]:
    """A work list's members as (resource_type, slug): each member's own kind, else the list's."""
    return [(m.get("entity_type") or work_list.get("entity_type") or "", m["entity_slug"])
            for m in work_list.get("members") or []]


def set_step_offers(registry, work_list: dict) -> list[dict]:
    """The set steps the work list's page offers, each with its priced line (`set_price`) for THIS list's members. A step
    that measures none of the members' kinds is not offered: there is nothing for it to run on."""
    members = set_members(work_list)
    kinds = {t for t, _ in members}
    offers = []
    for etype in sorted(kinds):
        for name, manifest in discover_set_steps(etype).items():
            if any(o["step"] == name for o in offers):
                continue
            offers.append({"step": name, "display_name": manifest.display_name or name.replace("_", " ").title(),
                           "description": manifest.description, "price": set_price(registry, manifest, members)})
    return offers
