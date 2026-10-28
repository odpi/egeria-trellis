"""Registering a database server with Egeria, so Egeria's own surveys can run on it.

OPTIONAL BY DESIGN. RE reaches a database itself; Egeria may not be able to
(Egeria connects from its own platform). Nothing here is required for RE's own
surveys, curation or anything else, nothing nags, and a database that was never
registered shows a NEUTRAL state, not an error. The one thing here that writes
to Egeria is a person's press of "Register the server with Egeria".

What the press does (every write is followed by a read of what it wrote, and a
pointer / proof row is stored only AFTER that read):

1. **The server element.** Looked up by its exact qualifiedName
   (`PostgreSQL Server::<host:port>`, the name Egeria's template derives). If it
   exists it is ADOPTED -- never a second server for the next database on the
   same host:port. If not, it is created from Egeria's own "PostgreSQL Server"
   catalog template (`POSTGRES_SERVER_TEMPLATE`, GUID below, the template step 1
   of `PostgreSQLServer:CreateAndSurvey...` uses). Read back BY GUID, qualifiedName
   compared, then the server pointer is stored (registry `settings`, no DDL).
2. **Egeria's server survey** (`PostgreSQLSurvey::survey-postgres-server`) is
   submitted on that server through the native-survey machinery (poll, read the
   engine action, read the report). It reports the databases it found as
   annotations; it does NOT create database elements ("the databases are not
   catalogued at this time", `PostgresServerSurveyActionService`).
3. **The database element.** Looked up by qualifiedName
   (`PostgreSQL Relational Database::<host:port>::<database>`); adopted if present
   (read back by GUID, `egeria_asset_guid` stored). If absent, Egeria's own
   `PostgreSQLDatabase:CreateAndSurveyGovernanceActionProcess` is submitted with
   the template placeholders mapped from RE's stored record. The database is read
   back by qualifiedName and then by GUID before `egeria_asset_guid` is written;
   the survey engine action the process started is found from the database's own
   ActionTarget relationship and recorded under the ordinary "Survey PostgreSQL
   Database" row, so that row shows it through the existing read-back.

Credentials never travel in a request. The templates' connection names a secrets
COLLECTION and a store path; Egeria's engine host reads the user and password from
the `.omsecrets` file at survey time. RE's existing projection
(`omsecrets_reproject`, `omsecrets_store`) writes that file from the registry; this
module only checks the collection is there and asks the projection to fill it when
it is not. The server element's connection uses the same collection as the database
it was registered from (`<slug>::PostgreSQL Secret`).

Which kinds have a separate server element at all is one tested function,
`technology_type_processes.server_has_separate_element`. A kind the config does not
cover says "this kind is not wired yet".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from resource_explorer import native_survey_run as nsr
from resource_explorer.catalogue_gateway import (
    GatewayError, PyegeriaCatalogueGateway, Relationship, database_qualified_name,
    parse_element_answer, server_name_for, server_qualified_name)
from resource_explorer.surveyors.technology_type_processes import (
    KIND_CATALOG_AND_SURVEY, KIND_SURVEY_EXISTING, NativeProcess, get_native_processes,
    get_wiring, server_has_separate_element)

log = logging.getLogger(__name__)

#: Egeria's own template for the server element (`PostgreSQLTemplateType.POSTGRES_SERVER_TEMPLATE`,
#: egeria checkout, postgres-server-connectors). A constant from the source, like the schema template's.
SERVER_TEMPLATE_GUID = "542134e6-b9ce-4dce-8aef-22e8daf34fdb"
DATABASE_SURVEY_QN = "PostgreSQLSurvey::survey-postgres-database"
#: The `requestType` of the engine action the database survey runs as.
DATABASE_SURVEY_REQUEST_TYPE = "survey-postgres-database"
VERSION_NOT_RECORDED = "not recorded"
#: A completed process whose database is still unreadable is "in flight" this long, then it is a stall.
AWAITING_REGISTRATION_WINDOW = timedelta(minutes=15)

# ── states (the catalog row's own; the survey rows keep native_survey_run's) ──
NOT_REGISTERED = "not_registered"
REGISTERED = "registered"
AWAITING_REGISTRATION = "awaiting_registration"

# ── wording ──
NOT_REGISTERED_WORDS = ("Egeria has not been given this database · register it to run Egeria's "
                        "own survey · optional")
SERVER_NOT_REGISTERED_WORDS = ("Egeria has not been given this database's server · register it "
                               "first · optional")
STALE_WORDS = "the Egeria asset RE had stored no longer exists"
REACH_NOTE = "Egeria connects from its own platform; RE can still survey this database itself"
REGISTER_LABEL = "Register the server with Egeria →"
START_AGAIN_LABEL = "Start again →"
REGISTER_AGAIN_LABEL = "Register again →"
#: Egeria answered about a pointer RE holds and there is no usable element: said in Egeria's frame, never "deleted"
#: (RE did not see a delete and cannot tell deleted from purged).
GONE_WORDS = "no longer in Egeria"
NO_RECORD_WORDS = "RE has no record of the earlier process, so it cannot check whether it is still running."
SERVER_NO_ANSWER_WORDS = ("Egeria gave no answer to a server create, so the server may or may not exist; "
                          "RE will not create it again until you start again")
CREATED_UNCONFIRMED_WORDS = "created, not yet confirmed: press to confirm"
SECRETS_PATH_WORDS = "secrets path not configured: RE cannot check Egeria's credentials"
SINGLE_STEP_NOT_BUILT = ("the server and the database are one thing for this kind, and registering them "
                         "in one step is not built yet")


def not_wired_words(technology_type: str) -> str:
    return f"this kind is not wired yet ({technology_type or 'no technology type'})"


# ── what is missing from the stored record, said exactly ───────────────────

def record_missing(entity) -> list[str]:
    """What the stored record lacks for Egeria to be given the database. Names the field."""
    missing = []
    if not (getattr(entity, "host", "") or "").strip():
        missing.append("the host")
    port = getattr(entity, "port", 0)
    if not isinstance(port, int) or isinstance(port, bool) or port <= 0:
        missing.append("the port")
    if not (getattr(entity, "database_name", "") or "").strip():
        missing.append("the database name")
    if not (getattr(entity, "db_user", "") or "").strip():
        missing.append("the database user")
    return missing


def unusable_record_reason(entity) -> str:
    missing = record_missing(entity)
    if not missing:
        return ""
    return "RE's stored record for this database has no " + ", no ".join(m.replace("the ", "") for m in missing) \
        + ", which Egeria's template needs"


def wiring_reason(entity_type: str, technology_type: str, entity) -> str:
    """Why RE cannot register this resource with Egeria, '' when it can. Registry-only: no Egeria call."""
    if entity is None:
        return "This resource is not registered."
    sep = server_has_separate_element(entity_type, technology_type)
    if sep is None:
        return not_wired_words(technology_type)
    if sep is False:
        return SINGLE_STEP_NOT_BUILT
    return unusable_record_reason(entity)


# ── names and the placeholders ──────────────────────────────────────────────

def secrets_collection_for(entity) -> str:
    from resource_explorer import omsecrets_store
    return omsecrets_store.secrets_collection_name(entity.slug)


def _store_path_name() -> str:
    from resource_explorer.config import get_config
    return get_config().egeria.secrets_store_path_name


def server_placeholders(entity) -> dict[str, str]:
    """The placeholders Egeria's server template takes (`getPostgresServerPlaceholderPropertyTypes`):
    hostIdentifier, portNumber, serverName, description, versionIdentifier, resourceName,
    secretsStorePathName, secretsCollectionName. No password: the connection reads the collection."""
    name = server_name_for(entity)
    return {
        "hostIdentifier": getattr(entity, "egeria_host", "") or entity.host,
        "portNumber": str(entity.port),
        "serverName": name,
        "description": f"PostgreSQL server at {name}",
        "versionIdentifier": VERSION_NOT_RECORDED,
        "resourceName": name,
        "secretsStorePathName": _store_path_name(),
        "secretsCollectionName": secrets_collection_for(entity),
    }


def database_placeholders(entity) -> dict[str, str]:
    """The placeholders Egeria's database template / the CreateAndSurvey process take
    (`getPostgresDatabasePlaceholderPropertyTypes`): hostIdentifier, portNumber, serverName,
    versionIdentifier, databaseName, databaseDescription, secretsStorePathName, secretsCollectionName."""
    return {
        "hostIdentifier": getattr(entity, "egeria_host", "") or entity.host,
        "portNumber": str(entity.port),
        "serverName": server_name_for(entity),
        "versionIdentifier": VERSION_NOT_RECORDED,
        "databaseName": entity.database_name,
        "databaseDescription": entity.description or f"PostgreSQL database {entity.database_name}",
        "secretsStorePathName": _store_path_name(),
        "secretsCollectionName": secrets_collection_for(entity),
    }


def server_pointer_key(server_name: str) -> str:
    return "egeria_server_guid::" + server_qualified_name(server_name)


def server_unconfirmed_key(server_name: str) -> str:
    """A server the template create returned a GUID for but whose read-back has not succeeded yet.
    The next press reads THAT GUID before it would create anything."""
    return "egeria_server_unconfirmed::" + server_qualified_name(server_name)


def server_cred_key(server_name: str) -> str:
    """Whose secrets collection (a database slug) the server element's connection was created with."""
    return "egeria_server_cred_slug::" + server_qualified_name(server_name)


