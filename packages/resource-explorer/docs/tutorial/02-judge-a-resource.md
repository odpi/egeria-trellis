# Walk 2 — Judge a repository

*Screens read on 2026-10-29 on a test server, `origin/main` at `afdac240`. Resource: `docling-project/docling`.*

A resource goes left to right through six stages. This walk reads the first (Scouting) and the fourth (Analysis).

## 1. Scouting: the questions

![Scouting on docling-project/docling. The header tiles read WEBSITE docling-project.github.io · no docs found, LANGUAGE python measured, STARS 69571, FORKS 5178, CONTRIBUTORS 389, LAST PUSHED 4d ago, SIZE 453.1 MB, SECURITY ? not read, DEPLOYMENTS 1904 · 4d ago. Below, "SCOUTING SIGNAL 156,999 lines of code across 652 source files, 1,896 files in total · Health 99/100", then "6 of 6 answered · Scouting" and the first answered question.](images/02-judge/02-scouting-questions-docling.webp)

*"6 of 6 answered · Scouting". The key reads "✓ answered 5 · ✓ answered automatically 1". The header line reads "surveyed 23h ago · published to Egeria 23h ago · ○ not yet indexed index it ›".*

Read the header first. A tile that has not been read says so: SECURITY reads "? not read", not zero. The first question, "Is this repository actively maintained?", is answered "Pass — 356 commit(s) in the last 90 days." Its source line reads "foss_scorecard · run time not recorded · evidence · copy as evidence · re-run · the numbers behind this › · 🔔 notify me".

Wait for a pane to finish before you read it. While it is still loading the count reads, for example, "4 of 6 answered · still reading 2 · 4 back".

## 2. Scouting: Survey & analyses

![The Survey & analyses tab for docling. Three tiles read "ANALYSES RUN ● 37 of 38", "NEED YOU ∅ 0" and "LAST SURVEY ● 4d ago · Scouting Survey". Below, "SURVEY DEFINITIONS · GIT REPOSITORY", a "Scope: scouting" button and the first definition, "Coarse Profile Survey · 7 steps · 3 fetch · runs 1 analysis" with a "Re-run…" button.](images/02-judge/02-survey-and-analyses-docling.webp)

*"ANALYSES RUN ● 37 of 38 · NEED YOU ∅ 0 · LAST SURVEY ● 4d ago". The first definition reads "Coarse Profile Survey · 7 steps · 3 fetch · runs 1 analysis", and it says "ran via Prefect" and "✓ ran 22d ago".*

"Re-run…" starts a run, so this walk did not press it.

## 3. Analysis: the questions and the marks

![The Analysis stage's Questions tab for docling. "16 of 20 answered · Analysis". The key reads "✓ answered 16 · ◌ no reader yet 4". The first question is "Are there outstanding CVEs?" answered "not checked: 0 of 65 declared dependenc(ies) could be queried, so no CVE result exists", with the source line "cve_scan · run 27d ago".](images/02-judge/02-analysis-questions-docling.webp)

*"16 of 20 answered · Analysis". The key reads "✓ answered 16 · ◌ no reader yet 4".*

Read the marks together with the words beside them. The row "Are there outstanding CVEs?" carries ✓, and its answer says "not checked: 0 of 65 declared dependenc(ies) could be queried, so no CVE result exists". The words are the answer; the mark says the question was answered, and here the answer is that nothing could be checked. Do not read it as "no CVEs". The mark on a not-checked answer is queued as a defect (status-words round 9, item 6).

The mark table and the absence words are in [the guide, section 2](../user-guide.md#2-the-words-and-marks).

## 4. Ask in the chat rail

The right rail reads "ASK scoped to docling", with a box, **Ask**, **what would answer this?** and "⌘/Ctrl + Enter". Asking records a chat turn, so this walk did not press **Ask** and shows no answer. The guide's section 4 describes the rail ("not re-verified on 2026-10-29").

**Not built yet in this walk:** nothing in the questions or marks; see [the guide's list](../user-guide.md#10-not-built-yet).
