# The Trellis journey — a narrative timeline

> **Status (2026-11-19, 6.3 Tech Preview):** a narrative timeline written through 2026-08-28 and **not extended** since (the span, commit counts and acts end there). The 6.3 story is in `RELEASE_NOTES.md` and `CHANGELOG.md`.

Source material for writing about the project. Dates and figures are from the
repository, not recollection; commit hashes are given so anything here can be
checked rather than trusted.

**Span:** first commit 2026-08-06, 559 non-merge commits by 2026-08-28.
Acceleration is visible: 60 commits in week 32, 71 in week 33, 178 in week 34,
334 in week 35.

---

## Act I — Two tools discover they are one system (Aug 6–16)

Trellis begins as a `uv` workspace monorepo consolidating two sibling Egeria
tools that grew up apart: **Resource Explorer**, which surveys and catalogs
repositories, databases and filesystems, and **Egeria Advisor**, which answers
questions about Egeria itself. Both imported on 2026-08-06.

The interesting work is not the merge. It is the **audit before the merge**.
Rather than assume duplication and extract, each module pair got one of three
verdicts: near-identical (extract as-is), diverged for a confirmed reason
(extract with parameters), genuinely different problems (leave alone). The
precedent was `trellis-vectorstore`, whose extraction found that "every
constructor parameter traces to a confirmed, behavioural difference — not a
guess."

That discipline produced a result worth writing about: **most apparent
duplication was real divergence.** Two collection routers with the same filename
and class name solve different problems. Two chunking strategies — windowed
retrieval versus semantic units — are a deliberate split, not an accident. What
*did* extract: a vector store (Aug 9), a shared resource-dependency mechanism
(Aug 16), and later a query cache whose EA copy turned out to be documented as
LRU throughout and **actually FIFO** — a real shipping bug found by comparison.

**The blog angle:** consolidation is usually written as "we merged two things."
The better story is the audit that decides what *not* to merge, and the bug that
only a comparison could find.

---

## Act II — The measurement habit (Aug 17–24)

A pattern establishes itself, and it is the project's signature: **a design claim
is not accepted until it is measured, including — especially — the author's own.**

Three examples, each of which overturned a plan:

**Perspective cannot dispatch.** The design asserted that Purpose and Perspective
together select which analyses run. Measured against the real catalog: not one of
twelve Perspectives reaches an analysis another does not also reach. The sets are
strictly nested — Perspective varies the *size* of a result, never its *content*.
Purpose scored 0.22 mean pairwise overlap against Perspective's 0.37. The design
was corrected: Purpose ranks, Perspective weights.

**Granularity is not precision** (Aug 22). A collapse the team believed in turned
out smaller and differently shaped than assumed, once counted.

**Docling goes from 0 dependencies to 61** (Aug 23). A dependency decision made
on measurement rather than reputation.

Alongside this, an unusual artifact appears: a **ratchet test**. 112 existing
"broad except, log-only, still returns success" sites were too many to fix, so a
baseline recorded them and the test fails if the count rises. The file may only
shrink. It has since caught four unrelated problems, none of them the one it was
written for — a syntax error, a deleted function, a stale entry, and a wrong
return shape.

**The blog angle:** what does it cost to make "measure before asserting" a
habit rather than an aspiration? And what do you do with 112 known defects you
cannot afford to fix today?

---

## Act III — Context compilation (Aug 27–28)

The question that starts it is deceptively small: *"context compilers seem to be
the fad du jour — does it make sense for us?"*

The answer turns out to be **"you already built the front half and called it
question dispatch."** `Purpose + Perspective → Questions → analysis_ids` is a
spec language, an intermediate representation, and a resolver registry. What was
missing was the back half: budget, compression, provenance, a manifest.

Two packages ship in two days — `trellis-artifact-tree` (containment trees,
five format adapters, a profiler) and `trellis-context` (a spec and a
deterministic packer) — plus the bridge that puts compiled evidence into RE's
chat prompt.

