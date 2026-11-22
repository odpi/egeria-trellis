"""Bridge from RE's ingestion walk to the shared containment tree.

Off by default (`ARTIFACT_TREE_ENABLED`). Off means today's behaviour exactly:
this module opens no connection, creates no table, and does no work.

On, it builds a tree per artifact **in addition to** the chunks that walk
already produces. Not instead of: chunks are retrieval units sized by content
profile, tree nodes are containment units that compression rungs are cut from,
and neither derives from the other (docs/context-compilation-design.md §15). The
two come from the same walk over the same files -- one read, two representations.

**Never breaks ingestion, and never lies about it.** A failure here does not
propagate -- a tree is an optimisation over a working pipeline and a broken one
must not cost the corpus -- but it is not swallowed into a success-shaped return
either. build_trees() hands back a TreeBuildResult carrying an explicit status,
so "the feature is off", "no adapter for this language", "some files were
skipped" and "the store was unreachable" are distinguishable outcomes rather
than four zeros. Returning a bare 0 for all of them is the exact shape
tests/test_no_silent_success.py ratchets against, and it was the first thing
this module got wrong.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_schema_ready = False


def _reset_for_tests() -> None:
    """Testing hook -- the schema-created guard is process-global."""
    global _schema_ready
    _schema_ready = False


@dataclass(frozen=True)
class TreeBuildResult:
    """What actually happened, in a form a caller can branch on.

    status:
      "disabled"    -- feature off; nothing attempted, nothing wrong
      "stored"      -- every artifact parsed and persisted
      "partial"     -- some skipped; `skipped` says how many
      "unsupported" -- no adapter for this kind; a gap to fill, not a failure
      "unavailable" -- setup failed; nothing stored, `reason` says why
    """

    status: str
    stored: int = 0
    skipped: int = 0
    reason: str = ""
    # Trees that parsed fine and say nothing useful -- a rasterised page or a
    # scanned document yields a valid, empty tree and Docling reports no error.
    # Counted, not listed: 20% of all artifacts are near-empty and almost all
    # are short markdown stubs, so per-artifact reporting would bury the cases
    # that matter. Only fidelities where OCR could help are counted at all.
    near_empty: int = 0
    structureless: int = 0

    def __bool__(self) -> bool:
        return self.stored > 0


def _store():
    from trellis_artifact_tree import ArtifactTreeConfig, ArtifactTreeStore

    from resource_explorer.config import get_config, require_pgvector_password

    cfg = get_config()
    pg = cfg.pgvector
    require_pgvector_password(pg)      # TR-12: refused by name, never libpq's authentication error
    from resource_explorer.registry import schema_mode_from_env
    return ArtifactTreeStore(ArtifactTreeConfig(
        host=pg.host, port=pg.port, dbname=pg.dbname,
        # RE's PgVectorConfig deliberately avoids a bare `user` field (it would
        # collide with the shell's $USER under pydantic-settings); the shared
        # package's plain dataclass has no such problem, so translate here.
        user=pg.db_user, password=pg.password,
        schema=cfg.artifact_tree.schema_name,
    ), ddl=schema_mode_from_env())


def build_trees(
    files: list[tuple[str, str]],
    resource_slug: str,
    kind: str = "markdown",
    source_version: str = "",
    adapter=None,
) -> TreeBuildResult:
    """Build and persist a tree per item.

    `files` is a list of (source_id, source). For text formats that is the
    (path, content) shape the pipeline's own walkers already yield, so callers
    pass what they have rather than re-reading anything. For PDFs the source is
    a path, because Docling opens the file itself -- handing it bytes we had
    just read would only make it write them back out.

    `adapter` pins the adapter instead of resolving `kind` through the default
    registry. Code needs that: one CodeAdapter is built per language, so the
    language is a property of the adapter rather than something re-derived per
    file.
    """
    from resource_explorer.config import get_config

    if not get_config().artifact_tree.enabled:
        return TreeBuildResult("disabled")
    if not files:
        return TreeBuildResult("stored")

    global _schema_ready
    try:
        from trellis_artifact_tree import AdapterRegistry, Provenance

        store = _store()
        if not _schema_ready:
            from resource_explorer.registry import schema_mode_from_env
            if schema_mode_from_env() == "external":
                store.check_schema()           # TR-115: no DDL in a running process; the schema Job made the tables
            else:
                store.create_schema()
            _schema_ready = True

        registry = AdapterRegistry()
        fetched_at = datetime.now(timezone.utc).isoformat()

        def prov(source_id: str) -> Provenance:
            return Provenance(
                source_kind="repo",
                source_id=source_id,
                fetched_at=fetched_at,
                # The commit the content came from, when the caller knows it.
                # Without it a tree can be read back but not placed in time,
                # which is what the design's as-of work needs.
                source_version=source_version,
            )

        stored = skipped = 0
        near_empty = structureless = 0
        for source_id, source in files:
            try:
                artifact_id = f"{resource_slug}:{source_id}"
                tree = (
                    adapter.parse(artifact_id, source, prov(source_id))
                    if adapter is not None
                    else registry.parse(
                        artifact_id=artifact_id, kind=kind,
                        source=source, provenance=prov(source_id),
                    )
                )
                store.put(tree)
                stored += 1
                from trellis_artifact_tree.diagnostics import diagnose

                d = diagnose(tree)
                if d.actionable and d.near_empty:
                    near_empty += 1
                    logger.warning("artifact tree: %s — %s", source_id, d.finding)
                elif d.structureless:
                    structureless += 1
            except Exception:
                # One unparseable artifact must not abandon the rest -- but it
                # is counted, so a caller can tell a clean run from a lossy one.
                skipped += 1
                logger.warning("artifact tree: skipped %s", source_id, exc_info=True)
        return TreeBuildResult(
            status="partial" if skipped else "stored",
            stored=stored, skipped=skipped,
            near_empty=near_empty, structureless=structureless,
        )
    except Exception as exc:
        logger.warning(
            "artifact tree: unavailable for this run "
            "(ingestion continues with chunks)", exc_info=True,
        )
        return TreeBuildResult("unavailable", reason=f"{type(exc).__name__}: {exc}")


def build_code_trees(
    files: list[tuple[str, str]],
    resource_slug: str,
    language: str,
    source_version: str = "",
) -> TreeBuildResult:
    """Trees from source, one CodeAdapter per language.

    An unsupported language returns "unsupported" rather than skipping every
    file individually: `go_code` is a real collection in this pipeline and the
    tree package has no Go grammar, so a thousand identical per-file warnings
    would be noise hiding one fact. Checked once, up front, and named.
    """
    from resource_explorer.config import get_config

    if not get_config().artifact_tree.enabled:
        return TreeBuildResult("disabled")

    try:
        from trellis_artifact_tree.adapters_code import SPECS, CodeAdapter
    except Exception as exc:
        return TreeBuildResult("unavailable", reason=f"{type(exc).__name__}: {exc}")

    if language not in SPECS:
        return TreeBuildResult(
            "unsupported",
            reason=f"no grammar for {language!r} (have: {', '.join(sorted(SPECS))})",
        )
    return build_trees(
        files, resource_slug, source_version=source_version,
        adapter=CodeAdapter(language=language),
    )


def build_pdf_trees_from_documents(
    documents: list[tuple[str, object]],
    resource_slug: str,
    source_version: str = "",
) -> TreeBuildResult:
    """Trees from ALREADY-CONVERTED Docling documents.

    `documents` is (display_path, DoclingDocument). Preferred over
    build_pdf_trees() wherever the caller is also chunking the same PDFs:
    conversion is the expensive step, and converting once for chunks and again
    for the tree doubles the cost of a PDF-heavy ingest for no benefit.
    """
    from resource_explorer.config import get_config

    if not get_config().artifact_tree.enabled:
        return TreeBuildResult("disabled")

    try:
        from trellis_artifact_tree.adapters_pdf import DoclingDocumentAdapter
    except Exception as exc:
        return TreeBuildResult("unavailable", reason=f"{type(exc).__name__}: {exc}")

    return build_trees(
        documents, resource_slug, source_version=source_version,
        adapter=DoclingDocumentAdapter(),
    )


def build_pdf_trees(
    paths: list[tuple[str, str]],
    resource_slug: str,
    source_version: str = "",
) -> TreeBuildResult:
    """Trees from PDFs by path, converting them itself.

    Use build_pdf_trees_from_documents() instead when the caller is also
    chunking the same files -- this one pays for a second conversion.

    `paths` is (display_path, absolute_path).

    The display path is what provenance records and what the artifact id is
    keyed on -- an absolute path is a property of this machine's checkout, not
    of the artifact, and would make the same document a different artifact on
    every host.
    """
    from resource_explorer.config import get_config

    if not get_config().artifact_tree.enabled:
        return TreeBuildResult("disabled")

    try:
        from trellis_artifact_tree.adapters_pdf import PdfAdapter
    except Exception as exc:
        return TreeBuildResult("unavailable", reason=f"{type(exc).__name__}: {exc}")

    return build_trees(
        paths, resource_slug, source_version=source_version, adapter=PdfAdapter(),
    )
