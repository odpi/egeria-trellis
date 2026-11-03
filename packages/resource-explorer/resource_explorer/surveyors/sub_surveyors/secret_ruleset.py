"""Loader and matcher for the vendored gitleaks ruleset — the engine
`repo_secret_scan` (`secret_scan.py`) drives. Split out of the surveyor
module itself so the ruleset-loading/matching/self-test concerns (which
have nothing to do with `StepOutcome`/`Annotation`/registry persistence)
are independently testable and, per design §1, independently swappable —
"an organisation with its own compliance requirements must be able to
point this codebase at a different ruleset without touching RE code": the
`SecretRuleset` this module builds is parameterised entirely by
`toml_path`, so pointing at a different vendored file is a config change,
not a code change.

**Vendoring shape chosen: rules-only data file, not a shelled-out binary**
(design §1's two legitimate paths). The ruleset itself lives at
`resource_explorer/configdata/vendored/gitleaks/gitleaks.toml`, pulled
verbatim from upstream — see `PROVENANCE.md` next to it for the exact
commit/date/license this was pulled at. This module reads it with the
stdlib `tomllib` (no new dependency) and matches with Python's own `re`
engine (not RE2, which is what gitleaks itself uses — a real, named
difference: a small number of gitleaks' 222 regexes could in principle
behave differently under backtracking `re` than under RE2's linear-time
engine, most plausibly as a performance difference on pathological input
rather than a correctness one, since none of the vendored regexes were
authored assuming RE2-only syntax. Not measured here; flagged in the
task's final report as an open, non-blocking risk).

**keywords are gitleaks' own pre-filter, reused for the same reason
gitleaks uses them: 222 regexes run once per non-excluded file, without a
keyword pre-filter, is a full regex sweep per file. Each rule's `keywords`
(lower-cased literal substrings from its own value's context) already ship
in the vendored TOML; a rule is even attempted only when at least one of
its keywords appears in the file content (case-insensitively) first.

**Measured, not assumed: 26 of the 222 vendored rules fail to compile
under Python's `re` and are skipped, loudly (one `log.warning` per rule,
naming it), not silently.** All 26 use syntax RE2 accepts and Python's
`re` does not — inline flags placed mid-pattern (`(?i)` other than at the
very start, which Python 3.11+ rejects outright) or a backslash-z-style anchor
`re` does not recognise. `load_ruleset()` drops exactly these and keeps
the other 196 (measured 2026-09-01, against the commit pinned in
`RULESET_VERSION` below) — the dropped rule ids are visible in this
module's own log output, not just this comment, so a future ruleset pull
that drops a different set is loud rather than a silent coverage
regression. This is the real, named cost of the "own regex engine" half of
the "vendored data file, own matcher" design choice — 196/222 (88%) rule
coverage today, not 100%, and worth surfacing per-rule if a future
iteration wants tighter parity (a Python-syntax rewrite of the 26, or a
switch to the `regex`/`re2`-bound third-party packages, neither attempted
here).
"""
from __future__ import annotations

import bisect
import logging
import math
import os
import re
import time
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_RULESET_PATH = (
    Path(__file__).resolve().parents[2] / "configdata" / "vendored" / "gitleaks" / "gitleaks.toml"
)

#: PROVENANCE.md's own recorded values — kept here too, deliberately
#: duplicated rather than parsed out of the markdown, because a markdown
#: file is for a human reader and this is what a running program needs to
#: stamp on every finding. If the vendored TOML is refreshed, update BOTH
#: this and PROVENANCE.md — see that file's own "Updating this vendored
#: copy" section.
RULESET_PROVIDER_NAME = "gitleaks"
RULESET_SOURCE_URL = "https://github.com/gitleaks/gitleaks"
#: The upstream commit that last touched config/gitleaks.toml at pull time
#: (2026-09-01) — this is the precise version pin (see PROVENANCE.md).
RULESET_VERSION = "09242ce9c8a60d9b051fc2d166f9e849b88c7ac0"
#: The date THAT COMMIT was made (2025-11-20), not the date it was pulled
#: into this repo — ruleset_freshness measures from when the rules
#: themselves were last changed upstream, not from when RE happened to
#: vendor them.
RULESET_AS_OF_DATE = "2025-11-20"

