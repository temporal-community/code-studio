# Demo: Build your own SDLC with the Temporal Agent Harness

**Runtime:** approximately 13 minutes, including clicks and transitions.  
**Audience:** developers building agent-powered software delivery processes.  
**Main message:** Your application defines the roles, tools, handoffs, and decisions. The Temporal Agent Harness supplies durable agent execution underneath that process.

This script describes the current Code Studio implementation. Rehearse against your running version before recording. Quoted paragraphs are spoken narration; screen directions are not spoken.

## Prepare before recording

Use one small, rehearsed feature throughout. Example request:

> Add a Markdown export for a feature's brief and acceptance criteria. Include the feature title, handle missing optional content, and use a readable filename.

If this feature already exists, choose another small change with a visible result and clear acceptance criteria.

Prepare these checkpoints:

- A feature waiting for plan approval.
- A completed feature with actual check evidence and a useful Review → Development repair history.
- A completed baseline/variant playbook experiment with human acceptance assessments.
- A local release preview and merge preview in a disposable demo checkout.
- A rehearsed worker crash/restart sequence, with a backup recording.

Use prepared runs to avoid waiting for full model and check execution. Say when you switch to an earlier run. Do not present separate runs as one uninterrupted live execution or imply that a full feature completes in 13 minutes.

Open these views before recording:

- Code Studio: <http://127.0.0.1:5174>
- Harness UI: <http://127.0.0.1:8001/>
- Temporal UI: <http://localhost:8233/namespaces/code-studio/workflows>
- A dedicated worker terminal.
- The three source files used in the closing segment: `cooperative/definitions.py`, `agent_dag/pipeline_agent.py`, and `agent_dag/pipeline_tools.py`.

Use the independent service setup below for the recovery segment. Keep the Temporal server and workspace data intact. Confirm that the worker reports namespace `code-studio` and the expected task queue before recording.

## 13-minute script

### 0:00–0:45 — Open with the outcome

**Screen:** Open a completed feature. Point to the plan, changed files, check evidence, and local release preview.

> This is Code Studio, a development environment built around a team of agents. Here is a feature that has moved from an idea through planning, implementation, review, and release preparation.
>
> Each stage has an owner, a handoff, and evidence we can inspect. People decide when the plan and reviewed changes are ready to move forward.
>
> Over the next few minutes, I'll show how the Temporal Agent Harness supports this process, and where you can customize it for your own team: what agents do, which tools they can use, how they collaborate, and what happens when a worker disappears.

### 0:45–2:00 — Introduce the process and its agents

**Screen:** Open Agents. Show the six lifecycle agents, then the feature's stage strip and approval gates.

> This application uses six stages. Ideation clarifies the request. Planner turns it into implementation steps and acceptance criteria. Design specifies the solution. Development changes the code. Review examines the implementation and checks. Deployment prepares a local release package.
>
> A coordinator owns this sequence. It starts a child agent for each stage, collects the result, and determines what happens next. That includes waiting for a person or sending review feedback back to Development.
>
> The harness gives us the machinery for running and observing those agents. Code Studio defines this particular SDLC: the six roles, their order, and the rules for moving between them.
>
> Your team could implement different stages, approval points, or escalation rules. These are application decisions you express in code.

### 2:00–3:30 — Customize how your team works

**Screen:** Open Planner's Studio. Show `planner_instructions.md`, `planner_settings.json`, and `planner_agent.py`. Add the instruction below using the Studio editor, then validate the agent.

```text
For export features, explicitly cover empty content, formatting, filenames,
and sensitive information in the acceptance criteria.
```

> Each agent has three editable parts: instructions describing its job, settings selecting its model and tools, and a Python definition controlling its orchestration.
>
> Let's teach Planner something about our team's expectations. For export features, I want the plan to address empty content, formatting, filenames, and sensitive information.
>
> This is how a team's development practices become inputs to the process. We can also use the Studio assistant to help edit the definition, then validate it before running it.
>
> The settings control the model, enabled tools, and turn limit. The Python file controls how the stage reads context, invokes the model, and saves its handoff.
>
> Each stage captures its definition when it starts. Changing these files affects future stage starts; the running stage retains the version it began with. We can inspect that exact version afterward.

