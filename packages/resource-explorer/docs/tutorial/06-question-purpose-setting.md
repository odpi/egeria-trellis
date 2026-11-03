# Walk 6 — Add a question and a purpose, change a setting

*Screens read on 2026-10-29 on a test server, `origin/main` at `afdac240`.*

Three things a person can change without touching Resource Explorer's code: a question, a purpose and a setting. This walk reads each place; it presses nothing that writes.

## 1. Questions: Admin › Configure › Question Catalog

Open **⚙ Admin**. The panel reads "System configuration — not something a user does to curate a specific resource, so it lives here rather than under one of the eight intents." Its menus are **CONFIGURE**, **RECONCILE**, **OBSERVE** and **OPEN DATA**. The OPEN DATA menu arrived with OD1 on 2026-10-29 (#911). Choose **❓ Question Catalog** under Configure.

![The Admin panel on the Question Catalog. The heading "Question Catalog" with a "+ Add question" button. The text reads "Every question authored in docs/dr-egeria/resource_questions.csv — the source of truth for both Scouting's Questions checklist and the Question elements published to Egeria. Append-only (project owner decision, 2026-09-20): add a new question or retire an old one; a question's own text is never reworded in place, since past survey answers refer to it exactly as it was asked. Retired questions stay listed, dimmed, never hidden." A search box "Search question text…", STAGE chips and PERSPECTIVE (FILTER) chips, and "52 / 52 question(s)".](images/06-question-purpose-setting/06-admin-question-catalog.webp)

*"Append-only (project owner decision, 2026-09-20): add a new question or retire an old one; a question's own text is never reworded in place". "52 / 52 question(s)".*

A question is a row in `resource_questions.csv`. It names its stage, the kinds it applies to, its purposes, its perspectives and the analysis that answers it. **+ Add question** adds a row. This walk did not open or press it, and shows no add form (not re-verified on 2026-10-29). The generator refuses a bare step name or a kind with no adapter (the guide, section 7).

## 2. Purposes: an investigation

Open the **Investigation** stage and a row in its table.

![The page of the investigation "Docling Family", with the mark "○ open". The line under the title reads "Study · ☁ Egeria · Explore, Learn". The description reads "An investigation of the Docling Family repos on GitHub". Buttons: Make current, Edit, Reclassify…, Suspend, Close. A "Next steps" box reads "Nothing judged yet · 13 resource(s) in scope, none marked. Dispositions are how this investigation says which ones matter." An "Egeria" block reads "bound · Project::Investigation::docling-family · 13 members not linked" with buttons Publish again, Link members (1), Unbind. Below, "Scope · 13".](images/06-question-purpose-setting/06-investigation-purposes.webp)

*"Study · ☁ Egeria · Explore, Learn". The purposes here are "Explore, Learn".*

Purposes are chosen when an investigation is started or edited (**Edit**). They order the questions: a stage's Questions tab reads, for example, "ordered by purpose · Explore, Learn · 4 of 6 lead, the rest follow in catalog order" (walk 2). **Edit** and the others change the investigation, so this walk did not press them.

## 3. A setting: Admin › Configure › Value profile settings

![The Admin panel on "Value profile settings": "the value profile's and quality checks' settings in force · read-only". Entries read "more than 25% of the tables measured · default", "300 seconds · default" and "60 seconds · default", each with a sentence and a named setting such as VALUE_PROFILE_LIVE_WRITE_TABLE_SHARE, VALUE_PROFILE_TABLE_BUDGET_SECONDS and VALUE_PROFILE_STATEMENT_TIMEOUT_SECONDS.](images/06-question-purpose-setting/06-admin-value-profile-settings.webp)

*"the value profile's and quality checks' settings in force · read-only". "300 seconds · default · The most time one table's value profile may take, across its chunks. Past it the table is "profile incomplete", never partly counted. Setting: VALUE_PROFILE_TABLE_BUDGET_SECONDS."*

This page shows the settings in force and says "read-only". Each entry names the setting that holds it ("Setting: VALUE_PROFILE_TABLE_BUDGET_SECONDS."), so a change is made where that setting is set, not on this page. The other Configure pages are **📝 Annotation Types**, **🗂 Groups**, **🔍 Repository discovery sources**, **🌐 API fetch addresses** and **📁 Egeria's mounts**; this walk read Annotation Types and Value profile settings only.

## Not built yet in this walk

- **Editing a value-profile setting on the screen** (2026-10-29): the page says "read-only".
- **Adding a perspective or a purpose from an Explorer page** (2026-10-29): not seen on any screen read for this walk; the guide's section 7 says both come from Dr.Egeria valid-value lists.
- **A "Dashboards" page and open data in the Explorer's stage strip** (2026-10-29): not in the stage strip. The Admin panel has an **OPEN DATA** menu ("Data portals", "Portal directories", "Open data"); this walk did not open it, so it says nothing about what it shows.
