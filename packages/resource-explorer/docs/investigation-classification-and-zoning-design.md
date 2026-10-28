# Investigation classification and zoning — design & plan

**Status: BUILT. All six phases are implemented and live-verified (2026-09-07/08).** Extends
`docs/investigation-framing-design.md` §1 (the Investigation record) and §6a
(promotion). Does not supersede it; that document still governs membership,
Purpose, and the local-first/promotable shape. This one covers two things it
left open: **which Egeria classification an investigation carries**, and
**who can see the investigation and the artifacts it produces**.

> **Superseded 2026-10-08:** the draft zone was retired as a default (owner ruling: "DRAFT is a content status, not a zone"). Where this document describes `resource-explorer-draft` as the default zone for published assets, read it as history; current behaviour is in `resource_explorer/egeria_identity.py` and `docs/admin-guide.md`. Private-investigation zoning (`[resource-explorer-private, <owner>]`) is unchanged.


Written 2026-09-07 from a proposal by the project owner. Everything asserted
about Egeria below was read out of the Egeria 6 source in
`~/localGit/egeria-v6/egeria`, not from documentation — file and line given
in each case, because two of the findings are the opposite of what the type
model suggests.

**§3.3 was rewritten later the same day after the project owner corrected it.**
The original claimed zone security lists could only be seeded from a config file
and that per-user zones were infeasible; there is a Security Officer API that
writes them at runtime. The underlying error is worth keeping in view: the
source was read correctly and the *running system* was not consulted, so a
checked-in config file stood in for live state — and it turned out not even to
match it. Everything in §3.3 is now read from the live platform.

---

## 0. Summary of the findings that shape the design

Four facts do most of the work here. Three of them were surprises.

1. **`Experiment` is a real Egeria classification**, and Dr.Egeria already
   accepts it. RE's local vocabulary is missing it.
2. **RE already collects a classification and never sends it.** Every
   investigation ever promoted is a bare `Project`.
3. **Zoning is legal on any element, not just Assets** — `ZoneMembership` is
   defined against `OpenMetadataRoot` as of the 6.0 archive. Projects,
   Collections, SurveyReports and Annotations can all carry it.
4. **Zone enforcement fails open, and the obvious private-zone spelling
   denies nobody.** An element with no `ZoneMembership` is not evaluated at
   all, and a zone the platform does not recognise is *ignored* rather than
   treated as restrictive. Both directions read as "visible".

(4) is the one that decides how this gets built. It is the shape
`find-absence-as-answer` describes with real consequences attached: the UI
says private, Egeria serves the element, and nothing errors.

---

## 1. The vocabulary

### 1.1 What Egeria actually has

`OpenMetadataType.java` (model 0130 Projects) defines five classifications
that are peers, plus one that is not:

| Classification | Egeria's own definition |
|---|---|
| `PersonalProject` | "an informal project that has been created by an individual to help them organize their work" |
| `StudyProject` | "a focused analysis of a topic, person, object, or situation" |
| `Task` | "a self-contained, short activity, typically for one or two people" |
| `Campaign` | "a long-term strategic initiative that is implemented through multiple related projects" |
| `Experiment` | "a project conducting an experiment that is testing a hypothesis (documented in the `hypothesis` attribute)" |
| `ProjectClassification` | **not a peer** — "capturing the description of a project's governance and the expectation of how the results will be used" |

Dr.Egeria's `Project Type` attribute
(`egeria-python/md_processing/data/compact_commands/commands_project_compact.json`)
already carries all five plus a bare `Project` in its `valid_values`, so the
authoring path needs nothing new. Its *description* string is stale and names
only four — logged as ISSUE-92 in `egeria-python/PYEGERIA_ISSUES.md`, not
fixed here.

RE's `ProjectRegistry.PROJECT_CLASSIFICATIONS` (`registry.py:5441`) is
`("PersonalProject", "Task", "StudyProject", "Campaign")` — correct as far as
it goes, missing `Experiment`.

### 1.2 `StudyProject` gets its Egeria meaning back

The proposal's motivation for a new category is that RE has been using
"Study" to mean *investigate without commitment yet*, which is not what
Egeria's `StudyProject` means. Egeria's is "a focused analysis of a topic,
person, object or situation" — which is a good description of most RE
investigations, and is the meaning we should adopt. `StudyProject` stays the
default for that reason, and the docstring at `registry.py:5440` that
justifies the default as "the least committal" should be rewritten: the
reason to default to it is that it is *accurate*, not that it is timid.

### 1.3 `adHoc` is not a classification — it is the absence of a binding

`project_classification` is validated against an Egeria vocabulary and is
destined for `initialClassifications` on the create body. A value in that
column that Egeria has never heard of gets dropped or rejected at the
boundary, and the column's name would then be a lie about what it holds.

