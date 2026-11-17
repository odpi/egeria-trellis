\
# Trellis workspace convenience targets.
#
# Thin wrappers around `uv run --package <name> ...` (see README.md "Getting
# started") — nothing here does anything `uv run` couldn't already do, this
# just saves typing the full invocation for the common cases. Anything not
# covered by a target still works via `make re CMD="..."` / `make ea CMD="..."`
# or the raw `uv run --package ...` form.

.DEFAULT_GOAL := help

RE_WEB_URL := http://localhost:8810
EA_WEB_URL := http://localhost:8880

.PHONY: help sync re re-web re-worker ea ea-web dev ps test test-re test-ea lint fmt \
        prefect-up prefect-down re-resync re-resync-apply re-sweep \
        up up-re up-ea up-container check

help: ## List available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## /{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## uv sync the whole workspace
	uv sync

re: ## Run a resource-explorer CLI command: make re CMD="survey ..."
	uv run --package resource-explorer resource-explorer $(CMD)

re-web: ## Start Resource Explorer's web UI (http://localhost:8810)
	uv run --package resource-explorer resource-explorer web

re-worker: ## Start Resource Explorer's worker role (background loops only, no HTTP)
	uv run --package resource-explorer resource-explorer worker

ea: ## Run an egeria-advisor CLI command: make ea CMD="..."
	uv run --package egeria-advisor egeria-advisor $(CMD)

ea-web: ## Start Egeria Advisor's web UI (http://localhost:8880)
	uv run --package egeria-advisor egeria-advisor-web

dev: ## Start both web UIs concurrently; Ctrl-C stops both
	@echo "Resource Explorer -> $(RE_WEB_URL)"
	@echo "Egeria Advisor    -> $(EA_WEB_URL)"
	@trap 'kill 0' INT TERM; \
	( uv run --package resource-explorer resource-explorer web 2>&1 | sed -u 's/^/[RE] /' ) & \
	( uv run --package egeria-advisor egeria-advisor-web 2>&1 | sed -u 's/^/[EA] /' ) & \
	wait

## Start-up
# ./trellis-up wraps the targets above with the two things they do not do:
# it makes sure a shared TRELLIS_JWT_SECRET exists in BOTH packages' .env
# (absent one, each app derives a secret from its hostname -- no error, just
# sessions that do not survive a restart or move), and it says up front when
# Egeria, Postgres or Ollama is unreachable rather than letting the app fail
# later. `dev`/`re-web`/`ea-web` above remain the direct route when the
# environment is already known-good.

up: ## Preflight, ensure secrets, then start both apps natively
	./trellis-up

up-re: ## Same, Resource Explorer only
	./trellis-up --re

up-ea: ## Same, Egeria Advisor only
	./trellis-up --ea

up-container: ## Start both via the trellis compose overlay in egeria-workspaces-fs (needs `make images`)
	./trellis-up --container

check: ## Preflight only -- report what is missing, start nothing, write nothing
	./trellis-up --check

# `make ps` — what is actually running, in one command.
#
# Exists because finding thirteen orphaned Prefect ephemeral API servers on
# Dev 1 on 2026-09-04 took four separate tool calls and a guess about what to
# grep for (docs/runtime-architecture-plan.md §2: "make ps lists every
# trellis-owned process and container with role, pid, age and port"). Some of
# those orphans were four days old; nothing would have surfaced them.
#
# Plain shell on purpose — no new dependency, and it has to work on a box
# where the thing being diagnosed is that a Python process is wedged. lsof is
# used for ports only if present, and its absence degrades to a blank column
# rather than an error, same as `docker ps` failing when Docker is not up.
TRELLIS_ROOT := $(shell pwd)

