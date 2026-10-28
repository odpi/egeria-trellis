"""Pydantic settings — loaded from config/explorer.yaml + .env overrides."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import json
from typing import Annotated, Any, Optional

from pydantic import BaseModel, BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

log = logging.getLogger(__name__)

# The package's own .env, resolved absolutely. env_file used to be the bare
# relative string ".env", which pydantic-settings resolves against the
# *process working directory* — so it only loaded when a command happened to
# be run from packages/resource-explorer. `make re-web` launches from the
# workspace root, so the web app silently ran with NO GITHUB_TOKEN: 60
# anonymous API requests an hour instead of 5000, and admin-only response
# fields (security_and_analysis) absent, which the security_features analysis
# then reported as "invisible for a repo you do not own" — about repos the
# user does own. Found 2026-08-31. The cwd-relative ".env" is kept as the
# second entry so a deployment can still override per-directory; later files
# win in pydantic-settings.
_PACKAGE_ENV = Path(__file__).resolve().parents[1] / ".env"
_ENV_FILES = (_PACKAGE_ENV, ".env")

# Every nested *Config class below is instantiated independently via
# Field(default_factory=...) on ExplorerConfig — pydantic-settings does
# NOT cascade a parent's env_file down into nested BaseSettings models
# constructed this way. Only ExplorerConfig itself declared env_file
# below, historically, which meant .env support silently never worked
# for ANY of these classes' own fields (GITHUB_TOKEN, EGERIA_*,
# KROKI_URL, FEEDBACK_ADMIN_TOKEN, ...) despite .env.example's own
# instructions to edit .env — only real exported process env vars ever
# took effect. Found 2026-08-10 while wiring up feedback-admin access.
# Fixed by giving every nested class the same env_file declaration;
# extra="ignore" is required alongside it since each class will now see
# the *whole* .env file, including keys meant for its sibling configs.
_ENV_FILE_CONFIG = SettingsConfigDict(
    populate_by_name=True, env_file=_ENV_FILES, env_file_encoding="utf-8", extra="ignore",
)


def parse_str_list(value: Any, setting: str = "setting") -> list[str]:
    """Read a list-of-names setting from an env value without ever raising.

    pydantic-settings parses a plain ``list[str]`` from the environment as JSON, so
    ``FEEDBACK_ADMIN_USERS=peterprofile`` crashed the whole web process at startup (2026-10-14). Accepted:
    a JSON list, a comma-separated list, a single name, or empty. Entries are trimmed and empties dropped.
    A value that cannot be read logs ONE warning and gives ``[]`` (fail closed: nothing listed).
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                items = json.loads(text)
            except ValueError:
                log.warning("%s: could not read %r as a JSON list; treating it as empty (no entries)", setting, value)
                return []
            if not isinstance(items, list):
                log.warning("%s: %r is not a list; treating it as empty (no entries)", setting, value)
                return []
        elif text.startswith(("{", '"', "'")) or "[" in text or "]" in text:
            log.warning("%s: could not read %r; use a JSON list or comma-separated names. Treating it as empty.",
                        setting, value)
            return []
        else:
            items = text.split(",")
    else:
        log.warning("%s: could not read a %s value; treating it as empty (no entries)", setting, type(value).__name__)
        return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, (str, int)) or isinstance(item, bool):
            log.warning("%s: ignoring an entry that is not a name: %r", setting, item)
            continue
        name = str(item).strip()
        if name:
            out.append(name)
    return out


#: A list of names that reads JSON, comma-separated, a single name or empty from the environment.
NameList = Annotated[list[str], NoDecode, BeforeValidator(lambda v, info: parse_str_list(v, info.field_name or "setting"))]


class PgVectorConfig(BaseSettings):
    """Connection to the shared egeria_advisor Postgres/pgvector instance.

    Same physical database Egeria Advisor uses (host=localhost port=5442
    dbname=egeria_advisor) — see egeria-advisor/advisor/config.py:242-246 —
    but RE's tables live in their own `resource_explorer` schema (migration
    plan decision D1), namespaced away from EA's flat `public` schema.
    Defaults match the shared instance managed by egeria-workspaces-fs's
    compose-configs/shared-infra/shared-infra.yaml; override via env for a
    from-scratch environment that doesn't have it.
    """
    host: str = Field(default="localhost", alias="PGVECTOR_HOST")
    port: int = Field(default=5442, alias="PGVECTOR_PORT")
    dbname: str = Field(default="egeria_advisor", alias="PGVECTOR_DBNAME")
    # NOT named "user": with populate_by_name=True, pydantic-settings also
    # accepts the bare field name as an env var — and the near-universal
    # shell env var $USER (e.g. $USER=dwolfson) silently won any real
    # PGVECTOR_USER default, which is exactly what happened during Phase 1
    # live verification. db_user avoids the collision outright.
    db_user: str = Field(default="egeria_advisor", alias="PGVECTOR_USER")
    password: str = Field(default="advisor", alias="PGVECTOR_PASSWORD")
    schema_name: str = Field(default="resource_explorer", alias="PGVECTOR_SCHEMA")
    max_connections: int = Field(default=10, alias="PGVECTOR_MAX_CONNECTIONS")
    ef_search: int = Field(default=100, alias="PGVECTOR_EF_SEARCH")
    # Bounds psycopg2's own connect() (libpq's connect_timeout, seconds) so a
    # hung TCP/DNS/SSL negotiation raises OperationalError instead of
    # blocking the calling thread forever. Added 2026-09-13 after the :8810
    # incident where ThreadedConnectionPool's underlying psycopg2.connect
    # never returned and never raised — see trellis_vectorstore/pg.py's
    # connect() and the incident note in web/routes/query.py's history.
    connect_timeout_seconds: int = Field(default=10, alias="PGVECTOR_CONNECT_TIMEOUT_SECONDS")

    model_config = _ENV_FILE_CONFIG


