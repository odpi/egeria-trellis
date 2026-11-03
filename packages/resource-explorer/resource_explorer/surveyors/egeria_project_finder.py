"""
Read-only search over Egeria's real Project catalog (pyegeria's
ProjectManager.find_projects) — backs Part 5 of
docs/discovery-automate-project-context-plan.md, the "pick an existing
Egeria Project" option in the Egeria Project context picker.

`PersonalProject`/`Campaign`/`StudyProject`/`Task` are all classifications
on one generic Egeria `Project` entity type, not separate types (confirmed
by reading pyegeria's actual body construction, not inferred from naming)
— find_projects() surfaces all of them together, undifferentiated by
classification, which matches the picker's "any existing project" search.

Deliberately does NOT implement get_linked_projects(actor_guid) ("projects
I'm a member of") — that needs a real actor GUID for "the current user,"
and this codebase has no per-user auth (every request connects to Egeria
as the same fixed service-account identity from config/.env, confirmed via
web/routes/egeria.py's whoami docstring). Resolving "which Egeria Actor
element is the current session" is its own real piece of work, not solved
here — flagged as a follow-up, not silently faked with the service
account's own identity (which would misrepresent whose memberships are
being shown). Text search via find_projects() works for anyone regardless
of identity and covers the picker's actual need today.

Mirrors egeria_tech_type_catalog.py's exact connect-lazily/fail-soft
pattern (frozen constructor args from config, one shared instance per
caller rather than refetch-per-request).
"""
from __future__ import annotations

import logging
import os
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

log = logging.getLogger(__name__)


class EgeriaProjectFinderError(RuntimeError):
    """Raised when Egeria Project search operations fail."""


class EgeriaProjectFinder:
    def __init__(
        self,
        platform_url: str | None = None,
        view_server: str | None = None,
        user_id: str | None = None,
        user_password: str | None = None,
    ) -> None:
        self.platform_url = platform_url or egeria_platform_url()
        self.view_server = view_server or egeria_view_server()
        self.user_id = user_id or os.getenv("EGERIA_USER_ID", "erinoverview")
        self.user_password = user_password or ""   # not used to authenticate (Brief I)
        self._project_manager = None

    def connect(self) -> None:
        if self._project_manager is not None:
            return
        if not self.platform_url:
            raise EgeriaProjectFinderError(
                "EGERIA_PLATFORM_URL is not set. Set it in .env or pass platform_url=."
            )
        try:
            from pyegeria import ProjectManager

            from resource_explorer.egeria_clients import current_principal, egeria_client

            self._project_manager = egeria_client(
                current_principal(), purpose="find projects", view_server=self.view_server,
                platform_url=self.platform_url).of(ProjectManager)
        except PermissionError:
            raise
        except ImportError as exc:
            raise EgeriaProjectFinderError("pyegeria is not installed.") from exc
        except Exception as exc:
            raise EgeriaProjectFinderError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc

    def search_projects(self, search_string: str = "*", limit: int = 20) -> list[dict]:
        """Real Egeria Projects (any classification — PersonalProject,
        Campaign, StudyProject, Task, or unclassified) whose name/
        qualifiedName matches search_string. Each result:
        {guid, qualified_name, display_name, description}. Empty list on
        no matches; raises EgeriaProjectFinderError on a real connection/
        API failure (callers decide whether that's fatal to their flow)."""
        self.connect()
        try:
            result = self._project_manager.find_projects(
                search_string=search_string, page_size=limit, output_format="JSON"
            )
        except Exception as exc:
            raise EgeriaProjectFinderError(f"find_projects({search_string!r}) failed: {exc}") from exc

        elements = result if isinstance(result, list) else []
        projects = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            header = el.get("elementHeader", {}) or {}
            props = el.get("properties", el) or {}
            guid = header.get("guid", "")
            qn = props.get("qualifiedName", "")
            if not guid and not qn:
                continue
            projects.append({
                "guid": guid,
                "qualified_name": qn,
                "display_name": props.get("displayName") or props.get("name") or qn,
                "description": props.get("description", ""),
            })
        return projects
