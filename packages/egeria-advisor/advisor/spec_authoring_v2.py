"""Report-spec authoring on pyegeria's authoring API (the EA re-base prototype, behind a flag).

`EA_SPEC_AUTHORING_V2=1` turns it on. Off (the default), nothing here is on any path and EA behaves as
before. On, the report-spec elicitor keeps its conversation and EA's canvas, and hands these to pyegeria:

- **Grounding.** The target type is resolved against Egeria's type system (pyegeria's Properties-class
  catalog and its registered specs); the find method and its scoping parameters come from the type's own
  registered spec, never from the LLM; columns are offered from, and kept only if they are, attributes of
  that type (or keys pyegeria's formatter derives). A key that isn't one is dropped and the reply says so.
- **Validation** is `pyegeria.authoring.validate` (the FormatSet model, attribute catalog, load-time
  resolution, and Dr.Egeria's validate when a runner is given), not `report_spec_parser.py`.
- **Serialization** is `pyegeria.authoring.serialize` (a `Create Report Spec` block carrying the spec's
  microflow), not the elicitor's `_generate_report_spec_md`; reading a document back is the microflow.
- **The answer** is `pyegeria.authoring.preview`: run as the signed-in user, structured TABLE, capped and
  saying so, never registered.

The spec stays a personal draft in EA's local stores (D16 is pyegeria's to wire later).
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

FLAG = "EA_SPEC_AUTHORING_V2"

#: The generic finder for a type with no registered find spec: pyegeria's own `Element-DrE-Basic` finder.
#: It is a view service, so its elements come back as view beans (`elementHeader` + `properties`), which the
#: column keys read. `MetadataExpert.find_metadata_elements_with_string`, used before (live 2026-11-13), is a
#: raw metadata-expert endpoint: its OpenMetadataElements (`elementGUID`, `elementProperties`) left every
#: cell of the table empty, `guid` included (pyegeria `core/element_shape.py` describes the two shapes).
#: `'*'` with a `metadata_element_type` lists every element of that type.
GENERIC_FIND = "ClassificationExplorer.find_elements_by_property_value"
#: Depth 0: V2's columns are the type's own attributes (exact keys, no relationship paths), so the finder
#: needn't walk relationships (Architect read-back 2c6e4667: generic Asset ~21 s, a 30 s timeout under load).
GENERIC_FIND_PARAMS = {"property_names": ["displayName", "qualifiedName"], "graph_query_depth": 0}

#: Run parameters every V2 spec declares, so the executor passes them on instead of dropping them as "not
#: parameters of this report" (it did: preview's page_size was ignored and the finder used its default 100).
RUN_PARAMS = ("page_size", "start_from")

#: The search a run uses when the question sets none. A finder that takes no default (the generic one
#: takes `property_value`, which the executor fills from `search_string`) fails without it (H08, live).
DEFAULT_SEARCH = "*"

#: Common names the LLM uses that aren't Egeria's.
_ALIASES = {"Term": "GlossaryTerm", "DataProduct": "DigitalProduct", "Product": "DigitalProduct",
            "Subscription": "DigitalSubscription", "Metric": "GovernanceMetric", "Policy": "GovernancePolicy",
            "Control": "GovernanceControl", "DataProductCatalog": "DigitalProductCatalog"}

#: Conditions the elicitor may set. `functional_status` and `element_status` are EA's words, translated
#: into a finder and its parameters by `find_action`; the others are pyegeria's FIND_PARAMETERS names.
CONDITION_PARAMS = ("search_string", "functional_status", "element_status", "output_sort_order",
                    "order_property_name")
#: Set by ground_conditions when status words were asked of a type that has a status, but none is a valid value
#: of it: the run is refused before any search (TR-7 (b)). Never a spec parameter.
STATUS_REFUSED = "status_refused"
#: Accepted from an older draft or a stray LLM reply, and read as `functional_status`: a status word never
#: reaches Egeria as the element status (B02, B05, B10, B11, B15, H05 on laz, 2026-11-13).
LEGACY_STATUS_PARAM = "limit_result_by_status"

#: Functional status (the owner, 2026-11-13: "Make sure EA's elicitor uses functional status"). What a user
#: means by "draft", "active", "in development" is the status the TYPE's own properties carry. The element
#: status in the header is only ever ACTIVE or DELETED on laz (every type's validElementStatusList), so it
#: filters nothing, and a content status sent as one is a 400. Values: laz's valid metadata values, which are
#: the Egeria enums (STATUS-VALID-VALUES-LAZ-2026-11-13); projectStatus is a string with valid values.
FUNCTIONAL_STATUS_VALUES: Dict[str, Tuple[str, ...]] = {
    "contentStatus": ("DRAFT", "PREPARED", "PROPOSED", "APPROVED", "REJECTED", "ACTIVE", "DEPRECATED",
                      "OBSOLETE", "OTHER"),
    "deploymentStatus": ("PROPOSED", "UNDER_DEVELOPMENT", "DEVELOPMENT_COMPLETE", "APPROVED_FOR_DEPLOYMENT",
                         "REJECTED", "STANDBY", "ACTIVE", "DISABLED", "FAILED", "OTHER"),
    "activityStatus": ("REQUESTED", "APPROVED", "WAITING", "ACTIVATING", "IN_PROGRESS", "PAUSED", "FOR_INFO",
                       "COMPLETED", "INVALID", "IGNORED", "FAILED", "CANCELLED", "ABANDONED", "OTHER"),
    "projectStatus": ("Proposed", "Planned", "Approved", "Activating", "Active", "On Hold", "Stalled",
                      "Completed", "Cancelled"),
}
#: The column that shows each property, so a reader can see the filter was applied.
STATUS_COLUMNS = {"contentStatus": "content_status", "deploymentStatus": "deployment_status",
                  "activityStatus": "activity_status", "projectStatus": "project_status"}
#: Words for a value its enum spells otherwise.
_STATUS_WORDS = {"IN_DEVELOPMENT": "UNDER_DEVELOPMENT", "DEVELOPMENT": "UNDER_DEVELOPMENT",
                 "BEING_DEVELOPED": "UNDER_DEVELOPMENT", "CANCELED": "CANCELLED", "ONHOLD": "ON_HOLD"}
#: The element status (header) a user can ask for: deleted elements, alone or with the rest.
ELEMENT_STATUS_WORDS = {"DELETED": ["DELETED"], "INCLUDE_DELETED": ["ACTIVE", "DELETED"]}

#: The finder that filters each property on the server. A list parameter takes several values; the
#: property-value finder (`value_param`) matches one value exactly.
_PROPERTY_VALUE_FIND = "ClassificationExplorer.find_elements_by_property_value"
#: TR-7 phase 2 (RULING-TR7-EA-PHASE2-STATUS-SEARCH-2026-11-15): contentStatus takes every asked value in one
#: search, a server-side OR scoped by type, so the chat answer is what the saved spec runs (no client merge).
_CONTENT_STATUS_FIND = "ClassificationExplorer.find_authored_elements"


def content_status_list_find() -> bool:
    """find_authored_elements with content_status_list can carry several contentStatus values: when the client
    has it AND pyegeria pages a run to its end (pyegeria.core.paging.paging_for). Its first page can be empty
    though matches follow (Egeria's paging contract; the 2026-11-14 DRAFT GlossaryTerm case), so without the
    pager it would read as "none". Feature-detected by name, never by version string."""
    import importlib
    try:
        paging = importlib.import_module("pyegeria.core.paging")
        explorer = importlib.import_module("pyegeria.omvs.classification_explorer")
    except ImportError:
        return False
    return hasattr(paging, "paging_for") and hasattr(getattr(explorer, "ClassificationExplorer", None),
                                                     "find_authored_elements")
SORT_ORDERS = ("ANY", "CREATION_DATE_RECENT", "CREATION_DATE_OLDEST", "LAST_UPDATE_RECENT",
               "LAST_UPDATE_OLDEST", "PROPERTY_ASCENDING", "PROPERTY_DESCENDING")


#: The elicitor fixes (prototype-only, separate from the re-base so their effect is measured apart):
#: one clarifying question for a request that names no kind of element; a condition (status, sort) checked
#: and shown as a column; a question that names its type is built, not asked; every type a registered spec
#: targets is offered (Asset included); a near-miss column key is read as the nearest attribute.
FIXES_FLAG = "EA_SPEC_ELICITOR_FIXES"


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _on(FLAG)


def fixes_enabled() -> bool:
    """The elicitor fixes, only with the re-base on."""
    return enabled() and _on(FIXES_FLAG)


#: Several status values through a one-value finder: one search per value, merged in `answer`. OFF, and
#: to stay off until a spec can run the same merge itself (the Architect's ruling 2026-11-14, ops §5b-1:
#: what chat shows must be what the saved spec runs; pyegeria's microflow runs one find stage, so a
#: multi-value find is a phase-2 requirement). Off, the first value the question gives is filtered and the
#: rest are `unmet`, in words.
STATUS_MERGE_FLAG = "EA_SPEC_STATUS_MERGE"


def status_merge_enabled() -> bool:
    return fixes_enabled() and _on(STATUS_MERGE_FLAG)


# ── Grounding ────────────────────────────────────────────────────────────────

def _norm(name: Optional[str]) -> str:
    return re.sub(r"[\s_\-]+", "", name or "")


def _snake(key: str) -> str:
    from pyegeria.authoring.validate import _snake as s
    return s(key)


@lru_cache(maxsize=1)
def _registry_by_type() -> Dict[str, List[Tuple[str, Any]]]:
    """{normalised target type: [(label, FormatSet)]} for registered find specs runnable from a search
    string alone (a find method, no analytic routine, no required GUID)."""
    from pyegeria.view.base_report_formats import get_report_registry
    out: Dict[str, List[Tuple[str, Any]]] = {}
    for label, fs in get_report_registry().items():
        a = fs.action
        if not a or not a.function or getattr(a, "analytic_function", None):
            continue
        if set(a.required_params or []) - {"search_string"}:
            continue
        if not a.function.split(".")[-1].startswith("find_"):
            continue
        out.setdefault(_norm(fs.target_type), []).append((label, fs))
    return out


@lru_cache(maxsize=1)
def _all_registry_types() -> List[str]:
    from pyegeria.view.base_report_formats import get_report_registry
    return sorted({_norm(fs.target_type) for fs in get_report_registry().values() if fs.target_type})


@lru_cache(maxsize=1)
def _catalog() -> Dict[str, tuple]:
    from md_processing.md_processing_utils.properties_class_fields import PROPERTIES_CLASS_FIELDS
    return PROPERTIES_CLASS_FIELDS


def resolve_type(name: Optional[str]) -> Optional[str]:
    """Egeria's name for a type the LLM proposed ("Glossary Term" -> "GlossaryTerm"), or None."""
    n = _norm(name)
    n = _ALIASES.get(n, n)
    if not n:
        return None
    cat = _catalog()
    for cand in (n, n[:1].upper() + n[1:]):
        entry = cat.get(f"{cand}Properties")
        if entry and entry[0] == "entity":
            return cand
        if cand in _registry_by_type():
            return cand
    lowered = {k[:-len("Properties")].lower(): k[:-len("Properties")] for k, v in cat.items()
               if k.endswith("Properties") and v[0] == "entity"}
    return lowered.get(n.lower())


def known_types() -> List[str]:
    """Types the elicitor may target: those with a registered find spec, Egeria's spelling."""
    out = []
    for t in (_all_registry_types() if fixes_enabled() else sorted(_registry_by_type())):
        r = resolve_type(t)
        if r and r not in out:
            out.append(r)
    return out


#: Types whose names are also words for what the user wants built or for "anything": "a report on ...",
#: "a dashboard for ...", "every element". They don't say what the report is about.
OUTPUT_WORDS = {"Report", "Dashboard", "Element", "Referenceable", "OpenMetadataRoot"}


def _singular(word: str) -> str:
    w = word.lower()
    for suf, rep_ in (("ies", "y"), ("sses", "ss"), ("s", "")):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return w[: -len(suf)] + rep_
    return w


def types_named_in(text: str) -> List[str]:
    """Egeria types the text names outright ("data product catalogs" -> DigitalProductCatalog), longest
    phrase first. Used to keep the elicitor from asking which kind of element when the user said so."""
    known = set(known_types())
    words = [_singular(w) for w in re.findall(r"[A-Za-z]+", text or "")]
    found: List[str] = []
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            phrase = "".join(w.capitalize() for w in words[i:i + size])
            t = resolve_type(phrase)
            if t in OUTPUT_WORDS:
                continue
            if t and t in known and t not in found and not any(t in f for f in found):
                found.append(t)
    return found


def base_spec(type_name: str) -> Optional[Tuple[str, Any]]:
    """The registered spec a new spec for `type_name` borrows its find method from: `<Type>-DrE-Basic`
    when there is one, else the matching spec with the most columns."""
    specs = _registry_by_type().get(_norm(type_name)) or []
    if not specs:
        return None
    for label, fs in specs:
        if label.endswith("-DrE-Basic"):
            return label, fs
    return max(specs, key=lambda s: sum(len(f.attributes or []) for f in s[1].formats or []))


def find_action(type_name: str, conditions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """{"function", "spec_params", "from", "optional_params"[, "required_params"]}: how to find `type_name`,
    from its registered spec or the generic finder scoped by type; with a functional-status condition, the
    finder that filters that property on the server. Never the LLM's guess."""
    status = (conditions or {}).get("functional_status")
    if isinstance(status, dict) and status.get("property") and status.get("values"):
        return status_find_action(type_name, status["property"], status["values"])
    base = base_spec(type_name)
    if base:
        label, fs = base
        params = {k: v for k, v in (fs.action.spec_params or {}).items() if not k.startswith("_")
                  and k != "output_format"}
        return {"function": fs.action.function, "spec_params": params, "from": label,
                "optional_params": _with_run_params(fs.action.optional_params)}
    return {"function": GENERIC_FIND, "spec_params": {"metadata_element_type": type_name, **GENERIC_FIND_PARAMS},
            "from": "generic finder", "optional_params": _with_run_params(_generic_optional_params())}


def _with_run_params(names) -> List[str]:
    out = [n for n in (names or []) if n not in CONDITION_PARAMS and n != LEGACY_STATUS_PARAM]
    return out + [n for n in RUN_PARAMS if n not in out]


def _generic_optional_params() -> List[str]:
    """The optional parameters of pyegeria's own Element-DrE-Basic spec, which uses GENERIC_FIND."""
    from pyegeria.view.base_report_formats import get_report_registry
    fs = get_report_registry().get("Element-DrE-Basic")
    if fs is not None and fs.action and fs.action.function == GENERIC_FIND:
        return list(fs.action.optional_params or [])
    return []


# ── Functional status ───────────────────────────────────────────────────────

def _fields(type_name: str) -> Tuple[str, ...]:
    entry = _catalog().get(f"{type_name}Properties")
    return tuple(entry[1]) if entry and entry[0] == "entity" else ()


def is_a(type_name: str, ancestor: str) -> bool:
    """`type_name` is `ancestor` or one of its subtypes. The catalog is generated from Egeria's Properties
    classes with each class's fields listed inherited-first, so a supertype's fields are a prefix of its
    subtypes' fields: walking the chain is a prefix test (DataSet -> DataAsset -> Asset -> Referenceable)."""
    if type_name == ancestor:
        return True
    mine, theirs = _fields(type_name), _fields(ancestor)
    # A heuristic: a subtype that adds no fields of its own has the same fields as its parent and fails the
    # test. Then the type falls to the property-value finder: still correct, just less specific.
    return bool(theirs) and len(theirs) < len(mine) and mine[:len(theirs)] == theirs


def status_properties(type_name: str) -> List[str]:
    """The functional-status properties `type_name` has, its own (the most specific) first: a property
    declared lower in the supertype chain comes later in the inherited-first field list. DigitalProduct ->
    [deploymentStatus, contentStatus]; Project -> [projectStatus, contentStatus]; Process -> [activityStatus]."""
    return [f for f in reversed(_fields(type_name)) if f in FUNCTIONAL_STATUS_VALUES]


def _status_key(value: Any) -> str:
    key = re.sub(r"[\s\-]+", "_", str(value or "").strip()).upper()
    return _STATUS_WORDS.get(key, key)


def _canonical(prop: str, key: str) -> Optional[str]:
    """`key` as `prop` spells it ("ACTIVE" -> "Active" for projectStatus), or None if it doesn't take it."""
    return next((v for v in FUNCTIONAL_STATUS_VALUES[prop] if _status_key(v) == key), None)


def _plural(type_name: str) -> str:
    if type_name.endswith("y"):
        return type_name[:-1] + "ies"
    return type_name + ("es" if type_name.endswith("s") else "s")


def _values_text(prop: str) -> str:
    return ", ".join(FUNCTIONAL_STATUS_VALUES[prop])


def _status_finder(type_name: str, prop: str) -> Tuple[str, Optional[str]]:
    """(function, list parameter) that filters `prop` on the server for `type_name`; a None list parameter
    means the property-value finder, which matches one value."""
    # contentStatus: find_authored_elements, every value in one list, scoped by type (TR-7 phase 2), where the
    # run pages to its end; it doesn't miss DRAFT once paged (the Architect's 2026-11-14 ~04:30Z correction).
    # Without the pager, the property-value finder on one value, which fills its first page. find_data_assets
    # stays out: the DataAsset census didn't show it complete.
    if prop == "contentStatus" and content_status_list_find():
        return _CONTENT_STATUS_FIND, "content_status_list"
    if prop == "deploymentStatus":
        if is_a(type_name, "Infrastructure"):
            return "AssetMaker.find_infrastructure", "deployment_status_list"
        if is_a(type_name, "DigitalProduct"):
            return "CollectionManager.find_digital_products", "deployment_status_list"
    if prop == "activityStatus" and is_a(type_name, "Process"):
        return "AssetMaker.find_processes", "activity_status_list"
    return _PROPERTY_VALUE_FIND, None


def takes_one_value(type_name: str, prop: str) -> bool:
    return _status_finder(type_name, prop)[1] is None


def status_find_action(type_name: str, prop: str, values: List[str]) -> Dict[str, Any]:
    """The find for `type_name` filtered on `prop` in `values`, on the server, scoped by type, depth 0."""
    function, list_param = _status_finder(type_name, prop)
    params: Dict[str, Any] = {"metadata_element_type": type_name, "graph_query_depth": 0}
    if list_param:
        params[list_param] = list(values)
        return {"function": function, "spec_params": params, "from": f"{prop} filter",
                "optional_params": list(RUN_PARAMS)}
    # One exact value: starts and ends with it. The value is pinned, so the run's search string (always
    # passed, as `search_string`) is bound to `property_value` and then overridden, never sent beside it.
    params.update(property_value=values[0], property_names=[prop], starts_with=True, ends_with=True,
                  ignore_case=True)
    return {"function": function, "spec_params": params, "from": f"{prop} filter",
            "optional_params": list(RUN_PARAMS), "required_params": ["property_value"]}


def ground_functional_status(type_name: Optional[str], values: Any
                             ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """({"property", "values", "note"} or None, unmet words) for status words asked of `type_name`.

    The code, not the LLM, picks the property: of the type's functional-status properties, the one that
    takes the most of the values, the type's own (most specific) on a tie. A value it doesn't take is
    reported, in words, as not applied; it never falls back to the element status."""
    raw = [v for v in (values if isinstance(values, (list, tuple)) else [values]) if str(v or "").strip()]
    keys = list(dict.fromkeys(_status_key(v) for v in raw))
    if not keys:
        return None, []
    asked = ", ".join(f"'{str(v).strip().lower()}'" for v in raw)
    if not type_name:
        return None, [f"status {asked}: no type to read a status from"]
    props = status_properties(type_name)
    if not props:
        return None, [f"{_plural(type_name)} have no status of their own (only Egeria's ACTIVE/DELETED element "
                      f"status), so status {asked} isn't applied"]
    takes = {p: [k for k in keys if _canonical(p, k)] for p in props}
    best = max(props, key=lambda p: (len(takes[p]), -props.index(p)))
    if not takes[best]:
        theirs = "; ".join(f"{p}: {_values_text(p)}" for p in props)
        return None, [f"{_plural(type_name)} have no {asked} status; their statuses are {theirs}"]
    chosen = [_canonical(best, k) for k in takes[best]]
    unmet: List[str] = []
    if takes_one_value(type_name, best) and len(chosen) > 1 and not status_merge_enabled():
        # the question's first value, never "the one with rows"; the rest are said, not dropped
        unmet.append(f"searched for {chosen[0]} only; {', '.join(chosen[1:])} "
                     f"{'was' if len(chosen) == 2 else 'were'} not searched (one {best} value per search here)")
        chosen = chosen[:1]
    for k in keys:
        if k in takes[best]:
            continue
        other = next((p for p in props if k in takes[p]), None)
        if other:
            unmet.append(f"'{k.lower()}' is a {other} value of {type_name}; a report filters one status, here "
                         f"{best}")
        else:
            unmet.append(f"{_plural(type_name)} have no '{k.lower()}' {best}; its values are {_values_text(best)}")
    both = [c for c in chosen if any(p != best and _canonical(p, _status_key(c)) for p in props)]
    if len(props) == 1:
        note = f"{type_name}'s status is {best}"
    else:
        note = f"{type_name} has {', '.join(props)}; filtered on {best}"
    if both:
        others = sorted({p for p in props if p != best for c in both if _canonical(p, _status_key(c))})
        note += (f": {', '.join(both)} is also a {' and '.join(others)} value, and {best} is {type_name}'s "
                 f"most specific status")
    if takes_one_value(type_name, best) and len(chosen) > 1:
        # only with STATUS_MERGE_FLAG on: one exact search per value, merged by `answer`; a saved spec is
        # one finder call, so it holds one
        note += (f"; the answer here merges one {best} search per value, the saved spec searches "
                 f"{chosen[0]} only")
    return {"property": best, "values": chosen, "note": note}, unmet


def attribute_menu(type_name: str) -> List[Tuple[str, str]]:
    """[(key, label)] a column may use for `type_name`: the registered spec's own columns first (known to
    render), then the type's properties, then derived header keys."""
    from pyegeria.authoring.validate import DERIVED_KEYS
    out: Dict[str, str] = {}
    base = base_spec(type_name)
    if base:
        for f in base[1].formats or []:
            for c in f.attributes or []:
                if c.key and c.key not in out:
                    out[c.key] = c.name or c.key
    entry = _catalog().get(f"{type_name}Properties")
    if entry and entry[0] == "entity":
        for field in entry[1]:
            k = _snake(field)
            out.setdefault(k, k.replace("_", " ").title())
    for k in sorted(DERIVED_KEYS):
        out.setdefault(k, k.replace("_", " ").title())
    return list(out.items())


def ground_columns(type_name: str, columns: List[Dict[str, Any]], *, near: Optional[bool] = None
                   ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Columns kept for `type_name`, and words for each one dropped. A camelCase key becomes snake_case
    when that is the attribute; an unknown key is dropped, never sent to a run."""
    import difflib
    near = fixes_enabled() if near is None else near
    menu = dict(attribute_menu(type_name))
    kept, dropped, seen = [], [], set()
    for col in columns or []:
        key = str(col.get("key") or "").strip()
        name = str(col.get("name") or key).strip()
        for cand in (key, _snake(key), _snake(key.split(".")[-1])):
            if cand in menu:
                key = cand
                break
        else:
            close = (difflib.get_close_matches(_snake(key.split(".")[-1]) if key else "", list(menu), n=1,
                                               cutoff=0.85) if near else [])
            if not close:
                dropped.append(f"'{name}' ({key or 'no key'}) isn't an attribute of {type_name}")
                continue
            dropped.append(f"'{name}' ({key}) read as {close[0]}, the nearest attribute of {type_name}")
            key = close[0]
        if key in seen:
            continue
        seen.add(key)
        kept.append({**col, "name": name or menu[key], "key": key})
    if not kept:
        kept = [{"name": "Display Name", "key": "display_name"}, {"name": "Description", "key": "description"}]
        kept = [c for c in kept if c["key"] in menu] or [{"name": "Qualified Name", "key": "qualified_name"}]
    return kept, dropped


def condition_columns(type_name: str, columns: List[Dict[str, Any]], conditions: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`columns` plus a column for each condition applied, so the reader can see it was applied: the
    functional-status property a status filter used, the sort attribute for a property sort, the times for a time sort."""
    menu = dict(attribute_menu(type_name))
    have = {c["key"] for c in columns}
    want = []
    status = conditions.get("functional_status")
    if isinstance(status, dict) and status.get("property") in STATUS_COLUMNS:
        key = STATUS_COLUMNS[status["property"]]
        menu.setdefault(key, key.replace("_", " ").title())
        want.append(key)
    if conditions.get("element_status"):
        want.append("status")
    if conditions.get("order_property_name"):
        want.append(_snake(str(conditions["order_property_name"])))
    order = str(conditions.get("output_sort_order") or "")
    if order.startswith("CREATION_DATE"):
        want.append("create_time")
    elif order.startswith("LAST_UPDATE"):
        want.append("update_time")
    out = list(columns)
    for k in want:
        if k and k in menu and k not in have:
            out.append({"name": menu[k], "key": k})
            have.add(k)
    return out


def ground_conditions(params: Dict[str, Any], type_name: Optional[str] = None
                      ) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """(conditions, notes, unmet): the conditions a question may set, checked for `type_name`.

    A status word becomes `functional_status` on the type's own property (`ground_functional_status`);
    what can't be applied is `unmet`, in words. Egeria's element status is only for an explicit "deleted",
    and only when the finder takes it."""
    out: Dict[str, Any] = {}
    notes: List[str] = []
    unmet: List[str] = []
    params = dict(params or {})
    words: List[Any] = []
    for k in ("functional_status", LEGACY_STATUS_PARAM):
        v = params.pop(k, None)
        if isinstance(v, dict):
            v = v.get("values")
        words += [x for x in (v if isinstance(v, list) else [v]) if x not in (None, "")]
    element = [x for x in words if _status_key(x) in ELEMENT_STATUS_WORDS]
    words = [x for x in words if _status_key(x) not in ELEMENT_STATUS_WORDS]
    if params.get("element_status") not in (None, "", []):
        e = params.pop("element_status")
        element += e if isinstance(e, list) else [e]
    params.pop("element_status", None)
    for k, v in params.items():
        if k not in CONDITION_PARAMS or v in (None, "", []):
            if k not in CONDITION_PARAMS:
                notes.append(f"'{k}' isn't a condition the finder takes")
            continue
        if k == "output_sort_order":
            if str(v).upper() in SORT_ORDERS:
                out[k] = str(v).upper()
        else:
            out[k] = str(v)
    if words:
        status, missed = ground_functional_status(type_name, words)
        unmet += missed
        if status is None and type_name and status_properties(type_name):
            # TR-7 (b): the type has a status, but none of the asked values is one of its valid values. Refused
            # before any search: an unfiltered run would answer a question about a status nobody has.
            out[STATUS_REFUSED] = "; ".join(missed)
        if status:
            out["functional_status"] = status
            if takes_one_value(type_name, status["property"]) and out.get("search_string", "*").strip() not in ("", "*"):
                unmet.append(f"the name filter '{out.pop('search_string')}' can't be combined with a "
                             f"{status['property']} filter (one property-value search)")
    if element:
        want = ELEMENT_STATUS_WORDS["INCLUDE_DELETED" if any(_status_key(x) == "INCLUDE_DELETED" for x in element)
                                    else "DELETED"]
        function = find_action(type_name, out)["function"] if type_name else None
        if function and finder_takes(function, "limit_results_by_status"):
            out["element_status"] = want
        else:
            unmet.append("deleted elements: this report's finder can't list them")
    return out, notes, unmet


def finder_takes(function: str, param: str) -> bool:
    """`function` ("Class.method") names `param` in its signature. False when it can't be resolved."""
    import importlib
    import inspect
    cls, _, method = (function or "").partition(".")
    try:
        from pyegeria.view.format_set_executor import _CLIENT_CLASS_MAP
        path = _CLIENT_CLASS_MAP.get(cls)
        if path:
            mod, attr = path.rsplit(".", 1)
            klass = getattr(importlib.import_module(mod), attr)
        else:   # resolved as the executor does: the first EgeriaTech sub-client that has the method
            from pyegeria.egeria_tech_client import EgeriaTech
            klass = next(c for c in EgeriaTech("v", "https://localhost", "u")._subclient_map.values()
                         if c.__name__ == cls)
        return param in inspect.signature(getattr(klass, method)).parameters
    except Exception:  # noqa: BLE001 -- unresolvable means "not known to take it"
        return False


# ── Build, validate, serialize, read back ────────────────────────────────────

def label_for(spec: Dict[str, Any]) -> str:
    raw = spec.get("title") or spec.get("draft_id") or "ea-spec"
    return re.sub(r"[^A-Za-z0-9_.\-]+", "-", str(raw)).strip("-")[:80] or "ea-spec"


def build_format_set(spec: Dict[str, Any]):
    """A FormatSet from an elicitor draft: grounded type, its registered find method, grounded columns."""
    from pyegeria.view._output_format_models import ActionParameter, Column, Format, FormatSet
    target = spec.get("target_type")
    conditions = spec.get("conditions") or {}
    action = find_action(target, conditions)
    params = dict(action["spec_params"])
    for k, v in conditions.items():
        if k in ("functional_status", STATUS_REFUSED):
            continue   # in the action (its finder and filter parameter), or not a parameter at all
        params[LEGACY_STATUS_PARAM if k == "element_status" else k] = v
    cols = [Column(name=c["name"], key=c["key"]) for c in spec.get("columns") or []]
    return FormatSet(
        target_type=target,
        heading=(spec.get("answers") or {}).get("Heading") or label_for(spec),
        description=(spec.get("answers") or {}).get("Description") or "",
        formats=[Format(types=["ALL"], attributes=cols)],
        action=ActionParameter(function=action["function"],
                               required_params=action.get("required_params") or ["search_string"],
                               optional_params=action.get("optional_params") or list(RUN_PARAMS),
                               spec_params=params),
    )


def spec_draft(spec: Dict[str, Any], fs=None):
    from pyegeria.authoring import SpecDraft
    return SpecDraft(label=label_for(spec), format_set=fs or build_format_set(spec))


def validate(spec: Dict[str, Any], *, client: Any = None, run: Any = None):
    from pyegeria.authoring import validate as pv
    return pv(spec_draft(spec), client, run=run)


def serialize(spec: Dict[str, Any]) -> str:
    from pyegeria.authoring import serialize as ps
    sd = spec_draft(spec)
    return ps(sd, title=f"# {sd.format_set.heading}")


def format_set_from_document(markdown: str, label: str):
    """The FormatSet a V2 document holds (its microflow), read with Dr.Egeria's own parser. Raises
    ValueError with words when the document isn't one."""
    from pyegeria.authoring.serialize import parsed_attributes
    from pyegeria.view.microflow import microflow_to_format_set, parse_microflow
    attrs = parsed_attributes(markdown).get("Create Report Spec")
    if not attrs:
        raise ValueError("the document has no Create Report Spec block")

    def val(name):
        v = attrs.get(name)
        return v.get("value") if isinstance(v, dict) else v
    mf, _, _ = parse_microflow(val("Microflow"))
    return microflow_to_format_set(mf, label=label, heading=val("Display Name"),
                                   description=val("Description") or "", target_type=val("Target Type Name"))


# ── The answer ───────────────────────────────────────────────────────────────

ANSWER_ROWS = 25


def search_of(spec: Dict[str, Any]) -> str:
    """The search string a run passes: the question's own (a condition, else the draft's content filter),
    else DEFAULT_SEARCH. A condition set as a spec parameter still wins inside the executor."""
    for src in (spec.get("conditions") or {}, spec.get("content_filters") or {}):
        v = src.get("search_string")
        if isinstance(v, str) and v.strip():
            return v
    return DEFAULT_SEARCH


def per_value_specs(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`spec` once per status value when its finder matches one value (the property-value finder), else
    `[spec]`. Each copy filters on one value; `answer` merges their rows. Only with STATUS_MERGE_FLAG on."""
    status = (spec.get("conditions") or {}).get("functional_status")
    if not status_merge_enabled() or not isinstance(status, dict) or len(status.get("values") or []) < 2 \
            or not takes_one_value(spec.get("target_type"), status.get("property")):
        return [spec]
    return [{**spec, "conditions": {**spec["conditions"], "functional_status": {**status, "values": [v]}}}
            for v in status["values"]]


def merge_previews(prevs: List[Dict[str, Any]], values: List[str], prop: str, max_rows: int) -> Dict[str, Any]:
    """One preview from several one-value runs: rows in value order, de-duplicated by GUID where the
    table has one (elements matched by different values of one property are distinct anyway), capped at
    `max_rows`, and capped whenever any run was."""
    tables = [p for p in prevs if isinstance(p.get("result"), dict) and p["result"].get("kind") == "table"]
    notes: List[str] = []
    for v, p in zip(values, prevs):
        notes += [f"{prop} {v}: {n}" for n in p.get("notes") or []]
    if not tables:
        return {**prevs[0], "notes": notes + [f"one {prop} search per value ({', '.join(values)}): none had rows"]}
    rows, seen, capped = [], set(), any(bool(p.get("capped")) for p in prevs)
    for p in tables:
        for r in p["result"].get("rows") or []:
            guid = r.get("guid") if isinstance(r, dict) else None
            if guid:
                if guid in seen:
                    continue
                seen.add(guid)
            rows.append(r)
    if len(rows) > max_rows:
        rows, capped = rows[:max_rows], True
    notes.append(f"one {prop} search per value ({', '.join(values)}), merged: {len(rows)} rows shown"
                 + ("; there may be more" if capped else ""))
    result = {**tables[0]["result"], "rows": rows}
    return {**tables[0], "result": result, "capped": capped, "shown": len(rows), "notes": notes}


def count_words(prev: Dict[str, Any]) -> str:
    """How many rows, said only as far as it is known (TR-7 (d), PY-17's rule): "N rows" only when the paging
    contract confirmed the end and nothing was cut off; otherwise "at least N rows". "" for a result that isn't
    a table of rows, or an empty one (pyegeria says its own absence)."""
    res = prev.get("result")
    if not isinstance(res, dict) or res.get("kind") != "table":
        return ""
    n = len(res.get("rows") or [])
    if n == 0:
        return ""
    end = prev.get("end") if prev.get("end") is not None else res.get("end")
    rows = f"{n} row{'s' if n != 1 else ''}"
    return rows if end == "confirmed" and not prev.get("capped") else f"at least {rows}"


def answer(spec: Dict[str, Any], client: Any, *, max_rows: int = ANSWER_ROWS) -> Dict[str, Any]:
    """Run the spec as `client`'s user (structured TABLE, capped) and render it for the reply. A status
    the finder matches one value at a time runs once per value, merged."""
    import time
    from pyegeria.authoring import preview
    t = time.time()
    refused = (spec.get("conditions") or {}).get(STATUS_REFUSED)
    if refused:
        return {"ok": False, "refused": True, "error": refused, "s": 0.0,
                "markdown": f"I didn't search: {refused}. Ask again with one of those values."}
    runs = per_value_specs(spec)
    prevs = []
    for one in runs:
        try:
            prevs.append(preview(spec_draft(one), client=client, max_rows=max_rows,
                                 params={"start_from": 0, "search_string": search_of(one)}))
        except Exception as exc:  # noqa: BLE001 -- the words go to the user
            which = ""
            if len(runs) > 1:
                which = f" ({one['conditions']['functional_status']['values'][0]})"
            return {"ok": False, "error": " ".join(str(exc).split())[:400], "s": round(time.time() - t, 2),
                    "markdown": f"I couldn't run it{which}: {' '.join(str(exc).split())[:300]}"}
    if len(runs) > 1:
        status = spec["conditions"]["functional_status"]
        prev = merge_previews(prevs, status["values"], status["property"], max_rows)
    else:
        prev = prevs[0]
    said = count_words(prev)
    if said:
        prev = {**prev, "notes": [said] + list(prev.get("notes") or [])}
    out = {"ok": True, **prev, "s": round(time.time() - t, 2)}
    out["markdown"] = render_markdown(prev)
    return out


def render_markdown(prev: Dict[str, Any]) -> str:
    """A markdown table of a preview, saying when it's capped or empty (egeria-table.js renders the
    structured result where the browser has it; this is the text everyone gets)."""
    res = prev.get("result") or {}
    notes = "; ".join(prev.get("notes") or [])
    if not isinstance(res, dict) or res.get("kind") != "table":
        body = (res.get("content") or res.get("message") or "") if isinstance(res, dict) else str(res)
        return f"{body}\n\n*{notes}*" if notes else str(body)
    cols = res.get("columns") or []
    keys = [c.get("key") if isinstance(c, dict) else c for c in cols]
    heads = [(c.get("label") or c.get("key")) if isinstance(c, dict) else c for c in cols]
    rows = res.get("rows") or []
    if not rows:
        return f"*No rows: {notes or 'ran, and found nothing'}.*"

    def cell(v):
        if isinstance(v, dict):
            v = v.get("display") or v.get("value") or v.get("text") or ""
        if isinstance(v, list):
            v = ", ".join(str(cell(x)) for x in v)
        return " ".join(str(v if v is not None else "").split()).replace("|", "\\|")[:120]
    lines = ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
    for r in rows:
        vals = [r.get(k) for k in keys] if isinstance(r, dict) else list(r)
        lines.append("| " + " | ".join(cell(v) for v in vals) + " |")
    if notes:
        lines.append(f"\n*{notes}*")
    return "\n".join(lines)
