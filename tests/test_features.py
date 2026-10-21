"""Feature approval gates, real Git worktrees, resumable writes, and two-session collaboration."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from features.git import LocalGit, file_hash
from features.runtime import FeatureService
from features.storage import FeatureConflict
from workspace.server import create_app
from workspace.store import WorkspaceStore
from tests.test_sdlc import NoNetworkGitHub
from tests.test_workspace import receive


def plan_for(job, **changes):
    return {'summary': 'Add task completion with tests.', 'goals': ['Complete tasks'],
            'acceptance_criteria': ['Missing IDs raise ValueError'], 'out_of_scope': ['Task deletion'],
            'steps': [{'title': 'Implement completion', 'details': 'Add complete_task and regression tests.', 'files': ['app.py', 'test_app.py']}],
            'test_plan': ['Run unittest'], 'risks': [], 'open_questions': [],
            'input_coverage': [{'input_id': c['id'], 'handling': 'Included: ' + c['text']} for c in job['brief']['inputs']], **changes}


class FakeAgent:
    def __init__(self):
        self.cancelled = []
        self.closed = []

    async def execute(self, job, callback, event):
        if job['mode'] == 'plan':
            await callback('plan-result', 'save_feature_plan', {'plan_json': json.dumps(plan_for(job))})
        else:
            files = await callback('list', 'feature_list_files', {})
            assert 'app.py' in files['files']
            source = await callback('read', 'feature_read_file', {'path': 'app.py'})
            await callback('write', 'feature_write_file', {
                'path': 'app.py', 'expected_hash': source['hash'], 'content': source['content'] + '\n# Feature implementation\n', 'summary': 'Implement feature'})
            await callback('done', 'save_implementation', {'result_json': json.dumps({
                'summary': 'Implemented the approved scope.', 'changed_files': ['app.py'],
                'suggested_checks': ['python -m unittest discover -v'], 'limitations': ['Tests not executed.']})})

    async def close_run(self, workflow_id):
        self.closed.append(workflow_id)

    async def cancel_run(self, workflow_id):
        self.cancelled.append(workflow_id)


@pytest.fixture
def setup(tmp_path):
    workspace = WorkspaceStore(tmp_path / 'state')
    messages = []
    service = FeatureService(workspace, tmp_path / 'state', messages.append, runtime=FakeAgent())
    repo = service.git.initialize_demo()
    yield workspace, service, repo, messages
    workspace.close()


def request(repo, ident='feature1', branch='feature/complete'):
    return {'id': ident, 'title': 'Complete tasks', 'description': 'Allow completing a task by ID.',
            'acceptance': 'Unknown IDs raise ValueError.', 'author': 'Alice', 'repository': repo['path'],
            'base': 'main', 'branch': branch}


def action(ident, rev=1, author='Alice'):
    return {'id': ident, 'revision': rev, 'author': author}


async def finish(service):
    tasks = list(service.tasks.values())
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), 10)


async def approved(service, repo):
    feature = await service.create(request(repo))
    await service.launch(feature['id'], action('plan1'), 'plan')
    await finish(service)
    return await service.mutate(feature['id'], 'approve', 'plan1', action('approve1'))


async def test_full_plan_approve_code_keeps_original_checkout_untouched(setup):
    workspace, service, repo, events = setup
    original = Path(repo['path'], 'app.py').read_text()
    feature = await service.create(request(repo))
    assert feature['status'] == 'draft'
    assert service.git.git(repo['path'], 'branch', '--show-current') == 'main'
    assert service.git.git(feature['worktree'], 'branch', '--show-current') == 'feature/complete'
    with pytest.raises(FeatureConflict, match='Approve'):
        await service.launch(feature['id'], action('premature'), 'code')
    await service.mutate(feature['id'], 'contribute', {'id': 'bob1', 'author': 'Bob', 'kind': 'requirement', 'text': 'Completing twice is idempotent.'})
    await service.launch(feature['id'], action('plan1', 2), 'plan')
    await finish(service)
    planned = service.store.get(feature['id'])
    assert planned['status'] == 'needs_review'
    assert planned['plans'][0]['plan']['input_coverage'][0]['input_id'] == 'bob1'
    assert Path(feature['worktree'], 'app.py').read_text() == original
    await service.mutate(feature['id'], 'approve', 'plan1', action('approve1', 2, 'Bob'))
    await service.launch(feature['id'], action('code1', 2), 'code')
    await finish(service)
    done = service.store.get(feature['id'])
    assert done['status'] == 'code_ready'
    assert done['implementation']['checks']['status'] == 'not_run'
    assert Path(repo['path'], 'app.py').read_text() == original
    assert 'Feature implementation' in Path(feature['worktree'], 'app.py').read_text()
    assert 'Feature implementation' in service.git.diff(done)['patch']
    assert all(e['type'] == 'feature-changed' for e in events)
    assert len(service.runtime.closed) == 2


async def test_new_input_revokes_approval_and_cancels_coding(setup):
    _, service, repo, _ = setup
    feature = await approved(service, repo)
    job = service.store.add_job(feature['id'], action('code1'), 'code', {})
    source = service.git.read(feature, 'app.py')
    await service.callback(feature['id'], job['id'], 'write1', 'feature_write_file', {'path': 'app.py', 'expected_hash': source['hash'], 'content': source['content'] + '\n# keep this edit\n'})
    await service.mutate(feature['id'], 'contribute', {'id': 'change', 'author': 'Bob', 'kind': 'decision', 'text': 'Also validate empty IDs.'})
    current = service.store.get(feature['id'])
    assert current['approval'] is None
    assert current['jobs'][-1]['status'] == 'superseded'
    assert job['workflow_id'] in service.runtime.cancelled
    with pytest.raises(FeatureConflict, match='approval'):
        await service.callback(feature['id'], job['id'], 'write2', 'feature_write_file', {'path': 'new.py', 'expected_hash': '', 'content': 'pass'})
    assert '# keep this edit' in service.git.read(current, 'app.py')['content']
    with pytest.raises(FeatureConflict, match='outdated'):
        await service.mutate(feature['id'], 'approve', 'plan1', action('approve-new', 2))


async def test_stale_brief_and_idempotent_contributions(setup):
    _, service, repo, _ = setup
    feature = await service.create(request(repo))
    contribution = {'id': 'input1', 'author': 'Bob', 'kind': 'question', 'text': 'Can completed tasks be completed again?'}
    await service.mutate(feature['id'], 'contribute', contribution)
    again = await service.mutate(feature['id'], 'contribute', contribution)
    assert len(again['inputs']) == 1 and again['input_revision'] == 2
    with pytest.raises(FeatureConflict, match='reused'):
        await service.mutate(feature['id'], 'contribute', {**contribution, 'text': 'Different'})
    with pytest.raises(FeatureConflict, match='changed'):
        await service.mutate(feature['id'], 'edit', {**action('edit1'), 'title': 'Old', 'description': 'Overwritten', 'acceptance': ''})
    assert service.store.get(feature['id'])['title'] == feature['title']


async def test_planner_snapshot_becomes_stale_and_questions_block_approval(setup):
    _, service, repo, _ = setup
    feature = await service.create(request(repo))
    job = service.store.add_job(feature['id'], action('plan1'), 'plan', {})
    await service.mutate(feature['id'], 'contribute', {'id': 'input1', 'author': 'Bob', 'kind': 'question', 'text': 'Which IDs?'})
    result = await service.callback(feature['id'], job['id'], 'saved1', 'save_feature_plan', {'plan_json': json.dumps(plan_for(job))})
    service.store.complete(feature['id'], job['id'])
    assert result['outdated']
    assert service.store.get(feature['id'])['status'] == 'draft'
    with pytest.raises(FeatureConflict, match='outdated'):
        await service.mutate(feature['id'], 'approve', job['id'], action('approve1', 2))
    job = service.store.add_job(feature['id'], action('plan2', 2), 'plan', {})
    with pytest.raises(ValueError, match='every collaborator'):
        await service.callback(feature['id'], job['id'], 'missing-input', 'save_feature_plan', {'plan_json': json.dumps(plan_for(job, input_coverage=[]))})
    await service.callback(feature['id'], job['id'], 'saved2', 'save_feature_plan', {'plan_json': json.dumps(plan_for(job, open_questions=['Which IDs?']))})
    service.store.complete(feature['id'], job['id'])
    with pytest.raises(FeatureConflict, match='open questions'):
        await service.mutate(feature['id'], 'approve', job['id'], action('approve2', 2))


async def test_human_and_agent_file_conflicts_do_not_overwrite(setup):
    _, service, repo, _ = setup
    feature = await approved(service, repo)
    job = service.store.add_job(feature['id'], action('code1'), 'code', {})
    old = service.git.read(feature, 'app.py')
    human = old['content'] + '\n# Bob edited this\n'
    async with service.lock(feature['id']):
        await service.write(feature['id'], None, 'human1', 'app.py', old['hash'], human, 'Bob')
    with pytest.raises(ValueError, match='changed since'):
        await service.callback(feature['id'], job['id'], 'agent1', 'feature_write_file', {'path': 'app.py', 'expected_hash': old['hash'], 'content': 'overwritten'})
    assert service.git.read(feature, 'app.py')['content'] == human
    with pytest.raises(ValueError, match='exactly the files'):
        await service.callback(feature['id'], job['id'], 'fake-report', 'save_implementation', {'result_json': json.dumps({'summary': 'Done', 'changed_files': ['app.py'], 'suggested_checks': [], 'limitations': []})})


async def test_file_receipt_survives_retries_and_recovers_after_crash(setup, monkeypatch):
    _, service, repo, _ = setup
    feature = await service.create(request(repo))
    original_put = service.store.put
    def crashed(_):
        raise RuntimeError('host crash after file save')
    monkeypatch.setattr(service.store, 'put', crashed)
    with pytest.raises(RuntimeError):
        await service.write(feature['id'], None, 'save1', 'new.py', '', 'x = 1\n', 'Alice')
    monkeypatch.setattr(service.store, 'put', original_put)
    result = await service.write(feature['id'], None, 'save1', 'new.py', '', 'x = 1\n', 'Alice')
    assert result['hash'] == file_hash('x = 1\n')
    # Simulate crash after replace but before the completed receipt was committed.
    row = service.store.db.execute('SELECT id,value FROM feature_writes').fetchone()
    record = json.loads(row[1]); record['status'] = 'pending'
    with service.store.db:
        service.store.db.execute('UPDATE feature_writes SET value=? WHERE id=?', (json.dumps(record), row[0]))
    result = await service.write(feature['id'], None, 'save1', 'new.py', '', 'x = 1\n', 'Alice')
    assert result['saved']
    with pytest.raises(FeatureConflict, match='different contents'):
        await service.write(feature['id'], None, 'save1', 'new.py', '', 'x = 2\n', 'Alice')


async def test_branch_idempotency_collision_and_external_branch_change(setup):
    _, service, repo, _ = setup
    feature = await service.create(request(repo))
    assert (await service.create(request(repo)))['worktree'] == feature['worktree']
    with pytest.raises(ValueError, match='already exists'):
        await service.create(request(repo, 'another'))
    with pytest.raises(ValueError):
        await service.create(request(repo, 'bad', '--bad'))
    service.git.git(feature['worktree'], 'checkout', '-b', 'external-branch')
    with pytest.raises(ValueError, match='branch changed'):
        await service.launch(feature['id'], action('plan1'), 'plan')


@pytest.mark.parametrize('path', ['../escape.py', '/tmp/escape.py', '.git/config', 'nested/.env.local', 'id_rsa', 'secret.pem', 'dir/../../escape.py'])
async def test_file_paths_cannot_escape_or_touch_credentials(setup, path):
    _, service, repo, _ = setup
    feature = await service.create(request(repo))
    with pytest.raises(ValueError):
        service.git.write(feature, path, '', 'secret')


async def test_symlink_files_and_parent_directories_are_rejected(setup, tmp_path):
    _, service, repo, _ = setup
    feature = await service.create(request(repo))
    root = Path(feature['worktree'])
    (root / 'outside').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='Symbolic'):
        service.git.write(feature, 'outside/escape.py', '', 'pass')
    assert 'outside' not in service.git.files(feature)


async def test_host_restart_resumes_persistent_job(setup):
    workspace, service, repo, _ = setup
    feature = await service.create(request(repo))
    service.store.add_job(feature['id'], action('plan1'), 'plan', service.git.context(feature))
    resumed = FeatureService(workspace, service.git.directory, lambda _: None, runtime=FakeAgent())
    await resumed.start()
    await finish(resumed)
    assert resumed.store.get(feature['id'])['status'] == 'needs_review'
    assert len(resumed.store.get(feature['id'])['plans']) == 1
    await resumed.stop()


def test_two_browser_sessions_share_branch_inputs_plan_and_files(tmp_path):
    app = create_app(tmp_path, with_agent=False, feature_runtime=FakeAgent(), github=NoNetworkGitHub())
    with TestClient(app) as client:
        with client.websocket_connect('/workspace/connect?name=Alice') as alice, client.websocket_connect('/workspace/connect?name=Bob') as bob:
            repo = client.post('/workspace/features/demo', json={}).json()
            created = client.post('/workspace/features', json=request(repo))
            assert created.status_code == 200, created.text
            feature = created.json(); base = '/workspace/features/' + feature['id']
            assert receive(alice, 'feature-changed')['feature_id'] == feature['id']
            assert receive(bob, 'feature-changed')['feature_id'] == feature['id']
            posted = client.post(base + '/inputs', json={'id': 'bob', 'author': 'Bob', 'kind': 'requirement', 'text': 'Idempotent completion'})
            assert posted.status_code == 200
            assert client.get(base).json()['inputs'][0]['author'] == 'Bob'
            stale = client.post(base + '/plan', json=action('stale'))
            assert stale.status_code == 409
            assert client.post(base + '/plan', json=action('plan1', 2)).status_code == 200
            client.portal.call(finish, app.state.features)
            assert client.post(base + '/plans/plan1/approve', json=action('approve1', 2, 'Bob')).status_code == 200
            assert client.post(base + '/code', json=action('code1', 2)).status_code == 200
            client.portal.call(finish, app.state.features)
            done = client.get(base).json()
            assert done['status'] == 'code_ready'
            source = client.get(base + '/file', params={'path': 'app.py'}).json()
            assert 'Feature implementation' in source['content']
            changed = client.get(base + '/diff/files').json()
            assert 'app.py' in {item['path'] for item in changed['files']}
            per_file = client.get(base + '/diff/file', params={'path': 'app.py'})
            assert per_file.status_code == 200 and 'Feature implementation' in per_file.json()['patch']
            assert client.get(base + '/diff/file', params={'path': 'test_app.py'}).status_code == 400
            saved = client.post(base + '/file', json={'id': 'save', 'author': 'Bob', 'path': 'app.py', 'expected_hash': source['hash'], 'content': 'def broken(:\n'})
            assert saved.status_code == 200
            check = client.post(base + '/syntax', json={}).json()
            assert check['status'] == 'failed'
            assert any('invalid syntax' in f.get('error', '') for f in check['files'])
            assert client.post(base + '/inputs', headers={'Origin': 'https://other.example'}, json={'id': 'bad', 'author': 'X', 'kind': 'decision', 'text': 'No'}).status_code == 403


async def test_workflow_prepares_in_temporal_sandbox():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner
    from agent_dag.feature_agent import FeatureAgentWorkflow
    SandboxedWorkflowRunner().prepare_workflow(workflow._Definition.must_from_class(FeatureAgentWorkflow))


async def test_changed_file_diff_api_covers_tracked_untracked_deleted_and_rejects_unchanged(setup):
    _, service, repo, _ = setup
    feature = await service.create(request(repo))
    root = Path(feature['worktree'])
    (root / 'app.py').write_text((root / 'app.py').read_text() + '\n# changed\n')
    (root / 'README.md').unlink()
    (root / 'new.txt').write_text('added\n')
    (root / 'tracked.txt').write_text('staged addition\n')
    (root / '.env.local').write_text('not exposed\n')
    service.git.git(root, 'add', 'tracked.txt')
    files = service.git.changed_files(feature)
    by_path = {item['path']: item for item in files['files']}
    assert set(by_path) == {'app.py', 'README.md', 'new.txt', 'tracked.txt'}
    assert by_path['tracked.txt']['change'] == 'added'
    assert by_path['app.py']['change'] == 'modified'
    assert by_path['README.md']['change'] == 'deleted' and not by_path['README.md']['content_available']
    assert by_path['new.txt']['change'] == 'added'
    assert service.git.file_diff(feature, 'app.py')['patch'].find('# changed') >= 0
    deleted = service.git.file_diff(feature, 'README.md')
    assert deleted['diff_available'] and not deleted['content_available'] and 'deleted file' in deleted['patch']
    assert 'added' in service.git.file_diff(feature, 'new.txt')['patch']
    with pytest.raises(ValueError, match='not changed'):
        service.git.file_diff(feature, 'test_app.py')
    with pytest.raises(ValueError):
        service.git.file_diff(feature, '../escape')
    # The legacy endpoint retains its public response shape.
    assert set(service.git.diff(feature)) == {'patch', 'truncated', 'new_files', 'worktree', 'branch'}


async def test_changed_file_diff_flags_oversized_and_binary_files(setup):
    _, service, repo, _ = setup
    feature = await service.create(request(repo))
    root = Path(feature['worktree'])
    (root / 'large.txt').write_text('x' * 100_001)
    (root / 'binary.bin').write_bytes(b'\x00binary')
    by_path = {item['path']: item for item in service.git.changed_files(feature)['files']}
    assert not by_path['large.txt']['content_available'] and not by_path['large.txt']['diff_available']
    assert not by_path['binary.bin']['content_available'] and not by_path['binary.bin']['diff_available']
    assert not service.git.file_diff(feature, 'large.txt')['diff_available']