# --- Model tiers ------------------------------------------------------------
#
# runtime-architecture-plan.md §5 / commit 1dede05: neither RE nor Egeria
# Advisor set Ollama's `num_ctx`, so a model loaded at its full default
# context window (131k for llama3.1:8b, ~22 GB) regardless of what a task
# actually needed. A tier resolves, per machine profile, the Ollama model,
# the `num_ctx` ceiling, and the RAG retrieval context budget (the measured
# time-to-first-token lever — see the plan's "Target environments and what
# was measured" section).
#
# This mirrors egeria-advisor/advisor/config.py's TIER_PRESETS /
# resolve_model_tier / resolve_llm_tier_config — same tier names, same
# num_ctx/budget values, same override precedence — so the two apps
# converge rather than drift (see trellis/CLAUDE.md), and a future
# extraction of this table into a shared trellis-* package is mechanical.
# The one structural difference: RE has a single Ollama model slot (no
# separate query/code/planning models), so each preset carries a flat
# `model` instead of EA's per-slot `models` dict.
#
# `dev`'s "model" entry is intentionally None: dev keeps today's
# behaviour — whatever OllamaConfig.model already resolved to (its class
# default, or an explicit LLM__OLLAMA__MODEL env override) — not a fixed
# preset value. Likewise `rag_context_budget_tokens: None` for dev means no
# new token-based RAG cutoff is introduced; build_context() joins every
# retrieved chunk unchanged, exactly as it does today.
DEFAULT_MODEL_TIER = "dev"

TIER_PRESETS: dict[str, dict[str, Any]] = {
    "dev": {
        "num_ctx": 32768,
        "rag_context_budget_tokens": None,
        "model": None,
    },
    "demo-gpu": {
        "num_ctx": 8192,
        "rag_context_budget_tokens": 2000,
        "model": "llama3.1:8b",
    },
    "demo-cpu": {
        # RE has one model slot (no separate code model the way EA does),
        # so demo-cpu and demo-gpu differ only in intent/documentation here,
        # not in value — both run the one 8B model. Kept as its own preset
        # rather than aliased to demo-gpu so a future per-slot RE model
        # split doesn't have to re-derive the CPU-safe choice from scratch.
        "num_ctx": 8192,
        "rag_context_budget_tokens": 2000,
        "model": "llama3.1:8b",
    },
}


class OllamaConfig(BaseSettings):
    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    temperature: float = 0.1
    # Resolved from the active model tier (see TIER_PRESETS /
    # resolve_llm_tier_config below) inside get_config() — not meant to be
    # set directly. Passed as the `num_ctx` Ollama option on every
    # generate/chat call (resource_explorer/llm_client.py).
    tier: str = DEFAULT_MODEL_TIER
    num_ctx: int = TIER_PRESETS[DEFAULT_MODEL_TIER]["num_ctx"]


class OpenAIConfig(BaseSettings):
    model: str = "gpt-4o-mini"
    temperature: float = 0.1


class AnthropicConfig(BaseSettings):
    model: str = "claude-haiku-4-5-20251001"
    temperature: float = 0.1


class LLMConfig(BaseSettings):
    backend: str = "ollama"  # ollama | openai | anthropic
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)


class EmbeddingsConfig(BaseSettings):
    model: str = "all-MiniLM-L6-v2"
    device: str = "auto"  # auto | mps | cuda | cpu
    dimension: int = 384


class RAGConfig(BaseSettings):
    top_k: int = 10
    min_score: float = 0.15
    max_collections_per_query: int = 3
    # Resolved from the active model tier inside get_config(). None (the
    # `dev` tier) means retrieved chunks are joined unchanged, as before
    # this feature existed; an int is a token budget (approximate — see
    # resource_explorer/prompt_templates.py's `_estimate_tokens`) that
    # build_context() truncates retrieved chunks to, highest-ranked first.
    budget_tokens: Optional[int] = None


class CacheConfig(BaseSettings):
    ttl_seconds: int = 3600
    max_size: int = 1000
    backend: str = "memory"  # memory | redis
    redis_url: str = ""


