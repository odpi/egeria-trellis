"""The shipped "Suggested portals" list (configdata/open_data_portal_seeds.yaml).

OFFERED, NEVER APPLIED: this module only reads the list and says which rows are not yet registered. Registering a
row is the existing portal registration with origin 'seed', one portal at a time, after a reach check.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from resource_explorer.open_data.store import normalise_url

SEEDS_PATH = Path(__file__).resolve().parent.parent / "configdata" / "open_data_portal_seeds.yaml"
FIELDS = ("display_name", "api_kind", "feed_url", "publisher", "identifier_scheme", "catalogue_licence", "checked",
          "size_note", "why")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FAILED = re.compile(r"^failed (\d{4}-\d{2}-\d{2})$")


@lru_cache(maxsize=1)
def _load() -> tuple[dict, ...]:
    data = yaml.safe_load(SEEDS_PATH.read_text(encoding="utf-8")) or {}
    return tuple(data.get("portals") or [])


def seed_rows() -> list[dict]:
    """Every row, with the derived fields: `status` (answered | failed | not_yet), `checked_on` (the date),
    `can_add`, and `words` (the date in words)."""
    out = []
    for raw in _load():
        row = {k: ("" if raw.get(k) is None else raw.get(k)) for k in FIELDS}
        row["checked"] = str(row["checked"])
        failed = _FAILED.match(row["checked"])
        if raw.get("add") is False or raw.get("not_yet"):
            row.update(status="not_yet", can_add=False, checked_on=row["checked"],
                       words=f"not yet: {raw.get('not_yet') or 'no adapter'}")
        elif failed:
            row.update(status="failed", can_add=False, checked_on=failed.group(1),
                       words=f"did not answer on {failed.group(1)}")
        else:
            row.update(status="answered", can_add=bool(row["feed_url"] and row["api_kind"]), checked_on=row["checked"],
                       words=f"checked {row['checked']} by the maintainers")
        out.append(row)
    return out


def unregistered(portals: list[dict]) -> list[dict]:
    """The rows whose feed (host + path, not the name) is not a registered portal's address. A row RE cannot read yet
    (no feed) is always listed: there is nothing to match, and it must stay visible."""
    held = {normalise_url(p["base_url"]) for p in portals}
    return [r for r in seed_rows() if not r["feed_url"] or normalise_url(r["feed_url"]) not in held]


def seed_by_feed(feed_url: str) -> dict | None:
    want = normalise_url(feed_url)
    return next((r for r in seed_rows() if r["feed_url"] and normalise_url(r["feed_url"]) == want), None)
