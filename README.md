# Code Studio

A local cooperative development platform. Teams customize **Ideation, Planner, Design,
Development, Review, and Deployment** agents, then run them together on a shared feature.
The Agents page opens each agent’s collaborative Studio. The Features page keeps the
brief, team input, agent handoffs, code, real checks, and release preview together.

UI work across all projects follows the [Holocene/io design policy](DESIGN.md),
including changes to Code Studio itself. New stage runs receive frozen guidelines
and on-demand written foundations for Design, Development, and Review. Component,
token, and icon APIs must be verified against the target project's installed source.

The Agents page also lists three support agents: Studio designer, Feature suggestion agent,
and Merge conflict resolver. Expand an entry to edit its instructions and inspect its
model selection, trigger, and recorded request activity. Suggestion and resolver model
settings follow Ideation and Development respectively; their prompts remain separate.
Choose **Save instructions** to persist changes in the workspace database. New requests
capture the saved prompt and version; in-flight requests retain their inputs. Studio
designer instructions are editable separately for each workflow agent it helps customize.
Conflicting saves preserve your draft; reload the latest version before retrying.
**Use default instructions** fills the editor but requires saving to apply.
The Feature suggestion agent powers **Suggest Feature / Submit** before a feature starts;
the workflow's Ideation agent has separate instructions in its Agent Studio.
Activity tracking starts when the updated workspace host first opens the database;
older requests are not backfilled. Studio counts acknowledged submissions, while the
advisor and resolver count completed responses. Neither count proves a merge or edit
was applied. Restart the host and worker after updating Python code.

When a stage is blocked on questions, its **Needs attention** box includes **Ask agent**
for explanations, examples, and follow-up questions. This read-only conversation uses
the stage's frozen instructions and context, and persists with that run. It does not
edit code, change scope, or resume work. Suggested requirements are proposals: review
and edit them, then choose **Submit requirements** to add them to Team input. This
updates scope and clears old approvals; choose **Resume workflow** to start again from
Ideation. Historical questions cannot submit requirements into a newer stage or scope.
Clarifications allow up to 12 questions per stage attempt, with one pending question at
a time. Host restarts reconnect pending explanations to the same Temporal execution.
Draft questions and requirements are recovered from this tab's session storage.

The runtime uses published packages: `temporal-agent-harness[ui,openai-agents,code-mode]==0.7.0`
from PyPI via uv and `@temporalio/agent-harness-svelte@0.1.0` from npm. No sibling harness
checkout is required.

## Start

Requirements: Git, Python 3.11+, uv, Node.js 20.19+ (or 22+), npm, Temporal CLI, and just.
Set an OpenAI API key with access to `gpt-6-luna` (the default builder/reviewer model).
Each agent can also use `gpt-6.1-sol`, selected in its Studio settings file.

```sh
# First setup only: preserve an existing .env.local.
cp .env.example .env.local
# Set OPENAI_API_KEY in .env.local.
just install
just dev
```

**Updating an already running demo: stop and restart `just dev`.** This milestone adds Python
workflows and host endpoints; Svelte hot reload alone cannot load them. Existing projects and history are retained. Three definition files per lifecycle agent are added automatically.

Open **http://127.0.0.1:5174**. The stack uses ports 5174 (UI), 8001 (Harness UI/API), 8002
(workspace host), and 7233 (Temporal). Temporal UI is at
http://localhost:8233/namespaces/code-studio/workflows. Logs are in `.local/logs/`.
`just dev` reuses an existing Temporal server, creates the `code-studio` namespace if needed,
and stops only its own processes on Ctrl-C. Python changes require a service restart.

The **Harness UI** runs automatically with `just dev` at http://127.0.0.1:8001/,
also accessible through `/harness/` in Studio. The feature overview embeds a
state-flow preview of the current **coordinator**, showing its delegation across
stages instead of following each stage child. New executions switch to their new
coordinator; features without one retain a generic Harness UI link.

Play, pause, scrub, and zoom inside the preview. The minimap is hidden. Only the
expand icon opens a new tab; it also minimizes the preview to avoid keeping both
views subscribed. Minimize/restore is remembered, and hidden tabs suspend the
iframe. Ordinary feature refreshes preserve playback. Studio and DAG views show
their own session preview at the bottom right.

