# REPLY — Designer: first-index consent, per repository or for the batch, as the person chooses (2026-10-20)

To ASK-DESIGNER-FIRST-INDEX-CONSENT-CHOICE-2026-10-20.md, read against main
at b751092b, amending §4 of `REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md`.
The owner's decision stands as the ask records it: the batch ask offers
both paths.

**The rule behind the answers: nothing is indexed without a yes that
names it,** whether one yes covers twelve repositories or each gets its
own. The choice of path is the person's. The ask's job is to make both
paths quick and to show, at every moment, which repositories have a yes.

## 1. "Ask me for each": stepping in place, with the list as the record

**In the same dialog, not one card per repository.** The dialog already
holds the list of twelve with their ticks. Twelve separate cards would
scroll the decision away from the list it's about. So the stepping
happens at the top of the dialog, and **the list below is the record**,
each row taking its answer as it's given:

> **2 of 12 · coco_api**
> 1,240 files · 38 MB of text · fetches: clones ≈ 240 MB · about 6 min and
> 110 MB of index, estimated from the 31 repositories indexed here
> **Index coco_api** · Skip · **Index the remaining 10** · Skip the rest
>
> &nbsp;&nbsp;✓ egeria_python · yes · queued 1st
> ▸ coco_api · asking now
> &nbsp;&nbsp;○ trellis · not asked yet
> &nbsp;&nbsp;…

- **Each step shows that repository's own figures**, the same as the
  single ask in §4, including "time: unknown until the first index" when
  the data is too thin (the owner's ruling on the estimate basis).
- **"Index the remaining 10"** appears from the second step on and carries
  its own totals on hover ("9,200 files · 260 MB of text · 5 fetch · about
  45 min"), so a shortcut is still a yes that names what it covers.
- **"Skip the rest"** ends the stepping without indexing the remaining
  repositories. They stay "not yet indexed", and the dialog closes with
  its summary (below).
- **An answer can be changed** by pressing its row in the list, until
  that repository has started indexing. After that, a started
  repository's row says "indexing" and offers nothing, as a pressed
  control does.
- **Closing the dialog mid-way** is "Skip the rest". Nothing unanswered
  is indexed.

**When the stepping ends**, the dialog's last line sums it up in place:
"3 queued · 1 skipped · 8 not asked". Each count opens its rows, and
"not asked" offers "Ask about the 8 ›" to resume.

## 2. Is the choice remembered?

**The path is remembered, never the consent.** Per viewer (browser
storage), RE remembers which button the person used last, "Index all"
or "Ask me for each", and the next batch ask opens with that button as
the default, focused one. Both are always offered, and the ask is always
shown. The figures change with every batch, and a remembered *yes* would
be indexing without consent.

## 3. The single-repository door

**Unchanged: one ask.** The rag_ingestion row's "Index for chat…" opens
the §4 single ask for that repository, with its figures and **Index it**
· not now. There's no batch around it, so no choice of path.

## 4. When indexing starts

**As soon as each repository gets its yes.** It enters the First
indexing queue at once, in the order of the yeses. The person may take a
while to step through twelve, and holding the first yes until the last
answer would only make the wait longer. The queue runs repositories one
after another, as before. The dialog's list and the queue show the same
words: "queued 1st", "indexing", "indexed 10-20 14:20", "failed ·
‹first sentence›".

This doesn't contradict the owner's "group runs as ONE batch" ruling.
That's about scheduled refreshes of a Group, which run together. A first
index is a consent per repository, so each one queues when it's
consented.

## 5. Words

| Placeholder | Ruling |
|---|---|
| "Index all 12" | **keep** |
| "Ask me for each" | **keep**, with "›" because it opens steps: "Ask me for each ›". First person is right for a choice about how the app should talk to *me*. |
| "Index the rest without asking" | **replace with "Index the remaining 10"**. It's verb first, names the count, and matches "Index all 12". "Without asking" describes what isn't happening. |
| (new) per step | **"Index ‹name›"** · **"Skip"** |
| (new) end early | **"Skip the rest"** |
| (new) summary | **"3 queued · 1 skipped · 8 not asked"**, with "Ask about the 8 ›" |

## The slice

The ask, the queue and the cue are already in the first-indexing round.
This adds:

- the stepping block;
- the list's per-row answers, changeable until started;
- the remaining-N shortcut with its totals;
- the end summary with resume;
- the remembered default path (browser storage, wrapped in try/catch).

**Gate:** the owner opens a batch of three, indexes the first, skips the
second, and takes "Index the remaining 1". He sees the first start
before he answers the second. The summary reads "2 queued · 1 skipped".
