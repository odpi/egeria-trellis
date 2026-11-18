"""
egeria_context.py — Best-effort live context from Egeria for plan enrichment.

Used during LGCI plan generation to:
  - Look up Actor profiles for named individuals in role assignments
  - List governance zones (valid values for zone fields)
  - Check whether a named project / glossary already exists

No public method raises: plan generation continues when Egeria is unreachable.

Absence says why (EA Phase 3, A-P2). "Egeria not reached" is never reported as
"none" or "not found". The ``*_answer`` methods return B5's state-plus-message
shape, ``{"state", ..., "message"}``:

- ``ok`` / ``found``: the read succeeded (``message`` None, or a cap note);
- ``none`` / ``not_found``: Egeria answered and holds nothing that matches;
- ``capped``: the first N were read and Egeria may hold more;
- ``not_configured``: no Egeria platform is configured;
- ``not_signed_in``: no token or password to read Egeria with;
- ``refused``: Egeria rejected the credential (HTTP 401);
- ``unreachable``: the platform could not be contacted;
- ``read_failed``: any other failure, with its reason.

The older list/None methods remain for existing callers; they return the
``*_answer`` result's payload, so their emptiness is ambiguous by design and new
code should use the answers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from loguru import logger

_MCP_CONFIG_PATH = str(Path(__file__).parent / "configdata" / "mcp_servers.json")

# Answer states (see the module docstring).
OK, FOUND, NONE, NOT_FOUND, CAPPED = "ok", "found", "none", "not_found", "capped"
NOT_CONFIGURED, NOT_SIGNED_IN, REFUSED, UNREACHABLE, READ_FAILED = (
    "not_configured", "not_signed_in", "refused", "unreachable", "read_failed")
# States in which Egeria was not read at all: nothing may be concluded about what it holds.
NOT_READ = frozenset({NOT_CONFIGURED, NOT_SIGNED_IN, REFUSED, UNREACHABLE, READ_FAILED})

NOT_CONFIGURED_MSG = ("Egeria not reached: no Egeria platform is configured "
                      "(EGERIA_VIEW_SERVER and EGERIA_VIEW_SERVER_URL).")

# Governance zones are read a page at a time, filtered to GovernanceZone. ZONE_CAP bounds the
# read; reaching it with a full page is reported as "capped", never as the complete list.
ZONE_PAGE_SIZE = 100
ZONE_CAP = 500


def _words(exc: BaseException) -> str:
    """A pyegeria exception's own one-line reason (its text runs "=> CODE => reason * Context ...")."""
    text = " ".join(str(exc).split())
    if "=>" in text:
        text = text.split("=>")[-1]
    return text.split(" * ")[0].strip() or type(exc).__name__


def failure_answer(exc: BaseException, what: str) -> dict:
    """{state, message} for an exception raised while reading ``what`` from Egeria."""
    try:
        from pyegeria.core._exceptions import (PyegeriaConnectionException, PyegeriaTimeoutException,
                                               PyegeriaUnauthorizedException)
    except ImportError:  # pragma: no cover - pyegeria is a hard dependency
        PyegeriaConnectionException = PyegeriaTimeoutException = PyegeriaUnauthorizedException = ()  # type: ignore
    if isinstance(exc, PyegeriaUnauthorizedException) or getattr(exc, "related_http_code", None) == 401:
        return {"state": REFUSED,
                "message": f"Egeria not reached: it refused the sign-in while reading {what} (HTTP 401). "
                           "Sign in again."}
    if isinstance(exc, (PyegeriaConnectionException, PyegeriaTimeoutException, ConnectionError, TimeoutError)):
        return {"state": UNREACHABLE,
                "message": f"Egeria not reached while reading {what}: {_words(exc)}"}
    return {"state": READ_FAILED, "message": f"Could not read {what} from Egeria: {_words(exc)}"}


# Egeria property names that hold the human-readable display name, in priority order
_NAME_PROPS = ("name", "displayName", "knownName", "title")


def _display_name(props: dict) -> str:
    for key in _NAME_PROPS:
        v = props.get(key)
        if v:
            return str(v)
    return props.get("qualifiedName", "")


