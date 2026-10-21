"""Real stage scripts, shared approvals, local check evidence, and reversible source previews."""
import asyncio
import inspect
import json
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from cooperative.definitions import STAGES, PROJECT_FILES, files_for
from cooperative.models import validate_project
from cooperative.runtime import PipelineService
from cooperative.source import SourceWorkspace
from cooperative.release import apply_to_source
from features.runtime import FeatureService
from features.storage import FeatureConflict
from workspace.store import WorkspaceStore
from workspace.server import create_app
from tests.test_feature_project import replace
from tests.test_sdlc import NoNetworkGitHub


def result(**changes):
    return dict(summary='A focused change.', verdict='ready', deliverables=[{'title': 'Handoff', 'body': 'Continue the approved feature.'}],
                acceptance_criteria=['Tests pass'], questions=[], risks=[], findings=[], changed_files=[], handoff='Next stage', **changes)


class Agent:
    def __init__(self):
        self.calls = []; self.cancelled = []; self.repair = False
    async def execute(self, run, callback, event):
        self.calls.append(run['stage']); changed = []
        if run['stage'] == 'development':
            old = await callback('read', 'stage_read_file', {'path': 'app.py'})
            content = 'def answer():\n    return 42\n' if not self.repair or self.calls.count('development') > 1 else 'def answer(:\n'
            await callback('write', 'stage_write_file', {'path': 'app.py', 'expected_hash': old['hash'], 'content': content})
            changed = ['app.py']
        if run['stage'] == 'deployment': await callback('zip', 'create_release_preview', {})
        value = result(); value['changed_files'] = changed
        await callback('saved', 'save_stage_result', {'result_json': json.dumps(value)})
    async def close_run(self, ident): pass
    async def cancel_run(self, ident): self.cancelled.append(ident)


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'source'; root.mkdir()
    (root / 'app.py').write_text('def answer():\n    return 0\n')
    (root / '.env.local').write_text('NOT_FOR_AGENTS=secret')
    (root / 'node_modules').mkdir(); (root / 'node_modules/skip.js').write_text('ignored')
    store = WorkspaceStore(tmp_path / 'state')
    events = []; agent = Agent()
    features = FeatureService(store, tmp_path / 'state', events.append, enabled=False)
    pipeline = PipelineService(features, events.append, runtime=agent)
    yield pipeline, root, agent, events
    store.close()


def request(root, ident='feature1'):
    return {'id': ident, 'title': 'Answer the question', 'description': 'Return 42 from answer.', 'acceptance': 'Answer is 42.',
            'repository': str(root), 'branch': 'feature/answer', 'base': 'HEAD', 'source_mode': 'current', 'author': 'Alice'}


def action(ident='action', stage=None, revision=1):
    return {'id': ident, 'author': 'Bob', 'revision': revision, 'stage': stage}


async def settle(p):
    for _ in range(12):
        tasks = list(p.tasks.values())
        if not tasks: return
        await asyncio.wait_for(asyncio.gather(*tasks), 20)
        await asyncio.sleep(0)
    raise AssertionError('Pipeline did not settle')


async def reviewed(p, root):
    f = await p.create(request(root)); await settle(p)
    assert p.get(f['id'])['pipeline']['status'] == 'needs_plan_approval'
    await p.control(f['id'], 'approve', action('plan')); await settle(p)
    f = p.get(f['id'])
    assert f['pipeline']['status'] == 'needs_release_approval', f.get('error')
    return f


async def test_full_pipeline_checks_two_approvals_release_and_explicit_apply(setup):
    p, root, agent, events = setup
    f = await reviewed(p, root)
    assert agent.calls == list(STAGES[:5])
    assert f['pipeline']['checks']['syntax']['status'] == 'passed'
    assert f['pipeline']['checks']['types']['status'] == 'not_available'
    assert 'return 0' in (root / 'app.py').read_text()
    assert '.env.local' not in p.git.files(f)
    assert 'node_modules/skip.js' not in p.git.files(f)
    await p.control(f['id'], 'approve', action('release')); await settle(p)
    done = p.get(f['id']); assert done['pipeline']['status'] == 'complete'
    assert agent.calls == list(STAGES)
    with zipfile.ZipFile(done['pipeline']['release']['path']) as archive:
        assert 'return 42' in archive.read('source/app.py').decode()
        assert not json.loads(archive.read('release-manifest.json'))['remote_deployment']
    await p.integration(f['id'], action('integration-preview'), 'prepare')
    await p.integration(f['id'], action('integration-validation'), 'validate')
    applied = await p.apply(f['id'], action('apply'))
    assert applied['applied'] == ['app.py']; assert 'return 42' in (root / 'app.py').read_text()
    assert (await p.apply(f['id'], action('apply'))) == applied
    assert all(e['type'] == 'feature-changed' for e in events)


