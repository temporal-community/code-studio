"""The shared studio assistant edits the feature builder, not the target codebase."""
from temporalio import workflow
from temporalio.contrib.workflow_streams import WorkflowStream

with workflow.unsafe.imports_passed_through():
    from agents import Agent, ModelSettings, Runner
    from temporal_agent_harness.ai_sdks.openai_agents_harness import as_openai_agent_tool
    from temporal_agent_harness.harness import agent
    from temporal_agent_harness.harness.agent_protocol import AgentConfig, MidTurn, TextMessage, TextReply, ToolApprovalPolicy
    from temporal_agent_harness.harness.agent_workflow import AgentWorkflowRunner
    from .sdlc_builder import project_list_files, project_read_file, project_edit_file, project_validate
    from .feature_builder import feature_flow


INSTRUCTIONS = '''You help teammates collaboratively customize their feature-builder agent in Agent Studio.
Edit the agent project, not the target repository. The four editable files are:
- feature.py: executable Code Mode orchestration for the planning and coding phases;
- planner.md: instructions for the model that creates the shared implementation plan;
- coder.md: instructions for the model that implements the approved plan;
- feature.json: name, model (gpt-6-luna or gpt-6.1-sol), tools, and max_turns (2..40).
The configurable file tools are feature_list_files, feature_read_file, feature_write_file.
Read before editing. Apply small, exact, version-checked patches. On a conflict, reread and
preserve others' edits. After changes call project_validate and report its actual result.
The team uses Run on codebase to start a frozen version of these four files. Planning and its
approved coding task use the same agent version; new edits affect future planning runs only.
Feature requests, plan approval, branch creation, version checks, and local writes are host
capabilities. You cannot bypass approval, start a run, or write target code through studio tools.
feature.py runs in the published harness Code Mode sandbox: import asyncio is allowed, but no
filesystem/network/package imports. Tools return strings; JSON-returning tools return JSON
strings. Keep the code executable and use the tool contract below. Do not edit PR reviewer files.
\nFEATURE FLOW TOOL CONTRACT:\n'''


@agent.defn(name='FeatureDesignerAgent')
class FeatureDesignerAgentWorkflow:
    @agent.init
    def __init__(self, config: AgentConfig):
        self.runner = AgentWorkflowRunner(config, stream=WorkflowStream(),
            approval_policy_default=ToolApprovalPolicy.allow_inherently_safe())
        self.conversation = []

    @agent.accepts(mid_turn=MidTurn.ENQUEUE)
    async def generate(self, message: TextMessage) -> TextReply:
        """Customize the shared feature-builder code, prompts, and settings with teammates."""
        result = await Runner.run(Agent(name='Feature agent designer', model='gpt-6-luna',
            instructions=INSTRUCTIONS + (feature_flow().__doc__ or ''),
            model_settings=ModelSettings(parallel_tool_calls=False),
            tools=[as_openai_agent_tool(self.runner, t) for t in
                   (project_list_files, project_read_file, project_edit_file, project_validate)]),
            input=[*self.conversation, {'role': 'user', 'content': message.text}], context=self.runner, max_turns=24)
        self.conversation = result.to_input_list()
        return TextReply(text=str(result.final_output))
