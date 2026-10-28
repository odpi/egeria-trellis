"""CLI entry point — all resource-explorer commands."""
from __future__ import annotations

import os

# Suppress gRPC fork warnings, HuggingFace progress bars, and tokenizer parallelism
# noise that pollutes normal CLI output. Set DEBUG=1 to restore verbose logging.
if not os.environ.get("DEBUG"):
    os.environ.setdefault("GRPC_VERBOSITY", "NONE")
    os.environ.setdefault("GLOG_minloglevel", "3")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TQDM_DISABLE", "1")

from typing import Optional

import typer
from rich.console import Console

# Module level only for the `serve` command's default port, which Typer
# evaluates at import time. a2a_role imports nothing heavier than stdlib at
# module scope, so this costs the CLI nothing on any other command.
from resource_explorer.a2a_role import DEFAULT_A2A_PORT
from resource_explorer.config import egeria_platform_url, egeria_view_server  # noqa: E402,F401

app = typer.Typer(
    name="resource-explorer",
    help="Scout, survey, and catalog information resources using Egeria.",
    rich_markup_mode="rich",
)
console = Console()


@app.callback()
def _announce_registry() -> None:
    """Print which registry this command will use, once, to stderr.

    A worktree has no `.env` and the default is the SHARED Postgres, so a
    stray command must be visible: the shared default prints
    `registry: localhost:5442/egeria_advisor (shared) ...`, anything else
    (e.g. a builder's temp SQLite) prints `registry: sqlite:///<file>`.
    Host and database only, never credentials. Not a refusal: 8810 and 8813
    legitimately use the default.
    """
    from resource_explorer.registry_label import describe_registry

    try:
        from resource_explorer.config import get_config
        url = get_config().registry.database_url
    except Exception:
        url = os.environ.get("REGISTRY_DATABASE_URL", "sqlite:///data/registry.db")
    typer.echo(describe_registry(url), err=True)


# ── sign in / sign out ───────────────────────────────────────────────────────
#
# `docs/runtime-architecture-plan.md` §4: Egeria is the identity provider, its
# bearer tokens last an hour, and every one of them dies when the platform
# restarts. A CLI that prompted on every command would be unusable and one
# that stored the password would undo the point of the token contract — so the
# password is exchanged once, here, and what lands on disk is this app's
# session JWT (mode 0600). See `resource_explorer/cli/session.py`.


@app.command(name="login")
def login_command(
    user: Optional[str] = typer.Option(
        None, "--user", help="Egeria user id (prompted for if omitted)"),
):
    """Sign in to Egeria and cache the session for later commands.

    The password is **prompted for, never taken as an argument** — an argument
    lands in the shell history, in `ps` output, and in whatever process
    accounting the box keeps. It is exchanged for an Egeria bearer token once
    and then discarded.

    Commands that reach Egeria (`survey --publish`, `egeria-reset`,
    `egeria-recheck`, `curate materialize`, `analysis run --publish`,
    `runs enqueue`) use this session; read-only commands keep working without
    one.
    """
    from resource_explorer.auth import login_with_password
    from resource_explorer.cli import session as cli_session

    from resource_explorer.egeria_clients import DaemonSignInRefused, is_daemon_user, refuse_daemon_sign_in

    if not user:
        from resource_explorer.config import get_config

        # Never the daemon's userId (EGERIA_USER_ID is RE's service account, Brief L).
        prefill = (get_config().egeria.login_default_user or "").strip()
        user = typer.prompt("Egeria user id", default=(None if not prefill or is_daemon_user(prefill) else prefill))
    try:
        refuse_daemon_sign_in(user)
    except DaemonSignInRefused as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from None
    password = typer.prompt("Password", hide_input=True)

    egeria_token = login_with_password(user, password)
    del password
    if not egeria_token:
        # One message for three causes (bad credentials, Egeria unreachable,
        # pyegeria missing): `login_with_password` cannot tell them apart, and
        # guessing one would be worse than naming all three.
        console.print(
            "[red]✗ Sign-in failed.[/red] Check the user id and password, and that the "
            "Egeria platform in .env (EGERIA_PLATFORM_URL/EGERIA_VIEW_SERVER) is reachable."
        )
        raise typer.Exit(1)

    record = cli_session.save_login(user, egeria_token)
    when = f" until {record.expires_at_local:%H:%M}" if record.expires_at_local else ""
    console.print(f"[green]✓ Signed in as {user}[/green]{when}")
    console.print(f"[dim]Session cached at {record.path} (mode 0600)[/dim]")


@app.command(name="logout")
def logout_command():
    """Forget the cached session.

    Local only, and honestly so: there is no server-side session to revoke —
    the Egeria bearer token inside the cached JWT stays valid at the platform
    until its own expiry. Deleting the file is the whole of what logout can do.
    """
    from resource_explorer.cli import session as cli_session

    if cli_session.clear_session():
        console.print("[green]✓ Signed out[/green] [dim](cached session removed)[/dim]")
    else:
        console.print("[dim]Nothing to do — no cached session.[/dim]")