def claim_key(slug: str) -> str:
    return "egeria_register_claim::" + slug


def server_claim_key(server_name: str) -> str:
    return "egeria_server_claim::" + server_qualified_name(server_name)


def _take(registry, key: str, value: str | None = None) -> bool:
    """Atomic insert-if-absent (the existing `app_settings` key/value table, no DDL). True only for the
    one caller whose insert took effect."""
    with registry._conn() as conn:
        cur = conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO NOTHING", (key, value if value is not None else _now(), _now()))
        return cur.rowcount == 1


def _release(registry, key: str) -> None:
    with registry._conn() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))


def take_claim(registry, slug: str) -> bool:
    """The per-database registration claim, taken before the database process is submitted."""
    return _take(registry, claim_key(slug))


def release_claim(registry, slug: str) -> None:
    _release(registry, claim_key(slug))


def take_server_claim(registry, server_name: str, holder_slug: str = "") -> bool:
    """The per-server claim, taken around the template create: two presses (one database twice, or two
    databases on one server) cannot both see the server absent and both create it. The value records
    WHO holds it (the database slug), so a "Start again" on another database cannot clear a live claim."""
    return _take(registry, server_claim_key(server_name), f"{holder_slug}@{_now()}")


def server_claim_holder(registry, server_name: str) -> str | None:
    """None = no claim; '' = a claim with no recorded holder; else the holder's database slug."""
    raw = registry.get_setting(server_claim_key(server_name), None)
    if raw is None or raw == "":
        return None if raw is None else ""
    return raw.split("@", 1)[0] if "@" in raw else ""


def release_server_claim(registry, server_name: str) -> None:
    _release(registry, server_claim_key(server_name))


def claim_held(registry, slug: str) -> str:
    return (registry.get_setting(claim_key(slug), "") or "").strip()


def server_pointer(registry, entity) -> str:
    """The server element's GUID RE stored after reading it back ('' when it never has)."""
    return (registry.get_setting(server_pointer_key(server_name_for(entity)), "") or "").strip()


def _process_for(entity_type: str, technology_type: str, kind: str) -> NativeProcess | None:
    return next((p for p in get_native_processes(entity_type, technology_type) if p.kind == kind), None)


def server_survey_process(entity_type: str, technology_type: str) -> NativeProcess | None:
    st = get_wiring(entity_type, technology_type).server_technology_type
    if not st:
        return None
    return next((p for p in get_native_processes(entity_type, st)
                 if p.kind == KIND_SURVEY_EXISTING and p.target == "server"), None)


# ── the port ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ElementBack:
    guid: str
    qualified_name: str


class RegistrationPort(nsr.EgeriaSurveyPort, Protocol):
    """The Egeria surface the registration adds to the survey port. Reads raise on a
    transport failure; `None` means Egeria answered that there is no such element."""

    def find_element(self, qualified_name: str) -> ElementBack | None: ...
    def read_element(self, guid: str) -> ElementBack | None: ...
    def create_server(self, placeholders: dict[str, str]) -> str: ...
    def initiate_process(self, process_qualified_name: str, request_parameters: dict[str, str]) -> str: ...
    def read_process(self, process_instance_guid: str) -> nsr.ActionRead: ...
    def survey_actions_for(self, database_guid: str) -> list[str]: ...


