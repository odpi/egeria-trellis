"""Textual TUI — full-screen interactive interface for Resource Explorer."""
from __future__ import annotations

import hashlib
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)


class ProjectItem(ListItem):
    """A single project row in the sidebar."""

    def __init__(self, slug: str, display_name: str, status: str, collections: int, group_slug: str = "") -> None:
        super().__init__()
        self.slug = slug
        self.display_name = display_name
        self.status = status
        self.collections = collections
        self.group_slug = group_slug

    def compose(self) -> ComposeResult:
        status_color = {
            "active": "green",
            "indexing": "yellow",
            "error": "red",
            "paused": "dim",
        }.get(self.status, "white")
        
        group_tag = f" [magenta]({self.group_slug})[/magenta]" if self.group_slug else ""
        yield Label(
            f"[bold]{self.display_name}[/bold]{group_tag}\n"
            f"[{status_color}]{self.status}[/{status_color}]  "
            f"[dim]{self.collections} collection(s)[/dim]"
        )


class ChatMessage(Static):
    """A single chat bubble — user or assistant."""

    def __init__(self, role: str, text: str = "") -> None:
        self._role = role
        self._color = "cyan" if role == "You" else "green"
        super().__init__(self._format(text))
        self.add_class(f"msg-{role.lower()}")

    def _format(self, text: str) -> str:
        return f"[bold {self._color}]{self._role}:[/bold {self._color}]\n{text}"

    def append_text(self, text: str) -> None:
        """Append a chunk to the existing content (called from worker thread via call_from_thread)."""
        if not hasattr(self, "_raw"):
            self._raw = ""
        self._raw += text
        self.update(self._format(self._raw))

    def set_text(self, text: str) -> None:
        """Replace the full text content."""
        if not hasattr(self, "_raw"):
            self._raw = ""
        self._raw = text
        self.update(self._format(text))


