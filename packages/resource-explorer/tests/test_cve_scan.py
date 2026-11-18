"""A CVE scan that says what it could not check.

Two findings from building this against real data, both preserved as tests
because either would ship a confident wrong answer:

* OSV returns EVERY advisory for a package when it cannot parse the version.
  numpy 1.23.5 is clean; the version as the manifest parser stores it
  ("==1.23.5 \\") returned 13. Across one real repo this was 45 reported
  advisories where the truth was 2.
* "No dependencies recorded" is not "no vulnerable dependencies". A repo whose
  manifests were never parsed would otherwise come back clean.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from resource_explorer.surveyors.sub_surveyors.cve_scan import (
    ECOSYSTEM_MAP,
    _severity_of,
    build_queries,
    normalise_version,
    query_osv,
)


class TestVersionNormalisation:
    """The validation that stops a maximal false positive."""

    @pytest.mark.parametrize("raw,expected", [
        ("1.23.5", "1.23.5"),
        ("==1.23.5", "1.23.5"),
        ("==1.23.5 \\", "1.23.5"),          # requirements-file line continuation
        ("v0.30.0", "0.30.0"),
        ("1.2.3-rc1", "1.2.3-rc1"),
        # An environment marker narrows WHERE a pin applies, not WHAT it pins.
        # 8.1.3 is still the installed version wherever the marker is true, so
        # refusing to query it would lose real coverage for no safety gain.
        ('==8.1.3 ; python_version>"3"', "8.1.3"),
    ])
    def test_a_pinned_version_survives(self, raw, expected):
        assert normalise_version(raw) == expected

    @pytest.mark.parametrize("raw", [
        ">=1.0,<2.0",      # a range: no single installed version to ask about
        "^4.17",           # npm caret
        "~=2.1",
        "*",
        "",
        "garbage-not-a-version",
    ])
    def test_anything_unpinned_is_refused(self, raw):
        """Refused, not best-effort. Sending an unparseable version to OSV
        returns every advisory for the package, so a near-miss here becomes a
        page of vulnerabilities the project does not have."""
        assert normalise_version(raw) == ""


class TestCoverage:
    def test_an_unqueryable_dependency_is_counted_and_explained(self):
        deps = [
            {"dep_name": "numpy", "dep_version": "==1.23.5 \\", "ecosystem": "python"},
            {"dep_name": "left-pad", "dep_version": "^1.0", "ecosystem": "javascript"},
            {"dep_name": "mystery", "dep_version": "1.0.0", "ecosystem": "cobol"},
            {"dep_name": "", "dep_version": "1.0.0", "ecosystem": "python"},
        ]
        queries, pairs, unqueryable = build_queries(deps)
        assert len(queries) == 1 and len(pairs) == 1
        assert len(unqueryable) == 3
        reasons = " ".join(u["reason"] for u in unqueryable)
        assert "range or unparseable" in reasons
        assert "not queryable against OSV" in reasons
        assert "no package name" in reasons

    def test_an_unmapped_ecosystem_is_not_silently_dropped(self):
        """A dependency nobody checked must not be indistinguishable from one
        that came back clean -- and Cargo is not in the parser's output at all,
        so a Rust repo would otherwise scan clean having scanned nothing."""
        assert "rust" in ECOSYSTEM_MAP           # mapped, ready for when it parses
        _, _, unqueryable = build_queries(
            [{"dep_name": "serde", "dep_version": "1.0.0", "ecosystem": "elixir"}])
        assert len(unqueryable) == 1


class TestQueryOsv:
    @staticmethod
    def _opener(payload):
        @contextmanager
        def _open(_req):
            class _R:
                @staticmethod
                def read():
                    return json.dumps(payload).encode()
            yield _R()
        return _open

    def test_a_network_failure_returns_an_error_not_an_empty_result(self):
        """"We could not ask" and "we asked and nothing came back" are opposite
        answers. Returning [] here would become a clean bill of health."""
        @contextmanager
        def _boom(_req):
            raise OSError("connection refused")
            yield  # pragma: no cover

        results, error = query_osv([{"package": {"name": "x", "ecosystem": "npm"},
                                     "version": "1.0.0"}], opener=_boom)
        assert results == []
        assert "OSError" in error

    def test_a_length_mismatch_refuses_to_pair_positionally(self):
        """OSV answers positionally. Pairing a short response would attribute
        one package's vulnerabilities to another -- worse than no answer."""
        results, error = query_osv(
            [{"package": {"name": "a", "ecosystem": "npm"}, "version": "1"},
             {"package": {"name": "b", "ecosystem": "npm"}, "version": "1"}],
            opener=self._opener({"results": [{"vulns": []}]}),
        )
        assert "positional pairing would be unsafe" in error

    def test_the_default_sender_is_httpx_and_an_error_status_is_an_error(self, monkeypatch):
        """TR-15: the real sender goes through httpx (certifi), and OSV answering 503 is "we could not ask", as
        urlopen's HTTPError was, never an empty result."""
        import httpx

        from resource_explorer.surveyors.sub_surveyors import cve_scan

        sent = []

        def fake_request(method, url, **kw):
            sent.append((method, url, kw.get("headers")))
            return httpx.Response(503, request=httpx.Request(method, url))

        monkeypatch.setattr(cve_scan.httpx, "request", fake_request)
        results, error = query_osv([{"package": {"name": "x", "ecosystem": "npm"}, "version": "1.0.0"}])
        assert results == [] and "HTTPStatusError" in error
        assert sent[0][0] == "POST" and sent[0][2] == {"Content-Type": "application/json"}

    def test_no_queries_is_not_an_error(self):
        assert query_osv([]) == ([], "")


class TestSeverity:
    def test_a_cvss_vector_is_not_treated_as_a_rating(self):
        """OSV's severity field carries a CVSS VECTOR, not a word. Matching
        HIGH/CRITICAL inside it finds nothing, and inventing a rating from it
        would mean computing a base score -- defined, but wrong-in-detail is
        worse than unrated on a security finding."""
        vector = "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:C/C:H/I:H/A:H"
        assert _severity_of({"severity": [{"type": "CVSS_V3", "score": vector}]}) == vector

    def test_a_named_rating_is_preferred_when_published(self):
        assert _severity_of({
            "database_specific": {"severity": "MODERATE"},
            "severity": [{"score": "CVSS:3.1/AV:N"}],
        }) == "MODERATE"

    def test_no_severity_at_all_is_empty_not_a_guess(self):
        """Plenty of advisories publish none -- GO-2026-5024 among them."""
        assert _severity_of({}) == ""
