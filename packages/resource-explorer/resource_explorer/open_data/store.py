"""Storage for portals, directories and imported datasets, over the shared registry's connection.

Values an adapter did not report are stored as the text `not_reported` (never ''), booleans as 'true'/'false', so
a column never holds a bare absence. `record_json` is the publisher's record verbatim.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from resource_explorer.open_data.absence import NOT_REPORTED


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    """Lower-case, underscores: the registry's own slug shape (`ProjectRegistry._normalize_slug`)."""
    out = re.sub(r"[^a-z0-9.]+", "_", (text or "").lower()).strip("_")
    return out or "unnamed"


def normalise_url(url: str) -> str:
    """For 'is this already registered': scheme-less, lower-case host, no trailing slash, no www."""
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def _enc(value) -> str:
    return NOT_REPORTED if value == NOT_REPORTED else ("true" if value is True else "false" if value is False else str(value))


def _dec_flag(text: str):
    return {"true": True, "false": False}.get(text, NOT_REPORTED)


class OpenDataStore:
    def __init__(self, registry=None):
        from resource_explorer.registry import ProjectRegistry

        self.registry = registry or ProjectRegistry()

    def _conn(self):
        return self.registry._conn()

    # ── portals ──────────────────────────────────────────────────────────
    def portal_exists(self, slug: str) -> bool:
        return self.get_portal(slug) is not None

    def add_portal(self, *, slug: str, display_name: str, api_kind: str, base_url: str, secret_ref: str = "",
                   origin: str = "hand", registered_by: str = "", adapter: str = "",
                   calls_per_page: int | None = None, page_size: int | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO open_data_portals (slug, display_name, api_kind, base_url, secret_ref, origin,"
                " registered_by, registered_at, adapter, calls_per_page, page_size)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (slug, display_name, api_kind, base_url, secret_ref, origin, registered_by, now(), adapter,
                 calls_per_page, page_size))

    def get_portal(self, slug: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM open_data_portals WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def list_portals(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM open_data_portals ORDER BY display_name").fetchall()]

    def portal_by_url(self, url: str) -> dict | None:
        want = normalise_url(url)
        return next((p for p in self.list_portals() if normalise_url(p["base_url"]) == want), None)

    def remove_portal(self, slug: str) -> int:
        with self._conn() as conn:
            return conn.execute("DELETE FROM open_data_portals WHERE slug = ?", (slug,)).rowcount

    # ── directories ──────────────────────────────────────────────────────
    def add_directory(self, *, slug: str, display_name: str, api_kind: str, base_url: str,
                      registered_by: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO portal_directories (slug, display_name, api_kind, base_url, registered_by,"
                " registered_at, last_run_at) VALUES (?, ?, ?, ?, ?, ?, '')",
                (slug, display_name, api_kind, base_url, registered_by, now()))

    def get_directory(self, slug: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM portal_directories WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def list_directories(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM portal_directories ORDER BY display_name").fetchall()]

    def touch_directory_run(self, slug: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE portal_directories SET last_run_at = ? WHERE slug = ?", (now(), slug))

    # ── datasets ─────────────────────────────────────────────────────────
    def dataset_by_identifier(self, identifier: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM open_data WHERE identifier = ?", (identifier,)).fetchone()
        return self._dataset(row) if row else None

    def get_dataset(self, slug: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM open_data WHERE slug = ?", (slug,)).fetchone()
        return self._dataset(row) if row else None

    def list_datasets(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM open_data ORDER BY imported_at DESC, slug").fetchall()
        return [self._dataset(r) for r in rows]

    def add_dataset(self, *, slug: str, row: dict, portals: list[str], retrieved_at: str, imported_by: str,
                    work_list_slug: str, datacite: dict | None) -> None:
        """`row` is an adapter's normalised candidate row (its `record` is the verbatim publisher record)."""
        flags = row["licence"]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO open_data (slug, identifier, identifier_kind, portal_slug, portals_json, title,"
                " publisher, licence_id, attribution, share_alike, commercial, modified, formats_json,"
                " size_bytes, record_json, retrieved_at, datacite_json, imported_by, imported_at, work_list_slug)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (slug, row["identifier"], row["identifier_kind"], portals[0], json.dumps(portals),
                 _enc(row["title"]), _enc(row["publisher"]), _enc(row["licence_id"]),
                 _enc(flags["attribution"]), _enc(flags["share_alike"]), _enc(flags["commercial"]),
                 _enc(row["modified"]), json.dumps(row["formats"]), _enc(row["size"]),
                 json.dumps(row["record"]), retrieved_at, json.dumps(datacite) if datacite else "",
                 imported_by, now(), work_list_slug))

    def datasets_in_work_list(self, work_list_slug: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM open_data WHERE work_list_slug = ? ORDER BY imported_at, slug",
                                (work_list_slug,)).fetchall()
        return [self._dataset(r) for r in rows]

    def update_scouted(self, slug: str, row: dict, retrieved_at: str) -> None:
        """A scout re-read: the same columns an import fills, refreshed. Identity, portals and work list stay."""
        flags = row["licence"]
        with self._conn() as conn:
            conn.execute(
                "UPDATE open_data SET title = ?, publisher = ?, licence_id = ?, attribution = ?, share_alike = ?,"
                " commercial = ?, modified = ?, formats_json = ?, size_bytes = ?, record_json = ?, retrieved_at = ?"
                " WHERE slug = ?",
                (_enc(row["title"]), _enc(row["publisher"]), _enc(row["licence_id"]), _enc(flags["attribution"]),
                 _enc(flags["share_alike"]), _enc(flags["commercial"]), _enc(row["modified"]),
                 json.dumps(row["formats"]), _enc(row["size"]), json.dumps(row["record"]), retrieved_at, slug))

    def get_data_request(self, investigation_slug: str):
        """The stored request, or None for "no data request stated" (no row, or the table is not applied yet).
        Raises nothing for a missing table: that is reported by `data_request_table_present`."""
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT * FROM investigation_data_requests WHERE investigation_slug = ?",
                                   (investigation_slug,)).fetchone()
        except Exception as exc:
            if _is_missing_table(exc):
                return None
            raise
        return request_to_dict(row) if row else None


    def data_request_table_present(self) -> bool:
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1 FROM investigation_data_requests WHERE 1 = 0").fetchall()
        except Exception as exc:
            if _is_missing_table(exc):
                return False
            raise
        return True


    def save_data_request(self, investigation_slug: str, req: dict, updated_by: str) -> dict:
        lic = req.get("licence") or {}
        values = (json.dumps([str(x).strip() for x in req.get("subjects") or [] if str(x).strip()]), str(req.get("period") or "").strip(),
                  json.dumps([str(x).strip() for x in req.get("places") or [] if str(x).strip()]), str(req.get("grain") or "").strip(),
                  "1" if lic.get("commercial") is True else "", "0" if lic.get("share_alike") is False else "",
                  json.dumps([str(x).strip().upper() for x in req.get("formats") or [] if str(x).strip()]), updated_by, now())
        try:
            with self._conn() as conn:
                conn.execute("DELETE FROM investigation_data_requests WHERE investigation_slug = ?", (investigation_slug,))
                conn.execute(
                    "INSERT INTO investigation_data_requests (investigation_slug, subjects_json, period, places_json, grain,"
                    " need_commercial, need_share_alike, formats_json, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (investigation_slug, *values))
        except Exception as exc:
            if _is_missing_table(exc):
                raise DataRequestTableMissing(
                    "The data request cannot be stored yet: its table is applied at the next owner-approved restart.") from exc
            raise
        return self.get_data_request(investigation_slug)

    @staticmethod
    def _dataset(row) -> dict:
        d = dict(row)
        d["portals"] = json.loads(d.pop("portals_json") or "[]")
        d["formats"] = json.loads(d.pop("formats_json"))
        d["record"] = json.loads(d.pop("record_json"))
        d["datacite"] = json.loads(d.pop("datacite_json")) if d.get("datacite_json") else None
        d.pop("datacite_json", None)
        d["licence"] = {"attribution": _dec_flag(d.pop("attribution")), "share_alike": _dec_flag(d.pop("share_alike")),
                        "commercial": _dec_flag(d.pop("commercial"))}
        d["size"] = d.pop("size_bytes")
        return d


# ── the data request on an investigation (B4; docs/ddl-open-data-data-request.md, approved 2026-10-30) ──────────────

class DataRequestTableMissing(RuntimeError):
    """`investigation_data_requests` does not exist yet (it is applied at an owner-approved restart)."""


def _is_missing_table(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "no such table" in text or "undefinedtable" in text or ("relation" in text and "does not exist" in text)


def request_to_dict(row) -> dict:
    d = dict(row)
    return {
        "subjects": json.loads(d["subjects_json"] or "[]"), "period": d["period"], "places": json.loads(d["places_json"] or "[]"),
        "grain": d["grain"],
        "licence": {**({"commercial": True} if d["need_commercial"] == "1" else {}),
                    **({"share_alike": False} if d["need_share_alike"] == "0" else {})},
        "formats": json.loads(d["formats_json"] or "[]"), "updated_by": d["updated_by"], "updated_at": d["updated_at"],
    }
