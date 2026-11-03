# Roll-forward experiment on coco_pharma, 2026-10-06 (predictions written BEFORE the commit)

Question: after the ISSUE-117 cascade, does a new commit on the unchanged 8813 build (46aaf5db,
no block) re-catalog onto NEW elements, with nothing undone in Egeria?
Authorised by the project owner (in this session, 2026-10-06, while away; the owner changes the
`us_sales` choice and reviews afterwards). Nothing is detached, archived, restored or deleted by anyone.

## Pre-state (read-only, 2026-10-06)

Registry latest choices for `localhost_docker_coco_pharma`: coco_ods catalogue, coco_sus catalogue,
us_sales **leave_out** (must be set to catalogue before the commit), eu_sales leave_out (never cataloged),
target_sales leave_out (set 14:41; not yet shown to have any element in Egeria, to be read first).
Newest proof row: id 35 `archived` us_sales 87271b37 (16:15).

Egeria, read by GUID: database 17f0a963 Memento, QN `…coco_pharma_archivedOn_Tue Oct 06 16:04:25 GMT 2026`;
schemas 28bbde37 coco_sus, a358abb3 coco_ods, 87271b37 us_sales all Memento; table 9863e995 Memento.
Cataloguer targets as read now: an EMPTY list (not the 3 the architect expected). Empty may mean the
targets were removed, or hidden because their elements are Mementos; NOT yet determined.

## Pre-state refreshed 2026-10-06T16:58Z (minutes before the press)

- Registry: us_sales = catalogue (set 16:57:33 by the owner); coco_sus, coco_ods catalogue; eu_sales and
  target_sales leave_out.
- Egeria lookup by template qualifiedName for eu_sales, target_sales, us_sales: "No elements found" (us_sales
  is a hidden Memento, the other two were never created by RE). Nothing for a leave_out to remove.
- FINDING in its own right: the JDBC cataloguer's catalog-target list reads EMPTY at 16:58Z (read twice,
  16:5x and 16:58). Cause undetermined: removed, or hidden because the elements are Mementos.
- Database 17f0a963, schemas 28bbde37, a358abb3, 87271b37 and table 9863e995 all still Memento.
- Peer round: Portal clear, egeria-python-20 clear, PR/CI not yet answered, architect: owner presses.

## Predictions

1. Publish: the database name no longer resolves, so RE creates a NEW database element with the original
   name. Old 17f0a963, new GUID recorded. The server lookup by name still finds d87059ce.
2. Schemas coco_sus, coco_ods, us_sales: new elements under the new database (DataSetContent), new GUIDs.
3. Attach: IF the 3 old targets are still listed (Mementos, same catalogTargetName) the guard matches by
   name, reports "already attached" and initiates nothing, so the cataloguer never builds tables under the
   new schemas. That is the defect the experiment exists to show (guard must match on element GUID and
   ignore targets whose element is archived). IF the list really is empty, the guard attaches normally and
   this prediction fails; record which.
4. Report: the SurveyReport by name was renamed too, so a new report with 76 annotations.
5. Ownership recorded on the new database.
6. Survey: a new engine action limited to the three schemas.

## Observed (filled in after)

Commit pressed by the owner on 8813 at about 16:59Z. Proof rows 36-47, read back by GUID at 17:35Z.

1. Publish: CORRECTED (see the correction section). The database `def55997` (RelationalDatabase, not Memento, original QN
   `PostgreSQL Relational Database::host.docker.internal:5442::coco_pharma`). Old 17f0a963 untouched, still Memento.
2. Schemas: CONFIRMED for coco_sus (new 4079a1d5) and coco_ods (new 1f31b602), original names, not Memento.
   us_sales was NOT re-catalogued: RE's own guard held it ("can't be re-included until Egeria restores
   archived elements"); its Memento 87271b37 still holds the original name.
3. Attach: PREDICTION FAILED, in the sense that the defect did not appear. The target list was already EMPTY
   before the press (read 16:58Z), so there were no stale targets to match by name. Two new targets were
   attached (proofs 38-41, attach_requested then target_attached) and the list now holds exactly 2
   (1f31b602 coco_pharma.coco_ods, 4079a1d5 coco_pharma.coco_sus). The name-matching guard defect is NOT
   shown; it remains a risk only if old targets are ever present with archived elements.
4. Report: a SurveyReport exists on the new database (ReportSubject 1) with 56 ResourceMeasureAnnotations,
   not the predicted 76. Not explained yet; the new database's own survey (below) may account for the count.
5. Ownership: owner_result proof 37 on def55997 (the row exists; the Egeria-side ownership was not read back).
6. Survey: new engine action `0d625a7c` COMPLETED in 53 s (requested 1791305953991, completed 1791306006994),
   "postgres-database-survey-service has completed the analysis of asset def55997". Was it limited to the
   three schemas? Not checked.

