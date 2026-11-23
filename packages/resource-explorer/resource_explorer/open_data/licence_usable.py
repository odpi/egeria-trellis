"""od_licence_usable (TR-54 B2): what a dataset's licence requires of the person who uses it, and whether that fits their purpose.

Input is the dataset's STORED licence (from import or scout): no portal call. The judgement is only what the licence IDENTIFIER says,
through RE's table of SPDX / Creative Commons / ODC ids (`licences.licence_flags`): attribution required or not, share-alike required
or not, commercial use allowed or not. Anything the table does not map is "not judged: licence <id> is not in RE's table"; a licence a
portal states only in words is read and "not judged"; no licence stated is `nothing_found`. Never "usable" by a guess, and never "not
usable" for a licence that is missing.

A stated purpose is the licence need an investigation the dataset belongs to has stated (its data request: commercial use needed,
share-alike not acceptable). With none, the requirements are listed and no fit is judged.
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


def _for(fit: list[dict]) -> str:
    """"; could not check for 'Water study'" after a summary, one clause per purpose; nothing when no purpose is stated."""
    return "".join(f"; {f['verdict'].replace('_', ' ')} for '{f['title']}'" for f in fit)


def judge(dataset: dict, purposes: list[dict] | None = None) -> analyses.OpenDataResult:
    """The result of one dataset, from its stored licence and the purposes (`purposes_for`) given. Pure: reads nothing else."""
    statement = licence_statement(dataset)
    if statement["state"] == "not_stated":
        return analyses.OpenDataResult(
            state="nothing_found", summary="the portal states no licence for this dataset",
            explanation="The portal's record states no licence, so nothing was read and nothing is judged.",
            measures={"licences_read": 0, "judged": False})
    if statement["state"] == "stated_in_words":
        text = licence_text(dataset) or ""
        shown = text if len(text) <= 200 else text[:200] + "…"
        fit = [{"investigation": p["investigation"], "title": p.get("title") or p["investigation"], "verdict": "could_not_check",
                "words": "the licence is stated in words and not identified"} for p in purposes or []]
        return analyses.OpenDataResult(
            state="measured", summary="not judged: licence stated in words, not identified" + _for(fit),
            explanation=f"The portal states the licence as: {shown}",
            measures={"licences_read": 1, "judged": False, "licence_id": None, "fit": fit})
    licence_id = licence_id_from_iri(str(dataset["licence_id"]))
    requirements = _requirements(str(licence_id))
    if requirements is None:
        fit = [{"investigation": p["investigation"], "title": p.get("title") or p["investigation"], "verdict": "could_not_check",
                "words": f"licence {dataset['licence_id']} is not in RE's table"} for p in purposes or []]
        return analyses.OpenDataResult(
            state="measured", summary=f"not judged: licence {dataset['licence_id']} is not in RE's table" + _for(fit),
            explanation="RE judges only the licence ids in its own table; this one is stated, and not judged.",
            measures={"licences_read": 1, "judged": False, "licence_id": str(dataset["licence_id"]), "fit": fit})
    say = (f"{licence_id}: attribution {requirements['attribution']}; share-alike {requirements['share_alike']}; "
           f"commercial use {requirements['commercial_use']}")
    fit = []
    flags = licence_flags(licence_id)
    for purpose in purposes or []:
        cell = _license_cell({"licence": flags, "licence_id": licence_id}, DataRequest.from_dict({"licence": purpose["licence"]}))
        fit.append({"investigation": purpose["investigation"], "title": purpose.get("title") or purpose["investigation"],
                    "verdict": cell["status"], "words": cell["words"]})
    say += _for(fit)
    return analyses.OpenDataResult(
        state="measured", summary=say,
        explanation=("Judged from the licence identifier through RE's table of licence ids; "
                     + ("against the purpose the investigation states." if fit
                        else "no purpose stated, so what the licence requires is listed and no fit is judged.")),
        measures={"licences_read": 1, "judged": True, "licence_id": str(licence_id), "requirements": requirements, "fit": fit})


def purposes_for(registry, store, dataset: dict) -> list[dict]:
    """The licence needs stated by the investigations this dataset is a member of (visible to the person running the check)."""
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


def run(ctx: analyses.RunContext) -> analyses.OpenDataResult:
    return judge(ctx.dataset, purposes_for(ctx.registry, ctx.store, ctx.dataset))


RUNNER = analyses.Runner(ANALYSIS_ID, run, lambda dataset: 0)        # reads the stored licence: no portal call
