"""Turns a curator's ACCEPTED verdict into a real Egeria `SolutionComponent`.

docs/architecture-recovery-report-then-curate.md's whole premise: RE reports
what the analysis believes; a curator decides whether it becomes real.
Everything up to here — findings, the review surface, the accept/reject/
retype verdict (this session's earlier work) — is RE reporting and a human
deciding. This module is the one remaining step: acting on that decision.

**Scope, deliberately narrow for this first slice:**

- Triggers on `verdict == "accepted"` ONLY. `rejected` writes nothing to
  Egeria — declining a proposal is not an Egeria event. `retyped` also
  writes nothing here; retyping is a correction to the PROPOSAL, and #10's
  design has no fixed vocabulary a retype could validate against (a repo's
  detected `type` values are free text), so this module trusts whatever the
  curator entered as-is if it later gets accepted under that new type — the
  same free-text field, not a second validated path.
- Creates a `SolutionComponent`, alone. Does NOT create or attach a
  `SolutionBlueprint` — candidate-cluster proposal (architecture-recovery-
  clustering.md, §10 of the report-then-curate note) is unbuilt, so there is
  no candidate blueprint to attach to yet. Wiring a materialized component
  into a blueprint is future work once that exists, not faked here.
- Does NOT touch an already-materialized component when a LATER verdict on
  the same scope changes (e.g. accept, then later reject). §9 of the
  report-then-curate note is explicit that reconciling a changed decision
  against an existing Egeria element "is not a reconciliation rule RE gets
  to apply" — that is a human choosing to update or retire the element
  directly in Egeria, not something this module does automatically.

**Idempotency, not a create-blind path.** Two things can each independently
cause a second "accepted" call for the same scope: a curator re-accepting
after re-running the survey, and this route being retried after a timeout
whose actual write succeeded. Both must land on the SAME Egeria element, not
a duplicate — the same qualifiedName-search-before-create discipline
`EgeriaPublisher._find_element_guid` already uses, backed by a local table
(`architecture_materialized_components`) that also skips the search call on
a warm read, mirroring `_find_or_create_asset`'s own caching shape.
"""
from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

from resource_explorer.egeria_timing import time_egeria_call
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

# Same defaults EgeriaPublisher uses (standard pyegeria env vars) — one
# Egeria connection convention for the whole codebase, not a second one
# invented here.
_DEFAULT_PLATFORM_URL = "https://localhost:9443"
_DEFAULT_VIEW_SERVER = "qs-view-server"
_DEFAULT_USER = "erinoverview"
_DEFAULT_TIMEOUT = 30

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class MaterializationError(RuntimeError):
    """Raised when Egeria credentials are absent, the platform is
    unreachable, or the create call itself fails. Caught at the route
    boundary (curate.py) and reported alongside the verdict — the verdict
    save is not rolled back for this, since the curator's decision is real
    and locally durable independent of whether Egeria is reachable right
    now (same non-fatal-but-visible shape as EgeriaPublisher.publish()'s
    own annotation_types_warning)."""