The proposal already contains the resolution: point 4 says every kind
*except* ad-hoc is created in or linked to Egeria. So ad-hoc is not a kind of
project at all — it is **"this investigation has no Egeria Project"**, which
the schema already models as a nullable `egeria_project_guid`
(`investigation-framing-design.md` §1 calls that "the single most important
structural decision here").

**Two columns.** `project_classification` keeps Egeria's vocabulary;
`egeria_binding` carries `local` (ad-hoc) or `egeria`.

**On the form these are two controls, not one — changed during implementation
(2026-09-07), and worth recording as a change rather than quietly building
something else.** The design above specified a single six-entry dropdown, with
"Ad-hoc" as one of its entries writing `StudyProject` + `local` underneath. That
is fewer clicks and matches how the proposal described it. It was built as two
selects instead, for one reason that only became obvious while writing the form:

> A single list **cannot express "an ad-hoc Personal investigation"**, or an
> ad-hoc Experiment. It forces every ad-hoc row to be a `StudyProject`, which
> re-imports the conflation this whole section exists to remove — one axis
> silently deciding the other.

So the form asks "what shape of work is this?" and "where does it live?"
separately, which is what the two columns already say. The cost is one extra
control; the benefit is that the ten combinations are all reachable and neither
axis has to stand in for the other.

Keeping the classification on an ad-hoc row (rather than nulling it) matches
what the schema comment at `registry.py:2046` already says about this column:
promoting later should not have to re-ask for something the user decided at
creation.

`egeria_binding` is a new column and is derived state — it is `egeria`
exactly when the row has, or is meant to have, a project GUID. It exists so
the *intent* is recorded before the promotion succeeds; a promotion that
fails halfway must not look like a deliberate ad-hoc investigation.

### 1.4 `Experiment` needs `hypothesis`

`ExperimentProperties` carries `hypothesis`, and it is what the
classification is *for*. Offering `Experiment` without collecting a
hypothesis publishes a classification whose defining property is empty —
which is the catalog-description version of the same absence problem. The
create/edit form must require it when `Experiment` is chosen, and the
publisher must send it.

---

## 2. The classification is collected and then thrown away — **fixed, Phase 1**

`project_classification` is:

- stored — `registry.py:2050` (column) and `:2068` (migration)
- offered in the UI — `web/static/index.html:10075`
- validated on create — `registry.py:5459`, which raises on an unknown value

and then `EgeriaInvestigationPublisher.promote()`
(`surveyors/egeria_investigation_publisher.py:140`) builds:

```python
res.project_guid = pm.create_project(
    body={"class": "NewElementRequestBody", "properties": properties},
)
```

with no `initialClassifications` key anywhere in the body. Every investigation
ever promoted from RE is an unclassified `Project`.

This is the same shape as the `Ownership`/purposes correction already
recorded in that file's own comments: a value collected carefully, validated
carefully, and dropped at the last hop. It is one field, it is independent of
everything else in this document, and it should land first.

**Verification, since a passing publish proves nothing here:** read the
classification back off the created Project. A test that only asserts
`create_project` was called with *a* body would have passed for the whole
period this has been broken.

**Built 2026-09-07.** `promote()` now sends
`initialClassifications: {"<Name>": {"class": "<Name>Properties"}}`, then reads
the classification back off the created Project and records the outcome in
three distinguishable states on `PromotionResult` — `classification_confirmed`
is the name when verified present, `""` when verified **absent** (an error,
though the Project stays bound because the route binds on `project_guid`), and
`None` when the read-back could not be made at all. An unrecognised
classification refuses *before* any Egeria write rather than creating a Project
without one. The promote panel renders all three states differently, since the
whole failure was that nothing on that screen said anything.

Six tests in `tests/test_investigation_routes.py`; each guard was made to fail
on purpose before being trusted — removing the `initialClassifications` line
fails the two body assertions, and stubbing the read-back to always confirm
fails the drop-detection and the could-not-verify tests.

---

## 3. Zoning

### 3.1 What is legal

`OpenMetadataTypesArchive6_0.getZoneMembershipClassification()` builds the
`ZoneMembership` classification against
`OpenMetadataType.OPEN_METADATA_ROOT` — every element, not `Asset`. So the
Project, its Folio/WorkingSet Collection, and the SurveyReports and
Annotations RE publishes can all carry zones. Nothing in the type model
blocks the proposal.

### 3.2 How enforcement actually decides — and the two ways it says "yes"

`OpenMetadataAccessSecurityConnector.validateZoneAccess()` (line 1090). The
loop over the element's zones:

```java
if (userId.equals(zoneName))
{
    return true;
}
else if (zoneName != null)
{
    List<String> associatedSecurityList = getAssociatedSecurityListForZone(zoneName, operation);

    // If the zone has no associated security list, then it is not a secured
    // zone and is ignored.  This is different from giving everyone access.
    if (associatedSecurityList != null)
    {
        securedZoneCount++;
        ... group / role / account-type / instance-based checks ...
    }
}
```

and after the loop:

```java
if (securedZoneCount > 0)
{
    return false;
}
...
// If this point is reached, the user has access.
return true;
```

Two things follow, and they are the crux of the whole design.

**(a) A zone named with a userId is a private grant.** Egeria has already
built "visible only to the creator" — it needs no configuration, because the
comparison is against the string itself.

**(b) A userId-only zone list denies nobody.** For any *other* user,
`"dwolfson"` is not their userId, `getAssociatedSecurityListForZone("dwolfson")`
returns null because no such zone is configured, the zone is ignored,
`securedZoneCount` stays 0, the loop ends, and the method returns `true`.
`zoneMembership = ["<userId>"]` is, for everyone except the owner, exactly
equivalent to no zone at all.

Denial requires **at least one zone in the list that the platform recognises
as secured**. So the correct spelling of a private element is a pair:

```
zoneMembership = ["resource-explorer-private", "<creator userId>"]
```

- the **owner** matches on entry two and short-circuits to `true`
- **everyone else** finds entry one *is* secured, fails its security list,
  reaches the end with `securedZoneCount == 1`, and gets `false`

One shared secured zone, and the per-user part is carried by the userId
string. No per-user configuration at any scale.

### 3.3 Creating a private zone — corrected 2026-09-07

**An earlier version of this section said the `associatedSecurityList` could
only come from the deployment's `.omsecrets` file, that RE could therefore
create only a "decorative" zone, and that per-user zones were infeasible. All
three were wrong.** The correction came from the project owner pointing at
pyegeria's `security_officer` module; everything below was then verified live
and read-only against the running platform.

**There is a write API, and it targets exactly the store the check reads.**

    SecurityOfficer.set_security_access_control(platform_name, body)
      -> POST /platforms/{guid}/security-access-control
      -> OpenMetadataPlatformSecurityVerifier.setSecurityAccessControl(...)
      -> userSecurityConnector.setSecurityAccessControl(...)

and `getAssociatedSecurityListForZone` reads that same connector. The body is
the shape the file uses, because the file is a *seed* for the store rather than
the store itself:

```json
{"class": "SecurityAccessControlRequestBody",
 "securityAccessControl": {
   "controlName": "resource-explorer-private",
   "controlTypeName": "GovernanceZone",
   "associatedSecurityList": {"DEFAULT": ["..."]}}}
```

**Read live, so this is not a reading of the source.** Against
`qs-view-server`, platform `Quickstart OMAG Server Platform`:

| control | what the live store holds |
|---|---|
| `egeria-runtime` | `READ: [openMetadataMember, allUsers]`, `DEFAULT: [runtimeManager, omagspcatnpa, defaultplatformnpa, lemmiestage, garygeeke, peterprofile]` |
| `security` | a long `DEFAULT` list of teams and service accounts |
| `digital-products` | **no `associatedSecurityList` at all** — unsecured |
| `resource-explorer-private` | `None` — does not exist yet, and can be created |

Two things fall out of that table beyond the main correction.

* **The live store differed from the file I had been reading — and that was
  not config drift, it was me reading the wrong file.** I read
  `compose-configs/**egeria-freshstart**/secrets/` and probed the running
  **quickstart** platform. Two different environments, so of course they
  disagreed. Retracted rather than left standing: an unexplained "the running
  config does not match the repo" would send the next reader hunting a drift
  that does not exist. The real lesson is narrower and duller — check which
  environment you are in before comparing a file to a platform. §3.3a is what
  that check produced.
* **§8's `digital-products` observation is independently confirmed** — the live
  quickstart store really does hold that zone with no `associatedSecurityList`,
  so the check ignores it. Reached from the running platform rather than from a
  file's indentation, which is the evidence the original observation lacked.

**Who may write one.** `setSecurityAccessControl` is gated by
`validateUserAsOperatorForPlatform`, which tests membership of the
`platform-services` control: `serverOperator`, `infrastructureTeam`,
`devOpsTeam`, `dataManagementTeam`, `securityTeam`, `serverAdministrator`,
`runtimeManager`, `metadataArchitect`, `platform`.

Read live **against quickstart**: `erinoverview` — the account RE already runs
as — holds four of them (`dataManagementTeam`, `metadataArchitect`,
`serverAdministrator`, `serverOperator`), as do `garygeeke`, `peterprofile` and
`lemmiestage`. **That is a fact about quickstart, not about Egeria** — see
§3.3a, which is the difference between "Phase 5 works here" and "Phase 5 ships".

**So: can we create a personal zone for a user who has none, and assign them to
it? Yes.** `set_security_access_control` with
`associatedSecurityList: {"DEFAULT": ["<userId>"]}` creates a working, enforced,
per-user zone at runtime. Naming individual users in a security list is already
the established pattern here — `lemmiestage`, `garygeeke` and `peterprofile` sit
in `egeria-runtime`'s own `DEFAULT` list.

**Which leaves a real design choice rather than a constraint.** Two workable
shapes:

1. **One shared secured zone + the userId shortcut.**
   `zoneMembership = ["resource-explorer-private", "<userId>"]`. One control
   created once; adding a user needs nothing, because `validateZoneAccess`
   returns true on `userId.equals(zoneName)` before consulting any list.
2. **A zone per user.** `set_security_access_control` per person, with the user
   named in `DEFAULT`. More faithful to how the rest of the deployment expresses
   access, inspectable through the Security Officer API, and extensible — a
   personal zone can later be shared with a named colleague, which (1) cannot
   express at all.

**Recommendation: (1) first, with (2) available.** (1) has no per-user
provisioning step and therefore no per-user failure mode, which matters because
a personal zone that silently failed to be created is precisely the fail-open
case §3.4 describes. (2) becomes worth building the first time somebody wants to
share a private investigation with one named person, and nothing in (1)
forecloses it.

**The trap in (1) is unchanged and still load-bearing:** the userId entry only
ever *grants*. Denial needs at least one zone the platform recognises as
secured, or `securedZoneCount` stays 0 and everyone falls through to `return
true`. Whichever shape is chosen, `resource-explorer-private` must exist as a
real control with a real list, and that list must not contain a broad group —
not `openMetadataMember` (effectively everyone), and not `instanceOwnersGroup`,
because `isUserAnOwner` returns **true when an element has no `Ownership`
classification at all**.

### 3.3a Quickstart is not freshstart — and Phase 5 must be designed for the empty one

Raised by the project owner, and it is the difference between a feature that
works on this machine and one that ships. **Everything measured in §3.3 was read
from the running quickstart platform** (`docker ps` confirms
`quickstart-egeria-main`), which preloads the Coco Pharmaceuticals directory.
Freshstart preloads almost none of it.

Counting the two seed directories:

| | quickstart (`coco-user-directory`) | freshstart (`egeria-user-directory`) |
|---|---:|---:|
| user accounts | 318 | 73 |
| security access controls | 36 | 11 |
| **GovernanceZone controls** | **23** | **3** (`egeria-runtime`, `digital-products`, `security`) |
| `erinoverview` present | yes | **no** |
| humans holding `serverOperator` | several | **none** |

So under freshstart:

* **RE's default account does not exist.** `erinoverview` is a Coco Pharma demo
  persona. Freshstart creates its users at runtime through its own Egeria-backed
  admin (`/api/admin/egeria-users` — see
  `egeria-workspaces-fs/compose-configs/ENVIRONMENT_DIVERGENCE.md`), and none of
  them is granted `serverOperator` by default.
* **The zone RE wants does not exist and neither do 20 others.** Whatever Phase 5
  needs, it has to create.
* **There is an operator identity, but it is a service account.** The runtime
  volume's directory defines a `serverOperator` SecurityRole whose members are
  `platform` and `rover` (a `DIGITAL` account) — not a human, and not the account
  RE is configured with. (The runtime volume and the compose-config seed have
  themselves diverged: the seed's `bootstrap` has no `securityGroups` at all,
  the runtime one has ten. Read the runtime volume when asking what is true of a
  running freshstart.)

**And there is no permissive fallback.** `OpenMetadataSecurityConnector`'s base
`validateUserAsOperatorForPlatform` does nothing but
`throwUnauthorizedPlatformAccess`. The only way through is real membership. So on
a stock freshstart, `set_security_access_control` fails for every human account —
which is correct behaviour, and is a genuine bootstrap ordering problem, because
operator rights are themselves granted *through* the same store.

#### What this means for Phase 5

1. **Zone provisioning is a deployment concern with an RE fallback, not an RE
   feature.** RE should attempt to create `resource-explorer-private` once
   (leader-elected, like `ensure_draft_zone_exists`), and when it is not an
   operator, say so precisely — naming the control it wanted, the groups that
   would authorise it, and that an operator must run the one-line grant — rather
   than failing quietly or crashing.
2. **"Could not create the zone" must disable private investigations, loudly.**
   This is the fail-open case from §3.4 wearing its most dangerous costume: if
   the control does not exist, `zoneMembership = ["resource-explorer-private",
   "<userId>"]` is an *unrecognised* zone, therefore ignored, therefore visible
   to everyone — while RE's own UI says private, because Phase 3's filter is
   local and works regardless. The two halves must not be able to disagree
   silently: if the zone is not confirmed present, private investigations must
   either refuse to publish or be labelled as RE-only-private.
3. **Freshstart needs a documented setup step**, and it belongs in
   `egeria-workspaces-fs` alongside the rest of the freshstart configuration:
   grant RE's service account `serverOperator` (or add it to `platform-services`'
   list), after which RE provisions its own zone. That is one line of deployment
   config — much less than the cross-repo change the pre-correction §3.3 claimed,
   but not nothing, and it is the reason this section exists.