async def test_scope_resets_approvals_comments_do_not_and_run_definitions_are_frozen(setup):
    p, root, agent, _ = setup
    f = await reviewed(p, root); ident = f['id']
    frozen = f['pipeline']['runs'][1]['project']
    replace(p.workspace, 'planner_instructions.md', 'A new planning style')
    assert p.get(ident)['pipeline']['runs'][1]['project'] == frozen
    comment = {'id': 'comment', 'kind': 'comment', 'author': 'Bob', 'text': 'Reading the results.'}
    await p.contribute(ident, comment); await p.contribute(ident, comment)
    assert p.get(ident)['input_revision'] == 1
    assert len(p.get(ident)['inputs']) == 1
    await p.contribute(ident, {**comment, 'id': 'scope', 'kind': 'requirement', 'text': 'Also support a default argument.'})
    latest = p.get(ident)
    assert latest['pipeline']['status'] == 'scope_changed'; assert latest['pipeline']['approvals'] == {}
    assert latest['input_revision'] == 2
    with pytest.raises(FeatureConflict): await p.control(ident, 'approve', action('stale'))
    with pytest.raises(FeatureConflict): await p.control(ident, 'rerun', action('skip', 'development', 2))
    assert p.get(ident)['pipeline']['current_stage'] == 'ideation'
    await p.control(ident, 'resume', action('resume', 'ideation', 2)); await settle(p)
    assert p.get(ident)['pipeline']['runs'][-1]['project']['version'] != frozen['version']
    assert all(i['kind'] != 'comment' for i in p.get(ident)['pipeline']['runs'][-1]['context']['feature']['inputs'])


async def test_failing_checks_route_review_back_to_development(setup):
    p, root, agent, _ = setup; agent.repair = True
    f = await reviewed(p, root)
    assert agent.calls.count('development') == agent.calls.count('review') == 2
    assert f['pipeline']['repairs'] == 1
    assert f['pipeline']['checks']['syntax']['status'] == 'passed'


async def test_review_can_report_upstream_changes_without_claiming_its_own_writes(setup):
    p, root, agent, _ = setup
    execute = agent.execute

    async def review_with_upstream_paths(run, callback, event):
        async def report(call_id, name, args):
            if run['stage'] == 'review' and name == 'save_stage_result':
                artifact = json.loads(args['result_json'])
                artifact['changed_files'] = run['context']['upstream']['development']['changed_files']
                args = {'result_json': json.dumps(artifact)}
            return await callback(call_id, name, args)
        await execute(run, report, event)

    agent.execute = review_with_upstream_paths
    f = await reviewed(p, root)
    review = f['pipeline']['runs'][-1]
    assert review['stage'] == 'review'
    assert review['result']['changed_files'] == []
    assert review['reported_changed_files'] == ['app.py']
    assert any(item['name'] == 'reconcile_changed_files' for item in review['trace'])
    assert f['pipeline']['checks']['syntax']['status'] == 'passed'
    assert 'review' not in f['pipeline']['approvals']
    assert 'deployment' not in agent.calls


