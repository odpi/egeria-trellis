"""`data_class_match` over file profiles (Files build step 8): which data files hold personal or sensitive data (Files
brief F7, §3 item 5), with the same proposal-only rule as databases.

The database analysis's matcher is reused as it is (`column_matching.data_class_match`: column name, type and sampled
values against every Data Class Egeria holds, every claim stated against its sample). What this module adds is the file
side of it:

* **the columns** are the newest `file_profiles` run's column rows (Files build step 5), one file at a time;
* **the values** are read here, from the head of each file Resource Explorer profiled, bounded by `SAMPLE_BYTES` and
  `MAX_VALUES` per column, through `file_profiles._open_plain` (no link followed, a regular file on the open descriptor).
  A file Egeria's CSV Survey Service profiled is never opened here (the step-5 rule: Resource Explorer measures nothing
  Egeria measured), so its columns are `not_sampled`, naming why; a file whose profile did not read (over the limit in a
  whole-only format, unreadable, not permitted) has no column to match;
* **the classes** are read from Egeria (`build_reference_clients`, as the signed-in person; read-only). When they cannot
  be read, every compared column is `no_candidates` with the reason, never "no match";
* **filename rules**: a file or folder name holding a word that suggests personal data (`PERSONAL_NAME_WORDS`) is a
  proposal of its own, apart from the value match, and says it rests on the name only.

Nothing is published and nothing is created in Egeria: an unmatched-but-patterned column's proposed class stays a
proposal here (the database step's DRAFT DataClass is not written for files yet). File and column names appear only on
Resource Explorer's own screen. Absence is never zero: no profile run is "not matched yet, profile the data files
first"; a run is a `step_runs` row (`STEP_KEY`) whose counts the card reads.
"""
from __future__ import annotations

import io
import logging
import os
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

log = logging.getLogger(__name__)

STEP_KEY = "file_data_class_match"
#: How much of each file is read for values: its head, cut at the last whole line.
SAMPLE_BYTES = 1024 * 1024
#: Values kept per column (non-null, in file order).
MAX_VALUES = 200
#: Matches kept on the run row (privacy-relevant and established first).
MAX_KEPT = 500
CLAIM_STALE_SECONDS = 900
ALREADY_RUNNING_WORDS = "a data-class match for {slug} is already running"
#: Words in a file or folder name that suggest personal data. A proposal on the name only, said so.
PERSONAL_NAME_WORDS = ("patient", "patients", "customer", "customers", "employee", "employees", "payroll", "salary",
                       "salaries", "ssn", "passport", "medical", "health", "personnel", "contacts", "members", "clients",
                       "students", "dob", "birth", "address", "addresses", "people", "persons", "users", "staff", "hr",
                       "pii", "gdpr", "phi")

EGERIA_NOT_OPENED = ("Egeria's CSV Survey Service profiled this file, so Resource Explorer does not open it; Egeria's value "
                     "counts are not read by this step yet")


class NothingToMatch(RuntimeError):
    """No data-file profile run exists for this file system."""


class AlreadyRunning(RuntimeError):
    """Another run for this file system holds the claim."""


class NotReachable(RuntimeError):
    """The mount point cannot be reached from this machine."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def name_signals(file_path: str) -> list[str]:
    """The personal-data words in a file's name or its folders' names, in order. Pure."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", file_path.lower()) if t]
    return [w for w in dict.fromkeys(tokens) if w in PERSONAL_NAME_WORDS]