**Presenter note:** If the prepared Planner run predates this edit, explicitly identify it as the earlier version. Do not claim that the saved run used the new instruction.

### 3:30–4:45 — Start a feature and inspect the handoff

**Screen:** Show the example feature request and acceptance criteria. Explain the isolated source workspace. Start the feature if practical, then switch to the prepared plan. Open Skills & artifacts.

> We'll use a small feature: exporting a feature brief and acceptance criteria as Markdown. I can review and edit the request before starting work.
>
> Code Studio gives the feature an isolated source workspace. The agents can work on the change while the main project remains available.
>
> I'm switching to a prepared run of this feature so we can inspect the results without waiting for generation.
>
> Here is the Ideation result and the Planner handoff. In Skills & artifacts, we can see the frozen playbooks and versioned outputs used by the stages.
>
> Code Studio builds this artifact library around the harness execution. The next agent gets explicit context, and we retain the records needed to understand how it reached its result.

### 4:45–6:00 — Make human approval part of execution

**Screen:** Inspect a plan waiting for approval. Point to the approval action and relevant criteria, then choose Approve plan & continue.

> The coordinator is now waiting for a person to approve this plan. Before continuing, I can check the scope, implementation steps, and validation strategy.
>
> This uses a native harness tool approval. Code Studio adds the checks that bind the decision to the relevant scope and source revision.
>
> Once I approve, the coordinator can move to Design and then Development.
>
> We also distinguish feedback from a requirements change. Feedback can help the agent work within the current scope. A new requirement changes the scope, invalidates the old approvals, and requires planning again.
>
> That distinction lets a team collaborate while keeping an approval meaningful. The application owns the rules for what a decision authorizes.

### 6:00–7:30 — Show observable, bounded agent work

**Screen:** Show Development activity, a source read or patch, and an actual check result. Expand the harness preview to inspect the coordinator and its stage children. Use saved tool activity if the live stage has already finished.

> Now Development is working with its allowed tools. We can inspect the source it reads, the edits it makes, and the checks it runs.
>
> This stage uses Code Mode. The model can write a short Python script that combines available tools, including gathering independent reads together. Each underlying call still goes through the tool execution machinery and applicable permissions.
>
> In Code Studio, Development can write source. Review can inspect the implementation and run checks, but it does not get Development's source-write permission.
>
> The host also checks file hashes and records successful writes. A claim in the model's response is not enough to establish that a file changed.
>
> The harness view exposes the delegation and execution events. The feature view brings those together with the code diff, command output, exit status, and recorded usage. That gives us something concrete to investigate when a stage fails or produces an unexpected result.

### 7:30–8:30 — Crash and restart only the worker

**Screen:** Switch to a prepared feature waiting at an approval gate. Show its coordinator workflow ID. Kill the independently launched agent worker using the instructions below. Keep Studio and Temporal UI visible. Restart with `just worker`, then approve and show progress on the same coordinator.

> This feature is waiting for approval. Notice its coordinator workflow ID.
>
> I'll stop the agent worker while keeping Temporal, the workspace host, and the UI running.
>
> The worker process is gone, but the workflow and its pending decision remain. The UI can still show the saved state.
>
> Now I'll start a replacement worker. It reconnects to the same Temporal namespace and task queue. We can approve the waiting step and continue this execution.
>
> The coordinator workflow ID is unchanged. We have demonstrated recovery without creating a replacement feature or starting its completed stages over.

**Presenter notes:**

- If using `kill -9`, call this a crash. If using Ctrl-C, call it a worker stop/restart.
- Waiting at an approval gate makes the result easy to explain and rehearse.
- Recovery during an in-flight operation can involve timeouts and retries. Do not promise immediate recovery or that every external action executes exactly once.
- A browser refresh demonstrates reconnection, not worker recovery.
- Use a recorded clip if reconnecting takes longer than this segment allows.