async def test_stage_changed_files_come_only_from_its_completed_write_receipts(setup):
    p, root, _, _ = setup
    p.schedule = lambda *_: None
    f = await p.create(request(root)); ident = f['id']
    f['pipeline']['runs'][0]['status'] = 'completed'
    f['pipeline']['stages']['design']['status'] = 'completed'
    f['pipeline']['approvals']['planner'] = {'revision': 1}; p.store.put(f)
    f = await p.launch(ident, 'development', 'Alice'); run = f['pipeline']['runs'][-1]
    old = await p.callback(ident, run['id'], 'read', 'stage_read_file', {'path': 'app.py'})
    for call_id, path, expected in [('z-write', 'z.py', ''), ('app-write', 'app.py', old['hash'])]:
        await p.callback(ident, run['id'], call_id, 'stage_write_file',
                         {'path': path, 'expected_hash': expected, 'content': 'answer = 42\n'})

    # A pending write and receipts owned by other runs/features are not evidence for this run.
    records = [
        {'feature_id': ident, 'job_id': run['id'], 'path': 'pending.py', 'status': 'pending'},
        {'feature_id': ident, 'job_id': 'another-run', 'path': 'previous.py', 'status': 'completed'},
        {'feature_id': 'another-feature', 'job_id': run['id'], 'path': 'foreign.py', 'status': 'completed'},
        {'feature_id': ident, 'job_id': run['id'], 'path': 'app.py', 'status': 'completed'},
    ]
    with p.store.db:
        for index, record in enumerate(records):
            p.store.db.execute('INSERT INTO feature_writes VALUES (?, ?)', ('receipt-' + str(index), json.dumps(record)))

    artifact = result()
    artifact['changed_files'] = ['fabricated.py', 'app.py']
    args = {'result_json': json.dumps(artifact)}
    assert await p.callback(ident, run['id'], 'saved', 'save_stage_result', args) == {'saved': True}
    assert await p.callback(ident, run['id'], 'saved', 'save_stage_result', args) == {'saved': True}
    _, saved = p.run(ident, run['id'])
    assert saved['result']['changed_files'] == ['app.py', 'z.py']
    assert saved['reported_changed_files'] == ['fabricated.py', 'app.py']
    assert len([item for item in saved['trace'] if item['name'] == 'reconcile_changed_files']) == 1
    assert len([item for item in saved['trace'] if item['name'] == 'save_stage_result']) == 1


async def test_stale_review_and_conflicting_source_never_overwrite_local_edits(setup):
    p, root, _, _ = setup; f = await reviewed(p, root)
    Path(f['worktree'], 'app.py').write_text('def answer():\n    return 43\n')
    with pytest.raises(FeatureConflict, match='Files changed'):
        await p.control(f['id'], 'approve', action('bad-release'))
    (root / 'app.py').write_text('human = True\n')
    with pytest.raises(ValueError, match='Source changed'):
        apply_to_source(p.git, f)
    assert (root / 'app.py').read_text() == 'human = True\n'


async def test_host_permissions_and_callback_receipts_survive_retries(setup):
    p, root, _, _ = setup
    p.schedule = lambda *_: None
    f = await p.create(request(root)); run = f['pipeline']['runs'][0]
    with pytest.raises(ValueError, match='disabled'):
        await p.callback(f['id'], run['id'], 'bad', 'stage_write_file', {'path': 'app.py', 'expected_hash': '', 'content': 'bad'})
    args = {'result_json': json.dumps(result())}
    assert await p.callback(f['id'], run['id'], 'done', 'save_stage_result', args) == {'saved': True}
    assert await p.callback(f['id'], run['id'], 'done', 'save_stage_result', args) == {'saved': True}
    await p.control(f['id'], 'pause', action('pause'))
    with pytest.raises(FeatureConflict):
        await p.callback(f['id'], run['id'], 'read', 'stage_read_file', {'path': 'app.py'})


async def test_pause_is_not_blocked_by_long_check(setup):
    p, root, _, _ = setup
    p.schedule = lambda *_: None
    f = await p.create(request(root)); ident = f['id']
    f['pipeline']['runs'][0]['status'] = 'completed'
    f['pipeline']['stages']['design']['status'] = 'completed'
    f['pipeline']['approvals']['planner'] = {'revision': 1}; p.store.put(f)
    f = await p.launch(ident, 'development', 'Alice'); run = f['pipeline']['runs'][-1]
    entered = asyncio.Event(); release = asyncio.Event()
    async def slow(*args):
        entered.set(); await release.wait(); return {'status': 'passed'}
    p.checks.run = slow
    task = asyncio.create_task(p.callback(ident, run['id'], 'check', 'stage_run_check', {'check': 'syntax'}))
    await entered.wait()
    await asyncio.wait_for(p.control(ident, 'pause', action('pause')), 1)
    release.set()
    with pytest.raises(FeatureConflict): await task
    assert p.get(ident)['pipeline']['status'] == 'paused'