The adapter treats a directly opened child as the root of its own graph and
keeps explicitly discovered descendants separate. `scripts/harness_server.py`
serves the published UI with a small, tested 0.6.0 compatibility adapter so
explicit links attach to the requested workflow, including closed workflows,
instead of silently selecting another managed session. It patches a temporary
asset copy and never edits the installed package. Recheck the adapter when
upgrading the harness. Both `just dev` and `just server` use this launcher.

Without just:

```sh
uv sync
npm ci
uv run --env-file .env.local python scripts/dev.py
```

## Build a feature together on this codebase

1. Open **http://127.0.0.1:5174**, choose **Start a feature**, and join as Alice.
   Open the same URL in another browser or private session and join as Bob.
2. The working directory defaults to this **code-studio** checkout. **Current working
   files** copies your current source, including uncommitted changes, into an isolated
   feature workspace. Dependencies, Git metadata, generated output, and common credential
   files are excluded. You can instead select **Committed Git revision** to create a real
   branch and Git worktree from a committed base; that option requires an initial commit.
3. Type an idea in **Describe feature** and choose **Submit** to generate a focused
   work description and acceptance criteria. Leave it blank and choose **Suggest Feature**
   for a fresh recommendation. The agent uses the selected working directory and starting
   source; existing features and earlier suggestions provide context. Returned details
   expand for review and editing. You can also open **Feature details** and write them
   yourself, save a draft, or choose **Start feature** when ready. Suggestions do not start
   implementation, and failed requests preserve your draft.
4. Ideation clarifies the brief and hands it to Planner. Review the plan’s output and
   handoff. Bob can post comments while work continues. Requirements, questions, and
   decisions update scope, pause current agents, and require a new plan and approval.
5. Choose **Approve plan & continue**. Design specifies the solution, Development writes
   code and tests, and Review checks the diff and evidence. The stage strip shows the
   current agent, completed work, and approval gates. Click a stage to inspect its output.
6. Use **Code** to inspect the diff or edit feature files. Saves use content hashes to
   reject stale writes while preserving browser drafts. **Checks** shows actual commands,
   output, exit status, and unavailable checks. For Code Studio these include Python
   syntax, pytest, browser collaboration tests, and Svelte/TypeScript validation.
7. Review sends blocking findings or failed checks back to Development, with a maximum
   of two automatic repair passes. Resolve remaining blockers with team input or a rerun.
   The team approves the review before Deployment starts.
8. Deployment produces a **downloadable local source ZIP** with a check manifest and
   deployment/rollback notes. It does not publish, merge, push, or deploy remotely.
9. Review the Code diff, then **Merge into project**. Studio combines the feature
   with your latest local files, checks the preview, and applies it when checks pass.
   Successful checks are reused only while their inputs and environment still match.
10. If conflicts remain, choose **Resolve with agent and merge**. A dedicated Temporal
    agent uses the complete original, local, and feature versions plus feature requirements.
    It uses the Development agent’s model setting and resolves files in the preview;
    checks must pass before application. Resolution summaries appear in merge details.
    Ambiguous intent, unavailable text (binary/large files), and model failures stop the
    merge with an explanation. Retry after addressing the cause. Local edits made during
    resolution invalidate the preview rather than being overwritten.
11. Application retains a backup and durable receipt. **Undo merge** restores previous
    files while preserving later edits. Interrupted applications expose **Restore previous
    files**. Studio does not commit or push automatically. Restart `just dev` after applying
    Python changes. Git-worktree features continue to use explicit Git branch review/merge.

Integration copies and baselines live under the managed feature workspace directories;
receipts are in `.local/shared-project/integrations/`. Retain these directories while
rollback is needed. Validation copies have their own private Git repository so Git
commands cannot accidentally discover the parent checkout. Application is journaled
with atomic file replacements, not a whole-directory filesystem transaction: other
programs can observe intermediate writes. Detected failures trigger recovery, and
conflicting later edits are preserved for reconciliation.

The isolated source is in `.local/shared-project/feature-worktrees/`. Python checks reuse
this checkout’s `.venv`; npm checks reuse its installed dependencies. No dependencies are
installed automatically. The check runner runs project test/build scripts locally, with a
300-second Python timeout (120 seconds for other commands), bounded output, and no
inherited API credentials. Only use codebases and checks you trust. The feature pytest
command excludes `test_check_code.py` and `test_execute_flow.py`; the pipeline integration
tests and the rest of the suite still run.