def _parse_element(elem: dict) -> dict:
    """Extract the fields we care about from a raw Egeria element dict."""
    header = elem.get("elementHeader", {})
    props  = elem.get("properties", {})
    return {
        "guid":           header.get("guid", ""),
        "type_name":      header.get("type", {}).get("typeName", ""),
        "qualified_name": props.get("qualifiedName", ""),
        "display_name":   _display_name(props),
    }


class EgeriaContext:
    """
    Lazy, per-request wrapper around pyegeria OMVs clients.

    Instantiate once per plan decomposition call.  Clients are created on
    first use and reused within the same instance.  Zone list is cached for
    the lifetime of the instance (one planning turn).
    """

    def __init__(
        self,
        mcp_config_path: str = _MCP_CONFIG_PATH,
        egeria_credentials: Optional[dict] = None,
    ) -> None:
        self._conn: dict | None = None
        self._conn_loaded = False
        self._conn_error: str | None = None
        self._mcp_config_path = mcp_config_path
        # The authenticated caller's {user_id, password, token}; None falls back
        # to the .env-backed service account (see
        # advisor.auth.resolve_egeria_credentials). Since 2026-09-04 a signed-in
        # request carries `token` (an Egeria bearer token) and an empty
        # `password`; the service-account fallback is the reverse. This instance
        # is created fresh per call site (not a shared singleton), so it's safe
        # to hold this on self for the instance's lifetime.
        self._egeria_credentials = egeria_credentials
        # The bearer token for this instance's requests, resolved alongside the
        # connection in _load_connection(). Empty means "authenticate the client
        # from its own configured credentials" (the service-account path).
        self._egeria_token: str = ""
        self._actor_mgr  = None
        self._project_mgr = None
        self._gov_officer = None
        self._glossary_mgr = None
        self._automated_curation_client = None
        self._valid_metadata_mgr = None
        self._zone_answer: dict | None = None
        self._tech_type_answer: dict | None = None
        # Why the last client could not be made: {state, message}, or None.
        self.unavailable: dict | None = None

    # ── Connection ─────────────────────────────────────────────────────────────

    def _load_connection(self) -> dict | None:
        """Read Egeria connection params.

        view_server/platform_url are static — same Egeria instance for everyone —
        resolved via advisor.mcp_config.get_pyegeria_platform_config() (env vars
        first, then MCP server config). user_id/user_pwd come from the
        authenticated caller (self._egeria_credentials) via
        advisor.auth.resolve_egeria_credentials, which itself falls back to the
        .env-backed service account when no per-request credentials are given.

        For a signed-in caller `user_pwd` is empty by design — the session JWT
        carries an Egeria bearer token instead (2026-09-04), stashed here on
        self._egeria_token and applied by _make_client() via
        trellis_auth.apply_token. The conn dict keeps its exact four keys
        because callers splat it into pyegeria client constructors.
        """
        from advisor.auth import resolve_egeria_credentials
        from advisor.mcp_config import get_pyegeria_platform_config
        creds = resolve_egeria_credentials(self._egeria_credentials)
        self._egeria_token = creds.get("token", "")

        conn = get_pyegeria_platform_config(self._mcp_config_path)
        view_server = conn["view_server"]
        platform_url = conn["platform_url"]
        if not (view_server and platform_url):
            return None
        return {
            "view_server":  view_server,
            "platform_url": platform_url,
            "user_id":      creds["user_id"],
            "user_pwd":     creds["password"],
        }

    def _get_connection(self) -> dict | None:
        if not self._conn_loaded:
            try:
                self._conn = self._load_connection()
            except Exception as exc:
                logger.info(f"EgeriaContext: could not resolve the Egeria connection: {exc}")
                self._conn = None
                self._conn_error = f"Egeria not reached: could not resolve its connection settings: {exc}"
            self._conn_loaded = True
            if not self._conn:
                logger.info("EgeriaContext: no Egeria connection config, running offline")
        return self._conn

    @property
    def viewer(self) -> str:
        """The user id reads run as, for messages ("that <viewer> can see")."""
        conn = self._get_connection() or {}
        return conn.get("user_id") or "this user"

    def _make_client(self, cls):
        """Instantiate and authenticate a pyegeria OMVs client. Returns None on failure."""
        conn = self._get_connection()
        if not conn:
            self.unavailable = ({"state": READ_FAILED, "message": self._conn_error} if self._conn_error
                                else {"state": NOT_CONFIGURED, "message": NOT_CONFIGURED_MSG})
            return None
        from advisor.egeria_signin import SIGN_IN_TO_READ_EGERIA, lacks_credential
        if lacks_credential(self._egeria_token, conn.get("user_pwd")):
            # A user id with no token and no password: apply_token would mint
            # with pyegeria's configured password under that id. Stay offline.
            logger.info(f"EgeriaContext: {SIGN_IN_TO_READ_EGERIA} "
                        f"(user {conn.get('user_id')!r} has no token or password)")
            self.unavailable = {"state": NOT_SIGNED_IN,
                                "message": f"Egeria not reached: not signed in. {SIGN_IN_TO_READ_EGERIA}"}
            return None
        try:
            client = cls(**conn)
            # Signed-in caller: reuse their Egeria bearer token so Egeria's own
            # provenance records the person, not the service account. Anonymous/
            # background caller: no token, so authenticate from the client's own
            # configured credentials (the .env service account) as before.
            from advisor.auth import apply_token
            apply_token(client, self._egeria_token)
            return client
        except Exception as exc:
            logger.info(f"EgeriaContext: client init failed [{cls.__name__}]: {exc}")
            self.unavailable = failure_answer(exc, "its connection")
            return None

    def _actor_manager(self):
        if self._actor_mgr is None:
            try:
                from pyegeria.omvs.actor_manager import ActorManager
                self._actor_mgr = self._make_client(ActorManager) or False
            except ImportError:
                self._actor_mgr = False
        return self._actor_mgr or None

    def _project_manager(self):
        if self._project_mgr is None:
            try:
                from pyegeria.omvs.project_manager import ProjectManager
                self._project_mgr = self._make_client(ProjectManager) or False
            except ImportError:
                self._project_mgr = False
        return self._project_mgr or None

    def _governance_officer(self):
        if self._gov_officer is None:
            try:
                from pyegeria.omvs.governance_officer import GovernanceOfficer
                self._gov_officer = self._make_client(GovernanceOfficer) or False
            except ImportError:
                self._gov_officer = False
        return self._gov_officer or None

    def _glossary_manager(self):
        if self._glossary_mgr is None:
            try:
                from pyegeria.omvs.glossary_manager import GlossaryManager
                self._glossary_mgr = self._make_client(GlossaryManager) or False
            except ImportError:
                self._glossary_mgr = False
        return self._glossary_mgr or None

    def _automated_curation(self):
        if self._automated_curation_client is None:
            try:
                from pyegeria.omvs.automated_curation import AutomatedCuration
                self._automated_curation_client = self._make_client(AutomatedCuration) or False
            except ImportError:
                self._automated_curation_client = False
        return self._automated_curation_client or None

    def _valid_metadata_manager(self):
        if self._valid_metadata_mgr is None:
            try:
                from pyegeria.omvs.valid_metadata import ValidMetadataManager
                self._valid_metadata_mgr = self._make_client(ValidMetadataManager) or False
            except ImportError:
                self._valid_metadata_mgr = False
        return self._valid_metadata_mgr or None


    # ── Public API ─────────────────────────────────────────────────────────────

    def _not_read(self) -> dict:
        """{state, message} for a client that could not be made."""
        return dict(self.unavailable or {"state": READ_FAILED,
                                         "message": "Egeria not reached: the pyegeria client could not be created."})

    def _lookup_by_name(self, client, method: str, name: str, kind: str) -> dict:
        """{state: found | not_found | <not read>, element, message} for one exact-name lookup.

        Only a case-insensitive exact display-name match counts as found. pyegeria's name search
        also returns near names; those are reported in the message, never taken as the match.
        """
        if not client:
            return {**self._not_read(), "element": None}
        try:
            results = getattr(client, method)(name, output_format="JSON")
        except Exception as exc:
            logger.info(f"EgeriaContext.{method}({name!r}): {exc}")
            return {**failure_answer(exc, f"{kind} '{name}'"), "element": None}
        from pyegeria.core._globals import NO_ELEMENTS_FOUND
        if isinstance(results, str) and results.strip() == NO_ELEMENTS_FOUND:
            results = []
        if not isinstance(results, list):
            return {"state": READ_FAILED, "element": None,
                    "message": f"Could not read {kind} '{name}' from Egeria: unexpected answer {str(results)[:120]!r}"}
        parsed = [_parse_element(e) for e in results if isinstance(e, dict)]
        name_low = name.strip().lower()
        for p in parsed:
            if p["display_name"].strip().lower() == name_low:
                return {"state": FOUND, "element": p, "message": None}
        near = [p["display_name"] for p in parsed if p["display_name"]][:3]
        tail = f" Similar names: {', '.join(near)}." if near else ""
        return {"state": NOT_FOUND, "element": None,
                "message": f"Egeria holds no {kind} named '{name}' that {self.viewer} can see.{tail}"}

    def actor_answer(self, name: str) -> dict:
        """An Actor profile by exact display name: {state, element, message}."""
        return self._lookup_by_name(self._actor_manager(), "get_actor_profiles_by_name", name, "actor profile")

    def project_answer(self, name: str) -> dict:
        """A project (or campaign / study) by exact display name: {state, element, message}."""
        return self._lookup_by_name(self._project_manager(), "get_projects_by_name", name, "project")

    def glossary_answer(self, name: str) -> dict:
        """A glossary by exact display name: {state, element, message}."""
        return self._lookup_by_name(self._glossary_manager(), "get_glossaries_by_name", name, "glossary")

    def find_actor_by_name(self, name: str) -> dict | None:
        """The Actor profile with exactly this display name, or None (not found OR not read:
        use actor_answer() to tell them apart)."""
        return self.actor_answer(name)["element"]

    def find_project_by_name(self, name: str) -> dict | None:
        """See project_answer(); None means not found or not read."""
        return self.project_answer(name)["element"]

    def find_glossary_by_name(self, name: str) -> dict | None:
        """See glossary_answer(); None means not found or not read."""
        return self.glossary_answer(name)["element"]

    def governance_zones_answer(self) -> dict:
        """Every governance zone name this user can see: {state, zones, message}.

        Reads GovernanceZone definitions only (graph depth 0), a page at a time until Egeria returns
        an empty page (a short page is not the last page), up to ZONE_CAP. The old read took the first
        100 governance definitions of every kind; on a real Coco Pharmaceuticals estate those are all
        NotificationTypes, so it answered "0 zones" while Egeria held 24.
        Cached for the lifetime of this instance.
        """
        if self._zone_answer is not None:
            return self._zone_answer
        gov = self._governance_officer()
        if not gov:
            self._zone_answer = {**self._not_read(), "zones": []}
            return self._zone_answer
        from pyegeria.core._globals import NO_ELEMENTS_FOUND
        zones: list[str] = []
        read = 0
        capped = False
        try:
            while True:
                page = gov.find_governance_definitions(
                    "*", output_format="JSON", page_size=ZONE_PAGE_SIZE, start_from=read,
                    graph_query_depth=0, metadata_element_subtypes=["GovernanceZone"])
                if isinstance(page, str) and page.strip() == NO_ELEMENTS_FOUND:
                    break
                if not isinstance(page, list):
                    raise RuntimeError(f"unexpected answer {str(page)[:120]!r}")
                if not page:
                    break
                for elem in page:
                    if elem.get("elementHeader", {}).get("type", {}).get("typeName") == "GovernanceZone":
                        name = _display_name(elem.get("properties", {}))
                        if name:
                            zones.append(name)
                read += len(page)
                if read >= ZONE_CAP:
                    capped = len(page) == ZONE_PAGE_SIZE
                    break
        except Exception as exc:
            logger.info(f"EgeriaContext.governance_zones_answer: {exc}")
            self._zone_answer = {**failure_answer(exc, "governance zones"), "zones": []}
            return self._zone_answer
        zones = sorted(set(zones))
        if capped:
            answer = {"state": CAPPED, "zones": zones,
                      "message": f"Showing the first {ZONE_CAP} governance zones; Egeria may hold more."}
        elif zones:
            answer = {"state": OK, "zones": zones, "message": None}
        else:
            answer = {"state": NONE, "zones": [],
                      "message": f"Egeria holds no governance zones that {self.viewer} can see."}
        logger.debug(f"EgeriaContext: governance zones {answer['state']} ({len(zones)})")
        self._zone_answer = answer
        return answer

    def list_governance_zones(self) -> list[str]:
        """Zone names only; [] is ambiguous (see governance_zones_answer())."""
        return self.governance_zones_answer()["zones"]

    def technology_types_answer(self) -> dict:
        """Every Technology Type display name: {state, technology_types, message}.

        Backs the "Planned Deployed Implementation Type" attribute on Create Solution Component (and
        similar). Cached for the lifetime of this instance.
        """
        if self._tech_type_answer is not None:
            return self._tech_type_answer
        client = self._automated_curation()
        if not client:
            self._tech_type_answer = {**self._not_read(), "technology_types": []}
            return self._tech_type_answer
        from pyegeria.core._globals import NO_ELEMENTS_FOUND
        try:
            results = client.get_all_technology_types(output_format="JSON")
            if isinstance(results, str) and results.strip() == NO_ELEMENTS_FOUND:
                results = []
            if not isinstance(results, list):
                raise RuntimeError(f"unexpected answer {str(results)[:120]!r}")
        except Exception as exc:
            logger.info(f"EgeriaContext.technology_types_answer: {exc}")
            self._tech_type_answer = {**failure_answer(exc, "technology types"), "technology_types": []}
            return self._tech_type_answer
        names = sorted({item.get("displayName") for item in results
                        if isinstance(item, dict) and item.get("displayName")})
        self._tech_type_answer = (
            {"state": OK, "technology_types": names, "message": None} if names else
            {"state": NONE, "technology_types": [],
             "message": f"Egeria holds no technology types that {self.viewer} can see."})
        return self._tech_type_answer

    def list_technology_types(self) -> list[str]:
        """Technology type names only; [] is ambiguous (see technology_types_answer())."""
        return self.technology_types_answer()["technology_types"]

    def enrich_entities(self, entities: dict) -> dict:
        """
        Enrich an extracted entities dict with live Egeria context (mutates in place).

        Each role with a person gets ``actor_found``: True (exact match; adds actor_guid and
        actor_qualified_name), False (Egeria answered: no such actor) or None (Egeria NOT read: the
        lookup could not run). ``actor_lookup_message`` says which, in words.
        Each named object gets ``exists_in_egeria`` the same way (True / False / None) with
        ``exists_check_message``. None must never be shown as "not found".
        """
        for role in entities.get("roles", []):
            person = role.get("person", "")
            if not person:
                continue
            answer = self.actor_answer(person)
            role["actor_lookup_message"] = answer["message"]
            if answer["state"] == FOUND:
                role["actor_guid"] = answer["element"]["guid"]
                role["actor_qualified_name"] = answer["element"]["qualified_name"]
                role["actor_found"] = True
            elif answer["state"] == NOT_FOUND:
                role["actor_found"] = False
            else:
                role["actor_found"] = None
            logger.debug(f"EgeriaContext: actor {person!r} -> {answer['state']}")

        for obj in entities.get("objects", []):
            otype = obj.get("type", "")
            oname = obj.get("name", "")
            if not oname:
                continue
            if otype in ("project", "campaign", "study_project", "personal_project", "sub_project"):
                answer = self.project_answer(oname)
            elif otype == "glossary":
                answer = self.glossary_answer(oname)
            else:
                continue  # not a type we check
            obj["exists_check_message"] = answer["message"]
            if answer["state"] == FOUND:
                obj["exists_in_egeria"] = True
                obj["egeria_guid"] = answer["element"]["guid"]
            elif answer["state"] == NOT_FOUND:
                obj["exists_in_egeria"] = False
            else:
                obj["exists_in_egeria"] = None
            logger.debug(f"EgeriaContext: {otype} {oname!r} -> {answer['state']}")

        return entities
