from __future__ import annotations

import logging
from datetime import datetime

from resource_explorer.registry import FileSystemEntity, ProjectRegistry
from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

log = logging.getLogger(__name__)


def run_hybrid_filesystem_survey(
    filesystem_slug: str,
    registry: ProjectRegistry,
    egeria_url: str | None = None,
    egeria_server: str | None = None,
    force_egeria_publish: bool = False,
) -> dict:
    """Run a filesystem survey using a hybrid approach.
    
    1. Runs the LocalFileSystemSurveyor to scan the directory and profile schemas.
    2. Saves the survey locally in the SQLite registry (source='local').
    3. If Egeria coordinates are available, runs EgeriaFileSystemSurveyor to publish
       the metadata (FileSystem folder, DataFile assets, SurveyReport, and Annotations)
       to Egeria, updating the registry survey to source='egeria-published'.
    """
    fs_entity = registry.get_filesystem(filesystem_slug)
    if not fs_entity:
        raise ValueError(f"FileSystem with slug '{filesystem_slug}' not found in registry.")

    # 1. Run local surveyor
    local_surveyor = LocalFileSystemSurveyor(fs_entity, registry)
    survey_data = local_surveyor.run()

    # Save local results first
    surveyed_at = survey_data["surveyed_at"]
    registry.add_filesystem_survey(
        fs_slug=fs_entity.slug,
        surveyed_at=surveyed_at,
        survey_data=survey_data,
        egeria_report_guid="local-only",
        source="local",
    )

    # 2. Determine Egeria coordinates
    url = egeria_url or fs_entity.egeria_url or ""
    server = egeria_server or fs_entity.egeria_server or ""
    # 3. Publish to Egeria ONLY when someone chose to (Brief I round 4): a URL on the resource
    # says which Egeria, never that this survey should be published there.
    if force_egeria_publish:
        log.info(f"Publishing filesystem survey results for {fs_entity.slug} to Egeria...")
        try:
            from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor
            egeria_surveyor = EgeriaFileSystemSurveyor(platform_url=url, view_server=server)
            publish_res = egeria_surveyor.catalog_and_survey(fs_entity, survey_data, registry=registry)
            log.info(f"Successfully published filesystem {fs_entity.slug} to Egeria: {publish_res}")
            survey_data["egeria_publish"] = publish_res
        except Exception as exc:
            log.exception(f"Failed to publish survey to Egeria for {fs_entity.slug}: {exc}")
            survey_data["egeria_publish_error"] = f"{type(exc).__name__}: {exc}"[:300]
            # Non-fatal, status updated on registry
            registry.update_filesystem_status(
                fs_entity.slug,
                fs_entity.status,
                error_message=f"Failed to publish to Egeria: {exc}",
            )
    else:
        log.info("Local-only survey completed for filesystem '%s': publishing to Egeria was not chosen.",
                 fs_entity.slug)

    return survey_data