## Customize the agents while work progresses

Open any card on **Agents**, or choose **Customize agent** inside a feature. Each Studio
has shared editing, presence, Python highlighting/formatting, validation, and an assistant
that can edit only that agent’s definition. Open the same Studio in two browser sessions
to collaborate. The contextual **Back to feature** link returns to the feature you were viewing.

| Studio file | Purpose |
| --- | --- |
| `<stage>_agent.py` | Executable Code Mode orchestration: read context, run the model, save the handoff. |
| `<stage>_instructions.md` | The agent’s editable role, priorities, and instructions. |
| `<stage>_settings.json` | Model, name, enabled tools, and turn limit. |

Try adding this to `planner_instructions.md`: **“For session identity changes, explicitly
cover refresh, direct links, navigation, and unavailable browser storage.”** Validate the
agent, then rerun Planner or start a second feature to compare results. In the feature’s
**Agent** tab, inspect the exact frozen files and version used by each run. Studio edits
apply when the next stage starts; they do not alter an agent already running. Rerunning a
stage resets its downstream outputs and approvals, while retaining code and run history.

Each model call budget reserves its last turn for a structured handoff, using the file/tool
results already collected. Once that turn begins, tools are disabled: the agent must finish
or report the work still blocked. Planner starts with 24 turns. Existing untouched 12-turn
Planner settings upgrade on restart; customized settings and past frozen runs are preserved.
A failed stage retains earlier outputs and files and can be retried with the current definition.

The default playbooks in `cooperative/prompts.py` give each stage distinct decisions,
deliverables, and stopping conditions. Shared guidance prioritizes focused inspection,
acceptance coverage, and evidence-backed handoffs. On startup, untouched original prompt
seeds upgrade automatically; custom Studio instructions and frozen runs are preserved.

New runs on both execution paths receive source search and range reads when source reading
is enabled, plus read-only access to their frozen shared library. Context includes the
complete immediate predecessor result and current Review feedback during Development
repairs. Other upstream results remain available through canonical artifact paths.
Oversized results use those paths instead of truncated handoffs to stay within the context
budget. Previously frozen runs keep their original tool contract.

The default chain is:

```text
Ideation → Planner → [team approves plan] → Design → Development → Review
                                                      ↑            │
                                                      └── fixes ───┘
Review → [team approves review] → Deployment → local preview + deployment plan
```

Only Development can write source. Review and Deployment can run configured checks;
Deployment can package the preview. The local host independently enforces stage/tool
permissions, approvals, scope revisions, file hashes, and write receipts. Results cannot
claim files changed unless the corresponding host writes succeeded. The host derives each
stage's `changed_files` from that run's successful write receipts; files inspected by Review
remain attributed to Development. A model list mismatch is recorded in tool activity without
discarding the stage's review or handoff. Active stages and
handoffs resume when the host restarts. Pausing retains partial files and run history.

Definitions live in `cooperative/definitions.py`; executable workflows are in
`agent_dag/stage_agent.py` and `agent_dag/studio_designer.py`. `cooperative/runtime.py`
coordinates persisted handoffs and approvals. All agents use the published harness packages.

Source and agent prompts are sent to the configured model. The demo excludes common
credential paths but cannot recognize secrets embedded in ordinary source. Display names
are local collaboration identities, not authenticated remote accounts.

## Existing PR-review and feature-builder demos

The earlier standalone PR review remains at **/?view=review**, with its original agent
files, fixtures, comparisons, and GitHub PR evidence. Earlier feature runs remain at
**/?view=features**. The cooperative Review agent reviews the current feature’s local diff;
the standalone PR flow continues to review GitHub pull requests.

## Review: two-person walkthrough on one computer

1. Open **http://127.0.0.1:5174/?view=review** in a normal window as **Alice**, and in a private window or another browser
   as **Bob**. Two tabs also work. Each joins separately.
2. Alice opens `agent.py`; Bob opens `instructions.md`. Edit different lines. Both sessions
   see shared edits, presence, and the activity feed. Select the same file to watch edits live.
3. Ask the shared builder: **“Focus this reviewer on correctness and missing edge cases.
   Preserve the evidence tools and validate the agent after your edits.”** Both people see
   its chat and file changes. Human edits made while the builder thinks are preserved;
   stale agent patches must reread the current revision.
