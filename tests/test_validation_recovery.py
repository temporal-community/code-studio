from unittest.mock import AsyncMock

import pytest

from features.storage import FeatureConflict
from tests.test_cooperative import setup, request, result


@pytest.mark.parametrize('stage,target', [('development', 'review'), ('review', 'deployment')])
async def test_manual_approval_needs_no_evidence_and_preserves_failed_checks(setup, monkeypatch, stage, target):
    from cooperative.manual_approval import accepted_check
    p, root, _, _ = setup
    f = await p.create(request(root), auto_start=False)
    state = f['pipeline']
    state.update(harness_version=2, status='blocked', current_stage=stage)
    state['approvals']['planner'] = {'revision': 1}
    state['runs'].append({'id': 'saved', 'stage': stage, 'status': 'completed',
                          'input_revision': 1, 'result': {**result(), 'verdict': 'blocked'}})
    state['stages'][stage] = {'status': 'needs_input', 'run_id': 'saved'}
    state['checks'] = {'types': {'status': 'failed', 'output': 'Actual error'}}
    p.store.put(f)
    monkeypatch.setattr(p, 'stop_execution', AsyncMock())
    monkeypatch.setattr(p, 'handoff', AsyncMock())
    action = {'id': 'manual', 'author': 'Alice', 'revision': 1}
    after = await p.control(f['id'], 'manual_approve', action)
    state = after['pipeline']
    assert state['current_stage'] == target
    assert state['approvals'][stage]['manual']
    assert state['checks']['types']['status'] == 'failed'
    assert state['runs'][0]['result']['verdict'] == 'blocked'
    await p.control(f['id'], 'manual_approve', action)
    assert p.handoff.await_count == 1
    assert len(p.get(f['id'])['pipeline']['manual_approvals']) == 1
    if stage == 'review':
        source = state['approvals']['review']['source_hash']
        assert accepted_check(state, 'types', state['checks']['types'], source, 1)
        assert not accepted_check(state, 'types', state['checks']['types'], 'changed', 1)
        assert not accepted_check(state, 'types', {**state['checks']['types'], 'output': 'New failure'}, source, 1)
        assert not accepted_check(state, 'types', state['checks']['types'], source, 2)


async def test_evidence_approval_action_is_no_longer_available(setup):
    p, root, _, _ = setup
    f = await p.create(request(root), auto_start=False)
    f['pipeline'].update(harness_version=2, status='blocked', current_stage='development')
    p.store.put(f)
    with pytest.raises(FeatureConflict):
        await p.control(f['id'], 'request_review', {'id': 'removed', 'author': 'Alice', 'revision': 1})
    assert p.get(f['id'])['pipeline']['status'] == 'blocked'


async def test_final_validation_retries_stale_once_only(setup, monkeypatch):
    from tests.test_check_reuse import active
    p, root, _, _ = setup
    f, run = await active(p, root)
    original = p.perform_check
    calls = []
    async def perform(ident, run_id, check, **kwargs):
        if check == 'types':
            calls.append(kwargs)
            return {'check': check, 'status': 'stale', 'source_hash': p.checks.fingerprint(f)}
        return await original(ident, run_id, check, **kwargs)
    monkeypatch.setattr(p, 'perform_check', perform)
    await p.final_checks(f['id'], run)
    assert calls == [{}, {'force': True}]
    assert p.get(f['id'])['pipeline']['checks']['types']['status'] == 'stale'


def test_timeout_approval_survives_progress_noise_but_not_new_conditions_or_failures():
    from cooperative.manual_approval import accepted_check, check_signature, check_condition
    check = {'status': 'timed_out', 'source_hash': 'source', 'cache_key': 'environment',
             'command': ['pytest', '-v'], 'timeout_seconds': 300, 'output': 'test_one PASSED\ntest_two'}
    pipeline = {'approvals': {'review': {'manual': True, 'revision': 1, 'source_hash': 'source',
        'accepted_checks': {'python_tests': check_signature(check)},
        'accepted_conditions': {'python_tests': check_condition(check)}}}}
    assert accepted_check(pipeline, 'python_tests', {**check, 'output': 'test_one PASSED\ntest_two PASSED\ntest_three'}, 'source', 1)
    for changed in ({'cache_key': 'new-dependencies'}, {'timeout_seconds': 600}, {'status': 'failed'},
                    {'command': ['different-command']}, {'output': 'test_one FAILED\ntest_two'}):
        assert not accepted_check(pipeline, 'python_tests', {**check, **changed}, 'source', 1)
    assert not accepted_check(pipeline, 'python_tests', check, 'changed-source', 1)
    assert not accepted_check(pipeline, 'python_tests', check, 'source', 2)