### 8:30–10:00 — Close the development–review loop

**Screen:** Switch to a prepared run with an actual Review finding. Show the finding, the Development repair, and updated checks. Briefly show browser preview evidence if relevant.

> I'll switch to an earlier run where Review found a problem. Here is the finding and the evidence behind it.
>
> The coordinator sent that feedback to Development. We can follow the repair through the changed files and the subsequent check results.
>
> Code Studio allows up to two automatic repair passes. After that, unresolved blockers require intervention. The limit and transition rules are explicit application policy.
>
> Checks are also evidence with a specific source and environment behind them. Matching results can be reused; changed inputs invalidate them. A focused check does not replace required full validation.
>
> For supported frontend previews, we can also inspect retained screenshots and navigation evidence. That helps with visual review, while remaining distinct from a complete acceptance test.
>
> We decide how much autonomy to give the agents, what evidence is required, and where the process stops for a person.

### 10:00–11:00 — Improve the process through experiments

**Screen:** Open a completed pair in Experiments. Compare the baseline playbook with a variant that requires explicit edge-case coverage. Show findings, checks, repair rounds, recorded usage, and human assessments.

> Once we can define our process, we can experiment with it. This pair compares our baseline playbook with a variant that asks for more explicit edge-case coverage.
>
> Both attempts capture the same starting source and feature inputs, with the same agent definitions. Each gets an isolated workspace and its own approvals.
>
> We can compare the resulting changes, review findings, checks, repair rounds, and recorded token usage. Acceptance outcomes are human assessments.
>
> Code Studio builds this experiment workflow on the same agent execution machinery. It does not automatically pick a winner. We use the evidence across several tasks to decide whether a new rule belongs in our team's process.

### 11:00–12:00 — Finish with a controlled release

**Screen:** Show the review approval and a prepared release result. Open the source ZIP details, deployment/rollback notes, and merge preview. Point to Undo merge on an already merged disposable example if needed.

> After review approval, Deployment prepares a local source package with check evidence and deployment and rollback notes.
>
> In this application, Deployment means local release preparation. It does not publish to a remote environment.
>
> We can inspect a merge preview before applying the feature to the project. Code Studio checks the integration candidate and retains a backup and receipt for undoing the merge while preserving later edits.
>
> A team could implement tools for its own release system and place approval gates around those actions. The harness provides the execution and approval mechanisms; the application defines the release policy and implements the integration.

**Presenter note:** Do not apply changes to the running demo's backend during recording. Use a prepared result or disposable checkout for the merge demonstration.

### 12:00–13:00 — Show the customization points

**Screen:** Briefly open the three source files below. Keep each view focused on a small relevant block.

| File | Point to |
| --- | --- |
| `cooperative/definitions.py` | Stage roles and allowed tools. |
| `agent_dag/pipeline_agent.py` | Stage transitions, child agents, approval waits, and repair limits. |
| `agent_dag/pipeline_tools.py` | Host tool contracts, including the approval boundary. |

> Here are the main places you would adapt this example. The definitions specify the roles and tools. The coordinator defines the process and its repair policy. The host tool contracts connect execution to the application and its human decisions.
>
> Instructions and settings are editable in Studio. Changing the pipeline's structure means changing application code.
>
> We have seen customization, agent handoffs, human approval, observable tool execution, worker recovery, and a controlled path to release preparation.
>
> Start with your team's roles, tools, handoffs, and decisions. The Temporal Agent Harness provides the durable agent execution underneath; your application defines how software gets delivered.

## Code walkthrough: exact workflows and tools to show

Keep the main code tour to about 90 seconds, distributed between the tool-execution segment and the closing minute. Open these files in advance and show only the relevant methods.

