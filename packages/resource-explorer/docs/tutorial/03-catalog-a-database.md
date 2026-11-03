# Walk 3 — Catalog a database

*Screens read on 2026-10-29 on a test server, `origin/main` at `afdac240`. Resource: `coco_pharma @ local-docker` (`localhost_docker_coco_pharma`).*

Open the database from the **DBs** list in the sidebar (use the filter box, then the row), or by address in the form `?type=db&resource=<slug>`. Use `type=db`: `?type=database` is not an address the Explorer knows, and it paints the page of the wrong kind of resource.

## 1. Describe and publish: header, credentials, findable

![The Curate stage, "Describe and publish" tab, for coco_pharma @ local-docker. The tab row reads "Describe and publish · Contents ✓ 4 chosen · ◌ 25 undecided · Structure ○ none yet · Questions · Survey & analyses · By analysis · Disposition · Schema Inventory · Table preview". Under the title: "surveyed 5d ago · local survey · published to Egeria", "RE's own survey 10-24: connected as egeria_admin, sees 30 of 30 schemas, SELECT on 266 of 266 relations", and "credential change date not recorded · Change credentials…". Then the FINDABLE section with "RE group egeria-databases" and the tag "coco", and WHAT PEOPLE SAY with one rating.](images/03-database/03-curate-describe-and-publish.webp)

*"surveyed 5d ago · local survey · published to Egeria". "RE's own survey 10-24: connected as egeria_admin, sees 30 of 30 schemas, SELECT on 266 of 266 relations". Beside it: "credential change date not recorded · Change credentials…".*

The line that starts "RE's own survey" says what the connecting account can see. "Change credentials…" is where a person replaces them; this walk did not press it.

## 2. Contents: what gets published, and the chips

![The Contents tab for coco_pharma. "asset c990f349-36de-4489-9338-7974dec54cb0 copy", then "What gets published", the sentence "Database element c990f349 in Egeria · published 10-24 00:45 · zones: none · everyone visible · 4 schemas chosen · 4 schemas attached · cataloguer connector's last refresh 10-24 00:45 (the connector's, not a schema's)", and three chips: "published by RE 2 ✓", "attached 4 schemas ✓" and "cataloged by Egeria 0 schemas ◌ not checked since 00:54". Below, "Your scope: 4 of 30 schemas known to RE · declared by erinoverview 10-05 · Egeria's latest survey covers 4 schemas, 55 tables".](images/03-database/03-curate-database-chips.webp)

*The chips read "published by RE 2 ✓", "attached 4 schemas ✓" and "cataloged by Egeria 0 schemas ◌ not checked since 00:54".*

A chip is a statement about the last time RE read Egeria, not about Egeria now. The third chip reads "not checked since 00:54", so its "0 schemas" is what RE saw then. The sentence above the chips and the chips both give counts for the same facts ("4 schemas chosen · 4 schemas attached" and "attached 4 schemas ✓"); today they agree. The sentence above the chips spells "cataloguer", while the chip says "cataloged".

## 3. Publish to Egeria

![The foot of the Describe and publish tab. The section "PUBLISH TO EGERIA" reads "◐ partly committed · 4 queued", then "Database element c990f349 in Egeria · Contents ›", then "Egeria reports", a Refresh button, "read 09:20 · 4 reports", and three report rows, each with "copy", "Show annotations" and "Ask about this →".](images/03-database/03-curate-publish-to-egeria.webp)

*"PUBLISH TO EGERIA ◐ partly committed · 4 queued". "Egeria reports · read 09:20 · 4 reports". Each row reads, for example, "Survey: coco_pharma @ local-docker · surveyed 5d ago · 1858 annotations".*

The publish table, the **Publish** press and what it sends are described in [the guide, section 6](../user-guide.md#6-publishing-to-egeria). This walk did not press **Publish**, **Refresh** or **Ask about this →**, and shows no press result.

## 4. Declared measures: what publishes and what does not

On a database where a library step has run, the Describe tab carries a "PUBLISH · DECLARED MEASURES" table. This one is the throwaway `laz_local_re_reproof_postreset_1025`; its table takes about 40 seconds to answer, so wait for it.

![The declared-measures table on the throwaway, scrolled to the right. The row "never_analyzed_tables · tables", 4 tables, 2026-10-25 01:36, measured, resource, ResourceMeasureAnnotation, has a Publish button and a "Keep in RE" button, the words "will publish · peterprofile · 2026-10-29 14:32 · sent by every commit until Keep in RE", and a green "✓ published · read back 2026-10-29 14:34". Rows above and below read "not chosen · stays in RE" and "○ local · not published".](images/03-database/03-declared-measures-re-row.webp)

*RE's own row, never_analyzed_tables: "will publish · peterprofile · 2026-10-29 14:32 · sent by every commit until Keep in RE" and "✓ published · read back 2026-10-29 14:34". The other rows read "not chosen · stays in RE" and "○ local · not published".*

![The two library rows of the same table. Both read "—", then "one value per member, not one for the resource · see tables_by_staleness, summary", "no as-of", "measured", "member", "generic measure · no native type declared". Each has a greyed "Publish" button, a "Keep in RE" button, "not chosen · stays in RE", "nothing to publish · one value per member, not one for the resource · see tables_by_staleness, summary" and "○ local · not published". Under the table, "PUBLISH TO EGERIA ✓ committed".](images/03-database/03-declared-measures-library-rows.webp)

*The two "Table Statistics Freshness" library rows, statistics_freshness and statistics_freshness_summary: "—", "no as-of", "generic measure · no native type declared", Publish disabled, "nothing to publish · one value per member, not one for the resource · see tables_by_staleness, summary".*

Read the two together. RE's own measure has one value for the resource, an as-of, a native type (ResourceMeasureAnnotation) and an enabled Publish; it was published and read back. The two library rows have no single value ("one value per member"), no as-of, no native type declared, and Publish is disabled with the reason beside it. This walk did not press any button on this table.

**Not built yet in this walk:** a library step's own row publishing from this table (see walk 5).
