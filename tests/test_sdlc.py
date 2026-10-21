"""SDLC milestone: real sandbox scripts, frozen evidence, durable callbacks and shared APIs."""
import asyncio
import inspect
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from agent_dag.pr_review import REVIEW_TOOLS, review_flow
from sdlc.models import ReviewExecution, digest
from sdlc.runtime import RunService, TemporalReviewRuntime, validate_code
from sdlc.storage import RunStore, evidence_tool
from sdlc.templates import PROJECT_FILES, demo_fixture
from workspace.document import Document
from workspace.server import create_app
from workspace.store import WorkspaceStore
from tests.test_workspace import receive

REVIEW = {'summary': 'The simplified implementation breaks empty input.', 'findings': [{
    'title': 'Restore the empty-input guard', 'severity': 'high', 'path': 'stats.py', 'line': 3,
    'explanation': 'An empty list now divides by zero instead of returning 0.0.',
    'suggestion': 'Return 0.0 when values is empty.',
}]}


class NoNetworkGitHub:
    credential_source = 'Offline test fixture'
    async def close(self):
        pass
    async def capture(self, url, progress=None):
        raise AssertionError('Fixture runs must not contact GitHub')


class SandboxRuntime:
    """Real published Monty sandbox and callbacks, with a deterministic model response."""
    def __init__(self):
        self.closed = []
        self.contexts = []
    async def execute(self, run, callback, event):
        from temporal_agent_harness.harness.code_mode.monty_stepper import ScriptRun
        spec = ReviewExecution(script=run['project']['files']['agent.py'],
                               instructions=run['project']['files']['instructions.md'], settings=run['settings'])
        tool = review_flow(spec=spec)
        sandbox = ScriptRun(spec.script, tool.__code_mode_stubs__, answer_os=lambda name, args: None)
        step = sandbox.start()
        for _ in range(90):
            if step.done:
                assert not step.error, step.error
                return
            answers = {}
            for call in step.awaiting:
                signature = inspect.signature(REVIEW_TOOLS[call.function_name])
                # Remove injected arguments from the real published tool signature.
                signature = signature.replace(parameters=[p for p in signature.parameters.values()
                                                          if p.name not in ('runner', 'spec', 'budget')])
                args = signature.bind(*call.args, **call.kwargs).arguments
                if call.function_name == 'review_changes':
                    self.contexts.append(args['context'])
                    answers[call.call_id] = json.dumps(REVIEW)
                else:
                    answers[call.call_id] = callback(str(call.call_id), call.function_name, dict(args))
            await asyncio.sleep(0)
            step = sandbox.resume(answers)
        raise AssertionError('Sandbox did not finish')
    async def close_run(self, workflow_id):
        self.closed.append(workflow_id)
    async def cancel_run(self, workflow_id):
        self.closed.append(workflow_id)


@pytest.fixture
def stores(tmp_path):
    workspace = WorkspaceStore(tmp_path)
    yield workspace, RunStore(workspace)
    workspace.close()


def ready_run(stores, ident='run-1'):
    workspace, runs = stores
    runs.create(ident, {'kind': 'fixture', 'fixture': 'demo-empty-average'}, 'Alice', workspace.project_snapshot())
    evidence = demo_fixture()['evidence']
    return runs.change(ident, status='running', evidence=evidence, evidence_hash=digest(evidence))


async def test_default_project_validates_and_removed_tool_is_rejected():
    assert (await validate_code(PROJECT_FILES))['ok']
    files = dict(PROJECT_FILES)
    settings = json.loads(files['agent.json'])
    settings['tools'].remove('get_checks')
    files['agent.json'] = json.dumps(settings)
    with pytest.raises(ValueError, match='get_checks'):
        await validate_code(files)
    files = {**PROJECT_FILES, 'agent.py': 'import subprocess\n'}
    with pytest.raises(ValueError, match='registered tools'):
        await validate_code(files)


