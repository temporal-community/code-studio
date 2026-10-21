"""Editable feature agents execute real sandbox code and freeze the reviewed definition."""
import asyncio
import inspect
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from agent_dag.feature_builder import FLOW_TOOLS, feature_flow
from features.models import FeatureExecution
from features.project import PROJECT_FILES, validate_code
from features.runtime import FeatureService, TemporalFeatureRuntime
from features.storage import FeatureConflict
from workspace.agent_host import AgentHost
from workspace.server import create_app
from workspace.store import WorkspaceStore
from tests.test_features import FakeAgent, request, action, finish, plan_for
from tests.test_sdlc import NoNetworkGitHub
from tests.test_workspace import receive


def replace(store, path, value, ident='edit'):
    doc = store.file(path)
    return store.edit(ident, doc.replace(0, len(doc.text), value, ident), 'Bob', file=path)


class SandboxFeatureAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.model_calls = []

    async def execute(self, job, callback, event):
        from temporal_agent_harness.harness.code_mode.monty_stepper import ScriptRun
        spec = FeatureExecution(mode=job['mode'], brief=job['brief'], context=job['context'],
                                plan=job['approved_plan']['plan'] if job['approved_plan'] else None,
                                agent_project=job['agent_project'])
        sandbox = ScriptRun(spec.agent_project['files']['feature.py'], feature_flow().__code_mode_stubs__, answer_os=lambda name, args: None)
        step = sandbox.start()
        for _ in range(30):
            if step.done:
                assert not step.error, step.error
                return
            answers = {}
            for call in step.awaiting:
                signature = inspect.signature(FLOW_TOOLS[call.function_name])
                signature = signature.replace(parameters=[p for p in signature.parameters.values() if p.name not in ('runner', 'spec', 'budget')])
                args = dict(signature.bind(*call.args, **call.kwargs).arguments)
                if call.function_name == 'get_feature_phase':
                    answers[call.call_id] = spec.mode
                elif call.function_name == 'get_feature_request':
                    answers[call.call_id] = json.dumps({'brief': spec.brief, 'context': spec.context})
                elif call.function_name == 'plan_feature':
                    self.model_calls.append(args)
                    answers[call.call_id] = json.dumps(plan_for(job))
                elif call.function_name == 'implement_feature':
                    self.model_calls.append(args)
                    answers[call.call_id] = json.dumps({'summary': 'No files needed changing.', 'changed_files': [], 'suggested_checks': [], 'limitations': []})
                else:
                    answers[call.call_id] = json.dumps(await callback(str(call.call_id), call.function_name, args))
            step = sandbox.resume(answers)
        raise AssertionError('Feature flow did not finish')


async def test_shared_feature_code_executes_in_published_sandbox_and_changes_behavior(tmp_path):
    workspace = WorkspaceStore(tmp_path)
    runtime = SandboxFeatureAgent()
    service = FeatureService(workspace, tmp_path, lambda _: None, runtime=runtime)
    try:
        repo = service.git.initialize_demo()
        feature = await service.create(request(repo))
        script = PROJECT_FILES['feature.py'].replace('Keep the implementation focused and cover every requirement.', 'Prioritize backward compatibility and edge cases.')
        replace(workspace, 'feature.py', script)
        await service.launch(feature['id'], action('plan1'), 'plan')
        await finish(service)
        assert runtime.model_calls[0]['focus'] == 'Prioritize backward compatibility and edge cases.'
        assert service.store.get(feature['id'])['status'] == 'needs_review'
        assert json.loads(runtime.model_calls[0]['context'])['brief']['title'] == feature['title']
    finally:
        await service.stop()
        workspace.close()