@app.command()
def add(
    github_url: str = typer.Argument(help="GitHub repository URL to add"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept all proposed collections without prompting"),
    subpath: Optional[str] = typer.Option(None, "--subpath", help="Index only this subdirectory (for monorepos)"),
    name: Optional[str] = typer.Option(None, "--name", help="Project slug override (required when --subpath is used)"),
    extra_docs_path: list[str] = typer.Option(
        [], "--extra-docs-path",
        help="Repo-relative path (file or directory) outside --subpath to ingest as docs/examples. "
             "Repeat to add multiple paths, e.g. --extra-docs-path docs/guide.md --extra-docs-path examples/",
    ),
    from_local: Optional[str] = typer.Option(
        None, "--from-local",
        help="Path to a local clone of the repository. Skips the GitHub download — "
             "useful when registering multiple sub-projects from the same repo.",
    ),
    group: Optional[str] = typer.Option(None, "--group", help="Assign this repo to a project group"),
):
    """Add a GitHub project (runs onboarding wizard to detect content and plan ingestion)."""
    if subpath and not name:
        console.print("[red]--name is required when using --subpath[/red]")
        raise typer.Exit(1)
    if extra_docs_path and not subpath:
        console.print("[yellow]--extra-docs-path is only useful together with --subpath; ignoring.[/yellow]")
        extra_docs_path = []
    from resource_explorer.cli.wizard import OnboardingWizard
    wizard = OnboardingWizard()
    wizard.run(github_url, accept_all=yes, subproject_path=subpath, slug_override=name,
               extra_docs_paths=extra_docs_path or None, local_path=from_local,
               group_slug=group)


@app.command()
def remove(
    slug: str = typer.Argument(help="Project slug to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove a project and drop all its pgvector collections."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        console.print(f"[red]Project '{slug}' not found.[/red]")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Remove '{project.display_name}' and all its collections?", abort=True)
    from resource_explorer.vector_store_pg import MultiCollectionStore
    store = MultiCollectionStore()
    for collection in project.collections:
        store.drop_collection(collection)
    registry.remove(slug)
    console.print(f"[green]Removed {project.display_name}.[/green]")


@app.command(name="list")
def list_projects(
    details: bool = typer.Option(False, "--details", "-d", help="Show collection names and vector counts"),
):
    """List all registered projects and their status."""
    from resource_explorer.cli.formatters import print_project_table
    from resource_explorer.registry import ProjectRegistry
    projects = ProjectRegistry().list_all()
    print_project_table(projects, console, details=details)


@app.command()
def ask(
    query: str = typer.Argument(help="Question to ask"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Scope to a specific project slug"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass query cache"),
):
    """Ask a one-shot question about a project (or all projects)."""
    import hashlib
    from resource_explorer.rag_system import RAGSystem
    from resource_explorer.observability.feedback_collector import FeedbackCollector

    # When no explicit project is given, check for a fuzzy alias match
    if not project:
        project = _maybe_resolve_alias(query)

    system = RAGSystem()
    response = system.query(query, resource_slug=project)
    console.print(response)
    query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
    FeedbackCollector().prompt_and_collect(query_hash)


@app.command()
def chat(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Scope to a specific project slug"),
    session_id: Optional[str] = typer.Option(None, "--session-id", "-s", help="Resume a previous session by ID"),
):
    """Start an interactive multi-turn chat session."""
    from resource_explorer.cli.interactive import InteractiveSession
    InteractiveSession(resource_slug=project, session_id=session_id).run()


@app.command(name="add-docs")
def add_docs(
    slug: str = typer.Argument(help="Project slug"),
    docs_url: Optional[str] = typer.Option(None, "--docs-url", help="Documentation site URL to ingest as web_docs"),
    homepage_url: Optional[str] = typer.Option(None, "--homepage", help="Homepage URL to store on the project"),
):
    """Attach a documentation site or homepage to an already-registered project."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.vector_store_pg import MultiCollectionStore

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        console.print(f"[red]Project '{slug}' not found.[/red]")
        raise typer.Exit(1)

    if homepage_url:
        # Just update the metadata field — no ingestion needed
        import sqlite3
        conn = sqlite3.connect(registry.db_path)
        conn.execute("UPDATE projects SET homepage_url = ? WHERE slug = ?", (homepage_url, project.slug))
        conn.commit()
        conn.close()
        console.print(f"[green]Homepage URL updated for '{slug}'.[/green]")

    if docs_url:
        # Update stored docs_url
        import sqlite3
        conn = sqlite3.connect(registry.db_path)
        conn.execute("UPDATE projects SET docs_url = ? WHERE slug = ?", (docs_url, project.slug))
        conn.commit()
        conn.close()
        console.print(f"[cyan]Ingesting docs from {docs_url}...[/cyan]")
        _ingest_web_docs(project, docs_url, registry)

    if not docs_url and not homepage_url:
        console.print("[yellow]Provide at least --docs-url or --homepage.[/yellow]")
        raise typer.Exit(1)


def _ingest_web_docs(project, docs_url: str, registry) -> None:
    """Fetch a docs site via Docling and insert into the project's web_docs collection."""
    from resource_explorer.ingestion.doc_parser import DocParser, DocChunk
    from resource_explorer.ingestion.data_prep import DataPrep
    from resource_explorer.vector_store_pg import MultiCollectionStore
    from resource_explorer.configdata.collection_config import COLLECTION_TYPES

    ctype = COLLECTION_TYPES.get("web_docs")
    if not ctype:
        console.print("[red]web_docs collection type not configured.[/red]")
        return

    collection_name = f"{project.slug}_web_docs"
    parser = DocParser(ctype.chunk_size, ctype.chunk_overlap)

    try:
        chunks = parser.parse_url(docs_url, project.slug)
    except Exception as exc:
        console.print(f"[red]Failed to fetch docs:[/red] {exc}")
        return

    chunks = DataPrep().filter(chunks)
    if not chunks:
        console.print("[yellow]No content extracted from docs URL.[/yellow]")
        return

    store = MultiCollectionStore()
    count = store.insert(collection_name, [c.text for c in chunks], [c.metadata for c in chunks])

    collections = list(project.collections)
    if collection_name not in collections:
        collections.append(collection_name)
    registry.update_indexed_at(project.slug, collections)
    console.print(f"[green]Inserted {count} chunks into {collection_name}.[/green]")


@app.command()
def refresh(
    slugs: Optional[list[str]] = typer.Argument(
        default=None,
        help="Project slug(s) to re-index. Omit when using --all.",
    ),
    all_projects: bool = typer.Option(False, "--all", help="Re-index every registered project"),
    top_level: bool = typer.Option(
        False, "--top-level",
        help="With --all: skip sub-projects (those with a parent_slug set)",
    ),
    no_stats: bool = typer.Option(False, "--no-stats", help="Skip GitHub statistics update"),
    history: int = typer.Option(
        90, "--history", "-H",
        help="Days of commit history to fetch (default: 90). Use 365 for a full year.",
    ),
    symbols: bool = typer.Option(
        False, "--symbols",
        help="Extract (or re-extract) code symbols from source files.",
    ),
):
    """Incrementally re-index one or more projects and refresh GitHub statistics.

    Examples:

      resource-explorer refresh myproject
      resource-explorer refresh proj1 proj2 proj3
      resource-explorer refresh --all
      resource-explorer refresh --all --top-level   # skip sub-projects
    """
    from resource_explorer.ingestion.incremental import IncrementalIndexer
    from resource_explorer.query_cache import QueryCache
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    targets = _resolve_slugs(registry, slugs, all_projects, top_level)
    if not targets:
        raise typer.Exit(1)

    failed: list[str] = []
    for project in targets:
        slug = project.slug
        label = f"[bold]{project.display_name}[/bold]" + (
            f" [dim]({slug})[/dim]" if project.display_name != slug else ""
        )
        if project.parent_slug:
            label += f" [dim]← sub-project of {project.parent_slug}[/dim]"
        console.print(f"\n[cyan]Refreshing {label} …[/cyan]")
        try:
            indexer = IncrementalIndexer()
            indexer.refresh(project)
            dropped = QueryCache().invalidate_project(slug)
            if dropped:
                console.print(f"  [dim]Cleared {dropped} cached query result(s).[/dim]")
            if symbols:
                try:
                    from resource_explorer.ingestion.pipeline import IngestionPipeline
                    count = IngestionPipeline().extract_symbols_only(
                        slug, project.github_url, project.collections or []
                    )
                    console.print(
                        f"  [dim]Symbols: {count} extracted.[/dim]" if count
                        else "  [yellow]No code collections — symbols skipped.[/yellow]"
                    )
                except Exception as exc:
                    console.print(f"  [yellow]Symbol extraction failed: {exc}[/yellow]")
            if not no_stats:
                try:
                    from resource_explorer.github.stats_fetcher import StatsFetcher
                    result = StatsFetcher().fetch(slug, lookback_days=history)
                    if "commits_fetch_error" in result:
                        console.print(
                            f"  [yellow]Commit history warning:[/yellow] {result['commits_fetch_error']}"
                        )
                    else:
                        n = result.get("commits_fetched", 0)
                        console.print(f"  [dim]Stats updated ({n} commits, {history}d lookback).[/dim]")
                except Exception as exc:
                    console.print(f"  [dim]Stats update skipped: {exc}[/dim]")
            # Collections were proposed once, at onboarding, from the repo as
            # it stood then. Nothing re-evaluates them, so a repo that gains
            # PDFs keeps ignoring them forever. This is a query over the file
            # inventory the refresh just updated -- no scan, no extra fetch.
            # It reports only: enabling a collection changes what gets embedded
            # and costs real ingestion time, so that stays a human decision.
            try:
                from resource_explorer.collection_drift import detect_drift
                for finding in detect_drift(registry, project.slug):
                    console.print(
                        f"  [yellow]Eligible but not enabled — {finding.summary}[/yellow]"
                    )
                    for path in finding.sample_paths:
                        console.print(f"      [dim]{path}[/dim]")
            except Exception as exc:
                console.print(f"  [dim]Collection drift check skipped: {exc}[/dim]")
            console.print(f"  [green]✓ Done[/green]")
        except Exception as exc:
            console.print(f"  [red]✗ Failed: {exc}[/red]")
            failed.append(slug)

    _print_batch_summary(targets, failed, "refreshed")


@app.command()
def status():
    """Show environment health: services, projects, collection counts."""
    from resource_explorer.dashboard.terminal_dashboard import print_status
    print_status(console)


@app.command()
def tui():
    """Launch the full-screen Textual TUI (project sidebar + chat + feedback)."""
    from resource_explorer.tui.app import run
    run()


def _install_stack_dump(label: str) -> None:
    """On-demand thread dump, not continuous polling.

    Added 2026-09-04 to the `web` command after a real incident: the
    server stopped responding, a request that should take seconds hung
    for 35+ seconds, and there was no way to tell "genuinely stuck" from
    "slow" without guessing. faulthandler.dump_traceback_later() would
    dump every N seconds unconditionally, which is noise on a healthy
    process doing real (slow) work. register(SIGUSR1) is the standard
    low-overhead pattern instead: costs nothing while idle, and
    `kill -USR1 <pid>` writes every thread's exact Python stack frame to
    this process's stderr — the actual "what is it doing", not a theory
    reconstructed from timing.

    Factored out of `web` so `worker` gets the identical instrumentation
    rather than a second copy that drifts: docs/runtime-architecture-
    plan.md §2 makes "every process answers SIGUSR1 with a thread dump"
    a rule, not a patch on one command.
    """
    import faulthandler
    import signal

    # faulthandler writes to the real stderr file descriptor, so both calls
    # raise io.UnsupportedOperation when stderr is not a real file — a captured
    # stream under a test runner, or a container that redirected it oddly.
    # Instrumentation must never be the reason a role fails to start, so this
    # degrades to "no thread dump" rather than taking the process down.
    try:
        faulthandler.enable()
    except Exception as exc:
        console.print(f"[dim]thread dumps unavailable in this {label} process ({exc})[/dim]")
        return
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
        console.print(
            f"[dim]Stuck? kill -USR1 {os.getpid()} dumps every thread's stack "
            f"to this {label} process's stderr.[/dim]"
        )
    # SIGUSR1 doesn't exist on Windows — faulthandler.enable() alone
    # (fatal-signal dumps) still applies; just no on-demand trigger.


@app.command()
def worker(
    shutdown_timeout: float = typer.Option(
        15.0, help="Seconds to allow for a graceful stop before exiting anyway"),
):
    """Run the background worker role: scheduler (run-due + Egeria outbox
    drain + RFA reconciliation), bootstrap monitor, Egeria resync,
    orphaned-run reconciliation and the survey-definition cache warm.

    Each loop is gated on its own Postgres advisory lock, so running this
    alongside a `web --embed-worker` process (or a second worker) is safe:
    whoever wins a loop's lock runs it and the others log `standby` and
    retry. See docs/process-model.md for the keys and the loop inventory.
    """
    import signal
    import threading

    from resource_explorer.observability.logging_setup import configure_logging
    from resource_explorer.worker import run_worker

    configure_logging()
    console.print("[cyan]Starting worker role[/cyan]")
    _install_stack_dump("worker")

    stop_event = threading.Event()

    def _request_stop(signum, _frame) -> None:
        # Bounded shutdown, same reason `web` passes uvicorn
        # timeout_graceful_shutdown=10: during the incident SIGTERM was
        # ignored for 8+ seconds and needed a kill -9. run_worker joins
        # its supervisors within shutdown_timeout and returns regardless;
        # the loops are daemon threads, so anything still stuck goes with
        # the process rather than holding it open.
        console.print(f"[yellow]signal {signum} — stopping worker[/yellow]")
        stop_event.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    run_worker(embedded=False, stop_event=stop_event,
               shutdown_timeout=shutdown_timeout)
    console.print("[green]worker stopped[/green]")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8810, help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev mode)"),
    embed_worker: bool = typer.Option(
        True, "--embed-worker/--no-embed-worker",
        help="Run the worker role's background loops inside this web process "
             "(default: on, so `make dev` stays one command). Turn it off when "
             "a separate `resource-explorer worker` owns them."),
    workers: int = typer.Option(
        1, "--workers", help="uvicorn worker processes (ignored with --reload)"),
):
    """Start the web UI (FastAPI + HTML frontend with Plotly charts and markdown)."""

    import uvicorn
    console.print(f"[cyan]Starting web UI at http://{host}:{port}[/cyan]")

    # Whether this process also runs the worker role. Set in the
    # environment, not passed as an argument, because with --workers N
    # uvicorn re-imports the app in child processes that never see this
    # function's locals — web/app.py's _embed_worker_enabled() reads it
    # there. Also inherited by --reload's reloaded child.
    os.environ["EXPLORER_EMBED_WORKER"] = "true" if embed_worker else "false"
    if embed_worker:
        console.print(
            "[dim]Worker role embedded in this process. Background loops are "
            "leader-elected, so a second RE process stands by rather than "
            "double-firing.[/dim]"
        )
        if workers > 1:
            console.print(
                "[yellow]--workers %d with --embed-worker: each uvicorn worker "
                "starts the worker role and advisory locks decide which one "
                "actually runs each loop. That works, but the losers hold three "
                "idle supervisor threads and a registry connection apiece. Prefer "
                "--no-embed-worker plus a separate `resource-explorer worker` "
                "process.[/yellow]" % workers
            )
    else:
        console.print(
            "[dim]No embedded worker: background loops need a separate "
            "`resource-explorer worker` process.[/dim]"
        )

    # Instrumentation added 2026-09-04, after a real incident: the server
    # stopped responding, a request that should take seconds hung for 35+
    # seconds, and SIGTERM was ignored for 8+ seconds before a kill -9 was
    # needed. There was no way to tell "genuinely stuck" from "slow" without
    # guessing — this closes that gap two ways.
    #
    # 1. On-demand thread dump — see _install_stack_dump, shared with the
    #    `worker` command.
    _install_stack_dump("web")

    # 2. A bounded graceful shutdown. uvicorn's own default is to wait
    #    indefinitely for in-flight requests/threads on SIGTERM — which is
    #    exactly why the kill -9 was needed instead. This does NOT fix
    #    whatever caused a thread to be slow (see the log-based instrumentation
    #    in survey_definition_reader.py's find_candidate_process_guids_by_
    #    questions for that half), it just guarantees SIGTERM/Ctrl-C always
    #    actually exits within 10s.
    #
    # Configure before the server starts, and hand uvicorn a matching config so
    # server and application lines share one format and one destination. Without
    # log_config, uvicorn installs its own non-propagating handlers and its
    # output diverges from everything else's.
    from resource_explorer.observability.logging_setup import (
        configure_logging, uvicorn_log_config)
    configure_logging()
    # workers and reload are mutually exclusive in uvicorn (reload wins and
    # warns); pass workers only when it can actually take effect.
    extra = {} if (reload or workers <= 1) else {"workers": workers}
    try:
        uvicorn.run("resource_explorer.web.app:app", host=host, port=port, reload=reload,
                    log_config=uvicorn_log_config(), timeout_graceful_shutdown=10,
                    **extra)
    finally:
        # uvicorn's timeout_graceful_shutdown bounds the SERVER; it does
        # nothing about a shared-pool worker stuck in pyegeria, which the
        # interpreter would otherwise join for ever on the way out. Same
        # reason the worker command does this — see concurrency.py's
        # "Abandoned workers" section.
        from resource_explorer.concurrency import shutdown as _pool_shutdown

        _pool_shutdown(wait=False)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(DEFAULT_A2A_PORT, help="Bind port — ONE port for every agent"),
    base_port: Optional[int] = typer.Option(
        None, "--base-port",
        help="DEPRECATED alias for --port. Agents no longer occupy consecutive ports."),
    all_agents: bool = typer.Option(
        False, "--all",
        help="DEPRECATED and ignored — every agent is always served."),
    shutdown_timeout: float = typer.Option(
        10.0, help="Seconds to allow for a graceful stop before exiting anyway"),
):
    """Run the `a2a` process role: every agent on ONE port, behind a bearer token.

    Layout (see packages/resource-explorer/docs/a2a.md):

      /                                    orchestrator — the default agent
      /.well-known/agents.json             index of all seven agents + auth scheme
      /.well-known/agent-card.json         the orchestrator's A2A card
      /whoami                              who your token says you are
      /agents/<name>/                      one of stats, code, docs, health,
                                           compare, integration, orchestrator
      /agents/<name>/v1/message:send       its A2A REST endpoint
      /agents/<name>/jsonrpc/              its A2A JSON-RPC endpoint

    Every call needs `Authorization: Bearer <token>` — either a trellis app JWT
    or a raw Egeria bearer token. Cards and the index are readable without one.
    `A2A_ALLOW_ANONYMOUS=true` restores the pre-2026-09-04 no-auth behaviour for
    local development, and says so loudly at startup.

    Replaces the one-port-per-agent layout (8080-8086, no authentication) per
    docs/runtime-architecture-plan.md §2.
    """
    import signal
    import threading

    from resource_explorer.a2a_role import run_a2a
    from resource_explorer.observability.logging_setup import configure_logging

    if base_port is not None:
        console.print(
            "[yellow]--base-port is deprecated: every agent is now served on a "
            "single port with path routing, so there is no base to offset from. "
            f"Using it as --port ({base_port}). Switch to --port.[/yellow]"
        )
        port = base_port
    if all_agents:
        console.print(
            "[yellow]--all is deprecated and ignored: every agent is always "
            "served, on this one port under /agents/<name>.[/yellow]"
        )

    configure_logging()
    console.print(
        f"[cyan]Starting Resource Explorer a2a role on {host}:{port}[/cyan]\n"
        f"[dim]Discovery: http://{host}:{port}/.well-known/agents.json[/dim]"
    )
    _install_stack_dump("a2a")

    stop_event = threading.Event()

    def _request_stop(signum, _frame) -> None:
        # Bounded shutdown, same contract as `worker`: SIGTERM was ignored for
        # 8+ seconds during the 2026-09-03 incident and needed a kill -9.
        console.print(f"[yellow]signal {signum} — stopping a2a role[/yellow]")
        stop_event.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    run_a2a(host=host, port=port, stop_event=stop_event,
            shutdown_timeout=shutdown_timeout)
    console.print("[green]a2a role stopped[/green]")


def _resolve_slugs(registry, slugs, all_projects: bool, top_level: bool):
    """Return the list of Project objects to act on, validating inputs."""
    from resource_explorer.registry import ProjectRegistry  # already imported at call site, but safe
    if all_projects:
        projects = registry.list_all()
        if top_level:
            projects = [p for p in projects if not p.parent_slug]
        if not projects:
            console.print("[yellow]No projects registered.[/yellow]")
            return []
        return projects

    if not slugs:
        console.print("[red]Provide at least one slug or use --all.[/red]")
        return []

    result = []
    for slug in slugs:
        p = registry.get(slug)
        if p is None:
            console.print(f"[red]Project '{slug}' not found — skipping.[/red]")
        else:
            result.append(p)
    if not result:
        console.print("[red]No valid projects found.[/red]")
    return result


def _print_batch_summary(targets, failed: list[str], verb: str) -> None:
    """Print a one-liner batch completion summary only when >1 project was processed."""
    if len(targets) <= 1:
        return
    ok = len(targets) - len(failed)
    color = "green" if not failed else ("yellow" if ok else "red")
    console.print(
        f"\n[{color}]{verb.title()}: {ok}/{len(targets)} succeeded"
        + (f" — failed: {', '.join(failed)}" if failed else "")
        + f"[/{color}]"
    )


def _print_survey_report(result) -> None:
    """Print a full single-project survey report to the console."""
    from resource_explorer.surveyors.survey_report import AnnotationType
    console.print(f"\n[bold]Survey Report: {result.project_display_name}[/bold]")
    console.print(f"GitHub: {result.github_url}")
    if result.resource_slug != result.project_display_name:
        console.print(f"Slug:   {result.resource_slug}")
    console.print(f"Surveyed at: {result.surveyed_at.isoformat()}")
    console.print(f"Annotations: {len(result.annotations)}  |  Errors: {len(result.errors)}\n")

    for ann_type in AnnotationType:
        group = result.by_type(ann_type)
        if not group:
            continue
        console.print(f"[bold yellow]{ann_type.value}[/bold yellow] ({len(group)})")
        for ann in group:
            console.print(f"  • {ann.summary}")
            if ann.explanation:
                console.print(f"    [dim]{ann.explanation}[/dim]")
            # Show column-level schema for data file profiles
            if (
                ann_type == AnnotationType.SCHEMA_ANALYSIS
                and getattr(ann, "analysis_step", "") == "DataProfiling"
                and ann.json_properties.get("columns")
            ):
                cols = ann.json_properties["columns"]
                shown = cols[:8]
                parts = [f"{c['name']} ({c['dtype']})" for c in shown]
                if len(cols) > 8:
                    parts.append(f"+{len(cols) - 8} more")
                console.print(f"    [dim]Columns: {', '.join(parts)}[/dim]")
        console.print()

    if result.errors:
        console.print("[bold red]Survey errors:[/bold red]")
        for err in result.errors:
            console.print(f"  [red]• {err}[/red]")
        console.print()


