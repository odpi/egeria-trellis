# -*- coding: utf-8 -*-
"""Seed RE's standard Data Classes and their keyword sets into Egeria.

Runs in two ways, through ONE function (`ensure_data_classes`):

* at worker start and every bootstrap-monitor pass (`bootstrap._loop`, leader-elected under
  `LOCK_BOOTSTRAP`, after the Dr.Egeria batch heal), and
* by hand: `python -m resource_explorer.surveyors.database.bootstrap_data_classes`.

**The seed** is `configdata/data_classes.yaml` (plus `RE_DATA_CLASSES_FILE`, an optional second file
of more entries). It is validated at load; a bad entry is named. At most `MAX_DATA_CLASSES` classes
and `MAX_KEYWORDS_PER_CLASS` keywords each. `RE_DATA_CLASSES_FILE` must be set in the WORKER
process's environment (the bootstrap monitor runs there); the web process reads it too, for the
Curate rules and the survey's keyword fallback, so set it on both.

**The canary.** One typed read: a plain `ValidValueDefinition` the bootstrap owns,
`ValidValueDefinition::ResourceExplorer::DataClassSeed::<fingerprint of the seed>`. It is created
last, only after every element of the seed is present, so its presence means "this seed ran to
completion". Present: the classes and sets are verified by typed lookup (each resolves to a GUID)
and reported "data classes: present, N sets"; nothing is created. Absent: the heal runs. Changing
the seed file changes the fingerprint, so a new class or keyword is healed on the next start.

**The heal creates only what is missing,** one element at a time, each looked up first:
the DataClass by qualifiedName; the keyword set and keywords TYPED as `ValidValueDefinition`
(Egeria's Reference Data view defaults its type to `ReferenceDataValue`, which hides plain
definitions). A class already present is never re-created; its keywords are reconciled by one typed
find on the keyword prefix. Nothing is ever changed or deleted.

**Edits are not propagated.** A class, set or keyword already present by qualifiedName is never
updated: changing an existing entry's displayName, description or dataPatterns in the seed changes
the fingerprint, so the next pass re-checks everything, but only ADDS what is missing (a new
keyword). Renamed or removed keywords stay in Egeria; change them there by hand.

**Cost.** A present pass is 13 reads for the shipped seed (the canary, each DataClass and each
keyword set), every monitor pass (10 minutes). Keywords and links are read only when healing.

**A failed check never creates.** A lookup that raises is "could not check" (the 6.2.2 audit's
finding): the pass stops there, records why, and creates nothing more.

**Proof.** Every pass that decides something writes one activity row (operation `bootstrap`,
entity `egeria/data-classes`) whose summary is the outcome line and whose items name every element
created with its GUID, or Egeria's own sentence when a create was refused. A second start creates
nothing and says "present".
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from resource_explorer.surveyors.survey_definition_reader import _as_guid
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

log = logging.getLogger(__name__)

#: The shipped seed.
SEED_FILE = Path(__file__).resolve().parents[2] / "configdata" / "data_classes.yaml"
#: An optional second seed file whose entries are added to the shipped ones.
EXTRA_SEED_ENV = "RE_DATA_CLASSES_FILE"

#: The type the keyword sets, keywords and the canary are created as, and looked up by.
VVD_TYPE = "ValidValueDefinition"
CANARY_PREFIX = "ValidValueDefinition::ResourceExplorer::DataClassSeed::"

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_KEYWORD_RE = re.compile(r"^[a-z0-9_]+$")
_FIELDS = ("name", "displayName", "description", "dataPatterns")
#: Caps on the seed: each keyword is one Egeria element and one link, created at start-up.
MAX_DATA_CLASSES = 100
MAX_KEYWORDS_PER_CLASS = 50


# ── the seed ──────────────────────────────────────────────────────────────────

class SeedError(ValueError):
    """The seed file is unreadable or an entry is invalid; the message names the file and entry."""


def _validate(raw: Any, source: str) -> list[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("data_classes"), list):
        raise SeedError(f"{source}: expected a top-level 'data_classes' list")
    out: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw["data_classes"], start=1):
        label = entry.get("name") if isinstance(entry, dict) else None
        where = f"{source}: entry {i}" + (f" ({label})" if isinstance(label, str) and label else "")
        if not isinstance(entry, dict):
            raise SeedError(f"{where}: not a mapping")
        unknown = sorted(set(entry) - set(_FIELDS))
        if unknown:
            raise SeedError(f"{where}: unknown field(s) {unknown}")
        name = entry.get("name")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise SeedError(f"{where}: 'name' must be letters and digits, starting with a letter")
        if name in seen:
            raise SeedError(f"{where}: data class '{name}' appears twice")
        for key in ("displayName", "description"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise SeedError(f"{where}: '{key}' must be a non-empty string")
        patterns = entry.get("dataPatterns")
        if not isinstance(patterns, list) or not patterns:
            raise SeedError(f"{where}: 'dataPatterns' must be a non-empty list of keywords")
        for kw in patterns:
            if not isinstance(kw, str) or not _KEYWORD_RE.match(kw):
                raise SeedError(f"{where}: keyword {kw!r} must be lower case letters, digits and underscores")
        if len(patterns) > MAX_KEYWORDS_PER_CLASS:
            raise SeedError(f"{where}: {len(patterns)} keywords, more than {MAX_KEYWORDS_PER_CLASS}")
        if len(set(patterns)) != len(patterns):
            raise SeedError(f"{where}: a keyword appears twice")
        seen.add(name)
        out.append({"name": name, "displayName": entry["displayName"].strip(),
                    "description": entry["description"].strip(), "dataPatterns": list(patterns)})
    if not out:
        raise SeedError(f"{source}: no data classes")
    if len(out) > MAX_DATA_CLASSES:
        raise SeedError(f"{source}: {len(out)} data classes, more than {MAX_DATA_CLASSES}")
    return out


def _read(path: Path) -> Any:
    import yaml

    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise SeedError(f"{path}: unreadable ({exc})") from exc


def load_data_class_seed(path: str | Path | None = None, extra_path: str | None = None) -> list[dict]:
    """The validated seed: the shipped file (or `path`), plus `RE_DATA_CLASSES_FILE`'s entries
    (or `extra_path`'s; "" for none). A name in both is an error, never an override."""
    base = Path(path) if path else SEED_FILE
    entries = _validate(_read(base), str(base))
    extra = os.environ.get(EXTRA_SEED_ENV, "") if extra_path is None else extra_path
    if extra:
        more = _validate(_read(Path(extra)), extra)
        clash = sorted({e["name"] for e in entries} & {e["name"] for e in more})
        if clash:
            raise SeedError(f"{extra}: data class(es) {clash} are already in {base}")
        entries += more
        if len(entries) > MAX_DATA_CLASSES:
            raise SeedError(f"{extra}: {len(entries)} data classes in all, more than {MAX_DATA_CLASSES}")
    return entries


#: The shipped seed (the six standard PII data classes), validated at import.
STANDARD_DATA_CLASSES = load_data_class_seed(extra_path="")


def current_seed() -> list[dict]:
    """The configured seed for READERS (the Curate rules, the survey's PII keywords): the shipped
    file plus `RE_DATA_CLASSES_FILE`. A bad second file is logged and the shipped seed answers, so
    a reader never fails on it (the start-up pass records the error)."""
    try:
        return load_data_class_seed()
    except SeedError as exc:
        log.warning("data classes: %s — using the shipped seed", exc)
        return STANDARD_DATA_CLASSES


def seed_fingerprint(seed: list[dict]) -> str:
    return hashlib.sha256(json.dumps(seed, sort_keys=True).encode()).hexdigest()[:12]


def canary_qualified_name(seed: list[dict]) -> str:
    return CANARY_PREFIX + seed_fingerprint(seed)


def data_class_qn(name: str) -> str:
    return f"DataClass::{name}"


def keyword_set_qn(name: str) -> str:
    return f"ValidValuesSet::{name}Keywords"


def keyword_qn(name: str, keyword: str) -> str:
    return f"ValidValueDefinition::{name}Keyword::{keyword}"


# ── lookups ───────────────────────────────────────────────────────────────────

class CouldNotCheck(RuntimeError):
    """An existence lookup raised. Never read as "absent": nothing is created on a failed check."""


def _existing_guid(expert, qualified_name: str, type_name: str | None = None) -> str | None:
    """The element's GUID, None when Egeria answers that it holds none, `CouldNotCheck` when the
    lookup itself failed.

    Exact qualifiedName only, read through the unique-name endpoint (`egeria_lookup`): the exact-value search
    timed out on a large repository. With `type_name`, the element must be of that type (a plain
    ValidValueDefinition is not resolved through the Reference Data view's default type); another type holding
    the name is `CouldNotCheck`, never "absent". Egeria's explicit "No elements found" is None; a raise is
    `CouldNotCheck`.
    """
    from resource_explorer.egeria_lookup import guid_by_qualified_name

    if expert is None:
        raise CouldNotCheck(f"could not check '{qualified_name}': no MetadataExpert client")
    try:
        return guid_by_qualified_name(expert, qualified_name, type_name=type_name)
    except Exception as exc:  # noqa: BLE001 - re-raised as the one "could not check" outcome
        raise CouldNotCheck(f"could not check '{qualified_name}': {_sentence(exc)}") from exc


def _existing_keywords(ref_manager, name: str) -> dict[str, str]:
    """{qualifiedName: guid} of the keyword definitions Egeria holds for one data class: one TYPED
    find on the keyword prefix. "No elements found" is {}; a raise or an unreadable answer is
    `CouldNotCheck`."""
    prefix = keyword_qn(name, "")
    try:
        raw = ref_manager.find_valid_value_definitions(
            search_string=prefix, starts_with=True, output_format="JSON",
            metadata_element_type=VVD_TYPE,
        )
    except Exception as exc:  # noqa: BLE001
        raise CouldNotCheck(f"could not check the keywords of '{name}': {_sentence(exc)}") from exc
    if isinstance(raw, str) and "no " in raw.lower() and "found" in raw.lower():
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("elementHeader"), dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise CouldNotCheck(f"could not check the keywords of '{name}': unreadable answer {str(raw)[:80]!r}")
    out: dict[str, str] = {}
    for el in raw:
        header = el.get("elementHeader") if isinstance(el, dict) else None
        qn = (el.get("properties") or {}).get("qualifiedName") if isinstance(el, dict) else None
        if not isinstance(header, dict) or not header.get("guid") or not isinstance(qn, str):
            raise CouldNotCheck(f"could not check the keywords of '{name}': an element without "
                                f"elementHeader.guid and qualifiedName")
        if qn.startswith(prefix):
            out[qn] = str(header["guid"])
    return out


def _sentence(exc: BaseException) -> str:
    """Egeria's own words for a failure (whitespace collapsed), never cut to a summary."""
    return " ".join(str(exc).split()) or type(exc).__name__


# ── bodies ────────────────────────────────────────────────────────────────────

def _data_class_body(dc: dict) -> dict:
    return {
        "class": "NewElementRequestBody",
        "isOwnAnchor": True,
        "properties": {
            # Live-verified 2026-09-22: omitting "class" makes Egeria reject the create with a 400
            # (pyegeria's `_async_create_element_body_request` needs `properties.class`).
            "class": "DataClassProperties",
            "qualifiedName": data_class_qn(dc["name"]),
            "displayName": dc["displayName"],
            "description": dc["description"],
            "dataPatterns": dc["dataPatterns"],
        },
    }


def _vvd_body(qualified_name: str, display_name: str, **props) -> dict:
    return {
        "class": "NewElementRequestBody",
        "isOwnAnchor": True,
        "properties": {"class": "ValidValueDefinitionProperties", "qualifiedName": qualified_name,
                       "displayName": display_name, "dataType": "string",
                       "scope": "ResourceExplorer", **props},
    }


#: The only properties Egeria 6.2's `ValidValueMemberProperties` has (read live 2026-10-13: a
#: ValidValueMember relationship carries `isDefaultValue` and `ordinal`). Anything else is dropped.
MEMBER_PROPERTY_NAMES = ("isDefaultValue", "ordinal")


def _member_body(keyword: str) -> dict:
    # An explicit body, never the no-body call: pyegeria would synthesise one and POST it
    # un-serialised (egeria_reference_catalog.publish_proposed_valid_value_set's docstring).
    # Egeria's names only: `label` and `description` used to be sent and were silently dropped.
    return {
        "class": "NewRelationshipRequestBody",
        "properties": {"class": "ValidValueMemberProperties", "isDefaultValue": False},
    }


# ── the outcome ───────────────────────────────────────────────────────────────

PRESENT = "present"
CREATED = "created"
COULD_NOT_CHECK = "could not check"
FAILED = "failed"

#: Relationship types the heal checks before it links (read live 2026-10-13: a DataClass and its
#: keyword set are linked by `DataValueDefinition`; a set and a keyword by `ValidValueMember`).
MEMBER_REL = "ValidValueMember"
DEFINITION_REL = "DataValueDefinition"


@dataclass
class SeedOutcome:
    status: str
    sets: int = 0
    created: list[dict] = field(default_factory=list)      # {"kind", "qualified_name", "guid"}
    reason: str = ""
    canary: str = ""

    @property
    def elements_created(self) -> list[dict]:
        """Data classes, keyword sets and keywords created (not links, not the canary)."""
        return [c for c in self.created if c.get("guid") and c["kind"] not in ("canary", "link")]

    @property
    def links_made(self) -> list[dict]:
        return [c for c in self.created if c["kind"] == "link" and not c.get("egeria_said")]

    @property
    def line(self) -> str:
        if self.status == PRESENT:
            return f"data classes: present, {self.sets} sets"
        if self.status == CREATED:
            n, k = len(self.elements_created), len(self.links_made)
            what = [f"{n} element{'' if n == 1 else 's'} created"] if n else []
            what += [f"{k} link{'' if k == 1 else 's'} made"] if k else []
            return f"data classes: created, {self.sets} sets ({', '.join(what) or 'nothing new'})"
        return f"data classes: {self.status} · {self.reason}"

    def change_key(self) -> tuple:
        """What makes a pass worth a new log line and activity row: the status, the reason with
        ids and numbers normalised, and the GUIDs actually created (a refused create carries none,
        so the same refusal every ten minutes is one row, not one per pass)."""
        return (self.status, _normalise(self.reason),
                frozenset(c["guid"] for c in self.created if c.get("guid")))

    def as_dict(self) -> dict:
        return {"status": self.status, "sets": self.sets, "created": self.created,
                "reason": self.reason, "canary": self.canary, "line": self.line}


_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{12,}\b|\d+",
                    re.IGNORECASE)


