"""
Read-only counterpart to EgeriaPublisher.

Pulls survey reports and annotations from Egeria, returning plain dicts
suitable for the CLI and web UI.  No SurveyResult dependency.

Primary data source for the survey list is the local SQLite registry
(fast, no Egeria connection needed).  Egeria is contacted only when
annotation details are requested (--full flag) or when the registry has
no data for the project.
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

if TYPE_CHECKING:
    from resource_explorer.registry import ProjectRegistry

log = logging.getLogger(__name__)

_DEFAULT_PLATFORM_URL = "https://localhost:9443"
_DEFAULT_VIEW_SERVER = "qs-view-server"
_DEFAULT_USER = "erinoverview"


class EgeriaReaderError(RuntimeError):
    """Raised when a required Egeria connection is unavailable."""


class EgeriaReader:
    """Read survey reports and annotations from Egeria.

    Parameters
    ----------
    platform_url  : Egeria platform URL (falls back to EGERIA_PLATFORM_URL env var)
    view_server   : Egeria view server name
    user_id       : Egeria user id
    user_password : Egeria user password
    registry      : Optional ProjectRegistry — used for the local cache path
    """

    def __init__(
        self,
        platform_url: str | None = None,
        view_server: str | None = None,
        user_id: str | None = None,
        user_password: str | None = None,
        registry: "ProjectRegistry | None" = None,
    ) -> None:
        self.platform_url = platform_url or egeria_platform_url()
        self.view_server = view_server or egeria_view_server()
        self.user_id = user_id or os.getenv("EGERIA_USER", _DEFAULT_USER)
        self.user_password = user_password or ""   # not used to authenticate (Brief I)
        self.registry = registry
        self._asset_maker = None
        self._discovery = None

    # ── connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Establish pyegeria client connections (lazy — called only when Egeria data needed)."""
        if self._asset_maker is not None:
            return
        if not self.platform_url:
            raise EgeriaReaderError(
                "EGERIA_PLATFORM_URL is not set. "
                "Set it in .env or pass platform_url= to EgeriaReader."
            )
        try:
            from pyegeria import AssetMaker
            from pyegeria.omvs.data_discovery import DataDiscovery

            from resource_explorer.egeria_clients import current_principal, egeria_client

            # Who (Brief I): the signed-in Caller on a route, the declared daemon job otherwise.
            clients = egeria_client(current_principal(), purpose="egeria read",
                                    view_server=self.view_server, platform_url=self.platform_url)
            self._asset_maker = clients.of(AssetMaker)
            self._discovery = clients.of(DataDiscovery)
        except PermissionError:
            raise
        except ImportError as exc:
            raise EgeriaReaderError("pyegeria is not installed.") from exc
        except Exception as exc:
            raise EgeriaReaderError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc

    # ── asset lookup ──────────────────────────────────────────────────────────

    def find_asset_guid(self, github_url: str) -> str | None:
        """Return the Egeria GUID of the repository Asset for the given GitHub URL.

        Searches by the qualifiedName prefix `GitHubRepository::{github_url}`
        (the repository is a plain Asset since the 2026-09-14
        SourceControlLibrary correction -- see egeria_publisher.py's module
        docstring). Returns None if not found.
        """
        self.connect()
        qualified_name = f"GitHubRepository::{github_url}"
        try:
            results = self._asset_maker.find_assets(
                search_string=qualified_name,
                starts_with=True,
                ignore_case=False,
                output_format="JSON",
            )
            if isinstance(results, list) and results:
                return results[0].get("elementHeader", {}).get("guid")
        except Exception as exc:
            log.debug("find_asset_guid search failed: %s", exc)
        return None

    # ── survey report listing ─────────────────────────────────────────────────

    def get_survey_reports_from_registry(self, slug: str) -> list[dict]:
        """Return published surveys from the local SQLite registry (no Egeria needed).

        Each item: {project_slug, surveyed_at, egeria_report_guid, published_at, annotation_count}
        """
        if self.registry is None:
            return []
        return self.registry.get_egeria_surveys(slug)

    def get_survey_reports_from_egeria(self, slug: str) -> list[dict]:
        """Search Egeria for SurveyReport assets linked to this project slug.

        Falls back path when the local registry has no data (e.g., published from
        another machine).  Each item: {guid, qualified_name, display_name, surveyed_at,
        annotation_count, description}
        """
        self.connect()
        search_prefix = f"SurveyReport::GitHubRepo::{slug}::"
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
                # qualifiedName: SurveyReport::GitHubRepo::{slug}::{ts}
                ts = qn.split("::")[-1] if "::" in qn else ""
                reports.append({
                    "guid": header.get("guid", ""),
                    "qualified_name": qn,
                    "display_name": props.get("displayName", ""),
                    "surveyed_at": ts,
                    "annotation_count": _safe_int(additional.get("annotation_count")),
                    "error_count": _safe_int(additional.get("error_count")),
                    "description": props.get("description", ""),
                })
        except Exception as exc:
            log.debug("get_survey_reports_from_egeria failed: %s", exc)
        # Newest first
        reports.sort(key=lambda r: r.get("surveyed_at", ""), reverse=True)
        return reports

    # ── annotation retrieval ──────────────────────────────────────────────────

    def get_annotations(self, slug: str, surveyed_at: str) -> list[dict]:
        """Return all annotations for a specific survey run from Egeria.

        Searches by qualifiedName prefix `Annotation::{slug}::{surveyed_at}::`.
        Each item: {guid, annotation_type, summary, confidence, analysis_step,
                    explanation, expression, json_properties, subtype_data}
        """
        self.connect()
        search_prefix = f"Annotation::{slug}::{surveyed_at}::"
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
                annotations.append(_parse_annotation(header, props))
        except Exception as exc:
            log.debug("get_annotations failed: %s", exc)
        return annotations

    def get_full_report(self, slug: str, surveyed_at: str, report_guid: str) -> dict:
        """Return report metadata + all annotations in one dict.

        {guid, slug, surveyed_at, annotations: list[dict]}
        """
        annotations = self.get_annotations(slug, surveyed_at)
        return {
            "guid": report_guid,
            "project_slug": slug,
            "surveyed_at": surveyed_at,
            "annotation_count": len(annotations),
            "annotations": annotations,
        }


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_annotation(header: dict, props: dict) -> dict:
    """Normalise a raw Egeria annotation element into a plain dict."""
    from resource_explorer.surveyors.egeria_survey_reader import parse_json_properties
    json_props = parse_json_properties(props.get("jsonProperties"))

    # Subtype-specific native fields gathered into a single dict
    subtype_data: dict = {}
    for field in (
        "candidateClassifications",
        # ClassificationAnnotation's own Egeria fields (6.2): the proposed classification and its values.
        "classificationName",
        "properties",
        "resourceProperties",
        "qualityScores",
        "candidateDataClassGUIDs",
        "actionRequested",
        "actionTargetName",
        "schemaName",
        "schemaType",
        "relatedEntityName",
        "relationshipTypeName",
    ):
        val = props.get(field)
        if val is not None:
            subtype_data[field] = val

    # A QualityAnnotation's score map rides in jsonProperties (Egeria's bean has no field for it); readers of the
    # old native key find it where they always looked.
    if "qualityScores" not in subtype_data and isinstance(json_props, dict):
        from resource_explorer.surveyors.annotation_props import QUALITY_SCORES_KEY

        carried = json_props.get(QUALITY_SCORES_KEY)
        if isinstance(carried, dict):
            subtype_data["qualityScores"] = carried

    # A proposal published in Egeria's own fields reads back as the candidates it is, for readers of the old key:
    # the whole ordered list from `properties` when it proposed several, else its one `classificationName`
    # (annotation_props.classification_fields writes both).
    if "candidateClassifications" not in subtype_data:
        from resource_explorer.surveyors.annotation_props import CANDIDATES_KEY

        values = subtype_data.get("properties")
        listed = values.get(CANDIDATES_KEY) if isinstance(values, dict) else None
        if isinstance(listed, str):
            try:
                listed = json.loads(listed)
            except ValueError:
                listed = None
        if isinstance(listed, list) and listed:
            subtype_data["candidateClassifications"] = [str(c) for c in listed]
        elif subtype_data.get("classificationName"):
            subtype_data["candidateClassifications"] = [subtype_data["classificationName"]]

    return {
        "guid": header.get("guid", ""),
        "type_name": header.get("type", {}).get("typeName", ""),
        "annotation_type": props.get("annotationType", ""),
        "summary": props.get("summary", ""),
        "confidence": props.get("confidence"),
        "analysis_step": props.get("analysisStep", ""),
        "additional_properties": props.get("additionalProperties") or {},
        "explanation": props.get("explanation", ""),
        "expression": props.get("expression", ""),
        "json_properties": json_props,
        "subtype_data": subtype_data,
        "source": "egeria",
    }
