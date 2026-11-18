"""
CLI subcommand group for managing Governance Plan Documents.

Entry point: egeria-advisor-plans

Commands:
  list               List inbox and outbox plans
  show  <doc_id>     Print plan content with TODO markers highlighted
  edit  <doc_id>     Open in $EDITOR, diff the changes, confirm save
  execute <doc_id>   Execute an inbox plan against Dr.Egeria [--dry-run]
  versions <doc_id>  List saved versions for a plan
"""
from __future__ import annotations

import difflib
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from loguru import logger

console = Console()

_TODO_PATTERN = "<!-- TODO: fill in -->"
_TODO_STYLED  = "[bold orange1]⚠ fill in[/bold orange1]"


def _highlight_todos(text: str) -> str:
    """Replace TODO markers with a Rich-markup-highlighted span."""
    return text.replace(_TODO_PATTERN, _TODO_STYLED)


def _get_doc_manager():
    from advisor.governance_docs import get_doc_manager
    return get_doc_manager()


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------

@click.group()
@click.option(
    '--user',
    'user_id',
    default=None,
    help="Egeria user id whose namespace this invocation operates in "
         "(new documents land there, and `list`/`show`/etc. can see that "
         "user's own namespace in addition to the shared one). Not "
         "required — it defaults to whoever `egeria-advisor login` signed "
         "in, and with neither it behaves like an anonymous web request "
         "(shared namespace only). See egeria-advisor's own --user for why "
         "this doesn't fall back to the EA_DAEMON_USER_ID service-account "
         "setting."
)
@click.pass_context
def plans(ctx: click.Context, user_id: Optional[str]):
    """Manage Governance Plan Documents (inbox / outbox / versions)."""
    from advisor.request_context import using_user
    from advisor.cli import session as cli_session

    # Same resolution as `egeria-advisor` itself: the cached login names the
    # user unless --user overrides it, and the credentials ride along for the
    # subcommands that make live Egeria calls. Resolved here, in the group, so
    # `list`/`show`/`versions` — which touch no Egeria at all — are not made to
    # care about a lapsed session.
    user_id, egeria_credentials = cli_session.resolve_identity(user_id)
    cm = using_user(user_id)
    cm.__enter__()
    ctx.call_on_close(lambda: cm.__exit__(None, None, None))
    ctx.obj = {"user_id": user_id, "egeria_credentials": egeria_credentials}


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@plans.command("list")
@click.option("--outbox", "show_outbox", is_flag=True, default=False, help="Include outbox plans")
def list_plans(show_outbox: bool):
    """List plans in inbox (and optionally outbox)."""
    from advisor.request_context import current_user_id
    dm = _get_doc_manager()
    requester = current_user_id()
    inbox = dm.list_inbox(requester_user_id=requester)
    outbox = dm.list_outbox(requester_user_id=requester) if show_outbox else []

    if not inbox and not outbox:
        console.print("[dim]No plans found.[/dim]")
        return

    def _render(items: list, label: str, style: str):
        if not items:
            return
        t = Table(title=label, border_style=style, show_lines=False)
        t.add_column("Doc ID", style="dim", no_wrap=True)
        t.add_column("Title")
        t.add_column("Status", justify="center")
        for p in items:
            t.add_row(p["doc_id"], p["title"], p["status"])
        console.print(t)

    _render(inbox, "Inbox", "violet")
    if show_outbox:
        _render(outbox, "Outbox", "green")


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@plans.command("show")
@click.argument("doc_id")
def show_plan(doc_id: str):
    """Print a plan document with TODO markers highlighted."""
    dm = _get_doc_manager()
    content = dm.load(doc_id)
    if content is None:
        console.print(f"[red]Plan {doc_id!r} not found.[/red]")
        sys.exit(1)

    highlighted = _highlight_todos(content)
    # Render as Rich Markdown; TODO markers are already Rich markup so we print
    # each line individually to mix markup + markdown correctly.
    console.print(Panel(
        Text.from_markup(highlighted),
        title=f"[bold]{doc_id}[/bold]",
        border_style="violet",
    ))


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------

@plans.command("edit")
@click.argument("doc_id")
@click.option("--editor", envvar="EDITOR", default="vi", show_default=True,
              help="Editor to open (defaults to $EDITOR or vi)")
