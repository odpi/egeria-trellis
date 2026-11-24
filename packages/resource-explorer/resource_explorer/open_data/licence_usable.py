"""od_licence_usable (TR-54 B2): what a dataset's licence requires of the person who uses it, and whether that fits their purpose.

Input is the dataset's STORED licence (from import or scout): no portal call. The judgement is only what the licence IDENTIFIER says,
through RE's table of SPDX / Creative Commons / ODC ids (`licences.licence_flags`): attribution required or not, share-alike required
or not, commercial use allowed or not. Anything the table does not map is "not judged: licence <id> is not in RE's table"; a licence a
portal states only in words is read and "not judged"; no licence stated is `nothing_found`. Never "usable" by a guess, and never "not
usable" for a licence that is missing.

The STORED result is shared by everyone who can see the dataset, so it holds dataset facts only. A purpose (the licence need an
investigation the dataset belongs to has stated) is read per reader at read time (`view`), and the fit is never stored.
"""
from __future__ import annotations

from resource_explorer.open_data import analyses
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.fit import DataRequest, _license_cell
from resource_explorer.open_data.licences import licence_flags, licence_id_from_iri, licence_statement, licence_text

ANALYSIS_ID = "od_licence_usable"


def _requirements(licence_id: str) -> dict | None:
    """What the table says the licence requires, or None when the table does not know the id."""
    flags = licence_flags(licence_id)
    if all(v == NOT_REPORTED for v in flags.values()):
        return None
    return {"attribution": "required" if flags["attribution"] else "not required",
            "share_alike": "required" if flags["share_alike"] else "not required",
            "commercial_use": "allowed" if flags["commercial"] else "not allowed"}


def judge(dataset: dict) -> analyses.OpenDataResult:
    """The STORED result of one dataset, from its stored licence alone. Dataset facts only: the licence id or words, what the table
    says it requires, and whether it was judged. Pure, and it reads nothing per-user: the result is one shared row per dataset, so
    no investigation, title or fit may be in it (the fit is `fit`, computed per reader at read time by `view`)."""
    statement = licence_statement(dataset)
    if statement["state"] == "not_stated":
        return analyses.OpenDataResult(
            state="nothing_found", summary="the portal states no licence for this dataset",
            explanation="The portal's record states no licence, so nothing was read and nothing is judged.",
            measures={"licences_read": 0, "judged": False})
    if statement["state"] == "stated_in_words":
        text = licence_text(dataset) or ""
        shown = text if len(text) <= 200 else text[:200] + "…"
        return analyses.OpenDataResult(
            state="measured", summary="not judged: licence stated in words, not identified",
            explanation=f"The portal states the licence as: {shown}",
            measures={"licences_read": 1, "judged": False, "licence_id": None})
    licence_id = licence_id_from_iri(str(dataset["licence_id"]))
    requirements = _requirements(str(licence_id))
    if requirements is None:
        return analyses.OpenDataResult(
            state="measured", summary=f"not judged: licence {dataset['licence_id']} is not in RE's table",
            explanation="RE judges only the licence ids in its own table; this one is stated, and not judged.",
            measures={"licences_read": 1, "judged": False, "licence_id": str(dataset["licence_id"])})
    return analyses.OpenDataResult(
        state="measured",
        summary=(f"{licence_id}: attribution {requirements['attribution']}; share-alike {requirements['share_alike']}; "
                 f"commercial use {requirements['commercial_use']}"),
        explanation="Judged from the licence identifier through RE's table of licence ids; it says what the licence requires, not whether it fits a purpose.",
        measures={"licences_read": 1, "judged": True, "licence_id": str(licence_id), "requirements": requirements})


def fit(measures: dict, purposes: list[dict]) -> list[dict]:
    """The fit of a stored result to each purpose given (`purposes_for`), computed from the STORED requirements. Pure; computed per
    reader at read time and never stored. A result that was not judged could not be checked, and says why."""
    out = []
    requirements = measures.get("requirements")
    flags = None if not requirements else {"attribution": requirements["attribution"] == "required",
                                           "share_alike": requirements["share_alike"] == "required",
                                           "commercial": requirements["commercial_use"] == "allowed"}
    for purpose in purposes or []:
        base = {"investigation": purpose["investigation"], "title": purpose.get("title") or purpose["investigation"]}
        if flags is None:
            out.append({**base, "verdict": "could_not_check", "words": "the licence was not judged, so it cannot be checked"})
            continue
        cell = _license_cell({"licence": flags, "licence_id": measures.get("licence_id")},
                             DataRequest.from_dict({"licence": purpose["licence"]}))
        out.append({**base, "verdict": cell["status"], "words": cell["words"]})
    return out


def purposes_for(registry, store, dataset: dict) -> list[dict]:
    """The licence needs stated by the investigations this dataset is a member of, as the CURRENT caller may see them: it reads
    through the registry's per-user scoping, so call it only for a reader (a web request) or inside `run_queue._run_as_requester`
    (the presser). With no identity at all the registry's shared view sees every investigation: `view` refuses that case."""
    out = []
    for inv in registry.list_investigations(include_closed=True):
        slug = inv.get("slug")
        if not slug or not any(m.get("entity_type") == "open_data" and m.get("entity_slug") == dataset["slug"]
                               for m in registry.list_investigation_members(slug)):
            continue
        request = store.get_data_request(slug)
        if request and request.get("licence"):
            out.append({"investigation": slug, "title": inv.get("display_name") or slug, "licence": request["licence"]})
    return out


def view(registry, store, dataset: dict) -> dict | None:
    """What a reader sees of the stored check: the shared dataset facts, plus the fit to the purposes THIS reader's investigations
    state. None when the check has not run on the dataset. A reader with no identity gets no fit (never the shared view of everyone's)."""
    from resource_explorer.registry import current_user_id

    _, rows = registry.query_step_annotations("open_data", dataset["slug"], ANALYSIS_ID)
    if not rows:
        return None
    row = rows[0]
    measures = (row.get("detail") or {}).get("additionalProperties", {}).get("measures") or {}
    out = {"state": row["result_state"], "summary": row["summary"], "measured_at": row["measured_at"],
           "licence_id": measures.get("licence_id"), "judged": measures.get("judged"), "requirements": measures.get("requirements"),
           "fit": [], "fit_words": ""}
    if not current_user_id():
        out["fit_words"] = "fit not judged: no signed-in reader"
        return out
    out["fit"] = fit(measures, purposes_for(registry, store, dataset))
    out["fit_words"] = "" if out["fit"] else "no purpose stated by your investigations"
    return out


def run(ctx: analyses.RunContext) -> analyses.OpenDataResult:
    """Dataset facts only, and no per-user read: a queued run, scheduled or pressed, stores the same shared result."""
    return judge(ctx.dataset)


RUNNER = analyses.Runner(ANALYSIS_ID, run, lambda dataset: 0)        # reads the stored licence: no portal call
