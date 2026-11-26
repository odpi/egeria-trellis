"""Postgres store for artifact trees.

The write path both apps call. Resource Explorer and Egeria Advisor each run
their own parse pipeline and so both produce trees; this is the one place that
knows how a tree is persisted, so the invariants hold no matter which pipeline
produced it.

**A tree is written whole or not at all.** `put()` replaces every node, rung and
chunk reference for an artifact inside one transaction. A reparse that half-
succeeded would leave a tree that `validate()` would reject if it were ever
loaded -- orphans pointing at deleted parents, cuts silently missing subtrees --
and it would be discovered by a packer mid-compile rather than at write time.
"""
from __future__ import annotations

from contextlib import contextmanager

from trellis_artifact_tree.config import ArtifactTreeConfig
from trellis_artifact_tree.model import ArtifactTree, Node, Provenance, Rung
from trellis_artifact_tree.schema import _q, create_schema_sql


class SchemaNotReady(RuntimeError):
    """`ddl="external"` and the artifact-tree tables are missing: someone with DDL rights must run `apply_schema` first."""


_TABLES = ("artifact", "artifact_node", "artifact_node_rung", "artifact_node_chunk")


class ArtifactTreeStore:
    def __init__(self, config: ArtifactTreeConfig, connection_factory=None, *, ddl: str = "auto") -> None:
        """`ddl="auto"` (default) is today's behaviour. `ddl="external"` means this instance never sends DDL:
        `create_schema()` is refused (the schema Job calls `apply_schema()`) and `check_schema()` is the read-only check."""
        if ddl not in ("auto", "external"):
            raise ValueError(f"ddl must be 'auto' or 'external', not {ddl!r}")
        self.ddl = ddl
        self.config = config
        # Injectable so tests can drive a fake or an already-open connection.
        # The default import is deferred: psycopg2 is a dependency, but a
        # caller only building SQL should not need a live libpq.
        self._connection_factory = connection_factory

    def _connect(self):
        if self._connection_factory is not None:
            return self._connection_factory()
        import psycopg2
        import psycopg2.extras

        return psycopg2.connect(
            host=self.config.host, port=self.config.port,
            dbname=self.config.dbname, user=self.config.user,
            password=self.config.password,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    @contextmanager
    def _conn(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_schema(self) -> None:
        if self.ddl == "external":
            raise RuntimeError(
                "create_schema() is refused with ddl='external': the schema Job runs apply_schema(); "
                "a running process only calls check_schema()")
        self.apply_schema()

    def apply_schema(self) -> int:
        """Create the tables and indexes (the schema Job's entry point; allowed in either mode). Returns the statements run."""
        n = 0
        with self._conn() as conn, conn.cursor() as cur:
            for stmt in create_schema_sql(self.config.schema):
                cur.execute(stmt)
                n += 1
        return n

    def check_schema(self) -> None:
        """Read-only: raise `SchemaNotReady` unless the four tables exist."""
        with self._conn() as conn, conn.cursor() as cur:
            missing = []
            for t in _TABLES:
                cur.execute("SELECT to_regclass(%s) AS r", (_q(self.config.schema, t),))
                row = cur.fetchone()
                if not (row and (row["r"] if isinstance(row, dict) else row[0])):
                    missing.append(t)
        if missing:
            raise SchemaNotReady(
                f"artifact-tree schema is not ready (missing: {', '.join(missing)}); "
                "ddl='external' sends no DDL: run `resource-explorer schema apply` first")

    def put(self, tree: ArtifactTree) -> None:
        """Replace an artifact's tree wholesale, in one transaction.

        ArtifactTree validates on construction, so anything reaching here is
        already known well-formed -- single-rooted, acyclic, no orphans. This
        method's job is to make sure that property survives the write.
        """
        s = self.config.schema
        p = tree.provenance
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {_q(s, 'artifact')}
                    (artifact_id, source_kind, source_id, fetched_at,
                     source_timestamp, source_version, extraction_fidelity)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (artifact_id) DO UPDATE SET
                        source_kind = EXCLUDED.source_kind,
                        source_id = EXCLUDED.source_id,
                        fetched_at = EXCLUDED.fetched_at,
                        source_timestamp = EXCLUDED.source_timestamp,
                        source_version = EXCLUDED.source_version,
                        extraction_fidelity = EXCLUDED.extraction_fidelity,
                        updated_at = now()""",
                (tree.artifact_id, p.source_kind, p.source_id, p.fetched_at,
                 p.source_timestamp, p.source_version, p.extraction_fidelity),
            )
            # ON DELETE CASCADE clears rungs and chunk refs with the nodes.
            cur.execute(
                f"DELETE FROM {_q(s, 'artifact_node')} WHERE artifact_id = %s",
                (tree.artifact_id,),
            )
            # Parents before children: the self-reference is a real FK, so
            # insertion order matters. A breadth-first walk from the root gives
            # that ordering for free, and the tree is known acyclic.
            for node in self._parents_first(tree):
                cur.execute(
                    f"""INSERT INTO {_q(s, 'artifact_node')}
                        (node_id, artifact_id, parent_id, kind, title,
                         ordinal, span_start, span_end)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (node.node_id, node.artifact_id, node.parent_id, node.kind,
                     node.title, node.ordinal,
                     node.span[0] if node.span else None,
                     node.span[1] if node.span else None),
                )
                for rung, text in sorted(node.rungs.items()):
                    cur.execute(
                        f"""INSERT INTO {_q(s, 'artifact_node_rung')}
                            (node_id, rung, text) VALUES (%s, %s, %s)""",
                        (node.node_id, int(rung), text),
                    )
                for ref in node.chunk_refs:
                    cur.execute(
                        f"""INSERT INTO {_q(s, 'artifact_node_chunk')}
                            (node_id, chunk_ref) VALUES (%s, %s)""",
                        (node.node_id, ref),
                    )

    def get(self, artifact_id: str) -> ArtifactTree | None:
        s = self.config.schema
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {_q(s, 'artifact')} WHERE artifact_id = %s",
                (artifact_id,),
            )
            art = cur.fetchone()
            if not art:
                return None

            cur.execute(
                f"SELECT * FROM {_q(s, 'artifact_node')} WHERE artifact_id = %s",
                (artifact_id,),
            )
            rows = cur.fetchall()

            cur.execute(
                f"""SELECT r.node_id, r.rung, r.text
                    FROM {_q(s, 'artifact_node_rung')} r
                    JOIN {_q(s, 'artifact_node')} n ON n.node_id = r.node_id
                    WHERE n.artifact_id = %s""",
                (artifact_id,),
            )
            rungs: dict[str, dict[Rung, str]] = {}
            for r in cur.fetchall():
                rungs.setdefault(r["node_id"], {})[Rung(r["rung"])] = r["text"]

            cur.execute(
                f"""SELECT c.node_id, c.chunk_ref
                    FROM {_q(s, 'artifact_node_chunk')} c
                    JOIN {_q(s, 'artifact_node')} n ON n.node_id = c.node_id
                    WHERE n.artifact_id = %s""",
                (artifact_id,),
            )
            chunks: dict[str, list[str]] = {}
            for r in cur.fetchall():
                chunks.setdefault(r["node_id"], []).append(r["chunk_ref"])

        nodes = tuple(
            Node(
                node_id=r["node_id"], artifact_id=r["artifact_id"],
                kind=r["kind"], title=r["title"], parent_id=r["parent_id"],
                ordinal=r["ordinal"],
                span=(r["span_start"], r["span_end"])
                if r["span_start"] is not None else None,
                rungs=rungs.get(r["node_id"], {}),
                chunk_refs=tuple(sorted(chunks.get(r["node_id"], []))),
            )
            for r in rows
        )
        # Constructing ArtifactTree re-validates. A tree that was written
        # well-formed and reads back malformed means the store lost something,
        # and that should surface here rather than inside a packer.
        return ArtifactTree(
            artifact_id=artifact_id,
            provenance=Provenance(
                source_kind=art["source_kind"], source_id=art["source_id"],
                fetched_at=art["fetched_at"],
                source_timestamp=art["source_timestamp"],
                source_version=art["source_version"],
                extraction_fidelity=art["extraction_fidelity"],
            ),
            nodes=nodes,
        )

    @staticmethod
    def _parents_first(tree: ArtifactTree) -> list[Node]:
        out, queue = [], [tree.root]
        while queue:
            node = queue.pop(0)
            out.append(node)
            queue.extend(tree.children(node.node_id))
        return out
