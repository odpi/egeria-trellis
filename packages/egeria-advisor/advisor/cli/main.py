"""
Egeria Advisor CLI - Main Entry Point

This module provides the main command-line interface for the Egeria Advisor.
"""

import os
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from loguru import logger

from advisor.rag_system import get_rag_system
from advisor.cli.formatters import ResponseFormatter
from advisor.cli.interactive import InteractiveSession
from advisor.cli.agent_session import AgentInteractiveSession

console = Console()


class DefaultCommandGroup(click.Group):
    """A group whose unrecognised first argument falls through to `ask`.

    `egeria-advisor` was a single command taking a free-text query, and the
    2026-09-04 login work needs it to also carry `login` and `logout`
    subcommands. Turning it into a plain `click.Group` would break
    `egeria-advisor "how do I create a glossary?"` — the query would be read as
    an unknown command — and every script and doc that spells it that way.

    So: named subcommands win, and *anything else* (a bare question, or a
    leading option like `-i`) is handed to `ask`, which is the old command
    unchanged. The precedence matters and is the one sharp edge: a query whose
    first word is literally `login` would be taken as the subcommand. That is
    the right trade — the subcommand names are two rare words, and
    `egeria-advisor ask "login ..."` says the other thing explicitly.
    """

    #: Never treat these as a query, whatever follows them.
    _PASSTHROUGH = ("--help", "-h", "--version")

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if args and args[0] in self._PASSTHROUGH:
                raise
            return "ask", self.get_command(ctx, "ask"), args

    def parse_args(self, ctx, args):
        # `click.Group` with no subcommand prints help; here a bare option
        # like `-i` or a lone question must reach `ask` instead. Only a
        # genuinely empty invocation falls through to the group's own help.
        if args and args[0] not in self._PASSTHROUGH and args[0] not in self.commands:
            args = ["ask", *args]
        return super().parse_args(ctx, args)


@click.group(cls=DefaultCommandGroup, invoke_without_command=False)
@click.version_option(version='0.1.0', prog_name='egeria-advisor')
def cli() -> None:
    """Egeria Advisor — AI-powered assistance for pyegeria.

    Ask a question directly (`egeria-advisor "what is a governance zone?"`),
    or use a subcommand. `login` caches an Egeria session so that live calls
    run as you rather than as the deployment's service account.
    """