4. Open **Runs & reports** in both sessions. Click **Validate agent**.
5. For a repeatable first run, choose **Saved fixture → Demo PR · empty average regression**
   and **Run agent**. This runs your actual `agent.py` in the harness sandbox with a live
   model review and synthetic PR/CI evidence. It requires Temporal, the worker, and an API key.
   The fixture contains one failing, two passing, and one skipped test; no tests are executed.
6. Both sessions see the run, findings, CI/test details, tool trace, frozen agent files, and
   evaluation assertions. Bob can discuss a finding; Alice sees the comment in **Discussion**.
7. Choose **GitHub PR** and run the default URL:
   **https://github.com/temporalio/ui/pull/3948**. The app captures the PR and available CI data
   before executing the agent. Missing reports, expired artifacts, and inaccessible logs are
   shown explicitly. It never infers individual passing tests from a green job.
8. Use **Save fixture** on that run. Edit the agent, then run the saved fixture again. Open
   **Compare**, select the earlier completed run, and compare findings, evaluation status,
   duration, and reported token use. Matching evidence hashes establish the same input.
9. Inspect `.local/shared-project/` on disk. Reload and rejoin: files, reports, fixtures, and
   comments persist. The builder conversation persists in Temporal.

The **Tool catalog** includes signatures and descriptions. Preview read-only tools against
any saved fixture without a model call. For the built-in fixture, try `read_file` with
`stats.py` or `search_repository` with `average`.

## Editable agent project

| File | Purpose |
| --- | --- |
| `agent.py` | Executable async Python flow, using the published harness Code Mode sandbox. |
| `instructions.md` | Instructions passed to the review model. |
| `agent.json` | Agent name, review model, and enabled tools. |
| `evaluations.json` | Fixture-specific expected findings and a maximum finding count. |
| `app.py` | Existing Python scratch file, retained from the shared-editor milestone. |

`agent.py` uses `import asyncio` and the registered host functions. It can sequence calls,
branch, or use `asyncio.gather`. Every tool returns a JSON string. The starter gathers evidence,
calls `review_changes(context, focus)`, then calls `save_review(review_json)`. Validation checks
JSON settings, supported imports, and the Python/tool type contract before a run is accepted.
A valid program can still fail at runtime; failures and partial reports remain visible.

`review_changes` returns a JSON object with `summary` and `findings`. Each finding requires
`title`, `severity` (`high`, `medium`, or `low`), `path`, 1-based `line`, `explanation`, and
`suggestion`. `save_review` validates this shape and referenced files/lines, then attaches
CI evidence from the capture, independently of the model's response.

Evaluation rules use a fixture ID. Set `fixture` to a saved fixture's ID and define
`expected_findings` entries with `name`, `path`, and a case-insensitive `contains` substring
matched against a finding's title/explanation. `max_findings` bounds noise. Live PR runs and
runs on other fixtures show **not configured**. Edit evaluations together with the agent;
each run keeps its own frozen rules. Evaluation checks review output, not repository tests.

## Standalone PR agents and tools

- **SDLC builder** (`agent_dag/sdlc_builder.py`): shared conversation that lists, reads, edits,
  and validates project files through the sole local callback host. It does not start runs.
- **PR reviewer** (`agent_dag/pr_review.py`): one Temporal workflow per run, executing that
  run's frozen `agent.py`. Its model sees the frozen instructions and supplied evidence.
- **Evidence tools**: `get_pull_request`, `get_diff`, `read_file`, `search_repository`,
  `get_checks`, `get_test_results`, and `get_failure_details`.
- **Review tools**: `review_changes` invokes the configured model; `save_review` saves the
  structured local report. Reports and discussion are not posted to GitHub.

GitHub capture is read-only. Public metadata works anonymously. For private repositories,
higher rate limits, or Actions logs/artifacts, configure `GITHUB_TOKEN` or `GH_TOKEN` in
`.env.local`, or authenticate with `gh auth login`; restart the workspace host after changes.
The host checks these sources in that order. If an automatically discovered CLI token is rejected
with HTTP 401/403, it retries the public PR anonymously and records that fallback in evidence notes.
An explicitly configured environment token fails visibly. Use a token with repository read access and,
when needed, Contents, Pull requests, Checks, Commit statuses, and Actions read permissions.
Credentials remain on the host and are excluded from project snapshots and artifacts.