#: A vendored secret-pattern ruleset "wants a longer window ... closer to
#: 'has a new major version shipped upstream' than to a calendar cutoff"
#: (design §1) — left as a real decision for whoever tunes this, not
#: resolved here to a specific number backed by data. 180 days (~6 months)
#: is a deliberately longer window than security_summary's 30-day one,
#: chosen because gitleaks ships new rules on roughly a monthly cadence
#: historically, so 30 days would flag "stale" on nearly every real run.
RULESET_STALENESS_THRESHOLD_DAYS = 180


# ── Scan bounds ─────────────────────────────────────────────────────────
#
# Measured 2026-10-03 on docling-core (653 scanned files, 115 MB): ONE
# 12.9 MB test-data JSON file (tests/data/doc/concatenated.json, longest line
# 1.3 MB) cost ~287 s, of which ~261 s was the single rule `cohere-api-token`
# and ~20 s `atlassian-api-token`, both with ZERO raw matches. The cause is the
# shape of those gitleaks regexes, not catastrophic backtracking: they open
# with a lazily-bounded `[\w.-]{0,50}?` (cohere: TWO nested ones, ~50x50 tries)
# that restarts at EVERY character of every word-character run, and the
# keyword pre-filter ("cohere" appearing ANYWHERE in the 12.9 MB file) admits
# the rule for the whole file. Cost scales with file size x run length, not
# with matches, which is why throughput varied ~100x between repos of similar
# total size. See docs/design-notes/implemented/SECRET-SCAN-BOUNDS-IMPLEMENTED.md.
#
# Python `re` cannot be interrupted and `regex` is not a declared dependency
# of this package, so the bound is enforced by (a) scanning big files in
# bounded windows, each keyword-gated on its OWN text (this alone removes the
# measured cost), and (b) checking a monotonic deadline before every rule x
# window call. One call is bounded by the window size, so a budget can be
# overshot by at most one call (seconds), never by the file.

#: Files at or under this many characters are scanned whole, exactly as before
#: the bounds existed (byte-identical results). Larger files are windowed.
WHOLE_FILE_CHARS = 256 * 1024
#: Window and overlap for files over WHOLE_FILE_CHARS. A match is attributed to
#: the window its START falls in; the overlap lets a match that crosses the
#: border still be seen whole. A match longer than the overlap that straddles a
#: border is missed (a private key block is ~1.7-3.2 KB; the overlap is 8 KB).
WINDOW_CHARS = 16 * 1024
WINDOW_OVERLAP_CHARS = 4 * 1024
#: How many bytes of a file are inspected for a NUL to call it binary (git's own
#: heuristic looks at the first 8000 bytes).
BINARY_SNIFF_BYTES = 8192

DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_FILE_BUDGET_SECONDS = 30.0
DEFAULT_TOTAL_BUDGET_SECONDS = 900.0

ENV_MAX_FILE_BYTES = "RE_SECRET_SCAN_MAX_FILE_BYTES"
ENV_FILE_BUDGET_SECONDS = "RE_SECRET_SCAN_FILE_BUDGET_SECONDS"
ENV_TOTAL_BUDGET_SECONDS = "RE_SECRET_SCAN_TOTAL_BUDGET_SECONDS"

#: How many skipped paths a report keeps for display (largest / slowest first).
TOP_SKIPPED_PATHS = 10


def _env_number(name: str, default, cast):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        value = None
    if value is None or value <= 0:
        log.warning("secret_ruleset: %s=%r is not a positive number — using the "
                    "default %s", name, raw, default)
        return default
    return value


