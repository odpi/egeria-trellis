"""stages/enrichment.js's three `saveEnrichmentField` call sites used to omit
the entity type entirely, so they always hit `saveEnrichmentField`'s (pre-fix)
hardcoded `/api/context/repo/{slug}/field` -- a database/filesystem
Enrichment save landed silently in the repo context bucket under that slug,
not the resource's own bucket. See test_re_api_entity_type_threading.py for
the `saveEnrichmentField` definition-level fix; this file pins that all three
call sites in enrichment.js actually pass the resource's real entity type.
"""
from __future__ import annotations

from pathlib import Path

ENRICHMENT_JS = (
    Path(__file__).resolve().parents[1]
    / "resource_explorer" / "web" / "static" / "next" / "stages" / "enrichment.js"
)


def _source() -> str:
    return ENRICHMENT_JS.read_text()


def test_api_entity_type_is_imported():
    src = _source()
    assert "apiEntityType" in src
    assert "from '/static/next/app.js'" in src


def test_every_save_call_site_passes_the_entity_type():
    src = _source()
    calls = src.count("saveEnrichmentField(")
    threaded = src.count("apiEntityType(state.resourceType)")
    # Three call sites in this module (judgement/observation save, the
    # owner-interim button, the licence-confirm button) -- every one of them
    # must thread the real entity type through, not just the import existing.
    assert calls == 4, f"expected 4 call sites, found {calls} -- update this test if that changes"
    assert threaded >= 3