4. **Test on freshstart before calling Phase 5 done.** Quickstart's 23 preloaded
   zones and 318 users make almost anything work. The environment that proves the
   feature is the empty one.

### 3.4 Fail-open, and why anchoring is the answer

`validateUserForAnchorMemberRead` (line 1403) only evaluates zones when the
requested entity actually carries the classification:

```java
if (repositoryHelper.getClassificationProperties(serviceName,
                                                 requestedEntity.getClassifications(),
                                                 ZONE_MEMBERSHIP_CLASSIFICATION.typeName,
                                                 methodName) != null)
```

An artifact that misses the stamp is not "denied by default" — it is not
checked. Combined with §3.2's "an unrecognised zone is ignored", the failure
mode in both directions is *visible*.

A design that depends on stamping every artifact correctly is therefore a
design with N chances to silently leak, where N grows with every new
annotation type. Do not build that.

**Use the anchor instead.** `OpenMetadataAPIAnchorHandler` (around line 152)
copies `ZoneMembership` onto the `Anchors` classification, with the comment
*"If the ZoneMembership classification is set then add it to the anchor. This
allows for efficient security evaluation."* So if RE anchors the artifacts it
produces to the investigation's Project, the zone travels with the anchor and
the invariant collapses from *"is every artifact stamped?"* to *"is every
artifact anchored?"* — one property, checkable in one query.