def sample_values(path: Path, ext: str, *, limit: int = SAMPLE_BYTES, max_values: int = MAX_VALUES):
    """({column: [values]} or None, rows read, why-not). Reads the head of the file only: a delimited or JSON-lines file
    up to `limit` bytes cut at its last whole line, a Parquet file's first row group. None with the reason when the file
    cannot be opened or parsed; never raises."""
    import pandas as pd

    from resource_explorer.surveyors.filesystem import file_profiles as fp

    try:
        fh = fp._open_plain(path)
    except fp.NotPermitted as exc:
        return None, 0, f"not permitted to read the file: {exc}"
    if fh is None:
        return None, 0, "not a plain file now (a link or special file): not followed"
    try:
        with fh:
            if ext == "parquet":
                import pyarrow.parquet as pq

                pf = pq.ParquetFile(fh)
                if not pf.metadata.num_row_groups:
                    return {}, 0, ""
                df = pf.read_row_group(0).to_pandas()
            elif ext in fp._DELIMITED or ext in fp._LINES_JSON:
                data = fp._cut_at_line(fh.read(limit))
                if ext in fp._LINES_JSON:
                    df = pd.read_json(io.BytesIO(data), lines=True)
                else:
                    try:
                        df = pd.read_csv(io.BytesIO(data), sep=fp._DELIMITED[ext], low_memory=False)
                    except UnicodeDecodeError:
                        df = pd.read_csv(io.BytesIO(data), sep=fp._DELIMITED[ext], low_memory=False, encoding="latin-1")
            else:
                return None, 0, f"values are not sampled from .{ext} files here (read whole only)"
    except Exception as exc:  # noqa: BLE001 -- a file that cannot be parsed says so, never a silent skip
        return None, 0, f"the file could not be parsed for values: {type(exc).__name__}: {exc}"[:300]
    values = {}
    for col in df.columns:
        vals = [v for v in df[col].tolist() if v is not None and not (isinstance(v, float) and v != v)]
        values[str(col)] = vals[:max_values]
    return values, len(df), ""


def default_catalog():
    """The Data Classes and Valid Value Sets Egeria holds, read as the signed-in person. Never raises: a failed read is
    an unavailable catalogue carrying the reason."""
    from resource_explorer.surveyors.database.egeria_reference_catalog import (ReferenceCatalog, build_reference_clients,
                                                                                load_reference_catalog)

    try:
        designer, ref_manager = build_reference_clients()
        return load_reference_catalog(designer, ref_manager)
    except Exception as exc:  # noqa: BLE001 -- carried as the reason on every compared column
        log.warning("Egeria's data classes could not be read", exc_info=True)
        return ReferenceCatalog(available=False, unavailable_reason=f"Egeria's data classes could not be read: {exc}")


def _display(m, file_path: str) -> dict:
    """The match as stored: keyed by file and column, its statement naming the file, not a database path."""
    d = m.as_dict()
    shown = f"{file_path} › {m.column_name}"
    d["statement"] = d["statement"].replace(m.column_path, shown, 1)
    d.update(column_path=shown, file_path=file_path, schema="", table="")
    return d


def _rank(d: dict) -> tuple:
    return (not d["privacy_relevant"], not d["established"], d["verdict"] != "unmatched_patterned", -d["confidence"],
            d["file_path"], d["column"])


