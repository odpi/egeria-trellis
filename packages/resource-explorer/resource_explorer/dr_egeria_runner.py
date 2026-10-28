"""Run one Dr.Egeria document on request: the ad-hoc runner (owner, 2026-10-14).

"We are going to need to be able to load Dr.Egeria commands - sometimes adhoc and sometimes as part of a
initialization (which we already do)." Initialization is `bootstrap.py`. This module is the ad-hoc half, and it
runs a document through the SAME processor the bootstrap heal uses, `bootstrap.run_dr_egeria_document` (the
`dr_egeria` CLI in a subprocess). It is reached from two places that both call `run_document`:

* the CLI, `resource-explorer dr-egeria run <path.md> [--dry-run] [--force]` (cli/dr_egeria_commands.py);
* the admin-only route `POST /api/bootstrap/dr-egeria/run` (web/routes/bootstrap.py).

**Who it runs as.** RE's service account, `Daemon(DaemonReason.DR_EGERIA)`. The pyegeria steps get their clients
from the factory for that identity; the `dr_egeria` subprocess gets the same user, password, platform and view
server in its environment, set explicitly, so it never runs as whatever `EGERIA_USER` the parent happened to have.
(The bootstrap heal still inherits its environment, so its `dr_egeria` runs as whatever `EGERIA_USER` pyegeria
finds in the environment or .env, which is not necessarily RE's `EGERIA_USER_ID`: a follow-up, not changed here.)

**What is logged.** One `activity_log` row per run, operation `dr_egeria`: written `running` with
`_runner: run_reconciler.process_identity()` BEFORE any work, so the reconciler can resolve it if this process
dies; finished with a terminal status and one item per command (command, element, ok/error, Egeria's words).
What the caller is shown is read back from that row. A dry run writes nothing at all.

**Repeats.** Dr.Egeria's `Create`/`Update` commands are name-keyed upserts, but its link-type commands do not
look for an existing relationship (bootstrap.py's IDEMPOTENCY section; `scripts/reconcile_survey_definition_links.py`
exists because re-running `Link First/Next Process Step` duplicated every edge). This runner does not paper over
that: it names the commands that are not safe to repeat, and it refuses a second run of a document whose content
hash already has a run row unless `force` is given. The hash rides in the row's `detail` (no new table).

**pyegeria steps.** pyegeria 6.2.3's Dr.Egeria has no command for a SurveyActionEngine, a SurveyActionService or
the SupportedGovernanceService link. A document may carry a sibling `<name>.pyegeria-steps.json` naming those three
operations (and only those); each finds its element by qualifiedName (the link: by engine, service and request
type) and creates it only when absent. They run before the document, in file order, which must be engine, service,
link. A failed step stops the run before the document.

**Paths.** Only `.md` files under `docs/dr-egeria/` (or `RE_DR_EGERIA_DOCS_DIR`) are accepted, after resolving
symlinks and `..`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

OPERATION = "dr_egeria"
INTENT = "enrichment"
ENTITY_TYPE = "dr_egeria_document"
DOCS_DIR_ENV = "RE_DR_EGERIA_DOCS_DIR"
STEPS_SUFFIX = ".pyegeria-steps.json"

RUNNING, OK, ERROR, WARNING, REFUSED = "running", "ok", "error", "warning", "refused"
PLANNED, NOT_RUN, UNKNOWN = "planned", "not_run", "unknown"

#: Name-keyed upserts: a repeat updates the element it created.
UPSERT_VERBS = frozenset({"Create", "Update"})
#: Reads.
READ_VERBS = frozenset({"Display", "View", "Search", "Find", "Validate"})
#: An ALLOW-list: every other verb is refused, whatever it does (Delete, Remove, Unlink, Detach, Declassify,
#: Clear, Cancel, Initiate, Run, Process, Import, Publish, and any verb Dr.Egeria adds later). Destructive and
#: runtime-request commands are the owner's to run himself. Of these, only UPSERT_VERBS and READ_VERBS are
#: safe to repeat; the rest add relationships, classifications or values and are flagged not safe.
ALLOWED_VERBS = UPSERT_VERBS | READ_VERBS | frozenset({"Link", "Attach", "Add", "Classify", "Reclassify",
                                                        "Setup", "Set"})

ENGINE_STEP = "Create Survey Action Engine"
SERVICE_STEP = "Create Survey Action Service"
LINK_STEP = "Link Supported Governance Service"
#: The ONLY pyegeria steps, in the order they must run.
PYEGERIA_STEPS = (ENGINE_STEP, SERVICE_STEP, LINK_STEP)

_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
#: pyegeria's two "nothing there" answers (pyegeria.core._globals NO_ELEMENT_FOUND / NO_ELEMENTS_FOUND).
_ABSENT = frozenset({"no element found", "no elements found"})
_LINK_READ_PAGE = 100
_LINK_READ_MAX_PAGES = 20
_SUMMARY_TITLE = "Dr. Egeria v2 Processing Summary"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
#: Wide enough that rich never folds a summary cell across lines (folding is still handled).
_CONSOLE_WIDTH = "4000"

_run_lock = threading.Lock()


class DocumentRefused(ValueError):
    """The path or the document is not acceptable; nothing was run or written. Maps to 400."""


class RunRefused(RuntimeError):
    """The run is refused because of other runs (a repeat, or one in progress). Maps to 409."""


class RepeatRefused(RunRefused):
    """This document's content was already applied; run it again only with `force`."""


