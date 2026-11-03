"""
Read-only access to Egeria's real Technology Type catalog (AutomatedCuration's
find_technology_types/get_tech_type_detail) — distinct from the RE-specific
additionalProperties.supported_technology_type convention used to tag Survey
Definitions (see survey_definition_reader.py).

This is the source of ground-truth technology names (e.g. "PostgreSQL
Relational Database", not "PostgreSQL Database") and of what a native Egeria
survey action service actually produces (get_tech_type_detail's
resourceList/governanceActionProcesses[].specification.producedAnnotationType),
confirmed live 2026-07-08. Process results in-memory per platform+server —
these calls are workspace-wide catalog metadata that doesn't change often
within a session, so callers should share one instance rather than refetch
per request.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

log = logging.getLogger(__name__)


#: How long a cached answer is served (seconds), and how many answers one catalog keeps (per cache). Same TTL as
#: the survey-definition candidates cache; the bound keeps a process-lifetime instance from growing with every
#: reader who ever asked.
CACHE_TTL_SECONDS = 300
CACHE_MAX_ENTRIES = 256


class _TTLCache:
    """A small per-instance cache: entries expire after `ttl`, and past `max_entries` the oldest goes first."""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self.ttl, self.max_entries = ttl, max_entries
        self._d: dict = {}
        self._lock = threading.Lock()

    _MISS = object()

    def get(self, key, default=_MISS):
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                return default
            if time.monotonic() - hit[0] >= self.ttl:
                self._d.pop(key, None)
                return default
            return hit[1]

    def __contains__(self, key) -> bool:
        return self.get(key) is not self._MISS

    def __getitem__(self, key):
        got = self.get(key)
        if got is self._MISS:
            raise KeyError(key)
        return got

    def __setitem__(self, key, value) -> None:
        with self._lock:
            self._d.pop(key, None)
            self._d[key] = (time.monotonic(), value)
            while len(self._d) > self.max_entries:
                self._d.pop(next(iter(self._d)))

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)


class EgeriaTechTypeCatalogError(RuntimeError):
    """Raised when Egeria Technology Type catalog operations fail."""


class EgeriaTechTypeCatalog:
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
        #: A client handed in from outside (tests) only; otherwise each read builds its own for whoever
        #: is asking (`_reader`), never held on the instance.
        self._automated_curation = None
        #: The answers, kept PER READER (the account Egeria answers for): an instance can be module-level
        #: and serve every signed-in person, so one person's answer is never served to another
        #: (caller-identity reads, 2026-10-14: a read runs as the caller and returns what the caller may see).
        #: Each answer expires after `CACHE_TTL_SECONDS`; each cache holds at most `CACHE_MAX_ENTRIES`.
        self._all_types_cache = _TTLCache()
        self._detail_cache = _TTLCache()

    def _reader(self) -> tuple:
        """(client, answer key) for whoever is asking NOW, resolved in this call.

        A catalog instance can be module-level (`web/routes/survey_definitions.py`) and serve
        concurrent requests, so neither the client nor the answers are pinned to an instance slot
        another request could rebind between this request's bind and its read: each read uses the
        client returned here, for this call's principal (the factory's per-request scope makes it
        one lookup, not one token, per call; Brief I), and caches under this principal's key.
        A client handed in from outside (tests) is kept as is, under the key None."""
        from resource_explorer.egeria_clients import current_principal

        if self._automated_curation is not None:
            return self._automated_curation, None      # handed in from outside: not ours to rebind
        principal = current_principal()
        if not self.platform_url:
            raise EgeriaTechTypeCatalogError(
                "EGERIA_PLATFORM_URL is not set. Set it in .env or pass platform_url=."
            )
        try:
            from pyegeria import AutomatedCuration

            from resource_explorer.egeria_clients import egeria_client

            client = egeria_client(
                principal, purpose="technology types", view_server=self.view_server,
                platform_url=self.platform_url).of(AutomatedCuration)
        except PermissionError:
            raise
        except ImportError as exc:
            raise EgeriaTechTypeCatalogError("pyegeria is not installed.") from exc
        except Exception as exc:
            raise EgeriaTechTypeCatalogError(
                f"Could not connect to Egeria at {self.platform_url}: {exc}"
            ) from exc
        # Who Egeria answers for: the account the client is built for (a person, or the daemon).
        return client, (principal.kind, principal.client_user or principal.user_id)

    def connect(self) -> None:
        """Check that a client can be built for whoever is asking now (raises as `_reader` does)."""
        self._reader()

    def list_technology_types(self, search: str = "*", refresh: bool = False) -> list[dict]:
        """All registered Technology Types (or a filtered subset if search is
        narrower than "*"), each a dict with technologyTypeGUID/qualifiedName/
        displayName/description/category. Cached per (search) after first call."""
        client, key = self._reader()
        if search == "*" and not refresh:
            hit = self._all_types_cache.get(key, None)
            if hit is not None:
                return hit

        try:
            result = client.find_technology_types(
                search_string=search, output_format="JSON"
            )
        except Exception as exc:
            raise EgeriaTechTypeCatalogError(f"find_technology_types({search!r}) failed: {exc}") from exc

        types = result if isinstance(result, list) else []
        if search == "*":
            self._all_types_cache[key] = types
        return types

    def get_tech_type_detail(self, display_name: str, refresh: bool = False) -> dict | None:
        """Full detail for one Technology Type by its exact real display name
        (e.g. "PostgreSQL Relational Database") — includes catalogTemplates,
        governanceActionProcesses, and resourceList. Returns None if Egeria has
        no Technology Type registered under that exact name. Cached per name."""
        client, who = self._reader()
        key = (who, display_name)
        if not refresh:
            hit = self._detail_cache.get(key, _TTLCache._MISS)
            if hit is not _TTLCache._MISS:
                return hit

        try:
            result = client.get_tech_type_detail(display_name, output_format="JSON")
        except Exception as exc:
            raise EgeriaTechTypeCatalogError(
                f"get_tech_type_detail({display_name!r}) failed: {exc}"
            ) from exc

        detail = result if isinstance(result, dict) else None
        self._detail_cache[key] = detail
        return detail

    def get_produced_annotation_types(self, display_name: str) -> list[dict]:
        """What a native Egeria survey/catalog process for this Technology Type
        actually produces, deduplicated by name — pulled from the real
        specification.producedAnnotationType lists in resourceList and
        governanceActionProcesses. Each entry: name, description, explanation,
        annotation_type (the real openMetadataTypeName, e.g.
        "ResourceMeasureAnnotation"), analysis_step_name. Empty list if the
        Technology Type isn't found or documents nothing."""
        detail = self.get_tech_type_detail(display_name)
        if not detail:
            return []

        seen: dict[str, dict] = {}
        for source_key in ("resourceList", "governanceActionProcesses"):
            for entry in detail.get(source_key) or []:
                spec = entry.get("specification") or {}
                for pat in spec.get("producedAnnotationType") or []:
                    name = pat.get("name", "")
                    if not name or name in seen:
                        continue
                    seen[name] = {
                        "name": name,
                        "description": pat.get("description", ""),
                        "explanation": pat.get("explanation", ""),
                        "annotation_type": pat.get("openMetadataTypeName", ""),
                        "analysis_step_name": pat.get("analysisStepName", ""),
                    }
        return list(seen.values())
