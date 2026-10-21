"""Shared builder agent with version-checked callbacks for the SDLC project files."""
from temporalio import workflow
from temporalio.contrib.workflow_streams import WorkflowStream

with workflow.unsafe.imports_passed_through():
    from agents import Agent, ModelSettings, Runner
    from temporal_agent_harness.ai_sdks.openai_agents_harness import as_openai_agent_tool
    from temporal_agent_harness.harness import agent
    from temporal_agent_harness.harness.agent_protocol import AgentConfig, MidTurn, TextMessage, TextReply, ToolApprovalPolicy
    from temporal_agent_harness.harness.agent_workflow import AgentWorkflowRunner
    from .pr_review import review_flow


@agent.callback_tool_defn(inherently_safe=True)
async def project_list_files() -> dict:
    """List editable shared agent project files and the current project revision."""
    ...


@agent.callback_tool_defn(inherently_safe=True)
async def project_read_file(path: str) -> dict:
    """Read a shared project file and its current project revision before editing it."""
    ...


@agent.callback_tool_defn(inherently_safe=True)
async def project_edit_file(path: str, base_revision: int, old_text: str, new_text: str, summary: str) -> dict:
    """Replace one exact unique string at the read revision. A conflict requires rereading.
    Empty old_text is allowed only for an empty file. Preserve other collaborators' work."""
    ...


@agent.callback_tool_defn(inherently_safe=True)
async def project_validate() -> dict:
    """Validate the shared agent's JSON settings and type-check its Python Code Mode script."""
    ...


INSTRUCTIONS = '''You help two people collaboratively build an SDLC pull-request review agent.
The project has agent.py (the executable Python Code Mode flow), instructions.md (the review
model's instructions), agent.json (model, name, allowed tools), and evaluations.json (fixture
expectations). app.py is retained scratch code from the previous demo; do not replace it.
Read before editing and apply small, exact, version-checked patches. If another person edited,
reread and preserve their changes. Use project_validate after changes. Report validation honestly.
The user starts review runs explicitly in the Run panel; you build the agent and do not start runs.
Do not add filesystem, network, or package imports to agent.py. It uses import asyncio and only
the registered host functions below. Tool results are JSON strings. The saved project code really
executes in Code Mode, so do not turn it into pseudocode or a configuration-only placeholder.
Keep review findings separate from actual CI/test evidence. Local review reports are not posted
to GitHub. Explain the changes briefly after editing; both collaborators see the same chat.
\nCODE MODE CONTRACT:\n'''


@agent.defn(name='SdlcBuilderAgent')
class SdlcBuilderAgentWorkflow:
    @agent.init
    def __init__(self, config: AgentConfig) -> None:
        self._runner = AgentWorkflowRunner(config, stream=WorkflowStream(),
                                           approval_policy_default=ToolApprovalPolicy.allow_inherently_safe())
        self._instructions = INSTRUCTIONS + (review_flow().__doc__ or '')
        self._conversation = []

    @agent.accepts(mid_turn=MidTurn.ENQUEUE)
    async def generate(self, message: TextMessage) -> TextReply:
        """Build or revise the shared SDLC review agent, preserving both collaborators' edits."""
        result = Runner.run_streamed(
            Agent(name='SDLC agent builder', model='gpt-6-luna', instructions=self._instructions,
                  model_settings=ModelSettings(parallel_tool_calls=False),
                  tools=[as_openai_agent_tool(self._runner, t) for t in
                         (project_list_files, project_read_file, project_edit_file, project_validate)]),
            input=[*self._conversation, {'role': 'user', 'content': message.text}],
            context=self._runner, max_turns=24,
        )
        async for _ in result.stream_events():
            pass
        self._conversation = result.to_input_list()
        return TextReply(text=str(result.final_output))