class PyegeriaRegistrationPort(nsr.PyegeriaSurveyPort):
    """The real port. Reads of elements and relationships go through the catalogue
    gateway's parsers (live-verified shapes); the two writes are `create_server`
    (Egeria's template) and `initiate_process`."""

    def __init__(self, identity=None) -> None:
        super().__init__(identity)
        self._gateway = None

    def _gw(self) -> PyegeriaCatalogueGateway:
        if self._gateway is None:
            self._gateway = PyegeriaCatalogueGateway(identity=self.principal())
        return self._gateway

    @staticmethod
    def _element_back(res: Any) -> ElementBack | None:
        """An element answer -> `ElementBack`, `None` ONLY for the exact "No element found" answer.
        A string that merely contains "not found" is Egeria saying something else (user, type, index)
        and is refused with that sentence: absent must never be inferred loosely."""
        from resource_explorer.catalogue_gateway import _guid_of, _qn_of
        if nsr.is_exact_absent(res):
            return None
        if isinstance(res, dict) and _guid_of(res):
            return ElementBack(_guid_of(res), _qn_of(res))
        raise nsr.NativeSurveyError(
            "Egeria answered with something other than an element: " + " ".join(str(res).split())[:300])

    def find_element(self, qualified_name: str) -> ElementBack | None:
        self._enter()
        body = {"class": "UniqueNameRequestBody", "name": qualified_name,
                "namePropertyName": "qualifiedName", "forLineage": False, "forDuplicateProcessing": False}
        try:
            res = self._get_expert().get_metadata_element_by_unique_name(
                name=qualified_name, property_name="qualifiedName", body=body)
        except Exception as exc:  # noqa: BLE001 -- only a TYPED not-found is absence
            if self._is_not_found(exc):
                return None
            raise
        return self._element_back(res)

    def read_element(self, guid: str) -> ElementBack | None:
        self._enter()
        try:
            res = self._get_expert().get_metadata_element_by_guid(guid)
        except Exception as exc:  # noqa: BLE001 -- only a TYPED not-found is absence
            if self._is_not_found(exc):
                return None
            raise
        return self._element_back(res)

    def create_server(self, placeholders: dict[str, str]) -> str:
        self._enter()
        body = {
            "class": "TemplateRequestBody",
            "templateGUID": SERVER_TEMPLATE_GUID,
            "isOwnAnchor": True,
            "deepCopy": True,
            "placeholderPropertyValues": dict(placeholders),
        }
        guid = self._get_curation().create_elem_from_template(body)
        if not isinstance(guid, str) or not guid.strip():
            raise nsr.NativeSurveyError(f"Egeria's template create answered {guid!r}, not a GUID")
        return guid.strip()

    def initiate_process(self, process_qualified_name: str, request_parameters: dict[str, str]) -> str:
        self._enter()
        guid = self._get_curation().initiate_gov_action_process(
            action_type_qualified_name=process_qualified_name,
            request_parameters=dict(request_parameters))
        if not guid or guid == "Action not initiated":
            raise nsr.NativeSurveyError(
                f"Egeria did not initiate {process_qualified_name!r} (it answered {guid!r})")
        return guid

    def read_process(self, process_instance_guid: str) -> nsr.ActionRead:
        """The process instance's own activityStatus when it carries one; otherwise its FIRST engine
        action's (found by the ActionRequester relationship, as Egeria's own FVT finds it). UNVERIFIED
        LIVE which of the two a build populates; neither is guessed -- no status is an error."""
        self._enter()
        try:
            return self.read_action(process_instance_guid)
        except nsr.NativeSurveyError:
            pass
        result = self._get_expert().get_related_metadata_elements(
            process_instance_guid, "ActionRequester",
            body={"class": "GetRequestBody"}, starting_at_end=1)
        guids = [(e.get("element") or {}).get("elementGUID")
                 for e in (result.get("elementList") or [])] if isinstance(result, dict) else []
        guids = [g for g in guids if g]
        if not guids:
            raise nsr.NativeSurveyError(
                "Egeria's process instance has no status and no engine action is linked to it yet")
        return self.read_action(guids[0])

    def survey_actions_for(self, database_guid: str) -> list[str]:
        self._enter()
        rels = self._gw().relationships(database_guid)
        return _survey_action_guids(rels)


def _survey_action_guids(rels: list[Relationship]) -> list[str]:
    return [r.other_guid for r in rels
            if r.type_name == "ActionTarget" and r.action_kind == DATABASE_SURVEY_REQUEST_TYPE and r.other_guid]


# ── the one place the catalog row's words come from ─────────────────────────

def _now() -> str:
    return nsr._now()


def derive_catalog_state(run: dict | None, *, has_pointer: bool) -> dict:
    """The "Catalog and Survey" row's state, from persisted proof only.

    The base derivation is `native_survey_run.derive_native_state` (submitted, running, Egeria's
    own failure words, ...). A process Egeria reports COMPLETED is REGISTERED only when the
    database pointer was stored (after a read of that GUID); until then it is awaiting registration.
    With no run at all, a stored pointer is `registered` and no pointer is the NEUTRAL `not_registered`."""
    base = nsr.derive_native_state(run, stored_annotation_count=None)
    st = base["state"]
    if st == nsr.NOT_RUN:
        base["state"] = REGISTERED if has_pointer else NOT_REGISTERED
    elif st in (nsr.AWAITING_REPORT, nsr.REPORT_INCOMPLETE, nsr.COMPLETE):
        base["state"] = REGISTERED if has_pointer else AWAITING_REGISTRATION
    return base


def _within_window(run: dict | None) -> bool:
    raw = (run or {}).get("surveyed_at") or ""
    try:
        started = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started < AWAITING_REGISTRATION_WINDOW


def catalog_in_flight(state: dict, run: dict | None) -> bool:
    if state["state"] in (nsr.SUBMITTED, nsr.RUNNING, nsr.UNREADABLE):
        return True
    return state["state"] == AWAITING_REGISTRATION and _within_window(run)


# ── connection-ish failures get the one extra line ──────────────────────────

_CONNECTION_WORDS = re.compile(
    r"(?:could not|unable to) connect|connection[^;]{0,80}refused|refused to connect|timed? ?out|unreachable|no route|connection reset|unknown ?host",
    re.IGNORECASE)
#: A sentence that names a credential or role problem is not a reach problem, whatever else it says.
_AUTH_WORDS = re.compile(r"password|authenticat|\brole\b|pg_hba|not authorized|permission denied", re.IGNORECASE)


def reach_note_for(run_state: dict) -> str:
    """Egeria's own sentence is always shown verbatim. When a failed or refused run reads as a
    connection problem, one line says Egeria connects from ITS platform and RE can still survey it.
    Connection-class wording only (could not connect, refused, timed out, unreachable, no route,
    connection reset, unknown host; bare "connection" is not enough, since a JDBC role failure names one): a role or password failure is not a reach problem and gets no such line."""
    if run_state.get("state") not in (nsr.FAILED, nsr.SUBMIT_FAILED):
        return ""
    text = " ".join(str(run_state.get(k) or "") for k in ("message", "error"))
    if _AUTH_WORDS.search(text):
        return ""
    return REACH_NOTE if _CONNECTION_WORDS.search(text) else ""


#: The address Egeria's own sentence names ("Connection to localhost:5442 refused").
_ADDRESS_IN_SENTENCE = re.compile(r"\b(?:to|at)\s+(?:host\s+)?[\"'`]?((?:[A-Za-z0-9_\-]+\.)*[A-Za-z0-9_\-]+:\d{2,5})\b")
OWN_MACHINE_HOSTS = ("localhost", "127.0.0.1", "::1")


