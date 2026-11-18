"""Onboarding wizard — interactive flow for adding a new GitHub project."""
from __future__ import annotations

import re
from pathlib import Path, PurePath

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from resource_explorer.github.analyzer import RepoAnalyzer
from resource_explorer.ingestion.vendored import is_vendored_abs
from resource_explorer.registry import Project, ProjectRegistry


#: TR-46: a failed `add` says so plainly, after its reason, and the command exits non-zero.
NOTHING_ADDED = "[red]Nothing was added.[/red]"


class OnboardingWizard:
    """
    Walks the user through adding a new project:
      1. Validate GitHub URL
      2. Fetch repo metadata
      3. Analyze repo → propose collections
      4. User confirms or customizes collection selection
      5. Register project
      6. Trigger ingestion pipeline
    """

    def __init__(self) -> None:
        self.console = Console()
        self.registry = ProjectRegistry()
        self.analyzer = RepoAnalyzer()

    def run(
        self,
        github_url: str,
        accept_all: bool = False,
        subproject_path: str | None = None,
        slug_override: str | None = None,
        extra_docs_paths: list[str] | None = None,
        local_path: str | None = None,
        group_slug: str | None = None,
    ) -> bool:
        """Analyse, register and ingest. True when the project was added (or re-indexed); False when nothing was
        added (the analysis failed, or no collection was chosen), after saying so (TR-46: `add` exits non-zero)."""
        label = f"{github_url}" + (f" [{subproject_path}]" if subproject_path else "")
        self.console.print(f"\n[bold]Analyzing[/bold] {label} ...")

        try:
            plan = self.analyzer.analyze(github_url, subpath=subproject_path)
            plan = self._augment_plan_for_extra_paths(plan, extra_docs_paths or [], local_path)
        except ValueError as exc:
            self.console.print(f"[red]Error:[/red] {exc}")
            self.console.print(NOTHING_ADDED)
            return False
        except Exception as exc:
            msg = str(exc)
            if "404" in msg or "Not Found" in msg:
                self.console.print(
                    f"[red]Repository not found:[/red] {github_url}\n"
                    "[dim]Check the URL is a public repo (not an org or private repo).[/dim]"
                )
            elif "401" in msg or "403" in msg:
                self.console.print(
                    "[red]Authentication error.[/red] Check GITHUB_TOKEN in .env has repo read scope."
                )
            elif "rate limit" in msg.lower():
                self.console.print(
                    "[red]GitHub API rate limit exceeded.[/red] Wait and retry, or use a token with higher limits."
                )
            else:
                self.console.print(f"[red]Failed to analyze repository:[/red] {exc}")
            self.console.print(NOTHING_ADDED)
            return False

        slug = re.sub(r"[^a-z0-9_]", "_", slug_override.lower()) if slug_override else self._url_to_slug(github_url)
        if subproject_path and not slug_override:
            # Derive slug from subpath when no override given: repo_subpath
            subpath_slug = re.sub(r"[^a-z0-9_]", "_", subproject_path.strip("/").replace("/", "_").lower())
            slug = f"{slug}_{subpath_slug}"

        # Find parent slug (project with same URL, no subproject_path)
        parent_slug = ""
        if subproject_path:
            parent = self.registry.get_by_github_url(github_url)
            if parent and not parent.subproject_path:
                parent_slug = parent.slug

        # Check by normalized URL as well as by slug — _url_to_slug() below
        # doesn't strip a trailing ".git" (unlike get_by_github_url()'s own
        # normalization), so "https://…/repo" and "https://…/repo.git" hash
        # to two different slugs ("repo" vs "repo_git") and would otherwise
        # both register as new (found live: a real "egeria-workspaces"/
        # "egeria-workspaces.git" duplicate — same fix org_importer.py's
        # OrgImporter.import_repo() already has). Skip this for a subproject
        # registration — a second project scoped to a different subpath of
        # the same URL is legitimately a distinct entry.
        existing_by_url = None if subproject_path else self.registry.get_by_github_url(github_url)
        if existing_by_url:
            slug = existing_by_url.slug
        if self.registry.exists(slug):
            self.console.print(f"[yellow]Project '{slug}' is already registered.[/yellow]")
            if accept_all or Confirm.ask("Re-index it?"):
                project = self.registry.get(slug)
                # Use newly provided extra_docs_paths if given, otherwise keep stored ones
                effective_extra = extra_docs_paths if extra_docs_paths is not None else (project.extra_docs_paths or [])
                if extra_docs_paths is not None and extra_docs_paths != project.extra_docs_paths:
                    self.registry.update_extra_docs_paths(slug, extra_docs_paths)
                plan = self._augment_plan_for_extra_paths(plan, effective_extra, local_path)
                # Drop existing collections so re-ingestion starts clean (no duplicate vectors). Chat votes on their
                # chunks are read first and carried to the new ids (chunk_votes, R0), so a re-index keeps them.
                from resource_explorer import chunk_votes
                from resource_explorer.vector_store_pg import MultiCollectionStore, record_inserts
                store = MultiCollectionStore()
                voted = {c: chunk_votes.snapshot(store, c) for c in project.collections}
                with record_inserts(set(project.collections)) as inserted:
                    for c in project.collections:
                        store.drop_collection(c)
                    self._trigger_ingestion(
                        slug, plan,
                        subproject_path=project.subproject_path or None,
                        extra_docs_paths=effective_extra or None,
                        local_path=local_path,
                    )
                for c, v in voted.items():
                    if v:
                        self.console.print(f"[dim]{c}: {chunk_votes.carry(v, inserted).words()}[/dim]")
                return True
            self.console.print("[dim]Left as it is: nothing re-indexed.[/dim]")
            return True

        self._show_plan(plan)

        if accept_all:
            confirmed_types = plan.proposed_collections
            self.console.print("[dim]Accepting all proposed collections (--yes).[/dim]")
        else:
            confirmed_types = self._confirm_collections(plan)
        if not confirmed_types:
            self.console.print("[yellow]No collections selected — aborting.[/yellow]")
            self.console.print(NOTHING_ADDED)
            return False

        docs_url = "" if accept_all else Prompt.ask(
            "Documentation site URL (optional, press Enter to skip)", default=""
        )

        display_name = plan.display_name
        if subproject_path:
            display_name = f"{plan.display_name} / {subproject_path}"

        project = Project(
            slug=slug,
            display_name=display_name,
            github_url=github_url,
            description=plan.description,
            homepage_url=plan.homepage_url,
            docs_url=docs_url,
            collections=[f"{slug}_{ct.name}" for ct in confirmed_types],
            subproject_path=subproject_path or "",
            parent_slug=parent_slug,
            extra_docs_paths=extra_docs_paths or [],
            group_slug=group_slug or "",
        )
        self.registry.add(project)
        self.console.print(f"\n[green]Registered '{display_name}' as '{slug}'.[/green]")
        self._trigger_ingestion(slug, plan, confirmed_types, subproject_path=subproject_path,
                                extra_docs_paths=extra_docs_paths, local_path=local_path)
        return True

    def _show_plan(self, plan) -> None:
        table = Table(title=f"Proposed collections for {plan.display_name}")
        table.add_column("Collection", style="cyan")
        table.add_column("Description")
        table.add_column("Files", justify="right")
        for ct in plan.proposed_collections:
            table.add_row(ct.name, ct.description, str(plan.file_counts.get(ct.name, "?")))
        self.console.print(table)

    def _confirm_collections(self, plan) -> list:
        self.console.print("\nSelect collections to create (press Enter to accept all):")
        selected = []
        for ct in plan.proposed_collections:
            if Confirm.ask(f"  Include [cyan]{ct.name}[/cyan]?", default=True):
                selected.append(ct)
        return selected

    def _trigger_ingestion(
        self,
        slug: str,
        plan,
        collection_types=None,
        subproject_path: str | None = None,
        extra_docs_paths: list[str] | None = None,
        local_path: str | None = None,
    ) -> None:
        from resource_explorer.ingestion.pipeline import IngestionPipeline
        self.console.print("\n[bold]Starting ingestion...[/bold]")
        pipeline = IngestionPipeline()
        pipeline.run(
            slug,
            plan.github_url,
            collection_types or plan.proposed_collections,
            subproject_path=subproject_path,
            extra_docs_paths=extra_docs_paths,
            local_path=local_path,
        )
        self._post_ingestion(slug, plan.github_url)

    def _post_ingestion(self, slug: str, github_url: str) -> None:
        # Store initial commit SHA so incremental refresh has a baseline
        try:
            from resource_explorer.github.client import GitHubClient
            client = GitHubClient()
            repo = client.get_repo(github_url)
            sha = client.get_latest_commit_sha(repo)
            self.registry.update_commit_sha(slug, sha)
        except Exception:
            pass

        # Fetch initial GitHub stats into SQLite
        self.console.print("[dim]Fetching project statistics...[/dim]")
        try:
            from resource_explorer.github.stats_fetcher import StatsFetcher
            result = StatsFetcher().fetch(slug)
            if "commits_fetch_error" in result:
                self.console.print(
                    f"[yellow]Warning:[/yellow] commit history could not be fetched: "
                    f"{result['commits_fetch_error']}\n"
                    f"[dim]Run 'project-explorer refresh {slug}' to retry.[/dim]"
                )
            else:
                n = result.get("commits_fetched", 0)
                self.console.print(f"[dim]Stats fetched ({n} commits stored).[/dim]")
        except Exception as exc:
            self.console.print(f"[dim]Stats fetch skipped: {exc}[/dim]")

    def _augment_plan_for_extra_paths(self, plan, extra_docs_paths: list[str], local_path: str | None):
        """
        Add collection types implied by extra_docs_paths that the analyzer missed.

        The analyzer only scans the --subpath directory.  Extra paths (outside the
        subpath) may contain examples, markdown, or PDFs whose collection types were
        never proposed.  This method scans those paths — from disk when --from-local
        is available, otherwise by heuristic on path names — and appends any missing
        collection types to plan.proposed_collections.
        """
        from resource_explorer.configdata.collection_config import COLLECTION_TYPES

        if not extra_docs_paths:
            return plan

        proposed_names = {ct.name for ct in plan.proposed_collections}
        candidates = ("examples", "markdown_docs", "pdfs", "api_reference")
        to_add = []

        if local_path:
            root = Path(local_path).expanduser().resolve()
            found_exts: set[str] = set()
            for rel in extra_docs_paths:
                p = root / rel
                if p.is_file():
                    found_exts.add(p.suffix.lower())
                elif p.is_dir():
                    for f in p.rglob("*"):
                        if f.is_file() and not is_vendored_abs(f, p):
                            found_exts.add(f.suffix.lower())
            for name in candidates:
                if name not in proposed_names:
                    ctype = COLLECTION_TYPES.get(name)
                    if ctype and any(ext in found_exts for ext in ctype.file_extensions):
                        to_add.append(ctype)
        else:
            # Heuristic when no local clone is available
            for rel in extra_docs_paths:
                parts = PurePath(rel).parts
                first = parts[0].lower() if parts else ""
                suffix = PurePath(rel).suffix.lower()
                if first in RepoAnalyzer.EXAMPLE_DIRS and "examples" not in proposed_names:
                    ctype = COLLECTION_TYPES.get("examples")
                    if ctype:
                        to_add.append(ctype)
                        proposed_names.add("examples")
                if suffix == ".md" and "markdown_docs" not in proposed_names:
                    ctype = COLLECTION_TYPES.get("markdown_docs")
                    if ctype:
                        to_add.append(ctype)
                        proposed_names.add("markdown_docs")
                if suffix == ".pdf" and "pdfs" not in proposed_names:
                    ctype = COLLECTION_TYPES.get("pdfs")
                    if ctype:
                        to_add.append(ctype)
                        proposed_names.add("pdfs")

        if to_add:
            plan.proposed_collections = plan.proposed_collections + to_add
            for ct in to_add:
                plan.file_counts[ct.name] = "extra"
            names = ", ".join(ct.name for ct in to_add)
            self.console.print(f"[dim]Added from extra-docs-paths: {names}[/dim]")

        return plan

    @staticmethod
    def _url_to_slug(url: str) -> str:
        url = url.rstrip("/")
        slug = url.split("/")[-1]
        return re.sub(r"[^a-z0-9_]", "_", slug.lower())
