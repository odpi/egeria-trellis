"""Turns a curator's ACCEPTED verdict on a candidate_blueprint finding into
a real Egeria `SolutionBlueprint`.

docs/blueprint-materialization-plan.md is the authoritative design for this
module; read it before changing anything here. This is Phase A of that
plan: the materializer itself, unit-testable in isolation with a fake/mocked
SolutionArchitect — no route, no outbox wiring, no frontend. Membership and
wire enqueueing (Phase B) are deliberately NOT here; see the module-level
scope note below and Decision 5 in the plan.

**Scope, deliberately narrow for this slice (mirrors materializer.py's own
"scope, deliberately narrow" framing):**

- `materialize_blueprint_element` finds-or-creates ONLY the `SolutionBlueprint`
  element itself. It does NOT attach members, does NOT attach child
  blueprints, and does NOT create any `SolutionLinkingWire`s — those are the
  route's job (Phase B), via the outbox, because a blueprint write is N+1+M
  elements and a crash mid-write must leave a resumable, visible state, not
  a half-formed live blueprint with no record of what's missing (plan
  Decision 5).
- `resolve_member_guids`/`resolve_child_blueprint_guids` are read-only
  lookups against already-materialized state. They never create anything
  and never raise for missing data — an unmaterialized member or child is
  data the caller (the route, in Phase B) acts on, not an error condition
  (plan Decisions 1 and 2: accepting a blueprint does NOT implicitly accept
  or materialize its members/children).

**Draft-status divergence from `ComponentMaterializer` — achieved, on the
second attempt (Backlog.md item 6, 2026-09-03).** `architecture-recovery.md`
§10 Phase 2 says explicitly "All at `ContentStatus = Draft`" for this
projection. First attempt: `class: "NewSolutionElementRequestBody"` with a
top-level `initialStatus: "DRAFT"`, per `SolutionArchitect.
create_solution_blueprint`'s own docstring — **failed client-side, before
any HTTP call**: `NewSolutionElementRequestBody` is documented in that
docstring in two separate pyegeria versions (5.3.4.23 here, 6.1.9 in the
canonical egeria-python checkout) but was never a real pydantic model, nor
(per egeria-python's own review, ISSUE-84) ever a real Egeria API surface —
`initialStatus` appears in no `.http` ground-truth file either. The
installed client validates every create-blueprint body against a bare
`TypeAdapter(NewElementRequestBody)`, whose `class` field is a strict
`Literal["NewElementRequestBody"]`; any other value raised a
`PyegeriaInvalidParameterException` ("Request body failed validation") that
read like an Egeria-side rejection but wasn't one. Second attempt, the real
mechanism (egeria-python-65's finding, odpi/egeria-python#337):
`contentStatus` is a plain field on `ReferenceableProperties` (base of
`SolutionBlueprintProperties`), settable inside `properties` on the
existing, real `NewElementRequestBody` — no separate request-body class
needed. Confirmed live against the real platform (post
`jdbcMaximumPoolSize` fix): `properties.contentStatus` round-trips as
`"DRAFT"` on read-back. `elementHeader.status` stays `"ACTIVE"` regardless
— that's OMRS's own instance-status axis (soft-delete/active at the
repository level), a different thing from the content-maturity axis
`contentStatus` represents; the design doc's own wording ("ContentStatus =
Draft") names the field this now sends, not the instance status.
`ComponentMaterializer` carried the identical gap (`NewElementRequestBody` with
no `contentStatus`) — fixed the same day, same fix, in that module directly
(`materializer.py`) rather than here, since it's out of scope for this module.

**Idempotency, not a create-blind path.** Same reasoning as
`ComponentMaterializer`: a repeat "accepted" call for the same
(perspective, cluster_name) — a curator re-accepting after re-running the
survey, or a retried request — must land on the SAME Egeria element, not a
duplicate. Local-cache check (`get_materialized_blueprint`) first, then a
qualifiedName search (`_find_element_guid`) before ever creating.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import logging
import os
import re
from typing import TYPE_CHECKING
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

# Same defaults ComponentMaterializer/EgeriaPublisher use (standard pyegeria
# env vars) — one Egeria connection convention for the whole codebase, not a
# second one invented here.
_DEFAULT_PLATFORM_URL = "https://localhost:9443"
_DEFAULT_VIEW_SERVER = "qs-view-server"
_DEFAULT_USER = "erinoverview"
_DEFAULT_TIMEOUT = 30

#: qualifiedName prefix of the SolutionComponents Egeria's content packs already define.
CONTENT_PACK_QN_PREFIX = "Egeria:ValidMetadataValue:"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class BlueprintMaterializationError(RuntimeError):
    """Raised when Egeria credentials are absent, the platform is
    unreachable, or the create call itself fails. Caught at the route
    boundary (curate.py, Phase B) and reported alongside the verdict — the
    verdict save is not rolled back for this, same non-fatal-but-visible
    shape as materializer.py's MaterializationError."""


class BlueprintIdentifierNeeded(BlueprintMaterializationError):
    """A second blueprint of a kind was accepted for a repository with no identifier. Raised BEFORE any
    search or create: nothing was written. The message is the sentence the pane shows."""


class BlueprintAmbiguous(BlueprintMaterializationError):
    """More than one element answers to the qualifiedName searched. Fail safe: nothing is adopted or created."""


#: The provenance RE writes on every SolutionBlueprint it creates (additionalProperties). The element is the
#: record; the registry is only its cache, so a cleared row is rebuilt from these.
PROVENANCE_VERSION = "1"
#: A claim older than this is treated as a crashed holder's (`registry.take_claim`, 900 seconds).
CLAIM_WINDOW_MINUTES = 15
#: A live holder re-stamps its claim this often (seconds) while it works, so a run longer than the window is
#: never mistaken for a crashed one. Well inside the window.
CLAIM_HEARTBEAT_SECONDS = 60


