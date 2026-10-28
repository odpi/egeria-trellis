"""CLI parity for the workflows and the run queue — plan §3.

"The CLI has no `analysis` command at all" was the plan's own finding, and it
was true for discovery and curate materialization too. Not because the CLI was
weaker than the web tier but because that code lived inside route modules. With
`resource_explorer/workflows/` extracted, these commands are thin: each one
calls the same function the corresponding route calls.

**`--queue` is the interesting flag.** By default a command runs its workflow
inline, in this process, and prints the result — which is what an operator at a
terminal wants, and what makes the CLI usable with no worker running at all.
With `--queue` the command instead enqueues a row and prints its id, so the same
command can hand work to a running worker: useful for a long survey you do not
want tied to your SSH session, and the reason `runs enqueue` exists as well.

The two paths deliberately share the workflow function rather than the queue
handler sharing an approximation of it: "what an analysis run does" must not
depend on whether a person or a worker started it.
"""
from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

console = Console()

analysis_app = typer.Typer(name="analysis", help="Run local analyses and stage batches.")
scout_app = typer.Typer(name="scout", help="Fast coarse scans (Scouting).")
discovery_app = typer.Typer(name="discovery", help="Find repos worth considering.")
curate_app = typer.Typer(name="curate", help="Act on curator verdicts.")
runs_app = typer.Typer(name="runs", help="Inspect and drive the Postgres run queue.")


def _registry():
    from resource_explorer.registry import ProjectRegistry

    return ProjectRegistry()


def _enqueue(kind: str, target: dict, *, activity_id: str = "") -> str:
    """Queue one row, attributed to whoever is signed in.

    `activate()` first: `requested_by()` reads the caller ContextVar, and a
    `--queue` command that never activated its cached session would enqueue
    into the unattributed bucket while looking, from the operator's side,
    exactly like one that did.
    """
    from resource_explorer.cli import session as cli_session
    from resource_explorer.run_queue import requested_by

    cli_session.activate(cli_session.resolve_identity()[1])
    run_id = _registry().enqueue_run(
        kind, target, result_ref=activity_id, requested_by=requested_by(),
    )
    console.print(f"[cyan]queued[/cyan] {kind} → run [bold]{run_id}[/bold]")
    console.print("[dim]A `resource-explorer worker` (or a `web --embed-worker`) "
                  "executes it. `runs show " + run_id + "` to follow it.[/dim]")
    return run_id


# ── analysis ─────────────────────────────────────────────────────────────────


@analysis_app.command("run")
def analysis_run(
    project: str = typer.Argument(help="Project slug"),
    analysis_id: str = typer.Argument(help="Analysis id, e.g. security_scan"),
    publish: bool = typer.Option(
        False, "--publish/--no-publish",
        help="Force the Egeria auto-publish attempt even when the resource has "
             "no assigned Egeria project. Off by default, matching the route: "
             "publish is gated on assignment, not on the caller asking."),
    queue: bool = typer.Option(False, "--queue", help="Enqueue instead of running inline"),
):
    """Run one analysis's mapped survey step(s) against a project."""
    if queue:
        _enqueue("analysis_run", {"slug": project, "analysis_id": analysis_id})
        return

    from resource_explorer.cli import session as cli_session
    from resource_explorer.workflows.analysis import resolve_analysis_plan, run_analysis

    # A run that will publish needs a signed-in owner. A run that will not is
    # local work on local data and stays usable with no session — the same
    # split `survey` makes.
    if publish:
        cli_session.require_and_activate(console)
    else:
        cli_session.activate(cli_session.resolve_identity()[1])

    is_ingest, steps = resolve_analysis_plan(analysis_id)
    if not is_ingest and not steps:
        console.print(f"[red]Analysis '{analysis_id}' has no mapped survey step(s).[/red]")
        raise typer.Exit(code=1)

    result = run_analysis(project, analysis_id, is_ingest=is_ingest, steps=steps)
    if result.status == "error":
        console.print(f"[red]{result.error}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]{result.summary}[/green]")
    # Three states, three sentences. "Not attempted" and "attempted and failed"
    # are different facts and the second one needs a retry.
    if result.published is True:
        console.print("[dim]Published to Egeria.[/dim]")
    elif result.published is False:
        console.print("[yellow]Auto-publish to Egeria failed — findings are stored; "
                      "publish again to retry.[/yellow]")
    elif publish:
        console.print("[yellow]--publish was given but nothing was published: the "
                      "resource has no assigned Egeria project, or the run produced "
                      "no annotations.[/yellow]")