async def test_approval_and_code_use_frozen_agent_while_next_plan_uses_edits(tmp_path):
    workspace = WorkspaceStore(tmp_path)
    service = FeatureService(workspace, tmp_path, lambda _: None, runtime=SandboxFeatureAgent())
    try:
        repo = service.git.initialize_demo()
        feature = await service.create(request(repo))
        before = workspace.feature_project_snapshot()
        await service.launch(feature['id'], {**action('plan1'), 'agent_version': before['version']}, 'plan')
        await finish(service)
        replace(workspace, 'coder.md', 'Write additional boundary tests.\n' + PROJECT_FILES['coder.md'])
        changed = workspace.feature_project_snapshot()
        assert changed['version'] != before['version']
        await service.mutate(feature['id'], 'approve', 'plan1', action('approve1'))
        await service.launch(feature['id'], action('code1'), 'code')
        await finish(service)
        old = service.store.get(feature['id'])
        assert old['jobs'][-1]['agent_project'] == before
        assert old['jobs'][0]['agent_project']['files']['coder.md'] == PROJECT_FILES['coder.md']
        await service.launch(feature['id'], action('plan2'), 'plan')
        await finish(service)
        assert service.store.get(feature['id'])['jobs'][-1]['agent_project'] == changed
        assert old['plans'][0]['agent_version'] == before['version']
        with pytest.raises(FeatureConflict, match='agent changed'):
            await service.launch(feature['id'], {**action('stale'), 'agent_version': before['version']}, 'plan')
    finally:
        await service.stop()
        workspace.close()


async def test_invalid_project_is_rejected_before_creating_a_task(tmp_path):
    store = WorkspaceStore(tmp_path)
    service = FeatureService(store, tmp_path, lambda _: None, runtime=FakeAgent())
    try:
        feature = await service.create(request(service.git.initialize_demo()))
        replace(store, 'feature.py', 'import subprocess\n')
        with pytest.raises(ValueError, match='registered tools'):
            await service.launch(feature['id'], action('bad'), 'plan')
        assert service.store.get(feature['id'])['jobs'] == []
        assert (await validate_code(PROJECT_FILES))['ok']
        with pytest.raises(ValueError):
            await validate_code({**PROJECT_FILES, 'feature.py': 'await missing_tool()'})
        config = json.loads(PROJECT_FILES['feature.json']); config['tools'] = ['feature_write_file']
        with pytest.raises(ValueError, match='read_file'):
            await validate_code({**PROJECT_FILES, 'feature.json': json.dumps(config)})
    finally:
        await service.stop()
        store.close()


async def test_custom_prompts_model_and_tools_reach_model_execution(monkeypatch):
    import agent_dag.feature_builder as module
    calls, events = [], []
    async def respond(agent, **kwargs):
        calls.append((agent, kwargs))
        return SimpleNamespace(final_output=module.FeaturePlan.model_validate(plan_for({'brief': {'inputs': []}})),
            context_wrapper=SimpleNamespace(usage=SimpleNamespace(input_tokens=5, output_tokens=6, total_tokens=11)))
    monkeypatch.setattr(module.Runner, 'run', respond)
    files = {**PROJECT_FILES, 'planner.md': 'Always explicitly discuss API compatibility.'}
    settings = json.loads(files['feature.json']); settings['model'] = 'gpt-6.1-sol'; settings['tools'] = []
    files['feature.json'] = json.dumps(settings)
    spec = FeatureExecution(mode='plan', brief={'inputs': []}, context={}, agent_project={'files': files})
    body = inspect.getclosurevars(module.plan_feature).nonlocals['user_fn']
    await body(SimpleNamespace(publish=events.append), spec, [0], 'repository context', 'Boundary cases')
    assert calls[0][0].model == 'gpt-6.1-sol'
    assert 'Always explicitly discuss API compatibility.' in calls[0][0].instructions
    assert calls[0][0].tools == []
    assert 'Boundary cases' in calls[0][1]['input']
    assert events[-1].usage.total_tokens == 11
    coder = inspect.getclosurevars(module.implement_feature).nonlocals['user_fn']
    with pytest.raises(ValueError, match='phase'):
        await coder(SimpleNamespace(publish=events.append), spec, [0], 'context', 'Bypass planning')
    spec.mode = 'code'
    async def code_response(agent, **kwargs):
        calls.append((agent, kwargs))
        return SimpleNamespace(final_output=module.ImplementationResult(summary='Inspected', changed_files=[], suggested_checks=[], limitations=['Read only']),
            context_wrapper=SimpleNamespace(usage=SimpleNamespace(input_tokens=5, output_tokens=6, total_tokens=11)))
    monkeypatch.setattr(module.Runner, 'run', code_response)
    await coder(SimpleNamespace(publish=events.append), spec, [0], 'context', 'Review only')
    assert calls[-1][0].tools == []
    assert files['coder.md'] in calls[-1][0].instructions


