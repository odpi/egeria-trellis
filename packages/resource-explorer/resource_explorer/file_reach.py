"""Who can read a file system's path, and so who leads its folder survey (Files step 2, fix round 1).

Design: `docs/design-notes/DESIGN-FILES-REMOTE-REACH.md` sections 1, 2 and 5 (slice 1). Outcomes 1 and 2, and outcome 3
for a file system registered with a remote protocol (S3 slice, 2026-11-02):

       remote_reachable     "reachable by Resource Explorer over S3 from <RE host> as <credential name> · Egeria has no
                             reach to this bucket · checked MM-DD HH:MM"   (one list call under the root answered)
       remote_unreachable   "Resource Explorer could not reach this bucket over S3 from <RE host> as <credential name> ·
                             <S3's reason> · checked ..."

Outcome 3 is always RE-led (Egeria has no remote file connector): the mechanism's check is the whole probe, and
nothing is asked of Egeria. The row's detail holds the credential NAME, never a key or secret.

**The probe never launches anything in Egeria.** Read from Egeria's source: the only engine-host call that tests a path is a
full Folder Survey launch, so a probe by launch would survey (and write a SurveyReport and CSV logs). Verification is in two
honest parts instead:

1. At attach-root, a mounted-prefix check (`probe_filesystem`): is the canonical mount under one of the platform
   container's mounted paths (`egeria_mounts`, the deployment's prefix map)? It records one outcome with a date and the
   path it checked:

       expected_reachable   "expected reachable by Egeria · under <container prefix> · unverified"
       mounts_unknown       "Egeria's mounts unknown on this deployment · RE-led until configured"   (the map is empty)
       re_only              "only Resource Explorer can read this path"   (outside every mount, and RE can read it)
       nobody_can_read      "neither Egeria nor Resource Explorer can read <path>"

2. The first Egeria-led survey is the verification (`settle_from_run`, called where a run's read-back is stored):

       a completed Folder Survey          egeria_verified      "Egeria can read <path> · verified <date>"
       BASIC-FILE-CONNECTOR-404-001 whose message holds an unfilled "~{name}~" placeholder
                                          egeria_connection_broken   "connection broken · placeholder unresolved ~{name}~ ·
                                                               re-attach the root folder · reach stays expected reachable". It says
                                                               nothing about the path: the lever is NOT flipped, the failed launch stays
                                                               as evidence.
       BASIC-FILE-CONNECTOR-404-001       egeria_cannot_read   "Egeria cannot read <path> · <date>", the lever flips to
                                                               Resource Explorer for the next run, the failed launch
                                                               (its engine action GUID) stays on the row as evidence

Every state is DERIVED from the newest stored `resource_reachability` row (`fork_from_row`), never from the branch the code
took. "Not checked yet" and "could not tell" are not outcomes. The path that was checked is stored on the row (in
`error_detail`, as JSON; no DDL) and it is that path that is shown, not today's `canonical_mount_point`.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from resource_explorer import egeria_mounts

log = logging.getLogger(__name__)

# The states.
EGERIA = "egeria"                       # verified by a completed Egeria-led survey
EXPECTED = "expected"                   # under a mounted prefix, unverified: the first Egeria-led survey verifies it
MOUNTS_UNKNOWN = "mounts_unknown"       # the deployment's prefix map is empty
RE_ONLY = "re_only"
EGERIA_CANNOT = "egeria_cannot"         # an Egeria-led survey failed with 404-001, RE can read it
NOBODY = "nobody"                       # neither can read it
CONNECTION_BROKEN = "connection_broken"  # Egeria's 404-001 named an unfilled ~{...}~ placeholder: not a verdict on the path
REMOTE = "remote"                       # outcome 3: Resource Explorer reaches it through a mechanism; Egeria cannot
REMOTE_UNREACHABLE = "remote_unreachable"  # outcome 3, and the mechanism's check was refused
NOT_CHECKED = "not_checked"
UNDECIDED = "undecided"

#: Who surveyed, in the words the report and the screens carry.
SURVEYOR_EGERIA = "Egeria's Folder Survey"
SURVEYOR_RE = "Resource Explorer"

#: The sentence on the SurveyReport RE publishes when it leads (design section 2).
RE_REPORT_SENTENCE = "surveyed by Resource Explorer · Egeria could not reach the path"
RE_UNKNOWN_MOUNTS_REPORT_SENTENCE = "surveyed by Resource Explorer · Egeria's mounts are unknown on this deployment"
EGERIA_REPORT_SENTENCE = "surveyed by Egeria's Folder Survey"

#: Egeria's own code for "the engine host's file system cannot see this folder".
NOT_VISIBLE_CODE = "BASIC-FILE-CONNECTOR-404-001"

#: `probed_from` of a row written by a settled Egeria-led run (the survey-derived rows).
FROM_SURVEY = "egeria-folder-survey"
FROM_ATTACH = "resource-explorer"

#: Said on every Egeria-led report: Egeria's FolderSurveyService adds its file-name log step unconditionally, so the CSV is
#: written inside the container whatever RE asks; RE's half of "off" is that it never reads, stores or shows it.
EGERIA_CSV_LINE = ("Egeria writes a file-name CSV inside its container on every Egeria-led survey; "
                   "Resource Explorer never reads, stores or shows it.")


def analysis_level_line() -> str:
    """"analysis level: top level only", from the level the native port sends with every folder survey."""
    from resource_explorer import native_survey_run as nsr

    return "analysis level: " + nsr.FOLDER_ANALYSIS_LEVEL.lower().replace("_", " ")


#: An unfilled template placeholder as Egeria prints it inside a message: "~{directoryAddress}~".
_UNFILLED_PLACEHOLDER = re.compile(r"~\{[^{}~]*\}~")


def unfilled_placeholder(message: str) -> str:
    """The first "~{name}~" in an Egeria message, or ''."""
    m = _UNFILLED_PLACEHOLDER.search(message or "")
    return m.group(0) if m else ""


def broken_connection_words(placeholder: str) -> str:
    return (f"connection broken · placeholder unresolved {placeholder} · re-attach the root folder · "
            "reach stays expected reachable · unverified").replace("  ", " ")


NOT_CHECKED_WORDS = "not checked yet"
UNDECIDED_WORDS = "could not tell whether Egeria can read this path"

#: Outcomes only a settled run writes; a re-check does not overwrite a verified one.
_VERIFIED = ("egeria_verified", "reachable")
_RE_LEADS = (RE_ONLY, MOUNTS_UNKNOWN, EGERIA_CANNOT, REMOTE)
_EGERIA_LEADS = (EGERIA, EXPECTED, CONNECTION_BROKEN)   # a broken connection is not a verdict: the lever stays


def date_of(iso: str) -> str:
    return (iso or "")[:10]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ForkState:
    """Who can read the path, as the stored rows prove it."""

    state: str = NOT_CHECKED
    sentence: str = NOT_CHECKED_WORDS
    checked_at: str = ""
    #: Who leads the survey: SURVEYOR_EGERIA, SURVEYOR_RE, or "" when nobody can be named.
    surveyor: str = ""
    #: What the probe observed for a state that is not an outcome (Egeria's own words).
    why: str = ""
    #: The path that was checked (stored on the row).
    path: str = ""
    #: The container prefix relied on (expected_reachable only) and where the map got it.
    prefix: str = ""
    prefix_source: str = ""
    report_sentence: str = ""
    #: The failed launch kept as evidence (egeria_cannot_read / nobody_can_read).
    evidence_action_guid: str = ""
    #: The stored path is not today's canonical mount: the row is about a path that has since been edited.
    path_changed: bool = False

    @property
    def decided(self) -> bool:
        return self.state in (EGERIA, EXPECTED, MOUNTS_UNKNOWN, RE_ONLY, EGERIA_CANNOT, NOBODY, CONNECTION_BROKEN,
                              REMOTE, REMOTE_UNREACHABLE)

    @property
    def container_path(self) -> str:       # name kept for older readers
        return self.path

    def as_dict(self) -> dict:
        return {"state": self.state, "sentence": self.sentence, "checked_at": self.checked_at,
                "checked_date": date_of(self.checked_at), "surveyor": self.surveyor, "why": self.why,
                "path": self.path, "container_path": self.path, "prefix": self.prefix,
                "prefix_source": self.prefix_source, "report_sentence": self.report_sentence,
                "evidence_action_guid": self.evidence_action_guid, "path_changed": self.path_changed,
                "decided": self.decided}


def container_path_of(fs) -> str:
    """The path Egeria's container sees: the canonical mount (the same expression the root folder is attached with)."""
    local = (getattr(fs, "local_mount_point", "") or "").rstrip("/")
    return ((getattr(fs, "canonical_mount_point", "") or local) or "").rstrip("/")


