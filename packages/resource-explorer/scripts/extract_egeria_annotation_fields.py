"""Write the fields Egeria's annotation property beans accept, for the strict test fakes.

Egeria's `*AnnotationProperties` beans are `@JsonIgnoreProperties(ignoreUnknown=true)`: a field the bean has no
setter for is dropped on deserialisation and nothing says so (live 2026-10-15: every `candidateClassifications` RE
sent on a ClassificationAnnotation). Jackson binds a JSON field through the bean's `setXxx` method, so the fields a
bean keeps are its setters plus those of every class it extends, plus `class` (the `@JsonTypeInfo` type id).

Reads an Egeria checkout at a release tag (default V6.2, `git show`, so the working tree's branch does not
matter) and writes `tests/fixtures/egeria_6_2/annotation_properties_fields.json`, one entry per
property class RE publishes (`annotation_props.build_annotation_props`' class map). `--check` fails when the file
differs from what the checkout gives (run it after an Egeria upgrade).

    uv run python scripts/extract_egeria_annotation_fields.py --egeria ~/localGit/egeria-v6/egeria [--ref V6.2] [--check]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "tests" / "fixtures" / "egeria_6_2" / "annotation_properties_fields.json"
BEANS = ("open-metadata-implementation/frameworks/open-metadata-framework/src/main/java/"
         "org/odpi/openmetadata/frameworks/openmetadata/properties")

_EXTENDS = re.compile(r"public\s+class\s+(\w+)\s+extends\s+(\w+)")
_SETTER = re.compile(r"public\s+void\s+set([A-Z]\w*)\s*\(")


def _classes_re_publishes() -> list[str]:
    sys.path.insert(0, str(HERE.parent))
    from resource_explorer.surveyors.annotation_props import EGERIA_PROPERTIES_CLASSES

    return sorted(set(EGERIA_PROPERTIES_CLASSES.values()) | {"AnnotationProperties"})


def _jackson_name(setter_suffix: str) -> str:
    """Jackson's default (legacy) property name for `setXxx`: every leading capital lower-cased.

    `setQualifiedName` -> `qualifiedName`, `setURL` -> `url`, `setURLPath` -> `urlpath` (BeanUtil's legacy
    mangling; Egeria's beans set no USE_STD_BEAN_NAMING).
    """
    i = 0
    while i < len(setter_suffix) and setter_suffix[i].isupper():
        i += 1
    return setter_suffix[:i].lower() + setter_suffix[i:]


def _git(egeria: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(egeria), *args], capture_output=True, text=True, check=True).stdout


def _fields(egeria: Path, ref: str, name: str, index: dict[str, str]) -> tuple[list[str], list[str]]:
    """(setter-backed fields of `name` and its superclasses, the chain walked)."""
    fields: set[str] = set()
    chain: list[str] = []
    while name in index:
        src = _git(egeria, "show", f"{ref}:{index[name]}")
        chain.append(name)
        fields |= {_jackson_name(m) for m in _SETTER.findall(src)}
        ext = _EXTENDS.search(src)
        name = ext.group(2) if ext else ""
    return sorted(fields | {"class"}), chain


def build(egeria: Path, ref: str) -> dict:
    paths = _git(egeria, "ls-tree", "-r", "--name-only", ref, "--", BEANS).split()
    index = {Path(p).stem: p for p in paths if p.endswith(".java")}
    commit = _git(egeria, "rev-parse", "--short", f"{ref}^{{commit}}").strip()
    out: dict = {"_source": {"egeria_ref": ref, "egeria_commit": commit, "beans": BEANS,
                             "rule": "setters of the bean and its superclasses, plus the class type id"}}
    for cls in _classes_re_publishes():
        if cls not in index:
            raise SystemExit(f"{cls}: no such bean in {egeria} at {ref}")
        fields, chain = _fields(egeria, ref, cls, index)
        out[cls] = {"chain": chain, "fields": fields}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--egeria", required=True, type=Path)
    ap.add_argument("--ref", default="V6.2", help="the Egeria release the dev platform runs (default V6.2)")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = json.dumps(build(a.egeria.expanduser(), a.ref), indent=2, sort_keys=True) + "\n"
    if a.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} is stale; rerun without --check", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
