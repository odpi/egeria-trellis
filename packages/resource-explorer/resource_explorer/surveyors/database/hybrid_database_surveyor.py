"""Hybrid database surveyor that uses Egeria when available, falls back to custom."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

log = logging.getLogger(__name__)


class HybridDatabaseSurveyor:
    """Orchestrates database surveys using a hybrid approach.
    
    Strategy:
    1. Check if Egeria is available and configured
    2. Check if survey already exists in Egeria
    3. If Egeria available and no recent survey: trigger Egeria survey
    4. If Egeria unavailable or fails: use custom surveyor
    5. Store results in registry with source tracking
    
    Parameters
    ----------
    registry : ProjectRegistry for storing results
    platform_url : Optional Egeria platform URL
    view_server : Optional Egeria view server name
    """

    def __init__(
        self,
        registry: ProjectRegistry,
        platform_url: str | None = None,
        view_server: str | None = None,
    ) -> None:
        self.registry = registry
        self.platform_url = platform_url
        self.view_server = view_server
        self._egeria_surveyor = None
        self._egeria_available = None

    def _check_egeria_available(self) -> bool:
        """Check if Egeria is available (cached result)."""
        if self._egeria_available is not None:
            return self._egeria_available
        
        from .egeria_database_surveyor import can_use_egeria
        self._egeria_available = can_use_egeria()
        return self._egeria_available

    def _get_egeria_surveyor(self):
        """Get or create Egeria surveyor instance."""
        if self._egeria_surveyor is None:
            from .egeria_database_surveyor import EgeriaDatabaseSurveyor
            self._egeria_surveyor = EgeriaDatabaseSurveyor(
                platform_url=self.platform_url,
                view_server=self.view_server,
            )
        return self._egeria_surveyor

    def survey(
        self,
        db_slug: str,
        credentials: dict | None = None,
        force_custom: bool = False,
        secrets_path: str | None = None,
        refresh: bool = False,
    ) -> dict:
        """Run a database survey using the hybrid approach.
        
        Args:
            db_slug: Database slug to survey
            credentials: Database credentials (user, password) for custom survey
            force_custom: If True, skip Egeria and use custom surveyor
            secrets_path: Path to Egeria secrets file (for Egeria survey)
            refresh: If True, bypass cached survey reports and run a fresh survey
            
        Returns:
            Survey results dict with keys:
            - source: "egeria" or "custom"
            - database_slug: Database slug
            - surveyed_at: ISO timestamp
            - schema_info: Schema information
            - statistics: Database statistics
            - annotations: List of annotations
            - errors: List of error messages
            - egeria_report_guid: Egeria report GUID (if from Egeria)
        """
        # Get database entity
        db_entity = self.registry.get_database(db_slug)
        if not db_entity:
            return {
                "source": "error",
                "database_slug": db_slug,
                "surveyed_at": datetime.utcnow().isoformat(),
                "errors": [f"Database '{db_slug}' not found in registry"],
            }

        # Determine survey strategy
        use_egeria = False
        if not force_custom and self._check_egeria_available():
            # Check if recent survey exists in Egeria
            try:
                egeria_surveyor = self._get_egeria_surveyor()
                latest = None
                if not refresh:
                    latest = egeria_surveyor.get_latest_survey(db_slug)
                
                if latest:
                    # Survey exists, retrieve it
                    log.info(f"Found existing Egeria survey for {db_slug}")
                    return self._retrieve_egeria_survey(db_slug, latest)
                else:
                    # No survey exists, trigger new one
                    log.info(f"No Egeria survey found or refresh requested for {db_slug}, triggering new survey")
                    use_egeria = True
            except Exception as exc:
                log.warning(f"Egeria check failed for {db_slug}: {exc}")
                log.info("Falling back to custom surveyor")

        # Execute survey
        if use_egeria:
            return self._run_egeria_survey(db_entity, secrets_path, credentials)
        else:
            return self._run_custom_survey(db_entity, credentials)

    def _run_egeria_survey(
        self,
        db_entity: DatabaseEntity,
        secrets_path: str | None,
        credentials: dict | None = None,
    ) -> dict:
        """Trigger an Egeria survey by running a local survey first and publishing results directly to Egeria."""
        # Resolve DB credentials: prefer explicit credentials, fall back to stored on entity.
        db_user = (credentials or {}).get("user") or getattr(db_entity, "db_user", "")
        db_pwd  = (credentials or {}).get("password") or getattr(db_entity, "db_password", "")

        # 1. Run local custom survey for immediate results
        local_result = None
        if credentials or db_user:
            resolved_creds = credentials or {"user": db_user, "password": db_pwd}
            local_result = self._run_custom_survey(db_entity, resolved_creds)

        # 2. Publish to Egeria if Egeria is available
        try:
            egeria_surveyor = self._get_egeria_surveyor()
            if local_result and "errors" not in local_result:
                log.info(f"Publishing local survey results to Egeria for {db_entity.slug}")
                schema_info = local_result.get("schema_info", {})
                statistics = local_result.get("statistics", {})
                surveyed_at = local_result.get("surveyed_at", datetime.utcnow().isoformat())
                
                pub_result = egeria_surveyor.publish_local_survey(
                    db_entity=db_entity,
                    schema_info=schema_info,
                    schema_count=len(schema_info.get("schemas", [])),
                    table_count=schema_info.get("total_tables", 0),
                    column_count=schema_info.get("total_columns", 0),
                    surveyed_at=surveyed_at,
                    registry=self.registry,
                    db_user=db_user,
                    db_pwd=db_pwd,
                    statistics=statistics,
                )
                
                # Format response
                local_result["source"] = "egeria-custom"
                local_result["egeria_report_guid"] = pub_result.get("report_guid")
                local_result["message"] = (
                    "Survey completed and published to Egeria successfully."
                )
                return local_result
            else:
                # Fall back to native Egeria trigger (no local data or local failed)
                log.info(f"Cataloging + triggering native Egeria survey for {db_entity.slug}")
                egeria_result = egeria_surveyor.catalog_and_survey(
                    db_entity=db_entity,
                    db_user=db_user,
                    db_pwd=db_pwd,
                    registry=self.registry,
                    survey_after_catalog=True,
                )
                engine_action_guid = egeria_result.get("survey_action_guid", "")
                
                if local_result:
                    local_result["source"] = "egeria"
                    local_result["engine_action_guid"] = engine_action_guid
                    local_result["message"] = (
                        "Egeria survey triggered. Local survey has errors; displaying local schema info."
                    )
                    return local_result
                else:
                    return {
                        "source": "egeria",
                        "database_slug": db_entity.slug,
                        "surveyed_at": datetime.utcnow().isoformat(),
                        "status": "pending",
                        "engine_action_guid": engine_action_guid,
                        "message": "Egeria survey triggered. Results will be available in Egeria after processing.",
                        "errors": [],
                    }
        except Exception as exc:
            from .egeria_database_surveyor import ArchivedDatabaseNeedsConfirmation
            if isinstance(exc, ArchivedDatabaseNeedsConfirmation):
                # S19: the database is archived in Egeria. Nothing was created; a survey never re-includes it.
                # The local result (if any) stands, and the outcome says what the person must confirm, and where.
                log.warning("Egeria publish held for %s: %s", db_entity.slug, exc)
                out = local_result or {"database_slug": db_entity.slug,
                                       "surveyed_at": datetime.utcnow().isoformat(), "errors": []}
                out["source"] = "custom" if local_result else "egeria"
                out["egeria_status"] = "needs_confirmation"
                out["needs_confirmation"] = {"node": "database", "archived_guid": exc.archived_guid,
                                             "date": exc.date, "state": exc.state}
                out["message"] = str(exc)
                out["errors"] = out.get("errors", []) + [str(exc)]
                return out
            log.error(f"Egeria publish/survey failed for {db_entity.slug}: {exc}")
            if local_result:
                local_result["source"] = "custom"
                local_result["errors"] = local_result.get("errors", []) + [f"Egeria integration failed: {exc}"]
                return local_result
            else:
                return self._run_custom_survey(db_entity, credentials)

    def _run_custom_survey(
        self,
        db_entity: DatabaseEntity,
        credentials: dict | None,
    ) -> dict:
        """Run survey using custom surveyor."""
        if credentials is None:
            return {
                "source": "error",
                "database_slug": db_entity.slug,
                "surveyed_at": datetime.utcnow().isoformat(),
                "errors": [
                    "Custom survey requires credentials. "
                    "Provide --user and --password options."
                ],
            }

        try:
            from .database_surveyor import DatabaseSurveyor
            
            log.info(f"Running custom survey for {db_entity.slug}")
            surveyor = DatabaseSurveyor(db_entity, credentials, self.registry)
            results = surveyor.survey()
            
            # Add source tracking
            results["source"] = "custom"
            return results
            
        except Exception as exc:
            log.error(f"Custom survey failed for {db_entity.slug}: {exc}")
            return {
                "source": "error",
                "database_slug": db_entity.slug,
                "surveyed_at": datetime.utcnow().isoformat(),
                "errors": [f"Custom survey failed: {str(exc)}"],
            }

    def _retrieve_egeria_survey(self, db_slug: str, latest_report: dict) -> dict:
        """Retrieve an existing survey from Egeria."""
        try:
            egeria_surveyor = self._get_egeria_surveyor()
            surveyed_at = latest_report["surveyed_at"]
            
            # Get annotations
            annotations = egeria_surveyor.get_annotations(db_slug, surveyed_at)
            
            # Convert to our format
            return {
                "source": "egeria",
                "database_slug": db_slug,
                "surveyed_at": surveyed_at,
                "egeria_report_guid": latest_report["guid"],
                "schema_count": latest_report.get("schema_count"),
                "table_count": latest_report.get("table_count"),
                "column_count": latest_report.get("column_count"),
                "annotations": annotations,
                "annotation_count": latest_report.get("annotation_count", len(annotations)),
                "description": latest_report.get("description", ""),
                "errors": [],
            }
        except Exception as exc:
            log.error(f"Failed to retrieve Egeria survey for {db_slug}: {exc}")
            return {
                "source": "error",
                "database_slug": db_slug,
                "surveyed_at": datetime.utcnow().isoformat(),
                "errors": [f"Failed to retrieve Egeria survey: {str(exc)}"],
            }

    def check_survey_source(self, db_slug: str) -> str:
        """Check where the latest survey came from.
        
        Returns:
            "egeria", "custom", or "none"
        """
        # Check Egeria first
        if self._check_egeria_available():
            try:
                egeria_surveyor = self._get_egeria_surveyor()
                if egeria_surveyor.check_survey_exists(db_slug):
                    return "egeria"
            except Exception:
                pass
        
        # Check local registry
        latest = self.registry.get_latest_database_survey(db_slug)
        if latest:
            return "custom"
        
        return "none"


def run_hybrid_survey(
    db_slug: str,
    credentials: dict | None = None,
    registry: ProjectRegistry | None = None,
    force_custom: bool = False,
    platform_url: str | None = None,
    view_server: str | None = None,
    secrets_path: str | None = None,
    refresh: bool = False,
) -> dict:
    """Convenience function to run a hybrid database survey.
    
    Args:
        db_slug: Database slug to survey
        credentials: Database credentials for custom survey (user, password)
        registry: Optional ProjectRegistry (creates new one if not provided)
        force_custom: If True, skip Egeria and use custom surveyor
        platform_url: Optional Egeria platform URL
        view_server: Optional Egeria view server name
        secrets_path: Path to Egeria secrets file
        refresh: If True, bypass cached survey reports and run a fresh survey
        
    Returns:
        Survey results dict
        
    Example:
        # Try Egeria first, fall back to custom
        results = run_hybrid_survey(
            "my-postgres",
            credentials={"user": "admin", "password": "secret"}
        )
        
        # Force custom surveyor
        results = run_hybrid_survey(
            "my-postgres",
            credentials={"user": "admin", "password": "secret"},
            force_custom=True
        )
    """
    if registry is None:
        from resource_explorer.registry import ProjectRegistry
        registry = ProjectRegistry()

    surveyor = HybridDatabaseSurveyor(
        registry=registry,
        platform_url=platform_url,
        view_server=view_server,
    )
    
    return surveyor.survey(
        db_slug=db_slug,
        credentials=credentials,
        force_custom=force_custom,
        secrets_path=secrets_path,
        refresh=refresh,
    )

# Made with Bob