@dataclass(frozen=True)
class ScanBounds:
    """The scanner's own limits. Lives here, not in the Prefect dispatcher,
    so the in-process (whole-definition) path is bounded too.

    Defaults, and why (profile of 2026-10-03, see the module note above):

    - `max_file_bytes` 5 MiB: above this a file is a data dump or a bundle, not
      source; docling-core's five biggest files (5.5-12.9 MB) are all test-data
      JSON/HTML. The skip is recorded and makes the scan PARTIAL.
    - `file_budget_seconds` 30: ~30x the ~1 s a 256 KiB source file takes under
      the worst rule; a file over it keeps the matches found so far.
    - `total_budget_seconds` 900: under the Prefect step ceiling (1200 s) and
      ~3x the slowest healthy scan on record (egeria_git, 277 s).

    Override with RE_SECRET_SCAN_MAX_FILE_BYTES, RE_SECRET_SCAN_FILE_BUDGET_SECONDS,
    RE_SECRET_SCAN_TOTAL_BUDGET_SECONDS (read when `from_environment()` runs, i.e.
    once per scan).
    """
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    file_budget_seconds: float = DEFAULT_FILE_BUDGET_SECONDS
    total_budget_seconds: float = DEFAULT_TOTAL_BUDGET_SECONDS

    @classmethod
    def from_environment(cls) -> "ScanBounds":
        return cls(
            max_file_bytes=_env_number(ENV_MAX_FILE_BYTES, DEFAULT_MAX_FILE_BYTES, int),
            file_budget_seconds=_env_number(
                ENV_FILE_BUDGET_SECONDS, DEFAULT_FILE_BUDGET_SECONDS, float),
            total_budget_seconds=_env_number(
                ENV_TOTAL_BUDGET_SECONDS, DEFAULT_TOTAL_BUDGET_SECONDS, float),
        )

    def as_dict(self) -> dict:
        return {"max_file_bytes": self.max_file_bytes,
                "file_budget_seconds": self.file_budget_seconds,
                "total_budget_seconds": self.total_budget_seconds}


@dataclass
class ScanReport:
    """Everything a scan did NOT do, as stored facts. A summary label is
    derived from this, never from the code path taken."""
    files_scanned: int = 0          # fully scanned
    files_excluded: int = 0         # excluded by the ruleset's own path allowlist
    skipped_size: list = field(default_factory=list)      # [(path, bytes)]
    skipped_binary: list = field(default_factory=list)    # [(path, bytes)]
    #: Files whose per-file budget ran out: scanned only partway, matches so far kept.
    timed_out: list = field(default_factory=list)         # [(path, bytes, units_done, units_total)]
    #: Files never opened because the total budget was already spent.
    not_reached: list = field(default_factory=list)       # [(path, bytes)]
    total_budget_hit: bool = False
    elapsed_seconds: float = 0.0
    bounds: ScanBounds = field(default_factory=ScanBounds)

    @property
    def partial(self) -> bool:
        """True when the scan did not read everything it was asked to. Binary
        files are counted but do not make a scan partial: they hold no text to
        match, and flagging every repo that contains a PNG would make the word
        meaningless."""
        return bool(self.skipped_size or self.timed_out or self.not_reached
                    or self.total_budget_hit)

    def counts(self) -> dict:
        return {
            "files_skipped_size": len(self.skipped_size),
            "files_skipped_binary": len(self.skipped_binary),
            "files_timed_out": len(self.timed_out),
            "files_not_reached_total_budget": len(self.not_reached),
            "total_budget_hit": self.total_budget_hit,
        }

    def detail(self) -> dict:
        """Persistable form. Paths and sizes only — never matched text."""
        def top(rows):
            return [list(r) for r in sorted(rows, key=lambda r: -r[1])[:TOP_SKIPPED_PATHS]]
        return {
            **self.counts(),
            "scan_bounds": self.bounds.as_dict(),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "top_skipped_size": top(self.skipped_size),
            "top_skipped_binary": top(self.skipped_binary),
            "top_timed_out": top(self.timed_out),
            "top_not_reached": top(self.not_reached),
        }


