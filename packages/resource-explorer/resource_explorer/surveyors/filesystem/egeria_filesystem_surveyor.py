from __future__ import annotations

import logging
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

if TYPE_CHECKING:
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry

log = logging.getLogger(__name__)


class EgeriaFileSystemSurveyorError(RuntimeError):
    """Raised when Egeria filesystem operations fail."""


# Investigated 2026-09-20 alongside the database-side "catalog_and_survey never
# refreshes an existing element's connection" fix (CATALOG-AND-SURVEY-REFRESH-FIX.md) — this
# surveyor does NOT have the same
# bug, verified rather than assumed symmetric:
#
# catalog_and_survey() below takes no db_user/db_pwd (or any credential) at
# all, and create_folder_element_from_template() doesn't accept any — a local
# filesystem mount has nothing to authenticate with. There is no credentials-
# carrying create call being skipped on repeat runs the way the PostgreSQL
# create_postgres_*_element_from_template calls are, so "corrected credentials
# never get applied" cannot happen here. It also creates no Connection at
# all — the DataFolder/DataFile templates it uses have no attached Connection
# subgraph the way the PostgreSQL templates do, so the deepCopy gap fixed on
# the database side doesn't apply either. See trigger_survey_by_guid's own
# docstring below for the separate, still-open question of whether the
# *native* FileDirectory survey needs a Connection on the FileFolder asset —
# that's a different, unverified concern from the one this fix addresses.


def _published_count(made):
    """How many annotations Egeria returned a GUID for; None when no per-annotation answer came back."""
    return sum(1 for g in made if g) if isinstance(made, list) else None


