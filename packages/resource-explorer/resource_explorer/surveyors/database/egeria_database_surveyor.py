"""Egeria-based database surveyor for triggering native PostgreSQL surveys."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

if TYPE_CHECKING:
    from resource_explorer.registry import DatabaseEntity

log = logging.getLogger(__name__)

# The real registered qualifiedName for each native survey (e.g.
# "PostgreSQLSurvey::survey-postgres-database") now comes from
# config/technology_type_processes.yaml (resource_explorer.surveyors.
# technology_type_processes) instead of being hardcoded here — adding a new
# Technology Type's native survey is a config change, not a code change.
#
# Note: pyegeria's AutomatedCuration.initiate_postgres_server_survey/
# initiate_postgres_database_survey hardcode a single-colon separator
# ("PostgreSQLSurvey:survey-postgres-database") — a pyegeria bug that makes
# those two SDK methods fail with OMAG-GENERIC-HANDLERS-400-013 ("name is not
# recognized") against any correctly-registered server (real qualifiedNames
# use "::"). That's why _initiate_native_survey below calls the private
# _async_initiate_survey directly instead of those two SDK methods.
#
# Second pyegeria gap, found live 2026-09-20 while fixing the "catalog_and_
# survey never refreshes an existing element's connection" bug (docs/Backlog.md
# "TIER 1 — catalog_and_survey never refreshes..."): AutomatedCuration.
# create_postgres_server_element_from_template/create_postgres_database_
# element_from_template build their TemplateRequestBody by hand and never set
# "deepCopy": True. Confirmed live against qs-view-server: without deepCopy,
# Egeria's templated cataloguing instantiates only the anchor element (the
# server/database asset itself) and never copies the template's attached
# Connection subgraph (a VirtualConnection embedding a SecretsStoreConnection,
# plus Endpoint and ConnectorType) — which is exactly why a freshly-cataloged
# asset can still end up with "no connection" (OPEN-SURVEY-0009) once a native
# survey tries to use it. _create_postgres_element_from_template below
# bypasses the two broken wrappers the same way _initiate_native_survey
# bypasses initiate_postgres_*_survey, adding "deepCopy": True to the raw
# request body. See CATALOG-AND-SURVEY-REFRESH-FIX.md for
# the full investigation, including why this only helps *fresh* catalog runs
# and not an already-broken existing asset (Egeria's own "reuse by
# qualifiedName" match path never triggers deepCopy's child-copying at all —
# confirmed live, see that doc).


class EgeriaDatabaseSurveyorError(RuntimeError):
    """Raised when Egeria survey operations fail."""


class ArchivedDatabaseNeedsConfirmation(EgeriaDatabaseSurveyorError):
    """S19 (owner ruling 2026-10-14): Egeria holds no database under this name but an archived copy of it
    (`<qn>_archivedOn_<date>`), so a create here would re-include it as a NEW element. Nothing is created; the
    person confirms through a Curate commit (Re-include), never through a survey."""

    def __init__(self, message: str, *, state: str, archived_guid: str = "", date: str = ""):
        super().__init__(message)
        self.state = state
        self.archived_guid = archived_guid
        self.date = date


# The connector provider RE's OWN secrets-store Connection must use --
# NOT the one Egeria's own documentation page and content-pack templates
# show (YAMLSecretsStoreProvider). Confirmed live 2026-09-21, tracing Egeria's
# own server source (AutomatedCurationRESTServices.saveClientSideSecret):
# that endpoint only calls saveSecretsCollection() when the resolved
# connector is an `instanceof YAMLSecretsFileConnector`; YAMLSecretsStoreProvider
# instantiates the READ-ONLY base class YAMLSecretsStoreConnector, so the
# `instanceof` check silently fails and the endpoint returns a plain success
# VoidResponse having done nothing -- no exception, no file write, no
# indication anything was skipped. YAMLSecretsFileProvider is the sibling
# provider that instantiates YAMLSecretsFileConnector, the subclass that
# actually implements saveSecretsCollection() (creates the file if missing,
# then writes it via Jackson's YAML ObjectMapper). This distinction only
# matters for a connector RE itself WRITES through; the per-database
# SecretsStoreConnection embedded via deepCopy templates (read-only, at
# native-survey time) correctly keeps YAMLSecretsStoreProvider, matching
# Egeria's own documented pattern -- see
# https://egeria-project.org/connectors/secrets/yaml-file-secrets-store-connector/.
# See PROBES-2026-09-21.md for the full investigation.
_YAML_SECRETS_FILE_PROVIDER_CLASS = (
    "org.odpi.openmetadata.adapters.connectors.secretsstore.yaml.YAMLSecretsFileProvider"
)
_OWN_SECRETS_STORE_QUALIFIED_NAME = "Resource Explorer:SecretsStoreConnector:YAML File Connection"

#: LineageMapping's ends (OpenMetadataTypesArchive1_2.getLineageMappingRelationship): end 1 is
#: `sourceElement`, end 2 `targetElement`. `LineageLinker.link_lineage(element_one_guid=...)` is end 1
#: (`LineageHandler.linkLineage` -> `createRelatedElementsInStore(type, elementOneGUID, elementTwoGUID)`).
LINEAGE_MAPPING_TARGET_END = 2


def _secrets_collection_name(db_slug: str) -> str:
    """The per-database secrets collection name written to RE's own secrets
    store and referenced back via the "secretsCollectionName" placeholder --
    one collection per database, named for its slug so it's unambiguous which
    database a collection belongs to inside the shared file."""
    return f"{db_slug}::PostgreSQL Secret"


def _build_secrets_collection_body(collection_name: str, db_user: str, db_pwd: str) -> dict:
    """Build the SecretsCollectionRequestBody for
    AutomatedCuration.save_client_side_secret -- see that method's docstring
    for the shape this mirrors. Key names ("userId"/"clearPassword") are
    fixed by the YAML secrets store connector's own convention (confirmed
    live 2026-07-09), not a choice made here."""
    return {
        "class": "SecretsCollectionRequestBody",
        "secretsCollection": {
            "collectionName": collection_name,
            "displayName": collection_name,
            "refreshTimeInterval": 60,
            "secrets": {
                "userId": db_user,
                "clearPassword": db_pwd,
            },
        },
    }


class EgeriaDatabaseSurveyor:
    """Trigger and retrieve PostgreSQL surveys using Egeria's native capabilities.
    
    This surveyor uses Egeria's pre-built governance processes to survey databases,
    leveraging Egeria's expertise and standardized metadata.
    
    Parameters
    ----------
    platform_url : Egeria platform URL (falls back to EGERIA_PLATFORM_URL env var)
    view_server : Egeria view server name
    user_id : Egeria user id
    user_password : Egeria user password
    """

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
        self.user_id = user_id or os.getenv("EGERIA_USER", "erinoverview")
        self.user_password = user_password or ""   # not used to authenticate (Brief I)
        self._identity = identity
        self._automated_curation = None
        self._asset_maker = None
        self._discovery = None
        self._metadata_expert = None
        self._secrets_store_guid = ""

    def principal(self):
        """Who this surveyor acts as: the identity it was given, else `current_principal()` (the
        signed-in Caller on a route, the declared daemon job otherwise), resolved once."""
        if self._identity is None:
            from resource_explorer.egeria_clients import current_principal

            self._identity = current_principal()
        return self._identity

    def _clients(self):
        from resource_explorer.egeria_clients import egeria_client

        return egeria_client(self.principal(), purpose="database survey",
                             view_server=self.view_server, platform_url=self.platform_url)

    def connect(self) -> None:
        """Establish pyegeria client connections."""
        if self._automated_curation is not None:
            return
        
        if not self.platform_url:
            raise EgeriaDatabaseSurveyorError(
                "EGERIA_PLATFORM_URL is not set. "
                "Set it in .env or pass platform_url= to EgeriaDatabaseSurveyor."
            )
        
        try:
            from pyegeria import AutomatedCuration, AssetMaker
            from pyegeria.omvs.data_discovery import DataDiscovery

            clients = self._clients()
            # AutomatedCuration for triggering surveys
            self._automated_curation = clients.of(AutomatedCuration)
            # AssetMaker for finding survey reports
            self._asset_maker = clients.of(AssetMaker)
            # DataDiscovery for retrieving annotations
            self._discovery = clients.of(DataDiscovery)
        except PermissionError:
            raise
        except ImportError as exc:
            raise EgeriaDatabaseSurveyorError("pyegeria is not installed.") from exc
        except Exception as exc:
            raise EgeriaDatabaseSurveyorError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc

    def catalog_and_survey(
        self,
        db_entity: "DatabaseEntity",
        db_user: str,
        db_pwd: str,
        registry=None,
        survey_after_catalog: bool = True,
        submitted_by: str = "",
        reinclude_guid: str = "",
    ) -> dict:
        """Guarded front door — see _catalog_and_survey."""
        from resource_explorer.egeria_linkage import guard_linkage

        with guard_linkage(registry, "database", db_entity.slug,
                           db_entity.display_name or db_entity.slug,
                           db_entity.egeria_asset_guid or ""):
            return self._catalog_and_survey(
                db_entity, db_user, db_pwd, registry, survey_after_catalog,
                submitted_by=submitted_by, reinclude_guid=reinclude_guid)

    @staticmethod
    def _note_stale_guid_if_any(exc, db_entity, guid: str, registry) -> None:
        """Record a divergence found inside a deliberately non-fatal handler.

        Separate from guard_linkage because the two answer different questions.
        The guard converts a *fatal* failure into a named one; this reports a
        failure the caller has decided to continue past. Both must record it —
        the whole defect being fixed is that an unusable GUID produced no visible
        signal anywhere.
        """
        if registry is None or not guid:
            return
        from resource_explorer.egeria_linkage import is_unknown_guid_error, note_divergence

        if not is_unknown_guid_error(exc):
            return
        note_divergence(registry, "database", db_entity.slug,
                        db_entity.display_name or db_entity.slug, guid, exc)

    def _create_postgres_element_from_template(self, technology_type: str, placeholder_values: dict) -> str:
        """Create a PostgreSQL Server/Database element from Egeria's own template,
        bypassing AutomatedCuration.create_postgres_server_element_from_template/
        create_postgres_database_element_from_template — see the pyegeria-gap
        note near the top of this module for why. The only difference from what
        those two wrappers send is one extra body key: "deepCopy": True, which is
        what actually causes Egeria to also instantiate the template's attached
        Connection subgraph rather than just the bare server/database element.

        Only fixes the *fresh catalog* case. Confirmed live 2026-09-20: calling
        this again for an element that already exists by qualifiedName returns
        the SAME guid (Egeria's template engine matches and reuses it) rather
        than creating a duplicate — so this is safe to call unconditionally —
        but that reuse path does NOT re-run deepCopy's child-copying, so it
        cannot repair an existing element that is already missing its
        connection. See CATALOG-AND-SURVEY-REFRESH-FIX.md.
        """
        template_guid = self._automated_curation.get_template_guid_for_technology_type(technology_type)
        body = {
            "class": "TemplateRequestBody",
            "templateGUID": template_guid,
            "isOwnAnchor": True,
            "deepCopy": True,
            "placeholderPropertyValues": placeholder_values,
        }
        return self._automated_curation.create_elem_from_template(body)

    def _ensure_own_secrets_store_guid(self) -> str:
        """Find or create RE's own YAML-file SecretsStore Asset, and cache its
        GUID for the lifetime of this surveyor instance.

        Mirrors _create_postgres_element_from_template's find-by-qualifiedName
        -then-create idiom, but this is a plain Asset/Connection/Endpoint/
        ConnectorType graph (ConnectionMaker), not a template instantiation --
        Egeria ships no reusable template for "a client's own secrets store,"
        and there is exactly one of these per RE deployment, so hand-building
        the four elements once is simpler than inventing one.

        **Returns an Asset guid, not the Connection's.** Confirmed live
        2026-09-21: `AutomatedCuration.save_client_side_secret` ultimately
        calls Egeria's `ConnectedAssetClient.getConnectorForAsset`, which
        requires an Asset (it resolves the Asset's attached Connection
        itself) -- passing the bare Connection's own guid fails with
        `OMAG-REPOSITORY-HANDLER-404-001 ... retrieved an object ... of type
        Connection rather than type Asset`. The "secrets store asset" language
        in Egeria's own client-side-secret docs and this method's docstring
        is literal, not loose.

        **Every relationship link below passes an explicit body.** Also
        confirmed live: `ConnectionMaker.link_connection_connector_type`/
        `link_connection_endpoint`/`link_asset_to_connection` all default
        `body=None`, and passing that default does not raise -- it silently
        creates no relationship at all. The first version of this method hit
        exactly this: `save_client_side_secret` later failed with `Null
        connectorType property passed in connection`, and separately with a
        generic 400 on the asset link, because none of the three links had
        actually been made despite no earlier call reporting an error.

        An explicit EGERIA_SECRETS_STORE_GUID always wins, for a deployment
        that already has one it wants reused (e.g. shared across RE and
        another tool) rather than one RE creates for itself.
        """
        if self._secrets_store_guid:
            return self._secrets_store_guid

        from resource_explorer.config import get_config

        cfg = get_config().egeria
        if cfg.secrets_store_guid:
            self._secrets_store_guid = cfg.secrets_store_guid
            return self._secrets_store_guid

        asset_qualified_name = f"{_OWN_SECRETS_STORE_QUALIFIED_NAME}::Asset"
        existing = self._find_element_guid(asset_qualified_name)
        if existing:
            self._secrets_store_guid = existing
            return existing

        from pyegeria import ConnectionMaker

        maker = self._clients().of(ConnectionMaker)
        relationship_body = {"class": "NewRelationshipRequestBody"}

        # Find-or-create each sub-element individually, not just the final
        # Asset. Confirmed live 2026-09-21: after an Egeria wipe-and-redeploy,
        # the ConnectorType from this method's own graph was found to already
        # exist while the Asset did not -- gating creation on the Asset alone
        # made the whole method blow up with a 409 duplicate-qualifiedName on
        # the very first create call instead of finding and reusing what was
        # already there. A partial graph (any subset of these four elements
        # already present) must not be fatal.
        connector_type_qn = f"{_OWN_SECRETS_STORE_QUALIFIED_NAME}::ConnectorType"
        connector_type_guid = self._find_element_guid(connector_type_qn)
        if not connector_type_guid:
            connector_type_guid = maker.create_connector_type({
                "class": "NewElementRequestBody",
                "isOwnAnchor": True,
                "properties": {
                    "class": "ConnectorTypeProperties",
                    "qualifiedName": connector_type_qn,
                    "displayName": "Resource Explorer YAML secrets store connector type",
                    "connectorProviderClassName": _YAML_SECRETS_FILE_PROVIDER_CLASS,
                },
            })

        endpoint_qn = f"{_OWN_SECRETS_STORE_QUALIFIED_NAME}::Endpoint"
        endpoint_guid = self._find_element_guid(endpoint_qn)
        if not endpoint_guid:
            endpoint_guid = maker.create_endpoint({
                "class": "NewElementRequestBody",
                "isOwnAnchor": True,
                "properties": {
                    "class": "EndpointProperties",
                    "qualifiedName": endpoint_qn,
                    "displayName": "Resource Explorer secrets store endpoint",
                    "networkAddress": cfg.secrets_store_path_name,
                },
            })

        connection_guid = self._find_element_guid(_OWN_SECRETS_STORE_QUALIFIED_NAME)
        if not connection_guid:
            connection_guid = maker.create_connection({
                "class": "NewElementRequestBody",
                "isOwnAnchor": True,
                "properties": {
                    "class": "ConnectionProperties",
                    "qualifiedName": _OWN_SECRETS_STORE_QUALIFIED_NAME,
                    "displayName": "Resource Explorer secrets store connection",
                    # The OCF secrets-store connector framework's own start()
                    # requires SOME non-null secretsCollectionName configuration
                    # property before it will initialize at all (confirmed live
                    # 2026-09-21: "OCF-CONNECTOR-400-009 ... secretsCollectionName
                    # was not supplied" otherwise). YAMLSecretsFileConnector's own
                    # start() override immediately nulls this back out after the
                    # framework's check passes -- every real save call supplies
                    # its own collectionName as a method argument -- so this
                    # value is never actually read; it exists purely to satisfy
                    # that startup validation.
                    "configurationProperties": {"secretsCollectionName": "resource-explorer-admin"},
                },
            })

        # Link unconditionally, not only on fresh-create. Confirmed live
        # 2026-09-21: a found-but-reused Connection can be a partial leftover
        # that was never linked to its ConnectorType/Endpoint (this is exactly
        # what happened after a wipe-and-redeploy left this graph's Connection
        # present but unlinked) -- gating the link calls on "just created"
        # reproduces the same "reuse never repairs" defect this whole method
        # exists to avoid. Both link_* calls are safe to repeat: re-linking an
        # already-correctly-linked pair is a live-verified no-op-on-success,
        # not a duplicate or an error.
        maker.link_connection_connector_type(connection_guid, connector_type_guid, body=relationship_body)
        maker.link_connection_endpoint(connection_guid, endpoint_guid, body=relationship_body)

        asset_guid = maker.create_asset(asset_type=["AssetProperties"], body={
            "class": "NewElementRequestBody",
            "isOwnAnchor": True,
            "properties": {
                "class": "AssetProperties",
                "qualifiedName": asset_qualified_name,
                "displayName": "Resource Explorer secrets store",
                "description": (
                    "Wraps RE's own YAML secrets store Connection so "
                    "AutomatedCuration.save_client_side_secret -- which "
                    "resolves a connector from an Asset, not a bare "
                    "Connection -- can reach it."
                ),
            },
        })
        maker.link_asset_to_connection(asset_guid, connection_guid, body=relationship_body)

        log.info(f"Created Resource Explorer's own SecretsStore asset: {asset_guid}")
        self._secrets_store_guid = asset_guid
        return asset_guid

    def _save_database_secret(self, db_slug: str, db_user: str, db_pwd: str) -> tuple[str, str]:
        """Write this database's credentials into RE's own secrets store as a
        named collection, and return the two placeholder values
        ("secretsCollectionName", "secretsStorePathName") the PostgreSQL
        template's embedded SecretsStoreConnection needs bound -- see
        PROBES-2026-09-21.md for why both were previously
        left as Egeria's own unsubstituted template placeholders.

        Non-fatal by design, matching this method's siblings in
        _catalog_and_survey (server/database survey initiation): a database
        can still be cataloged and locally scanned without a working native
        survey, and a secrets-write failure must not block that. Returns
        ("", "") on failure so the caller omits both placeholders rather than
        passing empty strings Egeria would still try to bind.
        """
        from resource_explorer.config import get_config

        collection_name = _secrets_collection_name(db_slug)
        try:
            secrets_store_guid = self._ensure_own_secrets_store_guid()
            body = _build_secrets_collection_body(collection_name, db_user, db_pwd)
            self._automated_curation.save_client_side_secret(secrets_store_guid, body)
        except Exception as exc:
            log.warning(f"Could not save secrets for database {db_slug!r} (non-fatal): {exc}")
            return "", ""
        return collection_name, get_config().egeria.secrets_store_path_name

    def _warn_if_database_has_no_connection(self, db_entity: "DatabaseEntity", server_name: str, db_guid: str) -> None:
        """Non-fatal visibility check: does this database element actually have
        a Connection attached?

        Not a fix — deliberately. Repairing an already-broken element (one
        cataloged before this module added deepCopy, or one Egeria's own
        by-qualifiedName reuse path skipped past) needs delete-and-recatalog,
        which changes the asset's GUID and orphans its existing Survey Reports/
        annotations. That is a real, bigger decision than this bug fix's scope —
        see CATALOG-AND-SURVEY-REFRESH-FIX.md and the Backlog
        entry — so it is not automated here. This only makes the absence
        visible (at WARNING level) instead of letting it surface later as an
        opaque OPEN-SURVEY-0009 from the native survey engine, the same
        "non-fatal must not mean invisible" principle _note_stale_guid_if_any
        already applies to stale GUIDs.

        The qualifiedName pattern checked (f"{qualifiedName}::Connection")
        matches what Egeria's own PostgreSQL templates use for their attached
        Connection template today (confirmed live) — a heuristic tied to this
        content pack's naming convention, not a guaranteed-stable Egeria API.
        """
        from resource_explorer.catalogue_gateway import database_qualified_name

        qualified_name = database_qualified_name(server_name, db_entity.database_name)
        connection_qn = f"{qualified_name}::Connection"
        try:
            has_connection = bool(self._find_by_qualified_name(connection_qn))
        except Exception as exc:
            log.debug(f"Could not check for a Connection on {qualified_name!r} (non-fatal): {exc}")
            return
        if not has_connection:
            log.warning(
                f"Database element {qualified_name!r} (guid={db_guid}) has no Connection "
                "attached — a native Egeria survey against it will fail with "
                "OPEN-SURVEY-0009. This element was cataloged before this fix, or via "
                "Egeria's by-qualifiedName reuse path, which does not create one. Fixing "
                "it requires delete-and-recatalog (changes the GUID, orphans existing "
                "Survey Reports/annotations) — see the design note "
                "CATALOG-AND-SURVEY-REFRESH-FIX.md."
            )

    def _catalog_and_survey(
        self,
        db_entity: "DatabaseEntity",
        db_user: str,
        db_pwd: str,
        registry=None,
        survey_after_catalog: bool = True,
        submitted_by: str = "",
        reinclude_guid: str = "",
    ) -> dict:
        """Catalog the PostgreSQL server + database in Egeria, then optionally initiate a native survey.

        S19 (owner ruling 2026-10-14): when no database holds its name, its archived copy is looked for FIRST,
        before anything is written. One there and `reinclude_guid` not naming it (the newest copy, confirmed in a
        Curate commit): `ArchivedDatabaseNeedsConfirmation`, nothing created. A lookup that cannot be made:
        `EgeriaDatabaseSurveyorError`, nothing created.

        Egeria's template-based creation stores the connection details (including
        credentials) so that subsequent surveys can be initiated without supplying
        credentials again — for a *freshly-cataloged* element. See
        CATALOG-AND-SURVEY-REFRESH-FIX.md for what this does and
        does not fix for an already-cataloged element.

        Returns dict with keys: server_guid, database_guid, survey_action_guid,
        server_survey_guid and survey_submissions.

        `survey_submissions` lists ONLY the surveys whose engine-action GUID
        came back AND whose proof row was persisted (see _record_survey_submission);
        callers must derive any "survey started" claim from it, never from the
        branch that ran.
        """
        self.connect()
        survey_submissions: list[dict] = []

        # Use egeria_host if set (e.g. host.docker.internal when Egeria runs in Docker);
        # fall back to the locally-visible host.
        from resource_explorer.catalogue_gateway import (
            database_qualified_name, server_name_for, server_qualified_name)

        egeria_host = getattr(db_entity, "egeria_host", "") or db_entity.host
        server_name = server_name_for(db_entity)

        # ── 1. Create / find PostgreSQL Server element ─────────────────────────
        # Adoption is by the FULL qualifiedName, never a bare name (Brief L, backlog 7f.7): a bare
        # `sales` matched host A's database when host B's `sales` was registered, and host B adopted it.
        # The names are the catalogue gateway's (one definition): `PostgreSQL Server::<host:port>` and
        # `PostgreSQL Relational Database::<host:port>::<db>`.
        # The server is ALWAYS found by its own qualifiedName. `egeria_asset_guid` is the DATABASE's guid in
        # every current flow (`set_database_egeria_guid`), so reusing it as the server printed the database
        # as the server (rehearsal 2, D-E) — and when the database lookup misses (e.g. `egeria_host`
        # changed) no comparison can tell, so the stored guid is never a server candidate at all (Brief L
        # round 2). NOTE a server element is SHARED by host:port: a commit attaches to whatever server
        # already carries that qualifiedName.
        db_guid_lookup = self._find_by_qualified_name(
            database_qualified_name(server_name, db_entity.database_name))
        server_guid = self._find_by_qualified_name(server_qualified_name(server_name))
        if server_guid and server_guid == db_guid_lookup:
            server_guid = ""
        if not db_guid_lookup:
            self._refuse_unconfirmed_reinclude(database_qualified_name(server_name, db_entity.database_name),
                                               reinclude_guid)

        # Whether a secrets write is needed at all: reusing both existing elements by name is
        # the common re-survey path, and a reused element's deepCopy already
        # ran (or didn't) long before this call, so writing a fresh secret for
        # it here would be a wasted live write, not a fix -- see
        # _create_postgres_element_from_template's own docstring on why reuse
        # never re-runs deepCopy.
        secret_placeholders: dict = {}
        if not server_guid or not db_guid_lookup:
            secrets_collection_name, secrets_store_path = self._save_database_secret(
                db_entity.slug, db_user, db_pwd
            )
            if secrets_collection_name:
                secret_placeholders = {
                    "secretsCollectionName": secrets_collection_name,
                    "secretsStorePathName": secrets_store_path,
                }

        if not server_guid:
            try:
                server_guid = self._create_postgres_element_from_template(
                    "PostgreSQL Server",
                    {
                        "serverName": server_name,
                        "hostIdentifier": egeria_host,
                        "portNumber": str(db_entity.port),
                        "databaseUserId": db_user,
                        # The server's OWN description: the rehearsal (2026-10-05) found the
                        # server element carrying the DATABASE's description.
                        "description": f"PostgreSQL server at {egeria_host}:{db_entity.port}",
                        # A second placeholder nothing supplied: it stayed as a literal
                        # `~{versionIdentifier}~` on the server element.
                        "versionIdentifier": "not recorded",
                        "databasePassword": db_pwd,
                        **secret_placeholders,
                    },
                )
                log.info(f"Created PostgreSQL server element: {server_guid}")
            except Exception as exc:
                raise EgeriaDatabaseSurveyorError(
                    f"Could not create PostgreSQL server element for {server_name}: {exc}"
                ) from exc

        # ── 2. Create / find PostgreSQL Database element ───────────────────────
        db_guid = db_guid_lookup

        if not db_guid:
            try:
                db_guid = self._create_postgres_element_from_template(
                    "PostgreSQL Relational Database",
                    {
                        "databaseName": db_entity.database_name,
                        "serverName": server_name,
                        "hostIdentifier": egeria_host,
                        "portNumber": str(db_entity.port),
                        "databaseUserId": db_user,
                        "description": db_entity.description or f"PostgreSQL database {db_entity.database_name}",
                        # The template's description placeholder is
                        # `databaseDescription`, not `description` (scratch test
                        # 2, 2026-10-05), and `versionIdentifier` is a second one
                        # nothing supplied: with `description` alone both stayed
                        # as literal ~{...}~ strings on the element. Supplied
                        # here so none remains; the version is "not recorded"
                        # because RE keeps no PostgreSQL version for the database.
                        "databaseDescription": db_entity.description or f"PostgreSQL database {db_entity.database_name}",
                        "versionIdentifier": "not recorded",
                        "databasePassword": db_pwd,
                        **secret_placeholders,
                    },
                )
                log.info(f"Created PostgreSQL database element: {db_guid}")
            except Exception as exc:
                raise EgeriaDatabaseSurveyorError(
                    f"Could not create PostgreSQL database element for {db_entity.database_name}: {exc}"
                ) from exc
        elif db_guid:
            # Found by name rather than just created — this is exactly the case
            # that can't be repaired by this fix (see docstring above).
            self._warn_if_database_has_no_connection(db_entity, server_name, db_guid)

        # Persist database GUID to registry
        if registry and db_guid:
            registry.set_database_egeria_guid(db_entity.slug, db_guid)

        # ── 3. Optionally initiate Egeria's native surveys ────────────────────
        server_survey_guid = ""
        survey_action_guid = ""
        if survey_after_catalog:
            # Survey the server first (captures connection info, database list, server config)
            if server_guid:
                try:
                    self._last_survey_process_qn = ""
                    server_survey_guid = self._initiate_survey("PostgreSQL Server", server_guid)
                    log.info(f"Egeria server survey initiated: {server_survey_guid}")
                    self._record_survey_submission(
                        registry, db_entity, "PostgreSQL Server", server_survey_guid,
                        submitted_by, survey_submissions)
                except Exception as exc:
                    log.warning(f"Server survey initiation failed (non-fatal): {exc}")
                    # This is where a stale cached GUID actually surfaces, and it
                    # was being thrown away at WARNING level. Confirmed live
                    # 2026-08-20: pointing this at a GUID Egeria cannot have
                    # returns OMAG-REPOSITORY-HANDLER-404-007 here, and the
                    # method still returned success with server_survey_guid=''.
                    # The catalog work above genuinely did succeed, so this stays
                    # non-fatal — but "non-fatal" must not mean "invisible", or
                    # every subsequent run silently skips the server survey too.
                    self._note_stale_guid_if_any(exc, db_entity, server_guid, registry)

            # Survey the database (captures schemas, tables, columns, relationships)
            if db_guid:
                try:
                    self._last_survey_process_qn = ""
                    survey_action_guid = self._initiate_survey("PostgreSQL Relational Database", db_guid)
                    log.info(f"Egeria database survey initiated: {survey_action_guid}")
                    self._record_survey_submission(
                        registry, db_entity, "PostgreSQL Relational Database",
                        survey_action_guid, submitted_by, survey_submissions)
                except Exception as exc:
                    log.warning(f"Cataloged OK but Egeria database survey initiation failed: {exc}")
                    self._note_stale_guid_if_any(exc, db_entity, db_guid, registry)

        return {
            "server_guid": server_guid,
            "server_survey_guid": server_survey_guid,
            "database_guid": db_guid,
            "survey_action_guid": survey_action_guid,
            "survey_submissions": survey_submissions,
        }

    _last_survey_process_qn: str = ""
    _last_survey_started_as: str = ""

    def _record_survey_submission(
        self, registry, db_entity, tech_type: str, engine_action_guid: str,
        submitted_by: str, out: list,
    ) -> None:
        """Persist the proof row for a survey Publish just initiated, through the
        SAME recorder a native-survey run uses (registry.record_native_survey_submission),
        so the read-back sweep and the native-survey list see it.

        Records nothing when there is no registry or no engine-action GUID
        (an empty/None return is not proof Egeria accepted anything). A
        recording failure is logged loudly and the survey is then NOT reported
        as started: the claim must match a row that exists.

        Arguments: entity_type 'database' and slug from the entity; the process
        qualified name is the one _initiate_survey actually used (a discovered
        Survey Definition process, else the configured survey_existing native
        process for the technology type); surveyed_at is the submission time (UTC,
        naive ISO, the registry's spelling); submitted_by is passed in, never read
        from a ContextVar here.
        """
        guid = (engine_action_guid or "").strip() if isinstance(engine_action_guid, str) else ""
        if registry is None or not guid:
            return
        process_qn = self._last_survey_process_qn
        if not process_qn:
            from resource_explorer.surveyors.technology_type_processes import (
                KIND_SURVEY_EXISTING, get_process_by_kind)
            native = get_process_by_kind("database", tech_type, KIND_SURVEY_EXISTING)
            process_qn = native.qualified_name if native else ""
        if not process_qn:
            log.error(f"Survey {guid} was initiated for {tech_type} but no process name is "
                      "known to record it under; no proof row written.")
            return
        from datetime import timezone
        try:
            registry.record_native_survey_submission(
                "database", db_entity.slug, process_qn,
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                engine_action_guid=guid, submitted_by=submitted_by or "",
                started_as=self._last_survey_started_as)
        except Exception as exc:  # noqa: BLE001
            log.error(f"Survey {guid} was initiated but its proof row could not be "
                      f"recorded: {exc}")
            return
        out.append({"technology_type": tech_type, "process_qualified_name": process_qn,
                    "engine_action_guid": guid})

    def trigger_survey_by_guid(self, db_guid: str, start_time: datetime | None = None) -> str:
        """Initiate Egeria's native PostgreSQL database survey using a stored GUID.

        Use this when the database is already cataloged in Egeria (db_guid known).
        Egeria uses its stored connection details — no credentials required here.

        start_time: when set, Egeria's own engine host defers execution to that
        moment (pyegeria converts it to epoch-millis on the wire) instead of
        firing immediately — this is what lets scheduler.py hand off precise
        timing to Egeria instead of relying purely on RE's own poll loop. Only
        honored on the dynamically-discovered-process branch of
        _initiate_survey; the native-survey fallback (_initiate_native_survey,
        already a workaround for a separate pyegeria bug — see its own
        docstring) calls a private pyegeria method with no start_time
        parameter at all, so start_time is silently not applied if that
        fallback is the one that ends up running.
        """
        self.connect()
        try:
            action_guid = self._initiate_survey("PostgreSQL Relational Database", db_guid, start_time=start_time)
            log.info(f"Egeria database survey initiated: {action_guid}")
            return action_guid
        except Exception as exc:
            raise EgeriaDatabaseSurveyorError(
                f"Failed to initiate Egeria survey for db_guid={db_guid}: {exc}"
            ) from exc

    def _find_survey_process_name(self, tech_type: str) -> str | None:
        """Query get_tech_type_detail for a survey process and return its qualifiedName.

        Delegates to SurveyDefinitionReader.find_candidate_process_guids, which
        generalizes this same lookup to return *all* matching candidates (used by
        the Survey Definition executor to detect ambiguity) — this method keeps its
        original single-result contract for backward compatibility with
        catalog_and_survey/_initiate_survey.
        """
        from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

        self.connect()
        reader = SurveyDefinitionReader(
            self.platform_url, self.view_server, identity=self.principal()
        )
        reader._automated_curation = self._automated_curation
        candidates = reader.find_candidate_process_guids(tech_type)
        if candidates:
            qn = candidates[0]["qualified_name"]
            log.info(f"Dynamically discovered Egeria survey process for {tech_type}: {qn}")
            return qn
        return None

    def publish_step_annotations(
        self,
        db_entity: "DatabaseEntity",
        schema_info: dict,
        statistics: dict | None,
        surveyed_at: str,
        registry=None,
        views: list | None = None,
        operations: dict | None = None,
        credential_capability: dict | None = None,
    ) -> dict:
        """Publish RE-computed annotations to Egeria WITHOUT cataloging the database
        or triggering Egeria's own native survey as a side effect.

        Narrower counterpart to publish_local_survey: that method also calls
        catalog_and_survey(survey_after_catalog=True), which re-catalogs the
        database and fires Egeria's native async PostgreSQL survey every time —
        not appropriate when a Survey Definition has already explicitly declared
        this step runs in RE. The database must already be cataloged in Egeria;
        this method does not auto-catalog it.
        """
        from resource_explorer.catalogue_gateway import database_qualified_name, server_name_for

        # Interim (2026-10-13 review): no private (born-private) publish exists for databases yet, so a database in a
        # private investigation is refused here, before anything is written, recorded once per state.
        from resource_explorer.private_publish import refuse_unsupported_private

        refuse_unsupported_private(registry, "database", db_entity.slug,
                                   name=getattr(db_entity, "display_name", "") or db_entity.slug)
        self.connect()
        expected_qn = database_qualified_name(server_name_for(db_entity), db_entity.database_name)
        db_guid = self._find_by_qualified_name(expected_qn)
        if not db_guid:
            raise EgeriaDatabaseSurveyorError(self._not_catalogued_sentence(db_entity, expected_qn))

        annotations = build_step_annotations(
            db_entity, registry, schema_info=schema_info, statistics=statistics, views=views,
            operations=operations, credential_capability=credential_capability)

        qualified_name = f"SurveyReport::PostgreSQL::{db_entity.slug}::{surveyed_at}"
        body = {
            "class": "NewElementRequestBody",
            "parentGUID": db_guid,
            "parentRelationshipTypeName": "ReportSubject",
            "properties": {
                "class": "AssetProperties",
                "typeName": "SurveyReport",
                "qualifiedName": qualified_name,
                "displayName": f"Survey: {db_entity.display_name}",
                "description": (
                    f"Survey Definition step results for "
                    f"{db_entity.host}:{db_entity.port}/{db_entity.database_name} "
                    f"run at {surveyed_at}."
                ),
                "additionalProperties": {
                    "annotation_count": str(len(annotations)),
                },
            },
        }
        report_guid = self._asset_maker.create_asset(body=body)
        log.info(f"Published SurveyReport in Egeria: {report_guid}")
        made = self._create_annotations(annotations, report_guid, db_entity.slug, surveyed_at)

        # Publish column-level lineage mapping relationships to Egeria
        self.lineage_not_checked = []
        if db_guid and views:
            try:
                self._publish_column_lineage(db_entity, views)
            except Exception as lin_err:
                log.warning(f"Could not publish column lineage: {lin_err}")

        # A publish measures nothing, so it NEVER writes a database_surveys
        # row (false-zero hotfix, 2026-09-30): the caller records the outcome
        # on the measured row it published, via
        # registry.record_database_survey_published().

        return {"asset_guid": db_guid, "report_guid": report_guid,
                # RE's own count (what it built and sent), as everywhere; Egeria's is `annotations_in_egeria`:
                # what Egeria returned a GUID for, None when RE cannot tell.
                "annotation_count": len(annotations),
                "annotations_in_egeria": sum(1 for g in made if g) if isinstance(made, list) else None,
                # Column names whose look-up could not be made, and "source -> target" pairs whose
                # existing LineageMappings could not be read: their lineage was not linked.
                "lineage_not_checked": list(self.lineage_not_checked)}

    def _initiate_survey(self, tech_type: str, target_guid: str, start_time: datetime | None = None) -> str:
        """Dynamically find a user-authored Survey Definition process for tech_type
        and initiate it as a GovernanceActionProcess; if none is found (or it fails
        to initiate), fall back to the native survey GovernanceActionType
        configured for this tech_type in config/technology_type_processes.yaml.

        start_time: passed straight through to initiate_gov_action_process on
        the dynamically-discovered-process branch only — see
        trigger_survey_by_guid's docstring for why the fallback branch can't
        honor it.
        """
        process_name = self._find_survey_process_name(tech_type)
        if process_name:
            try:
                targets = [
                    {
                        "class": "NewActionTarget",
                        "actionTargetName": "serverToSurvey",
                        "actionTargetGUID": target_guid,
                    }
                ]
                guid = self._automated_curation.initiate_gov_action_process(
                    action_type_qualified_name=process_name,
                    action_targets=targets,
                    start_time=start_time,
                )
                self._last_survey_process_qn = process_name
                self._last_survey_started_as = "process"
                log.info(f"Initiated dynamically discovered survey process {process_name} on {target_guid}: {guid}"
                         + (f" (start_time={start_time.isoformat()})" if start_time else ""))
                return guid
            except Exception as exc:
                log.warning(f"Failed to initiate dynamically discovered survey process {process_name}: {exc}. Trying fallback...")

        from resource_explorer.surveyors.technology_type_processes import (
            KIND_SURVEY_EXISTING,
            get_process_by_kind,
        )

        native = get_process_by_kind("database", tech_type, KIND_SURVEY_EXISTING)
        if not native:
            raise RuntimeError(
                f"No native survey process configured for technology_type={tech_type!r} — "
                "add an entry to config/technology_type_processes.yaml."
            )
        if start_time:
            log.info(
                f"start_time={start_time.isoformat()} requested but the native-survey fallback path "
                f"(pyegeria's private _async_initiate_survey) has no start_time parameter — firing immediately."
            )
        guid = self._initiate_native_survey(native.qualified_name, target_guid)
        self._last_survey_process_qn = native.qualified_name
        self._last_survey_started_as = "gov_action_type"
        return guid

    def _initiate_native_survey(self, survey_qualified_name: str, target_guid: str) -> str:
        """Call pyegeria's private _async_initiate_survey directly with a correct
        qualifiedName, bypassing AutomatedCuration.initiate_postgres_server_survey/
        initiate_postgres_database_survey — both hardcode a single-colon separator
        ("PostgreSQLSurvey:survey-postgres-database") that does not match the real
        double-colon qualifiedName Egeria actually registers
        ("PostgreSQLSurvey::survey-postgres-database"), causing
        OMAG-GENERIC-HANDLERS-400-013 "name is not recognized". This is a pyegeria
        bug — remove this workaround once it's fixed upstream.
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            self._automated_curation._async_initiate_survey(survey_qualified_name, target_guid)
        )

    _UUID_RE = __import__('re').compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        __import__('re').IGNORECASE,
    )

    def _find_element_guid(self, name: str) -> str:
        """Return the GUID of an existing Egeria element by display/qualified name, or '' if not found.

        '' is the callers' cue to *create* the element, so only a real miss returns it: pyegeria's
        "No elements found", or a typed not-found (`egeria_absence.is_absent`). Anything else that
        raised (unreachable, 401/403, 500, timeout, or "Multiple elements found for supplied name!")
        could not be checked, and raises `EgeriaDatabaseSurveyorError` saying so: nothing is created on
        it. This used to log "treating as absent" and return '', with only Egeria's duplicate-
        qualifiedName refusal between a failed look-up and a duplicate (AUDIT-PYEGERIA-6.2.2.md).
        """
        from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent

        try:
            result = self._automated_curation.get_guid_for_name(name)
        except Exception as exc:
            if lookup_exception_is_absent(exc):
                return ""
            raise EgeriaDatabaseSurveyorError(lookup_could_not_be_made(name, exc)) from exc
        return self._guid_from_lookup(result, name)

    def _not_catalogued_sentence(self, db_entity, expected_qn: str) -> str:
        """Why an exact-qualifiedName lookup missed, in words a person can act on. When RE holds a
        guid for this database from an earlier catalogue, that element's own qualifiedName is read:
        a different host string there is a host change, and the sentence names both hosts and the
        fix. Adoption stays exact-match (7f.7); this only explains the miss."""
        from resource_explorer.catalogue_gateway import parse_element_answer, server_name_for

        base = (f"Database '{db_entity.database_name}' is not yet published to Egeria under "
                f"{expected_qn!r} — cannot publish a Survey Definition step's results without an "
                "existing asset to attach the SurveyReport to.")
        stored = (db_entity.egeria_asset_guid or "").strip()
        if not stored:
            return base
        try:
            found = parse_element_answer(self._asset_maker.get_asset_by_guid(stored, output_format="JSON"))
        except Exception as exc:
            raise EgeriaDatabaseSurveyorError(
                f"{base} RE's stored element {stored} could not be read to say why: "
                f"{' '.join(str(exc).split())[:200] or type(exc).__name__}") from exc
        prefix = "PostgreSQL Relational Database::"
        qn = found.qualified_name if found is not None else ""
        if qn.startswith(prefix) and qn.endswith(f"::{db_entity.database_name}"):
            held = qn[len(prefix):-(len(db_entity.database_name) + 2)]
            ours = server_name_for(db_entity)
            if held != ours:
                return (f"The database element in Egeria ({stored}) is named for host {held}; this "
                        f"registration says host {ours}. Re-publish the database, or correct the "
                        "host (Egeria host) on the registration.")
        return base

    def _guid_from_lookup(self, result, name: str = "") -> str:
        """Found, a real miss (""), or could-not-check (raises `EgeriaDatabaseSurveyorError`, a RuntimeError):
        the shared `egeria_absence.guid_from_answer`. None (up to 6.2.1 a swallowed failure, ISSUE-134) and any
        odd answer used to read as "" and create."""
        from resource_explorer.egeria_absence import guid_from_answer

        return guid_from_answer(result, name or "(by-name look-up)", EgeriaDatabaseSurveyorError)

    def _metadata_expert_client(self):
        if self._metadata_expert is None:
            from pyegeria import MetadataExpert
            self._metadata_expert = self._clients().of(MetadataExpert)
        return self._metadata_expert

    def _refuse_unconfirmed_reinclude(self, qualified_name: str, reinclude_guid: str = "") -> None:
        """S19: raise unless a create under `qualified_name` re-includes nothing, or re-includes exactly the newest
        archived copy the person confirmed. The decision is `catalogue_gateway.decide_reinclude`, the one the
        catalogue commit uses."""
        from resource_explorer.catalogue_gateway import (
            NEEDS_CONFIRMATION, SIBLING_UNREADABLE, decide_reinclude, find_archived_siblings)
        try:
            sibs = find_archived_siblings(self._metadata_expert_client(), qualified_name)
        except Exception as exc:  # noqa: BLE001 - every failure is "could not check": nothing is created
            raise EgeriaDatabaseSurveyorError(
                f"{SIBLING_UNREADABLE} for {qualified_name!r}: nothing created "
                f"({' '.join(str(exc).split())[:200] or type(exc).__name__})") from exc
        got = decide_reinclude(None, sibs, reinclude_guid)
        if got["state"] in (NEEDS_CONFIRMATION, "changed"):
            sib = got["sibling"]
            tail = " · confirm Re-include in Curate › Contents" if got["state"] == NEEDS_CONFIRMATION else ""
            raise ArchivedDatabaseNeedsConfirmation(
                f"the database is {got['text']}{tail} · nothing created", state=got["state"],
                archived_guid=sib.guid, date=sib.date)

    def _find_by_qualified_name(self, qualified_name: str) -> str:
        """The GUID of the element whose qualifiedName is EXACTLY `qualified_name`, or '' (not found).

        Searches the qualifiedName property only (never displayName/resourceName, which carry a
        bare database name shared across hosts). Used for every adoption of an existing server or
        database (Brief L, 7f.7). A lookup that FAILS is not "absent": it raises
        `EgeriaDatabaseSurveyorError` rather than letting the caller create a duplicate.
        """
        try:
            result = self._automated_curation.get_guid_for_name(
                qualified_name, property_name=["qualifiedName"])
        except Exception as exc:
            raise EgeriaDatabaseSurveyorError(
                f"Egeria qualifiedName lookup for {qualified_name!r} failed, so RE cannot tell whether "
                f"it exists: {' '.join(str(exc).split())[:300] or type(exc).__name__}") from exc
        return self._guid_from_lookup(result, qualified_name)

    def check_survey_exists(self, db_slug: str) -> bool:
        """Check if any survey reports exist for this database in Egeria.
        
        Args:
            db_slug: Database slug to check
            
        Returns:
            True if survey reports exist, False otherwise
        """
        self.connect()
        
        search_prefix = f"SurveyReport::PostgreSQL::{db_slug}::"
        
        try:
            results = self._asset_maker.find_assets(
                search_string=search_prefix,
                starts_with=True,
                ignore_case=False,
                output_format="JSON",
            )
            return isinstance(results, list) and len(results) > 0
        except Exception as exc:
            log.debug(f"check_survey_exists failed for {db_slug}: {exc}")
            return False

    def get_survey_reports(self, db_slug: str) -> list[dict]:
        """Retrieve all survey reports for a database from Egeria.
        
        Args:
            db_slug: Database slug
            
        Returns:
            List of survey report dicts with keys:
            - guid: Survey report GUID
            - qualified_name: Full qualified name
            - display_name: Display name
            - surveyed_at: Timestamp from qualified name
            - annotation_count: Number of annotations
            - description: Report description
        """
        self.connect()
        
        search_prefix = f"SurveyReport::PostgreSQL::{db_slug}::"
        reports = []
        
        try:
            results = self._asset_maker.find_assets(
                search_string=search_prefix,
                starts_with=True,
                ignore_case=False,
                output_format="JSON",
            )
            
            if not isinstance(results, list):
                return []
            
            for el in results:
                header = el.get("elementHeader", {})
                props = el.get("properties", {})
                additional = props.get("additionalProperties", {})
                qn = props.get("qualifiedName", "")
                
                # Extract timestamp from qualifiedName: SurveyReport::PostgreSQL::{slug}::{ts}
                ts = qn.split("::")[-1] if "::" in qn else ""
                
                reports.append({
                    "guid": header.get("guid", ""),
                    "qualified_name": qn,
                    "display_name": props.get("displayName", ""),
                    "surveyed_at": ts,
                    "annotation_count": self._safe_int(additional.get("annotation_count")),
                    "schema_count": self._safe_int(additional.get("schema_count")),
                    "table_count": self._safe_int(additional.get("table_count")),
                    "column_count": self._safe_int(additional.get("column_count")),
                    "description": props.get("description", ""),
                })
        except Exception as exc:
            log.debug(f"get_survey_reports failed for {db_slug}: {exc}")
        
        # Sort newest first
        reports.sort(key=lambda r: r.get("surveyed_at", ""), reverse=True)
        return reports

    def get_annotations(self, db_slug: str, surveyed_at: str) -> list[dict]:
        """Retrieve all annotations for a specific survey from Egeria.
        
        Args:
            db_slug: Database slug
            surveyed_at: Survey timestamp
            
        Returns:
            List of annotation dicts with keys:
            - guid: Annotation GUID
            - annotation_type: Type of annotation
            - summary: Summary text
            - confidence: Confidence level (0-100)
            - analysis_step: Analysis step name
            - explanation: Detailed explanation
            - json_properties: Additional properties as JSON
        """
        self.connect()
        
        search_prefix = f"Annotation::PostgreSQL::{db_slug}::{surveyed_at}::"
        annotations = []
        
        try:
            results = self._discovery.find_annotations(
                search_string=search_prefix,
                starts_with=True,
                ignore_case=False,
                output_format="JSON",
                page_size=500,
            )
            
            if not isinstance(results, list):
                return []
            
            for el in results:
                header = el.get("elementHeader", {})
                props = el.get("properties", {})
                
                annotations.append({
                    "guid": header.get("guid", ""),
                    "annotation_type": props.get("annotationType", ""),
                    "summary": props.get("summary", ""),
                    "confidence": self._safe_int(props.get("confidence", 100)),
                    "analysis_step": props.get("analysisStep", ""),
                    "explanation": props.get("explanation", ""),
                    "expression": props.get("expression", ""),
                    "json_properties": props.get("jsonProperties", {}),
                })
        except Exception as exc:
            log.debug(f"get_annotations failed for {db_slug}/{surveyed_at}: {exc}")
        
        return annotations

    def get_survey_reports_by_guid(self, db_guid: str) -> list[dict]:
        """Retrieve all survey reports actually linked to this database asset in
        Egeria, via the real ReportSubject relationship — not a qualifiedName
        prefix guess like get_survey_reports(). This is the only way to see
        reports Egeria's own native survey engine created (triggered via
        trigger_survey_by_guid/Survey Definitions), since those use Egeria's own
        naming convention, not the "SurveyReport::PostgreSQL::{slug}::..." prefix
        RE's own publish methods use.

        Thin wrapper over the engine-agnostic reader in egeria_survey_reader.py
        (shared with EgeriaPublisher/EgeriaFileSystemSurveyor) — kept as a method
        here for backward compatibility with existing callers/tests.
        """
        from resource_explorer.surveyors.egeria_survey_reader import get_survey_reports_by_guid

        self.connect()
        return get_survey_reports_by_guid(self._asset_maker, db_guid)

    def get_annotations_by_report_guid(self, report_guid: str) -> list[dict]:
        """Retrieve all annotations attached to a specific SurveyReport via the
        real ReportedAnnotation relationship — not a qualifiedName prefix guess
        like get_annotations(). Works for reports created by any engine, since
        it walks the actual relationship rather than assuming RE's own naming
        convention.

        Thin wrapper over the engine-agnostic reader in egeria_survey_reader.py.
        """
        from resource_explorer.surveyors.egeria_survey_reader import get_annotations_by_report_guid

        self.connect()
        return get_annotations_by_report_guid(self._asset_maker, report_guid)

    def get_latest_survey(self, db_slug: str) -> dict | None:
        """Get the most recent survey report for a database.

        Args:
            db_slug: Database slug
            
        Returns:
            Survey report dict or None if no surveys exist
        """
        reports = self.get_survey_reports(db_slug)
        return reports[0] if reports else None

    def publish_local_survey(
        self,
        db_entity: "DatabaseEntity",
        schema_info: dict,
        schema_count: int,
        table_count: int,
        column_count: int,
        surveyed_at: str,
        registry=None,
        db_user: str = "",
        db_pwd: str = "",
        statistics: dict | None = None,
        views: list | None = None,
        submitted_by: str = "",
        survey_after_catalog: bool = True,
    ) -> dict:
        """Catalog the database in Egeria, trigger a native PostgreSQL survey,
        and publish the local survey report and annotations directly to Egeria.

        `survey_after_catalog=False` publishes everything but starts NO native
        survey: the catalogue commit (`catalogue_commit.py`) starts its own,
        scoped to the chosen schemas, and an unscoped one here would measure
        every schema the steward left out.
        """
        # Interim (2026-10-13 review): no private (born-private) publish exists for databases yet, so a database in a
        # private investigation is refused here, before anything is written, recorded once per state.
        from resource_explorer.private_publish import refuse_unsupported_private

        refuse_unsupported_private(registry, "database", db_entity.slug,
                                   name=getattr(db_entity, "display_name", "") or db_entity.slug)
        result = self.catalog_and_survey(
            db_entity=db_entity,
            db_user=db_user,
            db_pwd=db_pwd,
            registry=registry,
            survey_after_catalog=survey_after_catalog,
            submitted_by=submitted_by,
        )

        server_guid        = result.get("server_guid", "")
        db_guid            = result.get("database_guid", "")
        survey_action_guid = result.get("survey_action_guid", "")
        server_survey_guid = result.get("server_survey_guid", "")

        # ── Publish local survey report & annotations to Egeria ──────────────────
        report_guid = ""
        report_error = ""
        report_reused = False
        annotations_in_egeria = None
        annotations = []
        if db_guid:
            # Recreate annotations from local scan data using DatabaseSurveyor helpers
            try:
                from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor
                local_surveyor = DatabaseSurveyor(db_entity, {}, registry)
                if schema_info:
                    annotations.extend(local_surveyor._create_schema_annotations(schema_info))
                if statistics:
                    annotations.extend(local_surveyor._create_statistics_annotations(statistics))
                if views:
                    annotations.extend(local_surveyor._create_views_annotations(views))
            except Exception as exc:
                log.warning(f"Could not reconstruct local annotations: {exc}")

            qualified_name = f"SurveyReport::PostgreSQL::{db_entity.slug}::{surveyed_at}"
            body = {
                "class": "NewElementRequestBody",
                "parentGUID": db_guid,
                "parentRelationshipTypeName": "ReportSubject",
                "properties": {
                    "class": "AssetProperties",
                    "typeName": "SurveyReport",
                    "qualifiedName": qualified_name,
                    "displayName": f"Survey: {db_entity.display_name}",
                    "description": (
                        f"Automated survey of {db_entity.host}:{db_entity.port}/{db_entity.database_name} "
                        f"run at {surveyed_at}."
                    ),
                    "additionalProperties": {
                        "annotation_count": str(len(annotations)),
                        "schema_count": str(schema_count),
                        "table_count": str(table_count),
                        "column_count": str(column_count),
                    },
                },
            }
            pub = self._publish_survey_report(body, qualified_name, annotations, db_entity.slug, surveyed_at)
            report_guid, report_error = pub["report_guid"], pub["report_error"]
            report_reused, annotations_in_egeria = pub["reused"], pub["annotations_in_egeria"]

        # Use the created report GUID if available, otherwise fall back to Egeria's native action GUID
        effective_report_guid = report_guid or survey_action_guid

        # Publish column-level lineage mapping relationships to Egeria
        self.lineage_not_checked = []
        if db_guid and views:
            try:
                self._publish_column_lineage(db_entity, views)
            except Exception as lin_err:
                log.warning(f"Could not publish column lineage: {lin_err}")

        # No survey row is written here: a publish measures nothing (false-zero
        # hotfix, 2026-09-30). The outcome is stamped on the measured row this
        # publish read (published_at + report guid), never as a new row.
        if registry and db_guid:
            registry.record_database_survey_published(
                db_entity.slug, surveyed_at, effective_report_guid,
            )

        return {
            "server_guid":        server_guid,
            "asset_guid":         db_guid,
            "report_guid":        effective_report_guid,
            "server_survey_guid": server_survey_guid,
            "annotation_count":   len(annotations),
            "report_error":       report_error,
            "report_element_guid": report_guid,
            "report_reused":      report_reused,
            "annotation_error":   pub.get("annotation_error", "") if db_guid else "",
            "annotations_in_egeria": annotations_in_egeria,
            "lineage_not_checked": list(self.lineage_not_checked),
            "survey_submissions": result.get("survey_submissions", []),
        }

    def publish_engine_action_report(
        self,
        db_entity: "DatabaseEntity",
        annotations: list,
        *,
        asset_guid: str,
        request_type: str,
        engine_action_guid: str,
        engine_user: str,
        requester: str,
        registry=None,
        extra_properties: dict | None = None,
    ) -> dict:
        """Publish the SurveyReport of an Egeria engine action RE ran as an engine host (engine_host.py, E4),
        shaped like a native survey action service's report (DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE C9):

        * `ReportSubject` from the database asset (the action's target) and a `ReportOriginator` from the
          engine action, which is how Egeria's own readers and RE's `native_survey_run` find it;
        * qualifiedName `SurveyReport::<requestType>::<assetGUID>::<time>`, purpose = the request type,
          authors = [the engine user], anchored on the asset (`AnnotationStore.java:67-150`);
        * `Ownership` stamped to the requester, with the draft zones every RE publish gets.

        The report and its annotations go through the same path as every database SurveyReport
        (`_publish_survey_report`: create, annotations via `annotation_props.publish_annotations`, a 409 on the
        same name reuses the report). Returns what happened, never raises for an Egeria answer: `report_error`
        is set when the report could not be written (then `report_guid` is ""), `originator_error` when the
        report exists but could not be linked to the action. The caller decides the completion from these.
        Everything runs as the current principal (the engine host's `Daemon(ENGINE_HOST, requested_by)`)."""
        from datetime import timezone

        from resource_explorer.private_publish import refuse_unsupported_private

        # The sweep already left private resources unclaimed; this is the same guard every database publish has.
        refuse_unsupported_private(registry, "database", db_entity.slug,
                                   name=getattr(db_entity, "display_name", "") or db_entity.slug)
        self.connect()
        created = datetime.now(timezone.utc)
        stamp = created.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        egeria_time = created.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00"
        qualified_name = f"SurveyReport::{request_type}::{asset_guid}::{stamp}"
        additional = {"annotation_count": str(len(annotations)), "engineActionGUID": engine_action_guid}
        if requester:
            additional["requestedBy"] = requester
        # Engine host step 4: `derived_from_report`, the previous process step's report this one was derived from.
        additional.update({str(k): str(v) for k, v in (extra_properties or {}).items() if v not in (None, "")})
        body = {
            "class": "NewElementRequestBody",
            "anchorGUID": asset_guid,
            "isOwnAnchor": False,
            "parentGUID": asset_guid,
            "parentAtEnd1": True,
            "parentRelationshipTypeName": "ReportSubject",
            "properties": {
                "class": "SurveyReportProperties",
                "typeName": "SurveyReport",
                "qualifiedName": qualified_name,
                "displayName": f"Survey Report for {asset_guid}",
                "description": (f"This is the {request_type} survey report for asset {asset_guid} "
                                f"({db_entity.display_name}) generated at {stamp} by Resource Explorer's engine "
                                f"host for engine action {engine_action_guid}."),
                "purpose": request_type,
                "authors": [engine_user] if engine_user else [],
                "startTime": egeria_time,
                "createdTime": egeria_time,
                "additionalProperties": additional,
            },
        }
        out = self._publish_survey_report(body, qualified_name, annotations, db_entity.slug, stamp)
        out.update(qualified_name=qualified_name, annotation_count=len(annotations), originator_guid="",
                   originator_error="", governance={})
        report_guid = out["report_guid"]
        if not report_guid:
            return out
        from pyegeria import MetadataExpert

        try:
            expert = self._clients().of(MetadataExpert)
            out["originator_guid"] = expert.create_related_elements(body={
                "class": "NewRelatedElementsRequestBody",
                "typeName": "ReportOriginator",
                "metadataElement1GUID": engine_action_guid,
                "metadataElement2GUID": report_guid,
            }) or ""
            if not out["originator_guid"]:
                out["originator_error"] = "Egeria gave no relationship GUID for the ReportOriginator link"
        except Exception as exc:  # noqa: BLE001 - returned for the caller to complete survey-failed with
            out["originator_error"] = f"{type(exc).__name__}: {exc}"[:400]
            log.warning("ReportOriginator %s -> %s not created: %s", engine_action_guid, report_guid,
                        out["originator_error"])
        if requester:
            from resource_explorer.egeria_identity import draft_zones, stamp_published

            try:
                out["governance"] = stamp_published(report_guid, requester, identity=self.principal(),
                                                    zones=draft_zones())
            except Exception as exc:  # noqa: BLE001 - a governance gap is reported, never a failed survey
                out["governance"] = {"ownership": False, "zone_membership": False, "owner": requester,
                                     "reason": f"{type(exc).__name__}: {exc}"[:300]}
        return out

    @staticmethod
    def _is_duplicate_qualified_name(exc: Exception) -> bool:
        """Egeria's 409 for a create whose qualifiedName is already taken (OMAG-COMMON-409-001)."""
        text = " ".join(str(exc).split())
        return "409" in text and ("is not available for use" in text or "OMAG-COMMON-409" in text
                                  or "unique property" in text)

    def _annotations_held_by(self, report_guid: str):
        """How many annotations Egeria holds under this report, or None when the read failed (None is
        "could not tell", never zero)."""
        from resource_explorer.surveyors.egeria_survey_reader import annotations_from_report
        try:
            result = self._asset_maker.get_asset_by_guid(
                report_guid, body={"class": "GetRequestBody", "graphQueryDepth": 1}, output_format="JSON")
            return len(annotations_from_report(result)) if isinstance(result, dict) else None
        except Exception as exc:
            log.warning("could not count the annotations under report %s: %s", report_guid, exc)
            return None

    def _publish_survey_report(self, body: dict, qualified_name: str, annotations: list,
                               db_slug: str, surveyed_at: str) -> dict:
        """Create RE's SurveyReport and its annotations, or reuse the one Egeria already holds.

        The report's qualifiedName is keyed on the survey run's timestamp, so publishing the SAME run
        twice meets a 409. That one case looks the existing report up by its exact name and reuses it:
        annotations already under it are counted (and not published again; `publish_annotations` also
        looks each qualifiedName up before it creates, so a partial set only gains what is missing).
        Every other failure is returned as `report_error` for the caller to FAIL on: never "published"."""
        out = {"report_guid": "", "report_error": "", "annotation_error": "", "reused": False, "annotations_in_egeria": None}
        try:
            out["report_guid"] = self._asset_maker.create_asset(body=body)
            log.info(f"Published SurveyReport in Egeria: {out['report_guid']}")
            made = self._create_annotations(annotations, out["report_guid"], db_slug, surveyed_at)
            # Counted from what Egeria returned a GUID for (a failed or un-checkable one is None), not from
            # how many were sent; no list back means RE cannot tell, which is None, never a number.
            out["annotations_in_egeria"] = sum(1 for g in made if g) if isinstance(made, list) else None
            return out
        except Exception as exc:
            if not (not out["report_guid"] and self._is_duplicate_qualified_name(exc)):
                log.warning(f"Failed to publish SurveyReport to Egeria: {exc}")
                out["report_error"] = f"{exc}"[:400]
                return out
        try:
            existing = self._find_element_guid(qualified_name)
        except EgeriaDatabaseSurveyorError as exc:
            # Could not look: not "no report with that name". Nothing adopted, nothing created.
            out["report_error"] = (f"Egeria refused the report as a duplicate, and {exc}")[:400]
            log.warning(out["report_error"])
            return out
        if not existing:
            out["report_error"] = ("Egeria refused the report as a duplicate but no report with that name "
                                   f"could be read ({qualified_name})")
            log.warning(out["report_error"])
            return out
        out.update(report_guid=existing, reused=True)
        log.info("SurveyReport %s already in Egeria (same survey run %s): reusing it", existing, surveyed_at)
        held = self._annotations_held_by(existing)
        if held is None or held < len(annotations):
            try:
                self._create_annotations(annotations, existing, db_slug, surveyed_at)   # adopts what is there
            except Exception as exc:
                log.warning("publishing the missing annotations under %s failed: %s", existing, exc)
                out["annotation_error"] = f"{exc}"[:400]      # observable: the caller shows it
            held = self._annotations_held_by(existing)
        out["annotations_in_egeria"] = held
        return out

    def _publish_column_lineage(self, db_entity: "DatabaseEntity", views: list[dict]) -> None:
        """Publish column-level lineage to Egeria as LineageMapping relationships (source column at end 1,
        view column at end 2).

        LineageMapping is MULTI-LINK (`OpenMetadataTypesArchive1_2.getLineageMappingRelationship`,
        `setMultiLink(true)`): every `link_lineage` call adds another relationship between the same two
        columns. So each (source, target) pair is built once per run, then linked only when no
        LineageMapping between exactly those two exists (`egeria_link_once.link_once`), and never when
        that cannot be read."""
        if not views:
            return

        db_name = db_entity.database_name

        #: Column names whose look-up could not be made, and "source -> target" pairs whose existing links
        #: could not be read, so their mappings were skipped. Kept on the surveyor so a caller can tell "no
        #: lineage published" from "lineage RE could not check".
        self.lineage_not_checked = []
        _column_guid = self._lineage_column_guid

        # (source GUID, target GUID) -> (source qualifiedName, target qualifiedName, view name), first seen
        # wins. Two leaves can name one column ("orders" and "public.orders"), and two views can resolve to
        # one element through the short-name fallback: each pair is linked once.
        pairs: dict[tuple[str, str], tuple[str, str, str]] = {}
        for view in views:
            schema_name = view["schema"]
            view_name = view["name"]

            # Walk column lineages
            for target_col, lineage_tree in view.get("lineage", {}).items():
                sources = self._extract_leaf_sources(lineage_tree)
                if not sources:
                    continue

                target_qn = f"{db_name}.{schema_name}.{view_name}.{target_col}"
                target_guid = _column_guid(target_qn, f"{view_name}.{target_col}")

                if not target_guid:
                    log.debug(f"Could not find Egeria GUID for derived column '{target_qn}'")
                    continue

                for src_table, src_col in sorted(sources):
                    if "." in src_table:
                        s_name, t_name = src_table.split(".", 1)
                    else:
                        s_name, t_name = schema_name, src_table

                    src_qn = f"{db_name}.{s_name}.{t_name}.{src_col}"
                    src_guid = _column_guid(src_qn, f"{t_name}.{src_col}")

                    if not src_guid:
                        log.debug(f"Could not find Egeria GUID for source column '{src_qn}'")
                        continue
                    pairs.setdefault((src_guid, target_guid), (src_qn, target_qn, view_name))

        if not pairs:
            return

        from pyegeria import MetadataExpert
        from pyegeria.omvs.lineage_linker import LineageLinker

        from resource_explorer.egeria_link_once import COULD_NOT_CHECK, link_once

        clients = self._clients()
        linker = clients.of(LineageLinker)
        expert = clients.of(MetadataExpert)

        for (src_guid, target_guid), (src_qn, target_qn, view_name) in pairs.items():
            body = {
                "class": "NewRelationshipRequestBody",
                "properties": {
                    "class": "LineageMappingProperties",
                    "iscQualifiedName": f"SQLGlot::LineageMapping::{target_qn}",
                    "label": "Column Derivation Link",
                    "description": f"Statically analyzed lineage map of view {view_name} via SQLGlot."
                }
            }
            # Read from the view column (end 2, `targetElement`): it is fed by the few leaves of one
            # expression, while a base-table column (end 1, `sourceElement`) can feed every view on it.
            outcome = link_once(
                expert, relationship_type="LineageMapping", read_guid=target_guid,
                read_end=LINEAGE_MAPPING_TARGET_END, far_guid=src_guid,
                what=f"from {src_qn} to {target_qn}",
                create=lambda s=src_guid, t=target_guid, b=body: linker.link_lineage(
                    element_one_guid=s, relationship_type_name="LineageMapping", element_two_guid=t, body=b))
            if outcome.outcome == COULD_NOT_CHECK:
                self.lineage_not_checked.append(f"{src_qn} -> {target_qn}")

    def _lineage_column_guid(self, *names: str) -> str:
        """The first of `names` found in Egeria, or "". Lineage only LINKS two columns that already exist and
        never creates one, so a look-up that could not be made skips this mapping (recorded in
        `lineage_not_checked` and logged) rather than ending every other view's lineage."""
        for name in names:
            try:
                guid = self._find_element_guid(name)
            except EgeriaDatabaseSurveyorError as exc:
                self.lineage_not_checked.append(name)
                log.warning("column lineage: %s; this mapping is skipped", exc)
                return ""
            if guid:
                return guid
        return ""

    def _extract_leaf_sources(self, node: dict) -> list[tuple[str, str]]:
        """Traverse a serialized SQLGlot lineage node tree and return leaf sources as list of (table, column)."""
        res = []
        downstream = node.get("downstream", [])
        if not downstream:
            source = node.get("source", "")
            name = node.get("name", "")
            if name and source:
                res.append((source, name))
        else:
            for child in downstream:
                res.extend(self._extract_leaf_sources(child))
        return list(set(res))

    def _create_annotations(
        self,
        annotations: list,
        report_guid: str,
        db_slug: str,
        surveyed_at: str,
    ) -> list:
        """Push a list of Annotation objects to Egeria; returns one GUID or None per annotation.

        Delegates to the shared implementation — see annotation_props.py.
        Kept as a method because tests reach for it by name. `surveyed_at` is
        this run's own timestamp (this run's identity, part of the
        qualifiedName) — do not recompute it here."""
        from resource_explorer.surveyors.annotation_props import publish_annotations

        qualified_name_prefix = f"Annotation::PostgreSQL::{db_slug}::{surveyed_at}"
        return publish_annotations(
            self._discovery, self._find_element_guid,
            annotations, report_guid, qualified_name_prefix,
        )

    @staticmethod
    def _to_string_map(d: dict) -> dict[str, str]:
        """Delegates to the shared implementation — see annotation_props.py."""
        from resource_explorer.surveyors.annotation_props import to_string_map

        return to_string_map(d)
    def _build_annotation_props(self, ann, qualified_name: str) -> dict:
        """Delegates to the shared implementation — see annotation_props.py.

        Kept as a method because tests and call sites reach for it by name."""
        from resource_explorer.surveyors.annotation_props import build_annotation_props

        return build_annotation_props(ann, qualified_name)
    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        """Safely convert value to int."""
        try:
            return int(value) if value is not None else default
        except (ValueError, TypeError):
            return default