@analysis_app.command("stage-batch")
def analysis_stage_batch(
    project: str = typer.Argument(help="Project slug"),
    stage: str = typer.Argument(help="Funnel stage / intent, e.g. scouting"),
    queue: bool = typer.Option(False, "--queue", help="Enqueue instead of running inline"),
):
    """Run every local analysis tagged with one stage, as a single survey call."""
    from resource_explorer.workflows.analysis import resolve_stage_step_keys, run_stage_batch

    step_keys, analysis_count = resolve_stage_step_keys(stage)
    if not step_keys:
        console.print(f"[red]Stage '{stage}' has no batch-runnable local analyses.[/red]")
        raise typer.Exit(code=1)
    if queue:
        _enqueue("stage_batch", {"slug": project, "stage": stage, "step_keys": step_keys})
        return

    console.print(f"[cyan]{len(step_keys)} step(s) from {analysis_count} analyses[/cyan]")
    result = run_stage_batch(project, stage, step_keys)
    console.print(f"[green]{result.summary}[/green]" if result.status == "ok"
                  else f"[red]{result.summary}[/red]")
    for err in result.errors:
        console.print(f"  [yellow]{err}[/yellow]")
    if result.status == "error":
        raise typer.Exit(code=1)


# ── scouting ─────────────────────────────────────────────────────────────────


@scout_app.command("run")
def scout_run(
    project: str = typer.Argument(help="Project slug"),
    queue: bool = typer.Option(False, "--queue", help="Enqueue instead of running inline"),
):
    """Coarse scan — repo stats plus language classification."""
    if queue:
        _enqueue("scouting_scan", {"slug": project})
        return

    from resource_explorer.workflows.scouting import run_scouting_scan

    result = run_scouting_scan(project)
    if result.status == "error":
        console.print(f"[red]{result.error}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]{result.message}[/green]")


# ── discovery ────────────────────────────────────────────────────────────────


@discovery_app.command("search")
def discovery_search(
    query: str = typer.Argument("", help="Free-text keyword"),
    org: str = typer.Option("", "--org", help="Restrict to one GitHub account"),
    language: str = typer.Option("", "--language"),
    topic: str = typer.Option("", "--topic"),
    min_stars: int = typer.Option(0, "--min-stars"),
    limit: int = typer.Option(30, "--limit"),
):
    """Search GitHub for candidate repos, with local triage context attached."""
    from resource_explorer.workflows.discovery import (
        DiscoveryError,
        RepoSearchCriteria,
        enrich_repos,
        run_search_query,
    )

    registry = _registry()
    criteria = RepoSearchCriteria(
        keyword=query, org=org, language=language, topic=topic,
        min_stars=min_stars, limit=limit,
    )
    try:
        repos = run_search_query(criteria, registry)
    except DiscoveryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    table = Table(title=f"{len(repos)} result(s)")
    for col in ("repo", "★", "language", "registered", "disposition"):
        table.add_column(col)
    for r in enrich_repos(repos, registry):
        table.add_row(r.full_name, str(r.stars), r.language,
                      "yes" if r.already_registered else "", r.disposition)
    console.print(table)


@discovery_app.command("expand-org")
def discovery_expand_org(
    org: str = typer.Argument(help="GitHub account name"),
    queue: bool = typer.Option(False, "--queue", help="Enqueue instead of running inline"),
):
    """List a GitHub account's repo URLs, newest-activity first."""
    if queue:
        _enqueue("discovery_expand", {"org": org})
        return

    from resource_explorer.workflows.discovery import expand_org

    expansion = expand_org(org)
    if expansion.error:
        console.print(f"[red]{expansion.error}[/red]")
        raise typer.Exit(code=1)
    for url in expansion.urls:
        console.print(url, highlight=False)
    # Said out loud, not inferred from the count: a capped expansion that reads
    # as the whole account is the failure this flag exists to prevent.
    if expansion.truncated:
        console.print(f"[yellow]Truncated at {len(expansion.urls)} — this account "
                      "has more repos than were listed.[/yellow]")


# ── curate ───────────────────────────────────────────────────────────────────