def _detail(**kw) -> str:
    return json.dumps({k: v for k, v in kw.items() if v not in (None, "")}, sort_keys=True)


def read_detail(row: dict) -> dict:
    """The structured detail of a row this module wrote ({} for an older or foreign row)."""
    try:
        d = json.loads(row.get("error_detail") or "")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def fork_from_row(fs, row: dict | None) -> ForkState:
    """The state the newest probe row proves. Pure: a row (or none) in, a state out."""
    if not row:
        return ForkState()
    outcome = row.get("outcome") or ""
    at = row.get("probed_at") or ""
    date = date_of(at)
    d = read_detail(row)
    current = container_path_of(fs)
    path = d.get("path") or current
    changed = bool(d.get("path")) and d["path"] != current
    if changed:
        # The row answers for a path that is no longer the canonical mount: nothing is known about the new one.
        return ForkState(path=path, path_changed=True,
                         why=f"the canonical mount changed since this was checked ({path})")
    common = dict(checked_at=at, path=path, evidence_action_guid=row.get("engine_action_guid") or "")
    if outcome == "remote_reachable":
        from resource_explorer.reach_mechanisms import s3 as s3m

        return ForkState(state=REMOTE, sentence=d.get("sentence") or s3m.reach_sentence(
                             d.get("host", ""), d.get("credential_name", ""), at),
                         surveyor=SURVEYOR_RE, why=d.get("write_permission", ""),
                         report_sentence=s3m.publish_sentence(d.get("host", ""), d.get("credential_name", "")),
                         **common)
    if outcome == "remote_unreachable":
        return ForkState(state=REMOTE_UNREACHABLE, sentence=d.get("sentence") or f"{UNDECIDED_WORDS} · checked {date}",
                         why=d.get("error", ""), **common)
    if outcome in _VERIFIED:
        return ForkState(state=EGERIA, sentence=f"Egeria can read {path} · verified {date}", surveyor=SURVEYOR_EGERIA,
                         report_sentence=EGERIA_REPORT_SENTENCE, **common)
    if outcome == "expected_reachable":
        prefix = d.get("prefix", "")
        return ForkState(
            state=EXPECTED, sentence=f"expected reachable by Egeria · under {prefix} · unverified · checked {date}",
            surveyor=SURVEYOR_EGERIA, prefix=prefix, prefix_source=d.get("source", ""),
            report_sentence=EGERIA_REPORT_SENTENCE, **common)
    if outcome == "mounts_unknown":
        return ForkState(
            state=MOUNTS_UNKNOWN,
            sentence=f"Egeria's mounts unknown on this deployment · RE-led until configured · checked {date}",
            surveyor=SURVEYOR_RE, report_sentence=RE_UNKNOWN_MOUNTS_REPORT_SENTENCE, **common)
    if outcome == "re_only":
        return ForkState(state=RE_ONLY, sentence=f"only Resource Explorer can read this path · checked {date}",
                         surveyor=SURVEYOR_RE, report_sentence=RE_REPORT_SENTENCE, **common)
    if outcome == "egeria_cannot_read" and unfilled_placeholder(d.get("egeria_message") or row.get("error_detail") or ""):
        # A row the OLD rule wrongly flipped (404-001 naming "~{directoryAddress}~"): derived at read time as a broken
        # connection, so no stored row has to be hand-edited. The evidence (message, engine action) is unchanged.
        outcome = "egeria_connection_broken"
        d = {**d, "placeholder": unfilled_placeholder(d.get("egeria_message") or row.get("error_detail") or "")}
    if outcome == "egeria_connection_broken":
        ph = d.get("placeholder", "")
        return ForkState(state=CONNECTION_BROKEN, sentence=f"{broken_connection_words(ph)} · {date}",
                         surveyor=SURVEYOR_EGERIA, report_sentence=EGERIA_REPORT_SENTENCE,
                         why=d.get("egeria_message", ""), **common)
    if outcome == "egeria_cannot_read":
        return ForkState(state=EGERIA_CANNOT, sentence=f"Egeria cannot read {path} · {date}", surveyor=SURVEYOR_RE,
                         report_sentence=RE_REPORT_SENTENCE, why=d.get("egeria_message", ""), **common)
    if outcome == "nobody_can_read":
        return ForkState(state=NOBODY, sentence=f"neither Egeria nor Resource Explorer can read {path} · {date}",
                         why=d.get("re_why", "") or d.get("egeria_message", ""), **common)
    # Anything else did not establish who can read: it is neither outcome. Egeria's own words stay.
    why = (d.get("egeria_message") or row.get("error_detail") or row.get("error_code") or outcome).strip()
    return ForkState(state=UNDECIDED, sentence=f"{UNDECIDED_WORDS} · checked {date}", checked_at=at, why=why)


