# Brief — an as-of date on every packed evidence section (2026-11-10)

Status: **decided** (the project owner, 2026-11-10: all four §5 recommendations); build next. Requested by the owner on 2026-11-10 after the
closed-loop step 1 follow-up (#1021) showed how rarely staleness can be judged at all. Reviewed in outline
by the architect session on 2026-11-10; its additions are folded in below (§3, items a–h). The architect
passed the first full draft (78c072c3) with two additions: oldest-row `as_of` in §3a, and compile reuse in
§3h.

Rules that apply: one worktree, one PR, one `*-IMPLEMENTED.md`; DCO sign-off on every commit; the
coordinator's gate before merge.

---

## 1. The problem

The closed-loop diagnosis (`docs/feedback-closed-loop-design.md` §2.3) suggests **stale** when a packed
section's evidence is more than `STALE_AFTER_DAYS` (90) older than the answer. Since #1021 it also says
when it could not judge: "N of M sections have no as-of date".

It almost never can judge. The architect's survey of laz's last 400 compiles (2026-11-10):

| | entries |
|---|---|
| packed evidence entries | 4729 |
| … with an as-of in `manifest.declared` | 4 |
| … with an as-of on the pointer (`packed[i].pointer.as_of`) | 6, **all without a timezone** |
| … with both | 0 |
| `instructions` entries (not evidence; never dated) | 402, in 393 compiles |

So 99.8% of packed evidence carries no as-of, and the few that do are split across two locations, one of
which `not_judged` and the stale rule never read. A stale suggestion cannot fire, and the panel now says so
on nearly every rated answer. Saying so is correct; the cause is upstream, in the compile.

## 2. Where the date is lost today

The data exists at compile time. It is dropped on the way to the manifest.

- **Generic findings path** (`context_compile.py`, `_provenance`): each finding's `surveyed_at` is in the
  candidate's provenance. It reaches the manifest only through a pointer, and only for analyses in
  `_POINTABLE_VIEWS`. `_packed_entry` in `trellis_context/packer.py` writes key, role, rung, size and the
  pointer, never provenance.
- **Reader-fallback path** (findings empty or thin, so the analysis's own results reader is used):
  provenance is set to `surveyed_at: None`, though the results envelope carries `surveyed_at`
  (`_ENVELOPE_KEYS`).
- **`interface_surface`, `chaoss_metrics`, `api_structure`** extras: provenance `surveyed_at: None`
  whenever they create it.
- **Declared sections** (`declared_context.section_rungs`): `seen["as_of"]` is set from
  `fact.as_of`/`read_snapshot`/`surveyed_at`, and lands in `manifest.declared`. That's the source of the 4.
- **The fact layer** (`facts.py`, `Fact.as_of`): `read_snapshot` or `last_run_at`, available for every
  analysis the compile considers (`_facts`, already used by `_judge_gap`).
- **No timezone:** the registry writes 70 timestamps with `datetime.utcnow().isoformat()`, against 39
  timezone-aware ones; the ingestion pipeline and the database surveyor do the same. These are UTC by
  construction but carry no zone, so `_parse` correctly refuses them.

## 3. The change

**a) The field.** `packed[i].as_of` is the age of the evidence: when the analysis result was produced. It is
never compile time and never vote time. Sources, in order of preference:
1. the result's own `surveyed_at` (findings rows, or the reader's envelope) — the **oldest** one if the
   section packs several rows;
2. otherwise `Fact.as_of` from the fact layer (`read_snapshot`, else `last_run_at`).

Oldest, not newest, because `as_of` is what the stale rule ages: a section that packs one fresh row and one
200 days old is partly stale, and "newest" would hide it. The newest goes in its own field,
`packed[i].as_of_newest`, written only when it differs. One meaning per field.

When both exist, the result's `surveyed_at` wins: it describes the rows actually packed, while `Fact.as_of`
describes the analysis's latest run, which may postdate what was read. Record which one was used in
`packed[i].as_of_source` (`"result"` or `"fact"`), so a later reader can tell.

**b) Absent stays absent.** If neither source has a date, leave `as_of` out. Never fill it with compile time:
that would make every section look fresh, and the stale rule would be confidently wrong instead of
honestly silent.

**c) Timezone-aware writes, scoped.** Add one helper (e.g. `now_utc_iso()`) returning an aware UTC ISO
time, and switch the write sites that feed manifests to it: findings `surveyed_at`, results envelopes'
`surveyed_at`, run `last_run_at` and `read_snapshot`, and the database surveyor's `surveyed_at`. This is
not a repo-wide sweep of all 70 `utcnow()` sites. Existing stored naive dates stay as they are and are
reported as "unreadable" by `not_judged`; no backfill and no "assume UTC" at read time.

**d) One location, three readers.** The date lives on the packed entry. In the same PR, `not_judged`, the
stale rule in `suggestions`, and `journal._as_ofs_of` all read `packed[i].as_of` first, falling back to
`manifest.declared[key].as_of` for older manifests. The pointer's `as_of` keeps its own meaning (when the
linked view's data is from) and is not read for staleness.

**e) The provenance gaps.** Fix `surveyed_at: None` on the reader-fallback, `interface_surface`,
`chaoss_metrics` and `api_structure` paths in the same change, by reading the envelope's `surveyed_at` or
the fact layer. Any path that can't be fixed is listed in the PR as a known gap, by name.

