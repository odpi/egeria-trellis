"""`resource-explorer dr-egeria run <path.md> [--dry-run] [--force]`: the CLI door to the ad-hoc Dr.Egeria runner.

Thin on purpose: it calls `dr_egeria_runner.run_document`, the same function the admin route calls, and prints what
that function returns (read from the persisted activity row for a real run).
"""
from __future__ import annotations

import getpass

import typer
from rich.console import Console
from rich.table import Table

console = Console()

dr_egeria_app = typer.Typer(name="dr-egeria", help="Run Dr.Egeria documents from docs/dr-egeria/ as RE's service account.")


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
):
    """Run one Dr.Egeria document (and its pyegeria steps) against Egeria as RE's service account."""
    from resource_explorer import dr_egeria_runner as runner

    try:
        out = runner.run_document(path, dry_run=dry_run, force=force, requested_by=f"cli:{getpass.getuser()}")
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