class ProjectExplorerApp(App):
    """
    Full-screen TUI for Resource Explorer.

    Layout:
      ┌─────────────┬──────────────────────────────────┐
      │  Projects   │  Chat / response area            │
      │  (sidebar)  │                                  │
      │             ├──────────────────────────────────┤
      │             │  Input bar                       │
      └─────────────┴──────────────────────────────────┘

    Keys:
      Tab      — move focus between sidebar and chat
      Enter    — submit query (when input focused)
      f        — feedback thumbs-up/down for last response
      r        — refresh selected project
      Ctrl+C   — quit
    """

    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        layout: horizontal;
        height: 1fr;
    }
    #sidebar {
        width: 28;
        border-right: solid $accent-darken-2;
        background: $surface-darken-1;
    }
    #sidebar-label {
        background: $accent-darken-2;
        color: $text;
        padding: 0 1;
        text-align: center;
        height: 1;
    }
    #project-list {
        height: 1fr;
    }
    #chat-area {
        layout: vertical;
        width: 1fr;
    }
    #chat-log {
        height: 1fr;
        overflow-y: auto;
        padding: 1 2;
    }
    #status-bar {
        height: 1;
        background: $accent-darken-3;
        color: $text-muted;
        padding: 0 2;
    }
    #input-row {
        height: 3;
        layout: horizontal;
        border-top: solid $accent-darken-2;
        padding: 0 1;
    }
    #query-input {
        width: 1fr;
    }
    ChatMessage {
        margin: 0 0 1 0;
    }
    ProjectItem {
        padding: 0 1;
    }
    ProjectItem:hover {
        background: $accent-darken-1;
    }
    ProjectItem.--highlight {
        background: $accent;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("tab", "focus_next", "Switch pane", show=False),
        Binding("f", "feedback", "Feedback", show=True),
        Binding("r", "refresh_project", "Refresh", show=True),
    ]

    _CLARIFICATION_PREFIX = "Which project are you asking about?"

    selected_project: reactive[str | None] = reactive(None)
    last_query_hash: reactive[str | None] = reactive(None)
    _awaiting_feedback: bool = False
    _pending_clarification: dict | None = None  # {"query": str} when agent asked for project
    _last_query: str = ""

    def __init__(self, conv_agent, rag_system) -> None:
        super().__init__()
        self._conv = conv_agent   # ConversationAgent — persistent memory across turns
        self._rag = rag_system    # RAGSystem — used for streaming output

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="sidebar"):
                yield Label("Projects", id="sidebar-label")
                yield ListView(id="project-list")
            with Vertical(id="chat-area"):
                yield Vertical(id="chat-log")
                yield Static("", id="status-bar")
                with Horizontal(id="input-row"):
                    yield Input(
                        placeholder="Ask a question… (Tab to switch pane, f=feedback, r=refresh)",
                        id="query-input",
                    )
        yield Footer()

    def on_mount(self) -> None:
        self._load_projects()
        self.query_one("#query-input").focus()
        self._append_message(
            "Assistant",
            "Welcome to Resource Explorer! Select a project in the sidebar (Tab) "
            "or ask a question across all projects.",
        )

    # ── project sidebar ───────────────────────────────────────────────────────

    def _load_projects(self) -> None:
        from resource_explorer.registry import ProjectRegistry
        lv = self.query_one("#project-list", ListView)
        lv.clear()
        projects = ProjectRegistry().list_all()
        for p in projects:
            lv.append(ProjectItem(p.slug, p.display_name, p.status.value, len(p.collections), getattr(p, "group_slug", "")))
        if not projects:
            lv.append(ListItem(Label("[dim]No projects — run 'project-explorer add'[/dim]")))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ProjectItem):
            self.selected_project = event.item.slug
            self._conv.resource_slug = event.item.slug  # keep ConversationAgent in sync
            self._set_status(f"Scoped to: {event.item.display_name}  [{event.item.slug}]")
            if self._pending_clarification:
                pending = self._pending_clarification
                self._pending_clarification = None
                self._append_message(
                    "Assistant",
                    f"Got it — scoping to **{event.item.slug}** and re-running your question…",
                )
                self._set_status("Thinking…")
                self._run_query(pending["query"])

    # ── chat ──────────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        event.input.value = ""
        self._append_message("You", query)
        self.last_query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
        self._last_query = query

        # If awaiting clarification, treat typed input as a project name
        if self._pending_clarification:
            orig_query = self._pending_clarification["query"]
            self._pending_clarification = None
            slug = query.replace("project:", "").strip().replace(" ", "_").lower()
            self.selected_project = slug
            self._set_status(f"Scoped to: {slug} — re-running question…")
            self._append_message("Assistant", f"Got it — scoping to **{slug}** and re-running your question…")
            self._run_query(orig_query)
            return

        self._set_status("Thinking…")
        self._run_query(query)

    @work(thread=True)
    def _run_query(self, query: str) -> None:
        """
        Two-phase query:
          1. Stream general-RAG responses token-by-token into a live bubble.
          2. For agent-routed queries (stats, health, etc.) the stream() method
             returns one complete chunk — still goes through the same path.
        Memory across turns is maintained by self._conv (ConversationAgent).
        """
        log = self.query_one("#chat-log")
        bubble = ChatMessage("Assistant")
        self.call_from_thread(log.mount, bubble)
        self.call_from_thread(log.scroll_end, animate=False)

        accumulated = ""
        try:
            for item in self._rag.stream(query, resource_slug=self.selected_project):
                if isinstance(item, dict) and item.get("_done"):
                    break
                chunk = str(item)
                accumulated += chunk
                self.call_from_thread(bubble.append_text, chunk)
                self.call_from_thread(log.scroll_end, animate=False)
        except Exception as exc:
            accumulated = f"Error: {exc}"
            self.call_from_thread(bubble.set_text, accumulated)

        # Feed the completed turn into ConversationAgent memory so subsequent
        # turns have context even when streamed via the RAGSystem path.
        try:
            from beeai_framework.backend.message import UserMessage, AssistantMessage
            import asyncio
            from resource_explorer.concurrency import run_sync
            async def _add_to_memory():
                mem = self._conv._get_agent().memory
                await mem.add(UserMessage(query))
                await mem.add(AssistantMessage(accumulated))
            run_sync(lambda: asyncio.run(_add_to_memory()), timeout=5)
        except Exception:
            pass  # memory update is best-effort

        self.call_from_thread(self._on_stream_done, accumulated)

    def _on_stream_done(self, response: str) -> None:
        if response.startswith(self._CLARIFICATION_PREFIX):
            self._pending_clarification = {"query": self._last_query}
            self._set_status("Select a project from the sidebar (Tab) or type its name and press Enter")
        else:
            self._set_status("f=👍/👎 feedback  |  r=refresh project  |  Tab=switch pane")

    def _on_response(self, response: str) -> None:
        self._append_message("Assistant", response)
        if response.startswith(self._CLARIFICATION_PREFIX):
            self._pending_clarification = {"query": self._last_query}
            self._set_status("Select a project from the sidebar (Tab) or type its name and press Enter")
        else:
            self._set_status("f=👍/👎 feedback  |  r=refresh project  |  Tab=switch pane")

    def _append_message(self, role: str, text: str) -> None:
        log = self.query_one("#chat-log")
        log.mount(ChatMessage(role, text))
        log.scroll_end(animate=False)

    def _set_status(self, text: str) -> None:
        self.query_one("#status-bar", Static).update(text)

    # ── actions ─────���─────────────────────────────────────────────────────────

    def action_feedback(self) -> None:
        if not self.last_query_hash:
            self._set_status("No query to give feedback on yet.")
            return
        self._set_status("Feedback: y=👍  p=😐 partially correct  n=👎  (press key)")
        self._awaiting_feedback = True

    def on_key(self, event) -> None:
        if self._awaiting_feedback:
            self._awaiting_feedback = False
            from resource_explorer.observability.metrics_collector import MetricsCollector
            if event.key == "y":
                MetricsCollector().record_feedback(self.last_query_hash, 1)
                self._set_status("👍 Feedback recorded — thanks!")
            elif event.key == "p":
                MetricsCollector().record_feedback(self.last_query_hash, 0)
                self._set_status("😐 Feedback recorded — partially correct.")
            elif event.key == "n":
                MetricsCollector().record_feedback(self.last_query_hash, -1)
                self._set_status("👎 Feedback recorded.")
            else:
                self._set_status("Feedback cancelled.")
            event.prevent_default()

    def action_refresh_project(self) -> None:
        if not self.selected_project:
            self._set_status("Select a project in the sidebar first (Tab).")
            return
        self._set_status(f"Refreshing {self.selected_project}…")
        self._append_message("Assistant", f"Starting refresh for **{self.selected_project}**…")
        self._run_refresh(self.selected_project)

    @work(thread=True)
    def _run_refresh(self, slug: str) -> None:
        try:
            from resource_explorer.ingestion.incremental import IncrementalIndexer
            from resource_explorer.query_cache import QueryCache
            from resource_explorer.registry import ProjectRegistry
            project = ProjectRegistry().get(slug)
            if project:
                result = IncrementalIndexer().refresh(project)
                QueryCache().invalidate_project(slug)
                msg = (f"**{slug}** is not yet indexed; there was nothing of its own to refresh."
                       if result.value == "not_yet_indexed"
                       else f"Refresh complete for **{slug}**.")
            else:
                msg = f"Project **{slug}** not found."
        except Exception as exc:
            msg = f"Refresh failed: {exc}"
        self.call_from_thread(self._on_refresh_done, msg)

    def _on_refresh_done(self, msg: str) -> None:
        self._append_message("Assistant", msg)
        self._load_projects()
        self._set_status("Ready.")