def tried_words(run_state: dict, entity) -> dict:
    """For a failed or refused survey that reads as a CONNECTION problem (the same test as `reach_note_for`): the
    address Egeria tried, from its own sentence when it names one, else the stored egeria_host:port. The
    "localhost is Egeria's own machine" clause is a fact about the address, said only for localhost/127.0.0.1.
    {} when it is not a connection failure."""
    if not reach_note_for(run_state) or entity is None:
        return {}
    text = " ".join(str(run_state.get(k) or "") for k in ("message", "error"))
    m = _ADDRESS_IN_SENTENCE.search(text)
    address = m.group(1) if m else server_name_for(entity)
    own = address.rsplit(":", 1)[0].strip("[]").lower() in OWN_MACHINE_HOSTS
    return {"address": address, "own_machine": own,
            "words": f"Egeria tried {address}" + (", and localhost is Egeria's own machine" if own else "")}


def reach_proof(registry, entity_type: str, slug: str, technology_type: str, entity) -> dict:
    """What the persisted survey runs prove about Egeria REACHING this database -- the registration alone proves
    only that the elements exist. `state`: `connected` (a survey of it completed), `failed` (the newest survey
    failed on a connection problem, with the address it tried), else `not_yet`."""
    quals = [DATABASE_SURVEY_QN]
    sp = server_survey_process(entity_type, technology_type)
    if sp is not None:
        quals.append(sp.qualified_name)
    failed: dict = {}
    for qn in quals:
        runs = registry.list_native_survey_runs(entity_type, slug, qn)
        latest = runs[0] if runs else None
        if not latest:
            continue
        stored = (registry.count_native_survey_annotations(latest["survey_report_guid"])
                  if latest.get("survey_report_guid") else None)
        st = nsr.derive_native_state(latest, stored_annotation_count=stored)
        if st["state"] in (nsr.COMPLETE, nsr.PARTIAL):      # either survey reaching the server or the database
            return {"state": "connected", "words": "Egeria has connected"}
        t = tried_words(st, entity)
        if t and not failed:
            failed = t
    if failed:
        return {"state": "failed", "words": "Egeria couldn't connect", "address": failed["address"]}
    return {"state": "not_yet", "words": "Egeria hasn't connected yet"}


# ── pointer checks (the only read that decides "stale") ─────────────────────

def check_pointers(registry, port, entity_type: str, slug: str) -> dict:
    """Read the stored pointers back from Egeria. 'resolves' | 'gone' | 'none' (never stored) |
    'unreadable' (the read failed -- NOT the same as gone). A pointer is called gone only when
    Egeria answered that no such element exists."""
    entity = nsr._resource_entity(registry, entity_type, slug)
    out = {"database": "none", "server": "none", "error": ""}
    if entity is None:
        return out
    ptr = (getattr(entity, "egeria_asset_guid", "") or "").strip()
    if ptr:
        out["database"] = _classify(port, ptr, out)
        if out["database"] == "resolves":
            _record_held_address(registry, port, entity_type, slug, ptr)
    sptr = server_pointer(registry, entity)
    if sptr:
        out["server"] = _classify(port, sptr, out)
    return out


def held_address_key(entity_type: str, slug: str) -> str:
    return f"egeria_held_address::{entity_type}::{slug}"


def address_in_name(qualified_name: str) -> str:
    """'host.docker.internal:5442' from 'PostgreSQL Relational Database::host.docker.internal:5442::adventureworks'
    ('' for a name that does not carry one). The address Egeria holds is what its own element is NAMED, not a
    guess from RE's side."""
    parts = str(qualified_name or "").split("::")
    return parts[1].strip() if len(parts) >= 3 else ""


def _record_held_address(registry, port, entity_type: str, slug: str, guid: str) -> None:
    """After a pointer READ BACK, keep the address Egeria's element carries (a persisted proof; the key/value
    table, no DDL). A failed read-back changes nothing: what was proven earlier stays."""
    try:
        back = port.read_element(guid)
    except Exception:  # noqa: BLE001 -- not proof either way
        return
    held = address_in_name(getattr(back, "qualified_name", "")) if back is not None else ""
    if held:
        registry.set_setting(held_address_key(entity_type, slug), held)


def address_state(registry, entity_type: str, slug: str, entity) -> dict:
    """{current, held, will_change}: `current` is the address RE would send now (egeria_host or host, with the
    port); `held` the address Egeria's database element was last READ BACK under ('' when never read);
    `will_change` only when both are known and differ -- Egeria still has the old one until Register again."""
    current = server_name_for(entity) if entity is not None else ""
    held = (registry.get_setting(held_address_key(entity_type, slug), "") or "").strip()
    return {"current": current, "held": held, "will_change": bool(held and current and held != current)}


def _classify(port, guid: str, out: dict) -> str:
    try:
        return "resolves" if port.asset_exists(guid) else "gone"
    except Exception as exc:  # noqa: BLE001
        out["error"] = nsr._short_error(exc)
        return "unreadable"


# ── registering ─────────────────────────────────────────────────────────────

class RegistrationRefused(nsr.NativeSurveyCannotRun):
    """RE knows why it cannot register this resource. The message is what the row shows."""


def _ensure_credentials(registry, entity) -> dict:
    """The collection the connection names must exist in the secrets file Egeria's engine host
    reads. Returns what happened, for the response and the row:
    `checked` False = no secrets path configured, so RE cannot check (said, never silent);
    `written` True = the press re-projected the secrets file (a LOCAL write) from the registry.
    Absent and unfillable refuses with the projection's own reason (it never raises: it returns
    outcomes)."""
    collection = secrets_collection_for(entity)
    state = nsr.credentials_state(entity)
    if state == nsr.NOT_CONFIGURED:
        return {"checked": False, "written": False, "collection": collection, "reason": SECRETS_PATH_WORDS}
    if state == nsr.PRESENT:
        return {"checked": True, "written": False, "collection": collection, "reason": ""}
    from resource_explorer import omsecrets_reproject as rp
    outcomes = rp.reproject(registry, [entity.slug], only_missing=True)
    mine = next((o for o in outcomes if o.slug == entity.slug), None)
    if mine is None or mine.status in (rp.ERROR, rp.SKIPPED) or nsr.credentials_state(entity) == nsr.ABSENT:
        why = mine.message if mine is not None and mine.message else "the projection wrote nothing"
        raise RegistrationRefused(f"{nsr.NO_CREDENTIALS}: {why}")
    return {"checked": True, "written": mine.status == rp.WRITTEN, "collection": collection, "reason": ""}


class RegistrationUnresolved(nsr.NativeSurveyBusy):
    """An earlier registration process was submitted and has not been resolved (no readable database,
    not failed). A second one would risk a duplicate database, so the person must say 'start again'."""


