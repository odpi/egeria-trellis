"""
Ingest data from Phase 2 cache into the vector store.

This script reads the JSON files created by the data preparation pipeline
and populates vector store collections with embeddings for semantic search.

Also provides CodeIngester for directly ingesting code files from repositories.
"""

import json
import fnmatch
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Sequence, Tuple
from loguru import logger
import sys

from advisor.config import settings
from advisor.vector_store import get_vector_store
from advisor.embeddings import get_embedding_generator
from advisor.mlflow_tracking import track_operation


def path_is_excluded(
    path: Path,
    exclude_patterns: Optional[Sequence[str]] = None,
    root: Optional[Path] = None,
) -> bool:
    """True if `path` matches any of `exclude_patterns`.

    Patterns are the glob forms used by CollectionMetadata.exclude_patterns
    (advisor/collection_config.py) -- e.g. "**/node_modules/**",
    "**/concepts/**", "**/*.egg-info/**".

    Matching is fnmatch against the path string, NOT pathlib's
    PurePath.match(). PurePath.match() does not treat a leading "**" as a
    recursive wildcard before Python 3.13, so "**/concepts/**" silently
    matches nothing there -- which is how these patterns came to be
    declared in config and never actually applied. fnmatch's "*" spans "/",
    so "**/concepts/**" matches any path with a "concepts" directory
    component.

    The absolute path is tested first; the path relative to `root` (with and
    without a leading "/") is tested too, so a pattern anchored at the scan
    root rather than at "**" still matches.

    Args:
        path: File path to test
        exclude_patterns: Glob patterns to exclude; None/empty excludes nothing
        root: Optional directory the scan started from, for relative matching

    Returns:
        True if the path should be excluded
    """
    if not exclude_patterns:
        return False

    candidates = [Path(path).as_posix()]
    if root is not None:
        try:
            relative = Path(path).relative_to(root).as_posix()
        except ValueError:
            pass
        else:
            candidates.append(relative)
            candidates.append("/" + relative)

    return any(
        fnmatch.fnmatch(candidate, pattern)
        for pattern in exclude_patterns
        for candidate in candidates
    )


def existing_chunk_count(vector_store, collection_name: str) -> Optional[int]:
    """How many rows the collection's table already holds, or None if the
    table does not exist.

    Two traps this deliberately avoids (both bit trevor on 2026-09-04):

    * `collection_name in vector_store.list_collections()` compares the
      *collection* name with *table* names. `pyegeria_drE` is stored as the
      table `pyegeria_dre` (see _TABLE_NAME_MAP in advisor/vector_store_pg.py),
      so that check never matched drE — and always matched `pyegeria` and
      `pyegeria_cli`. `collection_exists()` resolves the mapping.
    * Existence alone says nothing about content. The web app's startup hook
      (advisor/web/app.py `_startup` -> `provision_schema()`) creates every
      collection table *empty* the moment the container starts, so on a fresh
      deployment the tables already exist before the first ingest runs. An
      empty table must be ingested into, not reported as "already exists".
    """
    if not vector_store.collection_exists(collection_name):
        return None
    stats = vector_store.get_collection_stats(collection_name) or {}
    return int(stats.get("num_entities") or 0)