@cli.command("ask")
@click.argument('query', required=False)
@click.option(
    '--interactive', '-i',
    is_flag=True,
    help='Start interactive mode'
)
@click.option(
    '--context', '-c',
    type=str,
    help='Provide context for the query (e.g., "glossary", "assets")'
)
@click.option(
    '--format', '-f',
    type=click.Choice(['text', 'json', 'markdown'], case_sensitive=False),
    default='text',
    help='Output format'
)
@click.option(
    '--no-citations',
    is_flag=True,
    help='Hide source citations'
)
@click.option(
    '--no-color',
    is_flag=True,
    help='Disable colored output'
)
@click.option(
    '--verbose', '-v',
    is_flag=True,
    help='Show detailed information'
)
@click.option(
    '--track/--no-track',
    default=True,
    help='Enable/disable MLflow tracking'
)
@click.option(
    '--feedback/--no-feedback',
    default=True,
    help='Enable/disable user feedback collection'
)
@click.option(
    '--debug', '-d',
    is_flag=True,
    help='Show debug/trace messages (loguru INFO level)'
)
@click.option(
    '--agent', '-a',
    is_flag=True,
    help='Use conversational agent mode (with conversation history)'
)
@click.option(
    '--user',
    'user_id',
    default=None,
    help='Egeria user id that owns any plan/report drafts and documents this '
         'session creates (chat-driven creation lands in this user\'s '
         'namespace instead of the shared one). Not required — since '
         '2026-09-04 the CLI has a cached login (`egeria-advisor login`) and '
         'defaults to whoever is signed in; --user overrides the namespace '
         'without changing whose Egeria token live calls use. With neither, '
         'this is the shared namespace, same as an anonymous web request. '
         'Deliberately does NOT fall back to the EGERIA_USER/.env '
         'service-account setting (advisor.config.settings.egeria_user) — '
         'that identity is a live-Egeria-call fallback only and is '
         'explicitly excluded from owning artifacts (see advisor/auth.py\'s '
         'module docstring).'
)
def ask(
    query: Optional[str],
    interactive: bool,
    context: Optional[str],
    format: str,
    no_citations: bool,
    no_color: bool,
    verbose: bool,
    track: bool,
    feedback: bool,
    debug: bool,
    agent: bool,
    user_id: Optional[str]
):
    """
    Egeria Advisor - AI-powered assistance for pyegeria
    
    Ask questions about Egeria concepts, get code examples, and receive
    guidance on using the pyegeria library.
    
    Examples:
    
        \b
        # Ask a direct question
        egeria-advisor "How do I create a glossary?"
        
        \b
        # Start interactive mode
        egeria-advisor --interactive
        
        \b
        # Get JSON output
        egeria-advisor "What is a collection?" --format=json
        
        \b
        # Provide context
        egeria-advisor "Show me examples" --context=glossary
    """
    # Configure logging level based on debug flag
    if not debug:
        # Disable all loguru logs when not in debug mode
        logger.remove()  # Remove all handlers
        # Redirect stderr to suppress library warnings (like amdgpu.ids)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)  # Redirect stderr (fd 2) to /dev/null
    
    # Disable colors if requested
    if no_color:
        console.no_color = True
    
    # Build options dict
    options = {
        'context': context,
        'format': format,
        'show_citations': not no_citations,
        'verbose': verbose,
        'track_metrics': track,
        'enable_feedback': feedback,
        'debug': debug,
        'agent_mode': agent,
    }
    
    from advisor.request_context import using_user
    from advisor.cli import session as cli_session

    # The cached login supplies the identity when --user was not given. Best
    # effort by design: asking a question answered from the local corpus needs
    # no Egeria, so a lapsed session must not block it — the commands that do
    # need one say so themselves (see cli/session.py). `egeria_credentials` is
    # threaded into the live-Egeria call sites reachable from here so they run
    # as the signed-in person rather than the .env service account.
    user_id, egeria_credentials = cli_session.resolve_identity(user_id)
    options['egeria_credentials'] = egeria_credentials

    try:
        # If agent mode is requested, automatically enable interactive
        if agent and not interactive and not query:
            interactive = True

        # Ambient identity for the whole session (see --user's help text):
        # any plan/report draft or document the chat/agent path creates
        # underneath this call — however many function calls deep, per
        # advisor/request_context.py's module docstring — lands in
        # user_id's namespace instead of the shared one. None (no --user and
        # no cached login) behaves exactly like an anonymous web request.
        with using_user(user_id):
            if interactive:
                # Start interactive mode
                start_interactive(options)
            elif query:
                # Handle direct query
                direct_query(query, options)
            else:
                # No query provided, show help
                ctx = click.get_current_context()
                click.echo(ctx.get_help())
                sys.exit(0)
    
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(0)
    except SystemExit:
        # Re-raise SystemExit to allow normal exit
        raise
    except Exception as e:
        if verbose:
            console.print_exception()
        else:
            console.print(f"[red]✗ Error:[/red] {e}")
            console.print("[dim]Use --verbose for more details[/dim]")
        sys.exit(1)


