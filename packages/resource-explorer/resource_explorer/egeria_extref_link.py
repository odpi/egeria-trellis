"""Link an element to an ExternalReference once: the one place RE makes an `ExternalReferenceLink`.

`ExternalReferenceLink` is MULTI-LINK in Egeria (`OpenMetadataTypesArchive1_2`, `setMultiLink(true)`):
every link call creates another relationship between the same two elements, it is never merged or
refused. A read of dev Egeria on 2026-10-14 found 132 links, 87 of them duplicates: repo surveys made one
per run (the homepage publisher), Enrichment doc-source publish made a few, one from an outbox retry.

So RE links only after reading the reference's existing links and finding none to this element; the
read, its paging and its three outcomes (already linked / linked / could not check) are the shared
`egeria_link_once.link_once`. The read starts at the reference: it is end 2 of the type (end 1 is the
referencing element).
"""
from __future__ import annotations

from resource_explorer.egeria_link_once import (  # noqa: F401 - re-exported for existing callers
    ALREADY_LINKED,
    COULD_NOT_CHECK,
    LINK_FAILED,
    LINKED,
    MAX_PAGES,
    PAGE_SIZE,
    LinkOutcome,
    link_once,
)

RELATIONSHIP_TYPE = "ExternalReferenceLink"
#: The reference sits at end 2 of ExternalReferenceLink; the referencing element at end 1.
REFERENCE_END = 2


def link_external_reference_once(metadata_expert, external_references, element_guid: str, ref_guid: str,
                                 *, page_size: int = PAGE_SIZE) -> LinkOutcome:
    """Link `element_guid` to the ExternalReference `ref_guid` unless a link between exactly these two
    already exists. Never raises; failures come back as the outcome, logged at warning."""
    return link_once(
        metadata_expert, relationship_type=RELATIONSHIP_TYPE, read_guid=ref_guid, read_end=REFERENCE_END,
        far_guid=element_guid, page_size=page_size, what=f"from {element_guid} to {ref_guid}",
        create=lambda: external_references.link_external_reference(element_guid, ref_guid))
