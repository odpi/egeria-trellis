# shellcheck shell=bash
# SPDX-License-Identifier: Apache-2.0
#
# The ONE runtime environment for Resource Explorer on a Mac host: the web app (laz: ~/.re-gate/start-8813.sh)
# and its Prefect worker (re-prefect-worker.sh, under launchd) both source this file, so a step the worker runs
# sees the settings the web app sees (read-back of 8f5af544, 2026-11-13). Installed as
# ~/.re-gate/re-runtime-env.sh; this copy is the template.
#
# Before this, the web app took its Egeria settings (view server, engine host, integration daemon, users, ...)
# from whatever shell launched it, while the worker had only the package .env: a step run by the worker would
# have fallen back to config defaults, silently.
#
# The package .env is NOT read here: both launchers pass it to `dotenv run` themselves, after sourcing this.
# Never echo a value from this file; compare names only (re-config-parity.sh).

# 1. Egeria connection: the EGERIA_*/PYEGERIA_* exports from ~/.zshrc, read at start (never copied into
#    another file). Only those lines are evaluated, not the rest of the shell profile.
if [ -f "$HOME/.zshrc" ]; then
    eval "$(grep -E '^[[:space:]]*export[[:space:]]+(EGERIA|PYEGERIA)_[A-Z0-9_]*=' "$HOME/.zshrc")"
fi

# 2. Owner-set Resource Explorer state (moved here unchanged from start-8813.sh, with its reasons).
# Engine host on (owner state since 2026-10-14); a restart without it silently drops the Egeria-dispatched steps.
export RE_ENGINE_HOST_ENABLED=1
# Chat model (owner 2026-10-20, after that day's model A/B on laz, an ops note outside this repo: qwen3.6:35b scored
# 19/23 vs llama3.1:8b 14/23, no runaways). Overrides the .env/config default without touching .env. Revert by
# deleting this line.
export LLM__OLLAMA__MODEL=qwen3.6:35b