async def test_invalid_agent_handoff_fails_visibly_and_can_be_retried(setup):
    p, root, _, _ = setup
    replace(p.workspace, 'planner_agent.py', 'not valid python !')
    f = await p.create(request(root)); await settle(p)
    latest = p.get(f['id']); assert latest['pipeline']['status'] == 'failed'
    assert latest['pipeline']['stages']['planner']['status'] == 'failed'
    replace(p.workspace, 'planner_agent.py', PROJECT_FILES['planner_agent.py'], 'repair')
    await p.control(f['id'], 'resume', action('resume')); await settle(p)
    assert p.get(f['id'])['pipeline']['status'] == 'needs_plan_approval'


@pytest.mark.parametrize('stage', STAGES)
async def test_each_saved_stage_script_validates_and_executes_in_real_sandbox(stage):
    from temporal_agent_harness.harness.code_mode.monty_stepper import ScriptRun
    from agent_dag.stage_agent import FLOW_TOOLS, stage_flow
    files = {name: PROJECT_FILES[name] for name in files_for(stage)}
    await validate_project(stage, files)
    script = files[files_for(stage)[0]].replace('Keep the handoff clear and actionable.', 'Include accessibility evidence.')
    script_run = ScriptRun(script, stage_flow().__code_mode_stubs__, answer_os=lambda *_: None)
    step = script_run.start(); called = []
    for _ in range(10):
        if step.done: assert not step.error; break
        answers = {}
        for call in step.awaiting:
            signature = inspect.signature(FLOW_TOOLS[call.function_name])
            signature = signature.replace(parameters=[p for p in signature.parameters.values() if p.name not in ('runner', 'spec', 'budget')])
            args = dict(signature.bind(*call.args, **call.kwargs).arguments); called.append((call.function_name, args))
            answers[call.call_id] = json.dumps(result()) if call.function_name == 'run_stage' else '{}'
        step = script_run.resume(answers)
    assert [c[0] for c in called] == ['get_stage_context', 'run_stage', 'save_stage_result']
    assert 'accessibility' in called[1][1]['focus']


async def test_new_workflows_load_in_temporal_sandbox():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
    from agent_dag.stage_agent import SdlcStageAgentWorkflow
    from agent_dag.studio_designer import StudioDesignerAgentWorkflow
    for cls in (SdlcStageAgentWorkflow, StudioDesignerAgentWorkflow):
        SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(cls))


def test_api_defaults_to_code_studio_and_supports_two_sessions(tmp_path):
    root = tmp_path / 'source'; root.mkdir(); (root / 'app.py').write_text('pass\n')
    app = create_app(tmp_path / 'state', with_agent=False, github=NoNetworkGitHub(), pipeline_runtime=Agent())
    with TestClient(app) as client:
        platform = client.get('/workspace/platform').json()
        assert platform['default_directory'] == str(Path(__file__).resolve().parents[1])
        assert len(platform['stages']) == 6
        created = client.post('/workspace/work', json=request(root))
        assert created.status_code == 200, created.text
        f = created.json(); assert 'source_manifest' not in f
        assert f['workspace_mode'] == 'snapshot'
        from tests.test_workspace import receive
        with client.websocket_connect('/workspace/connect?name=Alice&project=agent:design') as alice, client.websocket_connect('/workspace/connect?name=Bob&project=agent:design') as bob:
            first = receive(alice, 'snapshot'); second = receive(bob, 'snapshot')
            assert first['studio_session_ids']['design'] == second['studio_session_ids']['design']
            assert first['studio_session_ids']['review'] != first['studio_session_ids']['design']
            path = 'design_instructions.md'; doc = app.state.store.file(path)
            alice.send_json({'type': 'edit', 'id': 'alice-edit', 'file': path, 'author': 'Alice', 'operation': doc.replace(0, len(doc.text), 'Preserve accessibility.', 'alice-edit')})
            assert receive(bob, 'operation')['file'] == path
        assert client.post('/workspace/work/feature1/inputs', json={'id':'bob-comment','kind':'comment','author':'Bob','text':'I am reviewing.'}).status_code == 200
        assert client.get('/workspace/work/feature1').json()['input_revision'] == 1