class GitHubConfig(BaseSettings):
    token: str = Field(default="", alias="GITHUB_TOKEN")
    webhook_secret: str = Field(default="", alias="GITHUB_WEBHOOK_SECRET")
    requests_per_hour: int = 5000
    clone_timeout_seconds: int = 300
    ssl_verify: bool = True  # set GITHUB__SSL_VERIFY=false in .env to bypass (insecure)
    # The single source for the GitHub API base URL (e.g. a GitHub Enterprise
    # install). There is no runtime override: discovery, surveys and ingestion
    # all resolve it through GitHubClient().
    base_url: str = Field(default="https://api.github.com", alias="GITHUB_BASE_URL")

    model_config = _ENV_FILE_CONFIG


class MLflowConfig(BaseSettings):
    enabled: bool = True
    tracking_uri: str = "http://localhost:5025"
    experiment_name: str = "project-explorer"


class PhoenixConfig(BaseSettings):
    enabled: bool = True
    collector_endpoint: str = "http://localhost:6006/v1/traces"
    #: Phoenix groups spans by project, and everything that does not name one
    #: lands in `default`. That is a shared bucket: measured 2026-09-09, this
    #: machine's Phoenix held 106 spans in `default` from an unrelated BeeAI
    #: tutorial run in December 2025 (OpenMeteoTool, DuckDuckGo, 7 errors), and
    #: RE's first real span landed among them — anyone opening Phoenix cold
    #: would reasonably read the tutorial's numbers as RE's. Naming the project
    #: separates them without deleting anyone's history.
    project_name: str = "resource-explorer"


class ObservabilityConfig(BaseSettings):
    mlflow: MLflowConfig = Field(default_factory=MLflowConfig)
    phoenix: PhoenixConfig = Field(default_factory=PhoenixConfig)
    #: Retained so an explicit sqlite:/// path still resolves, and because
    #: MetricsCollector's own constructor still honours it — but the default
    #: store is now the shared Postgres instance (2026-08-23), matching the
    #: registry and the feedback store. Override METRICS_DATABASE_URL for a
    #: from-scratch environment without the shared instance.
    metrics_db: str = "data/metrics.db"
    metrics_database_url: str = Field(
        default=(
            "postgresql://egeria_advisor:advisor@localhost:5442/egeria_advisor"
            "?options=-csearch_path%3Dresource_explorer"
        ),
        alias="METRICS_DATABASE_URL",
    )


class AgentsConfig(BaseSettings):
    max_iterations: int = 20
    max_retries: int = 10
    stream_responses: bool = True
    # Bounds ConversationAgent._run_persistent's wait for one chat turn
    # (agent.run(), including every tool call it makes). Generous by
    # design — this is the backstop for a STUCK tool (a hung DB connect,
    # e.g.), not a latency budget for a normal slow LLM call. Added
    # 2026-09-13 after the :8810 incident: vector_search's Postgres
    # connect() never returned, and run_sync was waiting on it with no
    # timeout at all, freezing the event-loop thread that called it.
    chat_timeout_seconds: float = Field(default=300.0, alias="EXPLORER_CHAT_TIMEOUT_SECONDS")