@pytest.mark.parametrize('invalid', [None, 'source', 'review', 'missing_zip'])
async def test_manual_deployment_finishes_only_matching_existing_preview(setup, monkeypatch, invalid):
    from pathlib import Path
    p, root, _, _ = setup
    f = await p.create(request(root), auto_start=False)
    state = f['pipeline']; source = p.checks.fingerprint(f)
    state.update(harness_version=2, status='blocked', current_stage='deployment')
    state['approvals'].update(planner={'revision': 1}, review={'revision': 1, 'source_hash': source})
    state['runs'].append({'id': 'saved', 'stage': 'deployment', 'status': 'completed',
                          'input_revision': 1, 'result': {**result(), 'verdict': 'blocked'}})
    state['stages']['deployment'] = {'status': 'needs_input', 'run_id': 'saved'}
    state['checks'] = {'python_tests': {'status': 'timed_out', 'output': 'Check exceeded 120 seconds.'}}
    archive = root.parent / 'preview.zip'; archive.write_bytes(b'preview')
    state['release'] = {'source_hash': source, 'workspace_hash': source, 'path': str(archive)}
    if invalid == 'source': state['release']['workspace_hash'] = 'old'
    if invalid == 'review': state['approvals']['review']['revision'] = 0
    if invalid == 'missing_zip': archive.unlink()
    p.store.put(f)
    monkeypatch.setattr(p, 'stop_execution', AsyncMock())
    monkeypatch.setattr(p, 'handoff', AsyncMock())
    action = {'id': 'manual-deploy', 'author': 'Alice', 'revision': 1}
    if invalid:
        with pytest.raises(FeatureConflict): await p.control(f['id'], 'manual_approve', action)
        assert p.get(f['id'])['pipeline']['status'] == 'blocked'
        return
    after = await p.control(f['id'], 'manual_approve', action)
    assert after['pipeline']['status'] == 'complete'
    assert after['pipeline']['release'] == state['release']
    assert after['pipeline']['checks']['python_tests']['status'] == 'timed_out'
    assert after['pipeline']['runs'][0]['result']['verdict'] == 'blocked'
    assert after['pipeline']['approvals']['deployment']['manual']
    assert not after['pipeline'].get('pending_start')
    await p.control(f['id'], 'manual_approve', action)
    assert len(p.get(f['id'])['pipeline']['manual_approvals']) == 1
    p.handoff.assert_not_awaited()


async def test_deployment_reuses_approved_timeout_without_rerunning_it(setup, monkeypatch):
    from tests.test_check_reuse import active
    from cooperative.manual_approval import check_signature
    p, root, _, _ = setup
    f, run_id = await active(p, root)
    state = f['pipeline']; state['runs'][0]['stage'] = 'deployment'
    state['current_stage'] = 'deployment'
    state['stages']['deployment'] = {'status': 'running', 'run_id': run_id}
    state['approvals']['planner'] = {'revision': 1}
    identity = p.checks.identity(f, 'syntax')
    check = {'check': 'syntax', 'status': 'timed_out', 'output': 'timeout', **identity}
    state['checks']['syntax'] = check
    state['approvals']['review'] = {'manual': True, 'revision': 1, 'source_hash': identity['source_hash'],
                                   'accepted_checks': {'syntax': check_signature(check)}}
    p.store.put(f)
    spy = AsyncMock(wraps=p.checks.run); monkeypatch.setattr(p.checks, 'run', spy)
    actual = await p.perform_check(f['id'], run_id, 'syntax')
    assert actual['status'] == 'timed_out' and actual['cached']
    spy.assert_not_awaited()
    await p.perform_check(f['id'], run_id, 'syntax', force=True)
    spy.assert_awaited_once()
