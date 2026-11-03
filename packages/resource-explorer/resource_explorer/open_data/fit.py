"""The fit row and the winnow on scouted open-data columns (design §2, §4; OD2).

A data request has six dimensions (subjects, period, places, license needs, format needs, size ceiling). For each
dataset the fit row has one cell per dimension that can be compared, and every cell is one of four things:

  fits             the dataset states what the request needs;
  does_not_fit     the dataset states something that rules it out (the ONLY way a row is dropped);
  could_not_check  the dataset states nothing to compare against (never dropped, never passed);
  not_stated       the REQUEST states no need on that dimension (so there is nothing to compare, and the cell says so).

A request with no license need stated is not "any license": the cell reads "license need not stated" and the row is
never passed on it. A row passes only when every cell is `fits`. Nothing here calls out; it reads the stored columns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from resource_explorer.open_data.absence import NOT_REPORTED

FITS, DOES_NOT_FIT, COULD_NOT_CHECK, NOT_STATED = "fits", "does_not_fit", "could_not_check", "not_stated"
PASSES, DROPPED, OPEN = "passes", "dropped", "open"       # row verdicts: OPEN = nothing rules it out, not all cells fit

#: Coverage statements that mean "everywhere".
GLOBAL_WORDS = ("global", "worldwide", "world-wide", "whole world", "entire world", "all countries")

_YEAR = re.compile(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)")


@dataclass
class DataRequest:
    subjects: list[str] = field(default_factory=list)
    period: str = ""                          # "2019-2023" or "2021"
    places: list[str] = field(default_factory=list)
    need_commercial: bool | None = None       # True = commercial use needed; None = not stated
    need_share_alike_free: bool | None = None  # True = share-alike licenses are not acceptable; None = not stated
    formats: list[str] = field(default_factory=list)
    max_size_bytes: int | None = None

    @classmethod
    def from_dict(cls, d: dict | None) -> "DataRequest":
        d = d or {}
        listy = lambda v: [str(x).strip() for x in (v if isinstance(v, list) else str(v or "").replace(";", ",").split(",")) if str(x).strip()]  # noqa: E731
        lic = d.get("licence") or d.get("license") or {}
        return cls(
            subjects=listy(d.get("subjects")), period=str(d.get("period") or "").strip(), places=listy(d.get("places")),
            need_commercial=True if (lic.get("commercial") is True) else None,
            need_share_alike_free=True if (lic.get("share_alike") is False) else None,
            formats=[f.upper() for f in listy(d.get("formats"))],
            max_size_bytes=int(d["max_size_bytes"]) if str(d.get("max_size_bytes") or "").isdigit() else None)


def _years(text: str) -> tuple[int, int] | None:
    found = [int(y) for y in _YEAR.findall(text or "")]
    return (min(found), max(found)) if found else None


def _extra(record: dict, *keys: str):
    wanted = {k.lower() for k in keys}
    for item in record.get("extras") or []:
        if isinstance(item, dict) and str(item.get("key", "")).lower() in wanted and str(item.get("value") or "").strip():
            return str(item["value"]).strip()
    return None


def coverage_of(record: dict) -> dict:
    """Coverage and cadence AS THE PORTAL STATES THEM in the publisher's record; `NOT_REPORTED` for each it does not."""
    record = record if isinstance(record, dict) else {}
    start = _extra(record, "temporal_start", "temporal_coverage_start", "temporal_coverage_from", "time_period_start")
    end = _extra(record, "temporal_end", "temporal_coverage_end", "temporal_coverage_to", "time_period_end")
    span = _extra(record, "temporal_coverage", "time_period", "temporal")
    period = f"{start} to {end}" if start and end else (span or start or end or NOT_REPORTED)
    place = _extra(record, "spatial_coverage", "geographic_coverage", "spatial_text", "spatial", "geographic_location")
    cadence = _extra(record, "update_frequency", "frequency", "accrual_periodicity")
    tags = [t.get("name") or t.get("display_name") for t in record.get("tags") or [] if isinstance(t, dict)]
    return {"period": period, "places": place or NOT_REPORTED, "cadence": cadence or NOT_REPORTED,
            "tags": [t for t in tags if t] or NOT_REPORTED}


def _cell(status: str, words: str) -> dict:
    return {"status": status, "words": words}


def _license_cell(ds: dict, req: DataRequest) -> dict:
    if req.need_commercial is None and req.need_share_alike_free is None:
        return _cell(NOT_STATED, "license need not stated")
    lic = ds.get("licence") or {}
    problems, unknown = [], []
    if req.need_commercial:
        v = lic.get("commercial", NOT_REPORTED)
        (unknown if v == NOT_REPORTED else problems if v is False else []).append("commercial use")
    if req.need_share_alike_free:
        v = lic.get("share_alike", NOT_REPORTED)
        (unknown if v == NOT_REPORTED else problems if v is True else []).append("share-alike")
    problems, unknown = [p for p in problems if p], [u for u in unknown if u]
    lid = ds.get("licence_id")
    named = "no license reported" if lid == NOT_REPORTED else lid
    if problems:
        return _cell(DOES_NOT_FIT, f"{named}: " + ("share-alike, which the request cannot accept" if "share-alike" in problems
                                                   else "commercial use not allowed"))
    if unknown:
        return _cell(COULD_NOT_CHECK, f"could not check {', '.join(unknown)}: {named}")
    return _cell(FITS, f"{named} meets the stated license needs")


def _format_cell(ds: dict, req: DataRequest) -> dict:
    if not req.formats:
        return _cell(NOT_STATED, "format need not stated")
    have = ds.get("formats")
    if have in (NOT_REPORTED, None, []):
        return _cell(COULD_NOT_CHECK, "the portal lists no formats")
    have_up = {str(f).upper() for f in have}
    hit = sorted(have_up & set(req.formats))
    return _cell(FITS, f"offers {', '.join(hit)}") if hit else _cell(
        DOES_NOT_FIT, f"offers {', '.join(sorted(have_up))}, not {', '.join(req.formats)}")