@dataclass(frozen=True)
class RuleAllowlist:
    """One `[[rules.allowlists]]` block.

    gitleaks' semantics, reproduced rather than approximated:

    - `regexTarget` selects what the regexes are tested against — "secret"
      (the default) is the capture group, "match" the whole matched text,
      "line" the containing line. We do not have the line at match time and
      do not fabricate one: a "line"-targeted allowlist is applied against
      the full match, which is a superset of the secret and the closest
      honest approximation. Recorded here rather than silently ignored.
    - `stopwords` match if the secret CONTAINS the stopword,
      case-insensitively — not equality. That is what makes 1,446 of them
      tractable to write.
    - Blocks are OR-ed with each other and, within a block, a hit on any
      regex or any stopword allowlists the match. `condition = "AND"` is
      accepted by gitleaks but used by no rule in the vendored file
      (verified at load time, warned about if that ever changes).
    """
    regexes: tuple[re.Pattern, ...]
    stopwords: tuple[str, ...]      # already lower-cased
    regex_target: str               # "secret" | "match" | "line"

    def allows(self, secret: str, whole_match: str) -> bool:
        target = whole_match if self.regex_target in ("match", "line") else secret
        if any(pat.search(target) for pat in self.regexes):
            return True
        if self.stopwords:
            lowered = secret.lower()
            if any(sw in lowered for sw in self.stopwords):
                return True
        return False


@dataclass(frozen=True)
class SecretRule:
    rule_id: str
    description: str
    pattern: re.Pattern
    keywords: tuple[str, ...]   # already lower-cased
    #: Minimum Shannon entropy of the extracted secret, from the rule's own
    #: `entropy = ` key. None means the rule declares none.
    #:
    #: Not implementing this was worth ~48,500 false positives on one repo
    #: (2026-09-01): `generic-api-key` declares entropy 3.5, and every
    #: `"key": "contact_methods"` in a dict literal matched its regex and
    #: was reported as a committed credential. 130 of the 222 vendored
    #: rules declare a threshold — a matcher that ignores it is not running
    #: the ruleset it claims to run, and says so in the provenance line.
    entropy: float | None = None
    #: Per-rule `[[rules.allowlists]]`, applied after the global ones.
    allowlists: tuple[RuleAllowlist, ...] = ()
    #: Which regex group holds the secret. gitleaks: `secretGroup` if set,
    #: else group 1 if the pattern has one, else the whole match.
    secret_group: int | None = None

    def extract_secret(self, m: re.Match) -> str:
        """The text the entropy and allowlist gates are applied to."""
        if self.secret_group is not None:
            try:
                return m.group(self.secret_group) or ""
            except (IndexError, re.error):
                return m.group(0)
        if m.re.groups >= 1:
            return m.group(1) or m.group(0)
        return m.group(0)


@dataclass(frozen=True)
class SecretMatch:
    rule_id: str
    description: str
    path: str
    line: int
    #: Truncated/masked — see mask_excerpt(). Never the raw matched text.
    excerpt: str
    #: Byte offset of the match within the file. The only field that separates
    #: two hits of the SAME rule on the SAME line — which happens: egeria_trellis
    #: has two `generic-api-key` matches at doc_sections.json:24201, on one long
    #: JSON line. Without it their published qualifiedNames collide and one is
    #: silently dropped on publish. Defaulted so existing constructions and
    #: fixtures keep working; every real scan sets it.
    offset: int = -1