def describe_earlier(registry, entity_type: str, slug: str, process_qn: str) -> str:
    runs = registry.list_native_survey_runs(entity_type, slug, process_qn)
    claim = claim_held(registry, slug)
    r = next((x for x in runs if x.get("engine_action_guid")), None)
    if r:
        return (f"An earlier registration (process {r['engine_action_guid']}, Egeria says "
                f"{r.get('engine_action_status') or 'nothing yet'}, submitted {r.get('surveyed_at') or 'at an unrecorded time'}) "
                "has not produced a database RE can read in Egeria.")
    return (f"A registration press at {claim or 'an unrecorded time'} did not record a result. "
            + NO_RECORD_WORDS)


def _in_flight(registry, entity_type: str, slug: str, process_qn: str, *, catalog: bool) -> dict | None:
    runs = registry.list_native_survey_runs(entity_type, slug, process_qn)
    if not runs:
        return None
    latest = runs[0]
    if catalog:
        has_ptr = bool((getattr(nsr._resource_entity(registry, entity_type, slug), "egeria_asset_guid", "") or "").strip())
        st = derive_catalog_state(latest, has_pointer=has_ptr)
        return latest if catalog_in_flight(st, latest) else None
    stored = (registry.count_native_survey_annotations(latest["survey_report_guid"])
              if latest.get("survey_report_guid") else None)
    st = nsr.derive_native_state(latest, stored_annotation_count=stored)
    return latest if st["state"] in (nsr.SUBMITTED, nsr.RUNNING, nsr.AWAITING_REPORT, nsr.UNREADABLE) else None


def _read_back(port, qualified_name: str, what: str) -> ElementBack:
    """Find by exact qualifiedName and read that element back BY GUID. Returns it, or raises
    NativeSurveyError saying exactly what Egeria did not show. Never returns a guess."""
    found = port.find_element(qualified_name)
    if found is None:
        raise nsr.NativeSurveyError(f"Egeria has no {what} with the qualifiedName {qualified_name}")
    return _verify(port, found.guid, qualified_name, what)


def _verify(port, guid: str, qualified_name: str, what: str) -> ElementBack:
    back = port.read_element(guid)
    if back is None:
        raise nsr.NativeSurveyError(f"Egeria has no {what} with the GUID {guid} (read back after finding it)")
    if back.qualified_name != qualified_name:
        raise nsr.NativeSurveyError(
            f"the {what} Egeria holds under {guid} is named {back.qualified_name!r}, not {qualified_name!r}; "
            "nothing was stored")
    return back


def register_with_egeria(registry, port: RegistrationPort, entity_type: str, slug: str, *,
                         technology_type: str, submitted_by: str = "", start_again: bool = False) -> dict:
    """The press. Returns {"server": {...}, "database": {...}, "server_survey_error": str, "projected": {...}}.

    Raises `RegistrationRefused` (a reason RE knew before asking Egeria), `NativeSurveyBusy` /
    `RegistrationUnresolved`, or `NativeSurveyError` (Egeria's own sentence). Whatever was proven
    before a failure stays proven: a server read back and pointed at is not undone by a later step.

    Nothing is created on an inference: an element is absent only when Egeria said so in a typed or
    exact way (see `PyegeriaRegistrationPort`); a server the template create returned a GUID for but
    that did not read back is recorded and read FIRST on the next press; a database process is not
    submitted twice unless the person says `start_again`."""
    from resource_explorer import secret_redaction

    entity = nsr._resource_entity(registry, entity_type, slug)
    reason = wiring_reason(entity_type, technology_type, entity)
    if reason:
        raise RegistrationRefused(reason)
    process = _process_for(entity_type, technology_type, KIND_CATALOG_AND_SURVEY)
    if process is None:
        raise RegistrationRefused(not_wired_words(technology_type))
    # Order matters: the in-flight guard FIRST, and the claim is released only after it has passed and
    # Egeria has been asked whether the earlier process is still running. A refused press leaves the
    # stall protection exactly as it was.
    if _in_flight(registry, entity_type, slug, process.qualified_name, catalog=True):
        raise nsr.NativeSurveyBusy("Registering this database with Egeria is already in flight.")
    if start_again:
        _refuse_if_earlier_still_running(registry, port, entity, entity_type, slug, process.qualified_name)
        release_claim(registry, slug)
        _release_server_claim_for_start_again(registry, entity, slug)
        _log_write(f"start again requested for {slug}: this database's registration claim was released")

    with secret_redaction.redacting_logs(getattr(entity, "db_password", "") or ""):
        projected = _ensure_credentials(registry, entity)
        try:
            return _register_steps(registry, port, entity_type, slug, technology_type, submitted_by,
                                   entity, process, projected)
        except nsr.NativeSurveyError as exc:
            if projected.get("written"):
                # A later refusal must not hide that this press already wrote the local secrets file.
                exc.args = (f"{exc} · RE re-projected the secrets file (a local write) before this",)
            raise


def _release_server_claim_for_start_again(registry, entity, slug: str) -> None:
    """Clear the server claim only when it is this database's own, or has no recorded holder and no OTHER
    database on that server is in flight. A live claim held by another database is left alone (the press
    then refuses, naming it)."""
    sname = server_name_for(entity)
    holder = server_claim_holder(registry, sname)
    if holder is None:
        return
    if holder == slug:
        release_server_claim(registry, sname)
        return
    if holder == "":
        host = getattr(entity, "egeria_host", "") or entity.host
        for other in registry.list_databases():
            if other.slug == slug or (other.egeria_host or other.host) != host or other.port != entity.port:
                continue
            proc = _process_for("database", "PostgreSQL Relational Database", KIND_CATALOG_AND_SURVEY)
            if proc and _in_flight(registry, "database", other.slug, proc.qualified_name, catalog=True):
                return
        release_server_claim(registry, sname)


def _newest_run_with_guid(registry, entity_type: str, slug: str, process_qn: str) -> dict | None:
    return next((r for r in registry.list_native_survey_runs(entity_type, slug, process_qn)
                 if r.get("engine_action_guid")), None)


def _refuse_if_earlier_still_running(registry, port, entity, entity_type, slug, process_qn) -> None:
    run = _newest_run_with_guid(registry, entity_type, slug, process_qn)    # not just the newest row
    guid = (run or {}).get("engine_action_guid") or ""
    if not guid:
        return                  # no process to ask about; the confirm said RE could not check
    try:
        action = port.read_process(guid)
    except Exception as exc:  # noqa: BLE001
        raise nsr.NativeSurveyBusy(
            f"RE cannot tell whether the earlier registration (process {guid}) is still running: "
            f"{_egeria_sentence(exc, entity)}") from exc
    if action.status in nsr.ACTIVE_STATUSES:
        raise nsr.NativeSurveyBusy(
            f"The earlier registration (process {guid}) is still running: Egeria says {action.status}.")