class DataIngester:
    """Ingest prepared data into the vector store."""
    
    def __init__(self, cache_dir: Path = None):
        """
        Initialize data ingester.
        
        Args:
            cache_dir: Directory containing cached data files
        """
        self.cache_dir = cache_dir or Path(settings.advisor_cache_dir)
        self.vector_store = get_vector_store()
        self.embedding_generator = get_embedding_generator()
        
        logger.info(f"Initialized DataIngester with cache dir: {self.cache_dir}")
    
    def _extract_module_path(self, file_path: str) -> str:
        """
        Extract module path from file path.
        
        Example: /path/to/pyegeria/admin_services.py -> pyegeria.admin_services
        
        Args:
            file_path: Full file path
            
        Returns:
            Module path string
        """
        if not file_path:
            return ""
        
        try:
            path = Path(file_path)
            # Find the package root (look for common package names)
            parts = path.parts
            
            # Common package names to look for
            package_roots = ['pyegeria', 'egeria', 'advisor', 'src']
            
            # Find where the package starts
            start_idx = -1
            for i, part in enumerate(parts):
                if part in package_roots:
                    start_idx = i
                    break
            
            if start_idx >= 0:
                # Get parts from package root to file (without extension)
                module_parts = list(parts[start_idx:])
                module_parts[-1] = path.stem  # Remove .py extension
                return ".".join(module_parts)
            
            # Fallback: just use the filename without extension
            return path.stem
        except Exception as e:
            logger.warning(f"Error extracting module path from {file_path}: {e}")
            return ""
    
    def load_json_file(self, filename: str) -> List[Dict[str, Any]]:
        """Load data from a JSON file."""
        filepath = self.cache_dir / filename
        
        if not filepath.exists():
            logger.error(f"File not found: {filepath}")
            raise FileNotFoundError(f"Cache file not found: {filename}")
        
        logger.info(f"Loading {filename}...")
        with open(filepath) as f:
            data = json.load(f)
        
        logger.info(f"✓ Loaded {len(data)} items from {filename}")
        return data
    
    def ingest_code_elements(self, drop_existing: bool = False) -> int:
        """
        Ingest code elements (functions, classes, methods) into the vector store.
        
        Args:
            drop_existing: Drop existing collection if it exists
            
        Returns:
            Number of entities inserted
        """
        collection_name = "code_elements"
        logger.info(f"Ingesting code elements into {collection_name}")
        
        # Load data
        code_elements = self.load_json_file("code_elements.json")
        
        # Track with MLflow
        params = {
            "collection": collection_name,
            "num_items": len(code_elements),
            "drop_existing": drop_existing,
            "embedding_model": self.embedding_generator.model_name,
            "embedding_device": self.embedding_generator.device
        }
        
        with track_operation(f"ingest_{collection_name}", params=params) as tracker:
            # Create collection
            self.vector_store.create_collection(
                collection_name,
                description="Egeria Python code elements (functions, classes, methods)",
                drop_if_exists=drop_existing
            )
            
            # Prepare data for insertion
            texts = []
            ids = []
            metadata = []
            
            for elem in code_elements:
                # Create searchable text combining name, docstring, and signature
                text_parts = [
                    f"Name: {elem.get('name', '')}",
                    f"Type: {elem.get('type', '')}",
                ]
                
                if elem.get('docstring'):
                    text_parts.append(f"Documentation: {elem['docstring']}")
                
                if elem.get('signature'):
                    text_parts.append(f"Signature: {elem['signature']}")
                
                text = "\n".join(text_parts)
                texts.append(text)
                
                # Use file path + name as ID
                elem_id = f"{elem.get('file', 'unknown')}::{elem.get('name', 'unknown')}"
                ids.append(elem_id)
                
                # Store metadata - preserve ALL extracted metadata for filtering
                meta = {
                    # Basic identification
                    "name": elem.get("name", ""),
                    "element_type": elem.get("type", ""),  # function, class, method
                    "file_path": elem.get("file", ""),
                    "line_number": elem.get("line_number", 0),
                    "end_line_number": elem.get("end_line_number", 0),
                    
                    # Code structure metadata
                    "class_name": elem.get("parent_class", "") if elem.get("type") == "method" else elem.get("name", "") if elem.get("type") == "class" else "",
                    "method_name": elem.get("name", "") if elem.get("type") in ["method", "function"] else "",
                    "parent_class": elem.get("parent_class", ""),
                    "signature": elem.get("signature", ""),
                    "parameters": ",".join(elem.get("parameters", [])),  # Store as comma-separated string
                    "return_type": elem.get("return_type", ""),
                    
                    # Attributes
                    "is_async": elem.get("is_async", False),
                    "is_private": elem.get("is_private", False),
                    "is_public": elem.get("is_public", True),
                    "decorators": ",".join(elem.get("decorators", [])),  # Store as comma-separated string
                    "complexity": elem.get("complexity", 0),
                    
                    # Module path (extract from file path)
                    "module_path": self._extract_module_path(elem.get("file", "")),
                }
                metadata.append(meta)
            
            # Insert into the vector store
            count = self.vector_store.insert_data(
                collection_name,
                texts=texts,
                ids=ids,
                metadata=metadata
            )
            
            # Create index
            logger.info("Creating index...")
            self.vector_store.create_index(
                collection_name,
                index_type="IVF_FLAT",
                metric_type="L2"
            )
            
            # Log metrics
            tracker.log_metrics({
                "entities_inserted": count,
                "embedding_dimension": self.embedding_generator.embedding_dim
            })
            
            logger.info(f"✓ Ingested {count} code elements")
            return count
    
    def ingest_documentation(self, drop_existing: bool = False) -> int:
        """
        Ingest documentation sections into the vector store.
        
        Args:
            drop_existing: Drop existing collection if it exists
            
        Returns:
            Number of entities inserted
        """
        collection_name = "documentation"
        logger.info(f"Ingesting documentation into {collection_name}")
        
        # Load data
        doc_sections = self.load_json_file("doc_sections.json")
        
        # Create collection
        self.vector_store.create_collection(
            collection_name,
            description="Egeria Python documentation sections",
            drop_if_exists=drop_existing
        )
        
        # Prepare data for insertion
        texts = []
        ids = []
        metadata = []
        
        for i, section in enumerate(doc_sections):
            # Use section content as text
            text = section.get("content", "")
            if section.get("title"):
                text = f"Title: {section['title']}\n\n{text}"
            
            texts.append(text)
            
            # Generate ID
            section_id = f"doc_{i}_{section.get('file', 'unknown')}"
            ids.append(section_id)
            
            # Store metadata
            meta = {
                "title": section.get("title", ""),
                "file": section.get("file", ""),
                "section_type": section.get("section_type", ""),
                "level": section.get("level", 0),
                "word_count": section.get("word_count", 0)
            }
            metadata.append(meta)
        
        # Insert into the vector store
        count = self.vector_store.insert_data(
            collection_name,
            texts=texts,
            ids=ids,
            metadata=metadata
        )
        
        # Create index
        logger.info("Creating index...")
        self.vector_store.create_index(
            collection_name,
            index_type="IVF_FLAT",
            metric_type="L2"
        )
        
        logger.info(f"✓ Ingested {count} documentation sections")
        return count
    
    def ingest_examples(self, drop_existing: bool = False) -> int:
        """
        Ingest code examples into the vector store.
        
        Args:
            drop_existing: Drop existing collection if it exists
            
        Returns:
            Number of entities inserted
        """
        collection_name = "examples"
        logger.info(f"Ingesting examples into {collection_name}")
        
        # Load data
        examples = self.load_json_file("examples.json")
        
        # Create collection
        self.vector_store.create_collection(
            collection_name,
            description="Egeria Python code examples",
            drop_if_exists=drop_existing
        )
        
        # Prepare data for insertion
        texts = []
        ids = []
        metadata = []
        
        for i, example in enumerate(examples):
            # Combine description and code
            text_parts = []
            
            if example.get("description"):
                text_parts.append(f"Description: {example['description']}")
            
            if example.get("code"):
                text_parts.append(f"Code:\n{example['code']}")
            
            text = "\n\n".join(text_parts)
            texts.append(text)
            
            # Generate ID
            example_id = f"example_{i}_{example.get('file', 'unknown')}"
            ids.append(example_id)
            
            # Store metadata
            meta = {
                "file": example.get("file", ""),
                "example_type": example.get("example_type", ""),
                "line_number": example.get("line_number", 0),
                "language": example.get("language", "python")
            }
            metadata.append(meta)
        
        # Insert into the vector store
        count = self.vector_store.insert_data(
            collection_name,
            texts=texts,
            ids=ids,
            metadata=metadata
        )
        
        # Create index
        logger.info("Creating index...")
        self.vector_store.create_index(
            collection_name,
            index_type="IVF_FLAT",
            metric_type="L2"
        )
        
        logger.info(f"✓ Ingested {count} examples")
        return count
    
    def ingest_all(self, drop_existing: bool = False) -> Dict[str, int]:
        """
        Ingest all data types into the vector store.
        
        Args:
            drop_existing: Drop existing collections if they exist
            
        Returns:
            Dictionary with counts for each collection
        """
        logger.info("=" * 80)
        logger.info("Starting full data ingestion to the vector store")
        logger.info("=" * 80)
        
        results = {}
        
        try:
            # Ingest code elements
            results["code_elements"] = self.ingest_code_elements(drop_existing)
            
            # Ingest documentation
            results["documentation"] = self.ingest_documentation(drop_existing)
            
            # Ingest examples
            results["examples"] = self.ingest_examples(drop_existing)
            
            logger.info("=" * 80)
            logger.info("Ingestion Summary:")
            for collection, count in results.items():
                logger.info(f"  {collection}: {count:,} entities")
            logger.info("=" * 80)
            
            return results
            
        except Exception as e:
            logger.error(f"Ingestion failed: {e}")
            raise
        finally:
            self.vector_store.disconnect()