def run_file_class_match(fs_entity, registry, *, catalog_loader: Callable[[], object] | None = None) -> dict:
    """The press: match every column of the newest profile run against Egeria's data classes, and the file names against
    the personal-data words. Raises NothingToMatch, NotReachable, AlreadyRunning."""
    from resource_explorer.surveyors.database.column_matching import (MATCH_NO_CANDIDATES, MATCH_NOT_APPLICABLE,
                                                                      MATCH_NOT_SAMPLED, data_class_match)
    from resource_explorer.surveyors.database.sampling import HEAD, SampleProvenance
    from resource_explorer.surveyors.filesystem import file_profiles as fp
    from resource_explorer.surveyors.filesystem.walk_extensions import looks_secret

    slug = fs_entity.slug
    runs = registry.list_filesystem_file_column_runs(slug)
    if not runs:
        raise NothingToMatch("No data-file profiles to match yet. Profile the data files first.")
    profile_run = runs[0]
    rows = registry.list_filesystem_file_columns(slug, profile_run)
    root = Path(fs_entity.local_mount_point)
    if not root.is_dir():
        raise NotReachable(f"The mount point {fs_entity.local_mount_point} cannot be reached from here · nothing read.")
    claim_key = f"{STEP_KEY}:{registry._normalize_slug(slug)}"
    holder = f"{fp.machine()}:{os.getpid()}:{_now()}"
    if not registry.take_claim(claim_key, holder, stale_after_seconds=CLAIM_STALE_SECONDS):
        raise AlreadyRunning(ALREADY_RUNNING_WORDS.format(slug=slug))
    try:
        catalog = (catalog_loader or default_catalog)()
        by_file: dict[str, list[dict]] = {}
        for r in rows:
            by_file.setdefault(r["file_path"], []).append(r)
        matches: list[dict] = []
        files_out: list[dict] = []
        names: list[dict] = []
        sampled_at = _now()
        for file_path in sorted(by_file):
            cols = [r for r in by_file[file_path] if r.get("column_name") and r.get("state") in fp.PROFILED_STATES
                    + (fp.HEADER_SUSPECT,)]
            words = name_signals(file_path)
            if words:
                names.append({"file_path": file_path, "words": words})
            if not cols:
                files_out.append({"file_path": file_path, "outcome": "no_columns",
                                  "state": (by_file[file_path][0].get("state") or "")})
                continue
            source = cols[0].get("stats_source") or ""
            name = PurePosixPath(file_path).name
            values, rows_read, why = None, 0, ""
            if source.startswith(fp.EGERIA_SOURCE_PREFIX):
                why, outcome = EGERIA_NOT_OPENED, "egeria_profiled"
            elif looks_secret(name):
                why, outcome = "a secret-looking name: never opened", "secret_name"
            else:
                values, rows_read, why = sample_values(root / file_path, fp.ext_of(file_path))
                outcome = "sampled" if values is not None else "not_sampled"
            sampled = values is not None
            total = None
            if sampled and all((c.get("sample_ratio") in (None, 1, 1.0)) for c in cols):
                total = next((c.get("record_ct") for c in cols if c.get("record_ct") is not None), None)
            prov = (SampleProvenance(strategy=HEAD, sample_rows=rows_read, total_rows=total, sampled_at=sampled_at,
                                     truncated=any(len(v) >= MAX_VALUES for v in values.values()))
                    if sampled else SampleProvenance(strategy="catalog_stats_only", reason_not_sampled=why))
            files_out.append({"file_path": file_path, "outcome": outcome, "columns": len(cols), "rows_read": rows_read,
                              "why": why})
            for c in cols:
                m = data_class_match(
                    schema_name="", table_name=file_path, column_name=c["column_name"],
                    column_type=c.get("declared_type") or c.get("inferred_general_type") or "",
                    known_classes=catalog.data_classes if catalog.available else [],
                    sampled_values=(values or {}).get(c["column_name"]) if sampled else None, provenance=prov)
                if not catalog.available and m.verdict not in (MATCH_NOT_SAMPLED, MATCH_NOT_APPLICABLE):
                    m.verdict, m.confidence, m.matched_guid = MATCH_NO_CANDIDATES, 0, ""
                    m.matched_qualified_name = m.matched_display_name = ""
                    m.not_established_reason = catalog.unavailable_reason or "Egeria's data classes could not be read"
                d = _display(m, file_path)
                d["header_suspect"] = c.get("state") == fp.HEADER_SUSPECT
                matches.append(d)
        matches.sort(key=_rank)
        verdicts = Counter(d["verdict"] for d in matches)
        summary = {
            "profile_run": profile_run, "ran_at": _now(), "columns": len(matches), "files": len(by_file),
            "verdicts": dict(verdicts), "established": sum(1 for d in matches if d["established"]),
            "privacy_relevant": sum(1 for d in matches if d["privacy_relevant"] and d["established"]),
            "proposals": verdicts.get("unmatched_patterned", 0),
            "files_detail": files_out[:200], "files_not_shown": max(0, len(files_out) - 200),
            "file_outcomes": dict(Counter(f["outcome"] for f in files_out)),
            "name_signals": names[:200], "name_signal_files": len(names),
            "catalog": catalog.as_dict(), "matches": matches[:MAX_KEPT],
            "matches_not_kept": max(0, len(matches) - MAX_KEPT),
            "sample": {"bytes": SAMPLE_BYTES, "max_values": MAX_VALUES, "strategy": "head"},
            "machine": fp.machine(),
        }
        registry.record_step_run(slug, STEP_KEY, summary["ran_at"], entity_type="filesystem",
                                 source="resource-explorer", executor="local", metrics=summary)
        return summary
    finally:
        registry.release_claim(claim_key, holder)


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def run_words(m: dict) -> str:
    """The stored run in words. Every number from the run row; an unread catalogue is said before any count."""
    v = m.get("verdicts") or {}
    cat = m.get("catalog") or {}
    parts = [f"{_plural(m.get('columns', 0), 'column')} of {_plural(m.get('files', 0), 'profiled file')} examined"]
    if not cat.get("available", True):
        parts.append(f"nothing established · {cat.get('unavailable_reason') or 'Egeria’s data classes could not be read'}")
    else:
        parts.append(f"{_plural(m.get('established', 0), 'column')} match a data class"
                     f" ({m.get('privacy_relevant', 0):,} privacy-relevant)")
        parts.append(f"against {_plural(cat.get('data_class_count', 0), 'data class', 'data classes')} Egeria holds")
    if m.get("proposals"):
        parts.append(f"{_plural(m['proposals'], 'column')} with a pattern no class has · a proposal for a new class")
    for key, words in (("no_match", "matched no class"), ("inconclusive", "inconclusive on their sample"),
                       ("not_sampled", "not sampled"), ("not_applicable", "of a type no data class describes")):
        if v.get(key):
            parts.append(f"{_plural(v[key], 'column')} {words}")
    return " · ".join(parts)


