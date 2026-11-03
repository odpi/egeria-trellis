"""`resource-explorer dr-egeria run <path.md> --as <egeria-user> [--dry-run] [--force]`: the CLI door to the ad-hoc runner.

Thin on purpose: it calls `dr_egeria_runner.run_document`, the same function the admin route calls, and prints what
that function returns (read from the persisted activity row for a real run).

**Who it applies as: the person named by `--as`, nothing else.** The password is read with `getpass` at the terminal,
or from `RE_DR_EGERIA_PASSWORD` set for that one invocation (`op run -- resource-explorer dr-egeria run ...`);
never from the shared .env, never printed or logged. With no `--as` a real run REFUSES ("no Egeria identity set;
pass --as or sign in"); a dry run needs no identity. "applying as <user> from --as" is printed before the first command.
"""
from __future__ import annotations

import getpass
import os

import typer
from rich.console import Console
from rich.table import Table

console = Console()

dr_egeria_app = typer.Typer(name="dr-egeria", help="Run Dr.Egeria documents from docs/dr-egeria/ as the named Egeria user (--as).")


def _print(out: dict) -> None:
    table = Table(title=f"{out.get('document', '')} — {out.get('status', '')}", show_lines=False)
    for col in ("#", "kind", "command", "element", "status", "repeat", "error / note"):
        table.add_column(col, overflow="fold")
    for n, c in enumerate(out.get("commands") or [], 1):
        repeat = "safe" if c.get("safe_to_repeat") else "NOT SAFE"
        note = c.get("error") or c.get("outcome") or c.get("repeat_note") or ""
        table.add_row(str(n), c.get("kind", ""), c.get("command", ""), c.get("element", ""), c.get("status", ""),
                      repeat, note)
    console.print(table)
    unsafe = out.get("not_safe_to_repeat") or []
    if unsafe:
        console.print(f"[yellow]not safe to repeat:[/yellow] {', '.join(unsafe)}")
    if out.get("batch_note"):
        console.print(f"[yellow]note:[/yellow] {out['batch_note']}")
    for err in out.get("errors") or []:
        console.print(f"[red]invalid:[/red] {err}")
    if out.get("summary"):
        console.print(out["summary"])
    if out.get("activity_id"):
        console.print(f"[dim]activity {out['activity_id']}[/dim]")


@dr_egeria_app.command("run")
def run(
    path: str = typer.Argument(help="A .md document under docs/dr-egeria/ (relative to it, or a path inside it)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse and validate; list the commands; write nothing"),
    force: bool = typer.Option(False, "--force",
                               help="Run a document whose content was already applied (commands marked NOT SAFE "
                                    "may create duplicates)"),
    as_user: str = typer.Option("", "--as", help="The Egeria user to apply the document as (required unless "
                                                 "--dry-run). Password: prompted, or RE_DR_EGERIA_PASSWORD"),
):
    """Run one Dr.Egeria document (and its pyegeria steps) against Egeria as the Egeria user named by --as."""
    from resource_explorer import dr_egeria_runner as runner
    from resource_explorer.bootstrap import SOURCE_ADHOC_AS, DrEgeriaIdentity, NoDrEgeriaIdentity

    identity = None
    if not dry_run:
        if not as_user.strip():
            console.print("[red]refused:[/red] no Egeria identity set; pass --as or sign in")
            raise typer.Exit(1)
        from resource_explorer.egeria_clients import DaemonSignInRefused, refuse_daemon_sign_in

        try:
            refuse_daemon_sign_in(as_user)          # --as RE's own service account is refused, not run
        except DaemonSignInRefused as exc:
            console.print(f"[red]refused:[/red] {exc}")
            raise typer.Exit(1)
        password = os.environ.get("RE_DR_EGERIA_PASSWORD") or getpass.getpass(f"Egeria password for {as_user}: ")
        try:
            identity = DrEgeriaIdentity(user=as_user.strip(), source=SOURCE_ADHOC_AS, password=password)
        except NoDrEgeriaIdentity as exc:
            console.print(f"[red]refused:[/red] {exc}")
            raise typer.Exit(1)
    try:
        out = runner.run_document(path, dry_run=dry_run, force=force, requested_by=f"cli:{getpass.getuser()}",
                                  identity=identity, announce=lambda line: console.print(line, markup=False))
    except runner.DocumentRefused as exc:
        console.print(f"[red]refused:[/red] {exc}")
        raise typer.Exit(1)
    except runner.RepeatRefused as exc:
        console.print(f"[red]refused:[/red] {exc}")
        console.print("Pass --force to run it again on purpose.")
        raise typer.Exit(1)
    except runner.RunRefused as exc:
        console.print(f"[red]refused:[/red] {exc}")
        raise typer.Exit(1)
    _print(out)
    if out.get("status") not in ("ok", "planned"):
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    app.add_typer(dr_egeria_app)