def _print_survey_batch_summary(rows: list[dict], published: bool) -> None:
    """Print a Rich table summarising all projects surveyed in batch mode."""
    from rich.table import Table
    tbl = Table(
        "Project", "Annotations", "Errors",
        *( ["Report GUID"] if published else [] ),
        "Status",
        title="Survey Batch Summary",
        title_style="bold",
    )
    for r in rows:
        guid_short = (r["report_guid"] or "")[:12] + "…" if r["report_guid"] else "—"
        status = "[green]✓[/green]" if r["ok"] else "[red]✗[/red]"
        err_str = str(r["errors"]) if r["errors"] else "[dim]0[/dim]"
        row = [r["slug"], str(r["annotations"]), err_str]
        if published:
            row.append(guid_short)
        row.append(status)
        tbl.add_row(*row)
    console.print(tbl)


def _maybe_resolve_alias(query: str) -> Optional[str]:
    """
    Check for a fuzzy alias candidate before running a query.
    If found, prompt the user to confirm. If confirmed, saves the alias and returns the slug.
    """
    try:
        from resource_explorer.registry import ProjectRegistry
        registry = ProjectRegistry()
        result = registry.fuzzy_candidate(query)
        if not result:
            return None
        term, slug = result
        project = registry.get(slug)
        display = project.display_name if project else slug
        confirmed = typer.confirm(
            f'Did you mean "{display}"? Remember "{term}" as an alias for {slug}?',
            default=False,
        )
        if confirmed:
            registry.add_alias(term, slug)
            return slug
    except Exception:
        pass
    return None


# ── aliases sub-group ─────────────────────────────────────────────────────────


groups_app = typer.Typer(name="group", help="Group related resources under an umbrella project (e.g. 'egeria' containing egeria-python, egeria-advisor, and related DBs).")
app.add_typer(groups_app)


@groups_app.command(name="create")
def group_create(
    slug: str = typer.Argument(help="Group slug, e.g. 'egeria'"),
    display_name: str = typer.Option(..., "--name", help="Human-readable group name, e.g. 'Egeria'"),
    description: str = typer.Option("", "--desc", help="Optional description"),
):
    """Create (or rename) an umbrella project group."""
    from resource_explorer.registry import ProjectRegistry
    ProjectRegistry().create_group(slug, display_name, description)
    console.print(f"[green]Group '{slug}' created.[/green]")


@groups_app.command(name="list")
def group_list():
    """List all project groups with their member resources and aggregate stats."""
    from resource_explorer.registry import ProjectRegistry
    from rich.table import Table
    registry = ProjectRegistry()
    groups = registry.list_groups()
    if not groups:
        console.print("[dim]No groups defined yet. Create one with 'resource-explorer group create'.[/dim]")
        return

    table = Table(title="Project Groups", box=None, show_header=True, header_style="bold cyan")
    table.add_column("Group")
    table.add_column("Slug")
    table.add_column("Members", justify="right")
    table.add_column("Stars", justify="right")
    table.add_column("Commits (30d)", justify="right")
    table.add_column("Languages")

    for g in groups:
        stats = registry.get_group_aggregate_stats(g.slug)
        member_count = stats["project_count"] + stats["database_count"] + stats["filesystem_count"]
        
        langs = ", ".join(f"{l}({c})" for l, c in sorted(stats["languages"].items(), key=lambda x: x[1], reverse=True)[:3])
        
        table.add_row(
            g.display_name,
            g.slug,
            str(member_count),
            str(stats["stars"]),
            str(stats["commits_30d"]),
            langs or "[dim]none[/dim]"
        )
    console.print(table)