class StepFailed(RuntimeError):
    """A pyegeria step could not complete, or could not tell whether its element exists."""


# ── paths ────────────────────────────────────────────────────────────────────

def docs_root() -> Path:
    """The one directory documents may come from: `RE_DR_EGERIA_DOCS_DIR`, else the package's docs/dr-egeria."""
    from resource_explorer import bootstrap

    configured = (os.environ.get(DOCS_DIR_ENV) or "").strip()
    return (Path(configured).expanduser() if configured else bootstrap.DOCS_DIR).resolve()


def resolve_document(path: str | os.PathLike) -> Path:
    """The real path of an `.md` document under `docs_root()`, or `DocumentRefused`.

    A relative path is taken relative to the docs directory first, then to the working directory; either way the
    RESOLVED path (symlinks and `..` followed) must lie under the docs directory."""
    root = docs_root()
    text = os.fspath(path)
    if "\x00" in str(text):
        raise DocumentRefused("a document path may not contain a NUL byte")
    raw = Path(text).expanduser()
    candidates = [raw] if raw.is_absolute() else [root / raw, Path.cwd() / raw]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError, RuntimeError) as exc:
            raise DocumentRefused(f"unreadable document path {path!r}: {exc}") from None
        if not resolved.is_relative_to(root):
            continue
        if resolved.suffix.lower() != ".md":
            raise DocumentRefused(f"only Dr.Egeria markdown documents (.md) are run: {path}")
        if resolved.is_file():
            return resolved
    raise DocumentRefused(f"no document {path} under {root} (only documents under that directory are accepted)")


# ── the plan ─────────────────────────────────────────────────────────────────

@dataclass
class PlannedCommand:
    kind: str                      # "pyegeria" | "dr_egeria"
    command: str
    element: str
    safe_to_repeat: bool
    repeat_note: str
    step: Optional[dict] = None

    def item(self, status: str, **extra: Any) -> dict:
        out = {"kind": self.kind, "command": self.command, "element": self.element, "status": status,
               "safe_to_repeat": self.safe_to_repeat}
        out.update({k: v for k, v in extra.items() if v not in (None, "")})
        return out


@dataclass
class DocumentPlan:
    path: Path
    rel: str
    sha256: str
    commands: list[PlannedCommand] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    batch_note: str = ""
    #: The bootstrap batch whose `_batch.json` lists this document ('' when none): the heal may have applied it.
    batch_id: str = ""

    @property
    def steps(self) -> list[PlannedCommand]:
        return [c for c in self.commands if c.kind == "pyegeria"]

    @property
    def dr_egeria(self) -> list[PlannedCommand]:
        return [c for c in self.commands if c.kind == "dr_egeria"]

    @property
    def not_safe_to_repeat(self) -> list[str]:
        return [c.command for c in self.commands if not c.safe_to_repeat]


def _steps_path(doc: Path) -> Optional[Path]:
    """The document's pyegeria steps file, path-checked like the document: None when there is none; refused
    when it exists but resolves outside the docs directory (a symlink out, say) or is not a regular file."""
    sibling = doc.with_name(doc.stem + STEPS_SUFFIX)
    if not os.path.lexists(sibling):
        return None
    resolved = sibling.resolve()
    if not resolved.is_relative_to(docs_root()) or not resolved.is_file():
        raise DocumentRefused(f"the pyegeria steps file {sibling.name} resolves outside {docs_root()} "
                              "or is not a file; refused")
    return resolved


def _element_of(attributes: dict) -> str:
    for key in ("Qualified Name", "Display Name"):
        value = (attributes.get(key) or "").strip() if isinstance(attributes.get(key), str) else ""
        if value:
            return value
    ends = [v.strip() for v in attributes.values() if isinstance(v, str) and v.strip()][:2]
    return " -> ".join(ends)