class BlueprintMaterializer:
    """Owns its own lightweight Egeria connection rather than reusing
    EgeriaPublisher or ComponentMaterializer — mirrors ComponentMaterializer's
    own reasoning for why (a genuinely different call path, not
    EgeriaPublisher's asset/report/annotation-lifecycle surface). Kept
    outbox-agnostic on purpose: it returns GUIDs, it doesn't enqueue
    membership or wires, which keeps it unit-testable with a fake registry
    and no outbox machinery, matching how ComponentMaterializer is tested
    today."""

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
        self._metadata_expert = None      # only for the explicit SolutionComposition read (see _composition_children)

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
            raise BlueprintMaterializationError(
                "EGERIA_PLATFORM_URL is not set. "
                "Add it to your .env file or pass platform_url= to BlueprintMaterializer."
            )
        identity = self.resolve_identity()
        try:
            from pyegeria import AutomatedCuration
            from pyegeria.omvs.solution_architect import SolutionArchitect

            from resource_explorer.egeria_clients import egeria_client

            clients = egeria_client(identity, purpose="materialize blueprint",
                                    view_server=self.view_server, platform_url=self.platform_url)
            self.user_id = clients.user_id
            self._solution_architect = clients.of(SolutionArchitect)

            # Used only for the qualifiedName idempotency check
            # (get_guid_for_name) — same helper ComponentMaterializer uses,
            # same reason: search before create, never create blind.
            self._automated_curation = clients.of(AutomatedCuration)

            # The explicit read of a container's SolutionComposition relationships (6.2 leaves the
            # children key off a component that has none, so the element read alone cannot say "none").
            from pyegeria.omvs.metadata_expert import MetadataExpert
            self._metadata_expert = clients.of(MetadataExpert)
        except PermissionError:
            raise
        except ImportError as exc:
            raise BlueprintMaterializationError(
                "pyegeria is not installed. Add it to your dependencies."
            ) from exc
        except Exception as exc:
            raise BlueprintMaterializationError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc

    def _find_element_guid(self, qualified_name: str) -> str:
        """Byte-identical copy of ComponentMaterializer._find_element_guid —
        kept as its own copy rather than a shared import for the same reason
        that class gives for not sharing one with EgeriaPublisher: the two
        classes deliberately don't share a connection or a base class."""
        result = self._automated_curation.get_guid_for_name(qualified_name)
        if isinstance(result, list) and result:
            candidate = result[0] if isinstance(result[0], str) else result[0].get("guid", "")
            return candidate if _UUID_RE.match(candidate or "") else ""
        if isinstance(result, str) and _UUID_RE.match(result):
            return result
        return ""

    @staticmethod
    def qualified_name_for(entity_type: str, entity_slug: str, perspective: str, cluster_name: str) -> str:
        """The PRE-#556 form `SolutionBlueprint::{entity_type}::{entity_slug}::{perspective}::{cluster_name}`.
        LEGACY: it carries the root cluster's name, which the architect's ruling (2026-10-08) took out of a
        blueprint's identity. Kept only so a blueprint written under it is recognised and adopted; nothing is
        created under it. The identity RE writes is `blueprint_kinds.identity_qualified_name` (kind + repository)."""
        return f"SolutionBlueprint::{entity_type}::{entity_slug}::{perspective}::{cluster_name}"

    def _activity(self, entity_type: str, entity_slug: str, summary: str, *, name: str = "",
                  location: str = "", status: str = "ok") -> None:
        """One activity row on the repository's feed. Never raises: it is the visibility mechanism, not the work."""
        if not self._registry:
            return
        try:
            from resource_explorer.activity_logger import log_catalog
            log_catalog(self._registry, entity_type, entity_slug, name, location, status=status, summary=summary)
        except Exception:
            log.warning("could not write the activity row: %s", summary, exc_info=True)

    def _already_logged(self, entity_type: str, entity_slug: str, needle: str) -> bool:
        try:
            rows = self._registry.list_activity(entity_type=entity_type, entity_slug=entity_slug,
                                                operation="catalog", limit=500)
            return any(needle in (r.get("summary") or "") for r in rows)
        except Exception:
            return False

    def _log_legacy_adoption(self, entity_type: str, entity_slug: str, guid: str, *, name: str = "",
                             location: str = "") -> None:
        """Say, once per adopted blueprint, why it still carries a root name (never on a repeat cache hit)."""
        if not self._registry:
            return
        summary = f"adopted legacy-named blueprint {guid} \u00b7 delete it in Egeria to recreate under the new name"
        if self._already_logged(entity_type, entity_slug, f"adopted legacy-named blueprint {guid}"):
            return
        self._activity(entity_type, entity_slug, summary, name=name, location=location)

    def _record(self, entity_type, entity_slug, perspective, cluster_name, qualified_name, guid) -> None:
        """Record the cache row; say so when it displaced another cluster's row under the same qualifiedName."""
        if not self._registry:
            return
        entry = self._registry.record_materialized_blueprint(
            entity_type, entity_slug, perspective, cluster_name, qualified_name, guid)
        displaced = entry.get("displaced") if isinstance(entry, dict) else None
        if displaced:
            self._activity(entity_type, entity_slug,
                           f"blueprint {qualified_name} re-keyed to cluster {cluster_name!r}; displaced the cached "
                           f"row of cluster(s) {', '.join(repr(d) for d in displaced)}",
                           name=cluster_name, location=qualified_name)

    @staticmethod
    def _additional_properties(element) -> dict:
        """The additionalProperties of an element read by GUID, raw (`properties.additionalProperties`) or
        formatted (`additionalProperties` / `additional_properties`). {} when the answer carries none."""
        if not isinstance(element, dict):
            return {}
        props = element.get("properties") if isinstance(element.get("properties"), dict) else {}
        for source in (props, element):
            for key in ("additionalProperties", "additional_properties"):
                value = source.get(key)
                if isinstance(value, dict):
                    return value
        return {}

    def _find_blueprint(self, qualified_name: str) -> dict | None:
        """The SolutionBlueprint whose qualifiedName is EXACTLY this, or None. The search is restricted to the
        qualifiedName property and the SolutionBlueprint type, and the hit is READ BACK and verified (type
        SolutionBlueprint, qualifiedName equal): a hit that fails is not ours and raises, because creating
        beside it could duplicate the qualifiedName. More than one hit is `BlueprintAmbiguous`.
        Returns {guid, display_name, additional}."""
        try:
            result = self._automated_curation.get_guid_for_name(
                qualified_name, property_name=["qualifiedName"], type_name="SolutionBlueprint")
        except Exception as exc:
            text = f"{exc} {getattr(exc, 'context', '')}"
            if "Multiple elements" in text or "more than one" in text.lower():
                raise BlueprintAmbiguous(
                    f"ambiguous: more than one element in Egeria answers to {qualified_name}: "
                    f"nothing was adopted or created") from exc
            raise
        guid = ""
        if isinstance(result, list) and result:
            if len(result) > 1:
                raise BlueprintAmbiguous(
                    f"ambiguous: more than one element in Egeria answers to {qualified_name}: "
                    f"nothing was adopted or created")
            candidate = result[0] if isinstance(result[0], str) else result[0].get("guid", "")
            guid = candidate if _UUID_RE.match(candidate or "") else ""
        elif isinstance(result, str) and _UUID_RE.match(result):
            guid = result
        if not guid:
            return None
        element = self._solution_architect.get_solution_blueprint_by_guid(guid)
        type_name, found_qn = self._element_type_and_qn(element)
        if found_qn != qualified_name or (type_name and type_name != "SolutionBlueprint"):
            raise BlueprintMaterializationError(
                f"the element {guid} found for {qualified_name} is not that SolutionBlueprint "
                f"(type {type_name or 'unknown'}, qualifiedName {found_qn or 'unreadable'}): nothing was created")
        props = element.get("properties") if isinstance(element.get("properties"), dict) else {}
        display = (props.get("displayName") or element.get("displayName") or element.get("display_name") or "")
        return {"guid": guid, "display_name": display, "additional": self._additional_properties(element)}

    def _identity_clash(self, entity_type, entity_slug, perspective, cluster_name, identifier, live_clusters):
        """The registry row of ANOTHER live cluster whose identity gives the same Egeria identifier as the one
        asked for (`EGERIA-GIT-DEPLOYMENT[-<IDENTIFIER>]`, case and separators collapsed), or None."""
        from resource_explorer.blueprint_kinds import (
            identity_property,
            identity_qualified_name,
            legacy_qualified_names,
        )
        rows = self._registry.get_materialized_blueprints(entity_type, entity_slug)
        if not isinstance(rows, dict):
            return None
        want = identity_property(entity_slug, perspective, identifier)
        prefix = identity_qualified_name(entity_type, entity_slug, perspective)
        for row in rows.values():
            if row.get("cluster_name") == cluster_name and row.get("perspective") == perspective:
                continue
            if live_clusters is not None and row.get("cluster_name") not in live_clusters:
                continue
            qn = row.get("qualified_name") or ""
            if qn in legacy_qualified_names(entity_type, entity_slug, row.get("perspective", ""),
                                            row.get("cluster_name", "")):
                continue
            if qn == prefix:
                theirs = ""
            elif qn.startswith(prefix + "::"):
                theirs = qn[len(prefix) + 2:]
            else:
                continue
            if identity_property(entity_slug, perspective, theirs) == want:
                return row
        return None

    def _record_rekey_proof(self, entity_type, entity_slug, perspective, old_key, new_key, qualified_name,
                            guid, source="") -> str:
        """A proof row of its own kind ("rekey") in `catalogue_commit_proofs` (no new table): the PREVIOUS key
        (from the registry row being re-keyed, else the element's property, and which one), the new key and
        the GUID, written after the element was read back and the cache row rewritten. Proof rows are never
        edited. Returns "" on success or the reason it could not be written: the caller surfaces that in the
        result and an activity row (the adoption stands, unproven)."""
        try:
            if not self._registry:
                raise RuntimeError("no registry to write the proof to")
            self._registry.append_catalogue_commit_proof(
                entity_slug, proof="rekey", node_kind="blueprint_shape",
                table_name=f"{perspective}::{new_key}", element_guid=guid, qualified_name=qualified_name,
                detail={"entity_type": entity_type, "old_cluster_key": old_key, "old_key_source": source,
                        "new_cluster_key": new_key, "guid": guid})
            return ""
        except Exception as exc:
            log.warning("could not record the re-key proof for %s: %s", guid, exc)
            return f"{type(exc).__name__}: {exc}"

    def _decide_adoption(self, entity_type, entity_slug, perspective, cluster_name, qualified_name,
                         identifier, display_name, found, live_clusters=None, batch=None) -> tuple[str, str, str]:
        """An element exists under the NEW qualifiedName and this cluster has no usable cache row. Returns
        (the words of the adoption, the PREVIOUS cluster key when this is a re-key else "", where that key came
        from: "registry row" | "element property" | ""); the CALLER writes the cache row, the re-key proof and
        the activity row, in that order. Or raises a
        refusal, which is logged once per element here. Provenance RE wrote on the element is the PRIMARY rule;
        the displayName is the FALLBACK for an element without it.

        (a) re_cluster_key equals this cluster's: adopt (the caller rewrites a lost row: the element is the
            record, the registry its cache).
        (b) re_cluster_key present and different: if that cluster NO LONGER EXISTS (not in `live_clusters`;
            a re-survey renames clusters, so the key is not stable) this is the same real cluster under a new
            name: adopt and re-key the registry row. ONE adopter per element: among the live clusters with no
            cache row, only the first by name adopts; the others are refused (a re-cluster that split one group
            in two never leaves two claimants). RE writes nothing to the element (no new Egeria write kind: the
            publish does not touch additionalProperties after create), so the old `re_cluster_key` stays on it
            as a HINT, never a conflict; the registry row is the authority. If the other cluster is live, or
            `live_clusters` is unknown (None): refuse.
        (c) no provenance (not RE-made, or made before it): adopt only when the displayName equals what this
            cluster would produce and no LIVE other cluster's row records the qualifiedName; else refuse.
        """
        guid, prov = found["guid"], found["additional"]
        key = prov.get("re_cluster_key")
        gone = lambda name: live_clusters is not None and name not in live_clusters   # noqa: E731
        if key is not None and key == cluster_name:
            return (f"adopted blueprint {guid} named {qualified_name} for cluster {cluster_name!r} "
                    "\u00b7 matched by the provenance RE wrote on it"), "", ""
        if key and gone(key):
            rows = self._registry.get_materialized_blueprints(entity_type, entity_slug) if self._registry else {}
            rows = rows if isinstance(rows, dict) else {}
            # Claimants: the live clusters being ACCEPTED in this request (the pressed one plus its batch)
            # that have no cache row. First by name decides only inside one batch.
            claimants = sorted(c for c in ((batch or set()) | {cluster_name})
                               if c in live_clusters and f"{perspective}::{c}" not in rows)
            first = claimants[0] if claimants else cluster_name
            holders = [r for r in rows.values() if r.get("qualified_name") == qualified_name
                       and r.get("cluster_name") != cluster_name]
            live_holder = next((h for h in holders if not gone(h.get("cluster_name"))), None)
            if live_holder is not None:
                # A LIVE cluster's cache row owns the element: never re-key from it.
                from resource_explorer.blueprint_kinds import identifier_needed_sentence
                why = identifier_needed_sentence(perspective, entity_slug, str(live_holder.get("cluster_name")))
            elif first == cluster_name:
                holder = holders[0] if holders else None
                previous, source = ((holder["cluster_name"], "registry row") if holder else (key, "element property"))
                return (f"adopted blueprint {guid} named {qualified_name} for cluster {cluster_name!r} \u00b7 re-keyed "
                        f"from {previous!r}, which no longer exists; the element still carries re_cluster_key "
                        f"{key!r} as a hint"), previous, source
            else:
                why = (f"an element named {qualified_name} is being adopted for another cluster ({first}) in this "
                       "batch \u00b7 give this one an identifier")
        elif key:
            why = (f"an element named {qualified_name} already exists in Egeria for another cluster ({key}) "
                   "\u00b7 give this one an identifier")
        else:
            others = []
            if self._registry:
                rows = self._registry.get_materialized_blueprints(entity_type, entity_slug)
                if isinstance(rows, dict):
                    others = [r for r in rows.values() if r.get("qualified_name") == qualified_name
                              and not (r.get("cluster_name") == cluster_name and r.get("perspective") == perspective)
                              and not gone(r.get("cluster_name"))]
            if found["display_name"] == display_name and not others:
                return (f"adopted blueprint {guid} named {qualified_name} for cluster {cluster_name!r} "
                        "\u00b7 no provenance on it; matched by its displayName"), "", ""
            why = (f"an element named {qualified_name} already exists in Egeria and RE cannot tell which "
                   "cluster it is for \u00b7 give this one an identifier")
        needle = f"refused to adopt blueprint {guid} named {qualified_name} for cluster {cluster_name!r}"
        if not self._already_logged(entity_type, entity_slug, needle):
            self._activity(entity_type, entity_slug, f"{needle}: {why}", name=display_name,
                           location=qualified_name, status="failed")
        if identifier:
            why = why.replace("give this one an identifier", "give this one a different identifier")
            raise BlueprintMaterializationError(f"{why}: nothing was created")
        raise BlueprintIdentifierNeeded(why)

    def materialize_blueprint_element(
        self,
        entity_type: str,
        entity_slug: str,
        perspective: str,
        cluster_name: str,
        *,
        display_name: str,
        oversized: bool = False,
        verify_cached: bool = False,
        identifier: str = "",
        live_clusters: set[str] | None = None,
        batch: set[str] | None = None,
    ) -> dict:
        """Find-or-create the blueprint element, holding a CLAIM on its qualifiedName across check -> adopt or
        create -> cache row -> proof, so a worker and a UI request cannot both pass the check. The claim is
        released in a `finally` only when this call holds it. A refused claim writes nothing. See
        `_materialize_locked` for the rules."""
        from resource_explorer.blueprint_kinds import identity_qualified_name, validate_identifier
        held, holder, claim_key = False, uuid.uuid4().hex, ""
        if self._registry:
            try:
                qn = identity_qualified_name(entity_type, entity_slug, perspective,
                                             validate_identifier(identifier))
            except ValueError:
                qn = ""                      # the locked path refuses a bad identifier before anything
            if qn:
                claim_key = f"blueprint-claim::{qn}"
                taken = self._registry.take_claim(claim_key, holder)
                if taken is False:           # only a real False refuses (a bare fake registry has no claims)
                    raise BlueprintMaterializationError(self._claim_refusal(claim_key))
                held = taken is True
        beat = self._start_claim_heartbeat(claim_key, holder) if held else None
        try:
            return self._materialize_locked(
                entity_type, entity_slug, perspective, cluster_name, display_name=display_name,
                oversized=oversized, verify_cached=verify_cached, identifier=identifier,
                live_clusters=live_clusters, batch=batch)
        finally:
            if beat is not None:
                beat[0].set()
                beat[1].join(timeout=5)
            if held:
                try:
                    self._registry.release_claim(claim_key, holder)
                except Exception as exc:       # never mask the real outcome; the claim expires on its own
                    log.warning("could not release the claim %s: %s", claim_key, exc)
                    self._activity(entity_type, entity_slug,
                                   f"could not release the blueprint claim {claim_key} ({type(exc).__name__}: "
                                   f"{exc}); it expires {CLAIM_WINDOW_MINUTES} minutes after it was taken",
                                   status="failed")

    def _start_claim_heartbeat(self, claim_key: str, holder: str):
        """Renew the claim every CLAIM_HEARTBEAT_SECONDS while the holder works. Returns (stop_event, thread).
        The renewal is conditional on still holding the claim; once it is lost the heartbeat stops (it never
        re-takes a claim). A failed renewal is logged and retried; it never fails the work."""
        import threading
        stop = threading.Event()
        registry, interval = self._registry, CLAIM_HEARTBEAT_SECONDS

        def run():
            while not stop.wait(interval):
                try:
                    if registry.refresh_claim(claim_key, holder) is False:
                        log.warning("blueprint claim %s is no longer held; heartbeat stopped", claim_key)
                        return
                except Exception as exc:
                    log.warning("could not renew the claim %s: %s", claim_key, exc)

        t = threading.Thread(target=run, name="blueprint-claim-heartbeat", daemon=True)
        t.start()
        return stop, t

    def _claim_refusal(self, claim_key: str) -> str:
        """Why a press was refused, with the claim's age as a fact (not a verdict that its holder is dead)."""
        said = "another press is adopting this blueprint right now"
        try:
            got = self._registry.get_claim(claim_key)
            if got:
                taken = datetime.fromisoformat(got[1])
                age = int((datetime.utcnow() - taken).total_seconds())
                if age > 120:
                    expires = taken + timedelta(minutes=CLAIM_WINDOW_MINUTES)
                    said = (f"this blueprint was claimed {age // 60} minutes ago and the claim may be stale; "
                            f"it expires at {expires:%H:%M} UTC")
        except (ValueError, TypeError, KeyError) as exc:
            said += f" (the claim's age could not be read: {type(exc).__name__})"
        return f"{said}: nothing was written"

    def _materialize_locked(
        self,
        entity_type: str,
        entity_slug: str,
        perspective: str,
        cluster_name: str,
        *,
        display_name: str,
        oversized: bool = False,
        verify_cached: bool = False,
        identifier: str = "",
        live_clusters: set[str] | None = None,
        batch: set[str] | None = None,
    ) -> dict:
        """Find-or-create ONLY the SolutionBlueprint element itself (Draft,
        via NewSolutionElementRequestBody — see the module docstring's
        divergence note). Synchronous, same shape as
        ComponentMaterializer.materialize().

        **Identity: kind + repository** (architect's ruling, 2026-10-08):
        `SolutionBlueprint::<type>::<slug>::<kind>[::<identifier>]` (`blueprint_kinds.identity_qualified_name`).
        The root cluster's name is NOT in it and not in the cache key: the cluster name is RE's internal key,
        kept on the registry row. A second blueprint of a kind needs a PERSON's `identifier` (never derived
        from a cluster name); without one it is refused before anything is created.

        Blueprints written before that carry the cluster's name (two older forms,
        `blueprint_kinds.legacy_qualified_names`): they are ADOPTED, never duplicated and never created
        again, and the adoption is written to the activity log.

        `batch` (optional): the clusters accepted in THIS request. Today the route accepts one cluster per
        request, so the caller passes none and the batch is {the pressed cluster}; the parameter is the
        existing accept set, not a new API surface. Claimants for an element with a stale key are the live
        clusters of the batch with no cache row; first by name is the tie-break only inside one batch.

        `live_clusters` (optional): the names of the clusters that exist now. A registry row, or an element's
        `re_cluster_key`, naming a cluster not among them is stale (a re-survey renames clusters) and is taken
        over: adopted and re-keyed. None means unknown, and RE refuses conservatively.

        Returns {"status": "already_materialized" | "materialized",
        "guid": ..., "qualified_name": ...}. Raises
        BlueprintMaterializationError on any failure to reach or write to
        Egeria — the caller (curate.py, Phase B) catches this and reports it
        alongside the verdict, which is already saved and does not get
        rolled back.
        """
        from resource_explorer.blueprint_kinds import (
            identifier_needed_sentence,
            identity_property,
            identity_qualified_name,
            kind_word,
            legacy_qualified_names,
            validate_identifier,
        )
        try:
            identifier = validate_identifier(identifier)
        except ValueError as exc:
            raise BlueprintMaterializationError(f"{exc}: nothing was created") from exc
        qualified_name = identity_qualified_name(entity_type, entity_slug, perspective, identifier)
        legacy_names = legacy_qualified_names(entity_type, entity_slug, perspective, cluster_name)

        # Local cache first — same shape as ComponentMaterializer.materialize's
        # cached-GUID check, and for the same reason: a repeat accept
        # (re-running the survey, or a retried request) should not cost a
        # search call, let alone a create. The cluster's own row is read whatever name it was written
        # under (the registry's (..., perspective, cluster_name) key is the legacy key, read through the
        # mapping: a row whose qualifiedName is one of the old forms is an ADOPTED legacy blueprint).
        # The adoption was logged when it happened; a repeat cache hit logs nothing.
        if self._registry:
            cached = self._registry.get_materialized_blueprint(
                entity_type, entity_slug, perspective, cluster_name
            )
            if cached and cached.get("guid"):
                # `verify_cached` (the shape workflow): the cache names a GUID, and a blueprint a person has
                # since deleted in Egeria must not be re-attached to. Read it by GUID; gone means create
                # afresh. An UNREADABLE answer is not "gone" and raises, so the cache is never wiped on a
                # connection problem.
                hit = {"status": "already_materialized", "guid": cached["guid"],
                       "qualified_name": cached["qualified_name"]}
                if not verify_cached:
                    return hit
                self._ensure_connected()
                if self.blueprint_exists(cached["guid"]):
                    return hit

            # A second blueprint of the kind: another LIVE cluster already holds an identity that gives the
            # same Egeria identifier (case and separators collapsed, as `identity_property` does).
            clash = self._identity_clash(entity_type, entity_slug, perspective, cluster_name, identifier,
                                         live_clusters)
            if clash:
                if not identifier:
                    raise BlueprintIdentifierNeeded(identifier_needed_sentence(
                        perspective, entity_slug, str(clash.get("cluster_name") or "")))
                raise BlueprintMaterializationError(
                    f"the identifier {identifier!r} is already used by another {kind_word(perspective)} "
                    f"Blueprint for {entity_slug} \u00b7 give this one a different identifier: nothing was created")

        if not verify_cached:
            self._connect()
        else:
            self._ensure_connected()      # connected already by the cache check above, when it ran

        # A search that cannot be read is not a search that found nothing: creating on it could duplicate a
        # blueprint that is there (an outage, an index lag). Refuse instead.
        adopted_legacy = False
        try:
            found = self._find_blueprint(qualified_name)
            if not found:
                for legacy in legacy_names:
                    found = self._find_blueprint(legacy)
                    if found:
                        qualified_name, adopted_legacy = legacy, True
                        break
        except BlueprintMaterializationError:
            raise
        except Exception as exc:
            raise BlueprintMaterializationError(
                f"could not search Egeria for the blueprint by qualifiedName ({type(exc).__name__}): "
                f"nothing was created") from exc
        if found:
            guid = found["guid"]
            adoption, rekeyed_from, source = "", "", ""
            if not adopted_legacy:
                adoption, rekeyed_from, source = self._decide_adoption(
                    entity_type, entity_slug, perspective, cluster_name, qualified_name, identifier,
                    display_name, found, live_clusters, batch)
            self._record(entity_type, entity_slug, perspective, cluster_name, qualified_name, guid)
            result = {"status": "already_materialized", "guid": guid, "qualified_name": qualified_name}
            if rekeyed_from:
                proof_error = self._record_rekey_proof(entity_type, entity_slug, perspective, rekeyed_from,
                                                       cluster_name, qualified_name, guid, source)
                if proof_error:
                    result.update(status="adopted_unproven", proof_error=proof_error)
                    adoption += f" \u00b7 UNPROVEN: the re-key proof row could not be written ({proof_error})"
            if adoption and not self._already_logged(entity_type, entity_slug, adoption):
                self._activity(entity_type, entity_slug, adoption, name=display_name, location=qualified_name,
                               status="failed" if result["status"] == "adopted_unproven" else "ok")
            if adopted_legacy:
                self._log_legacy_adoption(entity_type, entity_slug, guid, name=display_name,
                                          location=qualified_name)
            return result

        properties: dict = {
            "class": "SolutionBlueprintProperties",
            "qualifiedName": qualified_name,
            "displayName": display_name,
            # architecture-recovery.md §10 Phase 2's "All at ContentStatus =
            # Draft" — achieved 2026-09-03, second attempt. The first
            # (class: "NewSolutionElementRequestBody" + top-level
            # initialStatus) never validated at all (Backlog.md item 6,
            # egeria-python PYEGERIA_ISSUES.md ISSUE-84). egeria-python-65's
            # review found the real mechanism: `contentStatus` is a plain
            # field on ReferenceableProperties (base of
            # SolutionBlueprintProperties), settable at creation like any
            # other property. Confirmed live against the real platform
            # (qs-view-server, post jdbcMaximumPoolSize fix): a blueprint
            # created with contentStatus: "DRAFT" round-trips it correctly
            # on read-back (properties.contentStatus == "DRAFT"). Separately
            # confirmed: elementHeader.status stays "ACTIVE" regardless —
            # that's OMRS's own instance-status axis (soft-delete/active at
            # the repository level), a different thing from the content-
            # maturity axis contentStatus represents; the design doc's own
            # wording ("ContentStatus = Draft") names exactly the field this
            # sends, not the instance status.
            "contentStatus": "DRAFT",
            # `<SLUG>-<KIND>` as the owner's own blueprints carry it. UNVERIFIED LIVE: pyegeria's
            # SolutionBlueprintProperties declares no `identifier` (only `version_identifier`; the Java
            # type may), so the wire name is the one the owner's blueprints show, sent as given.
            "identifier": identity_property(entity_slug, perspective, identifier),
        }
        # Same provenance reasoning as ComponentMaterializer.materialize:
        # this is evidence ABOUT the proposal, not a typed property of the
        # real element a curator just decided is real, so it rides in
        # additionalProperties rather than inventing a typed field.
        additional = {"recoveredBy": "architecture_recovery"}
        # Provenance (architect's ruling, 2026-10-08): what RE needs to recognise this element as its own
        # and as THIS cluster's, surviving a cleared registry row and a changed displayName.
        from resource_explorer.blueprint_kinds import kind_key
        additional.update({
            "re_entity_type": entity_type, "re_slug": entity_slug, "re_kind": kind_key(perspective),
            "re_cluster_key": cluster_name, "re_version": PROVENANCE_VERSION,
        })
        if identifier:
            additional["re_identifier"] = identifier
        if oversized:
            additional["oversized"] = "true"
        # Whose write this is (Brief I): `requestedBy` beside the provenance above, and Ownership =
        # the requester, from the one helper.
        from resource_explorer.egeria_identity import on_behalf_of

        behalf = on_behalf_of(self.resolve_identity())
        additional.update(behalf.provenance())
        properties["additionalProperties"] = additional

        # `class: "NewSolutionElementRequestBody"` + `initialStatus` (this
        # method's original body, matching SolutionArchitect.
        # create_solution_blueprint's own docstring) fails BEFORE any Egeria
        # call — confirmed live 2026-09-03 accepting a real candidate
        # blueprint (Backlog.md item 6) and by direct inspection: pyegeria
        # 5.3.4.23 (this checkout) and 6.1.9 (the canonical egeria-python
        # checkout) both document `NewSolutionElementRequestBody` in that
        # docstring but never define it as a pydantic model — the installed
        # `ServerClient._new_element_request_adapter` is a bare
        # `TypeAdapter(NewElementRequestBody)`, whose `class_` field is
        # `Literal["NewElementRequestBody"]`. Any other `class` value is a
        # client-side `PyegeriaInvalidParameterException`
        # ("Request body failed validation") raised locally by pydantic —
        # it never reaches the network, so this was never an Egeria-side
        # rejection despite how the caught exception reads. Logged as a
        # pyegeria gap (egeria-python's PYEGERIA_ISSUES.md) rather than
        # patched there directly, per this repo's own policy of routing
        # egeria-python fixes through that tracker.
        #
        # The fix here is the same one ComponentMaterializer already lives
        # with: `class: "NewElementRequestBody"`, no `initialStatus` (it
        # isn't a real field on that model either — `extra="ignore"` would
        # have silently dropped it even if the class name had validated).
        # This means materialized blueprints are created ACTIVE, not DRAFT,
        # same as materialized components — the module docstring's
        # "Divergence from ComponentMaterializer #1" no longer holds; both
        # materializers now share the identical gap, tracked as its own
        # follow-up (Backlog.md, "Confidence classification"/Draft-status
        # note) rather than fixed by inventing a body shape pyegeria can't
        # validate.
        body = {
            "class": "NewElementRequestBody",
            "isOwnAnchor": True,
            "properties": properties,
        }
        try:
            guid = self._solution_architect.create_solution_blueprint(body)
        except Exception as exc:
            raise BlueprintMaterializationError(
                f"Egeria rejected the new SolutionBlueprint: {exc}"
            ) from exc
        if not guid or not _UUID_RE.match(guid):
            raise BlueprintMaterializationError(
                f"Egeria returned no usable GUID for the new SolutionBlueprint (got {guid!r})"
            )

        from resource_explorer.egeria_identity import stamp_on_behalf

        # Ownership = the requester. Best-effort, and REPORTED: a stamp that did not land makes the
        # item partial ("requester not recorded (<reason>)"), never a plain "materialized".
        not_recorded = stamp_on_behalf(guid, behalf, identity=self.resolve_identity())
        self._record(entity_type, entity_slug, perspective, cluster_name, qualified_name, guid)
        result = {"status": "materialized", "guid": guid, "qualified_name": qualified_name}
        if not_recorded:
            result["requester_not_recorded"] = not_recorded
        return result

    # ── the shape writes (DESIGN-BLUEPRINT-BENCHMARK-EGERIA-WORKSPACES.md 6a) ─────────────────────────────

    def _ensure_connected(self) -> None:
        """Connect once. A cached-GUID path returns before `_connect`, so a method that needs the clients
        afterwards cannot assume them."""
        if self._solution_architect is None or self._automated_curation is None:
            self._connect()

    def blueprint_exists(self, guid: str) -> bool:
        """Whether a SolutionBlueprint is still in Egeria, read by GUID. Gone only on Egeria's own
        no-elements answer, a not-found exception or an API exception carrying a 404 (`egeria_absence`).
        Anything else (unauthorised, transport, 5xx, text we do not recognise) is UNREADABLE and raises:
        the caller must never create a second blueprint on a guess."""
        from resource_explorer.egeria_absence import ABSENT, PRESENT, is_absent

        try:
            result = self._solution_architect.get_solution_blueprint_by_guid(guid)
        except Exception as exc:
            if is_absent(exc) == ABSENT:
                return False
            raise BlueprintMaterializationError(
                f"could not tell whether the blueprint {guid} still exists: {type(exc).__name__}") from exc
        verdict = is_absent(result)
        if verdict == PRESENT:
            return True
        if verdict == ABSENT:
            return False
        raise BlueprintMaterializationError(
            f"could not tell whether the blueprint {guid} still exists: Egeria's answer was not readable")

    @staticmethod
    def _element_type_and_qn(element) -> tuple[str, str]:
        """(typeName, qualifiedName) of an element read by GUID, in either the raw or the formatted shape;
        "" for what the answer does not carry."""
        if not isinstance(element, dict):
            return "", ""
        header = element.get("elementHeader") or {}
        type_name = ((header.get("type") or {}).get("typeName") or element.get("typeName")
                     or element.get("type_name") or "")
        props = element.get("properties") or {}
        qn = props.get("qualifiedName") or element.get("qualifiedName") or element.get("qualified_name") or ""
        return type_name, qn

    def adopt_unmaterialized_members(
        self, registry, entity_type: str, entity_slug: str, member_slugs: list[str],
        slug_to_scope: dict[str, str],
    ) -> dict[str, str]:
        """slug -> GUID for members whose SolutionComponent already EXISTS in Egeria under RE's own
        qualifiedName though this registry never recorded it. They are ADOPTED (recorded, never created
        again): the owner's old blueprint left its components behind, and the new blueprint must meet them,
        not duplicate them. Creating a component stays the component verdict's job (privacy, zones), so a
        member Egeria does not hold is simply left out.

        `get_guid_for_name` searches several properties with no type restriction, so a hit is VERIFIED by
        reading it by GUID: it must be a SolutionComponent carrying exactly that qualifiedName. A hit that
        fails (or cannot be verified) is skipped and reported in `self.adoption_skipped` {slug: why}. Never
        raises."""
        from resource_explorer.surveyors.arch_recovery.materializer import ComponentMaterializer

        adopted: dict[str, str] = {}
        self.adoption_skipped: dict[str, str] = {}
        try:
            self._ensure_connected()
        except BlueprintMaterializationError as exc:
            log.warning("cannot adopt components, Egeria unreachable: %s", exc)
            for slug in member_slugs:
                self.adoption_skipped[slug] = "Egeria unreachable"
            return adopted
        for slug in member_slugs:
            scope = slug_to_scope.get(slug)
            if not scope:
                continue
            qn = ComponentMaterializer.qualified_name_for(entity_type, entity_slug, scope)
            try:
                guid = self._find_element_guid(qn)
            except Exception as exc:                       # several hits, or a search failure
                self.adoption_skipped[slug] = f"search failed: {type(exc).__name__}"
                continue
            if not guid:
                continue
            try:
                type_name, found_qn = self._element_type_and_qn(
                    self._solution_architect.get_solution_component_by_guid(guid))
            except Exception as exc:
                self.adoption_skipped[slug] = f"could not verify {guid}: {type(exc).__name__}"
                continue
            if type_name != "SolutionComponent" or found_qn != qn:
                self.adoption_skipped[slug] = (
                    f"{guid} is not a SolutionComponent named {qn} (read back as {type_name or 'unknown type'})")
                continue
            registry.record_materialized_component(entity_type, entity_slug, scope, qn, guid)
            adopted[slug] = guid
        return adopted

    def find_content_pack_component(self, display_name: str) -> dict | None:
        """The content-pack SolutionComponent Egeria already defines under EXACTLY this name, or None.
        Content-pack elements carry a qualifiedName of the form `Egeria:ValidMetadataValue:...`. The by-name
        search may match loosely, so a hit counts only when its displayName equals the name (case and
        punctuation aside). More than one such element is ambiguous: nothing is adopted and the answer is
        {"refused": "<sentence>"}. A search that could not be made (raised, other than a typed not-found, or
        gave no answer) is {"could_not_check": "<sentence>"}: never None, because None lets the caller create
        RE's own component under a DIFFERENT qualifiedName beside the pack's, which Egeria's duplicate refusal
        cannot catch. Never raises."""
        import re as _re

        def norm(t: str) -> str:
            return _re.sub(r"[^a-z0-9]", "", (t or "").lower())

        from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent

        try:
            self._ensure_connected()
            found = self._solution_architect.get_solution_components_by_name(display_name)
        except Exception as exc:
            if lookup_exception_is_absent(exc):
                return None
            words = lookup_could_not_be_made(display_name, exc)
            log.warning("content-pack lookup: %s", words)
            return {"could_not_check": words}
        if not isinstance(found, list):
            from resource_explorer.egeria_absence import ABSENT, is_absent

            if is_absent(found) == ABSENT:          # pyegeria's own miss string, exactly
                return None
            # None, {}, an unrecognised string: not an answer RE can read, so not "no pack element".
            return {"could_not_check": lookup_could_not_be_made(display_name, None if found is None else
                                                                ValueError(f"unexpected answer {found!r:.80}"))}
        hits = []
        for el in found:
            if not isinstance(el, dict):
                continue
            props = el.get("properties") or {}
            qn = props.get("qualifiedName") or el.get("qualifiedName") or ""
            name = props.get("displayName") or el.get("displayName") or ""
            guid = (el.get("elementHeader") or {}).get("guid") or el.get("guid") or ""
            if (qn.startswith(CONTENT_PACK_QN_PREFIX) and norm(name) == norm(display_name)
                    and _UUID_RE.match(guid or "")):
                hits.append({"guid": guid, "qualified_name": qn, "name": name})
        if len(hits) > 1:
            return {"refused": f"{len(hits)} content-pack elements are named {display_name!r}: "
                               f"none adopted"}
        return hits[0] if hits else None

    @staticmethod
    def _child_guids(element) -> tuple[set[str], bool]:
        """(child GUIDs, known) from a container read by GUID. `nestedSolutionComponents` is the container's
        children side of SolutionComposition (Egeria's own end definition: the other end,
        `usedInSolutionComponents`, is "the components that embed this component"); `subComponents` is the
        report-spec form pyegeria also reads. `known` is True only when at least one of the keys is
        PRESENT: an answer with neither says nothing about children, so an empty set is not "none"."""
        if not isinstance(element, dict):
            return set(), False
        keys = [k for k in ("nestedSolutionComponents", "subComponents") if k in element]
        out: set[str] = set()
        for k in keys:
            for entry in element.get(k) or []:
                if not isinstance(entry, dict):
                    continue
                header = (entry.get("relatedElement") or entry).get("elementHeader") or {}
                if header.get("guid"):
                    out.add(header["guid"])
        return out, bool(keys)

    #: One page of a container's composition read. A full page may be a cut-off list, so it says "could not tell".
    _COMPOSITION_PAGE = 1000

    def _composition_children(self, container_guid: str) -> tuple[set[str], bool]:
        """(GUIDs related to the container by `SolutionComposition`, whether the read answered), read as a
        RELATIONSHIP query (6.2: a component with no children comes back from the element read with no
        children key at all, which cannot tell "none" from "the read did not say").

        The read returns both ends, so each entry's `elementAtEnd1` is used to keep only the pairs where the
        container is end 1 (the parent); a reverse pair is never read as a child.

        Known ONLY on a typed answer: Egeria's exact "No elements found" (none), or a list of related
        elements whose GUIDs could all be read. A raised error, any other text, a list entry with no GUID,
        a missing client or a full page is "could not tell" -- never "none"."""
        from resource_explorer.egeria_absence import ABSENT, is_absent

        if self._metadata_expert is None:
            return set(), False
        try:
            # The page is asked for IN the body. Up to pyegeria 6.2.1 the page_size/start_from/starting_at_end
            # arguments were dropped (ISSUE-129); from 6.2.2 they are sent, but a field the body sets always
            # wins, so this one page of _COMPOSITION_PAGE is what Egeria gets. starting_at_end stays 0 (either
            # end) and the elementAtEnd1 filter below keeps the children, as it did before.
            res = self._metadata_expert.get_related_metadata_elements(
                container_guid, "SolutionComposition",
                {"class": "ResultsRequestBody", "startFrom": 0, "pageSize": self._COMPOSITION_PAGE})
        except Exception as exc:
            log.warning("could not read the composition of %s: %s", container_guid, type(exc).__name__)
            return set(), False
        if isinstance(res, str):
            return set(), is_absent(res) == ABSENT
        entries = res.get("elementList") if isinstance(res, dict) else res
        if not isinstance(entries, list) or len(entries) >= self._COMPOSITION_PAGE:
            return set(), False
        out: set[str] = set()
        for e in entries:
            if not isinstance(e, dict):
                return set(), False
            # Children only: the entries whose related element is NOT at end 1, i.e. the container is end 1
            # (the parent). A reverse pair (the container nested elsewhere) is not a child. An entry that does
            # not say which end it is cannot be classified: could not tell.
            end1 = e.get("elementAtEnd1")
            if not isinstance(end1, bool):
                return set(), False
            if end1:
                continue
            header = ((e.get("element") or {}).get("elementHeader") or e.get("elementHeader")
                      or (e.get("relatedElement") or {}).get("elementHeader") or {})
            guid = (e.get("element") or {}).get("elementGUID") or e.get("elementGUID") or header.get("guid") or ""
            if not guid:
                return set(), False
            out.add(guid)
        return out, True

    def sub_component_guids(self, container_guid: str) -> tuple[set[str], bool]:
        """(the container's sub-component GUIDs, whether the read said anything about them). Raises when
        Egeria gives no usable element at all: "could not read" must not look like "has none". When the
        element read carries no children key (6.2 omits it for a component with none) the container's
        SolutionComposition relationships are read explicitly instead."""
        element = self._solution_architect.get_solution_component_by_guid(container_guid)
        if not isinstance(element, dict) or not element:
            raise BlueprintMaterializationError(
                f"could not read the container {container_guid}: Egeria's answer was not an element")
        guids, known = self._child_guids(element)
        if known:
            return guids, True
        return self._composition_children(container_guid)

    def blueprint_member_guids(self, blueprint_guid: str) -> set[str] | None:
        """The GUIDs that are direct members of the blueprint, or None when the read does not carry its
        members (then nothing is claimed about them). Read only; never removes anything."""
        try:
            element = self._solution_architect.get_solution_blueprint_by_guid(blueprint_guid)
        except Exception as exc:
            log.warning("could not read the blueprint's members: %s", type(exc).__name__)
            return None
        if not isinstance(element, dict) or "collectionMembers" not in element:
            return None
        out = set()
        for entry in element.get("collectionMembers") or []:
            if isinstance(entry, dict):
                header = (entry.get("relatedElement") or entry).get("elementHeader") or {}
                if header.get("guid"):
                    out.add(header["guid"])
        return out

    def link_sub_components(self, container_guid: str, container_qn: str,
                            children: list[tuple[str, str]]) -> list[dict]:
        """`SolutionComposition` container -> each child, idempotent by the PAIR. `children` is
        [(child_guid, child_qn)]. Reads the container's sub-components BEFORE (a pair already held, by the
        content pack or an earlier run, is not rewritten: the relationship is multi-link and would
        duplicate), writes only the missing pairs, then reads AFTER. Each row is {key, container_guid,
        child_guid, status, read_back, note?, error?}:

          already_present  the before-read showed the pair (read_back True)
          linked           written, and the after-read shows it (read_back True)
          unconfirmed      written but not shown after, OR the read did not say whether children exist so
                           nothing was written (read_back False in the second case)
          unread           the before-read failed: nothing written (read_back False)
          error            the write raised (read_back False)
        """
        self._ensure_connected()
        rows = [{"key": f"SolutionComposition::{container_qn}::{child_qn}", "container_guid": container_guid,
                 "child_guid": child_guid, "status": "", "read_back": False}
                for child_guid, child_qn in children]
        try:
            before, known = self.sub_component_guids(container_guid)
        except Exception as exc:
            for r in rows:
                r.update(status="unread", error=f"{type(exc).__name__}: {exc}"[:300],
                         note="the read before the write failed: nothing was written")
            return rows
        if not known:
            for r in rows:
                r.update(status="unconfirmed", note="the read did not say whether children exist: "
                                                    "nothing was written")
            return rows
        wrote = False
        for r in rows:
            if r["child_guid"] in before:
                r.update(status="already_present", read_back=True)
                continue
            try:
                self._solution_architect.link_subcomponent(
                    container_guid, r["child_guid"], {"class": "NewRelationshipRequestBody"})
                wrote = True
                r["status"] = "pending"
            except Exception as exc:
                r.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
        if wrote:
            try:
                after, after_known = self.sub_component_guids(container_guid)
            except Exception as exc:
                after, after_known = set(), False
                for r in rows:
                    if r["status"] == "pending":
                        r["error"] = f"read after the write failed: {type(exc).__name__}"
        else:
            after, after_known = before, True
        for r in rows:
            if r["status"] == "pending":
                if after_known and r["child_guid"] in after:
                    r.update(status="linked", read_back=True)
                else:
                    r.update(status="unconfirmed", read_back=after_known)
        return rows

    def resolve_member_guids(
        self,
        registry,
        entity_type: str,
        entity_slug: str,
        member_slugs: list[str],
        slug_to_scope: dict[str, str],
    ) -> tuple[dict[str, str], list[str]]:
        """slug -> materialized SolutionComponent GUID, for members that ARE
        materialized; the second list is member slugs that are NOT (missing
        from slug_to_scope, no verdict, or no materialization) — Decision
        2's enforcement point: accepting a blueprint does not implicitly
        accept or materialize its members.

        `slug_to_scope` MUST be built the same way
        `_architecture_recovery_results` builds it (component finding's
        `detail["slug"]` -> its scope_locator) — see the plan's identity-
        mismatch warning: clustering keys members by component slug, but
        verdicts/materialization are keyed by scope_locator. Looking a slug
        up directly in `get_materialized_components()` without going
        through this map first silently finds nothing for every member.

        Never raises; an unmaterialized member is data, not an error.
        """
        resolved: dict[str, str] = {}
        unmet: list[str] = []
        for slug in member_slugs:
            scope = slug_to_scope.get(slug)
            if not scope:
                unmet.append(slug)
                continue
            row = registry.get_materialized_component(entity_type, entity_slug, scope)
            guid = row.get("guid") if row else ""
            if guid:
                resolved[slug] = guid
            else:
                unmet.append(slug)
        return resolved, unmet

    def resolve_child_blueprint_guids(
        self,
        registry,
        entity_type: str,
        entity_slug: str,
        perspective: str,
        child_names: list[str],
        verify: bool = False,
    ) -> tuple[dict[str, str], list[str]]:
        """Same shape as resolve_member_guids, for Decision 1's per-level
        acceptance: a parent blueprint's write attaches only children that
        already have their own materialized SolutionBlueprint — an
        unaccepted/unmaterialized child is reported back, not silently
        skipped or auto-materialized. Never raises.

        `verify=True` reads each cached child by GUID before it is linked: RE's cache can point at nothing
        (the 2026-10-08 wipe). A child Egeria says is GONE, or one that could not be read, is not linked and
        is in the unmet list; the two are kept apart in `child_blueprints_gone` / `child_blueprints_unreadable`
        ({name: guid} / {name: sentence}) for the caller to say. A gone child is never recreated here."""
        resolved: dict[str, str] = {}
        unmet: list[str] = []
        self.child_blueprints_gone: dict[str, str] = {}
        self.child_blueprints_unreadable: dict[str, str] = {}
        for child_name in child_names:
            row = registry.get_materialized_blueprint(entity_type, entity_slug, perspective, child_name)
            guid = row.get("guid") if row else ""
            if not guid:
                unmet.append(child_name)
                continue
            if verify:
                try:
                    self._ensure_connected()
                    present = self.blueprint_exists(guid)
                except Exception as exc:
                    self.child_blueprints_unreadable[child_name] = f"{type(exc).__name__}: {str(exc)[:160]}"
                    unmet.append(child_name)
                    continue
                if not present:
                    self.child_blueprints_gone[child_name] = guid
                    unmet.append(child_name)
                    continue
            resolved[child_name] = guid
        return resolved, unmet
