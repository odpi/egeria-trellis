"""Decision logic for the live Egeria test tiers, as pure functions.

Until 2026-10-05 the live READ tier (`requires_egeria`) was auto-ON whenever
the platform answered a 2 s probe, so every full suite from any worktree talked
to the shared dev platform as the configured user. Both tiers are now opt-in:

* READS: `--live-egeria-reads` or `RE_LIVE_EGERIA_READS=1`, after a peer round.
* WRITES: `--live-egeria-writes` AND a non-empty
  `RE_LIVE_EGERIA_WRITES_CLEARED=<who>/<UTC time>` naming that peer round.
  The flag alone skips the tier (a skip, not a failure). Writes imply reads.
* CI (`GITHUB_ACTIONS=true`): always off. There is no live Egeria there.

Another session's "done" is a statement about itself, never a clearance from
the others; the peer round is a question to every live peer, and the answer set
is what goes in the variable.

Everything here takes the environment and flags as arguments, so tests drive it
with fakes and never touch Egeria. The reachability probe is passed in as a
callable and is only called when the tier is opted in, so a default run makes
no contact with the platform at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

READS_ENV = "RE_LIVE_EGERIA_READS"
CLEARED_ENV = "RE_LIVE_EGERIA_WRITES_CLEARED"

READS_OPT_IN_REASON = (
    "live Egeria tier is opt-in: run with --live-egeria-reads "
    "(or RE_LIVE_EGERIA_READS=1) after a peer round"
)
WRITES_CLEARANCE_REASON = (
    "live Egeria writes need RE_LIVE_EGERIA_WRITES_CLEARED=<who>/<UTC time> "
    "naming the peer round"
)
WRITES_OPT_IN_REASON = (
    "live Egeria writes are opt-in: --live-egeria-writes plus "
    "RE_LIVE_EGERIA_WRITES_CLEARED=<who>/<UTC time> naming the peer round"
)
UNREACHABLE_REASON = "Egeria platform not reachable at the configured platform_url"
CI_REASON = "live Egeria tier is never run in CI (no live Egeria there)"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() not in ("", "0", "false", "no")


@dataclass(frozen=True)
class TierDecision:
    reads_on: bool
    writes_on: bool
    cleared_by: str
    read_skip_reason: str | None      # None when reads run
    write_skip_reason: str | None     # None when writes run


def decide(environ: Mapping[str, str], *, reads_flag: bool, writes_flag: bool,
           reachable: Callable[[], bool]) -> TierDecision:
    cleared = (environ.get(CLEARED_ENV) or "").strip()
    if (environ.get("GITHUB_ACTIONS") or "").lower() == "true":
        return TierDecision(False, False, "", CI_REASON, CI_REASON)

    writes_asked = writes_flag
    reads_asked = reads_flag or _truthy(environ.get(READS_ENV)) or writes_asked
    if not reads_asked:
        return TierDecision(False, False, "", READS_OPT_IN_REASON, WRITES_OPT_IN_REASON)

    up = reachable()           # only now: a default run never contacts Egeria
    if not up:
        return TierDecision(False, False, "", UNREACHABLE_REASON, UNREACHABLE_REASON)
    if writes_asked and not cleared:
        return TierDecision(True, False, "", None, WRITES_CLEARANCE_REASON)
    if writes_asked:
        return TierDecision(True, True, cleared, None, None)
    return TierDecision(True, False, "", None, WRITES_OPT_IN_REASON)


def banner(decision: TierDecision, platform_url: str, user_id: str) -> str | None:
    """The one session-start line. Platform URL and user id only: no password,
    no token, nothing else from the config."""
    if not decision.reads_on:
        return None
    head = f"live Egeria tier: {platform_url} as {user_id}"
    if decision.writes_on:
        return f"{head} · writes ON · cleared by {decision.cleared_by}"
    return f"{head} · reads ON"
