"""Every agent that builds a RequirementAgent must get its llm from
``_build_llm()``: for the ollama backend that is NativeOllamaChatModel (native
/api/chat, where num_ctx and the max_tokens cap take effect). A bare
"ollama:model" string goes to /v1/chat/completions, uncapped (q3 "What is
egeria?" held Ollama for 28 minutes on 2026-10-20). Construct only; no calls."""

import ast
from pathlib import Path

import resource_explorer.agents as agents_pkg
from resource_explorer.agents.ollama_native import NativeOllamaChatModel
from resource_explorer.config import ExplorerConfig

AGENTS_DIR = Path(agents_pkg.__file__).parent


def _ollama_config() -> ExplorerConfig:
    cfg = ExplorerConfig()
    cfg.llm.backend = "ollama"
    cfg.llm.ollama.model = "llama3.1:8b"
    cfg.llm.ollama.num_ctx = 4096
    cfg.llm.ollama.max_tokens = 512
    return cfg


def test_conversation_agent_builds_native_ollama_model(monkeypatch):
    import beeai_framework.agents.requirement as req

    captured = {}

    class _Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(req, "RequirementAgent", _Fake)

    from resource_explorer.agents.conversation_agent import ConversationAgent

    ConversationAgent(config=_ollama_config())._get_agent()

    llm = captured["llm"]
    assert isinstance(llm, NativeOllamaChatModel), type(llm)
    assert llm._settings.get("num_ctx") == 4096
    assert llm.parameters.max_tokens == 512


def _requirement_agent_llm_args():
    """Yield (file, line, llm-expr-source) for every RequirementAgent(...) call
    in resource_explorer/agents/, skipping docstring examples (not AST calls)."""
    for path in sorted(AGENTS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None)) == "RequirementAgent"
            ):
                llm = next((k.value for k in node.keywords if k.arg == "llm"), None)
                yield path.name, node.lineno, ast.unparse(llm) if llm is not None else None


def test_every_requirement_agent_takes_its_llm_from_build_llm():
    found = list(_requirement_agent_llm_args())
    assert found, "guard found no RequirementAgent(...) calls; the scan is broken"
    bad = [f for f in found if f[2] != "self._build_llm()"]
    assert not bad, f"RequirementAgent built without self._build_llm(): {bad}"
