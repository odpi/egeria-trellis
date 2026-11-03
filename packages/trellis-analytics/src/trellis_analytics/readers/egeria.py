"""Egeria reader: read only. It hands out pyegeria clients opened from an identity and offers no write method.

`PackReader` is the narrow interface the `context_pack` routine reads through (three reads, no writes), so the
routine is pure given its reader and tests fake it. `EgeriaPackReader` is the real one over pyegeria's
MetadataExpert: it answers as whoever the client was opened as, so Egeria's own zones and permissions apply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


class EgeriaReader:
    def __init__(self, view_server: str, platform_url: str, user_id: str, user_pwd: str | None = None):
        self.view_server, self.platform_url, self.user_id, self._pwd = view_server, platform_url, user_id, user_pwd

    def client(self, client_class):
        """Open `client_class` (a pyegeria client) for reading. The caller must not call its write methods."""
        c = client_class(self.view_server, self.platform_url, user_id=self.user_id, user_pwd=self._pwd)
        c.create_egeria_bearer_token(self.user_id, self._pwd)
        return c


class PackReaderError(RuntimeError):
    """Egeria could not be read. Never read as "nothing there": the pack says "not established"."""


@dataclass
class Element:
    """One Egeria element, as the pack needs it. `properties` is Egeria's own propertiesAsStrings."""
    guid: str
    type_name: str
    name: str = ""
    qualified_name: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    create_time: str = ""
    created_by: str = ""
    update_time: str = ""
    version: int = 0
    classifications: dict[str, dict] = field(default_factory=dict)   # classificationName -> propertiesAsStrings


@dataclass
class Related:
    type_name: str                 # the relationship type
    element: Element               # the element at the far end
    relationship_guid: str = ""


class PackReader(Protocol):
    """What `context_pack` asks of Egeria. `identity` is who the reads are made as (checked against the pack's)."""
    identity: str

    def get_subject(self, subject: str) -> Element | None: ...
    def related(self, guid: str) -> list[Related]: ...
    def elements_under(self, qualified_name_prefix: str) -> list[Element]: ...


_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_BODY = {"forLineage": True, "forDuplicateProcessing": True}


def _strings(raw: dict) -> dict:
    ep = raw.get("elementProperties") if isinstance(raw, dict) else None
    ps = ep.get("propertiesAsStrings") if isinstance(ep, dict) else None
    return ps if isinstance(ps, dict) else {}


def element_from_raw(raw: Any) -> Element:
    """A raw Egeria element (top-level `elementGUID`, `type.typeName`, `elementProperties`, `versions`)."""
    if not (isinstance(raw, dict) and raw.get("elementGUID")):
        raise PackReaderError(f"unrecognised element from Egeria: {str(raw)[:80]}")
    ps = _strings(raw)
    v = raw.get("versions") or {}
    cls = {c.get("classificationName", ""): ((c.get("classificationProperties") or {}).get("propertiesAsStrings") or {})
           for c in raw.get("classifications") or [] if isinstance(c, dict)}
    return Element(
        guid=str(raw["elementGUID"]), type_name=str((raw.get("type") or {}).get("typeName") or ""),
        name=str(ps.get("displayName") or ps.get("name") or ""), qualified_name=str(ps.get("qualifiedName") or ""),
        properties=dict(ps), create_time=str(v.get("createTime") or ""), created_by=str(v.get("createdBy") or ""),
        update_time=str(v.get("updateTime") or ""), version=int(v.get("version") or 0), classifications=cls)


def _no_elements(res: Any) -> bool:
    return isinstance(res, str) and ("no element" in res.lower() or "not found" in res.lower())