@dataclass
class SecretRuleset:
    """A loaded, ready-to-scan ruleset. Construct via `load_ruleset()`."""

    rules: list[SecretRule]
    #: Compiled path-exclusion regexes from the TOML's own `[allowlist]`
    #: (gitleaks' own default ignore-paths — design §1: "most standard
    #: rulesets ... ship their own default ignore-paths that should be
    #: honoured rather than re-invented").
    excluded_path_patterns: list[re.Pattern]
    #: Global value-shaped noise regexes (placeholders, template tokens) —
    #: gitleaks' own `[allowlist].regexes`, applied to a candidate match's
    #: matched text before it counts as a hit.
    excluded_value_patterns: list[re.Pattern]
    source_path: Path

    def is_excluded_path(self, rel_path: str) -> bool:
        p = rel_path.replace("\\", "/")
        return any(pat.search(p) for pat in self.excluded_path_patterns)

    def _value_is_noise(self, value: str) -> bool:
        return any(pat.search(value) for pat in self.excluded_value_patterns)

    def scan_text(self, rel_path: str, text: str) -> list[SecretMatch]:
        """All matches in one file's content, unbounded. `rel_path` is used
        only for the excerpt's path field — the exclusion check itself is the
        caller's responsibility (scan_paths applies it before reading
        content at all, since an excluded path shouldn't even be opened).
        scan_paths uses `_scan_text_bounded` instead."""
        matches, _complete, _done, _total = self._scan_text_bounded(rel_path, text, None)
        return matches

    def _accept(self, rule: SecretRule, m: re.Match) -> bool:
        """The three per-rule gates gitleaks applies after its regex and
        before reporting. Skipping them is not "being cautious" — it is
        running a different, much noisier ruleset while reporting gitleaks'
        name and commit."""
        value = m.group(0)
        if self._value_is_noise(value):
            return False
        secret = rule.extract_secret(m)
        if rule.entropy is not None and shannon_entropy(secret) < rule.entropy:
            return False
        if any(al.allows(secret, value) for al in rule.allowlists):
            return False
        return True

    def _scan_text_bounded(
        self, rel_path: str, text: str, deadline: float | None,
    ) -> tuple[list[SecretMatch], bool, int, int]:
        """Returns (matches, complete, units_done, units_total), a unit being
        one rule x window call. `deadline` is a
        time.monotonic() value checked before every rule x window call; when
        it passes, scanning stops and the matches so far are returned with
        complete=False. Text up to WHOLE_FILE_CHARS is scanned whole (results
        identical to the pre-bounds scanner); larger text in overlapping
        windows, each keyword-gated on its own content."""
        n_rules = len(self.rules)
        newline_offsets: list[int] | None = None

        def line_of(offset: int) -> int:
            nonlocal newline_offsets
            if newline_offsets is None:
                newline_offsets = _newline_offsets(text)
            return bisect.bisect_left(newline_offsets, offset) + 1

        def make(rule: SecretRule, m: re.Match, base: int) -> SecretMatch:
            start = base + m.start()
            return SecretMatch(
                rule_id=rule.rule_id, description=rule.description,
                path=rel_path, line=line_of(start), excerpt=mask_excerpt(m.group(0)),
                offset=start,
            )

        matches: list[SecretMatch] = []
        if len(text) <= WHOLE_FILE_CHARS:
            lowered = text.lower()
            for i, rule in enumerate(self.rules):
                if deadline is not None and time.monotonic() > deadline:
                    return matches, False, i, n_rules
                if rule.keywords and not any(kw in lowered for kw in rule.keywords):
                    continue
                for m in rule.pattern.finditer(text):
                    if self._accept(rule, m):
                        matches.append(make(rule, m, 0))
            return matches, True, n_rules, n_rules

        # Windowed. Collected as (rule_index, offset, match) and sorted so the
        # output order matches the whole-file order (rule-major, then offset).
        found: list[tuple[int, int, SecretMatch]] = []
        windows = []
        pos = 0
        while pos < len(text):
            windows.append((pos, min(pos + WINDOW_CHARS, len(text))))
            pos += WINDOW_CHARS
        done_calls = 0
        total_calls = n_rules * len(windows)
        for w_start, w_end in windows:
            lo = max(0, w_start - WINDOW_OVERLAP_CHARS)
            hi = min(len(text), w_end + WINDOW_OVERLAP_CHARS)
            chunk = text[lo:hi]
            lowered = chunk.lower()
            for i, rule in enumerate(self.rules):
                if deadline is not None and time.monotonic() > deadline:
                    found.sort(key=lambda t: (t[0], t[1]))
                    return [t[2] for t in found], False, done_calls, total_calls
                done_calls += 1
                if rule.keywords and not any(kw in lowered for kw in rule.keywords):
                    continue
                for m in rule.pattern.finditer(chunk):
                    abs_start = lo + m.start()
                    if not (w_start <= abs_start < w_end):
                        continue   # belongs to the neighbouring window
                    if self._accept(rule, m):
                        found.append((i, abs_start, make(rule, m, lo)))
        found.sort(key=lambda t: (t[0], t[1]))
        return [t[2] for t in found], True, total_calls, total_calls

    def scan_paths(self, root: Path, rel_paths: list[str],
                   bounds: ScanBounds | None = None
                   ) -> tuple[list[SecretMatch], ScanReport]:
        """Scans every non-excluded path under `root`, bounded. Returns
        (matches, ScanReport). The report carries every file that was NOT
        fully read — by size, by binary content, by time budget, or because
        the total budget ran out first — so a caller cannot turn a bounded
        scan into a clean zero. `bounds` defaults to ScanBounds.from_environment().
        """
        bounds = bounds or ScanBounds.from_environment()
        report = ScanReport(bounds=bounds)
        matches: list[SecretMatch] = []
        t_start = time.monotonic()
        total_deadline = t_start + bounds.total_budget_seconds
        for idx, rel in enumerate(rel_paths):
            if self.is_excluded_path(rel):
                report.files_excluded += 1
                continue
            full = root / rel
            if time.monotonic() > total_deadline:
                report.total_budget_hit = True
                # Everything left is accounted for, not dropped.
                for rest in rel_paths[idx:]:
                    if self.is_excluded_path(rest):
                        report.files_excluded += 1
                        continue
                    try:
                        size = (root / rest).stat().st_size
                    except OSError:
                        continue
                    report.not_reached.append((rest, size))
                break
            try:
                if not full.is_file():
                    continue
                size = full.stat().st_size
                if size > bounds.max_file_bytes:
                    report.skipped_size.append((rel, size))
                    continue
                with full.open("rb") as fh:
                    head = fh.read(BINARY_SNIFF_BYTES)
                if b"\x00" in head:
                    report.skipped_binary.append((rel, size))
                    continue
                text = full.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                log.debug("secret_scan: could not read %s: %s", rel, exc)
                continue
            deadline = min(time.monotonic() + bounds.file_budget_seconds, total_deadline)
            file_matches, complete, done, total = self._scan_text_bounded(rel, text, deadline)
            matches.extend(file_matches)
            if complete:
                report.files_scanned += 1
            else:
                report.timed_out.append((rel, size, done, total))
                if time.monotonic() > total_deadline:
                    report.total_budget_hit = True
        report.elapsed_seconds = time.monotonic() - t_start
        if report.partial:
            log.warning(
                "secret_scan: PARTIAL scan — %s (budgets: %s)",
                report.counts(), bounds.as_dict())
        return matches, report

    def provider_info(self):
        from resource_explorer.surveyors.sub_surveyors.provider_meta import (
            VENDORED_RULESET,
            ProviderInfo,
        )
        return ProviderInfo(
            provider_name=RULESET_PROVIDER_NAME,
            provider_kind=VENDORED_RULESET,
            version_or_as_of=RULESET_VERSION,
            source_url=RULESET_SOURCE_URL,
        )


