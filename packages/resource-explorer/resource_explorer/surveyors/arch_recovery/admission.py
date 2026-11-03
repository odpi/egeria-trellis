"""Whose boundary is it? The admission rule for a blueprint's nodes (DESIGN-BLUEPRINT-NODE-ADMISSION.md).

A node enters a repository's blueprint by the EVIDENCE CLASS of its boundary, and the class is shown on
the node:

  built_here       a compose `build:` context or Dockerfile inside the repository; a package manifest,
                   Gradle module or first-party code marker. A proposed component.
  shipped_here     a compose service that runs an image THIS repository publishes (its image name matches
                   a service the repository also builds, a CI publish step, or a manifest's package
                   name). A proposed component.
  referenced_only  a compose service running an image the repository neither builds nor publishes. NOT a
                   component: a runtime dependency of the deployment the repository describes.

Pure functions over files already on disk; nothing here reads a registry or contacts Egeria. The detector
(`detectors.build_components`) tags each component; `split` separates the admitted from the referenced-only
ones, and says what was found and not admitted.
"""
from __future__ import annotations

import os
import re

import yaml

BUILT = "built_here"
SHIPPED = "shipped_here"
REFERENCED = "referenced_only"
CLASSES = (BUILT, SHIPPED, REFERENCED)

#: The short word shown on a node.
WORDS = {BUILT: "built here", SHIPPED: "shipped here", REFERENCED: "referenced only"}

#: A repository whose every deployable is someone else's image.
ZERO_COMPONENT_SENTENCE = ("this repository deploys other software and builds none of its own: "
                           "see Dependencies · runtime")

#: Directory names under which a compose file is a fixture or an example, never a deployment of this
#: repository ("Not a component ever").
FIXTURE_DIRS = {"test", "tests", "testdata", "fixtures", "fixture", "example", "examples", "sample", "samples"}

_VAR = re.compile(r"\$\{[^}:\-?]*(?::?-([^}]*))?\}")
_CI_TAG = re.compile(r"^\s*(?:-\s*)?(?:tags?|images?)\s*:\s*(?P<v>.+?)\s*$", re.I)
_CI_CMD = re.compile(r"docker\s+(?:image\s+)?(?:push|tag|build\b.*?(?:-t|--tag))\s+(?P<v>\S+)", re.I)


def normalise_image(image: str) -> str:
    """`${REPO:-odpi}/egeria-platform:${TAG:-latest}` -> `odpi/egeria-platform`. Registry host, tag,
    digest and variable wrappers are dropped; a variable with no default vanishes."""
    s = _VAR.sub(lambda m: m.group(1) or "", (image or "").strip().strip("'\""))
    s = s.split("@", 1)[0]
    parts = s.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = parts[1:]                                   # registry host
    if parts:
        parts[-1] = parts[-1].split(":", 1)[0]               # tag
    return "/".join(p for p in parts if p).lower()


def same_image(a: str, b: str) -> bool:
    """Two image names are one image only when their normalised paths (registry host, tag and digest
    removed) are EQUAL. `redis` is not `bitnami/redis` and `egeria-platform` is not
    `odpi/egeria-platform`: matching last segments across namespaces turned every third-party image that
    shares a word with a package of this repository into one the repository ships."""
    na, nb = normalise_image(a), normalise_image(b)
    return bool(na) and na == nb


def is_fixture_path(rel: str) -> bool:
    return any(seg.lower() in FIXTURE_DIRS for seg in rel.replace("\\", "/").split("/")[:-1])


def compose_service_facts(root: str, rel: str) -> dict[str, dict]:
    """{service key: {"image", "build", "dockerfile"}} for one compose file. `build` is the build context
    when it is inside the repository ("." counts), "" when there is none or it is a remote URL;
    `dockerfile` is the `dockerfile:` of a mapping-form build (relative to the context), else ""."""
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            data = yaml.safe_load(fh.read())
    except (OSError, yaml.YAMLError):
        return {}
    services = (data or {}).get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return {}
    out: dict[str, dict] = {}
    for key, body in services.items():
        if not isinstance(key, str) or not isinstance(body, dict):
            continue
        build = body.get("build")
        ctx = ""
        dockerfile = ""
        if isinstance(build, str):
            ctx = build
        elif isinstance(build, dict):
            ctx = str(build.get("context") or ".")
            dockerfile = str(build.get("dockerfile") or "")
        remote = bool(re.match(r"^(https?://|git@|git://|github\.com/)", ctx))
        # Windows separators in a path read the same everywhere (a remote URL has none that matter).
        ctx, dockerfile = ctx.replace("\\", "/"), dockerfile.replace("\\", "/")
        image = body.get("image")
        out[key] = {"image": image.strip() if isinstance(image, str) else "",
                    "build": "" if remote else (ctx or ("." if build else "")),
                    "dockerfile": "" if remote else dockerfile}
    return out


def published_images(root: str, files: list[str], package_names: list[str]) -> dict[str, str]:
    """{image name: where this repository says it publishes it}: only what a PUBLISH SIGNAL says.

    * a CI workflow step that builds, tags or pushes a NAMESPACED image (`odpi/egeria-ui`);
    * a CI step that pushes a bare image name, only when it equals one of this repository's manifest
      package names (the manifest names what the bare image is; the CI step is the signal).

    A manifest package name alone is NOT a publish signal: a repository with a package called `redis` does
    not ship `bitnami/redis`. A compose service that builds and names an image is a signal too; the caller
    adds those (it already has the compose facts). Anything undecidable stays referenced only."""
    out: dict[str, str] = {}
    packages = {normalise_image(n) for n in package_names if n}
    for rel in files:
        low = rel.replace("\\", "/")
        if not (low.startswith(".github/workflows/") or low.startswith(".gitlab-ci") or
                low.endswith((".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", ".circleci/config.yml"))):
            continue
        try:
            text = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            m = _CI_CMD.search(line)
            names = [m.group("v")] if m else []
            t = _CI_TAG.match(line)
            if t:
                names += [x for x in re.split(r"[,\s]+", t.group("v")) if x and not x.startswith(("|", ">"))]
            for name in names:
                norm = normalise_image(name)
                if not norm:
                    continue
                if "/" in norm or norm in packages:
                    out.setdefault(norm, f"{rel}:{n}")
    return out


def classify_compose_service(facts: dict, decl: str, published: dict[str, str]) -> tuple[str, str]:
    """(class, evidence sentence) for one merged compose service. Order matters: a build context in the
    repository is `built_here`, whether or not the image is also named; a named image the repository
    publishes is `shipped_here`; a named image otherwise is `referenced_only`. A service with neither a
    build nor an image has no declared boundary of its own, so it is admitted as built here with that
    said, rather than silently dropped."""
    image, build = facts.get("image", ""), facts.get("build", "")
    if build:
        return BUILT, f"from {decl}" + (f" · build {build}" if build != "." else "")
    if image:
        for pub, where in published.items():
            if same_image(image, pub):
                return SHIPPED, f"image {normalise_image(image)} · published by {where}"
        return REFERENCED, f"image {normalise_image(image)} · from {decl}"
    return BUILT, f"from {decl} · no image named"


def split(components: list) -> tuple[list, list]:
    """(admitted components, referenced-only components)."""
    admitted = [c for c in components if c.admission != REFERENCED]
    referenced = [c for c in components if c.admission == REFERENCED]
    return admitted, referenced