| Order | File and symbol | What to highlight | Suggested narration |
| --- | --- | --- | --- |
| 1 | [`pipeline_agent.py`](agent_dag/pipeline_agent.py): `FeaturePipelineAgentWorkflow.execute`, `next_transition` | `start_subagent`, `run_subagent_turn`, `approve_stage`, and the two-pass repair limit. | “This is our SDLC policy: delegate a stage, inspect its result, then advance, wait for approval, or request a repair.” |
| 2 | [`live_stage.py`](agent_dag/live_stage.py): `LiveStageAgentWorkflow.execute`, `stage_model_tools` | `SdlcStageAgentV2`, the frozen Python definition, and `agent.code_mode_tool`. | “All six stages share this execution workflow. Their frozen instructions, settings, and allowed tools specialize each run.” |
| 3 | [`pipeline_tools.py`](agent_dag/pipeline_tools.py): `approve_stage` | `@agent.callback_tool_defn(inherently_safe=False)` and arguments binding approval to a run, scope revision, and source hash. | “This tool requires a human decision, and the host revalidates what that decision authorizes.” |
| 4 | [`optimized_tools.py`](agent_dag/optimized_tools.py): `stage_patch_file`, `stage_run_check` | The expected file hash, precise replacement, focused check targets, and returned validation evidence. | “Edits use conflict checks, and validation returns actual command evidence.” |

For a short Code Mode example, show the stage's editable Python file in Studio: `get_stage_context()` → `run_stage(...)` → `save_stage_result(...)`. Then point to `stage_model_tools` to explain that the model can also use Code Mode to compose its permitted source and check tools.

The decorated callback declarations intentionally have `...` bodies: they describe the tool contract. The local workspace host performs the operation. If someone asks where permissions and writes are enforced, open [`cooperative/runtime.py`](cooperative/runtime.py), `PipelineService.callback`, particularly its allowed-tool checks and source-write branch. Approval handling is in [`cooperative/pipeline_host.py`](cooperative/pipeline_host.py).

Useful optional examples, if the conversation calls for them:

- **Customizing an agent with an agent:** [`studio_designer.py`](agent_dag/studio_designer.py), `StudioDesignerAgentWorkflow.generate`. Show `project_read_file`, `project_edit_file`, and `project_validate` being adapted for the OpenAI Agents SDK. This is the assistant behind Agent Studio, separate from the feature-stage execution.
- **Explicit shared context:** [`stage_tools.py`](agent_dag/stage_tools.py), `stage_list_shared_files` and `stage_read_shared_file`, for frozen skills and upstream artifacts.
- **Role permissions:** [`cooperative/definitions.py`](cooperative/definitions.py), `DEFINITIONS`, contrasting Development's source-write access with Review's read/check tools.

Use `pipeline_agent.py` and `live_stage.py` for the current feature pipeline. Older workflow files remain for earlier demos and compatibility; introducing them during the main walkthrough makes the current execution path harder to follow.

## Run services independently for the worker demo

### Why not kill the worker under `just dev`?

`just dev` runs `scripts/dev.py`, which supervises the whole stack. Ctrl-C or a handled termination signal stops every child process it started. A Temporal server that was already running and merely reused is left alone.

The supervisor also checks whether any child has exited. Killing its agent worker triggers an error and cleanup of the remaining children. Therefore, killing only the worker process under `just dev` will still bring down the rest of that supervised stack.

For this recording, launch the services independently.

### Initial setup

1. Stop the current `just dev` with Ctrl-C and wait for cleanup.
2. From the repository root, confirm `.env.local` contains your existing model credentials and these connection settings:

   ```dotenv
   TEMPORAL_CONFIG_FILE=temporal.local.toml
   TEMPORAL_PROFILE=default
   ```

   The checked-in profile points to `localhost:7233`, namespace `code-studio`. Avoid conflicting `TEMPORAL_ADDRESS` or `TEMPORAL_NAMESPACE` overrides. Each `just` recipe loads `.env.local`.

3. Run each command below in its own terminal, from the repository root. Keep the terminals running.