def _size_cell(ds: dict, req: DataRequest) -> dict:
    if req.max_size_bytes is None:
        return _cell(NOT_STATED, "size ceiling not stated")
    size = ds.get("size")
    if size in (NOT_REPORTED, None, ""):
        return _cell(COULD_NOT_CHECK, "the portal states no size")
    return _cell(FITS if int(size) <= req.max_size_bytes else DOES_NOT_FIT, f"{int(size)} bytes against a ceiling of {req.max_size_bytes}")


def _period_cell(cov: dict, req: DataRequest) -> dict:
    if not req.period:
        return _cell(NOT_STATED, "period not stated")
    want = _years(req.period)
    if want is None:
        return _cell(COULD_NOT_CHECK, f"the requested period '{req.period}' names no year to compare")
    if cov["period"] == NOT_REPORTED:
        return _cell(COULD_NOT_CHECK, "the portal states no coverage period")
    have = _years(cov["period"])
    if have is None:
        return _cell(COULD_NOT_CHECK, f"the portal's coverage '{cov['period']}' names no year to compare")
    return _cell(FITS, f"covers {have[0]}-{have[1]}") if have[0] <= want[1] and have[1] >= want[0] else _cell(
        DOES_NOT_FIT, f"covers {have[0]}-{have[1]}, outside {want[0]}-{want[1]}")


def _place_cell(cov: dict, req: DataRequest) -> dict:
    if not req.places:
        return _cell(NOT_STATED, "place not stated")
    if cov["places"] == NOT_REPORTED:
        return _cell(COULD_NOT_CHECK, "the portal states no coverage place")
    text = str(cov["places"]).lower()
    # A worldwide statement covers any place (project owner, 2026-10-30): a row is dropped only on a stated mismatch.
    if any(w in text for w in GLOBAL_WORDS):
        return _cell(FITS, f"covers {', '.join(req.places)} (global)")
    hit = [p for p in req.places if p.lower() in text]
    return _cell(FITS, f"states {cov['places']}") if hit else _cell(DOES_NOT_FIT, f"states {cov['places']}, not {', '.join(req.places)}")


def fit_row(ds: dict, req: DataRequest) -> dict:
    cov = coverage_of(ds.get("record") or {})
    cells = {"license": _license_cell(ds, req), "format": _format_cell(ds, req), "size": _size_cell(ds, req),
             "period": _period_cell(cov, req), "place": _place_cell(cov, req)}
    statuses = {c["status"] for c in cells.values()}
    verdict = DROPPED if DOES_NOT_FIT in statuses else PASSES if statuses == {FITS} else OPEN
    return {"slug": ds["slug"], "title": ds["title"], "identifier": ds["identifier"], "coverage": cov, "cells": cells, "verdict": verdict}


def winnow(datasets: list[dict], req: DataRequest) -> dict:
    """Every row with its verdict, and the sentence that says what was dropped and why. A dropped row is KEPT in
    `rows` (verdict `dropped`) so the screen can list it; it is never silently removed."""
    rows = [fit_row(d, req) for d in datasets]
    dropped = [r for r in rows if r["verdict"] == DROPPED]
    opened = [r for r in rows if r["verdict"] == OPEN]
    why: dict[str, list[str]] = {}
    for r in dropped:
        for dim, c in r["cells"].items():
            if c["status"] == DOES_NOT_FIT:
                why.setdefault(dim, []).append(r["title"] if isinstance(r["title"], str) else r["slug"])
    parts = [f"{len(v)} dropped on {dim} ({', '.join(v)})" for dim, v in why.items()]
    return {"rows": rows, "dropped": len(dropped), "open": len(opened), "passes": len(rows) - len(dropped) - len(opened),
            "summary": "; ".join(parts) if parts else "none dropped",
            "could_not_check": sum(1 for r in rows if any(c["status"] == COULD_NOT_CHECK for c in r["cells"].values()))}


# ── Q-0011: overlap with data already held ────────────────────────────────────

def _tokens(ds: dict) -> set[str]:
    cov = coverage_of(ds.get("record") or {})
    text = str(ds["title"] if ds["title"] != NOT_REPORTED else "") + " " + " ".join(cov["tags"] if cov["tags"] != NOT_REPORTED else [])
    return {t for t in re.findall(r"[a-z0-9]{3,}", text.lower())}


def overlap(ds: dict, held: list[dict], threshold: float = 0.5) -> dict:
    """"may duplicate <slug>" when another HELD open-data dataset shares at least `threshold` of its title and tag words.
    Scope is said in the answer: only open data held here is compared; registered resources and Egeria are not."""
    mine = _tokens(ds)
    others = [h for h in held if h["slug"] != ds["slug"]]
    if not mine:
        return {"status": COULD_NOT_CHECK, "words": "could not check: the dataset has no title or tags to compare",
                "matches": [], "compared": len(others)}
    matches = []
    for h in others:
        theirs = _tokens(h)
        if theirs and len(mine & theirs) / len(mine | theirs) >= threshold:
            matches.append(h["slug"])
    scope = f"compared with {len(others)} other open-data dataset{'s' if len(others) != 1 else ''} held here; registered resources and Egeria are not compared yet"
    if matches:
        return {"status": "may_duplicate", "words": f"may duplicate {', '.join(matches)} ({scope})", "matches": matches, "compared": len(others)}
    return {"status": "none_found", "words": f"no overlap found ({scope})", "matches": [], "compared": len(others)}