**This must be verified live before it is relied on**, against a second
userId, on a real SurveyReport. The check to run is not "does the owner see
it" — that passes under every broken variant. It is **"does a second user
fail to see it"**, and it has to be seen to fail. A zoning scheme that has
never been observed to deny anybody is not known to work.

**Done 2026-09-07, and it did not pass first time.** The first denial test
reported all three non-owners still READING a correctly-zoned element, with the
control present in the store. That looked like the design being wrong; it was
the connector not having reloaded yet (§6, Phase 5). Worth keeping in view as
the general shape: the first run of a security check that *fails* is the one
worth trusting, because it is the only kind of result the broken variants cannot
produce. Anchoring is still the right structure for the same reason it always
was — one invariant instead of N — but it is Phase 4 and is not yet built, so
today RE stamps the elements it publishes directly.

### 3.5 Public means *in the publish zone*, not *unzoned*

Proposal point 6 asks that Task / Campaign / Study be publicly visible. Do
not implement that as "leave it unzoned". Unzoned means *not evaluated*,
which today happens to render as public and is a different fact — it diverges
the moment the connector or the deployment config tightens.

Put them explicitly in `egeria_identity.publish_zones()`
(`DEFAULT_PUBLISH_ZONES = ("egeria-runtime",)`), which is the machinery RE
already uses for curate-accept. "Follow the visibility rules of an existing
project" (also point 6) then means: when binding to an existing Egeria
Project, read that Project's `ZoneMembership` and use it, rather than
asserting our default over it.

### 3.6 What an investigation may zone — and what it must not

`investigation-framing-design.md` already establishes that membership is
many-to-many: "the same repo is simultaneously something you are evaluating,
something a compliance sweep is checking, and something someone else's
Campaign owns."

So the rule is:

> **An investigation zones what it produced, never what it references.**

In scope: the Project, its Folio/WorkingSet Collection, and the
SurveyReports/Annotations produced under it.

Out of scope: the repo/database/filesystem Assets themselves. Adding a public
repo to a personal investigation must not hide that repo from everyone else —
a privacy feature that removes other people's access is a data-loss feature.

This is a constraint on the promotion and zoning code, and it deserves a test
that adds an already-public asset to a `PersonalProject` investigation and
asserts the asset's zones are unchanged.

---

## 4. RE's own visibility — the half Egeria cannot enforce — **built, Phase 3**

Egeria zoning hides nothing inside RE. RE reads its own registry, and the
`investigations` table today has **no creator column at all** (DDL at
`registry.py:2030–2056`). `list_investigations()` returns every investigation
to every caller.

So proposal point 5 has no local anchor yet, and this is probably the larger
practical gap: most people looking at a personal investigation will be
looking at it in RE's UI, not in Egeria.

Needed:

- `created_by` on `investigations`, populated from `current_user_id()` — the
  same mechanism `resource_working_set` already uses for per-user view
  preferences (`registry.py:5380`), including its `SHARED_USER_ID` fallback
  for rows written before scoping existed.
- `list_investigations()` filters out `PersonalProject` / `Experiment`
  investigations not created by the caller. Note the fallback rule already
  established for the working set: a pre-existing row with no creator should
  not vanish from the person who made it.
- The same filter on the findings/dashboard reads that join through an
  investigation.

Backfill is a real decision, not a detail: existing investigations have no
creator, and defaulting them to "private to nobody" hides them from everyone
while defaulting them to public exposes anything that should have been
personal. Since none were created under a personal/private expectation (the
feature did not exist), **backfill to shared** and say so in the migration
comment.

**Built 2026-09-07 — with one correction to the paragraph above.** "None were
created under a private expectation" was wrong: `PersonalProject` was already
selectable, so somebody could have made one meaning "mine". The conclusion
survives anyway, for a better reason than the one given — those rows have
*always* been visible to everyone, so leaving them visible changes nothing,
whereas hiding them would be a new loss. They now carry a `visibility_note`
saying exactly that, so the gap is actionable instead of merely accepted. See
the Phase 3 entry in §6 for what shipped.

---

## 5. Reclassification (proposal point 7) — **built, Phase 6**

Changing an investigation's classification is **a flow with a report, not an
UPDATE**. It is up to three Egeria operations, each of which can fail
independently:

1. remove the old classification (`validateUserForElementDeclassify`)
2. add the new one (`validateUserForElementClassify`)
3. change `ZoneMembership` — which routes through
   `validateUserForElementClassify`'s ZoneMembership branch (line 1798) and
   requires `AccessOperation.PUBLISH` **on the original zones**, throwing
   `throwUnauthorizedZoneChange` otherwise

Plus the constraint RE already documented in `egeria_identity.current_zones`'
docstring: the connector rejects a zone change whose before and after are
equal.

Two directions, asymmetric risk:

- **Loosening** (Personal → Task): artifacts become visible. If a step fails,
  something stays private that should be public. Annoying, safe, visible to
  the user who asked.
- **Tightening** (Task → Personal): artifacts are *already public* and every
  one must be re-zoned. **Anything the sweep misses stays readable while the
  UI says private.** This is the dangerous direction and it is the one that
  must be built defensively: enumerate the targets first, re-zone, then
  **re-read and verify**, and report any element that could not be moved as a
  loud, per-element failure rather than a summary count.

