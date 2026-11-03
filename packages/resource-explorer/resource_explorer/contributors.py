"""A repository's contributors, two lists, from the GitHub API (REVIEW-QUESTIONS-AND-ANALYTICS-2026-10-22.md, addendum 1, owner 2026-10-23).

* `months_12`  commits in the last 365 days, counted by git author (GitHub login when GitHub links the email,
               otherwise the git author's email or name), from `GET /repos/{o}/{r}/commits?since=..&per_page=100`.
* `all_time`   GitHub's own contributions per linked account, from
               `GET /repos/{o}/{r}/contributors?per_page=100&anon=1` (anonymous rows kept as Anonymous).

The two lists count differently, and every surface says so in one line (`COMPARE_WORDS`).

**Cost (calls per repository per fetch).** One REST call per 100 commits in the window for `months_12`, one per
100 contributor rows for `all_time`, each capped at `MAX_CALLS` (50: the most recent 5,000 commits, the first
5,000 rows); plus one rate-limit read before the first call (GitHub does not count that read). A capped list says
so (`capped` holds where it stopped) and the card says "12-month list from the most recent 5,000 commits (window
cut at <date>)". A 403 (or any refusal) stops the list cleanly and NO contributor row is written for it, so a partial
list is never stored or shown as whole; the refusal itself IS stored (see Storage).

**Storage.** `project_contributors` (registry `PROJECT_CONTRIBUTORS_DDL`), one fetch per (repository, list) kept
with its rows; rank 0 is the fetch's own row, so "fetched, nothing there" is stored and "never fetched" is the
absence of rows. The rank-0 row's `display_name` holds the fetch's status as JSON (no person sits on that row):
`status` ok / not_permitted / failed / not_enough_calls, with `reason`, `reset_at` and `error` for a refusal, and
`human_total` (the non-bot total every share is over; `commits` keeps the total with bots) and `merged` for a
success. A refused fetch is a rank-0 row alone. When the newest fetch was refused and an older one succeeded, the
reader returns the older list with its age and a "latest fetch failed" cue. `contributors()` is the ONE reader: the question page's compile, the chat tool, chaoss_metrics
and the publish step all read through it.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

WINDOW_12 = "months_12"
WINDOW_ALL = "all_time"
WINDOWS = (WINDOW_12, WINDOW_ALL)
SOURCE = {WINDOW_12: "github:commits", WINDOW_ALL: "github:contributors"}
SHARE_BASIS = {WINDOW_12: "commits in window", WINDOW_ALL: "contributions all time"}
LOOKBACK_12 = 365
PER_PAGE = 100
#: Calls per list per fetch: 50 pages of 100 = the most recent 5,000 commits (or the first 5,000 rows).
MAX_CALLS = 50
#: GitHub links only the first 500 author email addresses of a repository to accounts; the rest come back anonymous.
LINKING_LIMIT = 500
FETCHES_KEPT = 5
TOP = 10

NO_FETCH_YET = "no_fetch_yet"
EMPTY_REPOSITORY = "empty_repository"
NOT_PERMITTED = "not_permitted"
FAILED = "failed"
NOT_ENOUGH_CALLS = "not_enough_calls"
LINKING_LIMIT_REACHED = "linking_limit"
STATUS_OK = "ok"
#: The statuses of a fetch that stored no list.
REFUSALS = (NOT_PERMITTED, FAILED, NOT_ENOUGH_CALLS)
#: A 403/429 that is GitHub's rate limit (remaining 0, or a retry-after) vs any other refusal.
REASON_RATE_LIMIT = "rate_limit"
REASON_REFUSED = "refused"
UNKNOWN_NAME = "unknown contributor"

NO_FETCH_WORDS = "no contributor fetch yet"
EMPTY_12_WORDS = "no commits in the last 12 months"
#: The brief gives no words for an all-time list GitHub answers empty; this says what was read.
EMPTY_ALL_WORDS = "GitHub lists no contributors"
COMPARE_WORDS = ("12-month list counts commits by git author in the window; all-time list counts GitHub's "
                 "contributions by linked account. Shares are not comparable across the two.")
FALLBACK_WORDS = "from git authors, 90-day fetch"


def not_permitted_words(reset_at: str, reason: str = REASON_RATE_LIMIT, error: str = "") -> str:
    """A rate-limit refusal names the reset time; any other refusal says what GitHub said (e.g. a repository too
    large to list its contributors), never a reset that does not apply."""
    if reason == REASON_REFUSED:
        return f"GitHub refused the read: {error}" if error else "GitHub refused the read"
    return f"GitHub refused the read (rate limit resets {reset_at})" if reset_at else \
        "GitHub refused the read (rate limit)"


def not_enough_calls_words(need: int, remaining: int, reset_at: str) -> str:
    return (f"not fetched: about {need} GitHub call(s) needed, {remaining} left"
            + (f" (rate limit resets {reset_at})" if reset_at else ""))


def failed_words(error: str) -> str:
    return f"the fetch failed: {error}" if error else "the fetch failed"


def status_words(meta: dict) -> str:
    """The words for a fetch that stored no list, from its stored status."""
    status = meta.get("status") or ""
    if status == NOT_PERMITTED:
        return not_permitted_words(meta.get("reset_at") or "", meta.get("reason") or REASON_RATE_LIMIT,
                                   meta.get("error") or "")
    if status == NOT_ENOUGH_CALLS:
        return not_enough_calls_words(int(meta.get("need") or 0), int(meta.get("remaining") or 0),
                                      meta.get("reset_at") or "")
    return failed_words(meta.get("error") or "")


def latest_failed_words(attempted_at: str, why: str, fetched_at: str, age_days: int | None) -> str:
    age = f", {age_days} day{'s' if age_days != 1 else ''} old" if age_days is not None else ""
    return (f"latest fetch failed ({(attempted_at or '')[:10]}: {why}); "
            f"showing the list fetched {(fetched_at or '')[:10]}{age}")


def merged_words(n: int) -> str:
    return f"merged {n} git identit{'ies' if n != 1 else 'y'} into linked accounts"


def tied_words(top: int, more: int, commits: int) -> str:
    return f"tied at {top}: {more} more with {commits} each not shown"


def linking_words(anonymous: int) -> str:
    return f"GitHub links only the first {LINKING_LIMIT} authors; {anonymous} more are anonymous"


def capped_words(cut_at: str) -> str:
    return (f"12-month list from the most recent {MAX_CALLS * PER_PAGE:,} commits "
            f"(window cut at {(cut_at or '')[:10]})")


def bots_words(n: int) -> str:
    return f"{n} bot{'s' if n != 1 else ''} excluded"


def _mask_at(text: str) -> str:
    return text.split("@", 1)[0] + "@\u2026" if "@" in text else text


@dataclass
class ContributorRow:
    id: int
    rank: int
    login: str
    display_name: str
    author_email: str
    commits: int
    share: float
    is_bot: bool
    #: User | Bot | Anonymous (all_time, as GitHub types them) or User | Bot | unlinked (months_12: a git author
    #: GitHub could not link to an account).
    kind: str

    @property
    def name(self) -> str:
        # Never the email: an address is not a name to show or publish. A name that holds an "@" (a git author
        # name typed as an address) is cut to its local part.
        return _mask_at(self.login or self.display_name or UNKNOWN_NAME)

    def as_dict(self) -> dict:
        return {"rank": self.rank, "login": self.login, "name": self.name, "display_name": _mask_at(self.display_name),
                "commits": self.commits, "share": round(self.share, 4), "is_bot": self.is_bot, "kind": self.kind}


@dataclass
class ContributorList:
    slug: str
    window: str
    rows: list = field(default_factory=list)          # the top N (bots excluded unless asked), by rank
    share_basis: str = ""
    fetched_at: str = ""
    lookback_days: int | None = None
    capped: bool = False
    cut_at: str = ""                                   # where the cap stopped the list ('' when read whole)
    bots_excluded: int = 0
    absence: str = ""                                  # '' or an absence (NO_FETCH_YET, a REFUSALS status, ...)
    reset_at: str = ""                                 # with not_permitted / not_enough_calls
    reason: str = ""                                   # with not_permitted: rate_limit | refused
    error: str = ""                                    # the refusal's own words
    absence_words: str = ""                            # the stored status in words, for a refusal
    total: int = 0                                     # NON-BOT commits / contributions: every share is over this
    total_all: int = 0                                 # the same, bots included
    total_rows: int = 0                                # contributor rows stored, bots and anonymous included
    humans: int = 0                                    # contributor rows that are not bots
    anonymous: int = 0
    merged: int = 0                                    # months_12: unlinked git identities merged into a login
    tied: int = 0                                      # humans not shown with the same count as the last shown
    #: When the newest fetch stored no list and an older one did: that newest fetch's status, time and words.
    latest_status: str = ""
    latest_attempt_at: str = ""
    latest_words: str = ""
    age_days: int | None = None
    #: Every stored row id behind `rows` plus the fetch's own row: what a surface that rendered this list saw.
    row_ids: list = field(default_factory=list)

    @property
    def top_share(self) -> float:
        """The shown rows' share of the TOTAL (not of the shown rows)."""
        return sum(r.share for r in self.rows)

    def top_n_share(self, n: int) -> float:
        return sum(r.share for r in self.rows[:n])

    def words(self) -> list[str]:
        """The card's lines for this list, in the brief's words: absence first, then the cut, the bots, the
        linking limit."""
        out: list[str] = []
        if self.absence == NO_FETCH_YET:
            return [NO_FETCH_WORDS]
        if self.absence in REFUSALS:
            return [self.absence_words or NO_FETCH_WORDS]
        if self.latest_status:
            out.append(latest_failed_words(self.latest_attempt_at, self.latest_words, self.fetched_at,
                                           self.age_days))
        if self.absence == EMPTY_REPOSITORY and not self.rows:
            return out + [EMPTY_12_WORDS if self.window == WINDOW_12 else EMPTY_ALL_WORDS]
        if self.capped and self.window == WINDOW_12:
            out.append(capped_words(self.cut_at))
        elif self.capped:
            out.append(f"all-time list from the first {MAX_CALLS * PER_PAGE:,} rows GitHub returned")
        if self.bots_excluded:
            out.append(bots_words(self.bots_excluded))
        if self.absence == LINKING_LIMIT_REACHED:
            out.append(linking_words(self.anonymous))
        if self.tied and self.rows:
            out.append(tied_words(len(self.rows), self.tied, self.rows[-1].commits))
        return out

    def hover(self) -> str:
        """The one line under the list (a title / hover): what the shares are over, and what was folded in."""
        if not self.rows:
            return "; ".join(self.words())
        parts = [f"shares over {self.total:,} non-bot {self.share_basis}"
                 + (f" ({self.total_all:,} with bots)" if self.total_all != self.total else "")]
        if self.bots_excluded:
            parts.append(bots_words(self.bots_excluded))
        if self.merged:
            parts.append(merged_words(self.merged))
        return "; ".join(parts)

    def as_dict(self) -> dict:
        return {"window": self.window, "rows": [r.as_dict() for r in self.rows], "share_basis": self.share_basis,
                "fetched_at": self.fetched_at, "lookback_days": self.lookback_days, "capped": self.capped,
                "cut_at": self.cut_at, "bots_excluded": self.bots_excluded, "absence": self.absence,
                "reset_at": self.reset_at, "reason": self.reason, "total": self.total, "total_all": self.total_all,
                "total_rows": self.total_rows, "humans": self.humans, "merged": self.merged, "tied": self.tied,
                "latest_status": self.latest_status, "latest_attempt_at": self.latest_attempt_at,
                "age_days": self.age_days, "top_share": round(self.top_share, 4), "words": self.words(),
                "hover": self.hover()}