Unexpected: the new schemas have NO tables or schema type at 17:35Z. Each has 3 related elements only
(CatalogTarget, ActionTarget, DataFlow). The cataloguer's last refresh was 17:04:24Z, after the attach, so
the tables were NOT built by that refresh. Cause unknown (candidates: needs another refresh; the cataloguer
looks up the schema by its own QN form `<DBQN>::<schema>` and RE's template form does not match).
Nothing was detached, archived, restored or deleted by anyone.

## Follow-up reads (read-only, ~17:40Z)

**Two causes are visible for the missing tables, and the first one stops the cataloguer before the second is reached.**

A. Daemon log, qs-integration-daemon, 17:03:51Z, refreshing action target `coco_pharma.coco_sus`:
   `JDBC-INTEGRATION-CONNECTOR-0016 ... NullPointerException: Cannot invoke JDBCResourceConnector.getConnection()
   because "jdbcResourceConnector" is null` at `JDBCIntegrationCatalogTargetProcessor.getJDBCSchemaName(...:314)`,
   called from `refreshDatabaseSchema(...:207)`. The connector had no JDBC resource connector for the target,
   so it could not even read the schema list. NOT a 409. Why the connector is null for the NEW database is not
   established (candidate: the new database element lacks the connection/secrets link the cataloguer follows).
   The same log shows the cataloguer still refreshing an action target `scratch_cat_test6_database` that my own
   target read does not list, so the target list I read is not the whole list the connector works from.

B. S16 candidate, CONFIRMED as a latent blocker: the OLD schema types of coco_sus and coco_ods are NOT Mementos
   and kept their original qualifiedNames
   (`PostgreSQL Relational Database Schema::host.docker.internal:5442::coco_pharma.coco_sus_schemaType`, same for
   coco_ods). The old us_sales schema type is a Memento and was renamed (the suffix `_archivedOn_...` repeated
   many times, one per archive pass). So a new schema type for the new coco_sus/coco_ods would collide on
   qualifiedName if the cataloguer got that far. It did not, because of A. Not observed as a 409.

C. 56 versus 76 annotations: NOT resolved. The related-element read returned one annotation per database at
   depth 0 and does not list the missing names; not diagnosed.

## Connection-graph reads (read-only, ~17:50Z)

- Database level: the OLD database 17f0a963 has its `::Connection` (VirtualConnection 24197efb) renamed with
  `_archivedOn_` and Memento. The NEW database def55997 has a LIVE ResourceConnection to a new VirtualConnection
  d98b8ad5 (`...coco_pharma::Connection`). The four exact database-level names (`::Connection`, `::Endpoint`,
  `::SecretsStoreConnection`, `::SecretStoreEndpoint`) all resolve to live elements. So the database-level
  connection graph WAS re-created; the architect's database-level candidate is not what blocks.
- Schema level (the cause that fits): the OLD coco_sus 28bbde37 still has 7 relationships including a
  ResourceConnection to its own schema connection `...coco_pharma.coco_sus::Connection` (VirtualConnection
  1e236ecc), which is LIVE, not Memento, with its original name; its schema type 06b3e6c9 is live too. The NEW
  coco_sus 4079a1d5 has 3 relationships (DataFlow, ActionTarget, CatalogTarget) and NO ResourceConnection. The
  schema-level connection graph is named on the schema's qualifiedName, was not archived, and kept its name, so the
  new schema's template create could not link its own. That matches the cataloguer's null jdbcResourceConnector.
  Not proven by a controlled test: the template create's log was not read.
- scratch_cat_test6: a name lookup of its database finds nothing, which does not show whether its target remains.
  Unresolved.

## Conclusion

Roll-forward of a database works at the asset level (new database, new schemas, new targets, new survey) and is
blocked below it: the cataloguer never built tables, for reason A, and would meet reason B next, because the
archive left some archived-lineage elements (the schema types of the two kept schemas) un-archived and keeping
their names. Prediction 3 is withdrawn as unproven: the target-list read does not show the connector's full list.
Cause of A, best fit from the connection-graph reads: the schema-level connection graph and schema types of the old
schemas were left live under their original names, so the new schemas could not get their own ResourceConnection.

## CORRECTION (2026-10-06, after PR/CI's 8813 log read)

This note said the press CREATED the new database def55997. That is wrong. Egeria's own creation times:
database def55997 and its connection d98b8ad5 were created at 16:04:26Z by erinoverview, i.e. by the
INCIDENT commit aa798e88's survey step (8813 log 11:04:27 CDT: "Created PostgreSQL database element:
def55997", because the lookup by name no longer found the archived 17f0a963). The press at 16:59Z adopted
that database (proof 36 database_published, 37 owner_result on def55997). Only the schemas were created by the
press: coco_sus 4079a1d5 at 16:59:08Z, coco_ods 1f31b602 at 16:59:11Z.

So: prediction 1 (new database created on re-publish) is not demonstrated by the press; it was done by the
incident commit's own survey step. Prediction 2 (new schemas) stands. The 56 annotations on def55997 and its one
SurveyReport also date from the 16:04 survey step, not the press (the 16:04 SurveyReport publish itself failed with a
409 on the old report name; the report found now was not traced to its creation time).
Also from the 8813 log: the leave-out of us_sales failed at its first column read-back
(`...us_sales_forecast::createddate was archived but the read-back does not show it`), and the run ended failed at 11:05:01 CDT.
