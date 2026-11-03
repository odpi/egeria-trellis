"""The repository's primary language: ONE choice, shared by the header's LANGUAGE tile and the Egeria publish.

Two sources can answer "what language is this repository in?":

* RE's own measurement: the line census `language_file_classification` reports (the `code_volume` metric row,
  `detail.by_language`), counted by SOURCE FILES. Text-only languages (Markdown, JSON, ...) are not source files
  and are left out, exactly as the analysis's own results reader leaves them out.
* GitHub's `repo.language`, fetched into `project_stats.primary_language`, which is the primary language by BYTES.

Rule (architect, 2026-10-23): a measured value beats a fetched one when both exist, and whatever shows says which
it is. Neither existing is "not measured", never an empty "measured". Live case: data_prep_kit read "HTML" from
GitHub while the census measured Python in 839 of 983 source files.
"""
from __future__ import annotations

MEASURED = "measured"
GITHUB = "github"

#: The `primary_language_source` additionalProperty values on the Egeria asset.
MEASURED_SOURCE_ID = "resource-explorer:language_file_classification"
GITHUB_SOURCE_ID = "github:by-bytes"

#: The chip word on the tile, per source.
CHIP_WORD = {MEASURED: "measured", GITHUB: "GitHub"}


def _day(ts: str) -> str:
    return str(ts or "")[:10]


def _as_of_words(ts: str) -> str:
    """The census row's walk time for the hover: 'YYYY-MM-DD HH:MM' (UTC as stored), or the day when that is all."""
    t = str(ts or "").replace("T", " ")
    return t[:16] if len(t) >= 16 else t


def _measured(code_volume: dict | None) -> dict | None:
    """(language, files, total source files, as_of) from the census row, or None when nothing was counted.

    A row with no walk time is not a measurement either (fix round 1, 2026-10-23): "measured" always carries its
    as-of, so a census without `surveyed_at` falls back exactly as if it were absent."""
    as_of = str((code_volume or {}).get("surveyed_at") or "").strip()
    if not as_of:
        return None
    detail = (code_volume or {}).get("detail")
    if isinstance(detail, str):
        import json
        try:
            detail = json.loads(detail or "{}")
        except ValueError:
            detail = {}
    by_language = (detail or {}).get("by_language") or {}
    counted = {k: v for k, v in by_language.items()
               if isinstance(v, dict) and not v.get("text_only") and int(v.get("files") or 0) > 0}
    if not counted:
        return None
    total = sum(int(v.get("files") or 0) for v in counted.values())
    # Most source files wins; code lines then the name break a tie, so the answer never depends on dict order.
    lang, row = min(counted.items(), key=lambda kv: (-int(kv[1].get("files") or 0), -int(kv[1].get("code") or 0), kv[0]))
    return {"language": lang, "files": int(row.get("files") or 0), "total_files": total,
            "as_of": as_of}


def choose_primary_language_from(code_volume: dict | None, stats: dict | None) -> dict:
    """The one choice, from the two rows. Pure.

    Returns {value, source ('measured' | 'github' | ''), chip, source_id, basis, as_of, files, total_files,
    github_value, disagreement, hover}. source '' means neither exists: the tile keeps its "not measured" state."""
    m = _measured(code_volume)
    gh = str((stats or {}).get("primary_language") or "")
    gh_at = str((stats or {}).get("fetched_at") or "")
    if m is not None:
        disagreement = (f"GitHub says {gh} (by bytes)" if gh and gh.casefold() != m["language"].casefold() else "")
        hover = f"measured · {m['files']} of {m['total_files']} source files · {_as_of_words(m['as_of'])}"
        return {"value": m["language"], "source": MEASURED, "chip": CHIP_WORD[MEASURED],
                "source_id": MEASURED_SOURCE_ID,
                "basis": f"source files {m['files']}/{m['total_files']}", "as_of": m["as_of"],
                "files": m["files"], "total_files": m["total_files"], "github_value": gh,
                "disagreement": disagreement,
                "hover": hover + (f"\n{disagreement}" if disagreement else "")}
    if gh:
        return {"value": gh, "source": GITHUB, "chip": CHIP_WORD[GITHUB], "source_id": GITHUB_SOURCE_ID,
                "basis": "bytes", "as_of": gh_at, "files": None, "total_files": None, "github_value": gh,
                "disagreement": "", "hover": f"GitHub · primary by bytes · fetched {_day(gh_at)}"}
    return {"value": "", "source": "", "chip": "", "source_id": "", "basis": "", "as_of": "",
            "files": None, "total_files": None, "github_value": "", "disagreement": "", "hover": ""}


def choose_primary_language(registry, slug: str) -> dict:
    """The choice for one repository, read from its census row and its latest GitHub stats row."""
    return choose_primary_language_from(registry.query_metrics(slug, "code_volume") or {},
                                        registry.get_latest_project_stats(slug) or {})


def publish_properties(choice: dict) -> dict:
    """The four additionalProperties the repository's Egeria asset carries, one prefix for all four (architect,
    2026-10-23: `primary_language_as_of`, never a bare `as_of`). Neither source: all empty strings."""
    return {"primary_language": choice.get("value") or "",
            "primary_language_source": choice.get("source_id") or "",
            "primary_language_basis": choice.get("basis") or "",
            "primary_language_as_of": choice.get("as_of") or ""}