def run() -> None:
    """
    Pre-warm the RAG system and ConversationAgent in the main thread before
    Textual starts. This prevents gRPC/PyTorch FD conflicts that occur when
    those libraries initialise inside a worker thread.
    """
    import sys
    from rich.console import Console
    from rich.status import Status
    from resource_explorer.rag_system import RAGSystem
    from resource_explorer.agents.conversation_agent import ConversationAgent

    console = Console()

    with Status("[bold green]Loading Resource Explorer…[/bold green]", console=console, spinner="dots"):
        # Pre-warm embedding model in the main thread — best-effort, non-fatal.
        try:
            from resource_explorer.embeddings import get_embedding_model
            get_embedding_model()
        except Exception as exc:
            console.print(f"[yellow]warn:[/yellow] Could not pre-warm embeddings: {exc}")

        # Pre-warm the pgvector connection pool — best-effort, non-fatal.
        try:
            from resource_explorer.vector_store_pg import _get_shared_store
            _get_shared_store().connect()
        except Exception:
            pass

        rag = RAGSystem()

        # _get_agent() is called lazily on first query so BeeAI init stays in the
        # worker thread (avoids asyncio loop conflicts with Textual's event loop).
        conv = ConversationAgent(rag_system=rag)

    ProjectExplorerApp(conv, rag).run()


if __name__ == "__main__":
    run()
