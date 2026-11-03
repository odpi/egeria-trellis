# DESIGN — File systems RE reaches that Egeria cannot: local-only and remote (2026-10-21)

From the design session, after two facts met the Files first slice. The
owner's words, given to the coordinator on 2026-10-20 and 2026-10-21: RE
can reach file systems Egeria cannot ("on my machine RE sees all my files
while Egeria sees only what's mounted in its container"; "we may need to
do more within RE, not sure if that is urgent, it may be a backlog
item"), and "there is a further twist: the file systems could be remote
from RE, and we login". The positions below are the design session's,
for the owner to confirm; nothing here reopens Files step 1.

## 1. The measured fact

`docker inspect` on quickstart-egeria-main: the container mounts only
egeria-workspaces subfolders at `/deployments/*` (the exchange area's
data lake, landing area, loading bay, distribution hub and treasury
history; `work`; the platform's runtime volumes). Host paths such as
`/Users/dwolfson/localGit/egeria-v6` are invisible to it, and the paths
that are visible differ from their host paths. A root folder whose
`pathName` is a host path never resolves inside Egeria.

## 2. Three outcomes of one probe

The reachability probe (built for Classic as `resource_reachability`,
PI-093) runs at registration and before every survey, and records one
outcome with a date on the file system:

| Outcome | Sentence on the resource | Who leads the survey |
|---|---|---|
| 1 | "Egeria can read `<container path>` · checked `<date>`" | Egeria's Folder Survey at the canonical mount; RE reads it back (Files brief §1a) |
| 2 | "only Resource Explorer can read this path · checked `<date>`" | RE's walk, publishing the same folder-survey annotation types under its own SurveyReport |
| 3 | "reachable by Resource Explorer over `<protocol>` from `<host>` as `<credential name>` · checked `<date>`" | RE's walk over the protocol, same publish as outcome 2 |

The lever is chosen and named; nothing reads as "the Egeria lever
failed". Understanding shows the three the same way, each report carrying
"surveyed by Egeria's Folder Survey" or "surveyed by Resource Explorer ·
Egeria could not reach the path" or "… · over SFTP from `<host>` · Egeria
has no reach to this host".

**Mapping (outcome 1), corrected 2026-10-21 from Egeria's source.** The
canonical mount step 1 records is the path as Egeria's container sees
it. There is no cheap stat: the only engine-host call that tests a path
is a full Folder Survey launch (`FolderSurveyService` ignores
`CHECK_ASSET`), so a probe must never launch a survey. Verification is in
two honest parts: at attach-root, a mounted-prefix check against a small
deployment-level prefix map (host prefix to container prefix), recorded
as "expected reachable by Egeria · under `/deployments/…` · unverified";
then the first Egeria-led
survey is the verification, a completed Folder Survey writing "Egeria can
read … · verified `<date>`", and `BASIC-FILE-CONNECTOR-404-001` writing
"Egeria cannot read … · `<date>`" with the lever flipping to RE-led for
the next run and the failed launch kept as evidence. Mandy item: a stat
or exists request type for the file survey service.

**The prefix map** (ruled 2026-10-21): configuration is the source, one
deployment setting of host-prefix to container-prefix pairs, no DDL. Two
pre-fills, each pair recording its source and date: docker inspect of the
platform container when it is local and reachable, and, for the
quickstart, the compose file itself (the owner: "at least in the
quickstart environment we can cheat and look at the quickstart compose
script to see what is mounted. May need more from Egeria, but this is a
way for now"), reading `egeria-quickstart.yaml`'s `egeria-main` volumes
with relative host paths resolved against the compose directory.
Precedence: configured, then inspected, then compose. With none, the map
is empty and every attach records "Egeria's mounts unknown on this
deployment · RE-led until configured", never a guess. Admin shows the
map read-only with each pair's source and date; editable later with the
zone-configuration Admin item.

**The file-name log cannot be turned off.** `FolderSurveyService` adds
"Profile File Names to External Log" unconditionally and never reads
`ignoreAnalysisSteps`. So on every Egeria-led survey Egeria writes a
file-name CSV inside its container; RE never reads, stores or shows it,
and the Egeria-led report's detail line says so. A path whose file names
are themselves sensitive is registered RE-led on purpose (a later
control). Mandy item: honour `ignoreAnalysisSteps`.

**Inaccessible files.** Egeria-led gives the count (the list lives in a
CSV asset behind the request for action); RE-led gives the paths. The
view says which.

## 3. Outcome 3: remote file systems

- **Access mechanisms are a user-extensible plugin point** (owner,
  2026-10-21: "in the long run, yes, we want to handle the common
  mechanisms (SFTP, S3) but it will need to be user extensible - not
  urgent but good to record"). SFTP and S3 are the first built-ins. The
  Analytic Library's packaging is the precedent: a reach mechanism is a
  package discovered by an entry point, with a manifest that declares
  its name, the protocol it speaks, the registration fields it needs
  (host, port, root, credential reference), its credential tier
  (read-only on the resource), and its cost shape (round trips per
  directory, bytes per file); it implements one interface, list a
  directory and stat an entry and read a capped file, and nothing else.
  The walk, the probe, the price line and the publish sentence are
  mechanism-agnostic and take the manifest's words. A mechanism that
  needs to execute commands on the host declares that, and the owner's
  configuration must allow it; none of the built-ins do.
- **Access methods, in order.** SFTP over SSH first: it reaches any host,
  lists and stats without executing commands. Then S3-compatible object
  stores, where a "folder" is a prefix and the listing is by prefix. SMB
  and NFS only as mounts on the RE host, which turn outcome 3 into
  outcome 2 and need no code. Egeria has no remote file connector, so
  outcome 3 is always RE-led.
- **The credential.** Per file system, stored under RE's credential key
  exactly as database credentials are: it is the resource's credential,
  the account allowed to read that path, and the registering person is
  recorded as requester. The run-as-initiating-user rule governs who RE
  is to Egeria; the remote login is the resource's, never the person's
  own key. A one-run, unstored credential for private investigations is
  a later option, as on databases.
- **The walk.** RE pulls the listing (SFTP listdir and stat, S3 list);
  it never runs a command on the remote host, because executing commands
  on someone's machine is a different trust level and is not offered.
  The price line counts round trips, one per directory, beside bytes,
  with a depth and an entry cap; `folder_narratives` and `file_profiles`
  fetch only their candidate files under the same caps.
- **Registration.** Step 1 stays local mount plus canonical mount. The
  remote step adds protocol, host, port, remote root and a credential
  reference as additive columns under the DDL deployment record, and the
  reachability check gains the SFTP and S3 paths.
- **Egeria asset.** The root is a `FileFolder` whose `pathName` is the
  remote root, with the host and protocol on the asset's connection
  (an `Endpoint` naming the host, a `ConnectorType` for SFTP or S3), so
  Egeria records where the data is even though it cannot read it.

## 4. Backlog: RE's walk as an Egeria survey service

Not urgent (owner). Two entries:

1. RE's walk declared as a `SurveyActionService` with request type
   `re-folder-survey`, producing Egeria's folder annotation types, so
   Egeria can schedule a folder survey on a path only RE can read through
   the case-3 engine-host wiring. Ties to
   `REQUIREMENTS-RE-DATA-CHANNELS.md`: Egeria's connectors cannot be the
   only channel.
2. Remote access mechanisms as a user-extensible plugin point (§3), SFTP
   and S3 as the built-ins, packaged and declared like library steps.
3. **An RE-owned folder survey that can skip steps** (owner, 2026-10-21:
   "if we want to be able to skip analysis steps in an Egeria Survey, we
   could create our own version that takes a parameter to gate some
   steps"). Ruling on its shape: it is entry 1 made concrete, not a Java
   connector. RE's walk, declared through the case-3 wiring as a
   `SurveyActionService` with request type `re-folder-survey`, takes a
   request parameter `skipSteps` (the step names from
   `SurveyFolderAnnotationType`, "Profile File Names to External Log"
   first among them) and produces Egeria's own folder annotation types
   under the same step names, so a reader cannot tell the two surveys
   apart except by the surveyor named on the report. It runs wherever RE
   has reach, which includes every path only RE can read; for a path
   only Egeria's container can read, Mandy's M-F2 (honour
   `ignoreAnalysisSteps`) stays the only way, so the two are
   complements, not alternatives. Not urgent; after the Files first
   slice and the case-3 gate.

## 5. Slices and gates

1. **Outcomes 1 and 2** (Files step 2 as re-briefed): the probe's two
   local outcomes, Egeria's Folder Survey on the coco data lake through
   its container path, RE's walk on the owner's path publishing the
   folder annotation types. Gate: both registered and surveyed in one
   walk, each report naming its surveyor.
2. **Outcome 3, SFTP** (its own step after the first slice's gate): the
   additive registration fields with their DDL record, the SFTP listing
   with the round-trip price, the credential stored as on databases.
   Gate: one remote host the owner names, registered, probed, surveyed
   with the price shown, published with the "over SFTP from" sentence,
   the credential never shown.
3. **S3-compatible** after SFTP, same shape, prefix as folder.

## 6. Open for the owner

- [x] SFTP and S3 as the first mechanisms, user-extensible (owner,
      2026-10-21).
- [ ] The other four positions in §3: the credential model, listings
      pulled with no remote commands, additive fields later, the publish
      sentence.
- [ ] Which remote host is the gate for outcome 3.
- [x] Whether S3 comes before SFTP, if the owner's estates are mostly
      object storage. **Decision (project owner, 2026-11-01):** "S3 first".
