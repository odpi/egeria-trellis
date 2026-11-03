"""Role-derived expectation sets — what a mature project of this kind should
have, and **where each thing actually is**.

Design reference: `docs/architecture-recovery-design.md` §5.5b, subsection
"The expectation set — and reporting *where*, not *whether*". Third and last
piece of the classification work, composing the two that precede it:
`repo_role.classify_repo_role()` says what the repo is, and
`doc_locations.find_artifact()` says where a given artifact lives.

The maintainer's framing, which this implements literally:

  1. classify the kind of thing the project is,
  2. derive what a mature project of that kind should have, then look for it,
  3. the result is part of the classification, and tells you where to look
     for everything else.

**Three properties this module is designed around, each with a reason.**

*Locations, never booleans.* Every expected artifact resolves to `in-repo`,
`sibling-repo`, `doc-site` or `not-found`, and only the last is an absence.
Finding 68 is why: asked naively, "does Kubernetes document its architecture?"
answers *no* — `kubernetes/kubernetes/docs/` is a tombstone untouched for ~1400
days — when the truth is `kubernetes/website`, updated the same day.

*Absence cuts both ways, so role must be established first.* A missing
deployment artifact in an `application` is a maturity finding; the same absence
in a `library` **confirms** the classification. `trellis.md` records exactly
that case ("no Dockerfiles, no compose files ... it has no deployment
perspective at all"). The identical observation means opposite things depending
on the role, which is why `NOT_EXPECTED` is a first-class output here rather
than an empty set.

*No score, ever.* §5.5a(c) point 4 and §5.5b are explicit, and the reason is
not squeamishness: a checklist becomes a maturity score, and a maturity score
punishes deliberate choices. A small, stable library documents lightly on
purpose; a project may keep tutorials in-tree on purpose. So this module emits
lists of findings with evidence and dates. It deliberately exposes no count, no
percentage and no grade, and callers should resist computing one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import doc_locations as dl
from . import repo_role as rr

# ---------------------------------------------------------------------------
# The expectation sets
# ---------------------------------------------------------------------------
#
# Indicative and explicitly provisional — §5.5b records them as "to be
# validated, not adopted as written". They are stated here as data rather than
# buried in branches precisely so that revising them is an edit to one table.
#
# Kinds are the ones `doc_locations.find_artifact` can actually resolve. There
# is no point expecting something no signal can find: an expectation that can
# never be met would report as a permanent absence and read as a defect in the
# project rather than in this table.

EXPECTED: dict[str, tuple[str, ...]] = {
    rr.ROLE_APPLICATION:   ("readme", "architecture", "deployment", "changelog"),
    rr.ROLE_MIDDLEWARE:    ("readme", "architecture", "deployment", "api-reference"),
    rr.ROLE_LIBRARY:       ("readme", "api-reference", "examples", "changelog"),
    rr.ROLE_TOOL:          ("readme", "deployment", "examples", "changelog"),
    rr.ROLE_TUTORIAL:      ("readme", "examples", "deployment"),
    rr.ROLE_SAMPLES:       ("readme", "examples"),
    rr.ROLE_DOCUMENTATION: ("readme",),
    rr.ROLE_DATASET:       ("readme",),
}

# Deliberate absences. Finding one of these is not a defect, and NOT finding it
# is positive confirmation of the role — the `trellis` library case above.
NOT_EXPECTED: dict[str, tuple[str, ...]] = {
    rr.ROLE_LIBRARY:       ("deployment",),
    rr.ROLE_DOCUMENTATION: ("deployment", "api-reference"),
    rr.ROLE_SAMPLES:       ("architecture", "changelog"),
    rr.ROLE_TUTORIAL:      ("architecture",),
    rr.ROLE_DATASET:       ("deployment", "architecture", "api-reference"),
}

# Roles whose presence means "there may be no architecture here worth
# recovering" — §5.5b's gate.
_NON_ARCHITECTURAL_ROLES = frozenset({rr.ROLE_TUTORIAL, rr.ROLE_SAMPLES, rr.ROLE_DOCUMENTATION, rr.ROLE_DATASET})

# The signals that constitute structural evidence for the gate below.
_STRUCTURAL_SIGNALS = frozenset({"deployment-artifacts", "entry-points", "package-manifest"})

# `package-manifest` is the only structural signal that the documentation
# machinery can produce by itself: a Docusaurus site has a `package.json`
# because Docusaurus is a Node program, not because the documented software
# lives there. When a static-site generator config sits at the repo root, that
# manifest is the generator's, so it stops counting as structural evidence.
# The other two do not have this problem — a docs site has no Dockerfile and no
# entry point of its own.
_GENERATOR_OWNED_SIGNALS = frozenset({"package-manifest"})

RUN = "run"
SKIP = "skip"


@dataclass
class Expectation:
    """One expected (or deliberately not-expected) artifact, resolved to a
    location. `expected=False` entries are the confirmations described in the
    module docstring — absence there supports the role rather than counting
    against the project."""
    kind: str
    outcome: str          # in-repo | sibling-repo | doc-site | not-found
    evidence: str
    date: str | None
    expected: bool

    @property
    def is_absence(self) -> bool:
        return self.outcome == "not-found"


@dataclass
class ExpectationReport:
    """What was expected for this repo's role, and where each thing was found.

    Deliberately four separate lists rather than one list plus a tally: naming
    the four states keeps the reader from collapsing them into a ratio, which
    is the failure mode §5.5b warns about."""
    owner_repo: str
    primary_role: str | None
    roles: list[str] = field(default_factory=list)
    found: list[Expectation] = field(default_factory=list)          # expected, located
    missing: list[Expectation] = field(default_factory=list)        # expected, absent
    confirmations: list[Expectation] = field(default_factory=list)  # not expected, absent
    unexpected: list[Expectation] = field(default_factory=list)     # not expected, yet present
    gate: str = RUN
    gate_reason: str = ""
    notes: list[str] = field(default_factory=list)
    #: "signal: detail" for each piece of evidence behind the `dataset` role ([] when the role did not fire).
    dataset_evidence: list[str] = field(default_factory=list)


def recovery_gate(classification: rr.RepoRoleClassification) -> tuple[str, str]:
    """Should architecture recovery run at all? (§5.5b.)

    **Keyed on containment, not primacy** — this is the correction that
    building the classifier forced. `odpi/egeria-workspaces` ranks `tutorial`
    primary (its README says "designed for learning", and it carries 37
    notebooks) *and* is target T1, from which architecture recovery scored
    18/27. A gate reading the primary role would skip the repo whose deployment
    architecture we have recovered most successfully.

    The question is not "what is this repo mostly?" but "is there an
    architecture here worth recovering?", and structural evidence answers it.
    So: skip only when a non-architectural role is present AND nothing
    structural was found. `egeria-workspaces` runs on its compose files; a pure
    notebook workshop does not.

    **`kubernetes/website` was cited here as a repo this gate skips. It never
    did** — verified live 2026-08-24. It carries a root `Dockerfile` (the site
    is containerised) plus nine more under `content/*/examples/`, which are
    documentation *content*: sample manifests shown to readers. The signal
    cannot tell an artifact that deploys the repo from one the repo is teaching
    about. Measured across the corpus, that distinction changes no gate outcome
    — the two repos whose deployment artifacts all sit under doc paths
    (`docling-serve`, `langchain-opea`) run on entry points and a package
    manifest anyway — so it is recorded rather than fixed. The stale claim is
    corrected here because a rationale that names a repo it gets wrong is worse
    than one that names none.
    """
    roles = {r.role for r in classification.roles}
    non_arch = roles & _NON_ARCHITECTURAL_ROLES
    if not non_arch:
        return RUN, "no tutorial/samples/documentation role present"

    signals = {ev.signal for role in classification.roles for ev in role.evidence}
    discounted = _GENERATOR_OWNED_SIGNALS if rr.SIGNAL_DOC_SITE_GENERATOR in signals else frozenset()
    structural = sorted((signals & _STRUCTURAL_SIGNALS) - discounted)
    if structural:
        return RUN, (f"{'/'.join(sorted(non_arch))} role present, but structural evidence "
                     f"found ({', '.join(structural)}) — there is an architecture here")
    if discounted & _STRUCTURAL_SIGNALS & signals:
        return SKIP, (f"{'/'.join(sorted(non_arch))} role present; a package manifest was found "
                      f"but a static-site generator config sits at the repo root, so that "
                      f"manifest belongs to the generator rather than to documented software "
                      f"— recovering an architecture would be the wrong question")
    return SKIP, (f"{'/'.join(sorted(non_arch))} role present and no structural evidence "
                  f"(deployment artifacts, entry points or a package manifest) — "
                  f"recovering an architecture would be the wrong question")


def expectations_for(role: str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(expected, not_expected)` for one role. Unknown or absent role yields
    empty sets rather than a guess — an expectation set derived from a role we
    could not determine would be inference dressed as a requirement."""
    if not role:
        return (), ()
    return EXPECTED.get(role, ()), NOT_EXPECTED.get(role, ())


