"""The shape of a blueprint: container or contents (DESIGN-BLUEPRINT-BENCHMARK-EGERIA-WORKSPACES.md 6a).

The project owner, 2026-10-08: "either you use the sub-component relationship and make the other servers
subcomponents of the platform and only add the platform to the blueprint (which will show the
encapsulation) or you leave out the omag platform from the blueprint", and "I actually prefer the
sub-component approach". So a cluster is written in ONE of two shapes, never a mix:

  container   the cluster's root is a REAL component. The root is the ONLY blueprint member and each other
              member of the cluster is its sub-component (a `SolutionComposition` root -> child). Egeria draws
              the encapsulation from that metadata. This is the DEFAULT.
  contents    the root is merely a grouping (it exists only as a path, or no member is the root). There is
              nothing to encapsulate with, so the children are the members and no root element is written.

The one test that picks the default is "is the root a real component": true when the root has its own
evidence class (built here or shipped here) or is a content-pack element; false when it exists only as a
path. A person may flip the shape before the write; the plan says which shape and why, in words.

Pure functions over what the recovery already persisted. Nothing here reads or writes Egeria; the
content-pack fact (which only Egeria can answer) arrives on the node as a flag.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from resource_explorer.surveyors.arch_recovery import admission as adm

CONTAINER = "container"
CONTENTS = "contents"
SHAPES = (CONTAINER, CONTENTS)

#: The checks a root with each reason produces. Short sentences, shown beside a short word.
WHY_NO_ROOT = "no member is the cluster's root: it is a grouping"
WHY_PATH_ONLY = "the root exists only as a path"
WHY_CONTENT_PACK = "the root is a content-pack element"


# ── names say what an element represents (the 2026-10-08 naming ruling, 'what it represents') ───────────────
#: The closed list. A sixth word is added only by a ruling. The word lives in displayName and NEVER in a
#: qualifiedName (adoption by name and the SolutionComposition pair keys depend on that).
CODE_MODULE = "code module"
CONTAINER_DEFINITION = "container definition"
IMAGE = "image"
RUNTIME = "runtime"
KINDS = (CODE_MODULE, CONTAINER_DEFINITION, IMAGE, RUNTIME)
#: A blueprint of one kind takes the plural; "runtime" reads the same.
_PLURAL = {CODE_MODULE: "code modules", CONTAINER_DEFINITION: "container definitions",
           IMAGE: "images", RUNTIME: "runtime"}
#: Neutral in a blueprint's kind list: an element the content pack defines carries no word.
CONTENT_PACK = "content_pack"

_MANIFESTS = {"pom.xml", "package.json", "pyproject.toml", "setup.py", "setup.cfg", "cargo.toml", "go.mod",
              "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "build.sbt",
              "composer.json", "gemfile"}
_COMPOSE = re.compile(r"compose.*\.ya?ml$", re.I)

#: Reference data a person extends: an image a content-pack element stands for. Only what the benchmark
#: note proves is here; an unlisted image is simply not matched and gets a component of RE's own.
CONTENT_PACK_IMAGES = {"odpi/egeria-platform": "OMAG Server Platform"}


def content_pack_name_for_image(image: str) -> str:
    return CONTENT_PACK_IMAGES.get(adm.normalise_image(image or ""), "")


def represents_kind(detail: dict | None, *, registered: bool = False, artefact: bool = False,
                    content_pack: bool = False) -> str | None:
    """The word for what an element represents, DERIVED from the evidence class RE already holds, never
    typed. None when the evidence does not say (no suffix beats a wrong one).

    * content_pack: an element Egeria's content pack defines. No word; its name is the pack's.
    * registered: a registered resource or a live read. runtime.
    * artefact: the row is about a built artefact by name (an image the repository publishes or references).
    * otherwise the file evidence: a Dockerfile or compose service (built, shipped or referenced) is a
      container definition; a package manifest is a code module."""
    d = detail or {}
    if content_pack:
        return None
    if registered:
        return RUNTIME
    if artefact:
        return IMAGE if d.get("image") else None
    ev = (d.get("admission_evidence") or "").strip()
    if ev.startswith("image "):
        return CONTAINER_DEFINITION
    if ev.startswith("from "):
        rel = ev[5:].split(" · ", 1)[0].strip()
        base = rel.replace("\\", "/").rsplit("/", 1)[-1]
        low = base.lower()
        if low.startswith("dockerfile") or low.endswith(".dockerfile") or _COMPOSE.search(low):
            return CONTAINER_DEFINITION
        if low in _MANIFESTS:
            return CODE_MODULE
    return None


def display_name(name: str, kind: str | None) -> str:
    """`<name> (<kind>)` for a component RE writes. No kind, a name that already carries it, or a
    sub-resource name (`<path> · <repository>`, which takes no suffix) is returned unchanged."""
    if not kind:
        return name
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not one of the closed list {KINDS}")
    if " · " in name or name.endswith(f" ({kind})"):
        return name
    return f"{name} ({kind})"


def blueprint_suffix_name(base: str, member_kinds: list) -> str:
    """`<Repository> <Kind> Blueprint (<represents>)` when every member is of one kind; a mixed blueprint,
    or one with a member whose kind is not known, takes no suffix. Content-pack members are neutral."""
    kinds = {k for k in member_kinds if k != CONTENT_PACK}
    if not kinds or None in kinds or "" in kinds or len(kinds) != 1:
        return base
    return f"{base} ({_PLURAL[kinds.pop()]})"


@dataclass(frozen=True)
class Node:
    """One member of a cluster, as the shape decision sees it."""

    slug: str
    name: str
    admission: str = adm.BUILT
    structural: bool = False          # a path-only grouping node, with no evidence of its own
    content_pack: bool = False        # Egeria's content pack already defines this component
    guid: str = ""                    # the SolutionComponent GUID, once materialised or adopted
    scope: str = ""
    kind: str | None = None           # what it represents (the five words), derived from its evidence
    image: str = ""


@dataclass
class ShapePlan:
    shape: str
    default_shape: str
    why: str
    words: str
    root: Node | None
    root_is_real: bool
    members: list[Node] = field(default_factory=list)            # direct blueprint members
    compositions: list[tuple[Node, Node]] = field(default_factory=list)   # (container, child)
    flipped: bool = False
    flip_refused: str = ""
    flip_to: str = ""

    def to_dict(self) -> dict:
        return {
            "shape": self.shape, "default_shape": self.default_shape, "why": self.why,
            "words": self.words, "root": self.root.name if self.root else "",
            "root_slug": self.root.slug if self.root else "", "root_is_real": self.root_is_real,
            "members": [n.slug for n in self.members],
            "compositions": [[a.slug, b.slug] for a, b in self.compositions],
            "flipped": self.flipped, "flip_refused": self.flip_refused, "flip_to": self.flip_to,
        }


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def find_root(cluster_name: str, nodes: list[Node], composed_into: str = "") -> Node | None:
    """The member the cluster is named after. The recovery names a cluster after its root, so the root is
    the member whose name or slug is the cluster's name (or the slug a composition cluster says it
    composes). None when no member is that: the name is only a path."""
    for target in (composed_into, cluster_name):
        key = _norm(target)
        if not key:
            continue
        for n in nodes:
            if n.slug == target or _norm(n.name) == key or _norm(n.slug) == key:
                return n
    return None


def root_is_real_component(root: Node | None) -> tuple[bool, str]:
    """THE test (6a): is the root a real component. True when it is a content-pack element or has its own
    evidence class (built here, shipped here); false when it exists only as a path, or is only referenced."""
    if root is None:
        return False, WHY_NO_ROOT
    if root.content_pack:
        return True, WHY_CONTENT_PACK
    if root.structural:
        return False, WHY_PATH_ONLY
    if root.admission in (adm.BUILT, adm.SHIPPED):
        return True, f"the root is a real component: {adm.WORDS[root.admission]}"
    return False, "the root is referenced only: no evidence of its own here"


def plan_shape(cluster_name: str, nodes: list[Node], *, requested: str = "",
               composed_into: str = "") -> ShapePlan:
    """Decide the shape and what it writes. `requested` is a person's flip ("" = take the default).

    A flip to CONTAINER is refused when there is no root to be the container, or when the root is only a
    path; the plan then keeps the default and says why. A flip to CONTENTS is always possible."""
    root = find_root(cluster_name, nodes, composed_into)
    real, why_root = root_is_real_component(root)
    default = CONTAINER if real else CONTENTS
    shape, refused = default, ""
    if requested in SHAPES and requested != default:
        if requested == CONTAINER and (root is None or root.structural):
            refused = ("no member is the root, so there is nothing to be the container" if root is None
                       else "the root is only a path, so there is no component to be the container")
        else:
            shape = requested
    flipped = shape != default
    children = [n for n in nodes if root is None or n.slug != root.slug]

    if shape == CONTAINER:
        members = [root]
        compositions = [(root, c) for c in children]
        words = (f"{root.name} as container · {len(children)} sub-component"
                 f"{'' if len(children) == 1 else 's'}")
    else:
        members = list(children)
        compositions = []
        if root is None:
            words = f"root is a grouping · {len(children)} member{'' if len(children) == 1 else 's'}"
        else:
            words = (f"{root.name} left out · {len(children)} member{'' if len(children) == 1 else 's'}")
    why = why_root
    if flipped:
        why = f"{why_root}; flipped to {shape} by a person"
        words += " · flipped"
    return ShapePlan(shape=shape, default_shape=default, why=why, words=words, root=root,
                     root_is_real=real, members=members, compositions=compositions, flipped=flipped,
                     flip_refused=refused,
                     flip_to=(CONTENTS if shape == CONTAINER else CONTAINER))


def plan_with_alternatives(cluster_name: str, nodes: list[Node], *, composed_into: str = "",
                           requested: str = "") -> dict:
    """The plan as a dict (the default, or the person's stored flip when `requested` is given: what the next
    Publish will write), plus what each shape would say (`alternatives`), so a screen can flip
    the manifest line before the write without asking the server again. A shape that cannot be had
    (no root to be the container) shows the default's words and the reason it was refused."""
    plan = plan_shape(cluster_name, nodes, requested=requested, composed_into=composed_into).to_dict()
    plan["alternatives"] = {}
    for shape in SHAPES:
        alt = plan_shape(cluster_name, nodes, requested=shape, composed_into=composed_into)
        plan["alternatives"][shape] = {"shape": alt.shape, "words": alt.words, "why": alt.why,
                                       "flip_refused": alt.flip_refused}
    return plan