class EgeriaConfig(BaseSettings):
    platform_url: str = Field(default="https://localhost:9443", alias="EGERIA_PLATFORM_URL")
    view_server: str = Field(default="qs-view-server", alias="EGERIA_VIEW_SERVER")
    engine_host: str = Field(default="engine-host", alias="EGERIA_ENGINE_HOST")
    #: The integration daemon a catalogue commit may ask to refresh the JDBC
    #: cataloguer (never to restart it). The scratch tests ran against this one.
    integration_daemon_server: str = Field(default="qs-integration-daemon", alias="EGERIA_INTEGRATION_DAEMON")
    user_id: str = Field(default="erinoverview", alias="EGERIA_USER_ID")
    user_password: str = Field(default="secret", alias="EGERIA_USER_PASSWORD")
    #: Further Egeria platforms a credential may be sent to, comma separated (Brief I). The
    #: configured `platform_url` is always allowed; any other URL is refused before a request.
    allowed_platform_urls: str = Field(default="", alias="EGERIA_ALLOWED_PLATFORM_URLS")
    #: Optional login-form prefill (`GET /api/auth/defaults`). Never the daemon's userId: the
    #: daemon (EGERIA_USER_ID) is RE's service account, and the endpoint is public (Brief L).
    login_default_user: str = Field(default="", alias="RE_LOGIN_DEFAULT_USER")
    kafka_endpoint: str = Field(default="localhost:9092", alias="EGERIA_KAFKA_ENDPOINT")
    # Base URL for Egeria Workspaces' "The Catalog" app (egeria-workspaces-fs
    # PyegeriaWebHandler) — supports deep-linking to a specific element via
    # {portal_url}/tech-catalog?guid={guid}, which auto-resolves to the right
    # section (e.g. Surveys & Annotations for a SurveyReport guid). Confirmed
    # against egeria-workspaces-fs's Apache proxy config (fastapi-proxy.conf,
    # ServerName localhost:8885) — override if your deployment differs.
    portal_url: str = Field(default="http://localhost:8885", alias="EGERIA_PORTAL_URL")
    # Governance zones assigned by default when cataloging repos (survey) or databases
    # NOTE: nothing on the 2026-10-05 build assigns a default zone (the read-back found none on any element).
    # A catalogue commit for a database writes a ZoneMembership ONLY when this (or the
    # EXPLORER_PUBLISH_ZONES environment variable) is set, and then as its LAST write. UNVERIFIED
    # FOR A SECOND COMMIT: a zone on the database element may lock the service identity, the
    # survey engine and the cataloguer out of later anchored writes. First live use: a throwaway
    # database with TWO commits, the second exercising anchored writes under the zone.
    default_catalog_zones: NameList = Field(default_factory=list)
    default_survey_zones: NameList = Field(default_factory=list)
    # Secrets store used when Egeria's own native "catalog and survey" processes
    # need credentials. Confirmed live 2026-07-09: secrets are written via
    # AutomatedCuration.save_client_side_secret(secrets_store_guid, body) with
    # secret key names "userId"/"clearPassword" for Postgres resources — but
    # this was never wired into a caller until PROBES-2026-09-21.md's investigation (2026-09-21)
    # found the reason a
    # freshly-cataloged coco_pharma asset still failed its native survey with
    # a SCRAM/no-password error: its embedded SecretsStoreConnection carried
    # the literal unsubstituted template placeholders "~{secretsCollectionName}~"
    # / "~{secretsStorePathName}~" — nothing had ever supplied real values for
    # either. **Decision (project owner, 2026-09-21):** Egeria does not share
    # secrets across clients — RE must keep its own secrets store (a YAML file
    # via Egeria's own client-side-secret structure, see
    # https://egeria-project.org/concepts/client-side-secret) rather than
    # pointing at Egeria's bundled egeria-servers.omsecrets/integration
    # .omsecrets/coco-user-directory.omsecrets, none of which are ever named
    # for a database RE surveys. secrets_store_guid is left blank by default —
    # EgeriaDatabaseSurveyor._ensure_own_secrets_store_guid() finds-or-creates
    # RE's own SecretsStore Connection/Endpoint/ConnectorType graph on first
    # use (same find-by-qualifiedName-then-create idiom as
    # _create_postgres_element_from_template) and caches the guid — set this
    # explicitly only to point at a pre-existing one instead. The path name
    # default now points at RE's own file rather than reusing one of Egeria's,
    # under the same bind-mounted directory (/deployments/secrets) every
    # deployment profile already gives the engine host container.
    secrets_store_guid: str = Field(default="", alias="EGERIA_SECRETS_STORE_GUID")
    secrets_store_path_name: str = Field(
        default="/deployments/secrets/resource-explorer.omsecrets",
        alias="EGERIA_SECRETS_STORE_PATH_NAME",
    )
    # The SAME physical .omsecrets file as secrets_store_path_name above, but
    # named from RE's own (bare-host) filesystem rather than from inside the
    # engine host container. secrets_store_path_name is a value RE hands to
    # Egeria (SecretsStorePathName, resolved container-side, e.g.
    # /deployments/secrets/resource-explorer.omsecrets) — RE itself is not on
    # that filesystem and cannot open that path directly. This is the bind
    # mount's host-side path (e.g. .../runtime-volumes/quickstart-platform-
    # data/secrets/resource-explorer.omsecrets) that lets RE read/write the
    # file directly for the credential-storage/drift-detection work in
    # docs/design-notes/exchange/REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md
    # §7 ("one secrets-collection name per (resource, role), written to both
    # places by RE in the same operation"). Empty by default — most
    # deployments (CI, a from-scratch checkout, a remote engine host) have no
    # host-visible path to this file at all, and every caller of
    # omsecrets_store must treat that as a plain no-op, never an error.
    secrets_store_local_path: str = Field(
        default="", alias="EGERIA_SECRETS_STORE_LOCAL_PATH"
    )

    model_config = _ENV_FILE_CONFIG