Model the return on `PromotionResult` (`egeria_investigation_publisher.py:53`)
— it already gets this right: `members_linked`, `members_unlinkable`,
`errors`, and an `ok` that is false if anything went wrong. A tightening that
partially fails must report `ok = False` and name what is still public.

§3.4's anchoring makes this dramatically simpler: re-zone the Project, and
anchored artifacts follow. That is another reason to do anchoring first.

**It did, and the prediction held.** Anchoring landed as Phase 4 and the sweep
here is over Projects and SurveyReports only — a bounded, enumerable set —
rather than over every annotation ever written. Confirmed live: tightening
re-zoned one Project and the Folio followed without being touched.

**One thing §5 did not anticipate: the tightening direction can be permanently
unavailable.** Moving an element OUT of a zone needs `PUBLISH` on its *original*
zones, and `egeria-runtime`'s security list excludes RE's account. So an
investigation whose artifacts reached the publish zones cannot be made private by
RE at all — not "needs a careful sweep", but "cannot". Loosening is unaffected,
because the `userId` entry in the private zone satisfies the same check. The
practical consequence is that **privacy is a decision best made before
promotion**, and the reclassifier says so rather than reporting a bare
permissions error.

---

## 6. Plan

Ordered smallest-blast-radius first. Each step is independently useful and
independently shippable.

**Phase 1 — publish what we already collect. ✅ Done 2026-09-07.** Added
`initialClassifications` to `EgeriaInvestigationPublisher.promote()`'s create
body, with read-back verification and a tri-state result. See §2. *(No schema
change, no security implications; one addition to the promote panel so the
outcome is visible.)*

**Live-checked 2026-09-07, and the check found a real defect in itself.** All
five classifications — Campaign, Task, PersonalProject, StudyProject and
**Experiment** — were created against `qs-view-server` and read back
CONFIRMED, then deleted; a follow-up search by a different route showed 50
Projects before and 50 after, zero leftovers. Egeria accepts
`initialClassifications` exactly as sent.

But the read-back parsing was **wrong**, in the direction that hides the bug.
`_confirm_classification` had guessed a top-level `classifications` list.
The real payload from `get_project_by_guid(..., output_format="JSON")` is an
`OpenMetadataRootElement` whose project classifications live under
**`elementHeader.projectKinds`**, grouped apart from `elementHeader.anchor`
(which carries `Anchors`) because they are all `ProjectKind` subtypes:

```
{"class": "OpenMetadataRootElement",
 "elementHeader": {"guid": ..., "type": {...}, "anchor": {...},
                   "projectKinds": [{"class": "ElementClassification",
                                     "classificationName": "Task",
                                     "type": {"typeName": "Task",
                                              "superTypeNames": ["ProjectKind"]}}]},
 "properties": {...}, "resourceList": [...], "mermaidGraph": ...}
```

Against that shape the original function found nothing, and returned `None` —
"could not tell" — on **every** call. The safe direction, as predicted, and
therefore invisible: the five tests passed, because the stub had invented the
same wrong shape the code assumed. Two artefacts agreeing with each other and
neither agreeing with Egeria.

One further measured detail, now load-bearing: **`projectKinds` is omitted, not
null, when a Project has no kind** — 36 of 50 live Projects carry the key and
14 lack it entirely. So an absent key cannot by itself be read as "not
classified", since a changed payload shape looks identical. `elementHeader.guid`
is the sentinel that separates them, and `_StrangePayloadPM` in the tests pins
the distinction.

**Phase 2 — vocabulary. ✅ Done 2026-09-07.** `Experiment` added to
`PROJECT_CLASSIFICATIONS` with `HYPOTHESIS_REQUIRED_FOR`; `hypothesis` is
required for it and **refused for everything else** (a hypothesis on a `Task`
would be stored and then silently discarded at publish time, which is worse
than being told). `egeria_binding` column added with `BINDING_LOCAL` /
`BINDING_EGERIA`, ad-hoc split onto it. The `StudyProject` docstring now says
*accurate* rather than *least committal*.

**Live-checked:** the `hypothesis` attribute round-trips inside
`ExperimentProperties` — sent, read back identical, deleted, 50 Projects before
and after. So the classification and its defining attribute are both confirmed
end to end, not just the classification.

Three decisions worth finding later:

* **Promoting an ad-hoc investigation is refused, not silently allowed.**
  `local` is a decision, not a not-yet; promoting anyway would overturn it and
  leave the row bound while still flagged local.
* **Binding a GUID moves the row off `local`, but unbinding does NOT move it
  back.** Asking for a Project is a choice; losing or clearing a binding is not
  the opposite choice, and recording it as one would invent an intent.
* **Existing rows backfill to `egeria`, not `local`.** Before the column
  existed, ad-hoc was not a choice anyone could make — every investigation was
  headed for Egeria whether or not it had arrived. `local` would invent a
  deliberate decision nobody took. A NULL binding therefore reads as `egeria`
  everywhere, and a test pins that specific direction.

**The vocabulary is now served, not hardcoded in the SPA** —
`GET /api/investigations/classifications` returns the five classifications with
Egeria's own definitions as their descriptions, a `requires_hypothesis` flag per
entry, and the two bindings. The dropdown, the descriptions and the
show-the-hypothesis-field rule are all built from it. This follows `/purposes`,
which exists for exactly this reason: a copy in the frontend is the mirror that
drifts, and here drift means offering a classification Egeria will reject or
hiding one it accepts.

Ten tests, each guard made to fail on purpose.

**Phase 3 — local visibility. ✅ Done 2026-09-07.** `created_by` on
`investigations`, stamped from `current_user_id()` in the registry (never taken
from a caller); `PRIVATE_CLASSIFICATIONS = ("PersonalProject", "Experiment")`;
one `_may_see_investigation` predicate that every read goes through.

**Scoping went into `get_investigation()` rather than the routes.** All ~15
investigation routes already funnel through it, so they inherit the filter with
no change of their own — and `current_user_id`'s own docstring gives the reason:
"a method that resolves it cannot be called without scoping; a route that has to
remember can."

**A private investigation returns 404, not 403** — deliberately the opposite of
this codebase's usual rule that absence must be distinguishable from emptiness.
A 403 confirms existence and leaks the name to anyone who guesses a slug, and
slugs are derived from display names.

**Two back-reference leaks the object-level filter alone would have missed**,
both found by asking where else an investigation is *named*:

* `find_entity_investigations()` is entity-centric — "which investigations is
  this repo in" — and runs on a page anyone can open. Unfiltered, a shared repo
  would announce every private investigation containing it.