@groups_app.command(name="show")
def group_show(slug: str = typer.Argument(help="Group slug")):
    """Show member resources and aggregate stats for a single group."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    group = registry.get_group(slug)
    if not group:
        console.print(f"[red]Group '{slug}' not found.[/red]")
        raise typer.Exit(1)

    repos = registry.list_projects_in_group(slug)
    dbs = registry.list_databases_in_group(slug)
    fss = registry.list_filesystems_in_group(slug)
    stats = registry.get_group_aggregate_stats(slug)

    console.print(f"[bold]{group.display_name}[/bold] ({group.slug})")
    if group.description:
        console.print(f"[dim]{group.description}[/dim]")
    console.print()
    
    if repos:
        console.print(f"[bold cyan]Repositories ({len(repos)}):[/bold cyan]")
        for r in repos:
            console.print(f"  - {r.display_name} ({r.slug})")
    
    if dbs:
        console.print(f"[bold cyan]Databases ({len(dbs)}):[/bold cyan]")
        for db in dbs:
            console.print(f"  - {db.display_name} ({db.slug})")
            
    if fss:
        console.print(f"[bold cyan]Filesystems ({len(fss)}):[/bold cyan]")
        for fs in fss:
            console.print(f"  - {fs.display_name} ({fs.slug})")

    console.print(f"\n[bold cyan]Aggregate Stats:[/bold cyan]")
    console.print(f"  Stars: {stats['stars']} | Forks: {stats['forks']}")
    console.print(f"  Commits (30d/90d): {stats['commits_30d']} / {stats['commits_90d']}")
    console.print(f"  Contributors (max): {stats['contributors_count']}")
    console.print(f"  DB Assets: {stats['db_schema_count']} schemas, {stats['db_table_count']} tables")
    console.print(f"  FS Assets: {stats['fs_file_count']} files")


@groups_app.command(name="assign")
def group_assign(
    resource_type: str = typer.Argument(..., help="Type of resource: repo, database, or filesystem"),
    resource_slug: str = typer.Argument(..., help="Slug of the resource to assign"),
    group_slug: str = typer.Argument(..., help="Group slug to assign it to"),
):
    """Assign a registered resource to a group. Needs a signed-in session: the group is a Folio in Egeria, so the
    change is checked like the web's (Brief T round 2) and reaches Egeria at the resource's next sync."""
    from resource_explorer.cli import session as cli_session
    from resource_explorer.registry import ProjectRegistry
    cli_session.require_and_activate(console)
    registry = ProjectRegistry()
    
    if not registry.get_group(group_slug):
        console.print(f"[red]Group '{group_slug}' not found. Create it first with 'group create'.[/red]")
        raise typer.Exit(1)

    if resource_type == "repo":
        if not registry.get(resource_slug):
            console.print(f"[red]Repository '{resource_slug}' not found.[/red]")
            raise typer.Exit(1)
        setter = registry.set_project_group
    elif resource_type == "database":
        if not registry.get_database(resource_slug, allow_unreadable=True):
            console.print(f"[red]Database '{resource_slug}' not found.[/red]")
            raise typer.Exit(1)
        setter = registry.set_database_group
    elif resource_type == "filesystem":
        if not registry.get_filesystem(resource_slug):
            console.print(f"[red]Filesystem '{resource_slug}' not found.[/red]")
            raise typer.Exit(1)
        setter = registry.set_filesystem_group
    else:
        console.print(f"[red]Invalid resource type '{resource_type}'. Use 'repo', 'database', or 'filesystem'.[/red]")
        raise typer.Exit(1)

    _refuse_group_change(registry, resource_type, resource_slug, group_slug)
    setter(resource_slug, group_slug)
    console.print(f"[green]{resource_type} '{resource_slug}' → group '{group_slug}'.[/green] "
                  "Egeria's Folio follows at the resource's next sync.")


def _refuse_group_change(registry, resource_type: str, resource_slug: str, group_slug: str) -> None:
    """Exit 1 with the reason when curation access refuses the group change (the web route's check)."""
    from resource_explorer.curation_egeria import group_change_refusal

    why = group_change_refusal(registry, resource_type, resource_slug, group_slug)
    if why:
        console.print(f"[red]not permitted · {why}[/red]")
        raise typer.Exit(1)


@groups_app.command(name="unassign")
def group_unassign(
    resource_type: str = typer.Argument(..., help="Type of resource: repo, database, or filesystem"),
    resource_slug: str = typer.Argument(..., help="Slug of the resource to unassign"),
):
    """Remove a resource from whatever group it belongs to. Needs a signed-in session (see `group assign`)."""
    from resource_explorer.cli import session as cli_session
    from resource_explorer.registry import ProjectRegistry
    cli_session.require_and_activate(console)
    registry = ProjectRegistry()

    from resource_explorer.resource_types import SURVEYED_RESOURCE_TYPES
    if resource_type in SURVEYED_RESOURCE_TYPES:
        _refuse_group_change(registry, resource_type, resource_slug, "")
    if resource_type == "repo":
        registry.set_project_group(resource_slug, "")
    elif resource_type == "database":
        registry.set_database_group(resource_slug, "")
    elif resource_type == "filesystem":
        registry.set_filesystem_group(resource_slug, "")
    else:
        console.print(f"[red]Invalid resource type '{resource_type}'. Use 'repo', 'database', or 'filesystem'.[/red]")
        raise typer.Exit(1)

    console.print(f"[green]{resource_type} '{resource_slug}' ungrouped.[/green]")


@groups_app.command(name="remove")
def group_remove(
    slug: str = typer.Argument(help="Group slug to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a group. Member resources are ungrouped, not removed. Needs a signed-in session: every member leaves
    the group's Folio in Egeria (at its next sync), so each is checked first; the Folio itself is never deleted."""
    from resource_explorer.cli import session as cli_session
    from resource_explorer.curation_egeria import group_delete_refusals, refusal_words
    from resource_explorer.registry import ProjectRegistry
    cli_session.require_and_activate(console)
    registry = ProjectRegistry()
    if not registry.get_group(slug):
        console.print(f"[red]Group '{slug}' not found.[/red]")
        raise typer.Exit(1)
    refused = group_delete_refusals(registry, slug)
    if refused:
        console.print(f"[red]{refusal_words(refused)}[/red]")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Delete group '{slug}'? Member resources will be ungrouped.", abort=True)
    unassigned = registry.delete_group(slug)
    console.print(f"[green]Group '{slug}' removed ({unassigned} resource(s) ungrouped).[/green]")


aliases_app = typer.Typer(name="aliases", help="Manage project name aliases.")
app.add_typer(aliases_app)


@aliases_app.command(name="list")
def aliases_list(
    slug: Optional[str] = typer.Argument(None, help="Project slug (omit to list all)"),
):
    """List stored aliases, optionally filtered to one project."""
    from resource_explorer.registry import ProjectRegistry
    rows = ProjectRegistry().list_aliases(slug)
    if not rows:
        console.print("[dim]No aliases found.[/dim]")
        return
    from rich.table import Table
    tbl = Table("Alias", "Project", "Source", "Created")
    for r in rows:
        tbl.add_row(r["alias"], r["project_slug"], r["confirmed_by"], r["created_at"][:10])
    console.print(tbl)


@aliases_app.command(name="add")
def aliases_add(
    alias: str = typer.Argument(help='Alias phrase, e.g. "Egeria Platform"'),
    slug: str = typer.Argument(help="Project slug to map to"),
):
    """Add a name alias for a project."""
    from resource_explorer.registry import ProjectRegistry
    registry = ProjectRegistry()
    if not registry.exists(slug):
        console.print(f"[red]Project '{slug}' not found.[/red]")
        raise typer.Exit(1)
    registry.add_alias(alias, slug)
    console.print(f'[green]Alias "{alias}" → {slug} saved.[/green]')


@aliases_app.command(name="remove")
def aliases_remove(
    alias: str = typer.Argument(help="Alias to remove"),
):
    """Remove a stored alias."""
    from resource_explorer.registry import ProjectRegistry
    removed = ProjectRegistry().remove_alias(alias)
    if removed:
        console.print(f'[green]Alias "{alias}" removed.[/green]')
    else:
        console.print(f'[yellow]Alias "{alias}" not found.[/yellow]')


repair_app = typer.Typer(
    name="repair",
    help="Repair a repo's registration: rename it, fix its github_url, enable a "
         "drift-flagged collection, or fix an investigation membership.",
)
app.add_typer(repair_app)


@repair_app.command(name="rename")
def repair_rename(
    slug: str = typer.Argument(help="Current repo slug"),
    new_slug: str = typer.Argument(help="New slug"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Rename a repo's slug — across the registry AND its pgvector collections.

    See docs/repair-operations-design.md for exactly what this moves and
    what it refuses. Shared collections another repo also lists are left
    untouched; only this repo's own collections are renamed.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.repair import RepairError, rename_repo
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        console.print(f"[red]Repo '{slug}' not found.[/red]")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(
            f"Rename '{slug}' to '{new_slug}'? This moves {len(project.collections)} "
            f"pgvector collection(s) and every registry row that references it.",
            abort=True,
        )
    try:
        result = rename_repo(slug, new_slug, registry=registry)
    except RepairError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Renamed '{result.old_slug}' -> '{result.new_slug}'.[/green]")
    for line in result.renamed_collections:
        console.print(f"  [dim]collection: {line}[/dim]")
    if result.unchanged_shared_collections:
        console.print(f"  [dim]shared collections left unchanged: {result.unchanged_shared_collections}[/dim]")


@repair_app.command(name="mark-false-zero-surveys")
def repair_mark_false_zero_surveys(
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually mark the rows. Without it this is a DRY RUN that only prints what it would mark."),
):
    """Mark false-zero database survey rows invalid (never deletes them).

    Finds rows whose source is a publish source ('egeria-published') and whose
    schema_info is empty, for ANY database — the damage left by a publish step
    that wrote a survey row with nothing measured. DRY RUN by default: prints
    slug, timestamp, source and reason per row plus a count, writes nothing.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.repair import (
        apply_false_zero_survey_repair, format_false_zero_plan, plan_false_zero_survey_repair)
    registry = ProjectRegistry()
    plan = plan_false_zero_survey_repair(registry)
    if apply:
        apply_false_zero_survey_repair(plan, registry)
    console.print(format_false_zero_plan(plan, apply=apply), markup=False, highlight=False)


@repair_app.command(name="set-github-url")
def repair_set_github_url(
    slug: str = typer.Argument(help="Repo slug"),
    new_url: str = typer.Argument(help="Correct github_url"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm dropping existing collections"),
):
    """Correct a repo's github_url. Drops its existing collections (they were
    embedded from the wrong repo) and leaves it needing a re-index — run
    `resource-explorer refresh <slug>` afterward."""
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.repair import RepairError, change_github_url
    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        console.print(f"[red]Repo '{slug}' not found.[/red]")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(
            f"Change github_url for '{slug}' from {project.github_url!r} to {new_url!r}? "
            f"This drops all {len(project.collections)} existing collection(s); "
            f"you'll need to re-index afterward.",
            abort=True,
        )
    try:
        result = change_github_url(slug, new_url, confirm=True, registry=registry)
    except RepairError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]'{slug}' now points at {result['new_url']}.[/green]")
    console.print(f"  [yellow]Dropped {len(result['dropped_collections'])} collection(s) — run "
                  f"'resource-explorer refresh {slug}' to re-index.[/yellow]")


@repair_app.command(name="enable-collection")
def repair_enable_collection(
    slug: str = typer.Argument(help="Repo slug"),
    collection_type: str = typer.Argument(help="Collection type name, e.g. 'pdfs' (see collection_drift output)"),
):
    """Turn on a collection type collection_drift flagged as eligible but not enabled."""
    from resource_explorer.repair import RepairError, enable_collection
    try:
        result = enable_collection(slug, collection_type)
    except RepairError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Enabled '{result['collection']}' — {result['chunks_inserted']} chunk(s) inserted.[/green]")


@repair_app.command(name="list-memberships")
def repair_list_memberships(slug: str = typer.Argument(help="Repo slug")):
    """Which investigations this repo is a Folio member of."""
    from resource_explorer.repair import RepairError, list_repo_investigation_memberships
    try:
        rows = list_repo_investigation_memberships(slug)
    except RepairError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if not rows:
        console.print(f"[dim]'{slug}' is not a member of any investigation.[/dim]")
        return
    from rich.table import Table
    tbl = Table("Investigation", "Status", "State", "Rationale")
    for r in rows:
        tbl.add_row(r["investigation_slug"], r["status"], r["state"], r["membership_rationale"])
    console.print(tbl)


@repair_app.command(name="repoint-membership")
def repair_repoint_membership(
    slug: str = typer.Argument(help="Repo slug"),
    from_investigation: str = typer.Argument(help="Investigation slug to remove membership from"),
    to_investigation: str = typer.Argument(help="Investigation slug to add membership to"),
):
    """Move a repo's investigation membership from one investigation to another."""
    from resource_explorer.repair import RepairError, repoint_investigation_member
    try:
        result = repoint_investigation_member(slug, from_investigation, to_investigation)
    except RepairError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]'{slug}' moved from '{from_investigation}' to '{to_investigation}'.[/green]")


@repair_app.command(name="drop-membership")
def repair_drop_membership(
    slug: str = typer.Argument(help="Repo slug"),
    investigation_slug: str = typer.Argument(help="Investigation slug to drop membership from"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Drop a repo from one investigation's Folio."""
    from resource_explorer.repair import RepairError, drop_investigation_member
    if not yes:
        typer.confirm(f"Drop '{slug}' from investigation '{investigation_slug}'?", abort=True)
    try:
        drop_investigation_member(slug, investigation_slug)
    except RepairError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]'{slug}' dropped from '{investigation_slug}'.[/green]")


@app.command()
def survey(
    slugs: Optional[list[str]] = typer.Argument(
        default=None,
        help="Project slug(s) to survey. Omit when using --all.",
    ),
    all_projects: bool = typer.Option(False, "--all", help="Survey every registered project"),
    top_level: bool = typer.Option(
        False, "--top-level",
        help="With --all: skip sub-projects (those with a parent_slug set)",
    ),
    publish: bool = typer.Option(False, "--publish", help="Push the survey report to Egeria"),
    refresh_cache: bool = typer.Option(False, "--refresh", help="Force-refresh the file type cache from Egeria before surveying"),
    platform_url: Optional[str] = typer.Option(None, "--egeria-url", help="Egeria platform URL (overrides EGERIA_PLATFORM_URL env var)"),
    view_server: Optional[str] = typer.Option(None, "--egeria-server", help="Egeria view server name (overrides EGERIA_VIEW_SERVER)"),
    data_path: Optional[str] = typer.Option(None, "--data-path", help="Path to a local clone for CSV/Parquet column-level profiling"),
):
    """Survey one or more projects and produce Egeria-aligned annotation reports.

    Without --publish: prints the survey as a markdown report (no Egeria required).
    With --publish: also pushes to Egeria.  Sub-projects that share a GitHub URL
    with their parent will share the same repository Asset in Egeria.

    Examples:

      resource-explorer survey myproject
      resource-explorer survey proj1 proj2 proj3
      resource-explorer survey --all
      resource-explorer survey --all --top-level     # skip sub-projects
      resource-explorer survey --all --publish       # survey + publish all
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator
    from resource_explorer.surveyors.survey_report import AnnotationType

    registry = ProjectRegistry()
    targets = _resolve_slugs(registry, slugs, all_projects, top_level)
    if not targets:
        raise typer.Exit(1)

    # A publish writes to Egeria on behalf of a person, so it needs one. A
    # bare `survey` prints a markdown report from local data and deliberately
    # does not — gating it would make the cheapest, most-used command depend
    # on a live platform for no reason (plan §4, "read-only commands keep
    # working without").
    identity = None
    if publish:
        from resource_explorer.cli import session as cli_session

        identity = cli_session.require_and_activate(console)

    batch = len(targets) > 1

    # Build Egeria client once (if needed) and reuse across projects
    pyegeria_client = None
    if refresh_cache or publish:
        pyegeria_client = _try_build_egeria_client(platform_url, view_server)

    publisher = None
    if publish:
        from resource_explorer.surveyors.egeria_publisher import EgeriaConnectionError, EgeriaPublisher
        try:
            publisher = EgeriaPublisher(
                platform_url=platform_url,
                view_server=view_server,
                registry=registry,
            )
        except EgeriaConnectionError as exc:
            console.print(f"[red]Egeria connection failed: {exc}[/red]")
            raise typer.Exit(1)

    orchestrator = SurveyOrchestrator(
        registry=registry,
        pyegeria_client=pyegeria_client,
        force_refresh=refresh_cache,
        data_path=data_path,
    )

    failed: list[str] = []
    survey_rows: list[dict] = []   # for batch summary table

    for project in targets:
        slug = project.slug
        label = f"[bold]{project.display_name}[/bold]"
        if project.parent_slug:
            label += f" [dim](sub-project of {project.parent_slug}, path: {project.subproject_path or '/'})[/dim]"
        console.print(f"\n[cyan]Surveying {label} …[/cyan]")

        try:
            result = orchestrator.run(slug)
        except Exception as exc:
            console.print(f"  [red]✗ Survey failed: {exc}[/red]")
            failed.append(slug)
            survey_rows.append({"slug": slug, "annotations": 0, "errors": 1, "report_guid": None, "ok": False})
            try:
                from resource_explorer.activity_logger import log_survey
                log_survey(
                    registry,
                    entity_type="repo",
                    entity_slug=project.slug,
                    entity_name=project.display_name,
                    entity_location=project.github_url,
                    intent="assessment",
                    status="error",
                    summary="Survey failed",
                    detail=str(exc),
                )
            except Exception:
                pass
            continue

        # ── Per-project report (condensed in batch mode) ──────────────────
        if not batch:
            _print_survey_report(result)
        else:
            ann_counts = {t.value: len(result.by_type(t)) for t in AnnotationType if result.by_type(t)}
            summary = ", ".join(f"{v} {k}" for k, v in ann_counts.items())
            status_str = f"[green]✓[/green]" if not result.errors else f"[yellow]⚠ {len(result.errors)} error(s)[/yellow]"
            console.print(f"  {status_str} {len(result.annotations)} annotations — {summary}")
            if result.errors:
                for err in result.errors[:3]:
                    console.print(f"    [dim red]{err}[/dim red]")

        # ── Egeria publish ────────────────────────────────────────────────
        report_guid = None
        refused = _resource_write_refusal(registry, "repo", project.slug) if publisher else ""
        if refused:                       # Brief Z2: this repository is skipped, the rest proceed
            console.print(f"  [yellow]{refused}[/yellow]")
        if publisher and not refused:
            from resource_explorer.private_publish import PrivateAwaitsOwner, record_waiting

            try:
                console.print(f"  [dim]Publishing to Egeria…[/dim]")
                # An explicit publish waits for Egeria (RUNS_PUBLISH_INLINE governs a run's auto-publish only).
                report_guid = publisher.publish(result, defer_drain=False)
                console.print(f"  [green]SurveyReport GUID: {report_guid}[/green]")
            except PrivateAwaitsOwner as exc:
                # Waiting, not failed (2026-10-13 review): only the private investigation's owner can publish it.
                console.print(f"  [yellow]{exc}[/yellow]")
                record_waiting(registry, exc, run_key=result.surveyed_at.isoformat(), source="cli survey",
                               name=project.display_name, location=project.github_url)
            except Exception as exc:
                console.print(f"  [red]Egeria publish failed: {exc}[/red]")
                try:
                    from resource_explorer.activity_logger import log_catalog
                    log_catalog(
                        registry,
                        entity_type="repo",
                        entity_slug=project.slug,
                        entity_name=project.display_name,
                        entity_location=project.github_url,
                        status="error",
                        summary="Egeria publish failed",
                        detail=str(exc),
                    )
                except Exception:
                    pass
                if not batch:
                    raise typer.Exit(1)
                failed.append(f"{slug}(publish)")

        survey_rows.append({
            "slug": slug,
            "annotations": len(result.annotations),
            "errors": len(result.errors),
            "report_guid": report_guid,
            "ok": slug not in failed,
        })

    if batch:
        _print_survey_batch_summary(survey_rows, publish)
    elif not failed and publish and not batch:
        # Single-project governance action prompt (batch skips this)
        catalog = typer.confirm(
            "\nTrigger governance action process to catalog this asset in Egeria?",
            default=False,
        )
        if catalog:
            console.print(
                "[yellow]Governance action integration is not yet implemented — "
                "watch for this in a future release.[/yellow]"
            )

    _print_batch_summary(targets, failed, "surveyed")


@app.command(name="survey-definition")
def survey_definition(
    slug: str = typer.Argument(help="Project slug to survey"),
    survey_def: Optional[str] = typer.Option(
        None, "--survey-definition", "-s",
        help="Name/qualifiedName of the Survey Definition (GovernanceActionProcess) in Egeria. "
             "If omitted, RE looks up candidates by Technology Type.",
    ),
    refresh_definition: bool = typer.Option(
        False, "--refresh-definition", help="Ignore any cached Survey Definition GUID and re-resolve"
    ),
    egeria_url: Optional[str] = typer.Option(None, "--egeria-url", help="Egeria platform URL override"),
    egeria_server: Optional[str] = typer.Option(None, "--egeria-server", help="Egeria view server name override"),
    engine: Optional[str] = typer.Option(
        None, "--engine",
        help="Force which engine runs this definition's 'resource-explorer'-tagged "
             "steps for THIS run only: 'resource-explorer' (local) or 'prefect'. "
             "Omit to use the configured behaviour (PREFECT_ROUTE_LOCAL_STEPS). "
             "Does not affect executes_at='egeria' steps.",
    ),
):
    """Execute a Survey Definition authored in Egeria against a registered project.

    Fetches the Survey Definition from Egeria, runs each step tagged
    executes_at=resource-explorer using the matching existing sub-surveyor
    (repo_health, repo_security, etc.), and publishes results back to Egeria via
    the existing, already-narrow EgeriaPublisher.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        run_survey_definition,
    )
    from resource_explorer.surveyors.survey_definition_reader import (
        SurveyDefinitionReaderError,
        UnsupportedSurveyDefinitionError,
    )

    registry = ProjectRegistry()
    project = registry.get(slug)
    if not project:
        console.print(f"[red]Project '{slug}' not found.[/red]")
        raise typer.Exit(1)

    if engine is not None and engine not in ("resource-explorer", "prefect"):
        console.print(f"[red]✗ --engine must be 'resource-explorer' or 'prefect' — got {engine!r}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Running Survey Definition for '{slug}'...[/cyan]")
    try:
        results = run_survey_definition(
            entity_type="repo",
            slug=slug,
            registry=registry,
            survey_definition_ref=survey_def,
            refresh_definition=refresh_definition,
            engine_override=engine,
        )
    except UnsupportedSurveyDefinitionError as exc:
        console.print(f"[red]✗ Unsupported Survey Definition: {exc}[/red]")
        raise typer.Exit(1)
    except (SurveyDefinitionExecutorError, SurveyDefinitionReaderError) as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]✗ Survey Definition run failed: {exc}[/red]")
        raise typer.Exit(1)

    if results.get("errors"):
        console.print("[red]✗ Completed with errors:[/red]")
        for err in results["errors"]:
            console.print(f"  {err}")
        for step in results.get("steps", []):
            console.print(f"  [dim]{step['step']}: {step['status']}[/dim]")
        raise typer.Exit(1)

    console.print(f"[green]✓ Survey Definition completed: {results.get('process_qualified_name', '')}[/green]")
    for step in results.get("steps", []):
        console.print(f"  {step['step']}: {step['status']}")
    if results.get("egeria_report_guid"):
        console.print(f"  Report GUID: {results['egeria_report_guid']}")


@app.command(name="egeria-reset")
def egeria_reset(
    slugs: Optional[list[str]] = typer.Argument(default=None, help="Project slug(s) to reset"),
    all_projects: bool = typer.Option(False, "--all", help="Reset all registered projects"),
):
    """Clear cached Egeria GUIDs and survey history for one or more projects.

    Use this after resetting the Egeria database so that the next
    'survey --publish' re-registers each project from scratch.

    Examples:
      resource-explorer egeria-reset egeria
      resource-explorer egeria-reset egeria beeai_framework
      resource-explorer egeria-reset --all
    """
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()

    if all_projects:
        targets = [p.slug for p in registry.list()]
    elif slugs:
        targets = list(slugs)
    else:
        console.print("[red]Provide one or more project slugs, or use --all.[/red]")
        raise typer.Exit(1)

    if not targets:
        console.print("[yellow]No registered projects found.[/yellow]")
        raise typer.Exit(0)

    from rich.table import Table
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Project", min_width=20)
    table.add_column("GUID cleared", justify="center", min_width=13)
    table.add_column("Survey records deleted", justify="right", min_width=22)

    for slug in targets:
        if not registry.exists(slug):
            console.print(f"[yellow]Project '{slug}' not found — skipping.[/yellow]")
            continue
        result = registry.clear_egeria_registration(slug)
        guid_str = "[green]yes[/green]" if result["asset_guid_cleared"] else "[dim]none[/dim]"
        table.add_row(slug, guid_str, str(result["surveys_deleted"]))

    console.print(table)
    console.print(
        "\n[dim]Run [bold]resource-explorer survey <slug> --publish[/bold] "
        "to re-register and publish a fresh survey.[/dim]"
    )


@app.command(name="egeria-reports")
def egeria_reports(
    slug: str = typer.Argument(help="Project slug"),
    full: bool = typer.Option(False, "--full", help="Show all annotations for the latest survey"),
    platform_url: Optional[str] = typer.Option(None, "--egeria-url", help="Egeria platform URL (overrides EGERIA_PLATFORM_URL env var)"),
    view_server: Optional[str] = typer.Option(None, "--egeria-server", help="Egeria view server name (overrides EGERIA_VIEW_SERVER)"),
):
    """Show Egeria survey reports published for a project.

    Reads from the local registry by default (no Egeria connection needed).
    Use --full to fetch and display all annotations from Egeria for the latest survey.
    """
    from resource_explorer.cli import session as cli_session
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.egeria_reader import EgeriaReader, EgeriaReaderError

    # `--full` is the branch that actually reaches Egeria; without it this is a
    # registry read and stays usable with no session (and with no platform).
    if full:
        cli_session.require_and_activate(console)

    registry = ProjectRegistry()
    project = registry.get(slug)
    if project is None:
        console.print(f"[red]Project '{slug}' not found.[/red]")
        raise typer.Exit(1)

    reader = EgeriaReader(
        platform_url=platform_url,
        view_server=view_server,
        registry=registry,
    )

    # ── Asset registration status ─────────────────────────────────────────────
    asset_guid = registry.get_egeria_asset_guid(slug)
    console.print(f"\n[bold]Egeria surveys for {project.display_name}[/bold]")
    if asset_guid:
        console.print(f"Asset GUID : [cyan]{asset_guid}[/cyan]")
    else:
        console.print("Asset GUID : [dim]not registered[/dim]")
    console.print(f"Platform   : {reader.platform_url}\n")

    # ── Survey history (from local registry) ──────────────────────────────────
    surveys = reader.get_survey_reports_from_registry(slug)

    if not surveys:
        console.print("[yellow]No surveys published to Egeria yet for this project.[/yellow]")
        console.print("Run [bold]resource-explorer survey " + slug + " --publish[/bold] to publish one.")
        raise typer.Exit(0)

    # Build table
    from rich.table import Table
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=3)
    table.add_column("Surveyed at", min_width=22)
    table.add_column("Annotations", justify="right", min_width=11)
    table.add_column("Egeria Report GUID")

    for i, row in enumerate(surveys, start=1):
        suffix = "  ← latest" if i == 1 else ""
        count = str(row.get("annotation_count") or "—")
        table.add_row(
            str(i),
            row.get("surveyed_at", "—"),
            count,
            f"[cyan]{row.get('egeria_report_guid', '—')}[/cyan]{suffix}",
        )

    console.print(table)

    if not full:
        console.print("\nRun with [bold]--full[/bold] to display all annotations for the latest survey.")
        raise typer.Exit(0)

    # ── Full annotation display for the latest survey ─────────────────────────
    latest = surveys[0]
    report_guid = latest.get("egeria_report_guid", "")
    surveyed_at = latest.get("surveyed_at", "")

    console.print(f"\n[bold]Annotations for survey: {surveyed_at}[/bold]")
    console.print(f"Report GUID: [cyan]{report_guid}[/cyan]\n")

    try:
        annotations = reader.get_annotations(slug, surveyed_at)
    except EgeriaReaderError as exc:
        console.print(f"[red]Could not connect to Egeria: {exc}[/red]")
        console.print("[dim]Hint: set EGERIA_PLATFORM_URL in .env to enable full annotation display.[/dim]")
        raise typer.Exit(1)

    if not annotations:
        console.print("[yellow]No annotations found in Egeria for this survey.[/yellow]")
        raise typer.Exit(0)

    # Group by annotation_type
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for ann in annotations:
        groups[ann.get("annotation_type", "Unknown")].append(ann)

    for ann_type, group in sorted(groups.items()):
        console.print(f"[bold yellow]{ann_type}[/bold yellow] ({len(group)})")
        for ann in group:
            summary = ann.get("summary", "")
            confidence = ann.get("confidence")
            line = f"  • {summary}"
            if confidence is not None:
                line += f"  [dim][confidence: {confidence}][/dim]"
            console.print(line)
            explanation = ann.get("explanation", "")
            if explanation:
                console.print(f"    [dim]{explanation}[/dim]")
            # Show key subtype fields inline
            sub = ann.get("subtype_data", {})
            if sub.get("actionRequested"):
                console.print(f"    [yellow]Action: {sub['actionRequested']}[/yellow]")
            if sub.get("qualityScores"):
                scores = "  ".join(f"{k}={v}" for k, v in sub["qualityScores"].items())
                console.print(f"    [dim]Scores: {scores}[/dim]")
        console.print()


# ── database sub-group ────────────────────────────────────────────────────────

database_app = typer.Typer(name="database", help="Manage database entities and surveys.")
app.add_typer(database_app)


@database_app.command(name="register")
def database_register(
    slug: str = typer.Argument(help="Unique identifier for this database"),
    db_type: str = typer.Option(..., "--type", help="Database type (postgresql, mysql, etc.)"),
    host: str = typer.Option(..., "--host", help="Database host"),
    port: int = typer.Option(..., "--port", help="Database port"),
    database: str = typer.Option(..., "--database", help="Database name"),
    display_name: Optional[str] = typer.Option(None, "--name", help="Display name (defaults to slug)"),
    description: str = typer.Option("", "--description", help="Database description"),
    group: Optional[str] = typer.Option(None, "--group", help="Assign this database to a project group"),
):
    """Register a database in the registry.
    
    Example:
        resource-explorer database register my-postgres \\
            --type postgresql \\
            --host localhost \\
            --port 5432 \\
            --database mydb \\
            --name "My PostgreSQL Database" \\
            --group egeria
    """
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry
    
    registry = ProjectRegistry()
    
    # Check if already exists
    if registry.database_exists(slug):
        console.print(f"[red]Database '{slug}' already registered.[/red]")
        raise typer.Exit(1)
    
    # Create database entity
    db_entity = DatabaseEntity(
        slug=slug,
        display_name=display_name or slug,
        db_type=db_type,
        host=host,
        port=port,
        database_name=database,
        description=description,
        group_slug=group or "",
    )
    
    # Register
    registry.register_database(db_entity)
    console.print(f"[green]✓ Database '{slug}' registered successfully.[/green]")
    console.print(f"  Type: {db_type}")
    console.print(f"  Host: {host}:{port}")
    console.print(f"  Database: {database}")


@database_app.command(name="update-credentials")
def database_update_credentials(
    slug: str = typer.Argument(help="Database slug to update"),
    user: str = typer.Option(..., "--user", "-u", help="New database username"),
    password: str = typer.Option(..., "--password", "-p", prompt=True, help="New database password (prompted, not echoed, when omitted)", hide_input=True),
):
    """Update the stored db_user/db_password for an already-registered database.

    The registration itself (slug, egeria_asset_guid, survey history) is
    untouched -- this only repoints which role/password future surveys connect
    with. The new credential is tested against the database first; if the
    connection fails nothing is stored and the exit code is non-zero.

    Example:
        resource-explorer database update-credentials <slug> --user <role>

    The password is prompted for.
    """
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()

    if not registry.database_exists(slug):
        console.print(f"[red]Database '{slug}' not found. Register it first with 'database register'.[/red]")
        raise typer.Exit(1)

    from rich.markup import escape

    from resource_explorer.credential_check import (
        CredentialCheckError,
        check_database_credential,
    )

    try:
        check_database_credential(registry.get_database(slug, allow_unreadable=True), user, password)
    except CredentialCheckError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1)

    registry.update_database_credentials(slug, user, password)

    # Project the same credential into the .omsecrets file, in the same
    # operation as the registry write — design REPLY-DATABASE-CREDENTIAL-
    # CAPABILITY-VISIBILITY.md §7. A no-op when no local .omsecrets path is
    # configured (EGERIA_SECRETS_STORE_LOCAL_PATH unset).
    from resource_explorer.omsecrets_store import secrets_collection_name, write_credential

    write_credential(secrets_collection_name(slug), user, password)

    console.print(f"[green]✓ Credentials updated for database '{slug}'.[/green]")
    console.print(f"  User: {user}")