def direct_query(query: str, options: dict):
    """
    Handle a single direct query and exit.
    
    Parameters
    ----------
    query : str
        The user's query
    options : dict
        CLI options
    """
    verbose = options.get('verbose', False)
    
    # Show welcome message
    if verbose:
        console.print(Panel(
            "[bold cyan]Egeria Advisor[/bold cyan]\n"
            "AI-powered assistance for pyegeria",
            border_style="cyan"
        ))
        console.print()
    
    # Initialize RAG system
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            progress.add_task("Initializing advisor...", total=None)
            rag = get_rag_system()
    except Exception as e:
        console.print(f"[red]✗ Failed to initialize advisor:[/red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)
    
    # Check system health
    if verbose:
        health = rag.health_check()
        if not all(health.values()):
            console.print("[yellow]⚠ Warning: Some services are not healthy[/yellow]")
            for service, status in health.items():
                icon = "✓" if status else "✗"
                color = "green" if status else "red"
                console.print(f"  [{color}]{icon}[/{color}] {service}")
            console.print()
    
    # Execute query
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            progress.add_task("Processing query...", total=None)
            
            result = rag.query(
                user_query=query,
                include_context=True,
                track_metrics=options.get('track_metrics', True),
                # The cached login, when there is one. Same dict shape a
                # signed-in web request produces ({user_id, password: "",
                # token}), so every live-Egeria call site downstream builds its
                # pyegeria client with `apply_token` and Egeria's provenance
                # records the person rather than the .env service account.
                # None when nothing is signed in — the pre-existing anonymous
                # behaviour, unchanged.
                # `egeria_authenticated` is deliberately left at its default:
                # the CLI has always passed True, live paths fall back to the
                # service account when no credentials are supplied, and
                # flipping it to False for an unsigned-in CLI would newly
                # refuse live-data questions that work today.
                egeria_credentials=options.get('egeria_credentials'),
            )
    except Exception as e:
        console.print(f"[red]✗ Query failed:[/red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)
    
    # Format and display response
    formatter = ResponseFormatter(
        format_type=options.get('format', 'text'),
        show_citations=options.get('show_citations', True),
        verbose=verbose
    )
    
    formatter.display(result, console)
    # MLflow >=2.10 spawns non-daemon worker threads that block Python shutdown.
    # All output is flushed by Rich; force-exit now so the shell prompt returns immediately.
    sys.stdout.flush()
    os._exit(0)


def start_interactive(options: dict):
    """
    Start interactive REPL mode.
    
    Parameters
    ----------
    options : dict
        CLI options
    """
    verbose = options.get('verbose', False)
    agent_mode = options.get('agent_mode', False)
    
    # Use agent mode if requested
    if agent_mode:
        # Show welcome banner for agent mode
        console.print(Panel(
            "[bold cyan]Egeria Advisor - Agent Mode[/bold cyan]\n\n"
            "Conversational AI assistant with memory and context awareness.\n\n"
            "[dim]Commands:[/dim]\n"
            "  [cyan]/help[/cyan]     - Show all commands\n"
            "  [cyan]/tools[/cyan]    - List MCP tools\n"
            "  [cyan]/execute[/cyan]  - Execute MCP tool (alias: /e)\n"
            "  [cyan]/clear[/cyan]    - Clear conversation history\n"
            "  [cyan]/history[/cyan]  - Show conversation history\n"
            "  [cyan]/stats[/cyan]    - Show agent statistics\n"
            "  [cyan]/exit[/cyan]     - Exit (or Ctrl+D)\n\n"
            "[dim]Type your question or /help for more info[/dim]",
            border_style="cyan"
        ))
        console.print()
        
        # Start agent session
        session = AgentInteractiveSession(options, console)
        session.run()
        return
    
    # Standard RAG mode
    # Show welcome banner
    feedback_status = "enabled" if options.get('enable_feedback', True) else "disabled"
    feedback_commands = ""
    if options.get('enable_feedback', True):
        feedback_commands = (
            "  [cyan]/feedback[/cyan] - Provide feedback on last response\n"
            "  [cyan]/stats[/cyan]    - Show feedback statistics\n"
        )
    
    console.print(Panel(
        "[bold cyan]Egeria Advisor - Interactive Mode[/bold cyan]\n\n"
        "Ask questions about Egeria concepts, get code examples, and receive guidance.\n\n"
        "[dim]Commands:[/dim]\n"
        "  [cyan]/help[/cyan]     - Show help\n"
        "  [cyan]/clear[/cyan]    - Clear conversation context\n"
        "  [cyan]/history[/cyan]  - Show query history\n"
        f"{feedback_commands}"
        "  [cyan]/exit[/cyan]     - Exit (or Ctrl+D)\n\n"
        f"[dim]Feedback collection: {feedback_status}[/dim]\n"
        "[dim]Type your question and press Enter[/dim]",
        border_style="cyan"
    ))
    console.print()
    
    # Initialize RAG system
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True
        ) as progress:
            progress.add_task("Initializing advisor...", total=None)
            rag = get_rag_system()
    except Exception as e:
        console.print(f"[red]✗ Failed to initialize advisor:[/red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)
    
    # Check system health
    health = rag.health_check()
    if not all(health.values()):
        console.print("[yellow]⚠ Warning: Some services are not healthy[/yellow]")
        for service, status in health.items():
            icon = "✓" if status else "✗"
            color = "green" if status else "red"
            console.print(f"  [{color}]{icon}[/{color}] {service}")
        console.print()
    else:
        console.print("[green]✓[/green] All systems ready\n")
    
    # Start interactive session
    session = InteractiveSession(rag, options, console)
    session.run()


# ---------------------------------------------------------------------------
# login / logout — the CLI's cached session (2026-09-04)
# ---------------------------------------------------------------------------

@cli.command("login")
@click.option(
    "--user", "user_id", default=None,
    help="Egeria user id to sign in as. Prompted for if omitted; defaults to "
         "the .env EGERIA_USER for convenience on a single-user box.",
)
def login_command(user_id: Optional[str]) -> None:
    """Sign in to Egeria and cache the session for later commands.

    The password is **prompted for, never taken as an argument** — an argument
    would land in the shell history, in `ps` output and in any process
    accounting the box keeps. It is exchanged for an Egeria bearer token once
    and then discarded; what gets written to disk is this app's session JWT
    (mode 0600, under $XDG_CONFIG_HOME/trellis/egeria-advisor/), never the
    password.

    Egeria's tokens last an hour, so this is a session, not a stored
    credential: when it lapses, commands that need Egeria say so in one line.
    """
    from advisor.auth import login_with_password
    from advisor.cli import session as cli_session

    if not user_id:
        from advisor.config import settings
        user_id = click.prompt("Egeria user id", default=settings.egeria_user or None)
    from advisor.auth import is_service_account, SERVICE_ACCOUNT_DETAIL
    if is_service_account(user_id):
        console.print(f"[red]✗ {SERVICE_ACCOUNT_DETAIL}[/red]")
        sys.exit(1)
    password = click.prompt("Password", hide_input=True)

    egeria_token = login_with_password(user_id, password)
    del password
    if not egeria_token:
        # One message for three causes (bad credentials, Egeria unreachable,
        # pyegeria missing) because `login_with_password` cannot distinguish
        # them and guessing would be worse than naming all three.
        console.print(
            "[red]✗ Sign-in failed.[/red] Check the user id and password, and that the "
            "Egeria platform in .env (EGERIA_VIEW_SERVER_URL) is reachable."
        )
        sys.exit(1)

    record = cli_session.save_login(user_id, egeria_token)
    when = f" until {record.expires_at_local:%H:%M}" if record.expires_at_local else ""
    console.print(f"[green]✓ Signed in as {user_id}[/green]{when}")
    console.print(f"[dim]Session cached at {record.path} (mode 0600)[/dim]")


@cli.command("logout")
def logout_command() -> None:
    """Forget the cached session.

    Local only, and honestly so: there is no server-side session to revoke —
    the Egeria bearer token inside the cached JWT stays valid at the platform
    until its own expiry. Deleting the file is the whole of what logout can do.
    """
    from advisor.cli import session as cli_session

    if cli_session.clear_session():
        console.print("[green]✓ Signed out.[/green]")
    else:
        console.print("[dim]Not signed in — nothing to do.[/dim]")


@click.command("web")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host")
@click.option("--port", default=8880, show_default=True, help="Bind port")
@click.option("--reload/--no-reload", default=True, show_default=True,
              help="Auto-reload the server on code changes (dev mode)")
def web_command(host: str, port: int, reload: bool):
    """Launch the browser-based web UI."""
    import uvicorn
    mode = "auto-reload on" if reload else "auto-reload off"
    console.print(f"[cyan]Starting Egeria Advisor web UI at http://{host}:{port} ({mode})[/cyan]")
    uvicorn.run(
        "advisor.web.app:app",
        host=host,
        port=port,
        reload=reload,
        # Watch the advisor package so edits to Python and static assets restart the server.
        reload_dirs=["advisor"] if reload else None,
        log_level="warning",
    )


if __name__ == '__main__':
    cli()