async def test_real_starter_script_runs_in_packaged_sandbox_and_saves_report(stores):
    workspace, runs = stores
    runtime = SandboxRuntime()
    messages = []
    service = RunService(runs, messages.append, runtime=runtime, github=NoNetworkGitHub())
    await service.start()
    try:
        result = await service.create('fixture-run', {'kind': 'fixture', 'fixture': 'demo-empty-average'}, 'Alice', 0)
        await service.tasks[result['id']]
        saved = runs.get(result['id'])
        assert saved['status'] == 'completed', saved['error']
        assert saved['report']['findings'] == REVIEW['findings']
        assert saved['evaluation']['status'] == 'passed'
        assert [c['status'] for c in saved['report']['tests']['cases']] == ['failed', 'passed', 'passed', 'skipped']
        assert [t['name'] for t in saved['trace']] == ['get_pull_request', 'get_diff', 'get_checks', 'get_test_results', 'get_failure_details', 'save_review']
        assert 'ZeroDivisionError' in runtime.contexts[0]
        assert runtime.closed == [saved['workflow_id']]
        assert all(m['type'] == 'review-run' for m in messages)
    finally:
        await service.stop()


def test_run_inputs_stay_frozen_and_fixture_is_repeatable(stores):
    workspace, runs = stores
    run = ready_run(stores)
    doc = workspace.file('instructions.md')
    workspace.edit('edit', doc.replace(0, 0, 'Focus on regressions.\n', 'alice'), 'Alice', 'instructions.md')
    assert runs.get(run['id'])['project']['files']['instructions.md'] == PROJECT_FILES['instructions.md']
    first = runs.save_fixture(run['id'], 'Before agent edits')
    again = runs.save_fixture(run['id'], 'Different name')
    assert first == again
    second = runs.create('run-2', {'kind': 'fixture', 'fixture': first['id']}, 'Bob', workspace.project_snapshot())
    assert second['version'] != run['version']
    runs.change('run-2', status='running', evidence=first['evidence'], evidence_hash=digest(first['evidence']))
    for ident in ('run-1', 'run-2'):
        runs.callback(ident, 'save', 'save_review', {'review_json': json.dumps(REVIEW)})
        runs.change(ident, status='completed')
    comparison = runs.compare('run-1', 'run-2')
    assert comparison['same_evidence'] and not comparison['same_agent']
    assert comparison['changed_files'] == ['instructions.md']
    assert len(comparison['retained']) == 1
    assert comparison['added'] == comparison['removed'] == []


def test_callback_receipts_reports_and_comments_survive_restart(tmp_path):
    workspace = WorkspaceStore(tmp_path)
    runs = RunStore(workspace)
    ready_run((workspace, runs))
    args = {'review_json': json.dumps(REVIEW)}
    result = runs.callback('run-1', 'save', 'save_review', args)
    runs.add_comment('comment', 'run-1', 'Bob', 'Please add an empty-input regression test.', 0)
    workspace.close()
    workspace = WorkspaceStore(tmp_path)
    runs = RunStore(workspace)
    try:
        assert runs.callback('run-1', 'save', 'save_review', args) == result
        assert len(runs.get('run-1')['trace']) == 1
        assert runs.comments('run-1')[0]['author'] == 'Bob'
        with pytest.raises(ValueError, match='different request'):
            runs.callback('run-1', 'save', 'get_checks', {})
    finally:
        workspace.close()


@pytest.mark.parametrize('finding', [{**REVIEW['findings'][0], 'path': '../secret.py'}, {**REVIEW['findings'][0], 'line': 999}])
def test_invalid_findings_cannot_be_saved(stores, finding):
    ready_run(stores)
    runs = stores[1]
    with pytest.raises(ValueError, match='outside'):
        runs.callback('run-1', 'save', 'save_review', {'review_json': json.dumps({**REVIEW, 'findings': [finding]})})
    assert runs.get('run-1')['report'] is None
    assert runs.db.execute('SELECT COUNT(*) FROM review_callbacks').fetchone()[0] == 0


def test_ci_evidence_cannot_be_overwritten_by_model(stores):
    ready_run(stores)
    with pytest.raises(ValueError):
        stores[1].callback('run-1', 'save', 'save_review', {'review_json': json.dumps({**REVIEW, 'tests': {'passed': 400}})})
    assert evidence_tool(demo_fixture()['evidence'], 'get_test_results', {})['counts'] == {'passed': 2, 'failed': 1, 'skipped': 1}
    with pytest.raises(ValueError, match='captured'):
        evidence_tool(demo_fixture()['evidence'], 'read_file', {'path': '/etc/passwd'})


