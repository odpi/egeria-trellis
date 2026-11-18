#!/usr/bin/env python3
"""Decide whether a CI job's heavy steps should run for a pull request.

Used by the egeria-advisor and resource-explorer workflows so their jobs can be REQUIRED checks:
the job always runs and reports, but skips its suite when the PR changes nothing it tests. A
path-filtered workflow can't be required, because on a PR it doesn't match, it never reports and
the merge waits forever.

  changed_paths.py --base <sha> --head <sha> --include PAT [PAT ...]   # run if ANY changed file matches
  changed_paths.py --base <sha> --head <sha> --ignore  PAT [PAT ...]   # run if ANY changed file does NOT match

Patterns use GitHub's path-filter glob: `*` matches within one path segment, `**` across segments.
Writes `run=true|false` to $GITHUB_OUTPUT (or stdout) and prints the deciding files. Any error
(an unknown sha, a failed git diff) exits non-zero, failing the job rather than skipping it.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys


def glob_to_regex(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def decide(files: list[str], include: list[str] | None = None, ignore: list[str] | None = None) -> tuple[bool, list[str]]:
    """(run, deciding files). include: files that match. ignore: files that don't."""
    if include is not None:
        pats = [glob_to_regex(p) for p in include]
        hits = [f for f in files if any(p.match(f) for p in pats)]
    else:
        pats = [glob_to_regex(p) for p in ignore or []]
        hits = [f for f in files if not any(p.match(f) for p in pats)]
    return bool(hits), hits


def changed_files(base: str, head: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", base, head], check=True,
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--include", nargs="+")
    group.add_argument("--ignore", nargs="+")
    args = ap.parse_args(argv)
    files = changed_files(args.base, args.head)
    run, hits = decide(files, include=args.include, ignore=args.ignore)
    why = "matching" if args.include is not None else "outside the ignore list"
    print(f"{len(files)} changed file(s); {len(hits)} {why}; run={str(run).lower()}")
    for f in hits[:50]:
        print(f"  {f}")
    line = f"run={str(run).lower()}\n"
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a") as fh:
            fh.write(line)
    else:
        sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