* `inherited_egeria_project_context()` returns `_inherited_from_name` (the
  investigation's display name) and the Project's qualifiedName, again onto a
  resource page. It would also let one user publish a shared repo into another
  user's private Project. Its `_ambiguous` flag now counts only what the caller
  can see, since an invisible investigation cannot make their view ambiguous.

**Two deliberate holes, both documented in the predicate rather than left to be
discovered:**

* **The shared/service identity sees everything.** The worker legitimately acts
  on a user's behalf without carrying their token (`egeria_identity`'s module
  docstring), so a queued promotion of a personal investigation must still find
  the row. Under `TRELLIS_ANONYMOUS_READ=true` — a dev-box override, not a
  supported mode — an anonymous caller is that identity too.
* **Ownerless rows stay visible.** Rows written before `created_by` existed have
  no owner and nothing can recover one; they have always been visible to
  everyone, so leaving them visible changes nothing, while hiding them would
  make somebody's own work vanish with no way back. They carry a
  `visibility_note` so the contradiction is surfaced rather than tolerated.

**This is visibility, not enforcement, and the code says so.** Nothing here
touches Egeria: an artifact already published from a private investigation stays
readable there regardless. That is Phase 5. Until it lands, "private" means "RE
does not show it to other people", no more — and a filter described as more than
it is would be the "checks weaker than they look" failure with a privacy label
on it.

The SPA renders a served `visibility` / `is_mine` / `visibility_note` rather than
recomputing the rule, which spans two columns and three conditions and would
otherwise get a second copy in the one place with no test around it.

Eight tests. The load-bearing ones assert **denial** — a second user failing to
see something — because "the owner can see their own" passes even when the
filter does nothing at all. Each guard was made to fail on purpose, including
dropping each back-reference filter individually while leaving the object-level
one intact.

**Phase 4 — anchoring. ✅ Done 2026-09-08, and it found two live bugs in
Phase 5.** Annotations are now created with `isOwnAnchor: False` +
`anchorGUID: <report>`, so they inherit the report's zones instead of carrying
none.

**Inheritance measured, not inferred.** A child with no zones of its own,
anchored to a privately-zoned parent, was DENIED to a non-owner and READ by the
owner. `validateUserForAnchorMemberRead`'s `else` branch — the one an earlier
draft of this document never read — evaluates `anchorEntity.getClassifications()`
when the requested element carries none itself.

**§7's open question is answered, and favourably.** Moving a parent from the
draft zone to private flipped its untouched anchored child from READ to DENIED
immediately, while the child's own `Anchors` classification still held the
**stale** copy `['resource-explorer-draft']`. So enforcement reads the LIVE
anchor and the cached copy is not authoritative: **re-zoning a parent protects
every anchored child with no sweep.** That removes most of the difficulty §5
attributes to the tightening direction of reclassification.

**Two bugs in Phase 5 that this exposed, both live for a few hours:**

*Annotations were public.* Measured against real published data: every
annotation came back `anchorGUID: None` — its own anchor — with **no
ZoneMembership at all**. `_stamp_governance` only ever stamped the asset and the
report. So a private investigation's SurveyReport was protected while the
annotations carrying its actual findings were world-readable. `parentGUID` does
not anchor; `anchorGUID` does.

*The shared repo asset was being zoned private* — the inverse failure, and the
worse one. `_stamp_governance(asset_guid, report_guid)` applied one zone list to
both, and the asset is `SourceControlLibrary::<github_url>`, **one per repo,
shared by every investigation referencing it**. Publishing a private survey would
have hidden a public repository from everyone else in the catalog. That is §3.6
("an investigation zones what it produced, never what it references") violated by
the code written to implement §3.6. The two are now stamped separately: the
report takes the private zones and the investigation owner, the asset keeps the
draft zone and the publishing identity.

Both were found by asking what the *other* elements in a publish look like —
neither would have been caught by testing the artifact the feature is about.

**And a third, from asking the same question of the investigation itself.** The
investigation's own Egeria `Project` was never zoned at all, and neither was its
Folio. Phase 5 protected what a private investigation *produces* and left the
thing that *names* it — display name, description, purposes, membership —
readable by everyone. The owner's point 5 is "the project **and** all related
artifacts"; only the second half had been built. `promote()` now zones the
Project (refusing loudly, as a publish does, when the zone is not enforced) and
the Folio is **anchored** to it rather than separately stamped, so it inherits
and keeps inheriting when the Project is re-zoned.

Three of the four defects in this phase were elements nobody had thought to look
at, found by enumerating what a publish actually creates rather than by testing
the feature's headline artifact. That is the general lesson worth carrying: for a
protection feature, the question is not "is the thing I protected protected" but
"what else did this operation create".

**Phase 5 — zoning. ✅ Done 2026-09-07, and the live test earned its keep.**
`ensure_private_zone_exists()` creates the control, reads it back, and gates
private publishing on `private_zone_is_enforced()`. `private_owner_for_entity()`
resolves whose an artifact is. `EgeriaPublisher.publish()` zones private
artifacts as `[private_zone(), owner]`, stamps `Ownership` to the investigation
owner rather than the publishing account, and **refuses outright** when the zone
is not confirmed. `promote_to_publish_zones()` skips private elements, so
accepting a finding is not an un-labelled publish-to-everyone button.

**Verified live, three non-owners denied:**

```
owner  erinoverview     -> READ
other  calliequartile   -> DENIED(PyegeriaUnauthorizedException)
other  tanyatidie       -> DENIED(PyegeriaUnauthorizedException)
other  faithbroker      -> DENIED(PyegeriaUnauthorizedException)
```

**Two defects the live test found that no amount of reading would have.**

*The store is not the connector.* The control read back from the secrets store
**immediately** — and a privately-zoned element was still readable by a
non-owner **six minutes later**, with denial beginning at seven. The security
connector reloads on `refreshTimeInterval` (10 minutes here, and
`SecretsStoreConnector` multiplies it by `60 * 1000`). So "I wrote it and read
it back" is exactly the evidence that looks conclusive and is not, and the first
version of this code reported `enforced: True` on the strength of it. A publish
in that window lands in a zone nothing is enforcing. `private_zone_status()` now
carries a `settling` state with the remaining seconds, and a freshly created
control is not trusted for `PRIVATE_ZONE_SETTLE_SECONDS` (default 720 —
deliberately longer than the interval, because being late costs minutes and
being early cannot be undone).