def _newline_offsets(text: str) -> list[int]:
    out: list[int] = []
    find = text.find
    i = find("\n")
    while i != -1:
        out.append(i)
        i = find("\n", i + 1)
    return out


def shannon_entropy(value: str) -> float:
    """Shannon entropy in bits per character — gitleaks' own measure.

    An empty string scores 0.0, which is below every declared threshold, so
    a rule whose capture group came back empty is dropped rather than
    admitted on a technicality.
    """
    if not value:
        return 0.0
    n = len(value)
    return -sum(
        (c / n) * math.log2(c / n)
        for c in Counter(value).values()
    )


def mask_excerpt(value: str, *, keep: int = 4, max_len: int = 40) -> str:
    """Truncates AND masks a matched value before it is ever persisted —
    design §1: "a survey step that persists the plaintext secret it just
    found would be creating the exact incident it exists to flag." Keeps
    only the first/last `keep` characters, unconditionally, regardless of
    how short the match is — a short match fully unmasked is still a
    stored secret."""
    v = value.strip()
    if len(v) <= keep * 2:
        masked = "*" * len(v)
    else:
        masked = v[:keep] + "*" * (len(v) - keep * 2) + v[-keep:]
    return masked[:max_len]


class RulesetUnavailable(Exception):
    """The vendored ruleset file is missing or unparseable — the
    'no scanner binary and no data-file fallback' case design §1 requires
    to become SKIPPED_BY_DESIGN, never a silent NO_SIGNAL."""