def edit_plan(doc_id: str, editor: str):
    """Open a plan in $EDITOR, show a diff, and confirm before saving."""
    dm = _get_doc_manager()
    original = dm.load(doc_id)
    if original is None:
        console.print(f"[red]Plan {doc_id!r} not found.[/red]")
        sys.exit(1)

    # Verify the plan is in inbox — outbox docs are immutable
    if not (dm.inbox_path() / f"{doc_id}.md").exists():
        console.print(f"[yellow]{doc_id!r} is in outbox and cannot be edited.[/yellow]")
        sys.exit(1)

    # Write to a temp file and open in editor
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix=f"{doc_id}_", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(original)
        tmp_path = tf.name

    try:
        ret = os.system(f'{editor} "{tmp_path}"')
        if ret != 0:
            console.print(f"[yellow]Editor exited with code {ret}.[/yellow]")

        edited = Path(tmp_path).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if edited == original:
        console.print("[dim]No changes made.[/dim]")
        return

    # Show unified diff
    diff_lines = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        edited.splitlines(keepends=True),
        fromfile=f"{doc_id}.md (original)",
        tofile=f"{doc_id}.md (edited)",
        lineterm="",
    ))

    if diff_lines:
        console.print()
        for line in diff_lines[:120]:  # cap at 120 diff lines to avoid flooding
            if line.startswith("+++") or line.startswith("---"):
                console.print(f"[bold]{line}[/bold]", end="")
            elif line.startswith("+"):
                console.print(f"[green]{line}[/green]", end="")
            elif line.startswith("-"):
                console.print(f"[red]{line}[/red]", end="")
            elif line.startswith("@@"):
                console.print(f"[cyan]{line}[/cyan]", end="")
            else:
                console.print(line, end="")
        if len(diff_lines) > 120:
            console.print(f"\n[dim]… {len(diff_lines) - 120} more diff lines not shown[/dim]")
        console.print()

    if click.confirm("Save changes?", default=False):
        dm.update(doc_id, edited)
        console.print(f"[green]✓ Saved.[/green] Previous version backed up to versions/.")
    else:
        console.print("[dim]Changes discarded.[/dim]")


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

@plans.command("execute")
@click.argument("doc_id")
@click.option("--dry-run", is_flag=True, default=False, help="Show extracted commands without executing")
@click.option("--perspective", default=None, help="Role context for outcome reports")
@click.pass_context
def execute_plan(ctx: click.Context, doc_id: str, dry_run: bool, perspective: str | None):
    """Execute an inbox plan document against Dr.Egeria."""
    from advisor.agents.governance_plan_agent import get_governance_plan_agent
    from advisor.cli import session as cli_session

    # The one command in this group that writes to Egeria, so the one that
    # insists on a live session rather than degrading to the service account:
    # everything a plan creates gets `Ownership`, and an artifact owned by
    # `erinoverview` because someone's session lapsed is worse than a refusal.
    # `--dry-run` extracts commands without executing them, so it is exempt.
    if dry_run:
        egeria_credentials = ctx.obj.get("egeria_credentials") if ctx.obj else None
    else:
        egeria_credentials = cli_session.require_egeria_credentials(console)

    agent = get_governance_plan_agent()
    if not dry_run:
        # The plan confirm gate: show exactly what would run, ask, then run it with the shown digest.
        shown = agent.prepare_execute(doc_id, egeria_credentials=egeria_credentials)
        if not shown.get("confirm_required"):
            console.print(Panel(Markdown(shown.get("response", "")), title=f"[bold]{doc_id}[/bold]",
                                border_style="red"))
            sys.exit(1)
        console.print(Panel(Markdown(f"```markdown\n{shown['command_section'].strip()}\n```"),
                            title=f"[bold]{doc_id}[/bold]: {shown['command_count']} command(s)",
                            border_style="cyan"))
        if not click.confirm(f"Process these {shown['command_count']} command(s) in Egeria as you?", default=False):
            agent.cancel_execute(shown["pending_id"])
            console.print("[yellow]Cancelled: nothing was written.[/yellow]")
            return

    label = f"[dry run] {doc_id}" if dry_run else doc_id
    console.print(f"[cyan]Executing plan:[/cyan] {label}")

    try:
        if dry_run:
            result = agent.execute(doc_id, perspective=perspective, dry_run=True,
                                   egeria_credentials=egeria_credentials)
        else:
            result = agent.execute(doc_id, perspective=perspective, egeria_credentials=egeria_credentials,
                                   pending_id=shown["pending_id"], digest=shown["plan_digest"])
    except Exception as exc:
        console.print(f"[red]✗ Execution failed:[/red] {exc}")
        sys.exit(1)

    status = result.get("query_type", "")
    response = result.get("response", "")

    if dry_run:
        console.print(Markdown(response))
    else:
        exec_status = result.get("execution_output", "")
        border = "green" if "Success" in response else "yellow" if "Partial" in response else "red"
        console.print(Panel(Markdown(response), title=f"[bold]{doc_id}[/bold]", border_style=border))

    if result.get("doc_id"):
        console.print(f"\n[dim]Document: {result['doc_id']}.md[/dim]")


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------

@plans.command("versions")
@click.argument("doc_id")
def list_versions(doc_id: str):
    """List saved edit versions for a plan document."""
    dm = _get_doc_manager()
    vers = dm.list_versions(doc_id)
    if not vers:
        console.print(f"[dim]No versions found for {doc_id!r}.[/dim]")
        return
    t = Table(title=f"Versions of {doc_id}", border_style="dim")
    t.add_column("Version file")
    t.add_column("Path", style="dim")
    for v in vers:
        t.add_row(v["version_file"], v["path"])
    console.print(t)