def _plan_steps(steps_file: Path, errors: list[str]) -> list[PlannedCommand]:
    try:
        spec = json.loads(steps_file.read_text())
    except (OSError, ValueError) as exc:
        errors.append(f"{steps_file.name}: not readable JSON ({exc})")
        return []
    steps = spec.get("steps") if isinstance(spec, dict) else None
    if not isinstance(steps, list):
        errors.append(f"{steps_file.name}: no 'steps' list")
        return []
    out: list[PlannedCommand] = []
    ranks = []
    for n, step in enumerate(steps, 1):
        command = step.get("command") if isinstance(step, dict) else None
        if command not in PYEGERIA_STEPS:
            errors.append(f"{steps_file.name} step {n}: {command!r} is not one of {list(PYEGERIA_STEPS)}")
            continue
        missing = [k for k in _STEP_FIELDS[command] if not str(step.get(k) or "").strip()]
        if missing:
            errors.append(f"{steps_file.name} step {n} ({command}): missing {', '.join(missing)}")
            continue
        ranks.append(PYEGERIA_STEPS.index(command))
        element = (step["qualified_name"] if command != LINK_STEP else
                   f"{step['governance_engine']} -> {step['governance_service']} ({step['request_type']})")
        out.append(PlannedCommand("pyegeria", command, element, True,
                                  "finds by qualifiedName first; creates only when absent", step=step))
    if ranks != sorted(ranks):
        errors.append(f"{steps_file.name}: steps are out of order; they must run "
                      f"{' -> '.join(PYEGERIA_STEPS)}")
    return out


_STEP_FIELDS = {
    ENGINE_STEP: ("qualified_name", "display_name"),
    SERVICE_STEP: ("qualified_name", "display_name"),
    LINK_STEP: ("governance_engine", "governance_service", "request_type"),
}


def _batch_of(doc: Path) -> str:
    """The batch id (directory name) when the `_batch.json` beside the document lists it in `files`."""
    from resource_explorer import bootstrap

    manifest = doc.parent / bootstrap.BATCH_MANIFEST_FILE
    if not manifest.is_file():
        return ""
    try:
        files = json.loads(manifest.read_text()).get("files") or []
    except (OSError, ValueError, AttributeError):
        return doc.parent.name             # unreadable manifest: assume it may list the document
    return doc.parent.name if doc.name in files else ""


def _batch_note(doc: Path) -> str:
    """When the document belongs to a bootstrap batch that is not idempotent, say what a repeat needs after it."""
    from resource_explorer import bootstrap

    manifest = doc.parent / bootstrap.BATCH_MANIFEST_FILE
    if not manifest.is_file():
        return ""
    try:
        spec = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return f"this document is in bootstrap batch {doc.parent.name}, whose manifest is unreadable"
    if spec.get("idempotent", True):
        return f"this document is in bootstrap batch {doc.parent.name} (idempotent)"
    script = (spec.get("post_heal") or {}).get("script") or ""
    return (f"this document is in bootstrap batch {doc.parent.name}, which is NOT idempotent"
            + (f"; after a repeat run, run {script} to strip duplicate links" if script else ""))


def plan_document(path: str | os.PathLike) -> DocumentPlan:
    """Parse and validate a document (and its pyegeria steps) without any Egeria call or registry write."""
    from md_processing.dr_egeria import setup_dispatcher
    from md_processing.v2.extraction import UniversalExtractor

    doc = resolve_document(path)
    text = doc.read_bytes()
    steps_file = _steps_path(doc)
    steps_bytes = steps_file.read_bytes() if steps_file is not None else b""
    digest = hashlib.sha256(text + b"\0" + steps_bytes).hexdigest()
    plan = DocumentPlan(path=doc, rel=doc.relative_to(docs_root()).as_posix(), sha256=digest,
                        batch_note=_batch_note(doc), batch_id=_batch_of(doc))
    if steps_bytes:
        plan.commands.extend(_plan_steps(steps_file, plan.errors))

    dispatcher = setup_dispatcher(None)     # pyegeria's own command registry; no client is used to resolve
    for cmd in UniversalExtractor(text.decode("utf-8")).extract_commands():
        if not cmd.is_command or cmd.verb == "Provenance":
            continue
        name = f"{cmd.verb} {cmd.object_type}"
        if cmd.verb not in ALLOWED_VERBS:
            plan.errors.append(f"{name}: '{cmd.verb}' commands are not run by RE (only "
                               f"{', '.join(sorted(ALLOWED_VERBS))}); destructive ones the owner runs himself")
            continue
        if not dispatcher.resolve_processor_class(cmd):
            plan.errors.append(f"{name}: Dr.Egeria has no such command")
            continue
        if cmd.verb in UPSERT_VERBS:
            safe, note = True, "name-keyed upsert: a repeat updates the same element"
        elif cmd.verb in READ_VERBS:
            safe, note = True, "read only"
        else:
            safe, note = False, ("Dr.Egeria does not look for an existing relationship or classification; "
                                 "a repeat can create a duplicate")
        plan.commands.append(PlannedCommand("dr_egeria", name, _element_of(cmd.attributes), safe, note))
    if not plan.dr_egeria and not plan.steps and not plan.errors:
        plan.errors.append("the document has no Dr.Egeria commands")
    return plan


