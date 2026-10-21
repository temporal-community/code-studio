"""A separate shared designer conversation for every agent in the catalog."""
from temporalio import workflow
from temporalio.contrib.workflow_streams import WorkflowStream

with workflow.unsafe.imports_passed_through():
    from agents import Agent, ModelSettings, Runner
    from temporal_agent_harness.ai_sdks.openai_agents_harness import as_openai_agent_tool
    from temporal_agent_harness.harness import agent
    from temporal_agent_harness.harness.agent_protocol import AgentConfig, MidTurn, TextReply, ToolApprovalPolicy
    from temporal_agent_harness.harness.agent_workflow import AgentWorkflowRunner
    from cooperative.models import StudioMessage
    from cooperative.definitions import DEFINITIONS, files_for
    from .sdlc_builder import project_list_files, project_read_file, project_edit_file, project_validate
    from .stage_agent import stage_flow


MODEL = 'gpt-6-luna'
MAX_TURNS = 24


def designer_instructions(agent_id):
    definition = DEFINITIONS[agent_id]
    return f'''You help teammates customize their {definition['name']} agent in Agent Studio.
Edit only its shared definition files: {', '.join(files_for(agent_id))}.
The Python file is real executable Code Mode orchestration; the Markdown file is the agent's
system prompt; JSON selects name, model (gpt-6-luna or gpt-6.1-sol), tools, max_turns (2..40),
and tool_mode (code or individual). New feature pipelines default to model-facing Code Mode:
the model can write Python scripts over only the saved stage tools, with concurrent independent
reads/checks and sequential writes. The editable outer Python flow still defines the stage handoff.
Allowed tools for this stage: {', '.join(definition['tools'])}.
Read before editing, make small version-checked patches, preserve concurrent edits, and validate
after changes. If a patch conflicts, reread before retrying. Explain your changes briefly.
You edit the agent, not the feature's target files. You cannot start pipeline runs or bypass
approvals. An active run uses a saved version; changes apply when a stage is next started or
explicitly rerun. Only import asyncio and registered functions are allowed in the Python flow.
Tool results are strings; context and artifact tools return JSON strings.
\nFLOW CONTRACT:\n''' + (stage_flow().__doc__ or '')


@agent.defn(name='StudioDesignerAgent')
class StudioDesignerAgentWorkflow:
    @agent.init
    def __init__(self, config: AgentConfig):
        self.runner = AgentWorkflowRunner(config, stream=WorkflowStream(),
            approval_policy_default=ToolApprovalPolicy.allow_inherently_safe())
        self.conversation = []

    @agent.accepts(mid_turn=MidTurn.ENQUEUE)
    async def generate(self, message: StudioMessage) -> TextReply:
        """Collaboratively customize the selected SDLC agent's code, prompt, and settings."""
        definition = DEFINITIONS[message.agent_id]
        instructions = message.instructions if message.instructions is not None else designer_instructions(message.agent_id)
        result = await Runner.run(Agent(name=definition['name'] + ' designer', model=MODEL,
            instructions=instructions, model_settings=ModelSettings(parallel_tool_calls=False),
            tools=[as_openai_agent_tool(self.runner, t) for t in
                   (project_list_files, project_read_file, project_edit_file, project_validate)]),
            input=[*self.conversation, {'role': 'user', 'content': message.text}], context=self.runner, max_turns=MAX_TURNS)
        self.conversation = result.to_input_list()
        return TextReply(text=str(result.final_output))