**Three design rules were falsified by their own author, in order:**

1. Chunk size derives from unit size → measured 340/164/356 against hand-picked
   768/1024/1536. Wrong magnitude *and* ordering.
2. Chunk size derives from document size → fits documentation within 20%, fails
   on code: one module's p75 document is 32,467 tokens.
3. **Decide which unit is coherent, then size to it.** Units-per-document is the
   discriminator, and the tree already carried it.

That third version explained something neither tool documented: RE's constants
track unit size, EA's track document size, and *neither is wrong* — they chunk
different things because they answer different retrieval questions.

**The blog angle:** an experiment that falsifies your design twice before you
write the code is not a setback, it is the cheapest thing that happened all week.

---

## The recurring failure — absence that looks like a fact

35 of 559 commit messages name absence, silence, staleness or a wrong zero. It is
the project's dominant bug class, and it wears a different costume every time:

- A gap note naming OSV.dev as a candidate tool, sitting beside a CVE scan
  already using OSV.dev
- A comment naming four retired perspectives while the API served twelve — a
  request built from it would have filtered on nothing and looked like it worked
- A test hard-coding a question as its example of an unanswered gap, which failed
  the moment the gap was closed
- A resolver reading one table while most analyses store elsewhere: seven
  analyses reported as missing while holding data
- A prompt asserting analyses "have not run" when two of three run cleanly and
  emit annotations explaining the empty result
- An empty file, produced by a failed command whose exit status went unchecked,
  which was structurally valid at every point it was inspected

The shape is always the same: **a true statement about the mechanism, read as a
claim about the world.** A manifest saying "no candidate — resolver produced
nothing yet" was accurate throughout; calling that set "gaps" is what turned it
into a claim about the catalogue.

The response was not more care. It was **borrowing a vocabulary that already
existed**: `result_status.py` had already separated *ran and found nothing* from
*never ran* — "the same number and opposite answers" — and the compiler simply
had not asked.

**The blog angle:** the strongest one available. Every team has this bug. Most do
not name it, and the ones that do usually name it once.

---

## The working method — several agents, one checkout

Worth writing about in its own right. Multiple Claude sessions work the same
repository simultaneously, in the same checkout, because each runs from the home
directory and cannot take a worktree.

That produced its own failure genre and its own rules. A blanket `git add -A`
swept an unrelated design doc into a commit about a backfill checklist. A bare
`git stash`, run to answer "is this failing test mine?", reverted three files
another session had in flight. Both recovered — but only because the session that
did it said so immediately.

The repo convention that resulted is more useful than a prohibition: **no
whole-tree git operations in a shared checkout — name the paths.** Every command
that takes a pathspec accepts one, and `git stash push -- <paths>` is the safe
door into the same room. A rule that only forbids gets worked around when the
need is real.

The sessions also cross-check each other. One ran a compile rather than relay a
number and found seven false gaps. Another verified a wire format by intercepting
the request instead of reading the source. A third caught its own negative control
being wrong — a check whose negative control never runs is the failure it exists
to prevent.

**The blog angle:** what does it actually take for several agents to work one
codebase without corrupting each other's work? The answer is not coordination
protocols. It is narrow rules with named alternatives, and telling each other
immediately when something breaks.

---

## Suggested series

1. **"We audited before we extracted, and most of the duplication was real."**
   Consolidating two tools, and the FIFO-cache bug only comparison could find.
2. **"112 known defects and a ratchet."** Living with debt you cannot afford to
   fix, and the test that has since caught four unrelated things.
3. **"My design was wrong twice before I wrote the code."** The profiler, and
   experiments that falsify their author.
4. **"Absence that looks like a fact."** The dominant bug class, six costumes,
   and the vocabulary that already existed.
5. **"Several agents, one checkout."** Shared-state failures and the rules that
   actually worked.
6. **"You already built the front half."** Context compilation as finishing
   something, not adopting a trend.