def distinct_name(display_name: str, member_names: list[str]) -> str:
    """A blueprint's name never equals a member's name (6a rule 1). The name is the kind and the
    repository; if a member happens to carry exactly that name, the blueprint says so with a suffix."""
    taken = {_norm(n) for n in member_names}
    name = display_name
    while _norm(name) in taken:
        name = f"{name} (blueprint)"
    return name


def component_nodes(registry, slug: str, snapshot: dict | None = None) -> dict[str, Node]:
    """{component slug: Node} for every live architecture_recovery component finding of a resource, newest
    row per scope. `Node.scope` is the scope_locator verdicts and materialisation are keyed by (the
    identity-mismatch trap the plan names: clustering keys members by slug).

    Read from the shared recovery snapshot (one bulk read, cached on the data's fingerprint) rather than one
    query per scope; a caller that already holds the snapshot passes it."""
    from resource_explorer.surveyors.repo_survey_definition_adapter import _recovery_scopes, _recovery_snapshot
    snapshot = snapshot if snapshot is not None else _recovery_snapshot(registry, slug)
    rows_by_scope = snapshot["rows"]
    out: dict[str, Node] = {}
    for scope in _recovery_scopes(registry, slug, "component", snapshot):
        rows = [r for r in rows_by_scope.get(scope, [])
                if r["check_name"] == "component"]
        if not rows:
            continue
        latest = max(rows, key=lambda r: r["surveyed_at"])
        try:
            detail = json.loads(latest.get("detail_json") or "{}") if latest.get("detail_json") else {}
        except (ValueError, TypeError):
            detail = {}
        if not detail.get("slug"):
            continue
        out[detail["slug"]] = Node(
            slug=detail["slug"], name=detail.get("name") or detail["slug"],
            admission=detail.get("admission") or adm.BUILT,
            structural=bool(detail.get("structural")), scope=scope,
            kind=represents_kind(detail), image=detail.get("image") or "")
    return out
