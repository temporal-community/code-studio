"""Model-free stage for exercising real coordinator, callbacks, state, and approvals."""
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    import json
    from temporal_agent_harness.harness import agent
    from temporal_agent_harness.harness.agent_protocol import TextReply
    from agent_dag.live_stage import LiveStageAgentWorkflow, observed_tool, stage_model_tools
    from agent_dag.stage_agent import save_stage_result
    from agent_dag.browser_tools import stage_browser
    from cooperative.agent_models import LiveStageExecution, StageState
    from cooperative.models import StageSettings
    from cooperative.definitions import files_for


@agent.defn(name='SdlcStageAgentV2')
class ScriptedStageWorkflow(LiveStageAgentWorkflow):
    @agent.accepts(model_callable=False)
    async def execute(self, message: LiveStageExecution) -> TextReply:
        """Exercise local stage tools without a model service."""
        self.progress.set(StageState(stage=message.stage, run_id=message.run_id,
            version=message.project['version'], status='running', paused=self.progress.current.paused,
            feedback=list(self.progress.current.feedback)))

        async def call(tool, **args):
            return await self.runner.run_tool(str(workflow.uuid4()), observed_tool(tool, self.runner, self.progress), **args)

        async def script(source):
            settings = StageSettings.model_validate_json(message.project['files'][files_for(message.stage)[2]])
            tool = stage_model_tools(settings, self.runner, self.progress, shared='shared_library' in message.context,
                optimized=bool(message.context.get('optimization_version')), patches=bool(message.context.get('source_patch_version')))[0]
            output = await self.runner.run_tool(str(workflow.uuid4()), tool, script=source)
            if output.startswith('Script error'):
                raise ValueError(output)

        if 'shared_library' in message.context:
            await script('''
files = await stage_list_shared_files()
skill = await stage_read_shared_file("/skills/project/SKILL.md")
assert "Project collaboration" in skill["content"]
for path in files["files"]:
    if path.startswith("/artifacts/") and path.endswith("/handoff.md"):
        handoff = await stage_read_shared_file(path)
        assert "Completed" in handoff["content"]
''')

        if message.stage == 'development':
            if message.context['feature']['description'] == 'wait for feedback':
                await workflow.wait_condition(lambda: bool(self.progress.current.feedback))
            if message.context.get('source_patch_version'):
                await script('''
page = await stage_read_range("app.py", 1, 20)
if "return 0" in page["content"]:
    patched = await stage_patch_file("app.py", page["hash"], "return 0", "return 42")
    assert patched["saved"]
''')
            else:
                await script('''import asyncio
async def main():
    files, old = await asyncio.gather(stage_list_files(), stage_read_file("app.py"))
    await stage_write_file("app.py", old["hash"], "def answer():\\n    return 42\\n")
asyncio.run(main())''')
        if message.stage == 'development' and message.context.get('optimization_version'):
            await script('''
hits = await stage_search_source("answer", "*.py", 10)
assert len(hits["matches"]) == 1
page = await stage_read_range("app.py", 1, 20)
assert "42" in page["content"]
check = await stage_run_check("syntax", ["app.py"], False)
assert check["scope"] == "focused"
assert check["status"] == "passed"
''')
        if message.stage == 'deployment':
            await script('await create_release_preview()')
        if message.context.get('browser_preview_version'):
            observation = await call(stage_browser, action='open', path='/', target='', width=375, height=812)
            assert observation['image'].startswith('data:image/jpeg;base64,')
        result = {'summary': 'Completed ' + message.stage, 'verdict': 'ready', 'deliverables': [],
            'acceptance_criteria': ['Returns 42'], 'questions': [], 'risks': [], 'findings': [],
            'changed_files': list(self.progress.current.changed_files), 'handoff': 'Continue the approved feature.'}
        await call(save_stage_result, result_json=json.dumps(result))
        with self.progress.mutate() as state:
            state.status = 'completed'
        return TextReply(text='Saved handoff')