def load_ruleset(toml_path: Path | str | None = None) -> SecretRuleset:
    path = Path(toml_path) if toml_path is not None else DEFAULT_RULESET_PATH
    if not path.is_file():
        raise RulesetUnavailable(
            f"vendored ruleset data file not found at {path} — this deployment "
            "appears to have omitted the vendored asset")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise RulesetUnavailable(f"vendored ruleset at {path} could not be parsed: {exc}") from exc

    rules: list[SecretRule] = []
    for raw in data.get("rules", []):
        rule_id = raw.get("id")
        regex = raw.get("regex")
        if not rule_id or not regex:
            continue
        try:
            pattern = re.compile(regex)
        except re.error as exc:
            log.warning("secret_ruleset: skipping rule %r — regex would not compile "
                        "under Python's re engine (gitleaks itself uses RE2): %s",
                        rule_id, exc)
            continue
        keywords = tuple(str(k).lower() for k in raw.get("keywords", []) or [])

        # Per-rule gates. These are not optional refinements — see
        # SecretRule.entropy for what ignoring them cost.
        raw_entropy = raw.get("entropy")
        entropy = None
        if raw_entropy is not None:
            try:
                entropy = float(raw_entropy)
            except (TypeError, ValueError):
                log.warning("secret_ruleset: rule %r has an unparseable entropy "
                            "value %r — scanning it WITHOUT an entropy gate, which "
                            "will over-report", rule_id, raw_entropy)

        allowlists: list[RuleAllowlist] = []
        for raw_al in raw.get("allowlists", []) or []:
            if str(raw_al.get("condition", "OR")).upper() == "AND":
                # No vendored rule uses this today. Warn rather than
                # silently OR it, because OR-ing an AND block allowlists
                # MORE than intended — it would suppress real findings,
                # which is the dangerous direction to be wrong in.
                log.warning("secret_ruleset: rule %r has an allowlist with "
                            "condition=AND, which is not implemented — treating "
                            "as OR, which may over-suppress", rule_id)
            al_regexes: list[re.Pattern] = []
            for raw_pat in raw_al.get("regexes", []) or []:
                try:
                    al_regexes.append(re.compile(raw_pat))
                except re.error as exc:
                    log.warning("secret_ruleset: rule %r has an allowlist regex that "
                                "would not compile under Python's re — dropping it, "
                                "so this rule will over-report: %s", rule_id, exc)
            stopwords = tuple(str(w).lower() for w in raw_al.get("stopwords", []) or [])
            if al_regexes or stopwords:
                allowlists.append(RuleAllowlist(
                    regexes=tuple(al_regexes), stopwords=stopwords,
                    regex_target=str(raw_al.get("regexTarget", "secret")),
                ))

        secret_group = raw.get("secretGroup")
        try:
            secret_group = int(secret_group) if secret_group is not None else None
        except (TypeError, ValueError):
            secret_group = None

        rules.append(SecretRule(
            rule_id=rule_id, description=raw.get("description", ""),
            pattern=pattern, keywords=keywords,
            entropy=entropy, allowlists=tuple(allowlists),
            secret_group=secret_group,
        ))

    excluded_paths: list[re.Pattern] = []
    excluded_values: list[re.Pattern] = []
    allowlist = data.get("allowlist", {})
    for raw_pat in allowlist.get("paths", []) or []:
        try:
            excluded_paths.append(re.compile(raw_pat))
        except re.error as exc:
            log.debug("secret_ruleset: skipping unparseable allowlist path regex: %s", exc)
    for raw_pat in allowlist.get("regexes", []) or []:
        try:
            excluded_values.append(re.compile(raw_pat))
        except re.error as exc:
            log.debug("secret_ruleset: skipping unparseable allowlist value regex: %s", exc)

    if not rules:
        raise RulesetUnavailable(
            f"vendored ruleset at {path} parsed but yielded zero usable rules — "
            "treating as unavailable rather than scanning with an empty ruleset")

    return SecretRuleset(
        rules=rules, excluded_path_patterns=excluded_paths,
        excluded_value_patterns=excluded_values, source_path=path,
    )