@database_app.command(name="reproject-secrets")
def database_reproject_secrets(
    slug: str = typer.Argument(None, help="Database slug to project (omit with --all)"),
    all_databases: bool = typer.Option(False, "--all", help="Project every registered database"),
):
    """Re-project stored credentials into the Egeria secrets file.

    Uses the credentials RE already holds (decrypted the way the survey runner
    decrypts them); nothing is typed or printed. One collection per database.
    A database with no stored credential is skipped and said. Idempotent.
    Use after a redeploy recreated the secrets directory.

    Example:
        resource-explorer database reproject-secrets --all
    """
    from resource_explorer import omsecrets_reproject, omsecrets_store
    from resource_explorer.registry import ProjectRegistry

    if bool(slug) == bool(all_databases):
        console.print("[red]Give exactly one of <slug> or --all.[/red]")
        raise typer.Exit(2)
    path = omsecrets_store.local_path()
    if not path:
        console.print("[red]EGERIA_SECRETS_STORE_LOCAL_PATH is not set; there is no secrets file to project to.[/red]")
        raise typer.Exit(1)
    registry = ProjectRegistry()
    if slug and not registry.database_exists(slug):
        console.print(f"[red]Database '{slug}' not found.[/red]")
        raise typer.Exit(1)
    outcomes = omsecrets_reproject.reproject(
        registry, [registry._normalize_slug(slug)] if slug else None, path=path)
    failed = False
    for o in outcomes:
        if o.status == omsecrets_reproject.WRITTEN:
            console.print(f"[green]written[/green]   {o.collection}")
        elif o.status == omsecrets_reproject.UNCHANGED:
            console.print(f"unchanged  {o.collection}")
        elif o.status == omsecrets_reproject.SKIPPED:
            console.print(f"[yellow]skipped[/yellow]   {o.slug}: {o.message}")
        else:
            failed = True
            console.print(f"[red]error[/red]     {o.slug}: {o.message}")
    if not outcomes:
        console.print("No registered databases.")
    if failed:
        raise typer.Exit(1)


@database_app.command(name="check-credential-drift")
def database_check_credential_drift(
    slug: str = typer.Argument(help="Database slug to check"),
):
    """Report whether RE's registry and the `.omsecrets` file agree on
    which secrets collection exists for this database.

    Design REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md §7: "Drift
    between the two is detectable by comparing collection names present on
    each side." This is a point check for one database, not a full
    reconciliation sweep or repair -- see `ProjectRegistry.
    check_credential_drift`'s docstring for what it deliberately does not
    cover (value-level drift, a scan across every database).

    Example:
        resource-explorer database check-credential-drift my-postgres
    """
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()

    if not registry.database_exists(slug):
        console.print(f"[red]Database '{slug}' not found.[/red]")
        raise typer.Exit(1)

    result = registry.check_credential_drift(slug)
    console.print(f"Collection: [cyan]{result['collection_name']}[/cyan]")
    console.print(f"  In registry:   {'yes' if result['in_registry'] else 'no'}")
    if not result["omsecrets_configured"]:
        console.print(
            "  In .omsecrets: [dim]not checked — EGERIA_SECRETS_STORE_LOCAL_PATH is unset[/dim]"
        )
        return
    console.print(f"  In .omsecrets: {'yes' if result['in_omsecrets'] else 'no'}")
    if result["in_sync"]:
        console.print("[green]✓ In sync.[/green]")
    else:
        console.print("[yellow]⚠ Drift detected — the two sides disagree.[/yellow]")


@database_app.command(name="list")
def database_list(
    db_type: Optional[str] = typer.Option(None, "--type", help="Filter by database type"),
):
    """List all registered databases."""
    from resource_explorer.registry import ProjectRegistry
    from rich.table import Table
    
    registry = ProjectRegistry()
    databases = registry.list_databases(db_type)
    
    if not databases:
        console.print("[dim]No databases registered.[/dim]")
        return
    
    table = Table("Slug", "Display Name", "Type", "Host", "Database", "Last Surveyed")
    for db in databases:
        last_surveyed = db.last_surveyed_at[:10] if db.last_surveyed_at else "Never"
        table.add_row(
            db.slug,
            db.display_name,
            db.db_type,
            f"{db.host}:{db.port}",
            db.database_name,
            last_surveyed,
        )
    
    console.print(table)