class KrokiConfig(BaseSettings):
    """Self-hosted diagram-rendering service (mermaid, among others) —
    backs POST /api/diagrams/mermaid, which the frontend calls instead of
    rendering mermaid client-side. Same shared container the rest of the
    egeria-v6 checkout uses (egeria-shared-kroki, defined in
    egeria-workspaces-fs/compose-configs/shared-infra/shared-infra.yaml),
    reached via its published host port — resource-explorer runs as a bare
    `uv run` process, not on the egeria_network docker network, so it can't
    resolve the container by name (http://egeria-shared-kroki:8000, the URL
    other *containerized* Egeria services use) the way they can.

    The default assumes Kroki runs on the same host as resource-explorer
    (a single-box dev checkout). In a deployment where Egeria/Kroki run on a
    different host, override KROKI_URL to that host's published Kroki port
    — nothing else needs to change; diagrams.py never assumes localhost.
    See docs/kroki-diagram-rendering.md for the full picture, including why
    this is deliberately a backend proxy rather than the browser calling
    Kroki directly, and what happens when Kroki is unreachable (a real
    concern once it's on a different host — network hiccups become more
    likely, not just "container isn't running").

    Published host port is 6002, not Kroki's own default 8000 — shared-infra.yaml
    moved off 8000 because it collided with common local dev servers (e.g.
    `mkdocs serve`) on the same host. Container-internal port is still 8000
    (unaffected; that's what the container-to-container URL above uses).
    """
    url: str = Field(default="http://localhost:6002", alias="KROKI_URL")
    # Higher than diagrams.py's old flat 15s default — a remote Kroki adds
    # real network latency on top of render time, which local-only testing
    # can't surface. Tune down for a known-fast local setup if 30s ever
    # feels too patient for a hung request.
    timeout_seconds: float = Field(default=30.0, alias="KROKI_TIMEOUT_SECONDS")

    model_config = _ENV_FILE_CONFIG


#: Default ceiling (seconds) on waiting for ONE Prefect-dispatched step's flow
#: run to reach a terminal state. 1200 s = 20 min. Basis: the slowest measured
#: Prefect-eligible step, assessment/secret_scan (repo_secret_scan), has median
#: 330.8 s (n=1) in docs/funnel-cost-measured.md (the PR/CI session's own
#: measurement quoted 277 s / median 331 s), declared compute_cost "minutes";
#: 1200 s is ~3.6x that median — generous for a slow repo, yet bounded.
DEFAULT_PREFECT_STEP_TIMEOUT_SECONDS = 1200.0


class PrefectConfig(BaseSettings):
    api_url: str = Field(default="http://localhost:4200/api", alias="PREFECT_API_URL")
    ui_url: str = Field(default="http://localhost:4200", alias="PREFECT_UI_URL")
    # True as of 2026-09-19 (project owner decision, PLAN-PREFECT-OR-ALTERNATIVE.md
    # §5 phase 3) — the gate for this default ("once phase 2 passes") is
    # satisfied: a live end-to-end repo_arch_coupling run through Prefect
    # completed cleanly, with a real result via state.result() and no silent
    # duplicate local execution (see PLAN-PREFECT-OR-ALTERNATIVE.md §5a).
    #
    # Was False 2026-09-04 – 2026-09-19 (and briefly True 2026-08-26 –
    # 2026-09-04, before the root cause below was understood). That earlier
    # True default leaked: 13 orphaned `prefect.server.api.server:create_app`
    # subprocess servers were found on this machine, reparented to launchd,
    # days old. Cause was NOT run_prefect_step's own fallback (that path is
    # fine — an unreachable PREFECT_API_URL falls back to running the step
    # locally in-process). It was Prefect's *own* client: when enabled=True
    # and no reachable API is configured, `prefect.client.get_client()`
    # itself starts an ephemeral subprocess server rather than raising —
    # and nothing in RE ever shuts that subprocess down, so every process
    # that ever dispatched a `prefect step` with Prefect enabled and no real
    # server up left one behind. Fixed at the root (prefect_adapter.py forces
    # `PREFECT_SERVER_EPHEMERAL_ENABLED=false` in-process, independent of
    # this default) before this default was flipped back on — the earlier
    # incident was a reason to fix the leak, not a permanent reason to leave
    # Prefect off.
    #
    # This default assumes a deployment where a compose service actually
    # provides Prefect — egeria-workspaces' optional-associated-runtimes/
    # prefect, or an equivalent — is reachable at PREFECT_API_URL. Without
    # one, `run_prefect_step` still degrades to running the step locally
    # (see the fallback branch's own comment), so a checkout with no Prefect
    # server at all keeps working, just without Prefect's retries/telemetry.
    # Steps that don't declare executes_at: prefect are unaffected regardless
    # of this setting (see route_local_steps).
    enabled: bool = Field(default=True, alias="PREFECT_ENABLED")
    work_pool: str = Field(default="default-agent-pool", alias="PREFECT_WORK_POOL")
    # Deadline for a dispatched step's flow run (seconds); see
    # DEFAULT_PREFECT_STEP_TIMEOUT_SECONDS for the basis of the default.
    step_timeout_seconds: float = Field(
        default=DEFAULT_PREFECT_STEP_TIMEOUT_SECONDS, gt=0,
        alias="PREFECT_STEP_TIMEOUT_SECONDS")
    # Route steps that explicitly declare executes_at="resource-explorer" through
    # Prefect as well. Off by default, and deliberately its own setting rather
    # than a second meaning for `enabled`.
    #
    # `enabled` used to imply this, so turning Prefect on silently overrode every
    # definition that had named its engine. executes_at is documented as naming
    # the execution engine and as being open-ended precisely so engines can be
    # chosen per step (docs/Backlog.md) — a global flag that overrules it takes
    # away the only way to say "run this one here", and makes executes_at:
    # "prefect" redundant into the bargain.
    #
    # Wanting RE's own steps run through Prefect for retries and telemetry is a
    # legitimate deployment choice; it just has to be asked for.
    route_local_steps: bool = Field(default=False, alias="PREFECT_ROUTE_LOCAL_STEPS")

    model_config = _ENV_FILE_CONFIG


