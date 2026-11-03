"""The Architecture IR — design doc §5.3.

A normalised intermediate representation produced BEFORE any Egeria write.
Everything downstream (projection, curation, drift, scoring) reads this, which
is what makes re-derivation and re-publication separable operations and lets
the whole spike be tested without an Egeria server.

Phase 0 also treats the IR as a hypothesis under test: if it cannot represent
what the detectors found, §5.3 needs revising before Phase 1. Deliberately
kept close to what §5.4 and §8.2 specify rather than inventing convenience
fields — a field the design doc does not name is a finding to report, not a
field to quietly add.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal

ANALYZER = "arch-spike"
ANALYZER_VERSION = "0.1.0"

# design doc §3.1 — SolutionComponentType.java, the closed 13-value vocabulary
COMPONENT_TYPES = (
    "Automated Action", "Long Running Daemon", "Multi-Step Process",
    "Third Party Process", "Manual Process", "Data Storage", "Software Service",
    "Software Library", "User Interface", "Console Command", "Data Distribution",
    "Publishing", "Insight Model",
)

# design doc §3.3b — Egeria's stock ConfidenceLevel, used unextended. Provenance,
# not degree; degree lives in `confidence` as an int 0-100.
ConfidenceLevel = Literal["Unclassified", "Ad Hoc", "Transactional",
                          "Authoritative", "Derived", "Obsolete", "Other"]


@dataclass
class Location:
    path: str
    line: int = 0
    excerpt: str = ""


@dataclass
class Evidence:
    """One justification for one claim (§5.4). A claim is a single assertion —
    "this is a Software Service" — not a whole component."""
    subject_kind: Literal["component", "port", "wire"]
    subject_slug: str
    assertion: str
    detector: str
    locations: list[Location] = field(default_factory=list)
    confidence: int = 50                        # 0-100, §3.3b
    confidence_level: str = "Derived"           # provenance, §3.3b


@dataclass
class Identity:
    """How this component's identity was established — design doc §8.2's
    precedence chain. `method` records which rung of the chain fired, because
    a component identified by deployment unit is a materially stronger claim
    than one identified by falling through to module path."""
    method: Literal["deployment-unit", "package-name", "module-path"]
    value: str
    deployment_context: str = ""    # qualifies the slug where one exists (§8.2)


@dataclass
class Component:
    slug: str
    name: str
    type: str | None
    identity: Identity
    files: list[str] = field(default_factory=list)      # globs, not paths
    confidence: int = 50
    confidence_level: str = "Derived"
    blueprint: str = ""                                  # solution deployment (§8.2b)
    # Which approach(es) proposed this component. Plural because independent
    # agreement is a signal in its own right (Phase 1 §4.4, portfolio note §3).
    proposed_by: list[str] = field(default_factory=list)
    # design §4.1 — physical/deployment/logical/dev. NOT interchangeable, and a
    # component is only ever comparable to ground truth in its own perspective
    # (plan §5a). Finding 15/16: a Dockerfile-directory component and the
    # compose-service component it builds are not duplicates of one thing —
    # they are physical and deployment readings of the same system, related
    # one-to-many by ImplementedBy (§3.6, §4.2 "map, never merge"), so scoring
    # must keep them apart rather than reconcile them into one.
    perspective: str = "physical"
    # Granularity is not precision (approach-portfolio-model.md §2a) — a
    # generator does not choose a depth, it emits the whole hierarchy and
    # links each candidate to its nearest candidate ancestor. `depth` counts
    # path segments below the owning package root (0 = attaches directly to
    # the package root); `parent_slug` is that ancestor's `slug`, or "" when
    # no shallower candidate exists (this component IS the coarsest
    # available reading for its branch). Nothing else about a Component's
    # shape changes — a nested component is still a component, so the
    # classifier, evidence model and persistence work unchanged (design
    # §3.3a — `SolutionComposition` nests `SolutionComponent`s natively, so
    # this is what the target model already expects, not a workaround).
    parent_slug: str = ""
    depth: int = 0
    # Whose boundary this is (DESIGN-BLUEPRINT-NODE-ADMISSION.md): "built_here" (a build
    # context, Dockerfile, manifest or first-party marker in this repository), "shipped_here"
    # (an image this repository publishes) or "referenced_only" (an image it neither builds
    # nor publishes: a runtime dependency, never a component). `admission_evidence` is the
    # short "from <file>" / "image <name>" sentence shown on the node; `image` is the image
    # a compose service runs, when it names one.
    admission: str = "built_here"
    admission_evidence: str = ""
    image: str = ""

    def __post_init__(self) -> None:
        if self.type is not None and self.type not in COMPONENT_TYPES:
            raise ValueError(f"{self.type!r} is not one of the 13 §3.1 types")


@dataclass
class IR:
    target: str
    checkout: str
    census: dict = field(default_factory=dict)
    components: list[Component] = field(default_factory=list)
    # Populated by `interfaces.propose()` (design §5.5f) — the comment here read
    # "not in this slice" until 2026-08-24, three weeks after it stopped being
    # true. Ports are the exposed surface (compose `ports:`/`expose:`,
    # Dockerfile EXPOSE, k8s Service, declared entry points); wires are the
    # links between components, attributed to the SOURCE component.
    ports: list[dict] = field(default_factory=list)
    wires: list[dict] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # things worth reporting

    @property
    def analyzer(self) -> dict:
        # §6.2: without analyzerVersion, a metric that moves between two runs is
        # ambiguous — did the code change, or did our detector improve?
        return {"analyzerName": ANALYZER, "analyzerVersion": ANALYZER_VERSION}

    def to_json(self) -> str:
        return json.dumps({
            "target": self.target,
            "checkout": self.checkout,
            "analyzer": self.analyzer,
            "census": self.census,
            "components": [asdict(c) for c in self.components],
            "ports": self.ports,
            "wires": self.wires,
            "evidence": [asdict(e) for e in self.evidence],
            "notes": self.notes,
        }, indent=2)