class ComponentMaterializer:
    """Owns its own lightweight Egeria connection rather than reusing
    EgeriaPublisher — this is triggered from a curator's verdict on ONE
    component (curate.py), a genuinely different call path from publishing
    a whole SurveyResult, and EgeriaPublisher's surface (asset/report/
    annotation lifecycle) is not what this needs."""

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
        # Whose materialization this is. Resolved at `_connect` rather than
        # here (see `resolve_identity`) — a materializer is built in one place
        # and used in another, and the identity that matters is the one in
        # force when Egeria is actually written to.
        self._identity = identity
        self.platform_url = platform_url or egeria_platform_url()
        self.view_server = view_server or egeria_view_server()
        self.user_id = user_id or os.getenv("EGERIA_USER", _DEFAULT_USER)
        self.user_password = user_password or ""   # not used to authenticate (Brief I)
        self.timeout = timeout or int(os.getenv("PYEGERIA_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT)))
        self._registry = registry
        self._solution_architect = None
        self._automated_curation = None

    def resolve_identity(self):
        """The `EgeriaIdentity` this materialization runs as.

        Constructor-supplied first (the worker knows whose run it executes),
        then the signed-in caller, then the service account. Curate is gated
        on a signed-in owner or curator before it reaches here, so the
        fallback is for background re-materialization only.
        """
        if self._identity is not None:
            return self._identity
        from resource_explorer.egeria_clients import current_principal

        # The declared daemon job (a queued run), else the signed-in Caller; never a silent
        # service-account fallback (Brief I).
        self._identity = current_principal()
        return self._identity

    def _connect(self) -> None:
        if not self.platform_url:
            raise MaterializationError(
                "EGERIA_PLATFORM_URL is not set. "
                "Add it to your .env file or pass platform_url= to ComponentMaterializer."
            )
        identity = self.resolve_identity()
        try:
            from pyegeria import AutomatedCuration
            from pyegeria.omvs.solution_architect import SolutionArchitect

            from resource_explorer.egeria_clients import egeria_client

            clients = egeria_client(identity, purpose="materialize components",
                                    view_server=self.view_server, platform_url=self.platform_url)
            self.user_id = clients.user_id
            self._solution_architect = clients.of(SolutionArchitect)

            # Used only for the qualifiedName idempotency check
            # (get_guid_for_name) — same helper EgeriaPublisher uses, same
            # reason: search before create, never create blind.
            self._automated_curation = clients.of(AutomatedCuration)
        except PermissionError:
            raise
        except ImportError as exc:
            raise MaterializationError(
                "pyegeria is not installed. Add it to your dependencies."
            ) from exc
        except Exception as exc:
            raise MaterializationError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc

    def _find_element_guid(self, qualified_name: str) -> str:
        """Same implementation as EgeriaPublisher._find_element_guid — kept
        as its own copy rather than a shared import because the two classes
        deliberately don't share a connection or a base class (see the
        module docstring); this is the one piece of logic worth keeping
        byte-identical between them regardless."""
        result = self._automated_curation.get_guid_for_name(qualified_name)
        if isinstance(result, list) and result:
            candidate = result[0] if isinstance(result[0], str) else result[0].get("guid", "")
            return candidate if _UUID_RE.match(candidate or "") else ""
        if isinstance(result, str) and _UUID_RE.match(result):
            return result
        return ""

    @staticmethod
    def qualified_name_for(entity_type: str, entity_slug: str, scope_locator: str) -> str:
        """`SolutionComponent::{entity_type}::{entity_slug}::{scope_locator}` —
        same naming shape as every other qualifiedName in this codebase
        (`GitHubRepository::{url}`, `SurveyReport::GitHubRepo::{slug}
        ::{ts}`): kind, then the path that makes it unique. scope_locator is
        the join key architecture_recovery findings already use (a
        component's path prefix), so this needs no separate identity."""
        return f"SolutionComponent::{entity_type}::{entity_slug}::{scope_locator}"

    def _privacy_for(self, entity_type: str, entity_slug: str):
        """`(private_owner, zones)` for this resource.

        `("", None)` — not private, publish normally.
        `(owner, [..])` — private, and the zone is confirmed enforced.
        `(owner, None)` — private, and it is NOT safe to publish. The caller
        must refuse; an unenforced zone is an *ignored* zone, which is public.

        The three cases are distinguished by the second element rather than by
        a bool, so a caller cannot accidentally treat "private but unsafe" as
        "not private" — which is the direction that leaks.
        """
        from resource_explorer.egeria_identity import private_zone_is_enforced, private_zones

        if not self._registry:
            return "", None
        try:
            owner = self._registry.private_owner_for_entity(entity_type, entity_slug)
        except Exception as exc:
            log.warning("could not determine private ownership for %s/%s — "
                        "materializing normally: %s", entity_type, entity_slug, exc)
            return "", None
        # Must be a real userId string. The value becomes a ZONE NAME, and a
        # non-string there is meaningless — `validateZoneAccess` compares it to
        # the caller's userId, so anything else silently matches nobody while
        # still marking the element private. Anything truthy-but-not-a-string
        # is a registry that is not answering this question, not an owner.
        if not isinstance(owner, str) or not owner.strip():
            return "", None
        owner = owner.strip()
        if not private_zone_is_enforced():
            return owner, None
        return owner, private_zones(owner)

    def materialize(
        self,
        entity_type: str,
        entity_slug: str,
        scope_locator: str,
        *,
        name: str,
        component_type: str = "",
        perspective: str = "",
        confidence: int = 0,
    ) -> dict:
        """Find-or-create the SolutionComponent for one accepted proposal.

        Returns {"status": "already_materialized" | "materialized",
        "guid": ..., "qualified_name": ...}. Raises MaterializationError on
        any failure to reach or write to Egeria — the caller (curate.py)
        catches this and reports it alongside the verdict, which is already
        saved and does not get rolled back.
        """
        qualified_name = self.qualified_name_for(entity_type, entity_slug, scope_locator)

        # Privacy FIRST, before `_connect()` and before anything is created.
        #
        # This is a SECOND publish path and therefore a second chance to leak —
        # the shape the design removes by anchoring instead of stamping (Phase
        # 4, not built). Until that lands every stamping path has to ask the
        # same question, so it is asked here too.
        #
        # It sits at the top rather than next to the stamping call, and that
        # placement is the whole point: the first version checked just before
        # `stamp_published`, by which time the SolutionComponent had already
        # been created in Egeria. Refusing there left a real, unzoned element
        # behind and reported "skipped" — strictly worse than either doing it
        # or not. Caught when a sabotage run created one for real.
        private_owner, private_zones_for_owner = self._privacy_for(entity_type, entity_slug)
        if private_owner and private_zones_for_owner is None:
            return {"status": "skipped",
                    "reason": ("belongs to a private investigation and the private "
                               "governance zone is not confirmed to be enforced; "
                               "nothing was written to Egeria"),
                    "scope_locator": scope_locator,
                    "qualified_name": qualified_name}
        # The same question for the draft zone it would be born in: never one that grants nobody once Egeria
        # e9fd817f4c ships (or whose control could not be read). At the top for the reason above.
        if not private_owner:
            from resource_explorer.egeria_identity import draft_zones, zone_lockout_refusal

            refusal = zone_lockout_refusal(draft_zones())
            if refusal:
                return {"status": "skipped", "reason": f"{refusal}; nothing was written to Egeria",
                        "scope_locator": scope_locator, "qualified_name": qualified_name}

        # Local cache first — same shape as _find_or_create_asset's cached-
        # GUID check, and for the same reason: a repeat accept (re-running
        # the survey, or a retried request) should not cost a search call,
        # let alone a create.
        if self._registry:
            cached = self._registry.get_materialized_component(entity_type, entity_slug, scope_locator)
            if cached and cached.get("guid"):
                return {"status": "already_materialized", "guid": cached["guid"],
                        "qualified_name": cached["qualified_name"]}

        self._connect()

        existing_guid = self._find_element_guid(qualified_name)
        if existing_guid:
            if self._registry:
                self._registry.record_materialized_component(
                    entity_type, entity_slug, scope_locator, qualified_name, existing_guid,
                )
            return {"status": "already_materialized", "guid": existing_guid,
                    "qualified_name": qualified_name}

        properties: dict = {
            "class": "SolutionComponentProperties",
            "qualifiedName": qualified_name,
            "displayName": name,
            # Backlog.md item 6 / blueprint_materializer.py's own docstring
            # (2026-09-03): the same Draft-status gap BlueprintMaterializer
            # had, fixed the same way — contentStatus is a plain field on
            # ReferenceableProperties, settable inside properties on the
            # existing NewElementRequestBody. architecture-recovery.md §10
            # Phase 2's "All at ContentStatus = Draft" now holds for
            # components too, not just blueprints.
            "contentStatus": "DRAFT",
        }
        # Confidence and perspective are evidence ABOUT the proposal, not
        # properties of the real element a curator just decided is real —
        # accepting is the point at which that evidence stops mattering to
        # what gets written, so neither rides on the created element.
        # additionalProperties still carries them for now, since there is no
        # typed home for "recovered from architecture_recovery, originally
        # proposed at N% confidence" on a SolutionComponent today, and
        # dropping that provenance entirely would be a worse default than an
        # untyped string.
        additional = {"recoveredBy": "architecture_recovery"}
        # Whose write this is (Brief I): `requestedBy` beside the other provenance keys, and the
        # Ownership owner (= the requester), from the one on-behalf helper.
        from resource_explorer.egeria_identity import on_behalf_of

        behalf = on_behalf_of(self.resolve_identity())
        additional.update(behalf.provenance())
        if perspective:
            additional["perspective"] = perspective
        if confidence:
            additional["originalConfidence"] = str(confidence)
        properties["additionalProperties"] = additional
        if component_type:
            properties["solutionComponentType"] = component_type

        body = {
            "class": "NewElementRequestBody",
            "isOwnAnchor": True,
            "properties": properties,
        }
        try:
            with time_egeria_call(self._registry, "create_solution_component", "write"):
                guid = self._solution_architect.create_solution_component(body)
        except Exception as exc:
            raise MaterializationError(f"Egeria rejected the new SolutionComponent: {exc}") from exc
        if not guid or not _UUID_RE.match(guid):
            raise MaterializationError(f"Egeria returned no usable GUID for the new SolutionComponent (got {guid!r})")

        # Ownership + the **draft** zone, like every other element RE creates.
        # Promotion into the publish zones is `workflows.curate.
        # promote_to_publish_zones`, called by the accept path immediately
        # after this.
        #
        # **Changed after a live failure, 2026-09-04.** This first wrote the
        # publish zones directly, on the reasoning that a materialized
        # component *is* the accepted outcome and should start where accepted
        # elements belong. Egeria disagreed, in as many words:
        #
        #     OMAG-SERVER-SECURITY-403-005 User erinoverview is not authorized
        #     to change the zone membership for element ... from
        #     [egeria-runtime] to [egeria-runtime]
        #
        # The security connector refuses a *no-op* zone change, so the accept
        # path's promotion then 403'd on an element that was already where it
        # belonged. One rule is both simpler and what the plan actually asks
        # for: **everything RE creates is born in the CONFIGURED draft zone
        # (if any; none by default since 2026-10-08), and acceptance is what moves it.** That makes the transition real and
        # observable rather than something one code path skips.
        from resource_explorer.egeria_identity import draft_zones, stamp_published

        identity = self.resolve_identity()
        # Decided at the top of this method, before anything was created.
        # A component materialised from a repo in someone's personal
        # investigation is derived from their private work: born in the draft
        # zone with the publishing identity as owner it would be visible to
        # every curator, and owned by whoever happened to run the analysis.
        # Private-zone handling is unchanged: a private investigation's owner owns its component.
        owner = private_owner or behalf.owner
        zones = private_zones_for_owner or draft_zones()

        governance = stamp_published(guid, owner, identity=identity, zones=zones)

        if self._registry:
            self._registry.record_materialized_component(
                entity_type, entity_slug, scope_locator, qualified_name, guid,
            )
        result = {"status": "materialized", "guid": guid, "qualified_name": qualified_name,
                  "governance": governance}
        if owner and not governance.get("ownership"):
            result["requester_not_recorded"] = "requester not recorded (Ownership was not set; see the log)"
        return result