@database_app.command(name="survey")
def database_survey(
    slug: str = typer.Argument(help="Database slug to survey"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Database username (required for custom survey)"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password (required for custom survey)", hide_input=True),
    use_egeria: bool = typer.Option(False, "--egeria", help="Try Egeria survey first (hybrid approach)"),
    force_custom: bool = typer.Option(False, "--force-custom", help="Skip Egeria and use custom surveyor"),
    egeria_url: Optional[str] = typer.Option(None, "--egeria-url", help="Egeria platform URL (overrides EGERIA_PLATFORM_URL)"),
    egeria_server: Optional[str] = typer.Option(None, "--egeria-server", help="Egeria view server name (overrides EGERIA_VIEW_SERVER)"),
    secrets_path: Optional[str] = typer.Option(None, "--secrets-path", help="Path to Egeria secrets file"),
):
    """Run a survey on a registered database using hybrid approach.
    
    By default, uses custom surveyor (requires --user and --password).
    With --egeria, tries Egeria first and falls back to custom if needed.
    
    Examples:
        # Custom survey (direct connection)
        resource-explorer database survey my-postgres --user admin --password secret
        
        # Hybrid approach (try Egeria first, fall back to custom)
        resource-explorer database survey my-postgres --egeria --user admin --password secret
        
        # Force custom survey (skip Egeria)
        resource-explorer database survey my-postgres --force-custom --user admin --password secret
        
        # Egeria-only survey (with secrets file)
        resource-explorer database survey my-postgres --egeria --secrets-path /path/to/secrets.omsecrets
    """
    from resource_explorer.registry import ProjectRegistry
    
    registry = ProjectRegistry()
    
    # Check if database exists
    if not registry.database_exists(slug):
        console.print(f"[red]Database '{slug}' not found. Register it first with 'database register'.[/red]")
        raise typer.Exit(1)
    
    # Determine survey mode
    if use_egeria or (not force_custom and not user):
        _require_resource_write(registry, "database", slug)      # Brief Z2: may catalog it in Egeria
        # Routed through executes_at="egeria-adaptive" (the folded-in
        # HybridDatabaseSurveyor strategy selector) via a one-step synthetic
        # Survey Definition, rather than calling run_hybrid_survey directly
        # — see docs/design-notes/EXECUTION-MODES-HYBRID-CLARIFICATION.md.
        from resource_explorer.surveyors.survey_definition_executor import (
            SurveyDefinitionExecutor,
        )

        console.print(f"[cyan]Surveying database '{slug}' (hybrid mode)...[/cyan]")

        # Prepare credentials if provided
        credentials = None
        if user and password:
            credentials = {"user": user, "password": password}

        try:
            executor = SurveyDefinitionExecutor(registry)
            exec_result = executor.run_synthetic_step(
                entity_type="database",
                slug=slug,
                re_analysis_step="postgres_schema_and_stats",
                executes_at="egeria-adaptive",
                db_user=(credentials or {}).get("user", ""),
                db_pwd=(credentials or {}).get("password", ""),
                # No --refresh CLI option exists for this command — matches
                # run_hybrid_survey's own default (refresh=False) exactly.
                refresh=False,
                force_custom=force_custom,
                platform_url=egeria_url,
                view_server=egeria_server,
                secrets_path=secrets_path,
            )
            step_report = (exec_result.get("steps") or [{}])[0]
            # Reconstruct run_hybrid_survey's historic flat response shape
            # (see web/routes/databases.py's identical unnesting — the
            # handler nests "schema_info"/"statistics" under "result" so
            # the executor's own generic publish step doesn't re-publish
            # them a second time).
            detail = dict(step_report.get("detail") or {})
            nested = detail.pop("result", None) or {}
            results = {**detail, **nested}
            results.setdefault("source", step_report.get("source", "custom"))
            results.setdefault("errors", [])
            results["errors"] = list(results["errors"]) + [
                e for e in exec_result.get("errors", []) if e not in results["errors"]
            ]
        except Exception as e:
            console.print(f"[red]✗ Survey failed: {e}[/red]")
            raise typer.Exit(1)
    else:
        # Use custom surveyor directly
        from resource_explorer.surveyors.database.database_surveyor import run_database_survey
        
        if not user or not password:
            console.print("[red]Custom survey requires --user and --password options.[/red]")
            raise typer.Exit(1)
        
        console.print(f"[cyan]Surveying database '{slug}' (custom mode)...[/cyan]")
        
        try:
            results = run_database_survey(
                slug,
                {"user": user, "password": password},
                registry,
            )
        except Exception as e:
            console.print(f"[red]✗ Survey failed: {e}[/red]")
            raise typer.Exit(1)
    
    # Display results
    if results.get("errors"):
        console.print(f"[red]✗ Survey failed:[/red]")
        for error in results["errors"]:
            console.print(f"  {error}")
        try:
            from resource_explorer.activity_logger import log_survey
            db_entity = registry.get_database(slug)
            log_survey(
                registry,
                entity_type="database",
                entity_slug=slug,
                entity_name=db_entity.display_name if db_entity else slug,
                entity_location=(
                    f"{db_entity.host}:{db_entity.port}/{db_entity.database_name}"
                    if db_entity else ""
                ),
                intent="assessment",
                status="error",
                summary="Database survey failed",
                detail="; ".join(str(e) for e in results["errors"]),
            )
        except Exception:
            pass
        raise typer.Exit(1)

    # Log successful database survey
    try:
        from resource_explorer.activity_logger import log_survey
        db_entity = registry.get_database(slug)
        source_label = results.get("source", "unknown")
        schema_info = results.get("schema_info", {})
        log_survey(
            registry,
            entity_type="database",
            entity_slug=slug,
            entity_name=db_entity.display_name if db_entity else slug,
            entity_location=(
                f"{db_entity.host}:{db_entity.port}/{db_entity.database_name}"
                if db_entity else ""
            ),
            intent="assessment",
            status="pending" if results.get("status") == "pending" else "ok",
            summary=(
                f"Database survey ({source_label}): "
                f"{schema_info.get('total_tables', results.get('annotation_count', 0))} tables"
            ),
            detail=results.get("message", ""),
        )
    except Exception:
        pass

    # Show survey source
    source = results.get("source", "unknown")
    if source == "egeria":
        console.print(f"[green]✓ Survey completed (source: Egeria)[/green]")
        
        # Check if pending
        if results.get("status") == "pending":
            console.print(f"\n[yellow]{results.get('message', 'Survey pending')}[/yellow]")
            if results.get("engine_action_guid"):
                console.print(f"[dim]Engine Action GUID: {results['engine_action_guid']}[/dim]")
            return
        
        # Show Egeria results
        console.print(f"\n[bold]Survey Report:[/bold]")
        console.print(f"  Report GUID: {results.get('egeria_report_guid', 'N/A')}")
        console.print(f"  Annotations: {results.get('annotation_count', 0)}")
        if results.get("description"):
            console.print(f"  Description: {results['description']}")
        
    elif source == "custom":
        console.print(f"[green]✓ Survey completed (source: Custom)[/green]")
        
        schema_info = results.get("schema_info", {})
        stats = results.get("statistics", {})
        
        console.print(f"\n[bold]Schema Summary:[/bold]")
        console.print(f"  Schemas: {len(schema_info.get('schemas', []))}")
        console.print(f"  Tables: {schema_info.get('total_tables', 0)}")
        console.print(f"  Columns: {schema_info.get('total_columns', 0)}")
        
        db_size = stats.get("database_size", {})
        if db_size:
            console.print(f"\n[bold]Database Size:[/bold]")
            console.print(f"  {db_size.get('size_pretty', 'unknown')}")
        
        console.print(f"\n[bold]Annotations:[/bold] {len(results.get('annotations', []))} created")
        console.print(f"[dim]Results stored in registry.[/dim]")
    else:
        console.print(f"[yellow]Unknown survey source: {source}[/yellow]")


@database_app.command(name="survey-definition")
def database_survey_definition(
    slug: str = typer.Argument(help="Database slug to survey"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Database username"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Database password", hide_input=True),
    survey_definition: Optional[str] = typer.Option(
        None, "--survey-definition", "-s",
        help="Name/qualifiedName of the Survey Definition (GovernanceActionProcess) in Egeria. "
             "If omitted, RE looks up candidates by Technology Type.",
    ),
    refresh_definition: bool = typer.Option(
        False, "--refresh-definition", help="Ignore any cached Survey Definition GUID and re-resolve"
    ),
    egeria_url: Optional[str] = typer.Option(None, "--egeria-url", help="Egeria platform URL override"),
    egeria_server: Optional[str] = typer.Option(None, "--egeria-server", help="Egeria view server name override"),
    engine: Optional[str] = typer.Option(
        None, "--engine",
        help="Force which engine runs this definition's 'resource-explorer'-tagged "
             "steps for THIS run only: 'resource-explorer' (local) or 'prefect'. "
             "Omit to use the configured behaviour (PREFECT_ROUTE_LOCAL_STEPS). "
             "Does not affect executes_at='egeria' steps.",
    ),
):
    """Execute a Survey Definition authored in Egeria against a registered database.

    Fetches the Survey Definition (a GovernanceActionProcess + chained steps) from
    Egeria, runs each step tagged executes_at=resource-explorer locally, and
    publishes results back to Egeria — without cataloging the database or
    triggering Egeria's own native survey as a side effect.

    Examples:
        resource-explorer database survey-definition my-postgres --user admin --password secret
        resource-explorer database survey-definition my-postgres -s PostgreSQLStandardSurvey --user admin --password secret
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        run_survey_definition,
    )
    from resource_explorer.surveyors.survey_definition_reader import (
        SurveyDefinitionReaderError,
        UnsupportedSurveyDefinitionError,
    )

    registry = ProjectRegistry()
    if not registry.database_exists(slug):
        console.print(f"[red]Database '{slug}' not found. Register it first with 'database register'.[/red]")
        raise typer.Exit(1)

    if engine is not None and engine not in ("resource-explorer", "prefect"):
        console.print(f"[red]✗ --engine must be 'resource-explorer' or 'prefect' — got {engine!r}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Running Survey Definition for database '{slug}'...[/cyan]")
    try:
        results = run_survey_definition(
            entity_type="database",
            slug=slug,
            registry=registry,
            survey_definition_ref=survey_definition,
            refresh_definition=refresh_definition,
            db_user=user or "",
            db_pwd=password or "",
            engine_override=engine,
        )
    except UnsupportedSurveyDefinitionError as exc:
        console.print(f"[red]✗ Unsupported Survey Definition: {exc}[/red]")
        raise typer.Exit(1)
    except (SurveyDefinitionExecutorError, SurveyDefinitionReaderError) as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]✗ Survey Definition run failed: {exc}[/red]")
        raise typer.Exit(1)

    from resource_explorer.activity_logger import log_survey
    db_entity = registry.get_database(slug)
    status = "error" if results.get("errors") else "ok"
    try:
        log_survey(
            registry,
            entity_type="database",
            entity_slug=slug,
            entity_name=db_entity.display_name if db_entity else slug,
            entity_location=(
                f"{db_entity.host}:{db_entity.port}/{db_entity.database_name}" if db_entity else ""
            ),
            intent="assessment",
            status=status,
            summary=f"Survey Definition run ({results.get('process_qualified_name', '')})",
            detail="; ".join(results.get("errors", [])),
        )
    except Exception:
        pass

    if results.get("errors"):
        console.print("[red]✗ Completed with errors:[/red]")
        for err in results["errors"]:
            console.print(f"  {err}")
        for step in results.get("steps", []):
            console.print(f"  [dim]{step['step']}: {step['status']}[/dim]")
        raise typer.Exit(1)

    console.print(f"[green]✓ Survey Definition completed: {results.get('process_qualified_name', '')}[/green]")
    for step in results.get("steps", []):
        console.print(f"  {step['step']}: {step['status']}")
    if results.get("egeria_report_guid"):
        console.print(f"  Report GUID: {results['egeria_report_guid']}")


@database_app.command(name="remove")
def database_remove(
    slug: str = typer.Argument(help="Database slug to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove a database from the registry."""
    from resource_explorer.registry import ProjectRegistry
    
    registry = ProjectRegistry()
    db = registry.get_database(slug)
    
    if not db:
        console.print(f"[red]Database '{slug}' not found.[/red]")
        raise typer.Exit(1)
    
    if not yes:
        typer.confirm(f"Remove database '{db.display_name}' ({slug})?", abort=True)
    
    registry.remove_database(slug)
    console.print(f"[green]✓ Database '{slug}' removed.[/green]")


@database_app.command(name="info")
def database_info(
    slug: str = typer.Argument(help="Database slug"),
):
    """Show detailed information about a database."""
    from resource_explorer.registry import ProjectRegistry
    from rich.table import Table
    
    registry = ProjectRegistry()
    db = registry.get_database(slug)
    
    if not db:
        console.print(f"[red]Database '{slug}' not found.[/red]")
        raise typer.Exit(1)
    
    console.print(f"\n[bold]{db.display_name}[/bold] ({db.slug})")
    console.print(f"Type: {db.db_type}")
    console.print(f"Host: {db.host}:{db.port}")
    console.print(f"Database: {db.database_name}")
    console.print(f"Status: {db.status.value}")
    if db.description:
        console.print(f"Description: {db.description}")
    console.print(f"Registered: {db.registered_at[:10]}")
    if db.last_surveyed_at:
        console.print(f"Last Surveyed: {db.last_surveyed_at[:10]}")
    
    # Show survey history
    surveys = registry.get_database_surveys(slug)
    if surveys:
        console.print(f"\n[bold]Survey History:[/bold] ({len(surveys)} surveys)")
        table = Table("Date", "Schemas", "Tables", "Columns")
        for survey in surveys[:5]:  # Show last 5
            table.add_row(
                survey["surveyed_at"][:10],
                str(survey["schema_count"]),
                str(survey["table_count"]),
                str(survey["column_count"]),
            )
        console.print(table)
    else:
        console.print("\n[dim]No surveys yet.[/dim]")


# ── filesystem sub-group ──────────────────────────────────────────────────────

filesystem_app = typer.Typer(name="filesystem", help="Manage filesystem entities and surveys.")
app.add_typer(filesystem_app)