def test_two_sessions_edit_the_agent_and_see_same_version(tmp_path):
    app = create_app(tmp_path, with_agent=False, github=NoNetworkGitHub())
    with TestClient(app) as client:
        with client.websocket_connect('/workspace/connect?name=Alice&project=feature') as alice, client.websocket_connect('/workspace/connect?name=Bob&project=feature') as bob:
            first = receive(alice, 'snapshot'); second = receive(bob, 'snapshot')
            assert first['feature_builder_session_id'] == second['feature_builder_session_id']
            assert first['feature_builder_session_id'] != first['builder_session_id']
            original_review = app.state.store.project_snapshot()
            doc = app.state.store.file('planner.md')
            alice.send_json({'type': 'edit', 'id': 'alice1', 'file': 'planner.md', 'author': 'Alice',
                             'operation': doc.replace(len(doc.text), len(doc.text), '\nRequire a test for every edge case.\n', 'alice1')})
            assert receive(bob, 'operation')['file'] == 'planner.md'
            settings = json.loads(PROJECT_FILES['feature.json']); settings['model'] = 'gpt-6.1-sol'
            doc = app.state.store.file('feature.json')
            bob.send_json({'type': 'edit', 'id': 'bob1', 'file': 'feature.json', 'author': 'Bob',
                           'operation': doc.replace(0, len(doc.text), json.dumps(settings), 'bob1')})
            receive(alice, 'operation')
            receive(alice, 'operation')
            saved = client.get('/workspace/features/agent').json()
            assert saved['version'] != first['feature_agent_version']
            assert 'Require a test' in saved['files']['planner.md']
            assert json.loads(saved['files']['feature.json'])['model'] == 'gpt-6.1-sol'
            assert app.state.store.project_snapshot()['version'] == original_review['version']
            assert client.post('/workspace/features/agent/validate', json={}).json()['ok']
    reloaded = WorkspaceStore(tmp_path)
    assert reloaded.feature_project_snapshot()['version'] == saved['version']
    reloaded.close()


async def test_designer_callbacks_edit_only_feature_agent_and_validate(tmp_path):
    store = WorkspaceStore(tmp_path)
    done = asyncio.Event(); results = []
    calls = [SimpleNamespace(tool_id='list', tool_name='project_list_files', tool_input={}),
             SimpleNamespace(tool_id='bad', tool_name='project_read_file', tool_input={'path': 'agent.py'}),
             SimpleNamespace(tool_id='validate', tool_name='project_validate', tool_input={})]
    class Client:
        async def get_pending_callbacks(self):
            return calls
        async def provide_callback_result(self, tool_id, **kwargs):
            results.append((tool_id, kwargs))
            if tool_id == 'validate': done.set()
    host = AgentHost(store, lambda _: None, project='feature'); host.client = Client()
    task = asyncio.create_task(host.run())
    try:
        await asyncio.wait_for(done.wait(), 5)
        assert results[0][1]['result']['files'] == list(PROJECT_FILES)
        assert 'selected agent project' in results[1][1]['error']
        assert results[2][1]['result']['ok']
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        store.close()