async def test_stale_revision_rejected_and_shutdown_resumes_same_run(stores):
    workspace, runs = stores
    entered = asyncio.Event()
    class WaitingRuntime(SandboxRuntime):
        async def execute(self, run, callback, event):
            entered.set()
            await asyncio.Event().wait()
    runtime = WaitingRuntime()
    service = RunService(runs, lambda _: None, runtime=runtime, github=NoNetworkGitHub())
    await service.start()
    with pytest.raises(ValueError, match='changed'):
        await service.create('bad', {'kind': 'fixture', 'fixture': 'demo-empty-average'}, 'Alice', 1)
    await service.create('resumed', {'kind': 'fixture', 'fixture': 'demo-empty-average'}, 'Alice', 0)
    await asyncio.wait_for(entered.wait(), 3)
    await service.stop()
    assert runs.get('resumed')['status'] == 'running'
    assert runtime.closed == []
    service = RunService(runs, lambda _: None, runtime=SandboxRuntime(), github=NoNetworkGitHub())
    await service.start()
    try:
        await service.tasks['resumed']
        assert runs.get('resumed')['status'] == 'completed'
    finally:
        await service.stop()


async def test_cancellation_is_terminal_and_does_not_resume(stores):
    ready_run(stores)
    runtime = SandboxRuntime()
    service = RunService(stores[1], lambda _: None, runtime=runtime, github=NoNetworkGitHub())
    await service.cancel('run-1')
    assert stores[1].get('run-1')['status'] == 'cancelled'
    await service.start()
    assert not service.tasks
    await service.stop()


def test_two_sessions_share_multiple_files_comments_and_fixture_events(tmp_path):
    app = create_app(tmp_path, with_agent=False, github=NoNetworkGitHub())
    with TestClient(app) as client:
        with client.websocket_connect('/workspace/connect?name=Alice') as alice:
            a = receive(alice, 'snapshot')
            with client.websocket_connect('/workspace/connect?name=Bob') as bob:
                b = receive(bob, 'snapshot')
                for socket, state, path, actor in ((alice, a, 'agent.py', 'Alice'), (bob, b, 'instructions.md', 'Bob')):
                    file = state['files'][path]
                    doc = Document({n['id']: n for n in file['nodes']}, file['clock'])
                    operation = doc.replace(0, 0, '# Shared change\n', actor)
                    socket.send_json({'type': 'edit', 'id': actor, 'file': path, 'operation': operation, 'author': actor})
                for socket in (alice, bob):
                    operations = [receive(socket, 'operation') for _ in range(2)]
                    assert {o['file'] for o in operations} == {'agent.py', 'instructions.md'}
                assert (tmp_path / 'agent.py').read_text().startswith('# Shared change')
                assert (tmp_path / 'instructions.md').read_text().startswith('# Shared change')
                ready_run((app.state.store, app.state.reviews))
                response = client.post('/workspace/review/runs/run-1/comments', json={'id': 'c1', 'author': 'Alice', 'text': 'Check edge cases'})
                assert response.status_code == 200
                for socket in (alice, bob):
                    assert receive(socket, 'review-comment')['run_id'] == 'run-1'
                result = client.post('/workspace/review/runs/run-1/fixture', json={'name': 'Shared fixture'})
                assert result.status_code == 200
                for socket in (alice, bob):
                    receive(socket, 'review-fixtures')
                assert client.get('/workspace/review/runs/run-1').json()['comments'][0]['text'] == 'Check edge cases'
        with client.websocket_connect('/workspace/connect?name=Bob') as bob:
            state = receive(bob, 'snapshot')
            assert state['files']['agent.py']['text'] == (tmp_path / 'agent.py').read_text()
            assert state['builder_session_id'] != state['session_id']
        assert client.post('/workspace/project/validate', json={}).json()['ok']
        assert client.post('/workspace/project/validate', json={}, headers={'origin': 'https://evil.example'}).status_code == 403