class RegistryConfig(BaseSettings):
    """Registry storage location. Defaults to the same shared Postgres
    instance/database Egeria Advisor uses (see PgVectorConfig) — search_path
    pinned to `resource_explorer` via the connection URL's `options` query
    param (`-csearch_path=resource_explorer`), so registry.py's ~27
    CREATE TABLE/INSERT/SELECT statements stay schema-agnostic; no
    per-statement schema-qualification needed. Override REGISTRY_DATABASE_URL
    directly (e.g. back to sqlite:///data/registry.db) for a from-scratch
    environment without the shared instance.
    """
    database_url: str = Field(
        default=(
            "postgresql://egeria_advisor:advisor@localhost:5442/egeria_advisor"
            "?options=-csearch_path%3Dresource_explorer"
        ),
        alias="REGISTRY_DATABASE_URL",
    )

    model_config = _ENV_FILE_CONFIG


class WebUIConfig(BaseSettings):
    """Which UI `/` serves. `next` (default) or `classic`.

    A quick revert without a code change: set RE_DEFAULT_UI=classic and
    restart. Read once at startup by `web/app.py`. `/next` and `/classic`
    always serve their own UI regardless of this setting.
    """
    default_ui: str = Field(default="next", alias="RE_DEFAULT_UI")
    #: The URL a browser elsewhere uses to reach this RE web app (e.g. `http://<box-hostname>:8810`), the same
    #: value the Portal's tile uses (EGERIA_RESOURCE_EXPLORER_URL). Read by a work list's publish to write
    #: `additionalProperties.resourceExplorerUrl` on its WorkingSet, so the Portal can link back. Empty (the
    #: default): no link is written. There is deliberately no localhost default: a reader on another machine
    #: following it would reach its own localhost.
    public_url: str = Field(default="", alias="RE_PUBLIC_URL")

    model_config = _ENV_FILE_CONFIG


class FeedbackConfig(BaseSettings):
    """Product/UI feedback storage + minimal admin gating.

    Kept as its own database file (separate from the project/resource
    registry) since feedback is an operationally distinct support/triage
    workflow — see resource_explorer/feedback_store.py.

    admin_token / admin_users are NOT a general auth system — see
    resource_explorer/web/admin_auth.py. Leaving both unset means every
    admin-only endpoint denies all requests (fail closed), unless
    RE_ADMIN_OPEN_WHEN_UNCONFIGURED is set for local dev.
    """
    #: Defaults to the same shared Postgres instance/schema the registry uses
    #: (2026-08-23), not a local SQLite file. This store was written to be
    #: portable from the start — it uses ProjectRegistry's ConnectionWrapper and
    #: a SQLAlchemy engine, and its own docstring says "nothing here is
    #: SQLite-specific" — so only the default URL was still pointing at a file.
    #: Override FEEDBACK_DATABASE_URL (e.g. back to sqlite:///data/feedback.db)
    #: for a from-scratch environment without the shared instance.
    database_url: str = Field(
        default=(
            "postgresql://egeria_advisor:advisor@localhost:5442/egeria_advisor"
            "?options=-csearch_path%3Dresource_explorer"
        ),
        alias="FEEDBACK_DATABASE_URL",
    )
    admin_users: NameList = Field(default_factory=list, alias="FEEDBACK_ADMIN_USERS")
    admin_token: str = Field(default="", alias="FEEDBACK_ADMIN_TOKEN")
    #: Local dev only (owner ruling 2026-10-14). With no admin configured, `require_admin` refuses everyone; this
    #: brings back "any signed-in user may act" for Retry / Run bootstrap / bulk resolve. Ignored once an admin
    #: is configured; never admits an anonymous caller; never affects `is_admin_request` (Feedback, Curate).
    admin_open_when_unconfigured: bool = Field(default=False, alias="RE_ADMIN_OPEN_WHEN_UNCONFIGURED")

    model_config = _ENV_FILE_CONFIG