def _normalise(text: str) -> str:
    return _ID_RE.sub("#", text or "")


class _Stop(Exception):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def _may_write(still_leader) -> None:
    """LOW-6: before the first write of a pass, confirm this process still leads."""
    if still_leader is not None and still_leader() is False:
        raise _Stop(COULD_NOT_CHECK, "this process no longer holds the bootstrap lock; nothing written")


def _create(fn, kind: str, qualified_name: str, created: list[dict], *args, **kwargs) -> str:
    """One create, its proof item appended. A refusal stops the pass with Egeria's sentence."""
    try:
        guid = _as_guid(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001
        created.append({"kind": kind, "qualified_name": qualified_name, "egeria_said": _sentence(exc)})
        raise _Stop(FAILED, f"creating '{qualified_name}': {_sentence(exc)}") from exc
    if not guid:
        created.append({"kind": kind, "qualified_name": qualified_name, "egeria_said": "no GUID returned"})
        raise _Stop(FAILED, f"creating '{qualified_name}': Egeria returned no GUID")
    created.append({"kind": kind, "qualified_name": qualified_name, "guid": guid})
    log.info("data classes: created %s '%s' (%s)", kind, qualified_name, guid)
    return guid


def _link(fn, what: str, created: list[dict], *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        created.append({"kind": "link", "qualified_name": what, "egeria_said": _sentence(exc)})
        raise _Stop(FAILED, f"linking {what}: {_sentence(exc)}") from exc
    created.append({"kind": "link", "qualified_name": what})


def _related_guids(expert, guid: str, relationship_type: str) -> set[str]:
    """GUIDs of the elements linked to `guid` by `relationship_type` (MetadataExpert, as
    `~/.re-gate/scripts/readback_dc.py` reads them). pyegeria's "No elements found" / "No elements
    returned" is the empty set; a raise or any other answer is `CouldNotCheck`."""
    try:
        raw = expert.get_related_metadata_elements(guid, relationship_type, body={"class": "ResultsRequestBody"})
    except Exception as exc:  # noqa: BLE001
        raise CouldNotCheck(f"could not check the {relationship_type} links of {guid}: {_sentence(exc)}") from exc
    if isinstance(raw, str) and raw.lower().startswith("no element"):
        return set()
    items = raw.get("elementList") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise CouldNotCheck(f"could not check the {relationship_type} links of {guid}: "
                            f"unreadable answer {str(raw)[:80]!r}")
    out = set()
    for it in items:
        el = it.get("element") if isinstance(it, dict) else None
        if not isinstance(el, dict) or not el.get("elementGUID"):
            raise CouldNotCheck(f"could not check the {relationship_type} links of {guid}: "
                                f"a related element without a GUID")
        out.add(str(el["elementGUID"]))
    return out


def _missing_elements(expert, seed: list[dict]) -> tuple[int, list[str]]:
    missing, sets = [], 0
    for dc in seed:
        if not _existing_guid(expert, data_class_qn(dc["name"])):
            missing.append(data_class_qn(dc["name"]))
        if _existing_guid(expert, keyword_set_qn(dc["name"]), VVD_TYPE):
            sets += 1
        else:
            missing.append(keyword_set_qn(dc["name"]))
    return sets, missing


def _heal(designer, ref_manager, expert, seed: list[dict], canary: str, created: list[dict],
          canary_present: bool, still_leader=None) -> SeedOutcome:
    sets = 0
    checked_leader = False

    def before_write():
        nonlocal checked_leader
        if not checked_leader:
            _may_write(still_leader)
            checked_leader = True

    for dc in seed:
        name = dc["name"]
        dc_qn, set_qn = data_class_qn(name), keyword_set_qn(name)

        dc_guid = _existing_guid(expert, dc_qn)
        dc_new = not dc_guid
        if dc_new:
            before_write()
            dc_guid = _create(designer.create_data_class, "DataClass", dc_qn, created, _data_class_body(dc))

        set_guid = _existing_guid(expert, set_qn, VVD_TYPE)
        set_new = not set_guid
        if set_new:
            before_write()
            set_guid = _create(ref_manager.create_valid_value_definition, "keyword set", set_qn, created,
                               _vvd_body(set_qn, f"{dc['displayName']} Keywords",
                                         description=f"Valid keyword list to match {dc['displayName']} columns."))
        sets += 1

        # Keywords: an existing set's are reconciled by one typed find, and its member links are
        # READ before any link is made (MED-1: a keyword whose link failed on an earlier pass is
        # linked now, never re-created). A new set has neither.
        have = {} if set_new else _existing_keywords(ref_manager, name)
        members = set() if set_new else _related_guids(expert, set_guid, MEMBER_REL)
        for kw in dc["dataPatterns"]:
            kw_qn = keyword_qn(name, kw)
            kw_guid = have.get(kw_qn)
            if not kw_guid:
                before_write()
                kw_guid = _create(ref_manager.create_valid_value_definition, "keyword", kw_qn, created,
                                  _vvd_body(kw_qn, kw, preferredValue=kw))
            if kw_guid not in members:
                before_write()
                _link(ref_manager.link_valid_value_definition, f"'{kw}' into {set_qn}", created,
                      set_guid, kw_guid, body=_member_body(kw))

        # The set is the class's definition: linked when the link is not there (read first, unless
        # either end is new in this pass and so cannot have one).
        linked = set() if (dc_new or set_new) else _related_guids(expert, set_guid, DEFINITION_REL)
        if dc_guid not in linked:
            before_write()
            _link(designer.link_data_class_definition, f"{set_qn} to {dc_qn}", created,
                  data_definition_guid=set_guid, data_class_guid=dc_guid)

    # Last, and only when every element and link above is present: the canary.
    if not canary_present:
        before_write()
        _create(ref_manager.create_valid_value_definition, "canary", canary, created,
                _vvd_body(canary, "RE data-class seed",
                          description="Present when Resource Explorer's data-class seed with this fingerprint "
                                      "ran to completion. Created by RE; do not edit."))
    outcome = SeedOutcome(CREATED, sets=sets, created=created, canary=canary)
    if not outcome.elements_created and not outcome.links_made:
        outcome.status = PRESENT            # everything was there; at most the canary was recorded
    return outcome


def ensure_data_classes(designer, ref_manager, expert=None, seed: list[dict] | None = None,
                        still_leader=None) -> SeedOutcome:
    """The one code path: canary read, then verify (canary present) or heal. Never raises.

    A present pass costs 13 reads for the shipped seed (the canary, then each DataClass and each
    keyword set by lookup); it does not re-read keywords or links. When the canary is present but
    an element is missing (LOW-1), the pass drops through to the heal, which checks every element
    and link and creates only what is missing; it does not create a second canary.

    `expert` (MetadataExpert) reads the links before linking; `still_leader()` returning False
    stops the pass before its first write.
    """
    created: list[dict] = []
    canary = ""
    try:
        seed = seed if seed is not None else load_data_class_seed()
        canary = canary_qualified_name(seed)
        canary_present = bool(_existing_guid(expert, canary, VVD_TYPE))
        if canary_present:
            sets, missing = _missing_elements(expert, seed)
            if not missing:
                return SeedOutcome(PRESENT, sets=sets, canary=canary)
            log.warning("data classes: canary present but %d missing (%s) — healing",
                        len(missing), ", ".join(missing))
        else:
            log.info("data classes: canary %s absent — checking each element", canary)
        if expert is None:
            raise CouldNotCheck("could not check the links: no MetadataExpert client")
        return _heal(designer, ref_manager, expert, seed, canary, created, canary_present, still_leader)
    except CouldNotCheck as exc:
        status = FAILED if any(c.get("guid") for c in created) else COULD_NOT_CHECK
        return SeedOutcome(status, created=created, reason=str(exc), canary=canary)
    except _Stop as stop:
        return SeedOutcome(stop.status, created=created, reason=stop.reason, canary=canary)
    except SeedError as exc:
        return SeedOutcome(FAILED, reason=f"the seed file: {exc}", canary=canary)
    except Exception as exc:  # noqa: BLE001 - a startup heal never raises
        # LOW-5: after any create this is a failed pass, not one that never got to check.
        status = FAILED if any(c.get("guid") for c in created) else COULD_NOT_CHECK
        return SeedOutcome(status, created=created, reason=_sentence(exc), canary=canary)


# ── proof ─────────────────────────────────────────────────────────────────────

def record_outcome(outcome: SeedOutcome, registry=None) -> str:
    """One activity row for the pass: summary = the outcome line, items = every element created
    (GUID) or refused (Egeria's sentence). Best effort; returns the row id or ""."""
    try:
        from resource_explorer.registry import ActivityEntry, ProjectRegistry

        registry = registry if registry is not None else ProjectRegistry()
        entry_id = str(uuid.uuid4())
        registry.write_activity(ActivityEntry(
            id=entry_id, ts=datetime.now(timezone.utc).isoformat(), operation="bootstrap",
            intent="curate", entity_type="egeria", entity_slug="data-classes",
            entity_name="Data classes", entity_location=outcome.canary,
            status="ok" if outcome.status in (PRESENT, CREATED) else "error",
            summary=outcome.line, detail=json.dumps(outcome.as_dict()), items=outcome.created,
        ))
        return entry_id
    except Exception as exc:  # noqa: BLE001 - the log line still says it
        log.warning("data classes: could not record the outcome: %s", exc)
        return ""


def _log_line(outcome: SeedOutcome) -> None:
    if outcome.status in (PRESENT, CREATED):
        log.info(outcome.line)
    else:
        log.warning(outcome.line)


# ── the monitor's pass ────────────────────────────────────────────────────────

_last_key: tuple | None = None
_last_outcome: SeedOutcome | None = None
_state_lock = threading.Lock()


def _clients(identity, purpose: str):
    from pyegeria.omvs.data_designer import DataDesigner
    from pyegeria.omvs.metadata_expert import MetadataExpert
    from pyegeria.omvs.reference_data import ReferenceDataManager

    from resource_explorer.egeria_clients import egeria_client

    clients = egeria_client(identity, purpose=purpose, view_server=egeria_view_server(),
                            platform_url=egeria_platform_url())
    return clients.of(DataDesigner), clients.of(ReferenceDataManager), clients.of(MetadataExpert)


def _still_bootstrap_leader() -> bool | None:
    from resource_explorer.leader_election import LOCK_BOOTSTRAP, still_leader

    return still_leader(LOCK_BOOTSTRAP)


def check_and_heal_data_classes(registry=None) -> SeedOutcome:
    """One monitor pass, as the daemon (`DaemonReason.BOOTSTRAP`). Logs and records an activity row
    only when `SeedOutcome.change_key` changes, so a steady "present", or the same refusal pass
    after pass, is said once per process. Never raises."""
    global _last_key, _last_outcome
    from resource_explorer.egeria_clients import Daemon, DaemonReason

    if not egeria_platform_url():
        outcome = SeedOutcome(COULD_NOT_CHECK, reason="Egeria not configured")
    else:
        try:
            designer, ref_manager, expert = _clients(Daemon(DaemonReason.BOOTSTRAP), "bootstrap data classes")
        except Exception as exc:  # noqa: BLE001
            outcome = SeedOutcome(COULD_NOT_CHECK, reason=_sentence(exc))
        else:
            outcome = ensure_data_classes(designer, ref_manager, expert,
                                          still_leader=_still_bootstrap_leader)
    key = outcome.change_key()
    with _state_lock:
        changed = key != _last_key
        _last_key, _last_outcome = key, outcome
    if changed:
        _log_line(outcome)
        record_outcome(outcome, registry)
    return outcome


def status() -> dict:
    with _state_lock:
        return _last_outcome.as_dict() if _last_outcome else {"status": "not checked yet", "line": ""}


# ── by hand ───────────────────────────────────────────────────────────────────

def bootstrap_data_classes(registry=None) -> int:
    """The manual run: the same `ensure_data_classes` the monitor calls, as RE's daemon."""
    if not egeria_platform_url():
        log.error("EGERIA_PLATFORM_URL environment variable is not set. Cannot connect to Egeria.")
        return 1
    try:
        from resource_explorer.egeria_clients import Daemon, DaemonReason

        log.info("Connecting to Egeria View Server '%s' at %s...", egeria_view_server(), egeria_platform_url())
        designer, ref_manager, expert = _clients(Daemon(DaemonReason.BOOTSTRAP), "bootstrap data classes")
    except Exception as e:
        log.error(f"Failed to connect to Egeria: {e}")
        return 1
    outcome = ensure_data_classes(designer, ref_manager, expert)
    _log_line(outcome)
    record_outcome(outcome, registry)
    return 0 if outcome.status in (PRESENT, CREATED) else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    sys.exit(bootstrap_data_classes())
