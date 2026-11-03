"""Turns a curator's ACCEPTED verdict on a proposed port into a real Egeria
`SolutionPort`, attached to its owning `SolutionComponent`.

**TEMPORARY, by explicit project-owner instruction (2026-09-03/04).**
`SolutionArchitect` has no `create_solution_port` method, and confirmed live
against the actual OpenAPI spec (`GET /v3/api-docs`, 2.39MB, every path
containing "port" enumerated) — there is no `POST .../solution-ports` create
endpoint anywhere in the server's API surface at all, not a client-side
pyegeria omission. Logged as Backlog.md item 8 / egeria-python
`PYEGERIA_ISSUES.md` ISSUE-85. Until pyegeria ships a real
`create_solution_port`, this module reaches the same result through the
generic `MetadataExpert.create_metadata_element` route
(`POST .../metadata-elements`, `typeName="SolutionPort"`) — confirmed live,
create → `link_solution_component_port` (attach) → read back via
`get_anchored_element_graph` all worked correctly end to end (throwaway
probe, created + verified + deleted 2026-09-04; see memory note
`reference_egeria_dedup_and_link_patterns` for the full trail, including
the two wrong turns along the way — a first `PortProperties.direction`
attempt used the WRONG enum, `typeName="PortType"`/`symbolicName="Input"`,
which belongs to the older, unrelated classic `Port` type; the real
registered enum is `SolutionPortDirection` with values `UNKNOWN`/`INPUT`/
`OUTPUT`/`INOUT`/`OUTIN`/`OTHER`).

**Delete this module's create path — not its callers' contract — the day
pyegeria ships `create_solution_component_port`.** `materialize_port_element`
is written to mirror `BlueprintMaterializer.materialize_blueprint_element`'s
public shape exactly (`{"status": ..., "guid": ..., "qualified_name": ...}`,
same idempotency-first structure) so a caller never has to change when the
create body inside is swapped for a real dedicated method call.

**Scope, deliberately narrow, same reasoning as `BlueprintMaterializer`'s own
"scope, deliberately narrow" framing:** `materialize_port_element` finds-or-
creates the `SolutionPort` element AND attaches it to its owning component
via `SolutionComponentPort` (confirmed `UNIT_LINK`, safe to retry) in one
call — unlike blueprint member attachment, a port with no owning component
is not a meaningful proposal, so there is no "materialize the port alone"
half-step worth splitting out.

Classic `Port`/`PortImplementation`/`PortAlias` (a different, older type
family) were considered and ruled out — see the memory note for why: their
attach relationship (`ProcessPort`) requires the owning end to be typed
`Process`, a subtype of `Asset`, while `SolutionComponent` is a subtype of
`DesignModelElement`. No overlap; a classic `Port` cannot attach to a
`SolutionComponent` this way. `SolutionPort` is the right type — same
`DesignModelElement` branch as `SolutionComponent`/`SolutionBlueprint`.
"""
from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

# Same defaults every other materializer in this package uses (standard
# pyegeria env vars) — one Egeria connection convention for the whole
# codebase, not a second one invented here.
_DEFAULT_PLATFORM_URL = "https://localhost:9443"
_DEFAULT_VIEW_SERVER = "qs-view-server"
_DEFAULT_USER = "erinoverview"
_DEFAULT_TIMEOUT = 30

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

# The REAL registered enum for SolutionPort.direction — confirmed live via
# ValidMetadataManager.get_typedef_by_name("SolutionPort")'s
# propertiesDefinition, not the OpenAPI schema's toString-derived enum
# listing (which shows the value strings but not the registered type name,
# and which a first attempt here wrongly matched to the unrelated classic
# Port.portType enum's OpenAPI listing instead). Values map RE's own
# port-direction vocabulary (see _architecture_interfaces_results'
# `direction` field, sourced from Dockerfile EXPOSE / compose ports) onto
# the Egeria-registered symbolic names.
_DIRECTION_ENUM_TYPE = "SolutionPortDirection"
_DIRECTION_MAP: dict[str, str] = {
    "input": "INPUT",
    "output": "OUTPUT",
    "input-output": "INOUT",
    "output-input": "OUTIN",
    "": "UNKNOWN",
}


def _direction_symbolic_name(direction: str) -> str:
    return _DIRECTION_MAP.get((direction or "").strip().lower(), "OTHER")