def fork_for(registry, fs) -> ForkState:
    """The state from the newest stored probe row for this file system. Registry-only: no Egeria call."""
    if registry is None or fs is None:
        return ForkState()
    return fork_from_row(fs, registry.get_latest_reachability(fs.slug))


def re_can_read(path: str) -> tuple[bool, str]:
    """Can THIS server open the folder? A stat and an access check; no file is read."""
    path = (path or "").strip()
    if not path:
        return False, "no path"
    if not os.path.isdir(path):
        return False, "not a folder on this server"
    if not os.access(path, os.R_OK | os.X_OK):
        return False, "this server may not list it"
    return True, ""


def _record(registry, slug: str, outcome: str, *, probed_from: str, error_code: str = "", detail: str = "",
            engine_action_guid: str = "") -> None:
    registry.record_reachability_check(
        slug, probed_at=_now(), outcome=outcome, probed_from=probed_from, error_code=error_code,
        error_detail=detail, latency_ms=None, engine_action_guid=engine_action_guid)


def probe_filesystem(slug: str, registry, *, mount_map: list | None = None,
                     read_check: Callable[[str], tuple[bool, str]] = re_can_read) -> ForkState:
    """The mounted-prefix check, recorded as one row with a date and the path checked. Asks Egeria NOTHING (no engine
    action, no read), so it can run on any press. Runs at attach-root and on "Check again".

    A row written by a settled Egeria-led run that says Egeria CAN read this very path stands: a prefix check cannot
    outweigh a survey that read it, so a re-check leaves it alone. A negative settled row (Egeria cannot read) is
    re-evaluated, which is how a person tries again after fixing the mounts; its row stays in the history as evidence."""
    fs = registry.get_filesystem(slug)
    if fs is None:
        return ForkState()
    from resource_explorer import reach_mechanisms

    if reach_mechanisms.is_remote(fs):
        return probe_remote(fs, registry)
    path = container_path_of(fs)
    latest = registry.get_latest_reachability(slug)
    if latest and latest.get("outcome") in _VERIFIED and read_detail(latest).get("path", path) == path:
        return fork_from_row(fs, latest)
    pairs = egeria_mounts.get_mount_map() if mount_map is None else mount_map
    if not pairs:
        _record(registry, slug, "mounts_unknown", probed_from=FROM_ATTACH, detail=_detail(path=path))
        return fork_for(registry, fs)
    hit = egeria_mounts.prefix_for(path, pairs)
    if hit is not None:
        _record(registry, slug, "expected_reachable", probed_from=FROM_ATTACH,
                detail=_detail(path=path, prefix=hit.container_prefix, source=f"{hit.source} · {hit.read_on}"))
        return fork_for(registry, fs)
    readable, why = read_check(fs.local_mount_point)
    if readable:
        _record(registry, slug, "re_only", probed_from=FROM_ATTACH, detail=_detail(path=path))
    else:
        _record(registry, slug, "nobody_can_read", probed_from=FROM_ATTACH, detail=_detail(path=path, re_why=why))
    return fork_for(registry, fs)