def build_report(owner_repo: str, client=None) -> ExpectationReport:
    """Classify `owner/repo`, then resolve every artifact its role implies.

    The **primary** role drives the expectation set — that is what primacy is
    good for, and what it is *only* good for (see `recovery_gate` for why it
    must not drive the run/skip decision too).
    """
    try:
        locations = dl.resolve_doc_locations(owner_repo, client=client)
    except Exception as exc:
        locations = None
    classification = rr.classify_repo_role(owner_repo, client=client, locations=locations)
    primary = classification.primary.role if classification.primary else None
    report = ExpectationReport(
        owner_repo=owner_repo,
        primary_role=primary,
        roles=[r.role for r in classification.roles],
        notes=list(classification.notes),
        dataset_evidence=[f"{e.signal}: {e.detail}" for r in classification.roles if r.role == rr.ROLE_DATASET
                          for e in r.evidence],
    )
    report.gate, report.gate_reason = recovery_gate(classification)

    expected, not_expected = expectations_for(primary)
    if primary is None:
        report.notes.append("no role determined — no expectation set applied")
        return report

    # Resolved ONCE above and reused for the classification and every artifact
    # kind. See find_artifact's note: resolution is identical per kind and
    # dominates the cost.
    for kind in expected + not_expected:
        is_expected = kind in expected
        try:
            found = dl.find_artifact(owner_repo, kind, client=client, locations=locations)
        except Exception as exc:  # never raise out of this module — doc_locations' posture
            report.notes.append(f"lookup for {kind!r} failed: {type(exc).__name__}: {exc}")
            continue
        item = Expectation(kind=kind, outcome=found.outcome, evidence=found.evidence,
                           date=found.date, expected=is_expected)
        if is_expected:
            (report.missing if item.is_absence else report.found).append(item)
        else:
            (report.confirmations if item.is_absence else report.unexpected).append(item)
    return report