## Standalone PR evidence and execution boundaries

A live capture records base/head SHAs, changed-file patches, head-file contents, check runs,
commit statuses, and available Actions failures and JUnit artifacts. The PR is checked again
at the end of capture; a moving base/head causes the run to fail rather than mix revisions.
Head and merge-commit checks show the SHA each actually tested. Actions reports are drawn
from the latest run of each workflow/event and its current attempt; older-attempt artifacts
are skipped. CI may continue changing after capture; a fixture preserves what was observed.

Capture limits are explicit: up to 100 changed files, 80,000 patch characters (12,000/file),
20 head-file contents (20,000 characters/file, 100,000 total), eight Actions workflows,
5 MB per artifact/log download, and 1,000 individual JUnit cases. JUnit artifacts are
recognized by names containing `junit`, `pytest`, or `test-results`/`test-report` variants.
Expired or inaccessible artifacts do not produce invented test results. The report retains
all captured evidence; tool responses use smaller excerpts for model context.
`read_file` and `search_repository` cover captured changed files, not the full repository.
Evidence notes and truncation flags identify missing coverage.

Runs allow at most three review model calls and 80 callback/trace entries, two active runs,
and eight queued/active local tasks. Capture times out after three minutes; execution after
ten minutes. Temporal adds a 15-minute workflow deadline. Cancelling a run requests Temporal
cancellation. Restarting the workspace host resumes unfinished runs and reuses persisted
callback receipts, so a saved report is not duplicated.

The standalone PR reviewer reads captured CI evidence; it does not clone or execute the PR.
The cooperative feature workflow above runs local checks against its isolated source.
Neither flow installs dependencies or publishes GitHub reviews. Agent orchestration scripts
run in the restricted harness sandbox; local test/build commands run in host subprocesses.

## Shared editing and local persistence

The browser and host merge edits using stable character IDs and deletion markers. Each fixed
project file supports 20,000 characters and 200,000 retained character records. Concurrent
edits converge, although their meaning or syntax may still need human correction. Python
files have syntax highlighting and **Format Python / Shift+Alt+F**, using local Ruff through
the same shared edit protocol. A file change during formatting requires a retry.

Pending edits are retained in the tab's session storage and resent on reconnect. Closing a
private session can discard an unsynced draft. SQLite stores project documents, edit and
callback receipts, run inputs, reports, fixtures, comments, and activity. The host mirrors the
files into `.local/shared-project/` before acknowledging file callbacks. **Edit through the
browser:** external editor changes are imported only when a file is first initialized, not
watched after that; a subsequent host save can overwrite them.

Workspace presence tracks live browser connections. Connections close when a page is
left or cached for back/forward navigation, and reopen when the page returns. The host
expires connections after two minutes without a client message so abandoned connections
cannot remain in the list indefinitely. Separate active tabs can still show the same
display name. Restart the workspace host after updating this cleanup behavior.

`WORKSPACE_DIR` selects another local project directory. Keep its SQLite database and Temporal
namespace together for conversation continuity. One host lock prevents duplicate callback
executors. This is a loopback, single-computer demo; display names are presence, not accounts
or authentication. It does not provide remote multi-tenant collaboration or collaborative undo.

For editing and tool previews without Temporal or a model key, run these in separate terminals:

```sh
just collaboration
just studio
```

Chat and review execution are disabled in this mode. Stop these processes before `just dev`.

## Services and checks

Individual recipes: `just temporal`, `just session-manager`, `just server`, `just worker`,
`just workspace`, and `just studio`. They load `.env.local` and must share the same Temporal
profile/namespace (default `temporal.local.toml`). Vite proxies `/api/` to 8001 and `/workspace/`
(including WebSockets) to 8002. Production preview uses those same proxies.

```sh
# All current milestone checks, without network, Temporal, credentials, or model calls:
just check-agents
```