def probe_remote(fs, registry, *, mechanism=None) -> ForkState:
    """Outcome 3: the mechanism's own check (S3: one ListObjectsV2 with MaxKeys=1 under the root), recorded as one row.
    Asks Egeria nothing. A mechanism that cannot even be built (no key in the store, an unknown profile) is recorded
    as unreachable with its redacted reason."""
    from resource_explorer import reach_mechanisms

    path = container_path_of(fs)
    try:
        mech = mechanism or reach_mechanisms.mechanism_for(fs)
        check = mech.check_reach()
    except reach_mechanisms.ReachError as exc:
        from resource_explorer.reach_mechanisms import s3 as s3m

        settings = s3m.S3Settings.from_filesystem(fs)
        at = _now()
        check = {"reachable": False, "error": str(exc), "host": s3m.re_host(),
                 "credential_name": settings.credential_name(), "protocol": getattr(fs, "protocol", ""),
                 "sentence": s3m.unreachable_sentence(s3m.re_host(), settings.credential_name(), str(exc), at)}
    outcome = "remote_reachable" if check.get("reachable") else "remote_unreachable"
    _record(registry, fs.slug, outcome, probed_from=FROM_ATTACH,
            error_code="" if check.get("reachable") else "REMOTE-REACH",
            detail=_detail(path=path, protocol=check.get("protocol"), host=check.get("host"),
                           credential_name=check.get("credential_name"), sentence=check.get("sentence"),
                           error=check.get("error"), write_permission=check.get("write_permission")))
    return fork_for(registry, fs)