class PortMaterializationError(RuntimeError):
    """Raised when Egeria credentials are absent, the platform is
    unreachable, or the create/attach call itself fails. Caught at the
    route boundary and reported alongside the verdict — the verdict save is
    not rolled back for this, same non-fatal-but-visible shape as every
    other materializer in this package."""


class PortMaterializer:
    """Owns its own lightweight Egeria connection rather than reusing
    EgeriaPublisher/ComponentMaterializer/BlueprintMaterializer — same
    reasoning those give for why: a genuinely different call path (this one
    needs `MetadataExpert`, not just `SolutionArchitect`), kept
    unit-testable with a fake registry and no outbox machinery."""

    def __init__(
        self,
        platform_url: str | None = None,
        view_server: str | None = None,
        user_id: str | None = None,
        user_password: str | None = None,
        timeout: int | None = None,
        registry: "ProjectRegistry | None" = None,
        identity=None,
    ) -> None:
        self._identity = identity
        self.platform_url = platform_url or egeria_platform_url()
        self.view_server = view_server or egeria_view_server()
        self.user_id = user_id or os.getenv("EGERIA_USER", _DEFAULT_USER)
        self.user_password = user_password or ""   # not used to authenticate (Brief I)
        self.timeout = timeout or int(os.getenv("PYEGERIA_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT)))
        self._registry = registry
        self._metadata_expert = None
        self._solution_architect = None

    def _connect(self) -> None:
        if not self.platform_url:
            raise PortMaterializationError(
                "EGERIA_PLATFORM_URL is not set. "
                "Add it to your .env file or pass platform_url= to PortMaterializer."
            )
        try:
            from pyegeria import MetadataExpert
            from pyegeria.omvs.solution_architect import SolutionArchitect

            from resource_explorer.egeria_clients import current_principal, egeria_client

            # Who (Brief I): the run's declared identity, else the signed-in Caller. This used
            # to be the service account always.
            clients = egeria_client(self._identity or current_principal(), purpose="materialize ports",
                                    view_server=self.view_server, platform_url=self.platform_url)

            # MetadataExpert: the generic create/read route (no dedicated
            # SolutionPort wrapper exists). SolutionArchitect: the real,
            # dedicated link_solution_component_port attach call — that one
            # IS a proper pyegeria method, only creation is missing.
            self._metadata_expert = clients.of(MetadataExpert)
            self._solution_architect = clients.of(SolutionArchitect)
        except PermissionError:
            raise
        except ImportError as exc:
            raise PortMaterializationError(
                "pyegeria is not installed. Add it to your dependencies."
            ) from exc
        except Exception as exc:
            raise PortMaterializationError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc

    def _find_element_guid(self, qualified_name: str) -> str:
        """Byte-identical in spirit to ComponentMaterializer/
        BlueprintMaterializer's own copies — search before create, never
        create blind. Uses MetadataExpert's own name-lookup rather than
        AutomatedCuration's (which the other two materializers use) since
        this class doesn't otherwise need AutomatedCuration at all."""
        result = self._metadata_expert.get_metadata_guid_by_unique_name(qualified_name)
        if isinstance(result, list) and result:
            candidate = result[0] if isinstance(result[0], str) else result[0].get("guid", "")
            return candidate if _UUID_RE.match(candidate or "") else ""
        if isinstance(result, str) and _UUID_RE.match(result):
            return result
        return ""

    @staticmethod
    def qualified_name_for(entity_type: str, entity_slug: str, scope_locator: str, port_name: str) -> str:
        """`SolutionPort::{entity_type}::{entity_slug}::{scope_locator}::{port_name}`
        — same naming shape as every other qualifiedName in this codebase:
        kind, then the path that makes it unique. `scope_locator` identifies
        the owning component; `port_name` disambiguates multiple ports on
        one component (e.g. an HTTP port and a metrics port)."""
        return f"SolutionPort::{entity_type}::{entity_slug}::{scope_locator}::{port_name}"

    def materialize_port_element(
        self,
        entity_type: str,
        entity_slug: str,
        scope_locator: str,
        port_name: str,
        *,
        component_guid: str,
        direction: str = "",
    ) -> dict:
        """Find-or-create the SolutionPort element (via the generic
        MetadataExpert route — see module docstring) AND attach it to
        `component_guid` via SolutionComponentPort (confirmed UNI_LINK,
        safe to retry). Synchronous, same shape as ComponentMaterializer.
        materialize()/BlueprintMaterializer.materialize_blueprint_element().

        Returns {"status": "already_materialized" | "materialized",
        "guid": ..., "qualified_name": ...}. Raises
        PortMaterializationError on any failure to reach or write to
        Egeria — the caller catches this and reports it alongside the
        verdict, which is already saved and does not get rolled back.
        """
        qualified_name = self.qualified_name_for(entity_type, entity_slug, scope_locator, port_name)

        if self._registry:
            cached = self._registry.get_materialized_port(
                entity_type, entity_slug, scope_locator, port_name
            )
            if cached and cached.get("guid"):
                return {"status": "already_materialized", "guid": cached["guid"],
                        "qualified_name": cached["qualified_name"]}

        self._connect()

        existing_guid = self._find_element_guid(qualified_name)
        if existing_guid:
            if self._registry:
                self._registry.record_materialized_port(
                    entity_type, entity_slug, scope_locator, port_name, qualified_name, existing_guid,
                )
            self._attach_if_needed(component_guid, existing_guid)
            return {"status": "already_materialized", "guid": existing_guid,
                    "qualified_name": qualified_name}

        body = {
            "class": "NewOpenMetadataElementRequestBody",
            "typeName": "SolutionPort",
            "isOwnAnchor": True,
            "properties": {
                "class": "NewElementProperties",
                "propertyValueMap": {
                    "qualifiedName": {
                        "class": "PrimitiveTypePropertyValue", "typeName": "string",
                        "primitiveTypeCategory": "OM_PRIMITIVE_TYPE_STRING",
                        "primitiveValue": qualified_name,
                    },
                    "displayName": {
                        "class": "PrimitiveTypePropertyValue", "typeName": "string",
                        "primitiveTypeCategory": "OM_PRIMITIVE_TYPE_STRING",
                        "primitiveValue": port_name,
                    },
                    "direction": {
                        "class": "EnumTypePropertyValue", "typeName": _DIRECTION_ENUM_TYPE,
                        "symbolicName": _direction_symbolic_name(direction),
                    },
                },
            },
        }
        try:
            guid = self._metadata_expert.create_metadata_element(body)
        except Exception as exc:
            raise PortMaterializationError(
                f"Egeria rejected the new SolutionPort: {exc}"
            ) from exc
        if not guid or not _UUID_RE.match(guid):
            raise PortMaterializationError(
                f"Egeria returned no usable GUID for the new SolutionPort (got {guid!r})"
            )

        self._attach_if_needed(component_guid, guid)

        # Whose write this is (Brief I), from the one on-behalf helper: the generic create body
        # above carries no additionalProperties, so `requestedBy` is merged in after it (read, merge,
        # write), and Ownership = the requester. A stamp that did not land is reported as partial.
        from resource_explorer.egeria_clients import current_principal
        from resource_explorer.egeria_identity import on_behalf_of, stamp_on_behalf

        identity = self._identity or current_principal()
        behalf = on_behalf_of(identity)
        not_recorded = stamp_on_behalf(guid, behalf, identity=identity, props_client=self._metadata_expert)

        if self._registry:
            self._registry.record_materialized_port(
                entity_type, entity_slug, scope_locator, port_name, qualified_name, guid,
            )
        if not_recorded:
            return {"status": "partial", "words": f"partial · {not_recorded}", "guid": guid,
                    "qualified_name": qualified_name}
        return {"status": "materialized", "guid": guid, "qualified_name": qualified_name}

    def _attach_if_needed(self, component_guid: str, port_guid: str) -> None:
        """SolutionComponentPort is UNI_LINK (confirmed live) — safe to call
        every time, including on an already-materialized port, without
        risking a duplicate relationship. Failure here is real (surfaced,
        not swallowed) since an unattached port is not a useful proposal —
        unlike blueprint member attachment, there's no "partial" status to
        fall back to for a single port."""
        try:
            self._solution_architect.link_solution_component_port(component_guid, port_guid, None)
        except Exception as exc:
            raise PortMaterializationError(
                f"SolutionPort created/found ({port_guid}) but could not be attached "
                f"to component {component_guid}: {exc}"
            ) from exc