The tests execute the starter flow in the installed Monty sandbox with a deterministic model
stand-in and real callbacks; exercise two WebSocket participants, multi-file edits, reconnects,
stale patches, local persistence, immutable runs, fixture comparisons, shared comments,
cancellation and restart recovery; and capture GitHub evidence through a mocked HTTP transport.
They also cover Python formatting, editor protocol/highlighting, the OpenAI streaming adapter,
real Git worktrees, approval and file conflicts, editable feature flows in the published sandbox,
version freezing across approval/coding, agent model configuration, the six-agent catalog, sequential handoffs, repair passes, local check evidence, release ZIPs,
and conflict-checked source application.
This does not replace a live GitHub/model run using the walkthrough above.

`uv run pytest -v` also runs the original DAG workflow integration tests. The real Temporal
tests share one ephemeral server per pytest process (separate from the dev server), but
keep fresh clients on each test's event loop, unique worker queues, and separate local
workspaces. Teardown terminates parked workflows and their children, including after
test failures. These tests require permission to bind local ports and use scripted/echo
agents or fake models without an OpenAI key. The original DAG stream waits are bounded
at 30 seconds so a stuck workflow fails instead of consuming the entire check timeout.

The pipeline callback host wakes on callback/child/approval events and completed callback
tasks. Its 250 ms polling interval remains a fallback for reconnects and missing stream
events, rather than an unconditional delay between tools. This applies to normal feature
runs as well as integration tests; restart the workspace host after changing this code.

## Original example and compatibility

New feature runs use a durable `FeaturePipelineAgent` coordinator with a
`SdlcStageAgentV2` child for each stage. The Activity view shows the parent/child
tree and model/tool events; stage output includes observable progress, token
usage, checks, changed files, and saved progress after the child closes.
Closed-child live streams may be unavailable; saved artifacts remain available.