**f) Real-shape tests.** Fixtures must look like a real manifest: an `instructions` entry with role
`instructions`, evidence entries with and without `as_of`, and a legacy entry with a naive pointer date.
(#1021 passed review with fixtures that left out `instructions`, and so missed counting it as undated.)

**h) Compile reuse must not freeze the date.** `_compile_id` hashes rung text only, so a later compile over
a re-surveyed but unchanged result gets the same id, and `record_compile` then only bumps `hits` and
`last_seen_at` (`registry.py`). The stored manifest would keep the first compile's `as_of` forever, and
evidence re-verified since would go on being suggested as stale. The options:

- (i) on a hit, refresh `as_of`/`as_of_source` in the stored manifest. It costs one JSON rewrite per hit,
  and concurrent hits race harmlessly (both write the current dates). But an answer given *before* the
  re-survey would then be judged against the newer date, which may postdate the answer itself.
- (ii) store the dates per hit. That's correct for every answer, but it needs a table or column (DDL).
- (iii) accept it, and word the rule "as of the first compile".

**Recommended: (i) with a short history.** On a hit whose dates differ, append
`{"seen_at": <hit time>, "as_of": {key: date}}` to a capped `manifest.as_of_seen` list (e.g. last 20), and
refresh the top-level fields. The diagnosis reads the newest entry at or before the answer's time, and falls
back to the packed entry. That gives (ii)'s per-answer accuracy inside the existing JSON, with no DDL. A hit
whose dates are unchanged writes nothing beyond what it writes today.

**g) Out of scope, noted for later.** The 90-day threshold may need to vary by analysis family: a
dependency list ages faster than a licence file. Not part of this change.

## 4. Where the work lands

- `trellis_context/packer.py`: `Candidate` (or `PackedSection`) carries optional `as_of`, `as_of_source`
  and `as_of_newest`; `_packed_entry` writes them when set. It is a library change: bump `trellis-context`
  0.1.0 → 0.2.0. RE depends on it as an unpinned workspace source (`pyproject.toml`), so it picks up 0.2.0 in
  the same PR; add a `>=0.2` floor there so a stale venv fails loudly rather than packing without dates. EA
  does not import `trellis_context` today (it is only named in `collection_config.py`'s package list), so it
  picks this up when it adopts the library, not before.
- `registry.record_compile`: the hit path per §3h.
- `resource_explorer/context_compile.py`: set the candidate's `as_of` per §3a on every evidence path,
  including the provenance fixes in §3e.
- The write-site helper (§3c), with the sites that feed manifests switched to it.
- `feedback_diagnosis.py` (`not_judged`, `suggestions`) and `journal.py` (`_as_ofs_of`) per §3d.
- Tests per §3f, plus a hit-path test: a re-surveyed, unchanged result refreshes the dates and appends to
  `as_of_seen`.
- Checks against laz after deploy:
  - positive: the share of new compiles' evidence entries with an aware `as_of` is near 100% for any resource
    surveyed after the deploy;
  - negative: no new compile's `as_of` equals its own `created_at` (that would mean compile time leaked in,
    against §3b).

No DDL: the manifest is stored as JSON. Compile identity is unchanged: `_compile_id` hashes rung text only,
and deliberately not provenance timestamps ("a re-read of the same stored result at a later `fetched_at` is
the same compile"). `as_of` is provenance and must stay out of the hash. Only the manifest gains a field.

## 5. Decisions for the project owner

1. **Go or no go** on this change as scoped above.
2. **Library or RE only.** Whether `as_of` becomes a field of `trellis_context`'s packed entry (recommended:
   EA's compiles would get it too), or RE adds it to the manifest after packing.
3. **Write-site scope.** Confirm §3c's scope (only the paths that feed manifests) rather than all 70
   `utcnow()` sites.
4. **Compile reuse (§3h).** (i) with a history, as recommended; plain (i); (ii) per-hit storage, with DDL;
   or (iii) "as of the first compile".

**Decision (project owner, 2026-11-10):** "go with your recommendations on all four". That is:
1. Go, as scoped.
2. `as_of`, `as_of_source` and `as_of_newest` become fields of `trellis_context`'s packed entry (0.2.0);
   RE takes a `>=0.2` floor.
3. Timezone-aware writes only at the sites that feed manifests, not all 70 `utcnow()` sites.
4. Compile reuse: (i) with a history: refresh the top-level dates on a hit, and append to a capped
   `manifest.as_of_seen`.

Build note from the architect's read-back: the `as_of_seen` append is a read-modify-write of the stored
JSON, so two concurrent hits could drop one history entry. Do the append in a single UPDATE (jsonb on PG),
or state the loss in the build PR.

**Decision (project owner, 2026-11-10):** split the build in two. Sizing the write sites showed that nearly every
survey write feeds manifests: 77 findings and metrics write calls, 36 of which pass their own `surveyed_at`, built
by 76 separate `utcnow()` calls. `surveyed_at` is also the string-compared read key (`MAX(surveyed_at)`).
- **PR A:** library fields, compile stamping, the three readers, the repeat-compile history and the provenance
  fixes. Dates are stamped as stored, so today's dates without a zone are reported as "cannot be read".
- **PR B:** one `now_utc_iso()` helper through the survey write paths, with an audit of the readers (64
  `fromisoformat` calls, and the read key).