async def test_runtime_delivers_frozen_project_and_json_callback_result(monkeypatch):
    import features.runtime as module
    done = asyncio.Event(); sent = []; acknowledged = []
    class Temporal:
        async def start_workflow(self, name, config, **options):
            assert name == 'ConfiguredFeatureAgent'
    class Client:
        def __init__(self, temporal, workflow_id): pass
        async def send_message(self, name, payload, *, on_item, update_id, timeout):
            sent.append(payload)
            async def stream():
                await done.wait()
                yield on_item(SimpleNamespace(event=None), 1)
            return stream()
        async def get_pending_callbacks(self):
            return [SimpleNamespace(tool_id='save', tool_name='save_feature_plan', tool_input={'plan_json': '{}'})]
        async def provide_callback_result(self, tool_id, *, result, update_id):
            acknowledged.append(result); done.set()
    monkeypatch.setattr(module, 'AgentClient', Client)
    runtime = TemporalFeatureRuntime(); runtime.temporal = Temporal()
    async def callback(*args): return {'saved': True}
    await runtime.execute({'id': 'plan', 'workflow_id': 'run1', 'mode': 'plan', 'brief': {}, 'context': {},
                           'approved_plan': None, 'agent_project': {'files': PROJECT_FILES}}, callback, lambda _: None)
    assert sent[0]['agent_project']['files'] == PROJECT_FILES
    assert json.loads(acknowledged[0]) == {'saved': True}


async def test_configurable_workflows_prepare_in_temporal_sandbox():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
    from agent_dag.feature_builder import ConfiguredFeatureAgentWorkflow
    from agent_dag.feature_designer import FeatureDesignerAgentWorkflow
    for cls in (ConfiguredFeatureAgentWorkflow, FeatureDesignerAgentWorkflow):
        SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(cls))


def test_agents_home_catalog_links_distinct_studios_and_versions(tmp_path):
    app = create_app(tmp_path, with_agent=False, github=NoNetworkGitHub())
    with TestClient(app) as client:
        state = client.get('/workspace/agents').json()
        assert [a['name'] for a in state['agents']] == ['Ideation', 'Planner', 'Design', 'Development', 'Review', 'Deployment']
        planner, review = state['agents'][1], state['agents'][4]
        assert planner['studio_url'] != review['studio_url']
        assert planner['files'] == ['planner_agent.py', 'planner_instructions.md', 'planner_settings.json']
        assert 'review_agent.py' in review['files']
        replace(app.state.store, 'planner_instructions.md', 'Include migration risks.')
        updated = client.get('/workspace/agents').json()
        assert updated['agents'][1]['version'] != planner['version']
        assert updated['agents'][4]['version'] == review['version']
        assert next(s for s in state['stages'] if s['name'] == 'Design')['agent'] == 'design'



async def test_saved_tool_configuration_is_enforced_by_local_host(tmp_path):
    store = WorkspaceStore(tmp_path)
    service = FeatureService(store, tmp_path, lambda _: None, runtime=FakeAgent())
    try:
        config = json.loads(PROJECT_FILES['feature.json'])
        config['tools'].remove('feature_write_file')
        replace(store, 'feature.json', json.dumps(config))
        feature = await service.create(request(service.git.initialize_demo()))
        await service.launch(feature['id'], action('plan1'), 'plan')
        await finish(service)
        await service.mutate(feature['id'], 'approve', 'plan1', action('approve1'))
        service.store.add_job(feature['id'], action('code1'), 'code', {}, agent_project=store.feature_project_snapshot())
        with pytest.raises(ValueError, match='disabled'):
            await service.callback(feature['id'], 'code1', 'write', 'feature_write_file', {'path': 'new.py', 'expected_hash': '', 'content': 'pass'})
        assert not (service.git.resolve_file(feature, 'new.py')).exists()
    finally:
        await service.stop()
        store.close()
