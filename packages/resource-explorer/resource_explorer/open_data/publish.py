"""Publishing one open dataset to Egeria (OD4), per the brainstorm §6, with a read-back by GUID.

What is written, in this order, each step recorded in the proof (a step that fails is a row that says why; the
publish is resumable because the asset is read first by its qualifiedName):
  1. the asset: a `DataSet` named `OpenData::<identifier>`, the publisher's identifier as the external identifier,
     the portals it was found on, the licence id (SPDX where the portal gives one) and the terms URL as properties;
  2. the DigitalResourceOrigin classification, naming the publisher (the portal is in the properties);
  3. the DataScope classification, only when the portal STATES coverage; "coverage not reported by this source" is
     recorded, never a made-up scope;
  4. the licence: linked to Egeria's licence type for that id when Egeria holds one, else kept as the property and
     said so (no licence type is created here: creating a governance definition is its own approved step);
  5. the read-back by GUID: the type, the qualifiedName, the classifications and the licence property come back.

Everything the publisher stated is marked `stated by the publisher`; what RE measured (the profile) is not published
here. The Egeria calls go through a port: tests use a fake; `PyegeriaPort` is the real one and has NOT been run
against a live Egeria by the build. The first live publish is the check, on the project owner's word.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol

from resource_explorer.open_data import fit
from resource_explorer.open_data.absence import NOT_REPORTED, NOT_REPORTED_WORDS
from resource_explorer.open_data.store import OpenDataStore, now

STEP_KEY = "external_data_publish"
ENTITY_TYPE = "open_data"
QN_PREFIX = "OpenData::"
LICENSE_TYPE_QN_PREFIX = "LicenseType::"
ASSET_TYPE = "DataSet"
STATED = "stated by the publisher"

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][\d:.]+Z?)?$")


class PublishRefused(RuntimeError):
    """Nothing was written."""


class EgeriaPort(Protocol):
    def find_guid(self, qualified_name: str, type_name: str) -> str: ...
    def create_asset(self, body: dict) -> str: ...
    def add_origin(self, guid: str, body: dict) -> None: ...
    def add_scope(self, guid: str, body: dict) -> None: ...
    def link_license(self, license_type_guid: str, guid: str, body: dict) -> str: ...
    def read_back(self, guid: str) -> dict: ...


def qualified_name(ds: dict) -> str:
    return f"{QN_PREFIX}{ds['identifier']}"


def _text(v) -> str:
    return "" if v in (None, NOT_REPORTED) else str(v)


def _licence_terms_url(ds: dict) -> str:
    rec = ds.get("record") or {}
    return _text(rec.get("license_url") or rec.get("licence_url"))


def asset_body(ds: dict) -> dict:
    cov = fit.coverage_of(ds.get("record") or {})
    stated = {
        "externalIdentifier": ds["identifier"], "externalIdentifierKind": ds.get("identifier_kind", ""),
        "sourcePortals": ", ".join(ds.get("portals") or []), "publisher": _text(ds.get("publisher")),
        "licenceId": _text(ds.get("licence_id")), "licenceTermsUrl": _licence_terms_url(ds),
        "modifiedOnPortal": _text(ds.get("modified")), "formats": ", ".join(ds.get("formats") or []) if isinstance(ds.get("formats"), list) else "",
        "retrievedAt": _text(ds.get("retrieved_at")), "coveragePeriod": _text(cov["period"]),
        "coveragePlace": _text(cov["places"]), "updateCadence": _text(cov["cadence"]),
        "statedBy": f"{STATED}: every property above except retrievedAt", "surveyor": "Resource Explorer",
    }
    return {
        "class": "NewElementRequestBody",
        "properties": {
            "class": "AssetProperties", "typeName": ASSET_TYPE, "qualifiedName": qualified_name(ds),
            "displayName": _text(ds.get("title")) or ds["identifier"],
            "description": (f"Open data from {', '.join(ds.get('portals') or []) or 'a portal'} · published by "
                            f"{_text(ds.get('publisher')) or NOT_REPORTED_WORDS} · registered in Resource Explorer"),
            "additionalProperties": {k: v for k, v in stated.items() if v != ""},
        },
    }


def origin_body(ds: dict) -> dict | None:
    publisher = _text(ds.get("publisher"))
    if not publisher:
        return None
    return {"class": "NewClassificationRequestBody",
            "properties": {"class": "DigitalResourceOriginProperties", "owner": publisher, "ownerTypeName": "Organization",
                           "ownerPropertyName": "name"}}


def scope_body(ds: dict) -> dict | None:
    """Only what the portal states. Times only when both ends are ISO dates; any other statement of the period or
    place rides in the classification's additional properties, as the portal worded it."""
    cov = fit.coverage_of(ds.get("record") or {})
    period, place = _text(cov["period"]), _text(cov["places"])
    if not period and not place:
        return None
    props: dict = {"class": "DataScopeProperties", "additionalProperties": {"statedBy": STATED}}
    if place:
        props["additionalProperties"]["place"] = place
    if period:
        props["additionalProperties"]["period"] = period
        parts = re.split(r"\s+to\s+", period)
        if len(parts) == 2 and all(_ISO.match(p.strip()) for p in parts):
            props["dataCoverageStartTime"], props["dataCoverageEndTime"] = (_iso(parts[0]), _iso(parts[1]))
    return {"class": "NewClassificationRequestBody", "properties": props}