def test_api_runs_real_sandbox_and_reads_report(tmp_path):
    app = create_app(tmp_path, with_agent=False, review_runtime=SandboxRuntime(), github=NoNetworkGitHub())
    with TestClient(app) as client:
        with client.websocket_connect('/workspace/connect?name=Alice') as socket:
            receive(socket, 'snapshot')
            response = client.post('/workspace/review/runs', json={'id': 'api-run', 'author': 'Alice', 'revision': 0,
                                                               'source': {'kind': 'fixture', 'fixture': 'demo-empty-average'}})
            assert response.status_code == 200, response.text
            for _ in range(20):
                event = receive(socket, 'review-run')
                if event['run']['status'] in ('completed', 'failed'):
                    break
            assert event['run']['status'] == 'completed', event
        detail = client.get('/workspace/review/runs/api-run').json()
        assert detail['report']['findings'] == REVIEW['findings']
        assert client.post('/workspace/review/tools/preview', json={'fixture': 'demo-empty-average', 'tool': 'get_checks'}).json()['checks'][0]['conclusion'] == 'failure'
        assert client.post('/workspace/review/tools/preview', json={'fixture': 'demo-empty-average', 'tool': 'save_review'}).status_code == 400


async def test_temporal_adapter_dispatches_and_acknowledges_callbacks(monkeypatch):
    import sdlc.runtime as module
    done = asyncio.Event()
    submissions, acknowledgements = [], []
    class Temporal:
        async def start_workflow(self, name, config, **options):
            assert name == 'PrReviewAgent'
    class Client:
        def __init__(self, temporal, workflow_id):
            pass
        async def send_message(self, name, payload, *, on_item, update_id, timeout):
            submissions.append((name, payload, update_id))
            async def stream():
                await done.wait()
                yield on_item(SimpleNamespace(event=None), 1)
            return stream()
        async def get_pending_callbacks(self):
            return [SimpleNamespace(tool_id='t1', tool_name='get_checks', tool_input={})]
        async def provide_callback_result(self, tool_id, *, result, update_id):
            acknowledgements.append((tool_id, result, update_id))
            done.set()
    monkeypatch.setattr(module, 'AgentClient', Client)
    runtime = TemporalReviewRuntime()
    runtime.temporal = Temporal()
    run = {'id': 'test', 'workflow_id': 'pr-review-test', 'project': {'files': PROJECT_FILES}, 'settings': json.loads(PROJECT_FILES['agent.json'])}
    await runtime.execute(run, lambda *args: '{"checks": []}', lambda _: None)
    assert submissions[0][0] == 'execute'
    assert submissions[0][1]['script'] == PROJECT_FILES['agent.py']
    assert acknowledgements == [('t1', '{"checks": []}', 'result:t1')]


async def test_structured_review_uses_durable_complete_response_and_reports_usage(monkeypatch):
    import agent_dag.pr_review as module
    events, calls = [], []
    async def respond(agent, **kwargs):
        calls.append((agent, kwargs))
        return SimpleNamespace(final_output=module.DraftReview.model_validate(REVIEW),
                               context_wrapper=SimpleNamespace(usage=SimpleNamespace(input_tokens=123, output_tokens=45, total_tokens=168)))
    monkeypatch.setattr(module.Runner, 'run', respond)
    spec = ReviewExecution(script=PROJECT_FILES['agent.py'], instructions='Find regressions.', settings=json.loads(PROJECT_FILES['agent.json']))
    # The published workflow-only decorator retains the body in its closure.
    body = inspect.getclosurevars(module.review_changes).nonlocals['user_fn']
    result = await body(SimpleNamespace(publish=events.append), spec, [0], 'captured context', 'correctness')
    assert json.loads(result) == REVIEW
    assert calls[0][0].output_type is module.DraftReview
    assert 'captured context' in calls[0][1]['input']
    assert [str(e.type) for e in events] == ['model_interaction_started', 'model_interaction_ended']
    assert events[-1].usage.total_tokens == 168
    with pytest.raises(ValueError, match='at most three'):
        await body(SimpleNamespace(publish=events.append), spec, [3], 'context', 'focus')
