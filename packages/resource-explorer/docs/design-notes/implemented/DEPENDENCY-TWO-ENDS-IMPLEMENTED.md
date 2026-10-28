# Dependency two ends — implemented (2026-10-08)

Built from `DESIGN-DEPENDENCY-ROW-TWO-ENDS.md` on `re/dependency-two-ends` (stacked on node admission).
A dependency row has two ends and a kind: dependent, relation, typed target, kind, evidence, state. Runtime
and data rows are read from the recovery IR's wires, so the table and the blueprint diagram share one record
(`dependency_table.py`; `mermaid.render(edge_ports=...)`). A confirmed row publishes one
`ResourceMeasureAnnotation` whose qualifiedName is built from the dependent (name and locator), the relation
and the target, never a GUID. The table header states what it does not read.

## Departures from the design note (accepted by the architect)

| | Departure | Reason | Follow-up |
|---|---|---|---|
| a | The annotation attaches to the repository's survey report, with the dependent named inside it (locator, plus the component's GUID once accepted). | There is no general annotation-on-element path yet. The locator is in the qualifiedName so a re-homing is a move, not a rewrite. | Attach to the accepted SolutionComponent's asset once Enrichment-publishing P1 gives that path; a migration re-homes rows by dependent GUID. |
| b | Wires are derived as a `drawn` class only; nothing is written to Egeria. | The materialiser deferred `SolutionLinkingWire` because wires are multi-link and not idempotent. | Write wires with the idempotency key (two ends plus relation as qualifiedName); the same problem the materialiser deferred. |
| c | Evidence is file-only for manifest rows and referenced-only rows. | The stored rows name the file, not the line; no line is invented. | Record the line in the dependency surveyor and the admission finding. |
| d | No reads/writes, endpoint, host:port or module-to-module rows. | Nothing in a compose file says read or write; wire findings carry service names only; stored build rows have no inter-module marker. | After P1: module-to-module `requires` from Gradle project dependencies (derivable). |
| e | `not established` is used only for an ambiguous name (several nodes) or a referenced-only service naming no image. | A name RE does not know is still a real, typed service named in the artifact; calling it unestablished would hide proposable rows. | Revisit when targets can be typed more strictly. |

## Behaviour to know (review of 76adf90e)

| | State | What it means |
|---|---|---|
| f | One link declared in several artifacts (dev and prod compose) is ONE row. | A link is identified by dependent (name and locator), relation and target, not by where it was read; the row carries every piece of evidence (`evidences`, each with its line where known) and the annotation carries `evidence_list`. One confirmation covers the link and exactly one annotation is emitted, so `assert_unique_qualified_names` cannot refuse the report. A confirmation recorded under an old per-evidence key still counts; the latest verdict over all of a row's keys wins by the order made. |
| g | Withdrawing a confirmed row never removes an annotation already published. | It changes what the NEXT survey publishes. The row says "future surveys only" and the withdraw button's title says the same. |
| h | Egeria adopts, never updates, an existing qualifiedName. | `egeria_outbox.apply_element` ADOPTS an existing qualifiedName's GUID without updating it. Within one survey's report a `target_guid` or `dependent_guid` that appears later, or a withdraw then reconfirm, never reaches Egeria. The "a move, not a rewrite" property of the qualifiedName is a design intent and is UNTESTED against Egeria. |
| i | Every survey publishes a new copy. | Each new survey makes a new report, so there is a new annotation copy per survey. A dependent re-keyed by node admission publishes under a new item_key next time. Nothing is archived and there is no cross-survey dedupe. |
| j | The table and the diagram are not quite one record. | The table resolves ends against ALL admitted components (`max_depth=None`); `mermaid.render` resolves against the projected ones at `DEFAULT_PROJECTION_DEPTH`, so a deep "wire" row can draw as an external port. Making both use one node set means choosing between a collapsed diagram and an unprojected one, so it is a follow-up, not a small fix. |
| k | The confirm route authorises and is bounded. | 401, 404, 403 (`_authorize_curation`), 400. At most 200 keys of 512 characters, repeated keys count once, an optional reason of at most 500 is stored with the confirmation and published as `confirm_reason`. |
| l | A request reads once. | `dependency_table.Context` holds the recovery rebuild, the nodes, the referenced rows and the interface findings for one request; the route passes it to the check, the record and the returned table. Synthetic fixture (150 manifest components, 80 compose services): cold build_table 0.27 s here vs 0.31 s on main, warm 0.05 s vs 0.09 s. egeria_git is for PR/CI to re-time. |

## Known gaps

- The Environment Deployment Blueprint route returns `wires` (the rows whose two ends are both referenced-only
  services) but no UI draws them yet.
- `build_table` reads the architecture recovery results once more than before; the cost is unmeasured.
