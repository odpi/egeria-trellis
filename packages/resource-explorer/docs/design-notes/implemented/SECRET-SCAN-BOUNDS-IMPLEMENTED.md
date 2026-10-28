# Secret scan bounds — implemented 2026-10-03

Branch `re/secret-scan-bounds`. Code: `resource_explorer/surveyors/sub_surveyors/secret_ruleset.py`
(`ScanBounds`, `ScanReport`, `scan_paths`, `_scan_text_bounded`) and `secret_scan.py` (summary).
Tests: `tests/test_secret_scan_bounds.py`.

## Problem

Trace of 2026-10-03: `repo_secret_scan` on docling_core ran ~50 min (2958 s wall, 2956 s CPU,
653 files, 115 MB), docling 1412 s, while openmetadata (19493 files, 299 MB) took 204 s. It ran
in-process in the web server with no size cap, binary check, time cap or match cap.

## Profile (real scanner code, docling-core main zipball, unauthenticated download, 653 scannable files, 115,092,606 bytes)

Per-rule timing on the three biggest files (unbounded, whole-file, `re`):

| file | size | longest line | total | worst rules (raw matches) |
|---|---|---|---|---|
| tests/data/doc/concatenated.json | 12.9 MB | 1.31 MB | 287 s | cohere-api-token 261 s (0), atlassian-api-token 20.5 s (0), adobe-client-id 4.4 s (0) |
| tests/data/doc/concatenated.html | 12.6 MB | 1.31 MB | 260 s | cohere-api-token 238 s (0), atlassian-api-token 16.6 s, adobe-client-id 4.1 s |
| tests/data/doc/2501.17887v1.json | 9.2 MB | 1.31 MB | 169 s | sumologic-access-id 110 s (0), generic-api-key 15 s, atlassian 11.9 s |

Files of 1-5 MB (all under the new size cap) cost another ~550 s whole-file, dominated by
`okta-access-token` (217 s total), `sumologic-access-id` (133 s), `cohere-api-token` (73 s),
`generic-api-key` (52 s); e.g. 2311.18481v1.json (3.7 MB) 57 s cohere + 43 s sumologic + 42 s okta.

## Cause

Not catastrophic (exponential) backtracking. The gitleaks "keyword + separator + token" rules open
with a lazily bounded `[\w.-]{0,50}?` (cohere has two nested, ~50x50 tries) that restarts at EVERY
character of every word-character run. The test data embeds base64 images (a 1.3 MB single line), so
the whole file is one long run. The keyword pre-filter is per FILE: "cohere" (or by chance "okta",
"sumologic" inside random base64) anywhere in 12.9 MB admits the rule for all of it. Cost is
file size x run length with zero matches, which is why throughput varied ~100x and size alone did
not explain it. A synthetic Docling-like JSON scanned fast because it had no long word runs.

A secondary quadratic: `text.count("\n", 0, m.start())` per match was O(file) per match; now a
bisect over precomputed newline offsets (identical line numbers; tested).

## Bounds

`ScanBounds` lives in the scanner (not the Prefect dispatcher), so the in-process whole-definition
path is bounded too. Read once per scan from the environment; a non-positive or unparsable value
falls back to the default with a warning.

| setting | env | default | basis |
|---|---|---|---|
| max file size | `RE_SECRET_SCAN_MAX_FILE_BYTES` | 5 MiB | docling-core's 8 biggest files (5.5-12.9 MB) are test-data JSON/HTML; everything the profile found above 5 MiB is data |
| per-file time | `RE_SECRET_SCAN_FILE_BUDGET_SECONDS` | 30 s | healthy 256 KiB source file is well under 1 s |
| per-step total time | `RE_SECRET_SCAN_TOTAL_BUDGET_SECONDS` | 900 s | under the 1200 s Prefect step ceiling; ~3x the slowest healthy scan (egeria_git 277 s) |
| binary check | n/a | NUL in first 8192 bytes | git's own heuristic; file is not read |

Mechanism (Python `re` cannot be interrupted; `regex` is only a transitive dependency and would
change rule semantics): files up to 256 KiB are scanned whole, exactly as before. Larger files are
scanned in 16 KiB windows with 4 KiB overlap, each keyword-gated on its OWN text, with a monotonic
deadline checked before every rule x window call. A match belongs to the window its start falls in.
One call is bounded by the window, so a budget can be overshot by at most one rule x window call.
Measured: docling-core with the defaults 2958 s -> 78 s (8 files over the cap skipped, 1 binary);
with the size cap lifted to 100 MB, 314 s and two files hit the 30 s file budget.

## Honesty

`scan_paths` returns `(matches, ScanReport)`. The report holds files skipped by size, by binary,
timed out (units done/total), never opened because the total budget ran out, and whether the total
budget hit. `scan_summary` is derived from it:

- any size skip, file timeout, unreached file or total-budget hit -> outcome `partial`
  (cause `files_over_size_cap` / `file_time_budget_hit` / `total_time_budget_hit`), never
  `no_signal`, never `recovered`, confidence 0 (not conclusive), even when matches were found.
  Summary: `PARTIAL scan — N file(s) over the B byte size cap were skipped; ...; largest: path (bytes)...
  This is NOT a clean result and NOT a claim of no secrets: the skipped files were not checked.`
- the row's detail carries `files_skipped_size`, `files_skipped_binary`, `files_timed_out`,
  `files_not_reached_total_budget`, `total_budget_hit`, `scan_bounds` (the limits in force),
  `elapsed_seconds`, and `top_skipped_size|binary|timed_out|not_reached` (10 each: path, bytes).
- binary files are recorded and mentioned in the summary but do NOT make a scan partial (they hold
  no text to match; flagging every repo with a PNG would empty the word). A deliberate choice.
- matches found before a budget hit are stored (`secret_pattern` rows) as before.
- a scan that skipped nothing persists exactly the keys it always did (tested).
- only paths and sizes are recorded, never matched text (tested against summary, detail and logs).

## Behaviour changes to know about

- Binary files (NUL in the first 8 KiB) used to be read with `errors="ignore"` and scanned; now
  skipped and counted, so `files_scanned` drops for repos with binaries.
- Files over 256 KiB are windowed: a match longer than the 4 KiB overlap straddling a window border
  is missed (a private-key block is ~1.7-3.2 KB). Files up to 256 KiB are byte-identical.
- `scan_paths` now returns `(matches, ScanReport)` instead of `(matches, scanned, excluded)`;
  its only caller is `SecretScanSurveyor`. `tests/test_annotation_finding_derivation.py` greps the
  source for the offset field and was updated to the new spelling.

## Not verified

- A real docling_core scan inside the web process (only the scanner functions were run, in a
  separate Python process on the extracted zipball).
- Old-vs-new equality on real files: 599 docling-core files up to 256 KiB identical (2 matches in
  the whole repo); the 43 files of 256 KiB-5 MiB had 0 matches in either, so windowed equality on
  real secrets rests on the synthetic test (`test_windowed_scan_equals_whole_scan`).
- Wall time of the remaining 78 s was not broken down per rule; it is the windows that contain a
  keyword (chance hits inside base64) running the lazy-prefix rules.
- The pathological test uses a synthetic exponential pattern; no shipped rule is exponential.
  Red evidence: on current main the new test file fails at import (no `ScanBounds`); the old code
  has no bound to measure, so an unbounded run on the pathological input was not attempted.
- Prefect-dispatched path not exercised (no Prefect contact by rule).
- The adapter's `compute_cost="high"` / Prefect timeout comments were not changed.