# ── Known-positive self-test fixture ────────────────────────────────────
#
# design §1: "prefer running the vendored ruleset's own shipped fixtures
# over a mere scanned-file count ... Running the scanner over that
# fixture ... is a direct proof the method fired correctly on THIS run."
#
# **Honest limitation, stated here rather than only in the task report:**
# gitleaks does ship its own test corpus (`testdata/` in the upstream
# repo), but it is shaped for gitleaks' own Go test suite (paired
# fixture-file + expected-JSON-report pairs consumed by Go test code), not
# a portable "run this content through any matcher and check rule IDs
# fired" format. Reusing it as-is would mean vendoring and interpreting
# gitleaks' Go test harness, not just its rules. What follows instead is a
# small SELF-AUTHORED fixture — built from well-known, publicly-documented
# EXAMPLE credential shapes (not fabricated at random) for four of the 222
# vendored rules, chosen for regex simplicity and to avoid the ruleset's
# own built-in EXAMPLE-suffix allowlist (aws-access-token allowlists any
# value ending "EXAMPLE" — using AWS's own doc example key would silently
# not match and defeat the self-test's purpose). This proves the loader +
# matcher + keyword-prefilter + exclusion pipeline actually fires on THIS
# run; it does not prove gitleaks' own upstream test corpus still passes
# against a newer pull of the ruleset, which is a materially weaker claim
# than "ran the vendored ruleset's own fixtures" and is named as such.
_SELF_TEST_FIXTURE = "\n".join([
    'aws_key = "AKIATESTFAKEKEY2345Q"',
    'gh_token = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"',
    "-----BEGIN RSA PRIVATE KEY-----",
    "MIIEowIBAAKCAQEAfakefakefakefakefakefakefakefakefakefakefakefake",
    "fakefakefakefakefakefakefakefakefakefakefakefakefakefakefakefake",
    "-----END RSA PRIVATE KEY-----",
    'slack_app_token = "xapp-1-A1B2C3-1234567890-abc123def456"',
])

#: rule_id -> whether the self-test fixture above is expected to trigger it.
#: Kept as its own frozenset (rather than inferring "whatever fired") so a
#: rule silently dropping out of the vendored TOML on a future pull (a
#: renamed/removed upstream rule id) is a detectable self-test FAILURE
#: rather than a quietly-shrinking expectation.
_SELF_TEST_EXPECTED_RULE_IDS = frozenset({
    "aws-access-token", "github-pat", "private-key", "slack-app-token",
})


@dataclass(frozen=True)
class SelfTestResult:
    passed: bool
    expected_rule_ids: frozenset
    matched_rule_ids: frozenset
    missing_rule_ids: frozenset


def run_self_test(ruleset: SecretRuleset) -> SelfTestResult:
    """Scans the fixture above and checks every expected rule fired.
    Passing does not certify EVERY one of the 222 rules works — only that
    the load/match/keyword-prefilter pipeline is alive and that these four
    representative rule ids, spanning distinct regex shapes (a prefix+
    charset token, a fixed-prefix token, a multi-line PEM block, and a
    hyphen-delimited token), still fire end to end."""
    matches = ruleset.scan_text("__self_test_fixture__", _SELF_TEST_FIXTURE)
    matched_ids = frozenset(m.rule_id for m in matches)
    missing = _SELF_TEST_EXPECTED_RULE_IDS - matched_ids
    return SelfTestResult(
        passed=not missing,
        expected_rule_ids=_SELF_TEST_EXPECTED_RULE_IDS,
        matched_rule_ids=matched_ids & _SELF_TEST_EXPECTED_RULE_IDS,
        missing_rule_ids=missing,
    )
