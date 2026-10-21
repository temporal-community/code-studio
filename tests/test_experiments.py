import copy
from pathlib import Path

import pytest

from features.storage import FeatureConflict
from sdlc.models import digest
from cooperative.library import CONVENTIONS
from tests.test_cooperative import setup, request


def experiment_request(feature_id='feature1'):
    return {'id': 'compare1', 'feature_id': feature_id, 'revision': 1, 'author': 'Alice',
            'title': 'Check explicit acceptance rules', 'hypothesis': 'Explicit tests reduce repairs',
            'baseline_playbook_version': digest(CONVENTIONS),
            'variant_playbook': CONVENTIONS + '\nInclude a test for every changed error state.\n'}


async def test_pair_freezes_source_brief_agents_and_playbooks_with_idempotent_retry(setup):
    service, root, _, _ = setup
    await service.create(request(root), auto_start=False)
    comparison = await service.experiments.create(experiment_request())
    experiment = service.experiments.get('compare1')
    attempts = [service.get(value) for value in experiment['attempts'].values()]
    assert attempts[0]['worktree'] != attempts[1]['worktree']
    assert all(f['base_sha'] == comparison['source_hash'] for f in attempts)
    assert all(f['description'] == 'Return 42 from answer.' for f in attempts)
    assert attempts[0]['experiment_config']['projects'] == attempts[1]['experiment_config']['projects']
    assert attempts[0]['experiment_config']['playbook'] != attempts[1]['experiment_config']['playbook']
    Path(attempts[0]['worktree'], 'app.py').write_text('changed_baseline = True\n')
    assert 'return 0' in Path(attempts[1]['worktree'], 'app.py').read_text()
    (root / 'app.py').write_text('changed_original = True\n')
    again = await service.experiments.create(experiment_request())
    assert again['source_hash'] == comparison['source_hash']
    assert len(service.store.list()) == 3
    with pytest.raises(FeatureConflict):
        await service.experiments.create({**experiment_request(), 'hypothesis': 'Different'})
    with pytest.raises(FeatureConflict, match='isolated'):
        await service.apply(attempts[0]['id'], {'revision': 1})


async def test_playbook_edit_conflict_and_frozen_experiments(setup):
    service, root, _, _ = setup
    saved = service.experiments.playbook()
    changed = service.experiments.edit_playbook({'expected_version': saved['version'], 'content': 'Shared v2', 'author': 'Bob'})
    assert service.experiments.playbook()['content'] == 'Shared v2'
    with pytest.raises(FeatureConflict):
        service.experiments.edit_playbook({'expected_version': saved['version'], 'content': 'Overwritten', 'author': 'Alice'})
    await service.create(request(root), auto_start=False)
    await service.experiments.create({**experiment_request(), 'baseline_playbook_version': changed['version']})
    service.experiments.edit_playbook({'expected_version': changed['version'], 'content': 'Shared v3', 'author': 'Bob'})
    experiment = service.experiments.get('compare1')
    assert experiment['playbooks']['baseline'] == 'Shared v2'
    feature = service.get(experiment['attempts']['baseline'])
    original = copy.deepcopy(feature['experiment_config']['projects']['ideation'])
    await service.launch(feature['id'], 'ideation', 'Alice')
    await service.stop()
    assert service.get(feature['id'])['pipeline']['runs'][0]['project'] == original


async def test_assessments_detect_conflicts_and_source_changes_and_missing_usage(setup):
    service, root, _, _ = setup
    await service.create(request(root), auto_start=False)
    comparison = await service.experiments.create(experiment_request())
    arm = comparison['attempts']['baseline']
    assert arm['tokens'] is None
    decision = {'author': 'Alice', 'expected_version': digest({}), 'verdict': 'met', 'notes': 'Checked the requirements.'}
    saved = await service.experiments.assess('compare1', 'baseline', decision)
    assert not saved['attempts']['baseline']['assessment_stale']
    with pytest.raises(FeatureConflict):
        await service.experiments.assess('compare1', 'baseline', decision)
    feature = service.get(arm['id'])
    Path(feature['worktree'], 'app.py').write_text('subsequent_edit = True\n')
    current = await service.experiments.compare('compare1')
    assert current['attempts']['baseline']['assessment_stale']