@curate_app.command("materialize")
def curate_materialize(
    project: str = typer.Argument(help="Project slug"),
    target: str = typer.Argument(
        help="A component's scope_locator, or a blueprint's 'perspective::cluster_name'"),
    entity_type: str = typer.Option("repo", "--entity-type"),
):
    """Publish one accepted component or blueprint to Egeria, through the same Publish the web press uses.

    It no longer writes directly: it queues ONE publish_architecture run for the target, and only when the
    Publish plan lists it (accepted and not yet in Egeria, or a blueprint still to finish). The run writes it
    as the web press would: the hold-back for a second blueprint of a kind, the attached record, the
    promotion and the per-item result rows. `perspective::cluster_name` is a blueprint; anything else is a
    component's scope_locator (the key `add_blueprint_verdict` builds).
    """
    from resource_explorer.cli import session as cli_session

    if entity_type != "repo":
        console.print("[yellow]Nothing attempted — Publish is only wired for entity_type='repo'.[/yellow]")
        raise typer.Exit(code=1)
    # The run commits on this person's behalf (Ownership = them), so it needs a person. Exits 2 with one line
    # when the session is missing or expired.
    cli_session.require_and_activate(console)

    from resource_explorer.architecture_publish import PublishAlreadyRunning, enqueue_publish, publish_plan
    from resource_explorer.run_queue import requested_by
    from resource_explorer.workflows.curate import CurationDenied, publish_item_access

    registry = _registry()
    proj = registry.get(project)
    if not proj:
        console.print(f"[red]Project '{project}' not found[/red]")
        raise typer.Exit(code=1)
    plan = publish_plan(registry, project)
    held = {b["key"]: b for b in plan["blueprints"].get("needs_identifier") or []}
    listed = ({c["path"] for c in plan["components"]["to_write"]} | {b["key"] for b in plan["blueprints"]["to_write"]})
    if target not in listed:
        why = (held[target]["words"] if target in held
               else "not in the Publish plan: not accepted, or already in Egeria with nothing left to finish")
        console.print(f"[yellow]Nothing queued — {target}: {why}[/yellow]")
        raise typer.Exit(code=1)

    def authorize(kind: str, key: str) -> None:
        # The same check the web press makes (workflows.curate.publish_item_access): every operation the write
        # performs, and ATTACH on a blueprint's members.
        d = publish_item_access(registry, project, kind, key)
        if not d.allowed:
            raise CurationDenied(d.reason)

    try:
        out = enqueue_publish(registry, proj, plan, requested_by=requested_by(), authorize=authorize, only={target})
    except CurationDenied as exc:
        console.print(f"[red]not permitted · {exc}[/red]")
        raise typer.Exit(code=3)
    except PublishAlreadyRunning as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1)
    console.print(json.dumps({k: out[k] for k in ("run_id", "activity_id", "queued")}, indent=2, default=str))
    console.print("[dim]Queued as Publish; the per-item result is on the Publish band (last publish) "
                  "when the run finishes.[/dim]")


# ── runs ─────────────────────────────────────────────────────────────────────


