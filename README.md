# Code Studio

Code Studio is a local example app built with the **Temporal Agent Harness**.
It demonstrates how to build an editable, multi-agent software development workflow
with human approvals, shared editing, and durable execution.

The example defines six agents: **Ideation, Planner, Design, Development, Review,
and Deployment**. You can customize their instructions and tools, run them on a
feature, inspect their work, and approve the next step. The application defines the
process; the harness provides durable agent execution underneath it.

**This is an example for learning and experimentation, not a production development
platform.** It runs on one computer and has no user authentication. Display names
identify browser participants; they are not accounts. Keep the services on loopback
and use codebases and test commands you trust.

## What it demonstrates

- Editable agent instructions, settings, and Python orchestration.
- Shared editing and conversations across browser sessions.
- A coordinator that delegates to stage agents, with human approval gates and a
  bounded review/repair loop.
- Saved inputs, handoffs, tool activity, check results, and restart recovery.
- Isolated feature workspaces, local release ZIPs, and reviewed merges into a project.
- Paired experiments for comparing agent playbooks against the same starting inputs.

## Run locally

Requirements:

- macOS or Linux: the local host uses Unix process and file-lock APIs.
- Git, Python 3.11+, and uv.
- Node.js 22.23.4+ and npm, matching `package.json`.
- Temporal CLI and, optionally, just.
- An OpenAI API key with access to the configured models. The example currently
  defaults to `gpt-6-luna` and also supports `gpt-6.1-sol` in agent settings.

The runtime uses `temporal-agent-harness[ui,openai-agents,code-mode]==0.6.0` and
`@temporalio/agent-harness-svelte@0.1.0`. Dependencies come from PyPI and npm;
no sibling harness checkout is required.

From the repository root:

```sh
# Create the local configuration only if it does not already exist.
if [ ! -f .env.local ]; then cp .env.example .env.local; fi
# Edit .env.local and set OPENAI_API_KEY before continuing.
just install
just dev
```

Without just:

```sh
uv sync
npm ci
uv run --env-file .env.local python scripts/dev.py
```

Use the same `.env.local` setup with either approach. Model calls use your API key
and incur usage charges. Source, prompts, and tool results are sent to the configured
model. Common credential files are excluded from source copies, but secrets embedded
in ordinary source files may still be included.

Open **http://127.0.0.1:5174**. The launcher starts the local services, creates the
`code-studio` namespace if needed, and reuses an existing Temporal server on port
7233. Ctrl-C stops the processes it started. Restart `just dev` after Python changes.

| Service | Address |
| --- | --- |
| Code Studio | http://127.0.0.1:5174 |
| Harness UI/API | http://127.0.0.1:8001 |
| Workspace host | http://127.0.0.1:8002 |
| Temporal server | localhost:7233 |
| Temporal UI | http://localhost:8233/namespaces/code-studio/workflows |

Local state and logs live under `.local/`. Keep the workspace data and Temporal
state together when restarting a demo; they contain the saved runs and conversations.

## Try a feature

1. Open **Features** and choose **Start a feature**. Use this checkout or a disposable
   project as the working directory. **Current working files** includes uncommitted
   source changes in an isolated copy; **Committed Git revision** starts a Git worktree.
2. Describe a small change, review the proposed brief and acceptance criteria, and
   start the feature. For collaboration, open another browser session with a different
   display name.
3. Follow Ideation and Planner, then choose **Approve plan & continue**. Design,
   Development, and Review work through the feature and its checks.
4. Inspect the code diff, handoffs, and check evidence. Review may send changes back
   to Development. Approve the review when you are ready to continue.
5. Deployment prepares a **local source ZIP and deployment notes**. It does not
   publish, push, or deploy the project to a remote service.
6. For a feature created from current working files, use **Merge into project** to
   review and apply the changes locally. The app retains recovery information and
   offers **Undo merge**. Git-worktree features use an explicit Git branch review/merge.

Open an agent from **Agents**, or choose **Customize agent** within a feature, to edit
its definition. Changes apply to future stage runs; existing runs keep their saved inputs.

| Agent file | Purpose |
| --- | --- |
| `<stage>_agent.py` | Python orchestration executed through Code Mode. |
| `<stage>_instructions.md` | The agent's role, priorities, and instructions. |
| `<stage>_settings.json` | Model, enabled tools, and turn limit. |

The [usage guide](docs/usage.md) covers stage questions, approvals, conflict recovery,
playbook experiments, and the standalone PR-review and DAG examples. The
[demo walkthrough](DEMO.md) provides a guided presentation.

## Optional previews and editing-only mode

For read-only browser previews, install Chromium on the host:

```sh
uv run playwright install chromium
```

Then use a feature's **Browser preview** tab. The current adapter previews Code
Studio's Vite frontend against the running backend. It does not run changed backend
code or verify saving and other mutations.

To try shared editing without Temporal or a model key, run these in separate terminals
after installing dependencies and creating `.env.local`:

```sh
just collaboration
```

```sh
just studio
```

Agent execution is disabled in this mode. Stop both processes before using `just dev`.

## Explore the code

| Location | Purpose |
| --- | --- |
| `cooperative/definitions.py` and `cooperative/prompts.py` | Stage definitions and default playbooks. |
| `agent_dag/pipeline_agent.py` and `agent_dag/live_stage.py` | Coordinator and stage workflows. |
| `cooperative/` | Approvals, shared artifacts, experiments, checks, and local integration. |
| `workspace/` | Shared editing, persistence, and agent callback execution. |
| `features/` | Feature source copies, Git worktrees, and file operations. |
| `sdlc/` | Standalone PR-review example and GitHub evidence capture. |
| `ui/` | Svelte application. |

## Checks

```sh
# Selected application checks without a running Temporal server or model credentials.
just check-agents

# Full build and test suite.
just check
```

The full suite requires the Temporal CLI, Chromium, and permission to bind local
ports. Temporal integration tests start an ephemeral server and use scripted agents
or model stand-ins. These tests do not replace a live-model smoke test.
`npm run test:collaboration` tests editor and client logic; it is not rendered browser
automation.

## License and attribution

Code Studio is available under the [MIT license](LICENSE).
