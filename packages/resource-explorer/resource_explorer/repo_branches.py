"""Which refs of a repository a survey run measured or listed (TR-132 C1), recorded in `repo_branch_runs`.

C1 lays the ground and creates nothing in Egeria except, behind its gates, the Branch classification on the default branch's
EXISTING root folder (surveyors/egeria_publisher.py). Here: resolve the ref the survey READ (name, kind, commit), list every
branch the GitHub API knows (metadata only) and store one row per ref per run: the surveyed ref as tier 'surveyed', every other
branch as tier 'listed'. A failure to list is said in words on the surveyed row; it never stops the survey.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: The surveyed ref's fields on a step run's metrics and on the SurveyReport's additionalProperties.
REF_FIELDS = ("surveyed_ref", "ref_kind", "surveyed_commit")


def ref_fields(ref: dict | None) -> dict:
    """{surveyed_ref, ref_kind, surveyed_commit} from a resolved ref, or {} when the run read no code (never made-up values)."""
    if not ref or not ref.get("ref_name"):
        return {}
    return {"surveyed_ref": ref["ref_name"], "ref_kind": ref.get("ref_kind") or "", "surveyed_commit": ref.get("surveyed_commit") or ""}


def record_repo_branches(registry, slug: str, run_id: str, client, repo, requested_ref: str | None = None,
                         read_commit: str | None = None) -> dict:
    """Resolve the surveyed ref, list the branches, store the run's rows. Returns the ref's `resolve_ref` dict plus
    `is_default`, `branches_listed` and `words`. `read_commit` is the SHA the run's zipball or clone served; for the default branch it is the surveyed commit. Never raises: a ref that cannot be resolved or a listing that fails is said in
    `words` and the surveyed row is still written when there is a ref."""
    words: list[str] = []
    try:
        ref = client.resolve_ref(repo, requested_ref)
    except Exception as exc:  # noqa: BLE001 -- recorded in words; a survey is not stopped for this
        log.warning("could not resolve the surveyed ref of %s: %s", slug, exc)
        return {"ref_name": "", "ref_kind": "", "surveyed_commit": "", "is_default": False, "branches_listed": 0,
                "words": f"the surveyed ref could not be resolved: {exc}"[:300]}
    if read_commit and not requested_ref and ref["ref_kind"] == "branch":
        ref = {**ref, "surveyed_commit": read_commit}      # the commit the run actually READ, not what the branch points to now
    default = getattr(repo, "default_branch", "") or ""
    is_default = ref["ref_kind"] == "branch" and ref["ref_name"] == default
    branches: list[dict] = []
    try:
        branches, more = client.list_branches(repo)
        if more:
            words.append(f"more than {len(branches)} branches: only the first {len(branches)} are listed")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not list the branches of %s: %s", slug, exc)
        words.append(f"the branches could not be listed: {exc}"[:200])
    listed_by_name = {b["name"]: b for b in branches}
    rows = [{"ref_name": ref["ref_name"], "ref_kind": ref["ref_kind"], "surveyed_commit": ref["surveyed_commit"],
             "tier": "surveyed", "is_default": is_default,
             "protected": (listed_by_name.get(ref["ref_name"]) or {}).get("protected") if ref["ref_kind"] == "branch" else None,
             "words": "; ".join(words)}]
    for b in branches:
        if ref["ref_kind"] == "branch" and b["name"] == ref["ref_name"]:
            continue
        rows.append({"ref_name": b["name"], "ref_kind": "branch", "surveyed_commit": b["head_sha"], "tier": "listed",
                     "is_default": b["name"] == default, "protected": b["protected"]})
    registry.record_repo_branch_runs(slug, run_id, rows)
    return {**ref, "is_default": is_default, "branches_listed": len(branches), "words": "; ".join(words)}
