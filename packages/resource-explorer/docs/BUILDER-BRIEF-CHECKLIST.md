# Builder brief checklist

**Status:** live. Short checklist for every builder brief.

Put these in every brief handed to a builder session.

- A worktree has no .env; RE defaults its registry to the shared Postgres. Set REGISTRY_DATABASE_URL to a temp SQLite file before any command or test that opens the registry; the guard will fail you otherwise.
- Use an absolute temp path: `REGISTRY_DATABASE_URL=sqlite:////abs/scratch/reg.db PGVECTOR_PORT=1`. Check `echo $REGISTRY_DATABASE_URL | cut -c1-12` prints `sqlite:` before running anything.
- Every `resource-explorer` command prints one `registry: ...` line on stderr. If it says `(shared)`, stop: you are about to touch the shared registry.
- Never set `RE_TESTS_ALLOW_SHARED_REGISTRY`.

See `docs/design-notes/implemented/TEST-SHARED-REGISTRY-GUARD-IMPLEMENTED.md`.
- Live Egeria tiers are opt-in. A default pytest run never contacts Egeria. Reads: `--live-egeria-reads` (or `RE_LIVE_EGERIA_READS=1`) after a peer round. Writes: `--live-egeria-writes` AND `RE_LIVE_EGERIA_WRITES_CLEARED=<who>/<UTC time>` (non-empty); the flag alone skips the tier. Never in CI.
- Another session's done is a statement about itself, never a clearance from the others; the peer round is a question to every live peer, and the answer set is what goes in the variable.