def _kind(window: str, login: str, is_bot: bool) -> str:
    if is_bot:
        return "Bot"
    if login:
        return "User"
    return "Anonymous" if window == WINDOW_ALL else "unlinked"


def _meta(head: dict | None) -> dict:
    """The status stored on a fetch's rank-0 row (`display_name`, JSON). A head written before statuses were stored
    is a successful fetch."""
    raw = (head or {}).get("display_name") or ""
    try:
        meta = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("status", STATUS_OK)
    return meta


def _newest_ok(heads: list[dict]) -> dict | None:
    return next((h for h in heads if _meta(h)["status"] == STATUS_OK), None)


def _age_days(fetched_at: str, now: datetime) -> int | None:
    try:
        return max(0, (now - datetime.fromisoformat(fetched_at[:19])).days)
    except (TypeError, ValueError):
        return None


def any_fetched(lists: dict) -> bool:
    """Whether a fetch of either list was ever attempted: the ONE predicate every surface uses to stop falling back to
    the 90-day git authors (a refused fetch counts; it is stored)."""
    return bool(lists) and not all(cl.absence == NO_FETCH_YET for cl in lists.values())


def contributors(registry, slug: str, window: str, top: int = TOP, *, include_bots: bool = False,
                 now: datetime | None = None) -> ContributorList:
    """The newest stored list of one window. `rows` is the top `top` (bots left out unless `include_bots`), ranked
    1..N as shown; shares are each row's part of the NON-BOT total (bots carry no share), so the top-N share is
    reported against that total.

    The newest fetch decides the state: never fetched -> NO_FETCH_YET; refused/failed with no earlier list -> that
    status as the absence, with its reset time and words; refused/failed after an earlier success -> the earlier list,
    with `latest_status`, its age and the "latest fetch failed" cue first in `words()`."""
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {WINDOWS}, not {window!r}")
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    out = ContributorList(slug=slug, window=window, share_basis=SHARE_BASIS[window])
    heads = registry.contributor_fetch_heads(slug, window)
    if not heads:
        out.absence = NO_FETCH_YET
        return out
    latest, latest_meta = heads[0], _meta(heads[0])
    ok = latest if latest_meta["status"] == STATUS_OK else _newest_ok(heads)
    if latest_meta["status"] != STATUS_OK:
        words = status_words(latest_meta)
        if ok is None:
            out.absence = latest_meta["status"] if latest_meta["status"] in REFUSALS else FAILED
            out.reset_at = str(latest_meta.get("reset_at") or "")
            out.reason = str(latest_meta.get("reason") or "")
            out.error = str(latest_meta.get("error") or "")
            out.absence_words = words
            out.fetched_at = latest["fetched_at"]
            out.row_ids = [int(latest["id"])]
            return out
        out.latest_status, out.latest_attempt_at, out.latest_words = latest_meta["status"], latest["fetched_at"], words
        out.age_days = _age_days(ok["fetched_at"], now)
    meta = _meta(ok)
    stored = registry.contributor_fetch_rows(slug, window, ok["fetched_at"])
    head = stored[0] if stored and stored[0]["rank"] == 0 else None
    people = [r for r in stored if r["rank"] >= 1]
    out.fetched_at = ok["fetched_at"]
    out.lookback_days = ok["lookback_days"]
    out.cut_at = ok["capped"] or ""
    out.capped = bool(out.cut_at)
    human_total = sum(int(r["commits"]) for r in people if not r["is_bot"])
    out.total = int(meta.get("human_total") if meta.get("human_total") is not None else human_total)
    out.total_all = int(head["commits"]) if head else sum(int(r["commits"]) for r in people)
    out.merged = int(meta.get("merged") or 0)
    out.total_rows = len(people)
    rows = [ContributorRow(id=int(r["id"]), rank=int(r["rank"]), login=r["login"] or "",
                           display_name=r["display_name"] or "", author_email=r["author_email"] or "",
                           commits=int(r["commits"]),
                           share=0.0 if r["is_bot"] else ((int(r["commits"]) / out.total) if out.total else 0.0),
                           is_bot=bool(r["is_bot"]),
                           kind=_kind(window, r["login"] or "", bool(r["is_bot"]))) for r in people]
    rows.sort(key=lambda r: (-r.commits, r.rank))
    out.humans = sum(1 for r in rows if not r.is_bot)
    out.bots_excluded = sum(1 for r in rows if r.is_bot) if not include_bots else 0
    eligible = [r for r in rows if include_bots or not r.is_bot]
    n = max(top, 0)
    shown = eligible[:n]
    for i, r in enumerate(shown, 1):                   # ranked as shown: a bot left out leaves no gap
        r.rank = i
    if shown and len(eligible) > n:
        last = shown[-1].commits
        out.tied = sum(1 for r in eligible[n:] if r.commits == last)
    out.rows = shown
    out.row_ids = ([int(head["id"])] if head else []) + [r.id for r in shown]
    if out.latest_status:
        out.row_ids = [int(latest["id"])] + out.row_ids
    if not people:
        out.absence = EMPTY_REPOSITORY
        return out
    out.anonymous = sum(1 for r in rows if r.kind == "Anonymous")
    if window == WINDOW_ALL and out.anonymous and len(people) > LINKING_LIMIT:
        out.absence = LINKING_LIMIT_REACHED
    return out