def _iso(text: str) -> str:
    t = text.strip()
    return t if "T" in t else f"{t}T00:00:00Z"


def licence_body(ds: dict) -> dict | None:
    lid = _text(ds.get("licence_id"))
    if not lid:
        return None
    return {"class": "NewRelationshipRequestBody",
            "properties": {"class": "LicenseProperties", "licenseId": lid, "conditions": _licence_terms_url(ds),
                           "licensedBy": _text(ds.get("publisher")), "notes": f"{STATED}; portal {', '.join(ds.get('portals') or [])}"}}


def publish_dataset(store: OpenDataStore, ds: dict, port: EgeriaPort, *, registry=None) -> dict:
    """Publish one dataset. Raises `PublishRefused` before any write (a private investigation). Otherwise returns the
    proof: every step with its outcome, the GUID, and the read-back. `complete` only when the read-back matched."""
    from resource_explorer.private_publish import PrivateKindNotSupported, refuse_unsupported_private

    registry = registry or store.registry
    try:
        refuse_unsupported_private(registry, ENTITY_TYPE, ds["slug"], name=_text(ds.get("title")))
    except PrivateKindNotSupported as exc:
        raise PublishRefused(str(exc)) from exc
    qn = qualified_name(ds)
    steps: list[dict] = []

    def step(name: str, outcome: str, words: str) -> None:
        steps.append({"step": name, "outcome": outcome, "words": words})

    guid = port.find_guid(qn, ASSET_TYPE)
    reused = bool(guid)
    if not guid:
        guid = port.create_asset(asset_body(ds))
    step("asset", "reused" if reused else "created", f"{ASSET_TYPE} {qn}")
    for name, builder, call in (("origin", origin_body, port.add_origin), ("scope", scope_body, port.add_scope)):
        body = builder(ds)
        if body is None:
            step(name, "not_written", "the publisher's name is not reported by this source" if name == "origin"
                 else "coverage is not reported by this source · no scope written")
            continue
        try:
            call(guid, body)
            step(name, "written", {"origin": "DigitalResourceOrigin names the publisher",
                                   "scope": "DataScope carries the coverage as the portal stated it"}[name])
        except Exception as exc:                 # said, and the publish is resumable
            step(name, "failed", f"{exc}"[:240])
    lic = licence_body(ds)
    if lic is None:
        step("licence", "not_written", f"license {NOT_REPORTED_WORDS} · nothing linked")
    else:
        lt = port.find_guid(f"{LICENSE_TYPE_QN_PREFIX}{ds['licence_id']}", "LicenseType")
        if not lt:
            step("licence", "kept_as_property", f"Egeria holds no license type for {ds['licence_id']} · the license is a "
                 "property of the asset, not a link")
        else:
            try:
                port.link_license(lt, guid, lic)
                step("licence", "linked", f"linked to the license type for {ds['licence_id']}")
            except Exception as exc:
                step("licence", "failed", f"{exc}"[:240])
    back = port.read_back(guid)
    matched = bool(back.get("matched"))
    proof = {"slug": ds["slug"], "identifier": ds["identifier"], "qualified_name": qn, "guid": guid, "reused": reused,
             "steps": steps, "read_back": back, "complete": matched and all(s["outcome"] != "failed" for s in steps),
             "at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    registry.record_step_run(ds["slug"], STEP_KEY, proof["at"], entity_type=ENTITY_TYPE, source="egeria-published",
                             executor="local", metrics=proof)
    return proof