def _register_steps(registry, port, entity_type, slug, technology_type, submitted_by, entity, process,
                    projected) -> dict:
    if True:
        out: dict[str, Any] = {"server": {}, "database": {}, "server_survey_error": "", "projected": projected}

        # 1. the server: adopt by qualifiedName; else a server created earlier and not yet confirmed;
        #    else create from Egeria's template. Read back by GUID before any pointer is stored.
        server_name = server_name_for(entity)
        sqn = server_qualified_name(server_name)
        unconfirmed_key = server_unconfirmed_key(server_name)
        try:
            existing = port.find_element(sqn)
            if existing is not None:
                server_guid, how = existing.guid, "adopted"
            else:
                pending = (registry.get_setting(unconfirmed_key, "") or "").strip()
                if pending and port.read_element(pending) is not None:
                    server_guid, how = pending, "adopted"        # verified by name below; never created again
                else:
                    if pending:
                        registry.set_setting(unconfirmed_key, "")  # Egeria said it is gone: nothing to adopt
                        if server_claim_holder(registry, server_name) == slug:    # only OUR claim
                            release_server_claim(registry, server_name)
                    if not take_server_claim(registry, server_name, slug):
                        holder = server_claim_holder(registry, server_name)
                        raise RegistrationUnresolved(
                            f"Another press{f' (database {holder})' if holder else ''} is creating this server "
                            f"({sqn}), or an earlier one did not confirm it. Creating it again could make a "
                            f"second server. "
                            + (f"Press {START_AGAIN_LABEL!r} on {holder}, which holds it, only if you mean to."
                               if holder and holder != slug else
                               f"Press {START_AGAIN_LABEL!r} only if you mean to."))
                    # We hold the claim. Another press may have registered the server between our "absent"
                    # and our claim: look again, and read the unconfirmed record again, before creating.
                    again = port.find_element(sqn)
                    pending2 = (registry.get_setting(unconfirmed_key, "") or "").strip()
                    if again is not None:
                        server_guid, how = again.guid, "adopted"
                    elif pending2 and port.read_element(pending2) is not None:
                        server_guid, how = pending2, "adopted"
                    else:
                        try:
                            server_guid, how = port.create_server(server_placeholders(entity)), "created"
                        except BaseException as exc:
                            if _egeria_answered(exc):
                                release_server_claim(registry, server_name)   # Egeria refused: nothing created
                                raise
                            # No answer (timeout, transport, cancellation): Egeria may have created it. KEEP
                            # the claim; the row is unresolved and offers "Start again".
                            if not isinstance(exc, Exception):
                                raise
                            raise RegistrationUnresolved(
                                f"Egeria gave no answer to the server create ({_egeria_sentence(exc, entity)}), so "
                                "the server may or may not exist. RE will not create it again until you press "
                                f"{START_AGAIN_LABEL!r}.") from exc
                    if how == "created":
                        registry.set_setting(unconfirmed_key, server_guid)   # BEFORE the read-back
                        registry.set_setting(server_cred_key(server_name), slug)
            back = _verify(port, server_guid, sqn, "server")
        except nsr.NativeSurveyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise nsr.NativeSurveyError(_egeria_sentence(exc, entity)) from exc
        registry.set_setting(server_pointer_key(server_name), back.guid)   # AFTER the read
        registry.set_setting(unconfirmed_key, "")
        release_server_claim(registry, server_name)           # confirmed by the read-back
        out["server"] = {"guid": back.guid, "qualified_name": back.qualified_name, "how": how}
        _log_write(f"registered server {sqn}: {how}, GUID {back.guid} (read back)")

        # 2. Egeria's own server survey, through the same machinery as every native survey
        sproc = server_survey_process(entity_type, technology_type)
        if sproc is not None and not _in_flight(registry, entity_type, slug, sproc.qualified_name, catalog=False):
            try:
                nsr.submit_native_survey(registry, port, entity_type, slug, sproc.qualified_name,
                                         technology_type=technology_type, submitted_by=submitted_by)
            except nsr.NativeSurveyError as exc:
                # The failure is on the row (submit_error) when Egeria refused; the registration stands.
                out["server_survey_error"] = nsr.scrub_secret(str(exc), entity)

        # 3. the database: adopt by qualifiedName, else Egeria's own process creates it
        dqn = database_qualified_name(server_name, entity.database_name)
        try:
            found = port.find_element(dqn)
        except Exception as exc:  # noqa: BLE001
            raise nsr.NativeSurveyError(_egeria_sentence(exc, entity)) from exc
        if found is not None:
            try:
                dback = _verify(port, found.guid, dqn, "database")
            except nsr.NativeSurveyError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise nsr.NativeSurveyError(_egeria_sentence(exc, entity)) from exc
            registry.set_database_egeria_guid(slug, dback.guid)           # AFTER the read
            registry.set_setting(held_address_key(entity_type, slug), server_name)
            release_claim(registry, slug)
            out["database"] = {"guid": dback.guid, "how": "adopted"}
            _record_survey_action(registry, port, entity_type, slug, dback.guid, submitted_by)
            return out

        # The atomic claim BEFORE the submit: one caller wins; every other press (a double click, a second
        # browser, a press after a window passed) is refused until the earlier run is resolved or the
        # person says "start again".
        if not take_claim(registry, slug):
            raise RegistrationUnresolved(
                describe_earlier(registry, entity_type, slug, process.qualified_name)
                + f" Submitting again could create a second database. Press {START_AGAIN_LABEL!r} only "
                "if you mean to.")
        submitted_at = _now()
        try:
            process_guid = port.initiate_process(process.qualified_name, database_placeholders(entity))
        except Exception as exc:  # noqa: BLE001
            error = _egeria_sentence(exc, entity)
            if _egeria_answered(exc):
                registry.record_native_survey_submission(
                    entity_type, slug, process.qualified_name, submitted_at,
                    submit_error=error, submitted_by=submitted_by)
                release_claim(registry, slug)             # Egeria refused it before writing: nothing started
                raise nsr.NativeSurveyError(error) from exc
            # No answer, a 5xx, a missing code or a garbled body: Egeria may have accepted the process and
            # be creating the database. KEEP the claim; a re-press must not start a second process.
            registry.record_native_survey_submission(
                entity_type, slug, process.qualified_name, submitted_at,
                submit_error=f"created or not yet known: {error}", submitted_by=submitted_by)
            raise RegistrationUnresolved(
                f"Egeria did not clearly answer the database process ({error}), so the database may or may "
                f"not be created. RE will not submit it again until you press {START_AGAIN_LABEL!r}.") from exc
        registry.record_native_survey_submission(
            entity_type, slug, process.qualified_name, submitted_at,
            engine_action_guid=process_guid, submitted_by=submitted_by, started_as="process")
        _log_write(f"submitted {process.qualified_name} for {slug}: process instance {process_guid}")
        out["database"] = {"how": "submitted", "process_instance_guid": process_guid}
        run = registry.list_native_survey_runs(entity_type, slug, process.qualified_name)[0]
        refresh_registration_run(registry, port, run, entity_type=entity_type, slug=slug)
        return out