| Terminal | Command | Purpose |
| --- | --- | --- |
| 1 | `just temporal` | Local Temporal server and UI, using `.local/temporal.db`. |
| 2 | `just session-manager` | Harness session management. |
| 3 | `just server` | Harness API and debugger UI. |
| 4 | `just workspace` | Workspace host and agent callback execution. |
| 5 | `just studio` | Code Studio frontend. |
| 6 | `just worker` | Agent workflows and worker-side activities. |

Skip `just temporal` if the intended local Temporal server is already running. Confirm it has the `code-studio` namespace; the independent recipes do not perform the all-in-one supervisor's namespace initialization. Start Temporal before the services that connect to it.

For an existing server missing the namespace, create it once:

```sh
temporal operator namespace create --namespace code-studio --address localhost:7233
```

Wait for the worker's ready message. It should identify the expected namespace and task queue. Open Studio and confirm that the prepared feature and history are available.

### Option A: Stop and restart the worker

In terminal 6, press Ctrl-C. Leave every other terminal running.

Restart in that same terminal:

```sh
just worker
```

The session-manager is a separate process; leave it running. Stopping `agent_dag.worker` stops this application's agent worker without shutting down the UI, workspace host, or Temporal server.

### Option B: Simulate an abrupt worker crash

From another terminal, list candidate processes:

```sh
pgrep -fl 'python.*-m agent_dag[.]worker'
```

Identify the actual Python process for this checkout. The list may also include a `uv run` wrapper or workers from another checkout; do not kill every match.

Replace `12345` below with the verified Python worker PID:

```sh
kill -9 12345
```

In the dedicated worker terminal, restart:

```sh
just worker
```

Use this sequence only with independently launched services. Do not kill the Temporal server, session-manager, workspace host, or `just dev` supervisor for the worker-only demonstration.

### Recovery verification and recording cues

1. Before stopping the worker, capture the feature's coordinator workflow ID and waiting approval.
2. Stop or crash the agent worker.
3. Show that Studio and Temporal UI remain available and the workflow is still present.
4. Restart the worker with the same connection settings and task queue.
5. Wait for the ready message, approve the waiting step, and show subsequent progress.
6. Compare the coordinator ID before and after. A new stage child workflow is expected; the feature coordinator should remain the same execution.

For this demonstration, describe exactly what the screen establishes: recovery of a feature waiting for approval. In-flight host callbacks may finish while the worker is unavailable, and interrupted model work may require retries. Keep the feature within its execution deadlines, and do not delete `.local` or reset Temporal state between stopping and restarting.

If progress does not resume, check the worker's namespace/task queue, its terminal output, the workflow's status in Temporal UI, and whether the other services are still running. Independently launched recipes print to their terminals rather than the supervisor's `.local/logs/` files.

## Accuracy notes

- The six-stage SDLC, frozen definitions, shared artifact library, experiments, source checks, merge handling, and release packaging are Code Studio application features built around the harness.
- The harness supplies agent execution primitives used here, including child-agent orchestration, Code Mode, tool approvals, and execution observability, on Temporal's durable workflow foundation.
- The shared library is not persistent cross-task agent memory.
- Browser preview evidence covers supported read-only visual/navigation operations; it is not proof of complete interactive acceptance coverage.
- Local collaboration identities are display names, not authenticated remote accounts.
- Missing check evidence or unrecorded usage must remain visibly missing. A manual approval does not turn failed checks into passed checks.

## References

- [Project README](README.md)
- [Service recipes](justfile)
- [Development supervisor](scripts/dev.py)
- [Agent worker](agent_dag/worker.py)
- [Pipeline coordinator](agent_dag/pipeline_agent.py)
- [Pipeline tools and approval contract](agent_dag/pipeline_tools.py)
- [Recovery and approval integration tests](tests/test_pipeline_harness.py)
- [Temporal Agent Harness](https://github.com/temporal-community/temporal-agent-harness)