def price(registry, slug: str) -> dict:
    """What the press would read, from the newest profile run's rows: {priced, files, columns, words}."""
    from resource_explorer.surveyors.filesystem import file_profiles as fp

    runs = registry.list_filesystem_file_column_runs(slug)
    if not runs:
        return {"priced": False, "files": None, "columns": None, "run": "",
                "words": "not priced · no data file has been profiled yet · profile the data files first"}
    rows = registry.list_filesystem_file_columns(slug, runs[0])
    cols = [r for r in rows if r.get("column_name") and r.get("state") in fp.PROFILED_STATES + (fp.HEADER_SUSPECT,)]
    files = {r["file_path"] for r in cols}
    by_re = {r["file_path"] for r in cols if not (r.get("stats_source") or "").startswith(fp.EGERIA_SOURCE_PREFIX)}
    if not cols:
        return {"priced": True, "files": 0, "columns": 0, "run": runs[0],
                "words": f"nothing to match · the profile run of {runs[0][:10]} holds no column"}
    return {"priced": True, "files": len(files), "columns": len(cols), "run": runs[0],
            "words": (f"matches {_plural(len(cols), 'column')} of {_plural(len(files), 'profiled file')} against the data "
                      f"classes Egeria holds (read from Egeria) · reads the first {SAMPLE_BYTES // (1024 * 1024)} MB of "
                      f"{_plural(len(by_re), 'file')} Resource Explorer profiled; files Egeria profiled are not opened")}


def view(registry, slug: str) -> dict:
    """{price, run, words}: the price before the press and the newest stored run (None when never run)."""
    rows = registry.query_step_runs(slug=slug, step_key=STEP_KEY, entity_type="filesystem", limit=1)
    m = (rows[0].get("metrics") or {}) if rows else {}
    p = price(registry, slug)
    run = None
    if m:
        run = {**m, "words": run_words(m), "stale": bool(p.get("run") and p["run"] != m.get("profile_run")),
               "rule": "every match is a proposal · a person confirms it; nothing is published to Egeria"}
    return {"price": p, "run": run, "words": "" if run else "not matched yet"}