def _http_code(exc: BaseException) -> int | None:
    for attr in ("related_http_code", "response_code"):
        v = getattr(exc, attr, None)
        try:
            if v not in (None, "") and not isinstance(v, bool):
                return int(v)
        except (TypeError, ValueError):
            continue
    return None


def _egeria_answered(exc: BaseException) -> bool:
    """Did EGERIA refuse the create before doing anything? Only then is it safe to release the claim.

    A template create is not transactional, so a 5xx (and a missing code) may come after part of the copy was
    written: those are AMBIGUOUS. Released only for an API/authorization exception whose code is 4xx, or for an
    invalid-parameter exception raised BEFORE a request was processed (it has no response and did not wrap a
    decode error; pyegeria also raises it after a 200 whose body would not parse)."""
    import json as _json

    from pyegeria.core._exceptions import PyegeriaAPIException, PyegeriaInvalidParameterException
    if isinstance(exc, PyegeriaAPIException):
        code = _http_code(exc)
        return code is not None and 400 <= code < 500
    if isinstance(exc, PyegeriaInvalidParameterException):
        inner = getattr(exc, "e", None) or exc.__cause__
        return getattr(exc, "response", None) is None and not isinstance(inner, _json.JSONDecodeError)
    return False


def _egeria_sentence(exc: Exception, entity=None) -> str:
    """Egeria's own sentence, whole (not cut at 300 characters), with the resource's password masked
    should a driver error have echoed it."""
    from resource_explorer.catalogue_gateway import _short
    try:
        text = _short(exc)
    except Exception:  # noqa: BLE001 -- an exception whose own str() fails
        text = type(exc).__name__
    return nsr.scrub_secret(text, entity)


def _log_write(message: str) -> None:
    log.info("egeria write: %s", message)


def _record_survey_action(registry, port, entity_type: str, slug: str, database_guid: str,
                          submitted_by: str) -> None:
    """The survey the process started on the database, recorded under the ordinary database-survey
    row so it shows through the existing read-back. Exactly one candidate or nothing is recorded:
    choosing among several would be a guess."""
    try:
        guids = port.survey_actions_for(database_guid)
    except Exception as exc:  # noqa: BLE001 -- the registration stands; the survey row says not run
        log.warning("could not find the survey action on %s: %s", database_guid, exc)
        return
    if len(set(guids)) != 1:
        return
    guid = guids[0]
    known = {r.get("engine_action_guid") for r in registry.list_native_survey_runs(
        entity_type, slug, DATABASE_SURVEY_QN)}
    if guid in known:
        return
    registry.record_native_survey_submission(
        entity_type, slug, DATABASE_SURVEY_QN, _now(), engine_action_guid=guid,
        submitted_by=submitted_by, started_as="process")


def refresh_registration_run(registry, port: RegistrationPort, run: dict, *, entity_type: str,
                             slug: str) -> dict:
    """Read the registration process back (read-only) and, once Egeria shows the database element,
    read it back by GUID and store the pointer. Never raises: a failed read is recorded as one."""
    guid = run["engine_action_guid"]
    entity = nsr._resource_entity(registry, entity_type, slug)
    try:
        action = port.read_process(guid)
    except Exception as exc:  # noqa: BLE001
        registry.record_native_survey_readback(
            guid, read_at=_now(), error=nsr.scrub_secret(nsr._short_error(exc), entity))
        return _refetch_catalog(registry, entity_type, slug, run)
    registry.record_native_survey_readback(guid, read_at=_now(), status=action.status,
                                           message=nsr.scrub_secret(action.message, entity))
    if action.status not in nsr.SUCCESS_STATUSES | nsr.ACTIVE_STATUSES:
        release_claim(registry, slug)         # Egeria's own terminal failure word: the run is resolved
    if entity is not None and action.status in nsr.SUCCESS_STATUSES | nsr.ACTIVE_STATUSES:
        dqn = database_qualified_name(server_name_for(entity), entity.database_name)
        try:
            found = port.find_element(dqn)
            if found is not None:
                back = _verify(port, found.guid, dqn, "database")
                registry.set_database_egeria_guid(slug, back.guid)         # AFTER the read
                release_claim(registry, slug)
                _record_survey_action(registry, port, entity_type, slug, back.guid,
                                      run.get("submitted_by") or "")
        except Exception as exc:  # noqa: BLE001
            registry.record_native_survey_readback(
                guid, read_at=_now(), error="database read failed: " + nsr.scrub_secret(nsr._short_error(exc), entity))
    return _refetch_catalog(registry, entity_type, slug, run)


def _refetch_catalog(registry, entity_type: str, slug: str, run: dict) -> dict:
    process_qn = run.get("executor_ref") or ""
    runs = registry.list_native_survey_runs(entity_type, slug, process_qn)
    latest = runs[0] if runs else None
    entity = nsr._resource_entity(registry, entity_type, slug)
    return derive_catalog_state(latest, has_pointer=bool(
        (getattr(entity, "egeria_asset_guid", "") or "").strip()))


# ── row notes and the register control, from persisted facts ─────────────

def server_credential_notes(registry, entity) -> list[str]:
    """Whose credentials the (shared) server element's connection uses -- from the recorded creator
    only, comparing collection names and the registry's db_user NAMES; no secret is read."""
    sname = server_name_for(entity)
    if not server_pointer(registry, entity):
        return []
    first = (registry.get_setting(server_cred_key(sname), "") or "").strip()
    if not first:
        return ["the server's connection credentials were not recorded by RE (it was registered outside RE "
                "or before this was recorded)"]
    if first == entity.slug:
        return []
    from resource_explorer import omsecrets_store
    note = (f"the server's connection uses {first}'s credentials "
            f"(collection {omsecrets_store.secrets_collection_name(first)}), not this database's")
    other = registry.get_database(first, allow_unreadable=True)
    if other is None:
        return [note + f"; {first} is no longer registered in RE"]
    if (other.db_user or "") != (entity.db_user or ""):
        note += (f". Their user names differ ({other.db_user or 'none'} there, "
                 f"{entity.db_user or 'none'} here), so the server survey may fail for this one")
    return [note]