class EgeriaPackReader:
    """`PackReader` over a pyegeria `MetadataExpert` client. Reads only: it calls no create, update or delete.

    `client` is already opened as `identity` (RE's `egeria_client(...).of(MetadataExpert)`, or
    `EgeriaPackReader.from_reader` for a user id and password)."""

    def __init__(self, client: Any, identity: str):
        self._me, self.identity = client, identity

    @classmethod
    def from_reader(cls, reader: EgeriaReader) -> "EgeriaPackReader":
        from pyegeria.omvs.metadata_expert import MetadataExpert
        return cls(reader.client(MetadataExpert), reader.user_id)

    def get_subject(self, subject: str) -> Element | None:
        try:
            if _GUID.match(subject):
                raw = self._me.get_metadata_element_by_guid(subject, body={"class": "GetRequestBody", **_BODY})
            else:
                body = {"class": "UniqueNameRequestBody", "name": subject, "namePropertyName": "qualifiedName", **_BODY}
                raw = self._me.get_metadata_element_by_unique_name(name=subject, property_name="qualifiedName", body=body)
                if raw is None or _no_elements(raw):
                    raw = self._by_display_name(subject)
        except PackReaderError:
            raise
        except Exception as exc:  # noqa: BLE001  a failed read is "could not read", not "absent"
            if "No elements found" in str(exc):
                return None
            raise PackReaderError(" ".join(str(exc).split())[:300] or type(exc).__name__) from exc
        if raw is None:
            raise PackReaderError("Egeria gave no answer (the client returned nothing): unreadable, not absent")
        if _no_elements(raw):
            return None
        return element_from_raw(raw) if isinstance(raw, dict) else None

    def _by_display_name(self, name: str) -> Any:
        body = {"class": "SearchStringRequestBody", "searchString": name, "startsWith": False, "ignoreCase": False, **_BODY}
        res = self._me.find_metadata_elements_with_string(search_string=name, body=body)
        if res is None or _no_elements(res):
            return res
        hits = [r for r in res if isinstance(r, dict) and _strings(r).get("displayName") == name
                and (r.get("type") or {}).get("typeName") == "RelationalDatabase"]
        if len(hits) > 1:
            raise PackReaderError(f"{len(hits)} databases are called {name!r}: give the qualified name or the GUID")
        return hits[0] if hits else "No elements found"

    def related(self, guid: str) -> list[Related]:
        body = {"class": "ResultsRequestBody", "graphQueryDepth": 0, **_BODY}
        try:
            res = self._me.get_all_related_elements(guid=guid, body=body)
        except Exception as exc:  # noqa: BLE001
            if "No elements found" in str(exc):
                return []
            raise PackReaderError(" ".join(str(exc).split())[:300] or type(exc).__name__) from exc
        if res is None:
            raise PackReaderError(f"Egeria gave no related-elements answer for {guid}: unreadable, not absent")
        if _no_elements(res) or (isinstance(res, dict) and not res):
            return []
        if not (isinstance(res, dict) and isinstance(res.get("elementList"), list)):
            raise PackReaderError(f"unrecognised related-elements answer for {guid}")
        out = []
        for item in res["elementList"]:
            if not (isinstance(item, dict) and isinstance(item.get("element"), dict)):
                raise PackReaderError("unrecognised relationship item from Egeria")
            out.append(Related(type_name=str((item.get("type") or {}).get("typeName") or ""),
                               element=element_from_raw(item["element"]),
                               relationship_guid=str(item.get("relationshipGUID") or "")))
        return out

    def elements_under(self, qualified_name_prefix: str) -> list[Element]:
        body = {"class": "SearchStringRequestBody", "searchString": qualified_name_prefix, "startsWith": True,
                "ignoreCase": False, "graphQueryDepth": 0, **_BODY}
        try:
            res = self._me.find_metadata_elements_with_string(search_string=qualified_name_prefix, starts_with=True, body=body)
        except Exception as exc:  # noqa: BLE001
            if "No elements found" in str(exc):
                return []
            raise PackReaderError(" ".join(str(exc).split())[:300] or type(exc).__name__) from exc
        if res is None:
            raise PackReaderError("Egeria gave no search answer: unreadable, not absent")
        if _no_elements(res):
            return []
        if not isinstance(res, list):
            raise PackReaderError("unrecognised search answer from Egeria")
        return [e for e in (element_from_raw(r) for r in res) if e.qualified_name.startswith(qualified_name_prefix)]
