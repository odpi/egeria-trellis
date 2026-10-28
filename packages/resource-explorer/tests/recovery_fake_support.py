"""The bulk read surface a duck-typed fake registry needs for the recovery readers.

`repo_survey_definition_adapter._recovery_snapshot` reads architecture-recovery rows through
`query_findings_all_runs_by_scope` / `query_metrics_by_scope` and keys its cache on
`analysis_data_fingerprint`. A fake that only implements the old per-scope methods inherits
this mixin, which derives the bulk answers FROM those per-scope methods -- so the fake's own
data and the fake's own per-scope semantics stay the single source of truth -- and reports a
fingerprint that never repeats, so a fake is never served from the cache (a fake has no data
version to key on, and a stale hit across two tests would be exactly the bug the cache's
tests exist to catch).
"""
from __future__ import annotations

import itertools

_instances = itertools.count(1)
_fingerprints = itertools.count(1)


class RecoveryBulkSurface:
    def _bulk_scopes(self, slug: str, kind: str):
        """Every scope the fake holds rows for under `kind`. Override."""
        raise NotImplementedError

    @property
    def database_url(self) -> str:
        if not hasattr(self, "_fake_url"):
            self._fake_url = f"fake://registry/{next(_instances)}"
        return self._fake_url

    @staticmethod
    def _normalize_slug(slug: str) -> str:
        return slug

    def analysis_data_fingerprint(self, slug, kinds):
        return ("fake", next(_fingerprints))

    def query_findings_all_runs_by_scope(self, slug, kind, order_class=None):
        return {s: self.query_findings_all_runs(slug, kind, s) for s in self._bulk_scopes(slug, kind)}

    def query_metrics_by_scope(self, slug, kind):
        out = {}
        query_metrics = getattr(self, "query_metrics", None)
        if query_metrics is None:        # a fake for a reader that never looked at metrics
            return out
        for s in self._bulk_scopes(slug, kind):
            m = query_metrics(slug, kind, s)
            if m:
                out[s] = m
        return out