def main():
    """Main entry point for data ingestion."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Ingest data into the vector store")
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop existing collections before ingesting"
    )
    parser.add_argument(
        "--collection",
        choices=["code_elements", "documentation", "examples", "all"],
        default="all",
        help="Which collection(s) to ingest"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="Path to cache directory (default: from config)"
    )
    
    args = parser.parse_args()
    
    try:
        ingester = DataIngester(cache_dir=args.cache_dir)
        
        if args.collection == "all":
            results = ingester.ingest_all(drop_existing=args.drop_existing)
            logger.info("✅ All data ingested successfully!")
        elif args.collection == "code_elements":
            count = ingester.ingest_code_elements(drop_existing=args.drop_existing)
            logger.info(f"✅ Ingested {count} code elements")
        elif args.collection == "documentation":
            count = ingester.ingest_documentation(drop_existing=args.drop_existing)
            logger.info(f"✅ Ingested {count} documentation sections")
        elif args.collection == "examples":
            count = ingester.ingest_examples(drop_existing=args.drop_existing)
            logger.info(f"✅ Ingested {count} examples")
        
        return 0
        
    except Exception as e:
        logger.error(f"❌ Ingestion failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())


class CodeIngester:
    """Ingest code files directly from repositories into the vector store."""
    
    def __init__(self, collection_name: str, chunk_size: Optional[int] = None, chunk_overlap: Optional[int] = None):
        """
        Initialize code ingester.
        
        Args:
            collection_name: Name of the vector store collection
            chunk_size: Size of text chunks (defaults to collection-specific value)
            chunk_overlap: Overlap between chunks (defaults to collection-specific value)
        """
        from advisor.collection_config import get_collection
        
        self.collection_name = collection_name
        self.vector_store = get_vector_store()
        self.embedding_generator = get_embedding_generator()
        
        # Get collection-specific parameters if not provided
        collection_meta = get_collection(collection_name)
        if collection_meta:
            self.chunk_size = chunk_size if chunk_size is not None else collection_meta.chunk_size
            self.chunk_overlap = chunk_overlap if chunk_overlap is not None else collection_meta.chunk_overlap
            logger.info(f"Using collection-specific parameters: chunk_size={self.chunk_size}, chunk_overlap={self.chunk_overlap}")
        else:
            # Fallback to defaults if collection not found
            self.chunk_size = chunk_size if chunk_size is not None else 1000
            self.chunk_overlap = chunk_overlap if chunk_overlap is not None else 200
            logger.warning(f"Collection {collection_name} not found in config, using defaults")
        
        # Per-file failures swallowed by ingest_file()/ingest_directory(), as
        # (path, "ExcType: message"). Callers that see zero files ingested read
        # this to report *why* instead of a silent zero (trevor, 2026-09-04).
        self.failed_files: List[Tuple[str, str]] = []

        logger.info(f"Initialized CodeIngester for collection: {collection_name}")

    def record_failure(self, file_path: Path, exc: BaseException) -> None:
        """Remember a per-file failure so a zero-file outcome can explain itself."""
        self.failed_files.append((str(file_path), f"{type(exc).__name__}: {exc}"))

    def failure_summary(self, limit: int = 3) -> str:
        """Human-readable digest of recorded failures: distinct error kinds with
        counts, plus up to `limit` example files. Empty string when nothing failed."""
        if not self.failed_files:
            return ""
        kinds: Dict[str, int] = {}
        for _, err in self.failed_files:
            kinds[err] = kinds.get(err, 0) + 1
        ranked = sorted(kinds.items(), key=lambda kv: kv[1], reverse=True)
        parts = [f"{count}x {err}" for err, count in ranked[:limit]]
        if len(ranked) > limit:
            parts.append(f"... and {len(ranked) - limit} other error kinds")
        examples = ", ".join(path for path, _ in self.failed_files[:limit])
        return f"{len(self.failed_files)} file(s) failed: {'; '.join(parts)} (e.g. {examples})"
    
    def _chunk_text(self, text: str) -> List[str]:
        """
        Split text into overlapping chunks.
        
        Args:
            text: Text to chunk
            
        Returns:
            List of text chunks
        """
        if len(text) <= self.chunk_size:
            return [text]
        
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + self.chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start += self.chunk_size - self.chunk_overlap
        
        return chunks
    
    def ingest_file(self, file_path: Path) -> Tuple[int, int, List[str]]:
        """
        Ingest a single file with code structure extraction for Python files.
        
        Args:
            file_path: Path to file
            
        Returns:
            Tuple of (files_processed, chunks_created, entity_ids)
        """
        try:
            if file_path.suffix == '.py':
                return self._ingest_python_file(file_path)
            elif file_path.suffix == '.java':
                # Java symbol extraction (self._extract_java_symbols) stopped
                # here — Resource Explorer now owns it (real tree-sitter, see
                # AST-ownership-transfer plan Phases 2/8). Unlike Python, Java
                # pgvector chunking was always plain text chunking
                # (_ingest_text_file), fully independent of symbol extraction
                # — so removing the extraction call has zero effect on
                # pgvector content for Java files.
                return self._ingest_text_file(file_path)
            else:
                return self._ingest_text_file(file_path)

        except Exception as e:
            logger.error(f"Error ingesting {file_path}: {e}")
            self.record_failure(file_path, e)
            return 0, 0, []

    def _extract_java_symbols(self, file_path: Path) -> None:
        """Deprecated, no longer called from ingest_file() — see the .java
        branch above. Kept in place (not deleted) as a rollback safety net
        per AST-ownership-transfer plan decision D8."""
        try:
            from advisor.data_prep.java_symbol_extractor import extract_java_symbols
            from advisor.code_symbol_store import get_symbol_store
            symbols = extract_java_symbols(file_path)
            if symbols:
                get_symbol_store().upsert_symbols(self.collection_name, symbols, language="java")
        except Exception as exc:
            logger.warning(f"Java symbol extraction failed for {file_path}: {exc}")
    
    def _ingest_python_file(self, file_path: Path) -> Tuple[int, int, List[str]]:
        """
        Ingest Python file with code structure extraction.
        
        Args:
            file_path: Path to Python file
            
        Returns:
            Tuple of (files_processed, chunks_created, entity_ids)
        """
        from advisor.data_prep.code_parser import CodeParser
        
        # Parse Python file to extract code elements
        parser = CodeParser()
        elements = parser.parse_file(file_path)
        
        if not elements:
            # Fallback to text chunking if parsing fails
            logger.warning(f"No code elements found in {file_path}, using text chunking")
            return self._ingest_text_file(file_path)
        
        # Prepare data with rich metadata
        texts = []
        ids = []
        metadata = []
        
        for elem in elements:
            # Create searchable text
            text_parts = [
                f"Name: {elem.name}",
                f"Type: {elem.type}",
            ]
            
            if elem.parent_class:
                text_parts.append(f"Class: {elem.parent_class}")
            
            if elem.signature:
                text_parts.append(f"Signature: {elem.signature}")
            
            if elem.docstring:
                text_parts.append(f"Documentation: {elem.docstring}")
            
            # Add body for context (truncate if too long)
            if elem.body and len(elem.body) < 2000:
                text_parts.append(f"Code:\n{elem.body}")
            
            text = "\n\n".join(text_parts)
            texts.append(text)
            
            # Generate ID
            elem_id = f"{file_path}::{elem.name}::{elem.line_number}"
            if len(elem_id) > 250:
                path_hash = hashlib.md5(str(file_path).encode()).hexdigest()[:16]
                elem_id = f"{path_hash}::{elem.name}::{elem.line_number}"
            ids.append(elem_id)
            
            # Store rich metadata
            meta = {
                # Basic identification
                "file_path": str(file_path),
                "collection": self.collection_name,
                "chunk_index": 0,  # Single element, not chunked
                "total_chunks": 1,
                
                # Code structure metadata
                "element_type": elem.type,
                "name": elem.name,
                "class_name": elem.parent_class if elem.type == "method" else (elem.name if elem.type == "class" else ""),
                "method_name": elem.name if elem.type in ["method", "function"] else "",
                "parent_class": elem.parent_class or "",
                "signature": elem.signature or "",
                "parameters": ",".join(elem.parameters),
                "return_type": elem.return_type or "",
                
                # Attributes
                "is_async": elem.is_async,
                "is_private": elem.is_private,
                "is_public": elem.is_public,
                "decorators": ",".join(elem.decorators),
                "complexity": elem.complexity,
                "line_number": elem.line_number,
                "end_line_number": elem.end_line_number,
                
                # Module path
                "module_path": self._extract_module_path(str(file_path)),
            }
            metadata.append(meta)
        
        # Insert into vector store
        if texts:
            self.vector_store.insert_data(
                self.collection_name,
                texts=texts,
                ids=ids,
                metadata=metadata
            )

        # Symbol-table writes to EA's own code_symbols/code_relationships
        # stopped here — Resource Explorer now owns structural code
        # intelligence extraction for the repos in RE's "egeria" project
        # group (AST-ownership-transfer plan Phase 8; consumers migrated in
        # Phases 6-7). `elements` (from CodeParser().parse_file() above) is
        # still used for the pgvector text/metadata built above — only the
        # code_symbols/code_relationships write is removed, not the parse
        # itself, since pgvector chunking for Python is derived from the
        # same parse. code_symbol_store.py's write path is deprecated, not
        # deleted — see its module docstring; code_symbols/code_relationships
        # are left in place, unwritten, as a rollback safety net (D8).

        return 1, len(elements), ids
    
    def _ingest_text_file(self, file_path: Path) -> Tuple[int, int, List[str]]:
        """
        Ingest non-Python file using text chunking.
        
        Args:
            file_path: Path to file
            
        Returns:
            Tuple of (files_processed, chunks_created, entity_ids)
        """
        # Read file content
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Create chunks
        chunks = self._chunk_text(content)
        
        # Prepare data
        texts = []
        ids = []
        metadata = []
        
        for i, chunk in enumerate(chunks):
            texts.append(chunk)
            # Generate ID with hash if path is too long (id column limit: 256 chars)
            chunk_id = f"{file_path}::chunk_{i}"
            if len(chunk_id) > 250:  # Leave margin for safety
                # Use hash of path + chunk index
                path_hash = hashlib.md5(str(file_path).encode()).hexdigest()[:16]
                chunk_id = f"{path_hash}::chunk_{i}"
            ids.append(chunk_id)
            metadata.append({
                "file_path": str(file_path),
                "collection": self.collection_name,
                "chunk_index": i,
                "total_chunks": len(chunks)
            })
        
        # Insert into the vector store
        if texts:
            self.vector_store.insert_data(
                self.collection_name,
                texts=texts,
                ids=ids,
                metadata=metadata
            )
        
        return 1, len(chunks), ids
    
    def _extract_module_path(self, file_path: str) -> str:
        """
        Extract module path from file path.
        
        Example: /path/to/pyegeria/admin_services.py -> pyegeria.admin_services
        
        Args:
            file_path: Full file path
            
        Returns:
            Module path string
        """
        if not file_path:
            return ""
        
        try:
            path = Path(file_path)
            # Find the package root (look for common package names)
            parts = path.parts
            
            # Common package names to look for
            package_roots = ['pyegeria', 'egeria', 'advisor', 'src']
            
            # Find where the package starts
            start_idx = -1
            for i, part in enumerate(parts):
                if part in package_roots:
                    start_idx = i
                    break
            
            if start_idx >= 0:
                # Get parts from package root to file (without extension)
                module_parts = list(parts[start_idx:])
                module_parts[-1] = path.stem  # Remove .py extension
                return ".".join(module_parts)
            
            # Fallback: just use the filename without extension
            return path.stem
        except Exception as e:
            logger.warning(f"Error extracting module path from {file_path}: {e}")
            return ""
    
    def ingest_directory(
        self,
        dir_path: Path,
        file_pattern: str = "*.py",
        recursive: bool = True,
        batch_size: int = 50,
        exclude_patterns: Optional[Sequence[str]] = None
    ) -> Tuple[int, int]:
        """
        Ingest all files in a directory with batching for performance.
        
        Args:
            dir_path: Path to directory
            file_pattern: File pattern to match
            recursive: Search recursively
            batch_size: Number of files to batch before inserting
            exclude_patterns: Glob patterns to skip (a collection's
                CollectionMetadata.exclude_patterns). Callers that do not
                pass these get every matching file, which is what let
                egeria_general ingest the concepts/ and types/ trees it is
                configured to exclude -- see path_is_excluded().
            
        Returns:
            Tuple of (files_processed, chunks_created)
        """
        total_files = 0
        total_chunks = 0
        
        # Find files
        files = []
        try:
            if recursive:
                files = list(dir_path.rglob(file_pattern))
            else:
                files = list(dir_path.glob(file_pattern))
        except (PermissionError, OSError) as e:
            logger.warning(f"Permission error accessing {dir_path}: {e}")
            logger.warning("Continuing with accessible files only")

        if exclude_patterns:
            kept = [
                f for f in files
                if not path_is_excluded(f, exclude_patterns, root=dir_path)
            ]
            excluded_count = len(files) - len(kept)
            files = kept
            if excluded_count:
                logger.info(
                    f"Excluded {excluded_count} file(s) matching {list(exclude_patterns)}"
                )

        logger.info(f"Found {len(files)} files matching {file_pattern}")

        # NOTE: symbol-table clearing is done once per *collection* by the caller
        # (scripts/ingest_collections.py), not here. Collections like 'pyegeria' have
        # multiple source directories (["pyegeria", "tests"]) each ingested via a
        # separate ingest_directory() call — clearing here on every call wiped out
        # the previous directory's symbols each time, leaving only the last
        # directory's files in code_symbols.

        # Process files individually to use Python parsing
        for idx, file_path in enumerate(files, 1):
            try:
                # Use ingest_file which handles Python parsing vs text chunking
                files_processed, chunks_created, entity_ids = self.ingest_file(file_path)
                
                # Note: ingest_file inserts directly, so we don't batch here
                # This is a trade-off: better metadata extraction vs batching efficiency
                # For Python files, we get rich metadata; for others, text chunking
                
                total_files += files_processed
                total_chunks += chunks_created
                
                if idx % 10 == 0:  # Log progress every 10 files
                    logger.info(f"Progress: {idx}/{len(files)} files processed, {total_chunks} chunks created")
                
            except Exception as e:
                logger.error(f"Error processing {file_path}: {e}")
                self.record_failure(file_path, e)
                continue

        if files and total_files == 0:
            # Every candidate was swallowed by a per-file except above — say so
            # at error level here, because the caller only sees (0, 0).
            logger.error(
                f"0 of {len(files)} files matching {file_pattern} under {dir_path} "
                f"were ingested — {self.failure_summary() or 'no per-file errors recorded'}"
            )

        return total_files, total_chunks