*A slug-normalisation leak.* `private_owner_for_entity` first called
`_normalize_slug`, which turns `egeria-python` into `egeria_python`, while
`working_set_members` stores the slug verbatim — as the two neighbouring reads
already assumed. It therefore matched nothing for any repo with a hyphen in its
name, which is most of them. Silent, and it **failed open**: no owner found means
"not private", so those artifacts would have published into the public zones.
Caught by a test written with a realistic slug, not by review.

**A third defect, in the second publish path.** `arch_recovery/materializer.py`
stamps zones independently of the publisher — a component materialised from a
private repo would have been born in the draft zone, owned by whoever ran the
analysis. It now asks the same question; but the first version of that guard sat
next to its `stamp_published` call, **after the SolutionComponent had already
been created**, so refusing there left a real unzoned element behind and reported
"skipped". Found because a sabotage run created one in the live catalog. The
check now runs before `_connect()`, and the test stubs `_connect` to raise, so
reaching Egeria at all fails the test.

**A fourth, found by nine unrelated tests going red at once.** The owner value
becomes a **zone name**, and both call sites took whatever the registry returned.
The materializer tests use a `MagicMock` registry, whose
`private_owner_for_entity` returns a truthy Mock — so every materialize looked
private and was refused. A mock artefact, but the gap underneath is real:
`validateZoneAccess` compares the zone name to the caller's userId with
`.equals`, so a non-string owner would match **nobody at all** while still
marking the element private — unreadable by everyone including its owner, with
no error anywhere. Both sites now require a non-empty `str`.

That this took four tries across three files is the argument for Phase 4:
anchoring turns "did every publish path remember, and remember correctly?" into
one property. It is the next thing to build.

Nineteen tests, every guard made to fail on purpose.

The mechanics, retained: create `resource-explorer-private` through
`SecurityOfficer.set_security_access_control`, in the same one-shot
leader-elected shape `ensure_draft_zone_exists` already uses — and, unlike that
one, **verify the control reads back**, because a zone element without a control
is the decorative case. Then zone Personal/Experiment investigations as
`["resource-explorer-private", "<creator userId>"]` and Task/Campaign/Study into
`publish_zones()`.

*No cross-repo change is needed on quickstart, where RE's account is already a
platform operator — that was the correction in §3.3. On **freshstart** it needs
one line of deployment config to grant RE's account `serverOperator`, and until
that lands the zone cannot be created at all: see §3.3a, including why "could not
create the zone" has to disable private publishing loudly rather than proceed.*

**Gate on the live two-user denial test** in §3.4. This is not a formality: the
whole design turns on `validateZoneAccess` returning *false* for somebody else,
and a zoning scheme never observed to deny anyone is not known to work — the
same trap as Phase 1's read-back, which returned "could not tell" on every call
while passing all five of its tests. Non-operator accounts exist for exactly
this: `calliequartile`, `tanyatidie` and `faithbroker` are all real users with
no platform-services group. Publish a SurveyReport from a private investigation
as one user, then read it as one of them, and require the read to FAIL before
calling the phase done.

Also decide, before writing the code, whether "private" follows the
classification or a separate flag — §7's open question. Phase 5 is where that
becomes load-bearing, because it decides what Phase 6 has to move.

**And run it on freshstart, not only quickstart.** Quickstart's 23 preloaded
zones and 318 users make almost anything work; the environment that proves the
feature is the empty one.

**Phase 6 — reclassification. ✅ Done 2026-09-08.**
`surveyors/investigation_reclassifier.py` — a flow with a per-element report, as
§5 required.

**Phase 4 shrank this a lot.** The sweep is over Projects and SurveyReports
only, because Folios and Annotations are anchored and enforcement reads the live
anchor. Verified live: tightening re-zoned one Project and the Folio followed
without being touched.

**Live, both directions, on a real promoted investigation:**

```
Task -> PersonalProject   other: READ   -> DENIED   (project and folio)
PersonalProject -> Task   other: DENIED -> READ     (project and folio)
```

**Order is Egeria first, local last, and that is the safety property.** On a
tightening, recording the classification before Egeria holds it would make every
RE screen say "private" over artifacts still being served to everyone — Phase 3's
filter is local and does not consult Egeria. So a tightening that cannot move
every element leaves the classification alone and says which elements are still
public. Verified live: with the Project unmovable, `local_applied` stayed False,
the investigation stayed `Task`, and `still_public` named the Project.

**A real product limit, found by trying it.** Once an element is in
`egeria-runtime`, RE cannot move it out: `PUBLISH` is checked against the
*original* zones and that zone's security list excludes RE's account
(`OPEN-METADATA-SECURITY-403-005`). **So an investigation whose artifacts have
been promoted to the publish zones cannot be made private again by RE.** The
reverse works — RE can move out of its own private zone, because the `userId`
entry satisfies the PUBLISH check. Loosening is therefore always available;
tightening is available only before promotion. The flow reports this precisely
rather than as "Egeria said no".

**Two defects the live run found that the stubs did not:**

*`still_public` cried wolf.* It named "the investigation Project" for a
**local-only** investigation, which has nothing in Egeria at all. A false alarm
rather than a leak — but a field that exists to be read when non-empty is worth
nothing if it is sometimes wrong, and this would have taught people to ignore it.

*The zone check refused in a fresh process.* `private_zone_is_enforced()` is
per-process state filled by the worker bootstrap, so a web or CLI process that
never ran it refused every tightening while the zone was healthy — a
self-inflicted outage that reads exactly like the real failure. The same lazy
"unknown is a question, not an answer" check the publisher already had. Second
site, same bug.

*And a third, in my own new code, prompted by a test that did not even flag it.*
The no-silent-success ratchet failed on somebody else's change, which sent me
looking at my own equivalent: `_report_guids` logged and continued when a
member's published surveys could not be listed. That is "we could not look"
rendered as "there was nothing" — a tightening would move what it found, report
success, apply the classification, and leave that member's reports **public**
while RE showed the investigation as private. The precise failure this feature
exists to prevent, reproduced inside the code meant to prevent it. Unreadable
members are now reported and block a tightening; a loosening still proceeds,
because there the failure direction is safe.

**Completed 2026-09-08 with the Egeria metadata half.** The first version moved
zones and the local row but never changed the Egeria Project's own kind
classification — §5 lists declassify/classify as steps 1 and 2, and only the
zones had been built. So an investigation promoted as `Task` and reclassified to
`PersonalProject` had correct visibility and a Project still claiming to be a
Task. `_move_kind_classification` now swaps it, after the zones, and a failure
there is reported as a **metadata inconsistency rather than an exposure** —
visibility went first and is correct.

