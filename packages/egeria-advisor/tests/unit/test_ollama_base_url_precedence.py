"""OLLAMA_BASE_URL beats advisor.yaml's llm.base_url for the Ollama client (trevor, 2026-09-04)."""
from advisor.llm_client import OllamaClient

import pytest

# Opens a real Postgres connection on construction (the metrics store): needs the password (TR-12 R1).
pytestmark = pytest.mark.requires_pgvector


def test_env_base_url_wins_over_yaml(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    assert OllamaClient().base_url == "http://host.docker.internal:11434"


def test_explicit_argument_wins_over_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    assert OllamaClient(base_url="http://explicit:1").base_url == "http://explicit:1"


def test_empty_env_falls_back_to_yaml(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "")
    assert OllamaClient().base_url.startswith("http://")