def publish_price(n: int) -> dict:
    return {"datasets": n, "line": (f"{n} dataset{'s' if n != 1 else ''} → {n} asset{'s' if n != 1 else ''} in Egeria "
                                    "(one DataSet each, with its origin, coverage and license); written as you, "
                                    "read back by GUID")}


def latest_proof(registry, slug: str) -> dict | None:
    rows = registry.query_step_runs(slug=slug, step_key=STEP_KEY, entity_type=ENTITY_TYPE, limit=1)
    return (rows[0].get("metrics") or None) if rows else None


# ── the real port ───────────────────────────────────────────────────────────────────────────────────────────────

class PyegeriaPort:
    """The Egeria calls over pyegeria, as the signed-in person (a Caller). NOT run against a live Egeria by the build."""

    def __init__(self, identity=None, *, platform_url: str | None = None, view_server: str | None = None):
        from pyegeria import AssetMaker, ClassificationExplorer, MetadataExpert

        from resource_explorer.config import egeria_platform_url, egeria_view_server
        from resource_explorer.egeria_clients import current_principal, egeria_client

        clients = egeria_client(identity or current_principal(), purpose="open data publish",
                                view_server=view_server or egeria_view_server(),
                                platform_url=platform_url or egeria_platform_url())
        self._expert = clients.of(MetadataExpert)
        self._assets = clients.of(AssetMaker)
        self._classify = clients.of(ClassificationExplorer)

    def find_guid(self, qualified_name: str, type_name: str) -> str:
        from resource_explorer.egeria_absence import lookup_could_not_be_made, lookup_exception_is_absent
        from resource_explorer.egeria_lookup import guid_by_qualified_name

        try:
            return guid_by_qualified_name(self._expert, qualified_name, type_name=type_name) or ""
        except Exception as exc:
            if lookup_exception_is_absent(exc):
                return ""
            raise PublishRefused(lookup_could_not_be_made(qualified_name, exc)) from exc

    def create_asset(self, body: dict) -> str:
        return self._assets.create_asset(body=body)

    def add_origin(self, guid: str, body: dict) -> None:
        self._classify.add_digital_resource_origin(guid, body)

    def add_scope(self, guid: str, body: dict) -> None:
        self._classify.add_data_scope(guid, body)

    def link_license(self, license_type_guid: str, guid: str, body: dict) -> str:
        return self._classify.add_license_to_element(license_type_guid, guid, body)

    def read_back(self, guid: str) -> dict:
        """The asset read BY GUID: its header names that GUID, its type is a DataSet, and what it carries."""
        try:
            got = self._assets.get_asset_by_guid(guid, body={"class": "GetRequestBody", "graphQueryDepth": 0},
                                                 output_format="JSON")
        except Exception as exc:
            return {"matched": False, "why": f"the read-back could not be made: {exc}"[:240]}
        header = (got.get("elementHeader") or {}) if isinstance(got, dict) else {}
        props = (got.get("properties") or {}) if isinstance(got, dict) else {}
        type_name = (header.get("type") or {}).get("typeName") or ""
        classes = sorted((c.get("classificationName") or "") for c in (header.get("classifications") or []))
        extra = props.get("additionalProperties") or {}
        ok = header.get("guid") == guid and type_name == ASSET_TYPE
        return {"matched": ok, "why": "" if ok else f"Egeria answered with {header.get('guid') or 'no element'} / {type_name or 'no type'}",
                "type_name": type_name, "qualified_name": props.get("qualifiedName", ""),
                "classifications": classes, "licence_id": extra.get("licenceId", ""),
                "origin": next((c.get("classificationProperties", {}).get("owner", "") for c in (header.get("classifications") or [])
                                if c.get("classificationName") == "DigitalResourceOrigin"), ""),
                "has_data_scope": "DataScope" in classes}