def can_use_egeria() -> bool:
    """Check if Egeria is available and configured.
    
    Returns:
        True if EGERIA_PLATFORM_URL is set and pyegeria is installed
    """
    if not egeria_platform_url():
        return False
    
    try:
        import pyegeria  # noqa: F401
        return True
    except ImportError:
        return False

# Made with Bob


def build_step_annotations(db_entity, registry=None, *, schema_info: dict | None = None,
                           statistics: dict | None = None, views: list | None = None,
                           operations: dict | None = None, credential_capability: dict | None = None) -> list:
    """The annotations a database run's results become, in one place: what `publish_step_annotations` publishes,
    and what RE's engine host publishes on its native-shaped report for a credentialed database step (case 3). Pure:
    builds annotation dicts, opens no connection and calls no Egeria."""
    from resource_explorer.surveyors.database.database_surveyor import DatabaseSurveyor

    annotations: list = []
    local_surveyor = DatabaseSurveyor(db_entity, {}, registry)
    if schema_info:
        annotations.extend(local_surveyor._create_schema_annotations(schema_info))
    if statistics:
        annotations.extend(local_surveyor._create_statistics_annotations(statistics))
    if views:
        annotations.extend(local_surveyor._create_views_annotations(views))
    if operations:
        annotations.extend(local_surveyor._create_operations_annotations(operations))
        annotations.extend(
            local_surveyor._create_table_statistics_annotations(operations, schema_info or {})
        )
    if credential_capability:
        annotations.extend(
            local_surveyor._create_credential_capability_annotations(credential_capability)
        )
    return annotations


def annotations_from_step_outputs(db_entity, step_outputs: list, registry=None) -> list:
    """`build_step_annotations` over a run's step outputs, read the way the database adapter's `_publish` reads
    them (the last output carrying each part wins). Nothing when no output carries one of the parts."""
    parts: dict = {}
    for output in step_outputs or []:
        if not isinstance(output, dict):
            continue
        for key in ("schema_info", "statistics", "views", "operations", "credential_capability"):
            if output.get(key):
                parts[key] = output[key]
    if not parts:
        return []
    return build_step_annotations(db_entity, registry, **parts)
