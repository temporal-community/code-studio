set dotenv-path := ".env.local"
set dotenv-load := true

default:
    @just --list

# Install the published Python and npm dependencies.
install:
    uv sync
    npm ci

# Start the shared workspace and agent stack; Ctrl-C stops its processes.
dev:
    uv run python scripts/dev.py

# Each recipe below can also be run in a separate terminal.
temporal:
    mkdir -p .local
    temporal server start-dev --namespace code-studio --db-filename .local/temporal.db

session-manager:
    uv run temporal-agent-harness session-manager

server:
    uv run python -m scripts.harness_server

worker:
    uv run python -m agent_dag.worker

# Local file host and sole agent callback executor.
workspace:
    uv run python -m workspace.server

# Shared editing without Temporal or an OpenAI key (run just studio separately).
collaboration:
    WORKSPACE_NO_AGENT=1 uv run python -m workspace.server

studio:
    npm run dev

check:
    npm run check
    npm run build
    npm run test:collaboration
    uv run pytest -v

# Milestone checks without a Temporal server or API key.
check-collaboration:
    npm run check
    npm run test:collaboration
    uv run pytest tests/test_workspace.py tests/test_formatting.py tests/test_openai_streaming.py -q

# SDLC milestone checks without a server, GitHub credential, or model calls.
check-sdlc:
    npm run check
    npm run build
    npm run test:collaboration
    npm run test:editor
    uv run pytest tests/test_workspace.py tests/test_formatting.py tests/test_openai_streaming.py tests/test_sdlc.py tests/test_github.py -q

# Both agent studios and feature runs, without model calls or external services.
check-agents:
    npm run check
    npm run build
    npm run test:collaboration
    npm run test:editor
    uv run pytest tests/test_workspace.py tests/test_formatting.py tests/test_openai_streaming.py tests/test_sdlc.py tests/test_github.py tests/test_features.py tests/test_feature_project.py tests/test_cooperative.py tests/test_stage_budget.py tests/test_stage_code_mode.py tests/test_shared_library.py tests/test_experiments.py tests/test_child_lifecycle.py tests/test_check_reuse.py tests/test_stage_context.py tests/test_source_queries.py tests/test_validation_recovery.py tests/test_harness_debug.py tests/test_integration.py -q