@filesystem_app.command(name="register")
def filesystem_register(
    slug: str = typer.Argument(help="Unique identifier for this filesystem"),
    local_path: str = typer.Option(..., "--local-path", "-l", help="Local directory path to walk/traverse"),
    canonical_path: str = typer.Option("", "--canonical-path", "-c", help="Canonical root path recorded in Egeria"),
    display_name: Optional[str] = typer.Option(None, "--name", help="Display name (defaults to slug)"),
    description: str = typer.Option("", "--description", help="Filesystem description"),
    egeria_url: Optional[str] = typer.Option(None, "--egeria-url", help="Egeria platform URL"),
    egeria_server: Optional[str] = typer.Option(None, "--egeria-server", help="Egeria view server name"),
    egeria_user: Optional[str] = typer.Option(None, "--egeria-user", hidden=True,
                                              help="Ignored: per-resource Egeria credentials are no longer used"),
    egeria_password: Optional[str] = typer.Option(None, "--egeria-password", hidden=True, hide_input=True,
                                                  help="Ignored: per-resource Egeria credentials are no longer used"),
    group: Optional[str] = typer.Option(None, "--group", help="Assign this filesystem to a project group"),
):
    """Register a local filesystem directory in the registry.
    
    Example:
        resource-explorer filesystem register my-data \
            --local-path /path/to/local/data \
            --canonical-path file://shared-nfs/data \
            --name "Shared Data Mount" \
            --group egeria
    """
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry

    _warn_ignored_egeria_credentials(egeria_user, egeria_password)
    registry = ProjectRegistry()
    
    # Check if already exists
    if registry.filesystem_exists(slug):
        console.print(f"[red]FileSystem '{slug}' already registered.[/red]")
        raise typer.Exit(1)
        
    # Resolve absolute path for local_path
    abs_local_path = os.path.abspath(local_path)
    if not os.path.exists(abs_local_path):
        console.print(f"[yellow]Warning: Local path '{abs_local_path}' does not exist on disk currently.[/yellow]")
        
    fs_entity = FileSystemEntity(
        slug=slug,
        display_name=display_name or slug,
        local_mount_point=abs_local_path,
        canonical_mount_point=canonical_path or abs_local_path,
        description=description,
        egeria_url=egeria_url or "",
        egeria_server=egeria_server or "",
        group_slug=group or "",
    )
    
    registry.register_filesystem(fs_entity)
    console.print(f"[green]✓ FileSystem '{slug}' registered successfully.[/green]")
    console.print(f"  Local Mount: {abs_local_path}")
    console.print(f"  Canonical Mount: {canonical_path or abs_local_path}")


@filesystem_app.command(name="list")
def filesystem_list():
    """List all registered filesystems."""
    from resource_explorer.registry import ProjectRegistry
    from rich.table import Table
    
    registry = ProjectRegistry()
    filesystems = registry.list_filesystems()
    
    if not filesystems:
        console.print("[dim]No filesystems registered.[/dim]")
        return
        
    table = Table("Slug", "Display Name", "Local Mount Point", "Canonical Mount Point", "Files", "Data Files", "Last Surveyed")
    for fs in filesystems:
        last_surveyed = fs.last_surveyed_at[:10] if fs.last_surveyed_at else "Never"
        table.add_row(
            fs.slug,
            fs.display_name,
            fs.local_mount_point,
            fs.canonical_mount_point or fs.local_mount_point,
            str(fs.file_count),
            str(fs.data_file_count),
            last_surveyed,
        )
        
    console.print(table)


@filesystem_app.command(name="survey")
def filesystem_survey(
    slug: str = typer.Argument(help="Filesystem slug to survey"),
    use_egeria: bool = typer.Option(False, "--egeria", help="Publish survey results to Egeria (hybrid approach)"),
    egeria_url: Optional[str] = typer.Option(None, "--egeria-url", help="Egeria platform URL override"),
    egeria_server: Optional[str] = typer.Option(None, "--egeria-server", help="Egeria view server name override"),
    egeria_user: Optional[str] = typer.Option(None, "--egeria-user", hidden=True,
                                              help="Ignored: per-resource Egeria credentials are no longer used"),
    egeria_password: Optional[str] = typer.Option(None, "--egeria-password", hidden=True, hide_input=True,
                                                  help="Ignored: per-resource Egeria credentials are no longer used"),
):
    """Run a local survey on the filesystem, walking files and profiling schemas.
    
    With --egeria, it automatically publishes the cataloged directory structure,
    data files, and survey annotations directly into Egeria's metadata server.
    """
    from resource_explorer.registry import ProjectRegistry, ProjectStatus
    
    registry = ProjectRegistry()
    
    # Check if filesystem exists
    fs_entity = registry.get_filesystem(slug)
    if not fs_entity:
        console.print(f"[red]FileSystem '{slug}' not found. Register it first with 'filesystem register'.[/red]")
        raise typer.Exit(1)
        
    _warn_ignored_egeria_credentials(egeria_user, egeria_password)
    if use_egeria:
        _require_resource_write(registry, "filesystem", slug)    # Brief Z2: catalogs and publishes it
    console.print(f"[cyan]Surveying filesystem '{slug}'...[/cyan]")
    registry.update_filesystem_status(slug, ProjectStatus.ACTIVE)
    
    try:
        if use_egeria:
            # Routed through executes_at="egeria-adaptive" (the folded-in
            # run_hybrid_filesystem_survey strategy) via a one-step
            # synthetic Survey Definition, rather than calling
            # run_hybrid_filesystem_survey directly — see
            # docs/design-notes/EXECUTION-MODES-HYBRID-CLARIFICATION.md.
            from resource_explorer.surveyors.survey_definition_executor import (
                SurveyDefinitionExecutor,
            )

            executor = SurveyDefinitionExecutor(registry)
            exec_result = executor.run_synthetic_step(
                entity_type="filesystem",
                slug=slug,
                re_analysis_step="filesystem_inventory",
                executes_at="egeria-adaptive",
                egeria_url=egeria_url,
                egeria_server=egeria_server,
                force_egeria_publish=True,
            )
            step_report = (exec_result.get("steps") or [{}])[0]
            detail = dict(step_report.get("detail") or {})
            nested = detail.pop("result", None) or {}
            results = {**detail, **nested}
            results.setdefault("source", step_report.get("source", "custom"))
        else:
            from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor
            local_surveyor = LocalFileSystemSurveyor(fs_entity, registry)
            results = local_surveyor.run()
            registry.add_filesystem_survey(
                fs_slug=slug,
                surveyed_at=results["surveyed_at"],
                survey_data=results,
                egeria_report_guid="local-only",
                source="local",
            )
    except Exception as e:
        console.print(f"[red]✗ Survey failed: {e}[/red]")
        registry.update_filesystem_status(slug, ProjectStatus.ERROR, str(e))
        raise typer.Exit(1)

    # Show results
    console.print(f"[green]✓ Survey completed successfully.[/green]")
    console.print(f"  Total Files: {results['total_files']}")
    console.print(f"  Data Files: {results['total_data_files']}")
    console.print(f"  Total Size: {results['total_size']}")
    
    # Log successful walk in activity log — annotations only present for
    # local-mode results (hybrid mode's annotations are built and published
    # separately inside run_hybrid_filesystem_survey).
    try:
        from resource_explorer.activity_logger import log_survey
        from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import build_rfa_annotations
        annotations = build_rfa_annotations(results) if not use_egeria else []
        log_survey(
            registry,
            entity_type="filesystem",
            entity_slug=slug,
            entity_name=fs_entity.display_name,
            entity_location=fs_entity.local_mount_point,
            intent="assessment",
            status="error" if annotations else "ok",
            summary=f"FileSystem survey: {results['total_files']} files ({results['total_data_files']} data files)",
            detail=f"Walked {fs_entity.local_mount_point}. Total size {results['total_size']}.",
            annotations=annotations,
        )
    except Exception:
        pass


@filesystem_app.command(name="survey-definition")
def filesystem_survey_definition(
    slug: str = typer.Argument(help="Filesystem slug to survey"),
    survey_definition: Optional[str] = typer.Option(
        None, "--survey-definition", "-s",
        help="Name/qualifiedName of the Survey Definition (GovernanceActionProcess) in Egeria. "
             "If omitted, RE looks up candidates by Technology Type.",
    ),
    refresh_definition: bool = typer.Option(
        False, "--refresh-definition", help="Ignore any cached Survey Definition GUID and re-resolve"
    ),
    egeria_url: Optional[str] = typer.Option(None, "--egeria-url", help="Egeria platform URL override"),
    egeria_server: Optional[str] = typer.Option(None, "--egeria-server", help="Egeria view server name override"),
    engine: Optional[str] = typer.Option(
        None, "--engine",
        help="Force which engine runs this definition's 'resource-explorer'-tagged "
             "steps for THIS run only: 'resource-explorer' (local) or 'prefect'. "
             "Omit to use the configured behaviour (PREFECT_ROUTE_LOCAL_STEPS). "
             "Does not affect executes_at='egeria' steps.",
    ),
):
    """Execute a Survey Definition authored in Egeria against a registered filesystem.

    Fetches the Survey Definition from Egeria, runs each step tagged
    executes_at=resource-explorer locally, and publishes results back to Egeria
    — without auto-cataloging the filesystem root or its files as a side effect.
    """
    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        run_survey_definition,
    )
    from resource_explorer.surveyors.survey_definition_reader import (
        SurveyDefinitionReaderError,
        UnsupportedSurveyDefinitionError,
    )

    registry = ProjectRegistry()
    if not registry.get_filesystem(slug):
        console.print(f"[red]FileSystem '{slug}' not found. Register it first with 'filesystem register'.[/red]")
        raise typer.Exit(1)

    if engine is not None and engine not in ("resource-explorer", "prefect"):
        console.print(f"[red]✗ --engine must be 'resource-explorer' or 'prefect' — got {engine!r}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Running Survey Definition for filesystem '{slug}'...[/cyan]")
    try:
        results = run_survey_definition(
            entity_type="filesystem",
            slug=slug,
            registry=registry,
            survey_definition_ref=survey_definition,
            refresh_definition=refresh_definition,
            engine_override=engine,
        )
    except UnsupportedSurveyDefinitionError as exc:
        console.print(f"[red]✗ Unsupported Survey Definition: {exc}[/red]")
        raise typer.Exit(1)
    except (SurveyDefinitionExecutorError, SurveyDefinitionReaderError) as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]✗ Survey Definition run failed: {exc}[/red]")
        raise typer.Exit(1)

    from resource_explorer.activity_logger import log_survey
    fs_entity = registry.get_filesystem(slug)
    status = "error" if results.get("errors") else "ok"
    try:
        log_survey(
            registry,
            entity_type="filesystem",
            entity_slug=slug,
            entity_name=fs_entity.display_name if fs_entity else slug,
            entity_location=fs_entity.local_mount_point if fs_entity else "",
            intent="assessment",
            status=status,
            summary=f"Survey Definition run ({results.get('process_qualified_name', '')})",
            detail="; ".join(results.get("errors", [])),
        )
    except Exception:
        pass

    if results.get("errors"):
        console.print("[red]✗ Completed with errors:[/red]")
        for err in results["errors"]:
            console.print(f"  {err}")
        raise typer.Exit(1)

    console.print(f"[green]✓ Survey Definition completed: {results.get('process_qualified_name', '')}[/green]")
    for step in results.get("steps", []):
        console.print(f"  {step['step']}: {step['status']}")
    if results.get("egeria_report_guid"):
        console.print(f"  Report GUID: {results['egeria_report_guid']}")