def ensure_checked(slug: str, registry) -> ForkState:
    """The stored state; the mounted-prefix check runs only when there is no row at all (so a press on a file system
    never re-evaluates a settled answer, and never launches anything)."""
    fs = registry.get_filesystem(slug)
    if fs is None:
        return ForkState()
    if registry.get_latest_reachability(slug) is None:
        try:
            return probe_filesystem(slug, registry)
        except Exception:  # noqa: BLE001 -- a check that could not run reads "not checked yet", never an error page
            log.warning("%s: the mounted-prefix check could not run", slug, exc_info=True)
            return ForkState()
    return fork_for(registry, fs)


def settle_from_run(registry, slug: str, derived: dict, *,
                    read_check: Callable[[str], tuple[bool, str]] = re_can_read) -> str:
    """The first Egeria-led survey is the verification. `derived` is `native_survey_run.derive_native_state` of the newest
    folder-survey run. A completed run records "Egeria can read <path> · verified <date>"; a failed one carrying
    BASIC-FILE-CONNECTOR-404-001 records "Egeria cannot read <path> · <date>" (or "neither ... can read" when this
    server cannot read it either) with the failed launch's GUID as evidence. Once per engine action. Returns the outcome
    written, or ''."""
    fs = registry.get_filesystem(slug)
    guid = (derived or {}).get("engine_action_guid") or ""
    if fs is None or not guid:
        return ""
    state, message = derived.get("state"), derived.get("message") or ""
    if state == "complete":
        outcome = "egeria_verified"
    elif state == "failed" and NOT_VISIBLE_CODE in message:
        # A 404-001 that names an unfilled "~{...}~" placeholder is about the folder's CONNECTION, not the path: the
        # path Egeria tried to open was the placeholder text itself. It is no verdict on reachability (live 2026-10-21).
        outcome = "egeria_connection_broken" if unfilled_placeholder(message) else "egeria_cannot_read"
    else:
        return ""
    if any(r.get("engine_action_guid") == guid for r in registry.list_reachability_history(slug, limit=100)):
        return ""
    path = container_path_of(fs)
    if outcome == "egeria_verified":
        _record(registry, slug, outcome, probed_from=FROM_SURVEY, detail=_detail(path=path), engine_action_guid=guid)
        return outcome
    if outcome == "egeria_connection_broken":
        _record(registry, slug, outcome, probed_from=FROM_SURVEY, error_code=NOT_VISIBLE_CODE,
                detail=_detail(path=path, egeria_message=message[:400], placeholder=unfilled_placeholder(message)),
                engine_action_guid=guid)
        return outcome
    readable, why = read_check(fs.local_mount_point)
    if not readable:
        outcome = "nobody_can_read"
    _record(registry, slug, outcome, probed_from=FROM_SURVEY, error_code=NOT_VISIBLE_CODE,
            detail=_detail(path=path, egeria_message=message[:400], re_why=why), engine_action_guid=guid)
    return outcome


def refusal_words(state: ForkState, *, for_egeria_lead: bool) -> str:
    """Why a press that needs this lead cannot go ahead, '' when it can. Words come from the state."""
    if for_egeria_lead:
        if state.state in _EGERIA_LEADS:
            return ""
        if state.state in _RE_LEADS:
            return state.sentence
        if state.state == REMOTE:
            from resource_explorer.reach_mechanisms.s3 import EGERIA_NO_REACH

            return f"{EGERIA_NO_REACH} · Resource Explorer leads"
        if state.state in (NOBODY, UNDECIDED, CONNECTION_BROKEN, REMOTE_UNREACHABLE):
            return state.sentence + (f" · {state.why}" if state.why and state.why not in state.sentence else "")
        return NOT_CHECKED_WORDS if not state.path_changed else f"{NOT_CHECKED_WORDS} · {state.why}"
    # Resource Explorer's walk.
    if state.state in _RE_LEADS:
        return ""
    if state.state in _EGERIA_LEADS:
        return "Egeria can read this path · its Folder Survey leads" if state.state == EGERIA else (
            state.sentence + " · its Folder Survey leads")
    if state.state in (NOBODY, UNDECIDED, CONNECTION_BROKEN, REMOTE_UNREACHABLE):
        return state.sentence + (f" · {state.why}" if state.why and state.why not in state.sentence else "")
    return NOT_CHECKED_WORDS if not state.path_changed else f"{NOT_CHECKED_WORDS} · {state.why}"
