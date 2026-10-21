"""One persistent Python collaborator; file I/O is fulfilled by the local workspace host."""

from temporalio import workflow
from temporalio.contrib.workflow_streams import WorkflowStream

with workflow.unsafe.imports_passed_through():
    from agents import Agent, ModelSettings, Runner
    from temporal_agent_harness.ai_sdks.openai_agents_harness import as_openai_agent_tool
    from temporal_agent_harness.harness import agent
    from temporal_agent_harness.harness.agent_protocol import (
        AgentConfig, MidTurn, TextMessage, TextReply, ToolApprovalPolicy,
    )
    from temporal_agent_harness.harness.agent_workflow import AgentWorkflowRunner


@agent.callback_tool_defn(inherently_safe=True)
async def workspace_read_file() -> dict:
    """Read the shared app.py. Returns its content and current revision. Always read before editing."""
    ...


@agent.callback_tool_defn(inherently_safe=True)
async def workspace_edit_file(base_revision: int, old_text: str, new_text: str, summary: str) -> dict:
    """Replace one exact, unique old_text in app.py at base_revision. Empty old_text is allowed
    only if the file is empty. Returns applied, revision, message. On a revision conflict,
    reread the file and adapt the patch to preserve the humans' changes. summary describes
    the change to collaborators. This writes a file; it does not run Python."""
    ...


INSTRUCTIONS = '''You are the Python agent in a shared coding workspace with two human collaborators.
Help them implement their requests in app.py. The file is real Python source, not an orchestration script.
Your only tools read and edit that shared file through callbacks executed on their local workspace host.
Always read the file before modifying it. Use small exact replacements. Preserve changes, comments, and
style introduced by people. A person can edit while you are thinking: if applied is false, reread and
adapt to the latest content. Use the returned revision for subsequent edits. Never claim an edit
succeeded unless applied was true. If the project keeps changing, explain and wait for the next message.
Write complete, readable, typed Python using the standard library. You cannot run code, tests, or type
checks in this milestone, so do not claim to have validated execution. Do not produce travel-booking
examples unless the user explicitly asks. After edits, give a short summary. Chat messages identify the
human author. Follow the latest request while respecting the shared project and previous decisions.'''


@agent.defn(name='WorkspacePythonAgent')
class WorkspacePythonAgentWorkflow:
    @agent.init
    def __init__(self, config: AgentConfig) -> None:
        self._runner = AgentWorkflowRunner(
            config, stream=WorkflowStream(),
            approval_policy_default=ToolApprovalPolicy.allow_inherently_safe(),
        )
        self._conversation = []

    @agent.accepts(mid_turn=MidTurn.ENQUEUE)
    async def generate(self, message: TextMessage) -> TextReply:
        """Read and edit the shared Python file to fulfill a collaborator's request."""
        result = Runner.run_streamed(
            Agent(name='Python agent', model='gpt-6-luna', instructions=INSTRUCTIONS,
                  model_settings=ModelSettings(parallel_tool_calls=False),
                  tools=[as_openai_agent_tool(self._runner, tool)
                         for tool in (workspace_read_file, workspace_edit_file)]),
            input=[*self._conversation, {'role': 'user', 'content': message.text}],
            context=self._runner, max_turns=16,
        )
        async for _event in result.stream_events():
            pass
        self._conversation = result.to_input_list()
        return TextReply(text=str(result.final_output))