@filesystem_app.command(name="remove")
def filesystem_remove(
    slug: str = typer.Argument(help="Filesystem slug to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove a filesystem from the registry."""
    from resource_explorer.registry import ProjectRegistry
    
    registry = ProjectRegistry()
    fs = registry.get_filesystem(slug)
    
    if not fs:
        console.print(f"[red]FileSystem '{slug}' not found.[/red]")
        raise typer.Exit(1)
        
    if not yes:
        typer.confirm(f"Remove FileSystem '{fs.display_name}' ({slug})?", abort=True)
        
    registry.remove_filesystem(slug)
    console.print(f"[green]✓ FileSystem '{slug}' removed.[/green]")



def _warn_ignored_egeria_credentials(user: Optional[str], password: Optional[str]) -> None:
    """Per-resource Egeria credentials are no longer accepted (owner, 2026-10-09): say so, once,
    naming the options only — never their values."""
    sent = [n for n, v in (("--egeria-user", user), ("--egeria-password", password)) if v]
    if sent:
        console.print(f"[yellow]{'/'.join(sent)} ignored: RE no longer uses per-resource Egeria "
                      "credentials; it calls Egeria as you (resource-explorer login) or as its daemon.[/yellow]")
        import logging

        logging.getLogger(__name__).warning(
            "CLI: %s ignored (per-resource Egeria credentials are no longer used)", "/".join(sent))


def _resource_write_refusal(registry, entity_type: str, slug: str, write: str = "publish") -> str:
    """Brief Z2: the CLI asks the same decision as the web (`workflows.curate.resource_access`). "" when the
    signed-in person may make this write, else "not permitted · <reason>"."""
    from resource_explorer.workflows.curate import resource_access

    d = resource_access(registry, entity_type, slug, write)
    return "" if d.allowed else f"not permitted · {d.reason}"


def _require_resource_write(registry, entity_type: str, slug: str, write: str = "publish") -> None:
    """Sign-in (exit 2 without one) and `_resource_write_refusal` (exit 1, with the reason)."""
    from resource_explorer.cli import session as cli_session

    cli_session.require_and_activate(console)
    why = _resource_write_refusal(registry, entity_type, slug, write)
    if why:
        console.print(f"[red]{why}[/red]")
        raise typer.Exit(1)


def _try_build_egeria_client(platform_url: Optional[str], view_server: Optional[str]):
    """Attempt to build a pyegeria client for optional cache refresh. Returns None on failure."""
    import os
    url = platform_url or egeria_platform_url()
    server = view_server or egeria_view_server()
    if not url:
        return None
    try:
        from pyegeria import ValidMetadataManager

        from resource_explorer.egeria_clients import current_principal, egeria_client

        # The signed-in CLI user (`resource-explorer login`), never env credentials (Brief I).
        # No session: no refresh, as before for an unreachable Egeria.
        return egeria_client(current_principal(), purpose="valid values cache",
                             view_server=server or None, platform_url=url).of(ValidMetadataManager)
    except Exception:
        return None


# ── prefect sub-group ─────────────────────────────────────────────────────────

prefect_app = typer.Typer(name="prefect", help="Manage Prefect flows, workers, and deployments.")
app.add_typer(prefect_app)


@prefect_app.command(name="deploy")
def prefect_deploy():
    """Deploy Resource Explorer flows to the Prefect server."""
    from pathlib import Path

    from resource_explorer.prefect.flows import re_survey_flow
    from resource_explorer.config import get_config

    config = get_config()
    console.print(f"[blue]Deploying flows to Prefect server at {config.prefect.api_url}...[/blue]")
    try:
        # .deploy() alone errors ("Either an image or remote storage location
        # must be provided") because a bare work_pool_name assumes the worker
        # fetches code from a remote source (git/Docker) — the right shape for
        # a `process`-type pool running on this same machine is `from_source`
        # pointing at this checkout, so the worker subprocess can just import
        # the flow module directly. Confirmed live against a local server
        # 2026-08-26 — this checkout's own root is the source, since the
        # worker process runs with the resource-explorer package importable.
        source_root = Path(__file__).resolve().parents[2]  # packages/resource-explorer/
        re_survey_flow.from_source(
            source=str(source_root),
            entrypoint="resource_explorer/prefect/flows.py:re_survey_flow",
        ).deploy(
            name="re-survey-step-deployment",
            work_pool_name=config.prefect.work_pool,
        )
        console.print(f"[green]✓ Flows successfully deployed to work pool '[bold]{config.prefect.work_pool}[/bold]'![/green]")
    except Exception as e:
        console.print(f"[red]Failed to deploy flows: {e}[/red]")
        raise typer.Exit(1)


@prefect_app.command(name="worker")
def prefect_worker(
    pool: Optional[str] = typer.Option(None, "--pool", "-p", help="Work pool name override"),
):
    """Start a local Prefect worker to run tasks from the work pool."""
    import subprocess
    from resource_explorer.config import get_config

    config = get_config()
    pool_name = pool or config.prefect.work_pool
    console.print(f"[blue]Starting Prefect worker for pool '[bold]{pool_name}[/bold]'...[/blue]")
    try:
        subprocess.run(["prefect", "worker", "start", "--pool", pool_name], check=True)
    except KeyboardInterrupt:
        console.print("[yellow]\nWorker stopped.[/yellow]")
    except Exception as e:
        console.print(f"[red]Failed to run worker: {e}[/red]")
        raise typer.Exit(1)


# ── board_summary backfill (BOARD-SUMMARY-READ-COST-IMPLEMENTED.md) ──

@app.command(name="backfill-board-summaries")
def backfill_board_summaries(
    slug: Optional[str] = typer.Argument(
        None, help="Database or filesystem slug to backfill. Omit and pass --all instead."),
    all_resources: bool = typer.Option(
        False, "--all", help="Backfill every database and filesystem resource in the registry."),
):
    """Pre-populate `board_summary` rows for a resource (or every resource)
    BEFORE any real user visits its By-analysis pane.

    `build_survey_results(..., board_id=...)` (`resource_explorer/workflows/
    analysis.py`) already self-heals — the first read of a missing/stale
    board recomputes it and persists the result, so a real visitor's request
    is never wrong, just slow the first time. On coco_pharma that first-visit
    cost measured over 4 minutes across all 7 discovery boards, all paid in
    the request path by whoever happened to click first (2026-09-29 owner
    gate check). This command drives the SAME recompute-and-persist function
    proactively, out of band, so an operator (or CI, right after a merge)
    can pay that cost once, before anyone is waiting on it.

    Only `board_id`s already fresh in `board_summary` are skipped — this is a
    warm/backfill command, not a forced recompute of everything. A board
    whose analysis has never run is still visited (it persists an empty-but-
    valid summary, the same self-heal a real request would trigger) so a
    later real run's write hook has a consistent row to update rather than a
    gap.

    Scoped to `database` and `filesystem` entity types only. `repo` boards
    are not covered — no per-analysis run route exists for repo through this
    branch's writer hook shape (see the IMPLEMENTED doc's writer-path
    section); nothing here silently pretends otherwise.
    """
    import time

    from resource_explorer.registry import ProjectRegistry
    from resource_explorer.workflows.analysis import (
        _read_board_summary_if_fresh,
        build_survey_results,
        list_survey_result_boards,
    )

    if bool(slug) == all_resources:
        if slug and all_resources:
            console.print("[red]Pass a slug OR --all, not both.[/red]")
        else:
            console.print("[red]Pass a resource slug, or --all to backfill every resource.[/red]")
        raise typer.Exit(code=1)

    registry = ProjectRegistry()

    targets: list[tuple[str, str]] = []
    if all_resources:
        targets.extend(("database", db.slug) for db in registry.list_databases())
        targets.extend(("filesystem", fs.slug) for fs in registry.list_filesystems())
        if not targets:
            console.print("[dim]No database or filesystem resources registered.[/dim]")
            return
    elif registry.database_exists(slug):
        targets.append(("database", slug))
    elif registry.filesystem_exists(slug):
        targets.append(("filesystem", slug))
    else:
        console.print(f"[red]'{slug}' is not a registered database or filesystem.[/red]")
        raise typer.Exit(code=1)

    total_boards = 0
    backfilled = 0
    skipped = 0
    errors: list[str] = []
    overall_start = time.monotonic()

    for entity_type, rslug in targets:
        try:
            boards = list_survey_result_boards(registry, entity_type, rslug).get("boards") or []
        except Exception as exc:
            errors.append(f"{entity_type}/{rslug}: could not list boards — {exc}")
            console.print(f"[red]{entity_type}/{rslug}: could not list boards — {exc}[/red]")
            continue

        for i, board in enumerate(boards, start=1):
            board_id = board["id"]
            total_boards += 1
            already_fresh = _read_board_summary_if_fresh(
                registry, entity_type, rslug, board_id, "", True,
            )
            if already_fresh is not None and already_fresh.get("dashboards"):
                console.print(
                    f"backfilling {rslug}: board {i}/{len(boards)} ({board_id})... "
                    f"[dim]already fresh, skipped[/dim]"
                )
                skipped += 1
                continue

            start = time.monotonic()
            try:
                build_survey_results(
                    registry, entity_type, rslug, stage="", include_empty=True, board_id=board_id,
                )
            except Exception as exc:
                elapsed = time.monotonic() - start
                errors.append(f"{entity_type}/{rslug}/{board_id}: {exc}")
                console.print(
                    f"backfilling {rslug}: board {i}/{len(boards)} ({board_id})... "
                    f"[red]FAILED after {elapsed:.1f}s — {exc}[/red]"
                )
                continue
            elapsed = time.monotonic() - start
            backfilled += 1
            console.print(
                f"backfilling {rslug}: board {i}/{len(boards)} ({board_id})... "
                f"[green]done in {elapsed:.1f}s[/green]"
            )

    total_elapsed = time.monotonic() - overall_start
    console.print(
        f"\n[bold]{len(targets)} resource(s), {total_boards} board(s) — "
        f"{backfilled} backfilled, {skipped} already fresh, {len(errors)} error(s), "
        f"{total_elapsed:.1f}s total[/bold]"
    )
    if errors:
        for e in errors:
            console.print(f"  [yellow]{e}[/yellow]")
        raise typer.Exit(code=1)


# ── batch CSV import/export (resource_explorer/batch_io.py) ─────────────────

@app.command(name="export-resources")
def export_resources(
    path: str = typer.Argument(help="CSV file to write"),
):
    """Export every registered resource as a CSV inventory + scorecard.

    Columns prefixed `status_` are observed state and are ignored if the file is
    imported again, so an export can be handed straight back to import-resources
    without editing. See resource_explorer/batch_io.py for why that line exists.
    """
    from resource_explorer.batch_io import export_rows, write_csv
    from resource_explorer.registry import ProjectRegistry

    rows = export_rows(ProjectRegistry())
    n = write_csv(rows, path)
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["resource_type"]] = by_type.get(r["resource_type"], 0) + 1
    console.print(f"[green]Wrote {n} resource(s) to {path}[/green]")
    for t, c in sorted(by_type.items()):
        console.print(f"  {t}: {c}")


@app.command(name="import-resources")
def import_resources(
    path: str = typer.Argument(help="CSV file to read (see export-resources for the shape)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen, write nothing"),
    group: Optional[str] = typer.Option(None, "--group", help="Group for rows that name none"),
):
    """Register resources listed in a CSV, skipping any already present.

    Registers only — no RAG ingestion, matching the org-import path, so a large
    batch is cheap. Surveying afterwards is the expensive half.
    """
    from resource_explorer.batch_io import describe_skipped, parse_csv, plan_import
    from resource_explorer.github.org_importer import OrgImporter
    from resource_explorer.registry import ProjectRegistry

    registry = ProjectRegistry()
    try:
        rows = parse_csv(path)
    except (OSError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if not rows:
        console.print("[yellow]No rows found.[/yellow]")
        raise typer.Exit(0)

    # The command line registers repos only; database rows are registered from
    # the web Find databases dialog (they need a server chosen by a person).
    plan = plan_import(registry, rows, importable=("repo",))
    console.print(f"[bold]{plan.total} row(s) in {path}[/bold]")
    console.print(f"  [green]{len(plan.to_register)}[/green] to register")
    console.print(f"  [dim]{len(plan.already_registered)} already registered[/dim]")
    for label, bucket in (("invalid", plan.invalid),
                          ("duplicate in file", plan.duplicate_in_file),
                          ("unsupported type", plan.unsupported_type)):
        if bucket:
            console.print(f"  [yellow]{len(bucket)} {label}[/yellow]")
            for r in bucket[:10]:
                console.print(f"     {describe_skipped(r)}")
            if len(bucket) > 10:
                console.print(f"     … and {len(bucket) - 10} more")

    if dry_run:
        console.print("[dim]--dry-run: nothing written.[/dim]")
        raise typer.Exit(0)
    if not plan.to_register:
        raise typer.Exit(0)

    importer = OrgImporter(registry=registry)
    registered = failed = 0
    for row in plan.to_register:
        try:
            importer.import_repo(
                row.address,
                row.display_name or row.address.rstrip("/").rsplit("/", 1)[-1],
                row.notes,
                row.group or group or "",
            )
            registered += 1
        except Exception as exc:
            # Per-row isolation, like _run_import_batch: one bad repo in a batch
            # of hundreds must not end the run.
            failed += 1
            console.print(f"  [red]line {row.line}: {row.address} — {exc}[/red]")

        # Disposition is intent, so it is applied whether or not registration
        # succeeded — a candidate can carry one before it is ever registered.
        if row.disposition:
            try:
                registry.set_disposition(row.address, row.disposition,
                                         reason=row.disposition_reason)
            except Exception as exc:
                console.print(f"  [yellow]line {row.line}: disposition not set — {exc}[/yellow]")

    console.print(f"[green]Registered {registered}[/green]"
                  + (f", [red]{failed} failed[/red]" if failed else ""))


@app.command(name="egeria-recheck")
def egeria_recheck(
    entity_type: Optional[list[str]] = typer.Option(
        None, "--entity-type", help="Restrict to repo/database/filesystem (repeatable; default: all)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be recorded, write nothing"),
):
    """Proactively re-check every cached Egeria GUID against the live server.

    Reactive divergence detection only marks a linkage stale the next time
    something actually uses the cached GUID — which can be a long time after
    Egeria's repository was reset. This asks Egeria about every cached GUID
    right now, so 'Admin > Egeria Links' reflects reality immediately rather
    than staying empty until each resource happens to be surveyed again.

    Examples:
      resource-explorer egeria-recheck
      resource-explorer egeria-recheck --dry-run
      resource-explorer egeria-recheck --entity-type repo --entity-type database
    """
    from resource_explorer.cli import session as cli_session
    from resource_explorer.egeria_linkage import recheck_all_linkages
    from resource_explorer.registry import ProjectRegistry

    # Every GUID this checks is read from the live platform, so it needs a
    # session — and reading as the person rather than as the service account
    # matters here: a GUID this user cannot see is stale *for them*.
    cli_session.require_and_activate(console)

    registry = ProjectRegistry()
    types = list(entity_type) if entity_type else None

    def _progress(checked, total, etype, slug):
        console.print(f"  [{checked}/{total}] {etype}/{slug}", end="\r")

    console.print("[bold]Rechecking cached Egeria GUIDs...[/bold]"
                  + (" [dim](dry run)[/dim]" if dry_run else ""))
    try:
        result = recheck_all_linkages(registry, entity_types=types, progress=_progress,
                                      dry_run=dry_run)
    except Exception as exc:
        console.print(f"\n[red]Could not connect to Egeria: {exc}[/red]")
        raise typer.Exit(1)

    console.print()  # clear the progress line
    console.print(f"  checked: {result['checked']}   "
                  f"[green]ok: {result['ok']}[/green]   "
                  f"[red]stale: {result['stale']}[/red]   "
                  f"[yellow]errors: {result['errors']}[/yellow]   "
                  f"[dim]skipped (no cached GUID): {result['skipped']}[/dim]")

    stale_rows = [d for d in result["details"] if d["result"] == "stale"]
    if stale_rows:
        # Printed as plain lines rather than a rich Table. Rich shrinks columns to
        # the console width — 80 when output is not a terminal — and *truncates*
        # rather than wrapping, which cut every GUID to 35 characters. A GUID
        # missing its last character still looks like a GUID, so a piped or saved
        # report carried identifiers that were quietly wrong, exactly when the
        # report is most likely to be kept and acted on later.
        console.print()
        console.print("[bold]Stale linkages"
                      + (" (not recorded — dry run)" if dry_run else " (recorded)")
                      + ":[/bold]")
        for row in stale_rows:
            console.print(f"  {row['entity_type']:<10}  {row['slug']:<28}  {row['guid']}",
                          highlight=False, soft_wrap=True)
        console.print(
            "\n[dim]Resolve from Admin > Egeria Links, or run "
            "[bold]resource-explorer egeria-reset <slug>[/bold] for repos.[/dim]"
        )

    error_rows = [d for d in result["details"] if d["result"] == "error"]
    if error_rows:
        console.print()
        console.print("[yellow]Could not verify (not marked stale):[/yellow]")
        for row in error_rows:
            console.print(f"  {row['entity_type']}/{row['slug']}: {row['detail'][:200]}")


# The workflow and run-queue command groups (plan §3's CLI parity) live in
# their own module — cli/main.py is already 2,300 lines, and the new commands
# are a coherent group rather than more of the same list.
from resource_explorer.cli import runs_commands as _runs_commands  # noqa: E402

_runs_commands.register(app)

from resource_explorer.cli import dr_egeria_commands as _dr_egeria_commands  # noqa: E402

_dr_egeria_commands.register(app)


if __name__ == "__main__":
    app()