@runs_app.command("list")
def runs_list(
    state: str = typer.Option("", "--state", help="queued | claimed | running | succeeded | failed | cancelled"),
    kind: str = typer.Option("", "--kind"),
    limit: int = typer.Option(30, "--limit"),
):
    """What is in the queue, newest first."""
    from resource_explorer.registry import ProjectRegistry

    if state and state not in ProjectRegistry.RUN_STATES:
        console.print(f"[red]Unknown state '{state}' — expected one of "
                      f"{list(ProjectRegistry.RUN_STATES)}[/red]")
        raise typer.Exit(code=1)
    rows = _registry().list_runs(state=state or None, kind=kind or None, limit=limit)
    if not rows:
        # "No rows matched" and "the queue is empty" are different answers; say
        # which one this is.
        scope = f" in state '{state}'" if state else ""
        console.print(f"[dim]No runs{scope}.[/dim]")
        return
    table = Table()
    for col in ("id", "kind", "state", "claimed_by", "enqueued_at", "error"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(r["id"], r["kind"], r["state"], r["claimed_by"] or "—",
                      r["enqueued_at"], (r["error"] or "")[:80])
    console.print(table)


@runs_app.command("show")
def runs_show(run_id: str = typer.Argument(help="Run id")):
    """Everything recorded about one run."""
    row = _registry().get_run(run_id)
    if row is None:
        console.print(f"[red]Run '{run_id}' not found[/red]")
        raise typer.Exit(code=1)
    console.print(json.dumps(row, indent=2, default=str))


@runs_app.command("cancel")
def runs_cancel(run_id: str = typer.Argument(help="Run id")):
    """Cancel a run that has not been claimed yet."""
    registry = _registry()
    row = registry.get_run(run_id)
    if row is None:
        console.print(f"[red]Run '{run_id}' not found[/red]")
        raise typer.Exit(code=1)
    if registry.cancel_queued_run(run_id):
        console.print(f"[green]cancelled {run_id}[/green]")
        return
    from resource_explorer.run_queue import UNCANCELLABLE_KINDS

    if row.get("kind") in UNCANCELLABLE_KINDS:      # refused by the registry; say why
        console.print(
            f"[yellow]Run {run_id} is an {row['kind']} run, held for its Egeria engine action — nothing was "
            "changed.[/yellow]\n[dim]RE has claimed that action; cancelling the run here would leave it "
            "claimed with nothing to complete it.[/dim]"
        )
        raise typer.Exit(code=1)
    console.print(
        f"[yellow]Run {run_id} is {row['state']}, not queued — nothing was "
        f"changed.[/yellow]\n[dim]A claimed run holds a real thread in "
        f"{row.get('claimed_by') or 'a worker'} and Python threads cannot be "
        "interrupted. Marking it cancelled would be a claim about work that is "
        "still running. If that worker dies, reconciliation resolves the row as "
        "failed.[/dim]"
    )
    raise typer.Exit(code=1)


#: Run kinds that may write to Egeria (an analysis or Survey Definition run auto-publishes when the resource has an
#: assigned Egeria Project): `runs enqueue` attributes them to the signed-in person only (Brief Z2 round 2).
PUBLISHING_KINDS = frozenset({"analysis_run", "survey_definition_run", "database_analysis_run"})


@runs_app.command("enqueue")
def runs_enqueue(
    kind: str = typer.Argument(help="analysis_run | survey_definition_run | scouting_scan | stage_batch | discovery_expand"),
    target: str = typer.Argument("{}", help="Target as a JSON object, e.g. '{\"slug\": \"myrepo\"}'"),
    requested_by: str = typer.Option(
        "", "--requested-by",
        help="Attribute the run to a user id (fairness applies). "
             "Defaults to the signed-in user from `resource-explorer login`."),
):
    """Queue work for a worker straight from a shell.

    The escape hatch for anything the typed commands above do not cover, and the
    way to script the queue. `target` is passed to the kind's handler verbatim —
    see run_queue.py's HANDLERS for what each one reads.
    """
    from resource_explorer import run_queue
    from resource_explorer.cli import session as cli_session
    from resource_explorer.registry import ProjectRegistry

    if kind not in ProjectRegistry.RUN_KINDS:
        console.print(f"[red]Unknown kind '{kind}' — expected one of "
                      f"{list(ProjectRegistry.RUN_KINDS)}[/red]")
        raise typer.Exit(code=1)
    if kind in run_queue.PERSON_ACTION_KINDS:
        # Brief Z round 3: a verbatim target and a free --requested-by would let a shell forge what the run
        # trusts (a Portal role override in the target, another person's grants via the requester).
        console.print("[red]use Publish (web) or `curate materialize` — person actions need a signed-in "
                      "requester[/red]")
        raise typer.Exit(code=1)
    try:
        parsed = json.loads(target)
    except ValueError as exc:
        console.print(f"[red]target is not valid JSON: {exc}[/red]")
        raise typer.Exit(code=1)
    if not isinstance(parsed, dict):
        console.print("[red]target must be a JSON object[/red]")
        raise typer.Exit(code=1)

    # A queued run must carry a real user id or it lands in the unattributed
    # bucket, which `claim_next_run` exempts from per-user fairness — one
    # person could then hold every worker slot. So: `--requested-by` wins when
    # given (a script attributing on someone's behalf), otherwise the signed-in
    # user, and only a genuinely signed-out shell falls through to "".
    if kind in PUBLISHING_KINDS:
        # Brief Z2 round 2: these runs may publish to Egeria, and the run re-checks curation access as its
        # requester. So the requester must be the person at this shell: a session is required, and a
        # --requested-by naming anyone else is refused (it would borrow their grants).
        identity = cli_session.require_and_activate(console)
        me = (getattr(identity, "user_id", "") or run_queue.requested_by() or "").strip()
        if requested_by and requested_by.strip() != me:
            console.print(f"[red]--requested-by {requested_by!r} is not the signed-in user ({me!r}): a run that "
                          "may publish to Egeria is attributed to the person who queues it[/red]")
            raise typer.Exit(code=1)
        requested_by = me
    else:
        cli_session.activate(cli_session.resolve_identity()[1])
    attributed_to = requested_by or run_queue.requested_by()
    if not attributed_to:
        console.print(
            "[yellow]Not signed in — this run is unattributed and exempt from "
            "per-user fairness. `resource-explorer login`, or pass "
            "--requested-by.[/yellow]"
        )

    run_id = _registry().enqueue_run(kind, parsed, requested_by=attributed_to)
    console.print(run_id, highlight=False)


def register(app: typer.Typer) -> None:
    """Attach every command group above to the root CLI app."""
    app.add_typer(analysis_app)
    app.add_typer(scout_app)
    app.add_typer(discovery_app)
    app.add_typer(curate_app)
    app.add_typer(runs_app)