def _plan_view(plan: DocumentPlan) -> dict:
    return {
        "document": plan.rel, "sha256": plan.sha256, "dry_run": True,
        "status": PLANNED if not plan.errors else ERROR, "valid": not plan.errors, "errors": list(plan.errors),
        "summary": (f"{len(plan.commands)} command(s) would run" if not plan.errors
                    else f"not valid: {len(plan.errors)} problem(s)"),
        "commands": [dict(c.item(PLANNED), repeat_note=c.repeat_note) for c in plan.commands],
        "not_safe_to_repeat": plan.not_safe_to_repeat, "batch_note": plan.batch_note, "activity_id": "",
        "bootstrap_batch": plan.batch_id,
    }


# ── the dr_egeria summary ────────────────────────────────────────────────────

def _summary_rows(stdout: str) -> Optional[list[list[str]]]:
    """The rows of dr_egeria's "Processing Summary" table, each [markdown command, canonical command, status,
    found, name, qualified name, message]. None when there is no such table."""
    lines = _ANSI.sub("", stdout or "").splitlines()
    starts = [i for i, line in enumerate(lines) if _SUMMARY_TITLE in line]
    if not starts:
        return None
    rows: list[list[str]] = []
    current: Optional[list[list[str]]] = None
    in_body = False
    for line in lines[starts[-1] + 1:]:
        s = line.strip()
        if s.startswith("┡"):
            in_body = True
            continue
        if not in_body:
            continue
        if s.startswith("│"):
            cells = [c.strip() for c in s.strip("│").split("│")]
            if len(cells) < 7:
                return None
            if len(cells) > 7:
                # A "│" inside the last cell (the free-text Message) is not a column edge: keep it in the message.
                cells = cells[:6] + [" │ ".join(cells[6:])]
            if current is None:
                current = [[c] for c in cells]
            else:
                for parts, c in zip(current, cells):
                    parts.append(c)
        elif s.startswith(("├", "└")):
            if current is not None:
                rows.append([" ".join(p for p in parts if p) for parts in current])
                current = None
            if s.startswith("└"):
                return rows
    return None                                   # the table never closed: not a table we can trust


def _error_blocks(stdout: str) -> list[tuple[str, list[str]]]:
    """dr_egeria's "Processing Errors" section: [("<verb> <object> (<qualified name>)", [error, ...]), ...]."""
    lines = _ANSI.sub("", stdout or "").splitlines()
    out: list[tuple[str, list[str]]] = []
    inside = False
    for line in lines:
        s = line.strip()
        if s.startswith("Processing Errors:"):
            inside = True
            continue
        if s.startswith("Processing Warnings:") or _SUMMARY_TITLE in s:
            inside = False
        if not inside:
            continue
        m = re.match(r"^-- (.*) --$", s)
        if m:
            out.append((m.group(1), []))
        elif s.startswith("- ") and out:
            out[-1][1].append(s[2:])
    return out


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _squash(text: str) -> str:
    """Whitespace-free, so a cell rich folded mid-word ("Governance" -> "Govern ance") still compares equal."""
    return "".join((text or "").split()).casefold()


_STATUS = {"SUCCESS": OK, "FAILURE": ERROR, "WARNING": WARNING}


def _row_matches(planned: PlannedCommand, row: list[str]) -> bool:
    want = _squash(planned.command)
    return want in (_squash(row[0]), _squash(row[1]))