class ArtifactTreeSettings(BaseSettings):
    """Containment trees over ingested artifacts (trellis-artifact-tree).

    OFF BY DEFAULT, and off means byte-for-byte today's behaviour: no tree is
    built, no connection is opened, no table is created. On means trees are
    built and persisted IN ADDITION to the existing chunking — not instead of
    it. Chunks are retrieval units sized by content profile; tree nodes are
    containment units that compression rungs are cut from. Replacing one with
    the other is the conflation docs/context-compilation-design.md §15 exists
    to prevent, so both are produced from the same walk.

    Connection details are deliberately NOT separate env vars: the tree lives
    in the same Postgres as pgvector (that being the reason pgvector was chosen
    over a standalone vector store — structure and vectors join in one query).
    Only the schema is its own, because both RE and EA write trees and the
    tables therefore belong to neither app's schema.
    """
    enabled: bool = Field(default=False, alias="ARTIFACT_TREE_ENABLED")
    schema_name: str = Field(default="artifact_tree", alias="ARTIFACT_TREE_SCHEMA")
    # OCR for PDFs. Off by default because Docling's DEFAULT engine is broken
    # here (rapidocr needs omegaconf, which soda-core's antlr4 pin holds at
    # 2.0.6, which rejects rapidocr's own PosixPath) -- with it on, EVERY
    # conversion fails. Not because OCR is unwanted: tables and headings
    # extract without it, but text inside a scanned page or a diagram does not.
    # Turning it on needs a different engine, not the soda migration:
    #   pip install 'trellis-artifact-tree[ocr]'      -> easyocr, portable
    #   pip install 'trellis-artifact-tree[ocr-mac]'  -> ocrmac, macOS, lighter
    pdf_ocr: bool = Field(default=False, alias="PDF_OCR_ENABLED")
    pdf_ocr_engine: str = Field(default="easyocr", alias="PDF_OCR_ENGINE")

    model_config = _ENV_FILE_CONFIG


class RunsConfig(BaseSettings):
    """Freshness gating for USER-INITIATED analysis runs."""

    #: How recently an analysis's data must have been produced for a new run to
    #: be considered redundant. Measured 2026-09-10 over 1,207 successful runs:
    #: 20.7% happened within 5 minutes of an identical prior run, and ~95% of
    #: those produced no different findings — a fifth of all runs buying
    #: nothing, at up to 110s each. The 1h→6h step is only +1.4pp, a flat
    #: region between burst duplication and the legitimate daily cadence, so
    #: anywhere in it behaves much the same; 1 hour sits at its start.
    #:
    #: Per-tier thresholds are the better answer and are NOT possible yet: they
    #: need the rot measurement in the funnel-cost spec's §5, which needs
    #: material-difference detection that does not exist. One number, honestly
    #: a placeholder, beats a per-tier table invented from nothing.
    freshness_seconds: int = 3600
    #: Gate user-initiated runs only. The scheduler is deliberately exempt (a
    #: project-owner decision, 2026-09-10): skipping scheduled sweeps would
    #: change what "nightly" means, which is a different decision from sparing
    #: someone a redundant click.
    gate_user_runs: bool = True
    #: Whether a run's auto-publish drains the Egeria outbox inline, before the
    #: run returns (True), or only enqueues the outbox rows and leaves draining
    #: to the scheduler's periodic `_drain_egeria_outbox` cycle (False, the
    #: default since 2026-10-14). Both paths enqueue the run's annotations, its
    #: evidence links (keyed by row id until their referents land) and one
    #: `publish_read_back` row; the run's publish word is derived from those
    #: rows (`registry.publish_proofs`): "queued for publish", "published ·
    #: read back", or "publish failed" with Egeria's words.
    #:
    #: Project-owner decision, 2026-10-14: a synchronous survey or analysis
    #: does NOT wait for its publish. Measured 2026-09-13 on a real
    #: `language_file_classification` publish (`egeria_workspaces_git`, 53
    #: outbox rows): the inline drain cost 546.61s of the run's 558.89s total.
    #: `RUNS_PUBLISH_INLINE=true` keeps the old wait; the per-run "wait" choice
    #: still overrides either way. The engine host is not governed by this
    #: flag: its publisher takes no `defer_drain` and completes in its own
    #: handler, because finishing an engine action needs the report GUID.
    publish_inline: bool = Field(default=False, alias="RUNS_PUBLISH_INLINE")


class RuntimeConfig(BaseSettings):
    """Process-role runtime settings — see docs/process-model.md and
    docs/runtime-architecture-plan.md §2.

    sync_pool_size bounds the ONE shared thread pool per process that
    bridges sync pyegeria/BeeAI calls into async code
    (resource_explorer/concurrency.py). It replaced six ad hoc pools, one
    of which was constructed with no max_workers at all. 8 matches the
    old per-batch ceiling (_GUID_RESOLVE_WORKERS in
    surveyors/survey_definition_reader.py) — these are reads against one
    Egeria server, and the goal is to stop waiting in series, not to
    load-test someone else's platform.

    embed_worker decides whether the FastAPI lifespan runs the worker
    role in-process (background loops in a daemon thread). It defaults
    True so `make dev` and an existing .env keep behaving exactly as
    they did when the loops were started from the lifespan directly.
    `resource-explorer web --no-embed-worker` sets it to False for this
    process; leader election (resource_explorer/leader_election.py) is
    what makes it safe to leave on with --workers N.
    """
    sync_pool_size: int = Field(default=8, alias="EXPLORER_SYNC_POOL_SIZE")
    embed_worker: bool = Field(default=True, alias="EXPLORER_EMBED_WORKER")

    model_config = _ENV_FILE_CONFIG