async def test_capture_requires_matching_scope_and_finished_stage(setup):
    service, root, _, _ = setup
    await service.create(request(root), auto_start=False)
    with pytest.raises(FeatureConflict):
        await service.experiments.create({**experiment_request(), 'revision': 2})
    feature = service.get('feature1'); feature['pipeline']['status'] = 'running'; service.store.put(feature)
    with pytest.raises(FeatureConflict, match='finish'):
        await service.experiments.create(experiment_request())


async def test_unstarted_edits_cannot_silently_change_the_paired_start(setup):
    service, root, _, _ = setup
    await service.create(request(root), auto_start=False)
    comparison = await service.experiments.create(experiment_request())
    variant = service.get(comparison['attempts']['variant']['id'])
    Path(variant['worktree'], 'app.py').write_text('changed_before_start = True\n')
    with pytest.raises(FeatureConflict, match='unstarted attempt'):
        await service.experiments.start('compare1', 'Alice')
    assert not service.experiments.get('compare1')['start_requested']
    assert not service.get(comparison['attempts']['baseline']['id'])['pipeline']['runs']


async def test_valid_pair_starts_once_and_same_run_new_evidence_invalidates_assessment(setup):
    from tests.test_cooperative import settle
    service, root, _, _ = setup
    await service.create(request(root), auto_start=False)
    comparison = await service.experiments.create(experiment_request())
    await service.experiments.start('compare1', 'Alice')
    await settle(service)
    await service.experiments.start('compare1', 'Alice')
    await settle(service)
    ident = comparison['attempts']['baseline']['id']
    feature = service.get(ident)
    assert len(feature['pipeline']['runs']) == 2
    assert feature['pipeline']['status'] == 'needs_plan_approval'
    await service.experiments.assess('compare1', 'baseline', {
        'author': 'Alice', 'expected_version': digest({}), 'verdict': 'partial', 'notes': 'Plan only'})
    feature['pipeline']['runs'][-1]['result']['summary'] = 'New evidence on the same run'
    service.store.put(feature)
    assert (await service.experiments.compare('compare1'))['attempts']['baseline']['assessment_stale']


async def test_partial_creation_recovers_without_recapturing_source(setup, monkeypatch):
    service, root, _, _ = setup
    await service.create(request(root), auto_start=False)
    original = service.create

    async def interrupted(request, **kwargs):
        if request['id'].endswith('-variant'):
            raise OSError('interrupted host')
        return await original(request, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(service, 'create', interrupted)
        with pytest.raises(OSError):
            await service.experiments.create(experiment_request())
    frozen_hash = service.experiments.get('compare1')['source']['base_sha']
    (root / 'app.py').write_text('human_change = True\n')
    await service.experiments.recover()
    recovered = await service.experiments.compare('compare1')
    assert recovered['status'] == 'ready'
    assert all(a['source_hash'] == frozen_hash for a in recovered['attempts'].values())


def test_experiment_and_playbook_api(tmp_path):
    from fastapi.testclient import TestClient
    from workspace.server import create_app
    from tests.test_sdlc import NoNetworkGitHub
    root = tmp_path / 'source'; root.mkdir(); (root / 'app.py').write_text('pass\n')
    app = create_app(tmp_path / 'state', with_agent=False, github=NoNetworkGitHub())
    with TestClient(app) as client:
        # Offline mode can prepare experiments; starting requires an enabled worker.
        assert client.post('/workspace/work', json=request(root)).status_code == 200
        playbook = client.get('/workspace/playbook').json()
        create = {**experiment_request(), 'baseline_playbook_version': playbook['version']}
        response = client.post('/workspace/experiments', json=create)
        assert response.status_code == 200, response.text
        assert len(response.json()['attempts']) == 2
        assert client.get('/workspace/experiments', params={'feature_id': 'feature1'}).json()['experiments'][0]['id'] == 'compare1'
        assert client.post('/workspace/experiments/compare1/start', json={'author': 'Alice'}).status_code >= 400
        assert client.get('/workspace/experiments/compare1').json()['attempts']['variant']['tokens'] is None
        invalid = {**create, 'id': '../outside'}
        assert client.post('/workspace/experiments', json=invalid).status_code == 422