def dr_egeria_results(planned: list[PlannedCommand], stdout: str, process_detail: str) -> list[dict]:
    """One item per planned Dr.Egeria command, read from dr_egeria's own summary table.

    Each command is matched to its row by name (either the markdown or the canonical command, whitespace
    ignored), in order, so a stray or missing row costs only the command it belongs to. `unknown` is kept for
    what truly cannot be matched, and always names why: no table, no row for the command, or a status word
    other than SUCCESS / FAILURE / WARNING (a DEFERRED row, say, is reported as deferred, not as ok): never `ok`
    without a row that says SUCCESS."""
    rows = _summary_rows(stdout)
    if rows is None:
        tail = (_ANSI.sub("", stdout or "").strip().splitlines() or [process_detail])[-1][:300]
        why = f"no summary table in dr_egeria's output; process: {process_detail}; last line: {tail}"
        return [p.item(UNKNOWN, error=why) for p in planned]
    blocks = _error_blocks(stdout)
    items = []
    at = 0
    for p in planned:
        hit = next((j for j in range(at, len(rows)) if _row_matches(p, rows[j])), None)
        if hit is None:
            same_place = at < len(rows) and len(rows) == len(planned)
            items.append(p.item(UNKNOWN, error=(
                f"dr_egeria reported {rows[at][0]!r} where {p.command!r} was planned" if same_place else
                f"no row for {p.command!r} in dr_egeria's summary ({len(rows)} row(s) for {len(planned)} "
                "command(s))")))
            if same_place:
                at += 1
            continue
        at = hit + 1
        _md_cmd, canonical, status_word, _found, _name, _qn, message = rows[hit]
        word = status_word.strip().upper()
        status = _STATUS.get(word, UNKNOWN)
        words = []
        if status != OK:
            words.append(message)
            if status == UNKNOWN:
                words.insert(0, f"dr_egeria's status was {status_word.strip() or 'empty'!r}, not "
                                "SUCCESS, FAILURE or WARNING")
            for n, (header, errs) in enumerate(blocks):
                if _norm(header).startswith(_norm(canonical) + " ("):
                    words.extend(errs)
                    blocks.pop(n)
                    break
        items.append(p.item(status, outcome="processed" if status == OK else "", message=message,
                            error="; ".join(w for w in words if w)))
    return items


# ── the pyegeria steps ───────────────────────────────────────────────────────