def catalog_notes(registry, entity_type: str, entity, latest: dict | None, derived: dict, in_flight: bool) -> list[str]:
    notes: list[str] = []
    sname = server_name_for(entity)
    if (registry.get_setting(server_unconfirmed_key(sname), "") or "").strip():
        notes.append(CREATED_UNCONFIRMED_WORDS)
    if nsr.credentials_state(entity) == nsr.NOT_CONFIGURED:
        notes.append(SECRETS_PATH_WORDS)
    if not in_flight and (claim_held(registry, entity.slug)):
        proc = _process_for(entity_type, "PostgreSQL Relational Database", KIND_CATALOG_AND_SURVEY)
        notes.append(describe_earlier(registry, entity_type, entity.slug, proc.qualified_name if proc else ""))
    holder = server_claim_holder(registry, sname)
    unconfirmed = bool((registry.get_setting(server_unconfirmed_key(sname), "") or "").strip())
    if not in_flight and holder in ("", entity.slug) and not unconfirmed:
        notes.append(SERVER_NO_ANSWER_WORDS)
    elif holder and holder != entity.slug:
        notes.append(f"Database {holder} holds the claim on this server; press {START_AGAIN_LABEL} on {holder}, "
                     "not here")
    notes += server_credential_notes(registry, entity)
    return notes


def stamp(iso: str) -> str:
    """'10-16 13:02' from a stored ISO time, or '' when there is none to read."""
    try:
        return datetime.fromisoformat(str(iso or "").replace("Z", "+00:00")).strftime("%m-%d %H:%M")
    except ValueError:
        return ""


def gone_line(registry, entity_type: str, slug: str, entity, technology_type: str = "") -> str:
    """The row's words for a pointer Egeria answered "no longer there" to: "no longer in Egeria · was registered
    10-15 as 4f2a9c01…". The old GUID is the stored pointer; the date is the newest registration run's own stored
    time, left out when none is stored (a date RE did not record is not guessed)."""
    guid = (getattr(entity, "egeria_asset_guid", "") or "").strip() if entity is not None else ""
    proc = _process_for(entity_type, technology_type or "PostgreSQL Relational Database", KIND_CATALOG_AND_SURVEY)
    run = next(iter(registry.list_native_survey_runs(entity_type, slug, proc.qualified_name)), None) if proc else None
    when = stamp((run or {}).get("surveyed_at"))
    was = " ".join(x for x in (f"was registered {when}" if when else "was registered", f"as {guid[:8]}…" if guid else "") if x)
    return f"{GONE_WORDS} · {was}"


def register_control(registry, entity, derived: dict, in_flight: bool, wiring: str, stale: bool = False,
                     will_change: bool = False) -> dict:
    """The one control: shown for not registered, a failed or refused run, an unconfirmed server, and an
    unresolved earlier run (as "Start again", which says what the earlier run is in the row's notes)."""
    first = REGISTER_AGAIN_LABEL if stale else REGISTER_LABEL     # a pointer Egeria lost: one press, nothing to duplicate
    base = {"label": first, "why_not": wiring, "start_again": False, "available": False, "confirm": ""}
    if wiring or in_flight:
        return base
    holder = server_claim_holder(registry, server_name_for(entity))
    # A server claim held by ANOTHER database can only be cleared there: not an unresolved run of this one.
    unresolved = (derived["state"] == AWAITING_REGISTRATION or bool(claim_held(registry, entity.slug))
                  or holder in ("", entity.slug))
    unconfirmed = bool((registry.get_setting(server_unconfirmed_key(server_name_for(entity)), "") or "").strip())
    if unresolved and derived["state"] != REGISTERED:
        proc = _process_for("database", "PostgreSQL Relational Database", KIND_CATALOG_AND_SURVEY)
        run = _newest_run_with_guid(registry, "database", entity.slug, proc.qualified_name) if proc else None
        failed = derived["state"] in (nsr.FAILED, nsr.SUBMIT_FAILED)
        if failed:
            # The earlier process failed: nothing it made can be duplicated, so the claim is cleared by one press.
            return {**base, "available": True, "label": START_AGAIN_LABEL, "start_again": True, "confirm": "",
                    "one_press": True}
        submitted = stamp((run or {}).get("surveyed_at"))
        earlier = f"An earlier registration (submitted {submitted}) hasn't finished." if submitted \
            else "An earlier registration hasn't finished."
        confirm = (f"{earlier} Starting again may create a second database element in Egeria. "
                   + (f"The earlier process {run['engine_action_guid']} was last read as "
                      f"{run.get('engine_action_status') or 'not yet read'}." if run else NO_RECORD_WORDS))
        return {**base, "available": True, "label": START_AGAIN_LABEL, "start_again": True, "confirm": confirm}
    if derived["state"] in (NOT_REGISTERED, nsr.FAILED, nsr.SUBMIT_FAILED) or unconfirmed:
        return {**base, "available": True}
    if will_change and derived["state"] == REGISTERED:
        # Egeria holds this database under an older address: one press sends the current one (a new element; the
        # old ones stay, which the row says -- deleting them is the owner's job).
        return {**base, "available": True, "label": REGISTER_AGAIN_LABEL}
    return base


# ── the databases a server survey found ─────────────────────────────────────

DATABASE_ANNOTATION_TYPE = "Capture Database Measurements"
_QN_NAME = re.compile(r"^Annotation::[^:]*::(?P<name>.+)::[0-9a-fA-F-]{36}$")


def discovered_database_names(annotations: list[dict]) -> list[str]:
    """The database names a server survey's report holds, exactly as Egeria reported them: the
    `Database Name` resource property of each database-measurements annotation, else the name inside
    its qualifiedName. Nothing is inferred from other annotation types."""
    names: list[str] = []
    for a in annotations:
        if a.get("annotation_type") != DATABASE_ANNOTATION_TYPE:
            continue
        detail = a.get("detail") or {}
        props = detail.get("resource_properties") or {}
        name = props.get("Database Name") or props.get("databaseName") or ""
        if not name:
            m = _QN_NAME.match(str(detail.get("qualified_name") or ""))
            name = m.group("name") if m else ""
        if name and name not in names:
            names.append(name)
    return names


def discovered_databases(registry, entity, server_run: dict | None) -> list[dict]:
    """For the newest complete server survey of this database's server: each database Egeria
    reported, whether RE has it registered under that server, and whether Egeria has an asset for
    it. `state`: `cataloged` (RE's pointer is stored), `found` (Egeria reported it; no asset yet --
    "found, not yet cataloged"), and `re_slug` empty when RE has no such registered database."""
    report = (server_run or {}).get("survey_report_guid") or ""
    if not report:
        return []
    names = discovered_database_names(registry.query_native_survey_annotations(report))
    host = (getattr(entity, "egeria_host", "") or entity.host)
    by_name = {}
    for d in registry.list_databases():
        if (d.egeria_host or d.host) == host and d.port == entity.port:
            by_name[d.database_name] = d
    out = []
    for name in names:
        d = by_name.get(name)
        pointer = (getattr(d, "egeria_asset_guid", "") or "").strip() if d else ""
        out.append({
            "name": name,
            "re_slug": d.slug if d else "",
            "state": "cataloged" if pointer else "found",
            "control": REGISTER_LABEL if (d and not pointer) else "",
        })
    return out