**The `Investigation` marker is safe by construction.** The project owner added
an Egeria classification called `Investigation` (2026-09-08) as an orthogonal
marker that coexists with the kind and does not drive zones. The swap removes
the **old kind by name**, gated on `PROJECT_CLASSIFICATIONS`, so the marker —
along with `Anchors`, `Ownership` and `ZoneMembership` — survives. Verified
live: `['Task']` -> `['PersonalProject']` with `anchor`, `ownership` and
`zoneMembership` all still present afterwards.

**Two bugs the live run found here too:**

*The read-back was asking the wrong client.*
`MetadataExpert.get_metadata_element_by_guid` returns the raw element shape;
`_confirm_classification` was measured against
`ProjectManager.get_project_by_guid`'s `elementHeader.projectKinds`. Reading with
the wrong one made the check return "could not tell" on **every** call —
correctly refusing to guess, and checking nothing. The third time in this feature
that a verification step has been the thing that was broken.

*The old classification was never removed*, so the Project carried BOTH kinds
and a check that only asked "is the new one present?" passed.
`declassify_metadata_element` raises when its Optional `body` is omitted
(pyegeria, logged as ISSUE-93 rather than patched); the body is now passed, and
the swap additionally asserts the OLD kind is gone rather than only that the new
one arrived.

Twenty-one tests, every guard made to fail on purpose — including three that had
to be strengthened first: the lateral-change test started in `egeria-runtime`,
which *is* `publish_zones()`, so a wrongly-applied re-zone would have been a
no-op and invisible; the marker-safety test read the source and passed a sabotage
run that stripped every classification; and the fakes accepted a `None` body,
which made them more forgiving than the system they stand for, so removing the
ISSUE-93 workaround passed. All three now model what was measured.

---

## 7. Open questions

- **Does `Anchors.zoneMembership` propagate to *newly created* children, or
  only to elements anchored at creation time?** The handler copies it when
  deriving anchor identifiers; whether a SurveyReport published after a later
  zone change picks up the new value, or the old one cached on the anchor,
  decides whether §5's tightening sweep can rely on it. **Measure before
  designing around it.**
- **What is RE's userId, really?** §3.2's private grant compares against the
  string in `zoneMembership`. RE publishes queued work *as the service
  account with `Ownership` set to `requested_by`*
  (`egeria_identity.py` module docstring — deliberate, and documented as
  interim). If the zone carries `requested_by` but the write runs as the
  service account, the service account must also be able to read back what it
  wrote. Probably fine (it is doing the writing, and `PUBLISH` is checked
  against the original zones) but it is exactly the kind of thing that works
  in the owner's session and fails in the worker.
- **`ProjectClassification`** — "the expectation of how the results will be
  used" — may be a better home for the public/private *intent* than inferring
  it from the project-type classification. Worth a look before Phase 5
  hardcodes the mapping "PersonalProject/Experiment ⇒ private".
- **Should `Experiment` be private by default?** The proposal groups it with
  `PersonalProject`. Egeria's definition ("testing a hypothesis") is about
  method, not confidentiality — a team experiment is a normal thing. Suggest
  private *by default*, changeable at creation, rather than private *by
  classification*.
- **Deployment config ownership.** Phase 5 needs a line in
  `egeria-workspaces-fs`'s `.omsecrets`. That is a different repo with a
  different change policy; confirm who lands it and whether it belongs in the
  freshstart config, the quickstart config, or both.

---

## 7a. After the 2026-09-08 redeploy — what changed, and two fixes it forced

The owner redeployed to pick up the latest Egeria, **without resetting the
metadata store**. Measured afterwards rather than assumed:

* **`Investigation` and `NamingStandardsVocabulary` now exist** (both were "not
  found" before). The marker is unblocked and the four Dr.Egeria Curation
  templates have types to target.
* **The private zone survived and still enforces** — present in the seed, the
  runtime volume and the container, and a live check confirmed owner READ /
  non-owner DENIED. Adding it to both compose seeds before the redeploy is what
  made that unconditional rather than lucky.

**Fix 1: platform discovery started refusing on a working deployment.** The
redeploy catalogued a second platform, so `_platform_name()` saw
`['Quickstart OMAG Server Platform', 'Local OMAG Server Platform']` and declined
to guess — which returned `enforced: False` and would have disabled private
publishing on a zone that was demonstrably denying. The refusal was the designed
safe direction and it was still the wrong answer. It now asks **which platform
already holds the control**, which is a fact rather than a guess, and refuses
only when none does (a create, where there genuinely is no evidence).

**Fix 2: the `Investigation` marker is applied after the create, not in it.**
Owner's decision, 2026-09-08: it is an orthogonal marker meaning "this Project is
an investigation", coexisting with the kind and not driving zones. RE now stamps
it on every Project it promotes.

It is a **separate classify call on purpose**. In `initialClassifications`, a
classification the platform lacks fails the whole create — and this type did not
exist until this redeploy, and will not exist on any older deployment. As a
follow-on step a missing type costs the marker and not the investigation, and a
failure is logged rather than raised: nothing in RE reads the marker, the kind is
what drives behaviour.

Verified live, including that it survives a change of kind:

```
after promote  -> projectKinds: ['Task', 'Investigation']
after reclass  -> projectKinds: ['Investigation', 'PersonalProject']
```

which holds structurally because the reclassifier removes the old kind **by
name**, gated on `PROJECT_CLASSIFICATIONS`, and the marker is not in that set.

---

## 8. Observation for `egeria-workspaces-fs` (not changed here)

The `digital-products` zone's `READ:` and `DEFAULT:` keys sit at the same indent
as `otherProperties` rather than nested under an `associatedSecurityList:` key,
as they correctly are for `egeria-runtime` and `security` immediately above and
below it. As written, that zone has no `associatedSecurityList` at all, so
`getAssociatedSecurityListForZone` returns null and the zone is **ignored** —
unsecured.

**In both environments**, checked after §3.3a made the distinction matter:
`compose-configs/egeria-freshstart/secrets/egeria-user-directory.omsecrets` and
`compose-configs/egeria-quickstart/secrets/coco-user-directory.omsecrets` carry
the identical shape. And it is not merely a reading of the files — the live
quickstart store returns `digital-products` with no `associatedSecurityList` key
at all, so the indentation really does reach the connector this way.

Its own `description` says "This is an unsecured governance zone", so the
outcome may well be intended; but the presence of the two keys suggests someone
meant them to bind, and the config reads as if they do. Flagged rather than
changed, since the correct resolution depends on which of the two was meant —
and now with live evidence of the effect rather than an inference from
whitespace.