class _Steps:
    """The three pyegeria operations, each find-or-create, on clients built for the daemon by the factory."""

    def __init__(self, clients) -> None:
        self.clients = clients
        self.guids: dict[str, str] = {}

    def _expert(self):
        from pyegeria import MetadataExpert

        return self.clients.of(MetadataExpert)

    def _maker(self):
        from pyegeria import AssetMaker

        return self.clients.of(AssetMaker)

    def find_guid(self, qualified_name: str) -> Optional[str]:
        """The element's GUID; None only when Egeria says there is none. Anything else raises: a lookup that
        cannot answer must never lead to a create."""
        if qualified_name in self.guids:
            return self.guids[qualified_name]
        answer = self._expert().get_metadata_guid_by_unique_name(qualified_name)
        if isinstance(answer, str) and _GUID_RE.match(answer.strip()):
            self.guids[qualified_name] = answer.strip()
            return self.guids[qualified_name]
        if isinstance(answer, str) and answer.strip().casefold() in _ABSENT:
            return None
        raise StepFailed(f"the lookup of {qualified_name!r} gave neither a GUID nor 'not found': "
                         f"{' '.join(str(answer).split())[:160]!r}; nothing was created")

    def _created(self, qualified_name: str, answer: Any, what: str) -> str:
        if isinstance(answer, str) and _GUID_RE.match(answer.strip()):
            self.guids[qualified_name] = answer.strip()
            return answer.strip()
        raise StepFailed(f"Egeria answered the {what} create with {' '.join(str(answer).split())[:160]!r}, "
                         "not a GUID; whether it was created is not known")

    def run(self, planned: PlannedCommand) -> dict:
        step = planned.step or {}
        if planned.command == ENGINE_STEP:
            return self._element(planned, step, "SurveyActionEngineProperties", "SurveyActionEngine",
                                 lambda body: self._maker().create_software_capability(body=body))
        if planned.command == SERVICE_STEP:
            return self._element(planned, step, "SurveyActionServiceProperties", "SurveyActionService",
                                 lambda body: self._maker().create_asset(body=body))
        if planned.command == LINK_STEP:
            return self._link(planned, step)
        raise StepFailed(f"unknown pyegeria step {planned.command!r}")      # plan_document refuses these first

    def _element(self, planned, step, properties_class, type_name, create) -> dict:
        qn = step["qualified_name"]
        guid = self.find_guid(qn)
        if guid:
            return planned.item(OK, outcome="found", guid=guid)
        body = {"class": "NewElementRequestBody", "isOwnAnchor": True,
                "properties": {"class": properties_class, "typeName": type_name, "qualifiedName": qn,
                               "displayName": step["display_name"],
                               "description": step.get("description") or None}}
        guid = self._created(qn, create(body), type_name)
        return planned.item(OK, outcome="created", guid=guid)

    def _link(self, planned, step) -> dict:
        engine_qn, service_qn = step["governance_engine"], step["governance_service"]
        request_type = step["request_type"]
        service_request_type = step.get("service_request_type") or request_type
        engine, service = self.find_guid(engine_qn), self.find_guid(service_qn)
        missing = [qn for qn, g in ((engine_qn, engine), (service_qn, service)) if not g]
        if missing:
            raise StepFailed(f"cannot link: no element {', '.join(repr(m) for m in missing)} in Egeria")
        existing = self._existing_link(engine, service, request_type)
        if existing is not None:
            return planned.item(OK, outcome="found", guid=existing)
        body = {"class": "NewRelationshipRequestBody",
                "properties": {"class": "SupportedGovernanceServiceProperties", "requestType": request_type,
                               "serviceRequestType": service_request_type}}
        answer = self._maker().link_supported_governance_service(engine, service, body)
        if isinstance(answer, str) and _GUID_RE.match(answer.strip()):
            return planned.item(OK, outcome="created", guid=answer.strip())
        # No GUID back: only a read that finds the link says it is there.
        try:
            found = self._existing_link(engine, service, request_type)
        except StepFailed as exc:
            return planned.item(UNKNOWN, error=f"Egeria returned no relationship GUID ({answer!r:.80}) and the "
                                               f"read-back failed: {exc}")
        if found is None:
            return planned.item(UNKNOWN, error=f"Egeria returned no relationship GUID ({answer!r:.80}) and the "
                                               "read-back does not show the link")
        return planned.item(OK, outcome="created", guid=found,
                            message="no GUID in the create's answer; confirmed by reading it back")

    def _existing_link(self, engine: str, service: str, request_type: str) -> Optional[str]:
        """The SupportedGovernanceService from this engine to this service with this requestType, if one exists
        ('' when it exists but its GUID is not in the answer). SupportedGovernanceService is MULTI-LINK, so a
        create with no read first would add a second registration. The engine is end 1."""
        from resource_explorer.egeria_outbox import _relationship_property

        expert = self._expert()
        for page in range(_LINK_READ_MAX_PAGES):
            answer = expert.get_related_metadata_elements(
                engine, "SupportedGovernanceService",
                {"class": "ResultsRequestBody", "startFrom": page * _LINK_READ_PAGE, "pageSize": _LINK_READ_PAGE},
                starting_at_end=1)
            if isinstance(answer, str) and answer.strip().casefold() in _ABSENT:
                return None
            if page > 0 and answer in ([], {}, ""):         # an empty later page is the end of the list
                return None
            if not isinstance(answer, dict):
                raise StepFailed("the read of the engine's supported governance services gave "
                                 f"{' '.join(str(answer).split())[:160]!r}; no link was created")
            items = answer.get("elementList") or []
            if not items:
                return None
            for item in items:
                if not isinstance(item, dict):
                    continue
                if ((item.get("type") or {}).get("typeName") == "SupportedGovernanceService"
                        and (item.get("element") or {}).get("elementGUID") == service
                        and _relationship_property(item.get("relationshipProperties"), "requestType")
                        == request_type):
                    return str(item.get("relationshipGUID")
                               or (item.get("relationshipHeader") or {}).get("guid") or "")
        raise StepFailed(f"the read of the engine's supported governance services stopped after "
                         f"{_LINK_READ_MAX_PAGES} pages; no link was created")

    def close(self) -> None:
        for client in list(getattr(self.clients, "_clients", {}).values()):
            close = getattr(client, "close_session", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - tidy-up of a finished run, never its answer
                    pass


# ── the run ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detail_of(row: dict) -> dict:
    try:
        detail = json.loads(row.get("detail") or "{}")
    except (TypeError, ValueError):
        return {}
    return detail if isinstance(detail, dict) else {}


def _runs_of(registry, sha256: str) -> list[dict]:
    return [r for r in registry.list_activity(operation=OPERATION, limit=1000)
            if _detail_of(r).get("sha256") == sha256 and not _detail_of(r).get("dry_run")
            and r.get("status") != REFUSED]


def _item_counts(row: dict) -> str:
    counts: dict[str, int] = {}
    for item in row.get("items") or []:
        status = item.get("status", "") if isinstance(item, dict) else ""
        counts[status] = counts.get(status, 0) + 1
    order = (OK, WARNING, ERROR, UNKNOWN, NOT_RUN)
    parts = [f"{counts[s]} {s.replace('_', ' ')}" for s in order if counts.get(s)]
    return ", ".join(parts) or "no command results recorded"


def _guard_repeat(registry, plan: DocumentPlan, force: bool, own_id: str = "") -> None:
    """Refuse a run that could repeat writes. Order: a live run of the same content (always refused, force or
    not); a bootstrap batch document (refused unless forced); an earlier run of the same content (refused
    unless forced). A running row whose owner is provably gone is first reconciled to `interrupted` exactly as
    run_reconciler does, and then counts as an earlier run (it may have written before it stopped)."""
    from resource_explorer.run_reconciler import reconcile_one

    for r in _runs_of(registry, plan.sha256):
        if r["id"] != own_id and r.get("status") == RUNNING:
            reconcile_one(registry, r)
    prior = [r for r in _runs_of(registry, plan.sha256) if r["id"] != own_id]
    running = [r for r in prior if r.get("status") == RUNNING]
    if running:
        r = running[0]
        raise RunRefused(f"a run of {plan.rel} (same content) is in progress: activity {r['id']} since {r['ts']} "
                         "(its process is alive or cannot be checked)")
    unsafe = ", ".join(plan.not_safe_to_repeat) or "none"
    if plan.batch_id and not force:
        raise RepeatRefused(
            f"{plan.rel} is listed in bootstrap batch {plan.batch_id}, which the bootstrap heal applies, so it may "
            f"already be in Egeria. Commands not safe to repeat: {unsafe}. Run it only on purpose, with force."
            + (f" Note: {plan.batch_note}." if plan.batch_note else ""))
    if prior and not force:
        r = prior[0]
        raise RepeatRefused(
            f"{plan.rel} (sha256 {plan.sha256[:12]}) was run before: activity {r['id']} at {r['ts']} ended "
            f"{r['status']} ({_item_counts(r)}). Commands not safe to repeat: {unsafe}. Run it again only on "
            "purpose, with force." + (f" Note: {plan.batch_note}." if plan.batch_note else ""))


def _daemon_env(clients) -> dict:
    """The `dr_egeria` child's environment: this process's, with RE's daemon credential and platform set
    explicitly (pyegeria's CLI reads EGERIA_USER / EGERIA_USER_PASSWORD / EGERIA_VIEW_SERVER(_URL))."""
    env = dict(os.environ)
    env.update({
        "EGERIA_USER": clients.user_id,
        "EGERIA_USER_PASSWORD": clients.identity.password or "",
        "EGERIA_VIEW_SERVER": clients.view_server,
        "EGERIA_VIEW_SERVER_URL": clients.platform_url,
        "EGERIA_PLATFORM_URL": clients.platform_url,
        "EGERIA_WIDTH": _CONSOLE_WIDTH,
        "NO_COLOR": "1",
    })
    return env


def _summarise(rel: str, items: list[dict]) -> tuple[str, str]:
    counts: dict[str, int] = {}
    for i in items:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    if items and counts.get(OK, 0) == len(items):
        status = OK
    elif counts.get(ERROR) or counts.get(UNKNOWN) or counts.get(NOT_RUN) or not items:
        status = ERROR
    else:
        status = WARNING
    parts = [f"{counts[s]} {s.replace('_', ' ')}" for s in (OK, WARNING, ERROR, UNKNOWN, NOT_RUN) if counts.get(s)]
    return status, f"Dr.Egeria {rel}: " + (", ".join(parts) or "nothing ran")


def _result_from_row(row: dict, plan: DocumentPlan) -> dict:
    """What the caller is shown, read from the persisted row (status words come from what was recorded)."""
    detail = _detail_of(row)
    return {
        "activity_id": row["id"], "document": detail.get("document", plan.rel), "sha256": detail.get("sha256", ""),
        "dry_run": False, "status": row["status"], "summary": row.get("summary", ""), "valid": True, "errors": [],
        "commands": row.get("items") or [], "not_safe_to_repeat": detail.get("not_safe_to_repeat", []),
        "batch_note": detail.get("batch_note", ""), "forced": bool(detail.get("force")),
    }


def run_document(path: str | os.PathLike, *, dry_run: bool = False, force: bool = False, requested_by: str = "",
                 registry=None) -> dict:
    """Run (or, with `dry_run`, only plan) one Dr.Egeria document. THE function the CLI and the route call.

    Raises `DocumentRefused` (bad path or invalid document; nothing written) or `RunRefused` /
    `RepeatRefused` (a run in progress, or the content already applied without `force`)."""
    plan = plan_document(path)
    if dry_run:
        return _plan_view(plan)
    if plan.errors:
        raise DocumentRefused(f"{plan.rel} is not valid: " + "; ".join(plan.errors))
    if not _run_lock.acquire(blocking=False):
        raise RunRefused("another ad-hoc Dr.Egeria run is in progress in this process")
    try:
        return _run_locked(plan, force=force, requested_by=requested_by, registry=registry)
    finally:
        _run_lock.release()


def _run_locked(plan: DocumentPlan, *, force: bool, requested_by: str, registry) -> dict:
    import asyncio

    from resource_explorer import bootstrap
    from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as, egeria_client
    from resource_explorer.registry import ActivityEntry, ProjectRegistry
    from resource_explorer.run_reconciler import process_identity

    if bootstrap._heal_lock.locked():
        raise RunRefused("a bootstrap heal is running; it also runs Dr.Egeria documents")
    registry = registry or ProjectRegistry()
    _guard_repeat(registry, plan, force)

    entry_id = str(uuid.uuid4())
    detail = {"document": plan.rel, "sha256": plan.sha256, "dry_run": False, "force": force,
              "requested_by": requested_by, "not_safe_to_repeat": plan.not_safe_to_repeat,
              "batch_note": plan.batch_note, "daemon_reason": DaemonReason.DR_EGERIA.value}
    planned_items = [c.item(NOT_RUN) for c in plan.commands]

    def write(status: str, summary: str, items: list[dict], *, running: bool) -> None:
        d = dict(detail, _runner=process_identity()) if running else dict(detail, finished_at=_now())
        registry.write_activity(ActivityEntry(
            id=entry_id, ts=started, operation=OPERATION, intent=INTENT, entity_type=ENTITY_TYPE,
            entity_slug=plan.rel, entity_name=plan.path.name, entity_location=str(plan.path), status=status,
            summary=summary, detail=json.dumps(detail_json(d)), items=items))

    def detail_json(d: dict) -> dict:
        return {k: v for k, v in d.items() if v is not None}

    started = _now()
    write(RUNNING, f"Dr.Egeria {plan.rel}: running {len(plan.commands)} command(s)", planned_items, running=True)
    # Another process may have started the same content between the check and the row: the later one stands down.
    try:
        _guard_repeat(registry, plan, force=True, own_id=entry_id)
    except RunRefused as exc:
        write(REFUSED, f"Dr.Egeria {plan.rel}: refused, {exc}", planned_items, running=False)
        raise

    items = list(planned_items)
    try:
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        if on_loop:
            raise RuntimeError("run_document must not run on an event-loop thread (use asyncio.to_thread)")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        steps = None
        try:
            with acting_as(Daemon(DaemonReason.DR_EGERIA)) as identity:
                clients = egeria_client(identity, purpose="Dr.Egeria document run", shared=False)
                steps = _Steps(clients)
                failed = False
                for n, planned in enumerate(plan.commands):
                    if planned.kind != "pyegeria":
                        continue
                    try:
                        items[n] = steps.run(planned)
                    except Exception as exc:  # noqa: BLE001 - recorded on the row with its words; the run stops
                        items[n] = planned.item(ERROR, error=f"{type(exc).__name__}: {exc}")
                        failed = True
                        break
                    finally:
                        write(RUNNING, f"Dr.Egeria {plan.rel}: running", items, running=True)
                if not failed and plan.dr_egeria:
                    proc = bootstrap.run_dr_egeria_document(plan.path, env=_daemon_env(clients))
                    results = dr_egeria_results(plan.dr_egeria, proc.stdout, proc.detail)
                    indexes = [n for n, c in enumerate(plan.commands) if c.kind == "dr_egeria"]
                    for n, item in zip(indexes, results):
                        items[n] = item
        finally:
            if steps is not None:
                steps.close()
            loop.close()
            asyncio.set_event_loop(None)
    except BaseException as exc:  # noqa: BLE001 - the row must not stay "running" (Ctrl-C included); then re-raised
        status = ERROR if isinstance(exc, Exception) else "interrupted"
        summary = f"Dr.Egeria {plan.rel}: {'failed' if status == ERROR else 'interrupted'}, {type(exc).__name__}: {exc}"
        write(status, summary, items, running=False)
        raise

    status, summary = _summarise(plan.rel, items)
    write(status, summary, items, running=False)
    log.info("dr_egeria run %s: %s", entry_id, summary)
    row = registry.get_activity(entry_id)
    return _result_from_row(row, plan)
