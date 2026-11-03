# Brief — documentation sources for databases at the Enrichment stage (2026-09-28)

Owner's ask: let a person point Resource Explorer at documentation for a
database that lives outside it — a wiki page, a data dictionary, a design
site, a runbook — check that it is reachable, and optionally ingest it so
the chat and the broader questions can answer from it.

This is the "catalog as text" layer of
`DESIGN-BROADER-QUESTIONS-WITHOUT-CONTENT-INGESTION.md` §2 extended by a
person-supplied source. The repository side already has the mechanism:
`website_ingestion` ("Ingest Documentation Site") embeds a project's doc
site into pgvector for Chat and Understanding, and skips when the
repository builds that site itself. Databases get the same mechanism with
three differences that matter: the source is declared by a person, not
discovered; the reachability check is read-only in the same posture as
`credential_capability`; and what was ingested is stated on every answer
that uses it.

## What a person does

On the Enrichment stage of a database (and filesystem; the shape is the
same), a **Documentation sources** block:

1. Add a source: a URL, a label, and what it is (data dictionary · design
   notes · runbook · wiki · other). Several sources per resource.
2. Resource Explorer probes it at once: reachable / needs sign-in /
   not found / blocked, with the HTTP status and the fetch cost, exactly as
   the credential probe reports what an account can see. Nothing is stored
   from the page at this step beyond title and byte count.
3. The person chooses **ingest** per source. Ingestion runs as an
   Enrichment-tier step (`doc_source_ingestion`) with a page and byte
   budget, records its cost on `step_runs`, and writes into the same
   pgvector collection scheme `website_ingestion` uses, tagged with the
   source id.
4. The block then shows, per source: reachable as of <time> · ingested
   <N pages, M KB> as of <time> · re-check · re-ingest · remove.

## What changes downstream

- **Chat** ("ask about this resource") retrieves from the ingested sources
  and every answer that used one names it: "from the data dictionary
  (wiki/coco-ods, ingested 28 Sep)". An answer that used none says so.
- **Questions** whose Answering Analysis is a document read
  (the licence row for databases, "what is this data about", "how is it
  supported") gain the source as evidence: the headline stays the
  measured one; the document adds a second line "documentation says: …"
  with its citation, never replacing a measured answer.
- **Egeria**: a declared source is an `ExternalReference` attached to the
  asset when the resource is published (read back on the linkage check, so
  a source declared in Egeria by someone else appears here too). Not
  published → local only, and the block says so.

## Rules

- Read-only probe; no form posts, no sign-in, no cookies accepted beyond
  what a GET needs. A source that needs sign-in is reported as such and
  cannot be ingested until a person supplies a reachable copy.
- Budgets are declared per source and enforced: default 200 pages / 20 MB /
  60 s; over budget is a state ("ingested 200 of 340 pages — budget"), never
  silent truncation.
- Ingested text is evidence, not truth: it is labelled "documentation
  says", carries the fetch time, and is never used to change a measured
  state (a data dictionary claiming a table is empty does not make it
  empty).
- Re-check on the resource's schedule; a source that becomes unreachable
  is flagged, and its ingested copy is kept but marked stale, with the
  date, on every answer that uses it.
- The person can remove a source; removal deletes the ingested copy and
  the ExternalReference it created (and only that one).

## Reuse, not rebuild

`website_ingestion`'s fetcher, chunker and pgvector writer; `website_ingestion`'s
skip logic inverted (repos skip when they own the site; databases never
own one, so the source is always external); the `credential_capability`
probe's state vocabulary for reachability; `egeria_linkage` for the
ExternalReference write and read-back; the Enrichment form conventions
already used for the human-supplied answers.

## Slices and gates

1. **Declare and probe** — the block, the source table, the read-only
   probe with its states, the ExternalReference on publish. Gate: on
   adventureworks add a URL that works, one that 404s and one that needs
   sign-in; each shows its state within five seconds with the status code;
   publish and see the ExternalReference in Egeria; unpublish shows "local
   only".
2. **Ingest and cite** — `doc_source_ingestion` with budgets and
   `step_runs` cost; chat answers cite the source; the three document-read
   questions show the "documentation says" line. Gate: ingest the
   AdventureWorks schema description page from Microsoft's documentation,
   ask "what is the salesorderheader table for" in chat and get an answer
   that cites it; ask the same on coco_pharma with no source and get "no
   documentation source declared" rather than a guess.
3. **Freshness** — scheduled re-check, stale marking, removal. Gate: break
   a URL, run the schedule, see the stale mark and date on the answer.

Sizes: 1 and 3 are small; 2 is medium and depends on 1. All three are
independent of the By-analysis, read-cost and harness work in flight.

## Not in this brief

Crawling beyond the declared pages; ingesting PDFs or office documents
(a later source type); using documentation to alter any measured state;
anything for repositories, which already have `website_ingestion`.