ps: ## List trellis-owned processes and containers: role, pid, age, port
	@printf '%-22s %-8s %-10s %-7s %s\n' ROLE PID AGE PORT COMMAND
	@ps -Ao pid=,etime=,command= 2>/dev/null \
	| grep -v -e '[g]rep' -e 'make ps' -e '[p]s -Ao' \
	| while IFS= read -r line; do \
		pid=$$(printf '%s' "$$line" | awk '{print $$1}'); \
		age=$$(printf '%s' "$$line" | awk '{print $$2}'); \
		cmd=$$(printf '%s' "$$line" | awk '{$$1="";$$2=""; sub(/^[ \t]+/,""); print}'); \
		exe=$$(printf '%s' "$$cmd" | awk '{print $$1}'); \
		role=''; \
		case "$$cmd" in \
		  *"resource-explorer web"*)   role='re-web' ;; \
		  *"resource-explorer worker"*) role='re-worker' ;; \
		  *"resource-explorer serve"*|*"resource-explorer a2a"*) role='re-a2a' ;; \
		  *"resource-explorer tui"*)   role='re-tui' ;; \
		  *egeria-advisor-web*)        role='ea-web' ;; \
		  *"prefect.server.api.server:create_app"*) role='prefect-ephemeral' ;; \
		  *"prefect worker"*|*"prefect server"*) role='prefect' ;; \
		  *uvicorn*) case "$$cmd" in *$(TRELLIS_ROOT)*) role='uvicorn(trellis)' ;; esac ;; \
		esac; \
		if [ -z "$$role" ]; then \
		  case "$$exe" in \
		    */ollama|ollama) role='ollama' ;; \
		  esac; \
		fi; \
		[ -n "$$role" ] || continue; \
		port=$$(lsof -nP -a -p "$$pid" -iTCP -sTCP:LISTEN 2>/dev/null \
			| awk 'NR>1 {n=split($$9,a,":"); print a[n]}' | sort -u | paste -sd, - ); \
		[ -n "$$port" ] || port='-'; \
		printf '%-22s %-8s %-10s %-7s %.70s\n' "$$role" "$$pid" "$$age" "$$port" "$$cmd"; \
	  done
	@echo
	@echo 'containers (docker ps, trellis-relevant):'
	@docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null \
	| grep -E '^(egeria-|quickstart-|trellis|.*prefect|.*ollama|resource-explorer|egeria-advisor)' \
	| awk -F'\t' '{printf "  %-34s %-22s %.60s\n", $$1, $$2, $$3}' \
	|| echo '  (docker not available)'
	@echo
	@echo 'Notes: a re-web process with the worker embedded shows only as re-web —'
	@echo '  which loop it actually runs is decided by advisory locks; grep its log'
	@echo '  for "worker loop started" / "worker loop standby". `kill -USR1 <pid>`'
	@echo '  dumps every thread of any RE web or worker process.'

test: test-re test-ea ## Run both test suites

test-re: ## Run resource-explorer's test suite
	uv run --package resource-explorer pytest packages/resource-explorer/tests

test-ea: ## Run egeria-advisor's test suite
	uv run --package egeria-advisor --extra dev pytest packages/egeria-advisor/tests

# Maintenance scripts. These exist as targets because running them from the
# wrong checkout is a real hazard, not a hypothetical one: this workspace has
# four clones of the repo on different branches sharing ONE database, and a
# sweep run from a stale one on 2026-08-26 cleared 23 asset GUIDs while leaving
# 97 publish claims behind — the older script simply did not know about them.
# `uv run --package` resolves from the workspace root, so these always run the
# code that belongs with the tree you invoke them from.

re-resync: ## Report drift between RE and Egeria (read-only)
	uv run --package resource-explorer python packages/resource-explorer/scripts/sweep_stale_egeria_guids.py

re-resync-apply: ## Clear the drift re-resync reports — writes
	uv run --package resource-explorer python packages/resource-explorer/scripts/sweep_stale_egeria_guids.py --apply

re-sweep: re-resync ## Alias for re-resync

lint: ## ruff check both packages
	uv run --package resource-explorer ruff check packages/resource-explorer/resource_explorer
	uv run --package egeria-advisor ruff check packages/egeria-advisor/advisor

fmt: ## black both packages
	uv run --package resource-explorer black packages/resource-explorer/resource_explorer
	uv run --package egeria-advisor black packages/egeria-advisor/advisor

prefect-up: ## Bring up Prefect (server+worker) for RE's local survey-step dispatch — idempotent, bare-host only until Trellis is containerized (see the script)
	packages/resource-explorer/scripts/prefect_up.sh

prefect-down: ## Stop what prefect-up started
	packages/resource-explorer/scripts/prefect_down.sh

## Container images
# Step 3 of docs/runtime-architecture-plan.md: one image per app, built for
# both arm64 and amd64 in CI (.github/workflows/images.yml), used unchanged
# by the dev profile (as an artifact — dev boxes still run native `uv run`)
# and by the demo profile (as the running container). These targets are the
# local, single-arch (host-arch) equivalent for building and smoke-testing
# on a dev box; they intentionally do not push anywhere.
#
# image-run-re assumes the shared-infra compose network from
# egeria-workspaces-fs (compose-configs/shared-infra/shared-infra.yaml) is
# already up — `egeria_network`, with Postgres reachable in-network as
# `egeria-shared-postgres`. Picks a port off the two the user's native
# servers hold (8810 RE, 8880 EA per re-web/ea-web above): 8811.

image-re: ## Build the resource-explorer image for the host arch, tag :local
	docker buildx build -f docker/Dockerfile.resource-explorer --build-arg TRELLIS_RE_SHA=$$(git rev-parse HEAD) -t trellis/resource-explorer:local -t trellis/resource-explorer:$$(git rev-parse --short=12 HEAD) --load .

image-ea: ## Build the egeria-advisor image for the host arch, tag :local
	docker buildx build -f docker/Dockerfile.egeria-advisor -t trellis/egeria-advisor:local --load .

images: image-re image-ea ## Build both images for the host arch

image-run-re: ## Run resource-explorer's web role against shared-infra, on 8811 (8810 is the native dev server's port)
	docker run --rm -p 8811:8810 \
		--network egeria_network \
		-e PGVECTOR_HOST=egeria-shared-postgres \
		-e EGERIA_PLATFORM_URL=https://egeria-main:9443 \
		-e KROKI_URL=http://egeria-shared-kroki:8000 \
		trellis/resource-explorer:local web