# ── the fetch ───────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class FetchOutcome:
    window: str
    absence: str = ""
    reset_at: str = ""
    reason: str = ""
    pages: int = 0
    rows: int = 0
    total: int = 0
    human_total: int = 0
    merged: int = 0
    capped: str = ""
    row_ids: list = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class _Refused(Exception):
    def __init__(self, reset_at: str, message: str, reason: str = REASON_RATE_LIMIT) -> None:
        super().__init__(message)
        self.reset_at = reset_at
        self.reason = reason


class _Empty(Exception):
    pass


def _header(headers, name: str) -> str:
    headers = headers or {}
    for k, v in headers.items():
        if str(k).lower() == name:
            return str(v)
    return ""


def _reset_from_headers(headers) -> str:
    raw = _header(headers, "x-ratelimit-reset")
    if not raw:
        return ""
    try:
        return datetime.fromtimestamp(int(raw), timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return str(raw)


def _is_rate_limit(status: int, headers) -> bool:
    """GitHub's rate limit (primary: remaining 0; secondary: a retry-after) as opposed to any other refusal."""
    return status == 429 or _header(headers, "x-ratelimit-remaining") == "0" or bool(_header(headers, "retry-after"))


def _github_message(exc) -> str:
    data = getattr(exc, "data", None)
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return str(exc)


def _get(client, path: str, params: dict):
    """One call. A refusal (401/403/429) becomes _Refused: a rate limit with the reset time, any other refusal with
    GitHub's own message; 409 (GitHub's answer for the commits of an empty repository) becomes _Empty; anything else
    propagates."""
    from github import GithubException

    try:
        headers, data = client.rest_get(path, params)
    except GithubException as exc:
        if exc.status in (401, 403, 429):
            if _is_rate_limit(exc.status, exc.headers):
                raise _Refused(_reset_from_headers(exc.headers), _github_message(exc), REASON_RATE_LIMIT) from exc
            raise _Refused("", _github_message(exc), REASON_REFUSED) from exc
        if exc.status == 409:
            raise _Empty(str(exc)) from exc
        raise
    return headers, data


def _is_bot_login(user: dict | None) -> bool:
    if not user:
        return False
    return (user.get("type") == "Bot") or str(user.get("login") or "").endswith("[bot]")


def _shares(rows: list[dict]) -> tuple[list[dict], int]:
    """Rank the rows and give each human its share of the NON-BOT total (a bot carries no share). Returns the rows
    and that total."""
    human_total = sum(r["commits"] for r in rows if not r["is_bot"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["share"] = 0.0 if r["is_bot"] else ((r["commits"] / human_total) if human_total else 0.0)
    return rows, human_total


def estimate_pages(registry, slug: str, window: str) -> tuple[int, str]:
    """Calls this list's next fetch should take, and on what basis: the previous successful fetch's count, else the
    repository's own counts (`project_stats`), else one call."""
    ok = _newest_ok(registry.contributor_fetch_heads(slug, window))
    if ok:
        if window == WINDOW_12:
            n = int(ok["commits"])
        else:
            n = len([r for r in registry.contributor_fetch_rows(slug, window, ok["fetched_at"]) if r["rank"] >= 1])
        basis = "the previous fetch"
    else:
        stats = {}
        try:
            stats = registry.get_latest_project_stats(slug) or {}
        except Exception:  # noqa: BLE001
            stats = {}
        if window == WINDOW_12:
            n = stats.get("commits_365d") or (4 * (stats.get("commits_90d") or 0))
        else:
            n = stats.get("contributors_count") or 0
        basis = "the repository's commit count" if window == WINDOW_12 else "the repository's contributor count"
        if not n:
            return 1, "no count recorded"
    return min(MAX_CALLS, max(1, math.ceil(n / PER_PAGE))), basis


def cost_line(registry, slug: str) -> str:
    """The line shown before a fetch: the calls both lists should take."""
    p12, b12 = estimate_pages(registry, slug, WINDOW_12)
    pall, ball = estimate_pages(registry, slug, WINDOW_ALL)
    return (f"about {p12 + pall} GitHub call(s): 12-month list {p12} (from {b12}), all-time list {pall} "
            f"(from {ball}); at most {MAX_CALLS} each")


def _merge_unlinked(by_key: dict[str, dict], seen: dict[str, dict[str, set]]) -> int:
    """Fold each unlinked git identity into the ONE linked login this fetch saw with the same git email or name.
    Ambiguous (two logins share it) stays apart. Returns how many identities were merged."""
    by_email: dict[str, set] = {}
    by_name: dict[str, set] = {}
    for key, s in seen.items():
        for e in s["emails"]:
            by_email.setdefault(e, set()).add(key)
        for n in s["names"]:
            by_name.setdefault(n, set()).add(key)
    merged = 0
    for key in [k for k in by_key if not k.startswith("login:")]:
        entry = by_key[key]
        email, name = entry["author_email"].lower(), entry["display_name"].lower()
        targets = (by_email.get(email, set()) if email else set()) | (by_name.get(name, set()) if name else set())
        if len(targets) != 1:
            continue
        target = by_key[next(iter(targets))]
        if bool(target["is_bot"]) != bool(entry["is_bot"]):
            continue                                       # a bot and a person are never one identity
        target["commits"] += entry["commits"]
        target["is_bot"] = target["is_bot"] or entry["is_bot"]
        del by_key[key]
        merged += 1
    return merged


def _fetch_12(client, full_name: str, now: datetime) -> tuple[list[dict], int, int, str, int, int]:
    since = (now - timedelta(days=LOOKBACK_12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_key: dict[str, dict] = {}
    seen: dict[str, dict[str, set]] = {}                   # login key -> the git emails and names it committed as
    total = pages = 0
    oldest = ""
    last_full = False
    for page in range(1, MAX_CALLS + 1):
        try:
            _, data = _get(client, f"/repos/{full_name}/commits",
                           {"since": since, "per_page": PER_PAGE, "page": page})
        except _Empty:
            data = []
        pages += 1
        data = data or []
        for c in data:
            git_author = (c.get("commit") or {}).get("author") or {}
            user = c.get("author") or None                     # GitHub's linked account, null when unlinked
            login = (user or {}).get("login") or ""
            email = (git_author.get("email") or "").strip()
            name = (git_author.get("name") or "").strip()
            key = f"login:{login.lower()}" if login else (f"email:{email.lower()}" if email else f"name:{name.lower()}")
            entry = by_key.setdefault(key, {"login": login, "display_name": name, "author_email": email,
                                            "commits": 0, "is_bot": False})
            entry["commits"] += 1
            entry["is_bot"] = entry["is_bot"] or _is_bot_login(user) or name.endswith("[bot]")
            if login:
                s = seen.setdefault(key, {"emails": set(), "names": set()})
                if email:
                    s["emails"].add(email.lower())
                if name:
                    s["names"].add(name.lower())
            total += 1
            date = git_author.get("date") or ""
            if date and (not oldest or date < oldest):
                oldest = date
        last_full = len(data) >= PER_PAGE
        if not last_full:
            break
    capped = oldest if (pages >= MAX_CALLS and last_full) else ""
    merged = _merge_unlinked(by_key, seen)
    rows = sorted(by_key.values(), key=lambda r: (-r["commits"], (r["login"] or r["display_name"]).lower()))
    rows, human_total = _shares(rows)
    return rows, total, pages, capped, human_total, merged


def _fetch_all(client, full_name: str) -> tuple[list[dict], int, int, str, int, int]:
    rows: list[dict] = []
    pages = 0
    last_full = False
    for page in range(1, MAX_CALLS + 1):
        _, data = _get(client, f"/repos/{full_name}/contributors", {"per_page": PER_PAGE, "anon": 1, "page": page})
        pages += 1
        data = data or []                                  # 204 No Content: an empty repository
        for c in data:
            anonymous = c.get("type") == "Anonymous"
            login = "" if anonymous else (c.get("login") or "")
            name = (c.get("name") or "") if anonymous else ""
            email = (c.get("email") or "") if anonymous else ""
            rows.append({"login": login, "display_name": name, "author_email": email,
                         "commits": int(c.get("contributions") or 0),
                         "is_bot": (not anonymous and _is_bot_login(c)) or "[bot]" in (name + email)})
        last_full = len(data) >= PER_PAGE
        if not last_full:
            break
    capped = f"rows {len(rows)}" if (pages >= MAX_CALLS and last_full) else ""
    total = sum(r["commits"] for r in rows)
    rows.sort(key=lambda r: -r["commits"])                # GitHub's own order is by contributions; kept stable
    rows, human_total = _shares(rows)
    return rows, total, pages, capped, human_total, 0


def fetch_contributors(registry, client, slug: str, full_name: str, *, now: datetime | None = None) -> dict:
    """Fetch both lists for one repository and store each one whole, or store why not.

    `client` is a GitHubClient (its `rest_get` and `check_rate_limit`); `full_name` is "owner/repo". A list that
    could not be read (refused, too few calls left, any failure) is stored as a rank-0 row with its status, reset
    time and words and NO contributor rows; an older successful list of that window is kept. Returns
    {window: FetchOutcome.as_dict()} plus "cost_line" (the estimate made before the first call)."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    fetched_at = now.isoformat()
    result: dict = {"cost_line": cost_line(registry, slug)}
    log.info("contributors %s: %s", slug, result["cost_line"])
    try:
        rate = client.check_rate_limit()
    except Exception:  # noqa: BLE001 - optimistic, as the commits fetch is
        rate = None
    for window, fetch in ((WINDOW_12, lambda: _fetch_12(client, full_name, now)),
                          (WINDOW_ALL, lambda: _fetch_all(client, full_name))):
        outcome = FetchOutcome(window)
        lookback = LOOKBACK_12 if window == WINDOW_12 else None

        def _store_refusal(meta: dict) -> None:
            ok = _newest_ok(registry.contributor_fetch_heads(slug, window))
            outcome.row_ids = registry.write_contributor_fetch(
                slug, window, fetched_at, [], total=0, lookback_days=lookback, source=SOURCE[window],
                keep_fetches=FETCHES_KEPT, meta=meta, keep_also=ok["fetched_at"] if ok else "")

        need, _ = estimate_pages(registry, slug, window)
        remaining = int((rate or {}).get("remaining") or 0)
        if rate is not None and remaining < need:
            outcome.absence, outcome.reset_at = NOT_ENOUGH_CALLS, str(rate.get("reset_at") or "")[:19]
            _store_refusal({"status": NOT_ENOUGH_CALLS, "need": need, "remaining": remaining,
                            "reset_at": outcome.reset_at})
            result[window] = outcome.as_dict()
            continue
        try:
            rows, total, pages, capped, human_total, merged = fetch()
        except _Refused as exc:
            outcome.absence, outcome.reset_at, outcome.reason = NOT_PERMITTED, exc.reset_at, exc.reason
            outcome.error = str(exc)[:300]
            _store_refusal({"status": NOT_PERMITTED, "reason": exc.reason, "reset_at": exc.reset_at,
                            "error": outcome.error})
            result[window] = outcome.as_dict()
            continue
        except Exception as exc:  # noqa: BLE001 - one list failing never writes a partial list
            outcome.absence, outcome.error = FAILED, f"{type(exc).__name__}: {exc}"[:300]
            _store_refusal({"status": FAILED, "error": outcome.error})
            result[window] = outcome.as_dict()
            continue
        outcome.pages, outcome.rows, outcome.total, outcome.capped = pages, len(rows), total, capped
        outcome.human_total, outcome.merged = human_total, merged
        outcome.row_ids = registry.write_contributor_fetch(
            slug, window, fetched_at, rows, total=total, lookback_days=lookback,
            source=SOURCE[window], capped=capped, keep_fetches=FETCHES_KEPT,
            meta={"status": STATUS_OK, "human_total": human_total, "merged": merged})
        if not rows:
            outcome.absence = EMPTY_REPOSITORY
        result[window] = outcome.as_dict()
    return result


# ── what the surfaces show ──────────────────────────────────────────────────────────────────────────────────────


def both(registry, slug: str, top: int = TOP) -> dict:
    return {w: contributors(registry, slug, w, top) for w in WINDOWS}


def headline_sentence(lists: dict) -> str:
    """Names the top contributor and the top-3 share (over the non-bot total): the all-time list when there is one,
    else the 12-month. A list shown behind a failed newer fetch says so, with its age."""
    for window in (WINDOW_ALL, WINDOW_12):
        cl = lists.get(window)
        if cl is None or not cl.rows:
            continue
        top = cl.rows[0]
        span = "all time" if window == WINDOW_ALL else "in the last 12 months"
        stale = (f" (list fetched {cl.fetched_at[:10]}, {cl.age_days} days old; the latest fetch failed)"
                 if cl.latest_status else "")
        return (f"top contributor {top.name} ({top.share:.0%} of {cl.share_basis}); "
                f"top 3 hold {cl.top_n_share(3):.0%} {span}{stale}")
    return ""


def absence_sentence(lists: dict) -> str:
    """When neither list has rows but a fetch was attempted: each list's own words, never the 90-day fallback."""
    parts = []
    for window, label in ((WINDOW_ALL, "all time"), (WINDOW_12, "last 12 months")):
        cl = lists.get(window)
        if cl is not None:
            parts.append(f"{label}: {'; '.join(cl.words()) or NO_FETCH_WORDS}")
    return "contributors, " + "; ".join(parts) if parts else ""


def text_block(lists: dict, *, limit: int = TOP) -> list[str]:
    """The lists as lines a model or a person reads (the compile's extent and the chat tool's answer)."""
    lines = ["contributors:"]
    for window, label in ((WINDOW_ALL, "all time (GitHub contributions by linked account)"),
                          (WINDOW_12, "last 12 months (commits by git author)")):
        cl = lists[window]
        words = cl.words()
        if not cl.rows:
            lines.append(f"- {label}: {'; '.join(words) or NO_FETCH_WORDS}")
            continue
        shown = cl.rows[:limit]
        lines.append(f"- {label}, fetched {cl.fetched_at[:10]}: top {len(shown)} of {cl.humans} hold "
                     f"{sum(r.share for r in shown):.0%} of {cl.total} non-bot {cl.share_basis}"
                     + (f" ({'; '.join(words)})" if words else ""))
        if cl.merged:
            lines.append(f"  ({merged_words(cl.merged)})")
        for r in shown:
            extra = f" [{r.kind}]" if r.kind not in ("User",) else ""
            lines.append(f"  {r.rank}. {r.name}{extra}: {r.commits} ({r.share:.1%})")
    lines.append(f"- {COMPARE_WORDS}")
    return lines


def annotations(registry, slug: str) -> list:
    """ONE ResourceMeasureAnnotation per stored list (Egeria has no contributor annotation type; following its lead
    as far as it goes): annotationType "Contributors", `resourceProperties` top10 as JSON (each entry's "name" is
    the shown name, never an email), `measurementName` the window, `as_of` (in resourceProperties) the fetch time, `basis` the share basis.
    Identified by (check_name, item_key = window), so a second publish of the same report reuses the same
    qualifiedName; the outbox then updates that element when its `resourceProperties` differ
    (egeria_outbox.REFRESHED_ANNOTATION_TYPES). A list never fetched, refused with no earlier list, or empty
    publishes nothing."""
    from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation

    out = []
    for window in WINDOWS:
        cl = contributors(registry, slug, window)
        if not cl.rows:
            continue
        top10 = [{"name": r.name, "commits": r.commits, "share": round(r.share, 4)} for r in cl.rows]
        out.append(ResourceMeasureAnnotation(
            summary=(f"Contributors, {'last 12 months' if window == WINDOW_12 else 'all time'}: "
                     + ", ".join(r.name for r in cl.rows[:3])),
            analysis_step="repo_chaoss_metrics", annotation_type_name="Contributors",
            check_name="contributors", item_key=window,
            explanation="; ".join([COMPARE_WORDS, *cl.words(), cl.hover()]),
            confidence=100,
            resource_properties={"top10": json.dumps(top10), "as_of": cl.fetched_at},
            additional_properties={"measurementName": window, "basis": cl.share_basis,
                                   "source": SOURCE[window], "total": str(cl.total),
                                   "total_with_bots": str(cl.total_all), "merged": str(cl.merged),
                                   "capped": cl.cut_at, "bots_excluded": str(cl.bots_excluded)}))
    return out