The feature's **Skills & artifacts** tab browses read-only virtual files. New
stage runs freeze `/skills/project/SKILL.md` (shared collaboration conventions)
and `/skills/<stage>/SKILL.md` (that stage's saved Studio instructions). Edit the
stage instructions in Studio to change its playbook for future runs. Edit the
shared project playbook in a feature's **Experiments** tab. Saves use version
checks to preserve teammate edits; existing runs keep their frozen contents.

Saving a structured stage result automatically publishes `result.json`,
`handoff.md`, and numbered deliverable Markdown files under
`/artifacts/scope-<revision>/<stage>/<run-id>/`. Reruns keep earlier versions.
The browser can show all saved versions or one run's exact frozen inputs.
Agents can call `stage_list_shared_files` and `stage_read_shared_file` through
Code Mode or individual tools. They see only completed artifacts from their
scope that existed at launch; content hashes protect those references. These
files do not grant source-write access or imply an approval.

This library uses callback tools and saved feature records on harness 0.6.0.
It does not require the draft VFS mount APIs from upstream PR #170 and does not
add persistent agent memory. A native mount adapter can use these paths later.

### Paired playbook experiments

Open a feature's **Experiments** tab once its current stage has finished. Enter
a hypothesis and a variant playbook, then choose **Create isolated pair**. The
baseline uses the saved shared project playbook. Both attempts capture the same
current feature source, brief, contributions, and all six agent definitions.
This captures the feature workspace as it is now, including saved changes, not
necessarily the original repository revision. Each attempt gets its own copy;
later Studio or shared-playbook edits do not alter either attempt.

Choose **Start both attempts**, then use each **Open attempt & approvals** link
to inspect plans and approve them separately. The comparison shows source diffs,
saved stage artifacts, current versus stale check evidence, recorded token usage,
repair rounds, and review findings. Missing usage is reported as unrecorded, not
zero. Acceptance outcomes are human assessments with notes and conflict checks;
they are marked stale when source, scope, results, or check evidence changes.
No automatic winner is selected. Repeat on several tasks before keeping a rule.

Experiment source snapshots, playbooks, definitions, and assessments persist in
the workspace. Interrupted creation can resume from the captured source manifest,
and a repeated start does not launch another execution. If the original source
changes before its snapshot was copied, preparation fails rather than silently
capturing different inputs. Experiment attempts cannot apply changes to their
shared baseline; use their diffs to decide which changes to adopt in your project.

Local checks: `uv run pytest tests/test_experiments.py -q`.
Real Temporal, model-free paired approval test:
`uv run pytest tests/test_experiment_harness.py -q`.

Plan and release decisions are native harness tool approvals, resolved through
the feature API so scope revisions and reviewed source hashes remain enforced.
The coordinator waits across host restarts. Pause stops new callback execution
at a tool boundary; in-flight work can finish. Resume continues the same run.
Live feedback reaches the next model turn without changing approved scope;
requirements and decisions still invalidate the previous scope.

Stage settings default to `"tool_mode": "code"`: the model can batch independent
reads/checks using Python over its saved tool allow-list. Writes still use the
host's hash checks, receipts, and tool budget. Set `"tool_mode": "individual"`
to expose the tools separately. This adds no shell or arbitrary source access.
The coordinator uses deterministic stage transitions and a bounded repair loop.

### Faster stage cycles

Newly launched durable stages use compact, frozen inputs: the project playbook
is supplied once, upstream summaries point to complete canonical `result.json`
artifacts, and repository excerpts favor referenced or changed files. Existing
saved stages retain their original prompts and tool contracts.

Stages with source-read permission also receive `stage_search_source(query,
glob, limit)` for bounded literal searches and `stage_read_range(path,
start_line, line_count)` for up to 200 lines. Truncation is explicit. Full-file
reads remain necessary before replacing existing file contents.

New Development runs with source-read and source-write permission also receive
`stage_patch_file(path, expected_hash, old_text, new_text)`. Use it for small edits
to existing files: the old block must match exactly once, and the hash must match
the full file. A range read supplies that hash without loading the whole file.
Each block is limited to 20 KB; an empty replacement deletes the matched block.
The host preserves unmatched content and line endings, rejects stale or ambiguous
edits, and uses durable write receipts to recover interrupted saves. Await edits
sequentially and use the returned hash for the next edit. New files and intentional
full replacements still use `stage_write_file`. This is available in both tool
modes after restarting the host and worker; already frozen runs keep their tools.

For new stages, `stage_run_check(check, targets, force)` accepts `targets=[]`
for full validation. Focused `syntax` accepts Python source paths; focused
`python_tests` accepts existing pytest test-file paths. Other checks require
full coverage. Use `force=False` normally and `force=True` to investigate
flakiness or external-state changes. Code Mode requires all three arguments.

The host coalesces identical in-flight requests and persists successful and
failed evidence keyed by source, targets, command, sanitized environment,
runtime binaries, and installed dependency metadata. A cached failure stays
failed. Timeouts, unavailable checks, and stale evidence are not automatically cached.
Deployment reuses an explicitly accepted check when its source and validation
identity still match; the recorded result remains failed or incomplete.
Dependency metadata is local evidence, not a snapshot of external services;
force reruns when external state matters.

Focused results never satisfy full validation. Development, Review, and release
preparation validate syntax, configured Python/browser tests, types, and build,
reusing matching full results across stages. Unconfigured checks remain visibly
unavailable. Checks run with at most two concurrent processes, and npm scripts
are serialized per workspace. Inputs are checked before and after execution
and at the final validation barrier. Review still independently inspects code;
plan and release approvals remain required.

Python checks have a 300-second limit (other commands: 120 seconds). Pytest
prints test names, the ten slowest durations, and a stack trace after 60 seconds
in one test, making timeouts diagnosable without reducing test coverage.

Agents plan and validate with the tools available to them. Browser visual testing
is not a workflow requirement. Source inspection and available automated checks
support approval; unavailable visual validation is reported as a limitation,
not automatically promoted into a blocking requirement. Concrete defects and
failed checks still require attention. If an agent cannot recommend approval,
it leaves the stage blocked for the team.

**Manual Approve** is available for blocked Development, Review, and Deployment. It
requires no evidence: Development advances to Review, and Review advances to
Deployment. Deployment completes the existing local preview without applying
files or publishing anything; its ZIP must exist and match the current reviewed
source. The author, source hash, scope, and accepted check evidence are
recorded while the original findings and failed checks remain unchanged. A
manual Review approval accepts those existing check failures for release
preparation; changed source, scope, or different check failures require a new
decision. Equivalent timeouts can retain approval despite progress-log changes
when command, timeout, dependencies, source, and scope still match; newly
reported failures require another decision. Deployment blockers identify the
check and offer **View check output**, **Retry Deployment**, and **Manual Approve**.

The legacy `browser_tests` name refers to `npm run test:collaboration`, not
rendered browser automation. Agents receive that capability limitation and
do not demand manual browser evidence or send impossible validation tasks back
to Development. Stale checks retry once;
persistent staleness/timeouts block for intervention rather than consuming code
repair rounds. Vite's `.vite-temp` cache and directory timestamps are excluded
from dependency fingerprints; actual dependency file changes still invalidate
cached checks.

### Read-only browser previews

Open a feature's **Browser preview** tab, choose **Build preview**, then **Open
preview**. The host builds the feature's isolated frontend and serves an immutable
copy on a separate loopback port. It reads live data from the existing backend.
The first adapter supports Code Studio's Vite frontend (`dist/index.html`); other
projects need an adapter. New backend APIs are not available until the running
backend supports them. This previews feature source, not the final integration
candidate after conflict resolution.

The gateway allows only audited read routes. It rejects other HTTP methods,
unknown routes, redirects, and socket edits/prompts. A separate read-only socket
provides snapshots and refresh notifications. CSP limits network access to the
preview origin, disables forms/frames/workers, and the agent browser also blocks
external requests and writes. The preview banner and disabled mutation controls
are usability aids; backend restrictions do not depend on them. Navigation,
disclosures, and preview-local theme choices remain available. No live user
cookies or browser storage are copied. Preview URLs are local to the host.

Install the browser on the host once:

```sh
uv sync
uv run playwright install chromium
```

Restart the workspace host and worker. Newly frozen Development and Review runs
with source-read and check permissions receive `stage_browser(action, path,
target, width, height)`. Actions are `open`, `inspect`, `navigate`, and `scroll`.
The tool stays separate from Code Mode so screenshots reach the model as images.
It does not expose typing, submissions, arbitrary JavaScript, or external URLs.
Existing runs keep their original tool contracts.

Screenshots, accessible page content, browser errors, run IDs, scope revisions,
and source hashes are retained in **Browser preview**. Metadata is stored in
SQLite and JPEGs under `.local/shared-project/browser-previews/evidence/`.
Changed source or scope marks earlier evidence stale. Backend data remains live;
screenshots record one moment, not a reproducible database snapshot. This is
visual/navigation evidence, not a passing mutation or acceptance test.

At most two preview servers run at once. They expire after 30 minutes or when
stopped; screenshots remain available. Preview builds reuse installed dependencies
and serialize with other npm checks. Browser sessions are disposable. After a
restart or interrupted action, agents must reopen the page; an uncertain action
is not automatically repeated. Browser callback evidence is durable. Bounded
image data also crosses the callback history to deliver screenshots to the model.

`uv run pytest tests/test_browser_preview.py -q` verifies gateway denial, real
Chromium navigation, screenshots, retry behavior, staleness, and real Temporal
stage callbacks without model credentials. It requires Chromium, the Temporal
CLI, and permission to bind local ports.

An already-closed child is now cleaned up without losing its saved handoff.
Late feedback reports that the stage has finished; unrelated control errors
still propagate. The compatibility adapter is specific to harness 0.6.0.

Restart the workspace host and agent worker to load these changes. Existing
legacy feature runs retain their original workflow path. No harness dependency
upgrade is required. Regenerate client contracts with
`uv run python scripts/generate_agent_types.py` after editing workflow schemas.

`uv run pytest tests/test_pipeline_harness.py -q` exercises real local Temporal
workflows, Code Mode callbacks, approvals, restart recovery, pause/resume, and
scope/hash conflicts without model credentials. It requires the Temporal CLI
and permission to bind local ports. `tests/test_stage_code_mode.py` checks the
script tool surface and generated contracts. These checks do not replace a live
model smoke test.

The original example's workflows, tools, and components remain under `agent_dag/` and `ui/`.
`agent_dag/openai_streaming.py` adapts harness 0.6.0's observer to OpenAI SDK 3.24.0 events that
lack `name` on completed function arguments. It preserves the original event for replay and
can be removed once the dependency contains the upstream fix.

The initial application and integration tests came from
[`temporal-community/temporal-agent-harness`](https://github.com/temporal-community/temporal-agent-harness/tree/0.6.0/examples/agent_dag),
commit `b0692a68b7ebca64689b6e3c8cd098a1422b44d2`; the DAG example matches release `0.6.0`.
Its MIT license is retained in [licenses/temporal-agent-harness.txt](licenses/temporal-agent-harness.txt).