class ResolvedLLMTierConfig(BaseModel):
    """The effective Ollama model, num_ctx, and RAG context budget after
    resolving the active tier against any explicit override. See
    TIER_PRESETS above and resolve_llm_tier_config()'s docstring."""
    tier: str
    model: str
    num_ctx: int
    rag_context_budget_tokens: Optional[int]


def resolve_model_tier() -> str:
    """
    Resolve the active model tier.

    Priority: ``EXPLORER_MODEL_TIER`` env var, then the ``dev`` default. An
    unrecognised value is logged and skipped rather than raised, so a typo
    degrades to the default instead of failing startup.

    (Unlike egeria-advisor, RE has no runtime YAML config loader —
    configdata/explorer.yaml is not read at runtime — so there is no
    ``llm.tier`` yaml fallback to check here; the env var and the default
    are the only two sources.)
    """
    env_tier = os.environ.get("EXPLORER_MODEL_TIER", "").strip()
    if env_tier:
        if env_tier in TIER_PRESETS:
            return env_tier
        log.warning(
            "Unknown EXPLORER_MODEL_TIER=%r (expected one of %s); falling back to default",
            env_tier, sorted(TIER_PRESETS),
        )
    return DEFAULT_MODEL_TIER


def resolve_llm_tier_config() -> ResolvedLLMTierConfig:
    """
    Resolve the effective Ollama model, ``num_ctx``, and RAG context budget
    for the active tier.

    Model resolution order (highest wins):
      1. ``LLM__OLLAMA__MODEL`` env var, if actually present in the
         environment (checked via ``os.environ`` directly, not the
         pydantic-resolved value, so an unset var never masquerades as an
         override — matches egeria-advisor's OLLAMA_MODEL precedence
         check in resolve_llm_tier_config()).
      2. The resolved tier's preset model.
      3. ``OllamaConfig``'s own class default (only reachable for ``dev``,
         whose preset leaves the model alone).
    """
    tier = resolve_model_tier()
    preset = TIER_PRESETS[tier]

    explicit_model = os.environ.get("LLM__OLLAMA__MODEL", "").strip()
    if explicit_model:
        model = explicit_model
    elif preset["model"]:
        model = preset["model"]
    else:
        model = OllamaConfig.model_fields["model"].default

    return ResolvedLLMTierConfig(
        tier=tier,
        model=model,
        num_ctx=preset["num_ctx"],
        rag_context_budget_tokens=preset["rag_context_budget_tokens"],
    )


_llm_tier_config: ResolvedLLMTierConfig | None = None


def get_llm_tier_config(force_refresh: bool = False) -> ResolvedLLMTierConfig:
    """Return the cached resolved tier config, logging it once on first
    resolution. Call sites (web/app.py's lifespan, worker.py's run_worker())
    that want a startup log line regardless of import-order timing should
    call this explicitly rather than relying on the lazy first-call log."""
    global _llm_tier_config
    if _llm_tier_config is None or force_refresh:
        _llm_tier_config = resolve_llm_tier_config()
        cfg = _llm_tier_config
        log.info(
            "LLM tier resolved: tier=%s model=%s num_ctx=%s rag_context_budget_tokens=%s",
            cfg.tier, cfg.model, cfg.num_ctx, cfg.rag_context_budget_tokens,
        )
    return _llm_tier_config


class ExplorerConfig(BaseSettings):
    pgvector: PgVectorConfig = Field(default_factory=PgVectorConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    egeria: EgeriaConfig = Field(default_factory=EgeriaConfig)
    kroki: KrokiConfig = Field(default_factory=KrokiConfig)
    prefect: PrefectConfig = Field(default_factory=PrefectConfig)
    registry: RegistryConfig = Field(default_factory=RegistryConfig)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
    artifact_tree: ArtifactTreeSettings = Field(default_factory=ArtifactTreeSettings)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    runs: RunsConfig = Field(default_factory=RunsConfig)

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


_config: ExplorerConfig | None = None


def egeria_platform_url() -> str:
    """THE Egeria platform URL (Brief I round 4, found live on 8813): `config.egeria.platform_url`
    — the environment / .env value, else its default. No other module reads EGERIA_PLATFORM_URL
    from the environment (the ban test enforces it): two sources once disagreed, and a direct read
    saw '' where the configured default existed."""
    return get_config().egeria.platform_url


def egeria_view_server() -> str:
    """THE Egeria view server: `config.egeria.view_server` (same reasoning)."""
    return get_config().egeria.view_server


def get_config() -> ExplorerConfig:
    global _config
    if _config is None:
        _config = ExplorerConfig()
        # Apply the resolved model tier on top of the pydantic-parsed llm/rag
        # blocks: Ollama model, num_ctx, and the RAG context token budget.
        # See resolve_llm_tier_config()'s docstring for override precedence.
        tier_cfg = get_llm_tier_config()
        _config.llm.ollama.tier = tier_cfg.tier
        _config.llm.ollama.model = tier_cfg.model
        _config.llm.ollama.num_ctx = tier_cfg.num_ctx
        _config.rag.budget_tokens = tier_cfg.rag_context_budget_tokens
    return _config
