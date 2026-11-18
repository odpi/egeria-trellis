"""Which HTML files in a repository are site-generator scaffolding, not documentation?

The repo web_docs collection takes every .html file in the clone. For a docs-site repository that
includes the site generator's template folders: egeria-docs' `site/overrides/` (mkdocs-material's
custom_dir) held 6 template files (licence headers, Jinja blocks, CSS links) plus a meta-refresh
redirect stub, and together they made the whole of egeria_docs' web_docs collection, 7 chunks of noise that chat
could cite as documentation (owner, 2026-11-11, option A).

Two rules, both conservative so a real page is never dropped:
- TEMPLATE: under a conventional template folder (any path segment in SITE_TEMPLATE_DIRS) AND not a
  complete page (it carries template syntax, or has no <html>/<!DOCTYPE>). A rendered page that
  happens to live in a folder called `partials` is a complete document with no template syntax,
  and is kept.
- REDIRECT STUB: a meta-refresh redirect whose visible text is a sentence at most (under
  REDIRECT_TEXT_LIMIT characters). It has nothing to retrieve.
"""
from __future__ import annotations

import re
from pathlib import PurePath

#: Template folders of the common site generators: mkdocs-material custom_dir (`overrides`, and its
#: `partials`), Sphinx (`_templates`), Jekyll (`_layouts`, `_includes`), Hugo (`layouts`, `partials`).
SITE_TEMPLATE_DIRS = frozenset({"overrides", "partials", "_templates", "_layouts", "_includes", "layouts"})

REDIRECT_TEXT_LIMIT = 300

_TEMPLATE_SYNTAX = re.compile(r"\{%|\{\{|\{#")          # Jinja / Liquid / Go templates
_FULL_DOCUMENT = re.compile(r"<html[\s>]|<!doctype\s+html", re.I)
_META_REFRESH = re.compile(r"<meta[^>]+http-equiv\s*=\s*[\"']?refresh", re.I)
_TAG_OR_SCRIPT = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<[^>]+>", re.I | re.S)


def in_template_dir(rel_path: str | PurePath) -> bool:
    """True when a DIRECTORY on the path (never the file name) is a template folder."""
    return any(part in SITE_TEMPLATE_DIRS for part in PurePath(rel_path).parts[:-1])


def is_redirect_stub(content: str) -> bool:
    if not _META_REFRESH.search(content):
        return False
    text = re.sub(r"\s+", " ", _TAG_OR_SCRIPT.sub(" ", content)).strip()
    return len(text) < REDIRECT_TEXT_LIMIT


def skip_reason(rel_path: str | PurePath, content: str) -> str | None:
    """Why this HTML file is not documentation ("site template" / "redirect stub"), or None."""
    if in_template_dir(rel_path) and (_TEMPLATE_SYNTAX.search(content) or not _FULL_DOCUMENT.search(content)):
        return "site template"
    if is_redirect_stub(content):
        return "redirect stub"
    return None
