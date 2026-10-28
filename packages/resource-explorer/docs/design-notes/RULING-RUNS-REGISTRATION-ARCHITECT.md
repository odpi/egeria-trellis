# RULING — Architect: the three items the runs-and-registration reply left open (2026-10-16)

Answers the "Left open, for the architect" list in
`REPLY-DESIGNER-RUNS-REGISTRATION.md` (on `re/design-runs-registration`,
cb98cb42), itself the reply to `ASK-DESIGNER-RUNS-REGISTRATION.md`
(dwolfson/trellis#719, merged). Read against main at 9670423c:
`catalog_and_survey.py` (`server_placeholders`, `database_placeholders`,
`server_cred_key`, `server_credential_notes`, `check_pointers`, `_classify`),
`mixed_process.py` (`expected_steps_record`, the run line) and
`native_survey_run.py` (the graded step list). Design ruling only; builders
implement. Nothing here needs a DDL, an Egeria write or a registry write to
decide; the slices that follow it may (marked).

## 1. "8 steps" against "2 of 4 ran": each count names what it counts

**What the numbers are.** "8 steps" is the definition as declared now.
"4" is the run's own plan, the request types stored at submission
(`declared.expected_steps`, PRs #716/#718), and the run is graded against
that list, never against today's definition. Both are true. They disagree
only when the definition changed after the run started, which is exactly
the case the owner hit.

**Ruling.**

- **The header counts the definition and says so: "8 steps".** It stays as
  is. The definition card is about the definition.
- **The run line counts the run's plan, and its denominator is "of its 4"
  only when that plan differs from the definition now.** When the run's
  stored list equals today's steps, the line is unchanged: "◐ 2 of 8 ran".
  When it differs:

  > ◐ 2 of its 4 steps ran · made under an earlier version of this
  > definition (4 steps, now 8) · …

  "its" ties the 4 to the run; the clause names the version change as a
  fact, so neither number reads as wrong. The 4 opens the run's own step
  list (the designer's "counts open what they counted"); the 8 opens the
  definition's steps.
- **A run never says anything about steps it never had.** A run made under
  4 steps does not show steps 5–8 as "not started" or "not run"; that is
  already the rule in the run line (`mixed_process.py`, "no 'not started
  yet' is ever said of a step the run never had") and it stays.
- **"Re-run…" runs the definition as it is now** and its plan (the dialog)
  lists 8 steps. That is where the reader meets the new count, before the
  press.
- **A legacy run row (no stored list) gets no denominator.** Today such a
  row grades against the steps Egeria created, so "2 of 4" there means
  "of the 4 Egeria started", which is a different count wearing the same
  words. It reads "◐ 2 ran · this run's plan wasn't recorded". A count RE
  did not record is not shown as one it did.
- **Earlier runs (the "runs · 6 ›" list)** follow the same rule per line: a
  line made under a different version carries "(4-step version)" after its
  count.

Detecting "differs" is a comparison of the stored request-type list with
the declared one, order included; a renamed or reordered step counts as a
different version. No definition version number is needed for this, and
none is introduced.

## 2. One server element, shared; its connection carries a server credential, never a borrowed one

**What happens today.** Both coco_pharma and egeria sit on
host.docker.internal:5442. `server_placeholders` fills the server template's
`secretsCollectionName` from the registering *database* (`<slug>::PostgreSQL
Secret`), and `server_cred_key` records whose it was. So the first database
registered decides the credential Egeria's server survey uses for every
database on that server, and the second database's row has to explain that
(`server_credential_notes`).

**Egeria's model.** A PostgreSQL server is one `SoftwareServer` (deployed
implementation type "PostgreSQL Relational Database Server"), identified by
host and port; its `ServerEndpoint` and its `Connection` (through
`ConnectionEndpoint` and `ConnectToAsset`) describe how to reach *the
server*. Each database is its own `RelationalDatabase` asset, hosted by the
server, with its *own* Connection and endpoint (the JDBC URL names the
database). Credentials are not on either element: each Connection names a
secrets store path and a secrets collection, and the engine host reads the
user and password from that collection at survey time. The server survey
and the database survey are different connectors on different connections,
and Egeria expects them to carry different credentials if they need
different access (the server survey lists databases; a database survey
reads one database's catalogue).

**Ruling.**

1. **Yes, two databases share one server element.** The server is one
   thing in the world and in Egeria; one element per host:port is right,
   and two would be a duplicate the cataloguer and the Portal would both
   have to reconcile. The existing element (`PostgreSQL Server::host.docker.internal:5442`,
   56a974f8) stays.
2. **Each database keeps its own connection and its own credential.** Its
   surveys (catalogue and survey, the database survey, RE's own steps that
   run against it) use the database's Connection, whose collection is that
   database's `<slug>::PostgreSQL Secret`. That is already so; this ruling
   makes it the rule, so no later change routes a database survey through
   the server's connection.
3. **The server's connection carries a server credential, owned by the
   server, never the first database's.** First-registrant-wins is wrong for
   three reasons: what the server survey can see changes with registration
   order; deleting or re-crediting the first database silently changes the
   server survey; and a database's owner never agreed to lend their
   credential to another database's server survey. The server's collection
   is named for the server (`<host:port>::PostgreSQL Server Secret`), set on
   the server's row in RE, where #717 already put the Egeria-visible host.
   The person may choose to reuse a database's account for it, but they
   choose it on the server row, by name, and the row then says so ("uses
   the same account as coco_pharma, chosen 10-17").
4. **Until a server credential is set, the server survey says it has
   none.** RE does not fill the server collection from a database. The
   server row reads "Egeria's server survey has no credential · set one ›",
   and the server survey step reports ○ not run · needs a credential
   (the designer's §4 words). Registering a database still creates or
   adopts the server element; a server element without a usable credential
   is a true state, not a failure of the register.
5. **The existing element is re-pointed, not recreated.** Moving 56a974f8's
   connection from `localhost_docker_coco_pharma::PostgreSQL Secret` to the
   server's own collection is an update of the Connection's
   `secretsCollectionName` (or, if the template's connection cannot be
   updated in place, a replacement connection attached to the same server
   element). It is a live Egeria write: the owner's direct word, as for any
   other. `server_cred_key` then records the server collection, and
   `server_credential_notes` reads it from that record.
6. **The designer's §11 line changes accordingly.** On a database's row:
   "Egeria's server element is shared with coco_pharma" (one gesture away,
   as ruled). The credential clause moves to the server row, because it is
   the server's: "the server survey uses ‹server credential›". It is raised
   onto the database row only when a survey of *this* database was refused
   for a credential reason, and then it names this database's credential,
   which is the one that ran.

Slices this implies (builders): the server credential on the server row
(the `.omsecrets` projection gains a server collection; registry storage
for it may be a DDL, owner's word); server placeholders filled from the
server collection; the server survey's not-run state; the one live
re-point of 56a974f8 (owner's word).

## 3. Settled by observation: "not visible" after a delete is absence

**Observed 2026-10-16.** After `delete_metadata_element`, the
SoftwareServer read back as a typed not-found (`PyegeriaNotFoundException`).
The RelationalDatabase read back as an HTTP 500 wrapping
`OMAG-REPOSITORY-HANDLER-400-010` ("not visible"): Egeria soft-deleted it,
and the repository handler declines to return a deleted instance. RE's
`_classify` treats only a typed not-found as gone, so the database landed in
`unreadable`, the designer's open item 2.

**Ruling: for a GUID RE holds as a pointer, 400-010 is absence.** It is
Egeria answering about that element ("this instance is not visible to
you"), not a failed read. The pointer RE holds no longer leads anywhere
RE can use, which is what `gone` means. So:

- `_classify` returns `gone` for a typed not-found **and** for an error
  whose Egeria message id is `OMAG-REPOSITORY-HANDLER-400-010`, matched on
  the id, not the sentence, and only on the read of the pointer's own GUID.
- The row reads as the designer ruled: "no longer in Egeria · was
  registered 10-15 as 4f2a… · Register again →", one press, no glyph.
- Everything else stays `unreadable`: a 500 without that id, a timeout, a
  401/403 (a sign-in or zone refusal is not absence), a reach failure.
  "Couldn't read Egeria" keeps its own words and offers no register.
- **One caution, recorded so a builder does not widen it.** 400-010 also
  fires for an instance a caller cannot see for other reasons than
  deletion. RE reads pointers as the signed-in user (end-user identity
  rule), so a pointer that becomes invisible to *this* user while it is
  visible to others would read as gone, and Register again would create a
  second element. Two guards keep that safe: the register press first
  looks the element up by qualifiedName (it already does, and adopts a
  visible one), and the "no longer in Egeria" row never deletes or
  overwrites anything on its own. If the qualifiedName lookup finds an
  element the GUID read could not see, the row says "Egeria has a database
  of this name RE can't read · ask its owner", not "no longer in Egeria".
- Why the two element types differ (hard delete for the server, soft delete
  for the database) is Egeria's to say; it goes on the Egeria-side
  question list, not into RE's classification. RE treats both answers the
  same way.

The test pins both shapes: a fake that raises the typed not-found and a
fake that raises a 500 carrying 400-010 both yield `gone`; a 500 carrying
any other id yields `unreadable`. The fakes bind the real gateway signature.