class EgeriaFileSystemSurveyor:
    """Catalog filesystems and files in Egeria and publish survey reports."""

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

    def connect(self) -> None:
        """Initialize connection bearer tokens for Egeria services."""
        if self._automated_curation is not None:
            return

        if not self.platform_url:
            raise EgeriaFileSystemSurveyorError(
                "EGERIA_PLATFORM_URL is not set. "
                "Set it in .env or pass platform_url= to EgeriaFileSystemSurveyor."
            )

        try:
            from pyegeria import AutomatedCuration, AssetMaker
            from pyegeria.omvs.data_discovery import DataDiscovery

            from resource_explorer.egeria_clients import current_principal, egeria_client

            # Who (Brief I): the identity handed in, else the signed-in Caller on a route or the
            # declared daemon job in the worker — never the entity's stored credential.
            clients = egeria_client(self._identity or current_principal(), purpose="file system survey",
                                    view_server=self.view_server, platform_url=self.platform_url)
            self._automated_curation = clients.of(AutomatedCuration)
            self._asset_maker = clients.of(AssetMaker)
            self._discovery = clients.of(DataDiscovery)
        except PermissionError:
            raise
        except ImportError as exc:
            raise EgeriaFileSystemSurveyorError("pyegeria is not installed.") from exc
        except Exception as exc:
            raise EgeriaFileSystemSurveyorError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc

    def catalog_and_survey(
        self,
        fs_entity: FileSystemEntity,
        survey_data: dict,
        registry: ProjectRegistry | None = None,
    ) -> dict:
        """Catalog the filesystem root folder and data files in Egeria, then publish the survey report."""
        # Interim (2026-10-13 review): no private (born-private) publish exists for file systems yet, so a file system
        # in a private investigation is refused here, before anything is written, recorded once per state.
        from resource_explorer.private_publish import refuse_unsupported_private

        refuse_unsupported_private(registry, "filesystem", fs_entity.slug,
                                   name=fs_entity.display_name or fs_entity.slug)
        self.connect()

        local_mount = fs_entity.local_mount_point.rstrip("/")
        canonical_mount = (fs_entity.canonical_mount_point or local_mount).rstrip("/")
        fs_name = fs_entity.display_name or fs_entity.slug

        # 1. Catalog/find the filesystem representation (using DataFolder template as compensation)
        log.info(f"Cataloging FileSystem root folder in Egeria: name={fs_name}, path={canonical_mount}")
        fs_guid = self._find_element_guid(canonical_mount)
        if not fs_guid:
            try:
                # Egeria DataFolder template: path_name, folder_name, file_system, description
                fs_guid = self._automated_curation.create_folder_element_from_template(
                    path_name=canonical_mount,
                    folder_name=fs_name,
                    file_system=fs_entity.slug,
                    description=fs_entity.description or f"FileSystem root mapping: {local_mount} -> {canonical_mount}",
                )
                log.info(f"Created FileSystem root DataFolder in Egeria: {fs_guid}")
            except Exception as exc:
                raise EgeriaFileSystemSurveyorError(
                    f"Could not create FileSystem root folder element for {canonical_mount}: {exc}"
                ) from exc

        # Update registry GUID
        if registry and fs_guid:
            registry.set_filesystem_egeria_guid(fs_entity.slug, fs_guid)

        # 2. Catalog data files
        file_guids = {}
        #: rel_path -> why its look-up could not be made, so nothing was created for it. One file that
        #: cannot be checked is skipped and listed; it does not end the run (as `publish_sub_resources`).
        files_not_checked: dict[str, str] = {}
        inventory = survey_data.get("inventory", [])
        for file_info in inventory:
            if not file_info.get("is_data_file"):
                continue

            rel_path = file_info["file_path"]
            canonical_path = f"{canonical_mount}/{rel_path}"
            
            # Catalog/find the file asset
            try:
                file_guid = self._find_element_guid(canonical_path)
            except EgeriaFileSystemSurveyorError as exc:
                files_not_checked[rel_path] = str(exc)
                log.warning("Data file %s not cataloged: %s", rel_path, exc)
                continue
            if not file_guid:
                try:
                    file_guid = self._create_data_file_asset(file_info, canonical_path, fs_entity.slug)
                    log.info(f"Created DataFile asset in Egeria for {rel_path}: {file_guid}")
                except Exception as exc:
                    log.warning(f"Could not catalog data file {rel_path} in Egeria: {exc}")
                    continue
            
            if file_guid:
                file_guids[rel_path] = file_guid

        # 3. Build local Annotation objects for SurveyReport
        annotations = []
        
        # Add ResourceMeasureAnnotation for filesystem walk stats
        from resource_explorer.surveyors.survey_report import (
            AnnotationType,
            ResourceMeasureAnnotation,
            SchemaAnalysisAnnotation,
        )
        
        annotations.append(
            ResourceMeasureAnnotation(
                summary=(
                    f"FileSystem scan: {survey_data['total_files']} files "
                    f"({survey_data['total_data_files']} data files), "
                    f"total size: {survey_data['total_size']}"
                ),
                analysis_step="FileSystemWalk",
                confidence=95,
                resource_properties={
                    "total_files": survey_data["total_files"],
                    "total_data_files": survey_data["total_data_files"],
                    "total_size_bytes": survey_data["total_size_bytes"],
                    "total_size": survey_data["total_size"],
                    "formats": survey_data.get("formats", {}),
                },
                json_properties={"source": "local-scan"},
            )
        )

        # Add SchemaAnalysisAnnotation for each profiled file
        for file_info in inventory:
            if not file_info.get("is_data_file") or "row_count" not in file_info:
                continue

            rel_path = file_info["file_path"]
            canonical_path = f"{canonical_mount}/{rel_path}"

            annotations.append(
                SchemaAnalysisAnnotation(
                    summary=(
                        f"{rel_path}: "
                        f"{file_info['row_count']:,} rows × {file_info['col_count']} columns"
                    ),
                    analysis_step="DataProfiling",
                    confidence=95,
                    schema_name=canonical_path,
                    schema_type=file_info["format"],
                    explanation=file_info.get("null_summary", ""),
                    json_properties={
                        "row_count": file_info["row_count"],
                        "col_count": file_info["col_count"],
                        "columns": file_info.get("columns", []),
                        "file_size": file_info["file_size"],
                    },
                )
            )

        # 4. Create and publish the SurveyReport
        surveyed_at = survey_data.get("surveyed_at") or datetime.utcnow().isoformat()
        qualified_name = f"SurveyReport::FileSystem::{fs_entity.slug}::{surveyed_at}"
        body = {
            "class": "NewElementRequestBody",
            "parentGUID": fs_guid,
            "parentRelationshipTypeName": "ReportSubject",
            "properties": {
                "class": "AssetProperties",
                "typeName": "SurveyReport",
                "qualifiedName": qualified_name,
                "displayName": f"Survey: {fs_name}",
                "description": f"Automated walk and profile of filesystem {fs_name} at local mount {local_mount} run at {surveyed_at}.",
                "additionalProperties": {
                    "annotation_count": str(len(annotations)),
                    "file_count": str(survey_data["total_files"]),
                    "data_file_count": str(survey_data["total_data_files"]),
                    "total_size": str(survey_data["total_size"]),
                },
            },
        }

        report_guid = ""
        made = []
        try:
            report_guid = self._asset_maker.create_asset(body=body)
            log.info(f"Published SurveyReport in Egeria: {report_guid}")
            
            # Push annotations under the SurveyReport
            made = self._create_annotations(annotations, report_guid, fs_entity.slug, surveyed_at)
        except Exception as exc:
            log.warning(f"Failed to publish SurveyReport to Egeria: {exc}")

        effective_report_guid = report_guid or "local-only"

        # Update registry with Egeria published survey
        if registry:
            registry.add_filesystem_survey(
                fs_slug=fs_entity.slug,
                surveyed_at=surveyed_at,
                survey_data=survey_data,
                egeria_report_guid=effective_report_guid,
                source="egeria-published",
            )

        return {
            "filesystem_guid": fs_guid,
            "report_guid": effective_report_guid,
            "file_guids": file_guids,
            "files_not_checked": files_not_checked,
            # RE's own count (built and sent); Egeria's is `annotations_in_egeria` (GUIDs returned, None
            # when RE cannot tell), the same two names every producer uses.
            "annotation_count": len(annotations),
            "annotations_in_egeria": _published_count(made),
        }

    def publish_step_annotations(
        self,
        fs_entity: FileSystemEntity,
        survey_data: dict,
        registry: ProjectRegistry | None = None,
    ) -> dict:
        """Guarded front door — see _publish_step_annotations for what this does.

        Everything below depends on the Egeria GUID RE cached for this
        filesystem. If Egeria no longer knows it, guard_linkage records the
        divergence and raises a named error naming the entity and the three
        resolutions, instead of the opaque SERVER_ERROR_500 that reached the UI
        verbatim before. See resource_explorer/egeria_linkage.py.
        """
        from resource_explorer.egeria_linkage import guard_linkage

        with guard_linkage(registry, "filesystem", fs_entity.slug,
                           fs_entity.display_name or fs_entity.slug,
                           fs_entity.egeria_asset_guid or ""):
            return self._publish_step_annotations(fs_entity, survey_data, registry)

    def _publish_step_annotations(
        self,
        fs_entity: FileSystemEntity,
        survey_data: dict,
        registry: ProjectRegistry | None = None,
    ) -> dict:
        """Publish RE-computed annotations to Egeria WITHOUT cataloging the filesystem
        root or any data files as a side effect.

        Narrower counterpart to catalog_and_survey: that method also auto-catalogs
        the filesystem root folder and every data file it finds as new Egeria
        assets — not appropriate when a Survey Definition has already explicitly
        declared this step runs in RE. The filesystem must already be cataloged in
        Egeria (fs_entity.egeria_asset_guid set, or findable by canonical mount
        point); this method does not auto-catalog it.
        """
        # Interim (2026-10-13 review): no private (born-private) publish exists for file systems yet, so a file system
        # in a private investigation is refused here, before anything is written, recorded once per state.
        from resource_explorer.private_publish import refuse_unsupported_private

        refuse_unsupported_private(registry, "filesystem", fs_entity.slug,
                                   name=fs_entity.display_name or fs_entity.slug)
        self.connect()

        canonical_mount = (fs_entity.canonical_mount_point or fs_entity.local_mount_point).rstrip("/")
        fs_name = fs_entity.display_name or fs_entity.slug
        fs_guid = fs_entity.egeria_asset_guid or self._find_element_guid(canonical_mount)
        if not fs_guid:
            raise EgeriaFileSystemSurveyorError(
                f"FileSystem '{fs_name}' is not yet published to Egeria — cannot "
                "publish a Survey Definition step's results without an existing "
                "asset to attach the SurveyReport to."
            )

        from resource_explorer.surveyors.survey_report import (
            ClassificationAnnotation,
            RequestForActionAnnotation,
            ResourceMeasureAnnotation,
            SchemaAnalysisAnnotation,
        )

        inventory = survey_data.get("inventory", [])

        # "Capture File Counts" parity with Egeria's native FileDirectory:CreateAndSurvey
        # (see docs/filesystem-survey-analytics-plan.md §1) — counts plus the
        # OS-level attributes/timestamps only a metadata pass can provide.
        annotations = [
            ResourceMeasureAnnotation(
                summary=(
                    f"FileSystem scan: {survey_data['total_files']} files "
                    f"({survey_data['total_data_files']} data files), "
                    f"total size: {survey_data['total_size']}"
                ),
                analysis_step="FileSystemWalk",
                confidence=95,
                resource_properties={
                    "total_files": survey_data["total_files"],
                    "total_data_files": survey_data["total_data_files"],
                    "total_size_bytes": survey_data["total_size_bytes"],
                    "total_size": survey_data["total_size"],
                    "formats": survey_data.get("formats", {}),
                    "hidden_count": survey_data.get("hidden_count", 0),
                    "symlink_count": survey_data.get("symlink_count", 0),
                    "executable_count": survey_data.get("executable_count", 0),
                    "writable_count": survey_data.get("writable_count", 0),
                    "unique_extension_count": survey_data.get("unique_extension_count", 0),
                    "unique_filename_count": survey_data.get("unique_filename_count", 0),
                    "last_access_time": survey_data.get("last_access_time", ""),
                    "last_creation_time": survey_data.get("last_creation_time", ""),
                    "last_modification_time": survey_data.get("last_modification_time", ""),
                    "inaccessible_file_count": survey_data.get("inaccessible_file_count", 0),
                },
                json_properties={"source": "local-scan"},
            )
        ]

        # "Profile Deployed Implementation Types/Asset Types/File Types" parity —
        # one ClassificationAnnotation per recognized type group.
        classification = survey_data.get("classification", {})
        type_counts = classification.get("type_counts", {})
        for label, count in sorted(type_counts.items()):
            annotations.append(
                ClassificationAnnotation(
                    summary=f"{count} file(s) classified as '{label}'",
                    analysis_step="FileSystemClassification",
                    candidate_classifications=[label],
                    confidence=90,
                    json_properties={"file_count": count},
                )
            )

        # "Profile File Extensions" parity.
        if classification.get("extension_counts"):
            annotations.append(
                ResourceMeasureAnnotation(
                    summary=f"File extension breakdown across {survey_data['total_files']} files",
                    analysis_step="FileSystemClassification",
                    confidence=95,
                    resource_properties={"by_extension": classification["extension_counts"]},
                )
            )

        # "Missing File Reference Data" parity — files that couldn't be classified.
        unclassified_count = classification.get("unclassified_count", 0)
        if unclassified_count:
            annotations.append(
                RequestForActionAnnotation(
                    summary=f"{unclassified_count} file(s) could not be classified against known file reference data",
                    analysis_step="FileSystemClassification",
                    confidence=70,
                    action_requested="Review unclassified file extensions/names and enrich Egeria's file reference data if any are significant.",
                    json_properties={"sample_paths": classification.get("unclassified_sample", [])},
                )
            )

        # "Inaccessible files" parity — files/directories the walk couldn't stat or enter.
        inaccessible_files = survey_data.get("inaccessible_files", [])
        if inaccessible_files:
            annotations.append(
                RequestForActionAnnotation(
                    summary=f"{len(inaccessible_files)} file(s)/director(y/ies) could not be accessed during the scan",
                    analysis_step="FileSystemWalk",
                    confidence=95,
                    action_requested="Review file/directory permissions for the listed paths.",
                    json_properties={"inaccessible": inaccessible_files[:20]},
                )
            )

        # Data profiling failures — RE-specific (no Egeria native equivalent),
        # surfaced so a run with many malformed files doesn't read as silent.
        profiling_errors = survey_data.get("profiling_errors", [])
        if profiling_errors:
            annotations.append(
                RequestForActionAnnotation(
                    summary=f"{len(profiling_errors)} data file(s) could not be schema-profiled",
                    analysis_step="DataProfiling",
                    confidence=95,
                    action_requested="Review the listed files — commonly malformed delimiters, missing optional parser dependencies (e.g. xlrd for legacy .xls), or empty files.",
                    json_properties={"errors": profiling_errors[:20]},
                )
            )

        for file_info in inventory:
            if not file_info.get("is_data_file") or "row_count" not in file_info:
                continue
            rel_path = file_info["file_path"]
            canonical_path = f"{canonical_mount}/{rel_path}"
            annotations.append(
                SchemaAnalysisAnnotation(
                    summary=(
                        f"{rel_path}: {file_info['row_count']:,} rows × {file_info['col_count']} columns"
                    ),
                    analysis_step="DataProfiling",
                    confidence=95,
                    schema_name=canonical_path,
                    schema_type=file_info["format"],
                    explanation=file_info.get("null_summary", ""),
                    json_properties={
                        "row_count": file_info["row_count"],
                        "col_count": file_info["col_count"],
                        "columns": file_info.get("columns", []),
                        "file_size": file_info["file_size"],
                    },
                )
            )

        surveyed_at = survey_data.get("surveyed_at") or datetime.utcnow().isoformat()
        qualified_name = f"SurveyReport::FileSystem::{fs_entity.slug}::{surveyed_at}"
        body = {
            "class": "NewElementRequestBody",
            "parentGUID": fs_guid,
            "parentRelationshipTypeName": "ReportSubject",
            "properties": {
                "class": "AssetProperties",
                "typeName": "SurveyReport",
                "qualifiedName": qualified_name,
                "displayName": f"Survey: {fs_name}",
                "description": (
                    f"Survey Definition step results for filesystem {fs_name} "
                    f"run at {surveyed_at}."
                ),
                "additionalProperties": {
                    "annotation_count": str(len(annotations)),
                    "file_count": str(survey_data["total_files"]),
                    "data_file_count": str(survey_data["total_data_files"]),
                },
            },
        }
        report_guid = self._asset_maker.create_asset(body=body)
        log.info(f"Published SurveyReport in Egeria: {report_guid}")
        made = self._create_annotations(annotations, report_guid, fs_entity.slug, surveyed_at)

        if registry:
            registry.add_filesystem_survey(
                fs_slug=fs_entity.slug,
                surveyed_at=surveyed_at,
                survey_data=survey_data,
                egeria_report_guid=report_guid,
                source="egeria-published",
            )

        return {"filesystem_guid": fs_guid, "report_guid": report_guid,
                "annotation_count": len(annotations), "annotations_in_egeria": _published_count(made)}

    def get_survey_reports_by_guid(self, fs_guid: str) -> list[dict]:
        """All SurveyReports linked to this filesystem's asset GUID via the real
        ReportSubject relationship — regardless of which engine created them.
        Thin wrapper over the engine-agnostic reader shared with
        EgeriaDatabaseSurveyor/EgeriaPublisher (egeria_survey_reader.py)."""
        from resource_explorer.surveyors.egeria_survey_reader import get_survey_reports_by_guid

        self.connect()
        return get_survey_reports_by_guid(self._asset_maker, fs_guid)

    def get_annotations_by_report_guid(self, report_guid: str) -> list[dict]:
        """All annotations attached to a specific SurveyReport via the real
        ReportedAnnotation relationship. Thin wrapper over the engine-agnostic
        reader in egeria_survey_reader.py."""
        from resource_explorer.surveyors.egeria_survey_reader import get_annotations_by_report_guid

        self.connect()
        return get_annotations_by_report_guid(self._asset_maker, report_guid)

    def trigger_survey_by_guid(self, fs_guid: str) -> str:
        """Initiate Egeria's native FileDirectory survey using a stored GUID —
        the filesystem equivalent of EgeriaDatabaseSurveyor.trigger_survey_by_guid,
        added 2026-08-24 to close survey_definition_executor.py's Egeria-trigger
        stub for entity_type="filesystem" (see filesystem/survey_definition_
        adapter.py's other_engine_handlers registration).

        Use this when the filesystem is already cataloged in Egeria (fs_guid
        known) — Egeria surveys the already-cataloged FileFolder, it does not
        create one as a side effect (unlike catalog_and_survey() above).

        Confirmed live (2026-08-24) against a real Egeria instance via
        GovernanceOfficer.find_governance_definitions(search_string='survey-folder',
        metadata_element_type='GovernanceActionType'): the real, registered
        process is qualifiedName "FileSurvey::survey-folder" (double colon —
        NOT pyegeria's own AutomatedCuration.initiate_file_folder_survey()
        default of "FileSurveys:survey-folder", which is both a different
        prefix ("FileSurveys" vs "FileSurvey") and a single colon — the same
        class of pyegeria default-string bug already worked around for
        PostgreSQL in _initiate_native_survey below (OMAG-GENERIC-HANDLERS-
        400-013 "name is not recognized" is the failure mode if you rely on
        the default). Passed explicitly here rather than trusting the default.
        Its one required action target is named "fileToSurvey" (also
        confirmed live, from the GovernanceActionType's own supportedActionTarget
        specification), but initiate_file_folder_survey's own signature only
        takes a bare GUID — it builds that action-target wiring internally,
        not exposed as a parameter here.

        NOT yet exercised end-to-end against a real cataloged filesystem —
        this environment has zero filesystem resources registered to test
        against (confirmed: GET /api/filesystems/ returns []). The process/
        target-name discovery above is real and live-verified; the actual
        trigger call itself is not. Verify against a real cataloged
        filesystem before relying on this."""
        self.connect()
        try:
            engine_action_guid = self._automated_curation.initiate_file_folder_survey(
                file_folder_guid=fs_guid, survey_name="FileSurvey::survey-folder",
            )
            log.info(f"Egeria filesystem survey initiated: {engine_action_guid}")
            return engine_action_guid
        except Exception as exc:
            raise EgeriaFileSystemSurveyorError(
                f"Failed to initiate Egeria survey for fs_guid={fs_guid}: {exc}"
            ) from exc

    def _create_data_file_asset(self, file_info: dict, canonical_path: str, fs_name: str) -> str:
        """Create a data file element in Egeria using template metadata values."""
        ext = Path(file_info["file_path"]).suffix.lstrip(".").lower()
        fmt = file_info["format"]
        
        # Select correct template GUID
        if fmt == "CSV":
            template_guid = "13770f93-13c8-42be-9bb8-e0b1b1e52b1f" # CSV Data File template
        elif fmt == "Parquet":
            template_guid = "7f6cd744-79c3-4d25-a056-eeb1a91574c3" # Parquet Data File template
        elif fmt == "Excel":
            template_guid = "e4fabff5-2ba9-4050-9076-6ed917970b4c" # Spreadsheet Data File template
        else:
            template_guid = "ae3067c7-cc72-4a18-88e1-746803c2c86f" # Generic File template
            
        body = {
            "class": "TemplateRequestBody",
            "templateGUID": template_guid,
            "isOwnAnchor": True,
            "placeholderPropertyValues": {
                "fileName": file_info["file_name"],
                "fileType": f"{fmt} File",
                "filePathName": canonical_path,
                "fileExtension": ext,
                "fileSystemName": fs_name,
                "description": f"Scanned {fmt} data file on filesystem {fs_name}",
                "versionIdentifier": "1.0"
            }
        }
        try:
            return self._automated_curation.create_elem_from_template(body)
        except Exception as exc:
            log.warning(f"Failed to create asset using template {fmt}: {exc}. Trying fallback to generic File...")
            if template_guid != "ae3067c7-cc72-4a18-88e1-746803c2c86f":
                body["templateGUID"] = "ae3067c7-cc72-4a18-88e1-746803c2c86f"
                body["placeholderPropertyValues"]["fileType"] = "File"
                try:
                    return self._automated_curation.create_elem_from_template(body)
                except Exception as inner_exc:
                    log.warning(f"Generic File template fallback failed: {inner_exc}")
            raise

    def _create_annotations(
        self,
        annotations: list,
        report_guid: str,
        fs_slug: str,
        surveyed_at: str,
    ) -> None:
        """Push a list of local Annotation objects to Egeria.

        Delegates to the shared implementation — see annotation_props.py.
        Kept as a method because tests reach for it by name. `surveyed_at` is
        this run's own timestamp (this run's identity, part of the
        qualifiedName) — do not recompute it here."""
        from resource_explorer.surveyors.annotation_props import publish_annotations

        qualified_name_prefix = f"Annotation::FileSystem::{fs_slug}::{surveyed_at}"
        return publish_annotations(
            self._discovery, self._find_element_guid,
            annotations, report_guid, qualified_name_prefix,
        )

    def _build_annotation_props(self, ann, qualified_name: str) -> dict:
        """Delegates to the shared implementation — see annotation_props.py.

        Kept as a method because tests and call sites reach for it by name."""
        from resource_explorer.surveyors.annotation_props import build_annotation_props

        return build_annotation_props(ann, qualified_name)
    @staticmethod
    def _to_string_map(d: dict) -> dict[str, str]:
        """Delegates to the shared implementation — see annotation_props.py."""
        from resource_explorer.surveyors.annotation_props import to_string_map

        return to_string_map(d)
    def _find_element_guid(self, name: str) -> str:
        """Return the GUID of an existing Egeria element by display/qualified name, or '' if not found.

        '' is the callers' cue to create, so only a real miss returns it ("No elements found", or a typed
        not-found: `egeria_absence.is_absent`). A look-up that raised for any other reason, or gave no
        answer, could not be checked: `EgeriaFileSystemSurveyorError` says so and nothing is created. It
        used to swallow every exception and return '' (AUDIT-PYEGERIA-6.2.2.md, "Not in scope")."""
        from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent

        try:
            result = self._automated_curation.get_guid_for_name(name)
        except Exception as exc:
            if lookup_exception_is_absent(exc):
                return ""
            raise EgeriaFileSystemSurveyorError(lookup_could_not_be_made(name, exc)) from exc
        from resource_explorer.egeria_absence import guid_from_answer

        return guid_from_answer(result, name, EgeriaFileSystemSurveyorError)
