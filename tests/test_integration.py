from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cooperative.integration import Integrations
from tests.test_cooperative import setup, request, action


async def ready(p, root):
    (root / 'delete.py').write_text('obsolete = True\n')
    f = await p.create(request(root), auto_start=False)
    work = Path(f['worktree'])
    (work / 'app.py').write_text('def answer():\n    return 42\n')
    (work / 'added.py').write_text('added = True\n')
    (work / 'delete.py').unlink()
    f['pipeline'].update(status='complete', release={'workspace_hash': p.checks.fingerprint(f)})
    p.store.put(f)
    return f


async def verified(p, root):
    f = await ready(p, root)
    record = await p.integration(f['id'], action('preview'), 'prepare')
    record = await p.integration(f['id'], action('validate'), 'validate')
    assert record['status'] == 'verified', record
    return p.get(f['id']), record


async def test_snapshot_diff_lists_real_changes_without_git_and_rejects_damaged_baseline(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    listing = p.git.changed_files(f)
    assert [(v['path'], v['change']) for v in listing['files']] == [('added.py','added'),('app.py','modified'),('delete.py','deleted')]
    assert '+    return 42' in p.git.file_diff(f, 'app.py')['patch']
    assert '/dev/null' in p.git.file_diff(f, 'delete.py')['patch']
    (Path(f['baseline']) / 'app.py').write_text('tampered')
    with pytest.raises(ValueError, match='baseline changed'): p.git.changed_files(f)


async def test_combined_validation_includes_unrelated_local_changes_and_does_not_touch_checkout(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    (root / 'local.py').write_text('def broken(:\n')
    record = await p.integration(f['id'], action('preview'), 'prepare')
    assert record['status'] == 'preview'
    assert (root / 'app.py').read_text().endswith('return 0\n')
    result = await p.integration(f['id'], action('validate'), 'validate')
    assert result['status'] == 'failed'
    assert result['checks']['syntax']['status'] == 'failed'
    with pytest.raises(ValueError, match='validate integration'): await p.apply(f['id'], action('apply'))


async def test_preview_reports_conflicts_without_overwriting_local_changes(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    (root / 'app.py').write_text('local_edits = True\n')
    record = await p.integration(f['id'], action('preview'), 'prepare')
    assert record['status'] == 'conflicts'
    assert record['conflicts'][0]['path'] == 'app.py'
    assert (root / 'app.py').read_text() == 'local_edits = True\n'
    with pytest.raises(ValueError, match='Resolve conflicts'): await p.integration(f['id'], action('validate'), 'validate')


async def test_verified_apply_receipt_is_idempotent_and_rollback_preserves_other_files(setup):
    p, root, _, _ = setup
    f, record = await verified(p, root)
    first = await p.apply(f['id'], action('apply'))
    assert first == await p.apply(f['id'], action('apply'))
    assert set(first['applied']) == {'added.py', 'app.py', 'delete.py'}
    assert (root / 'app.py').read_text().endswith('return 42\n')
    assert not (root / 'delete.py').exists()
    (root / 'later.py').write_text('preserved = True\n')
    result = await p.integration(f['id'], action('rollback'), 'rollback')
    assert result['status'] == 'rolled_back'
    assert (root / 'app.py').read_text().endswith('return 0\n')
    assert (root / 'delete.py').is_file() and not (root / 'added.py').exists()
    assert (root / 'later.py').is_file()
    assert not p.get(f['id'])['pipeline']['applied']


async def test_local_edit_after_validation_invalidates_application(setup):
    p, root, _, _ = setup
    f, _ = await verified(p, root)
    (root / 'new_local.py').write_text('new = True\n')
    with pytest.raises(ValueError, match='Local files changed'): await p.apply(f['id'], action('apply'))
    assert (root / 'app.py').read_text().endswith('return 0\n')


async def test_rollback_refuses_to_overwrite_later_edits(setup):
    p, root, _, _ = setup
    f, _ = await verified(p, root)
    await p.apply(f['id'], action('apply'))
    (root / 'app.py').write_text('later_edit = True\n')
    result = await p.integration(f['id'], action('rollback'), 'rollback')
    assert result['status'] == 'recovery_required' and result['rollback_conflicts'] == ['app.py']
    assert (root / 'app.py').read_text() == 'later_edit = True\n'
    assert (root / 'added.py').is_file()  # Preflight prevents a partial rollback.


async def test_mid_apply_failure_rolls_back_written_files(setup, monkeypatch):
    import cooperative.integration as module
    p, root, _, _ = setup
    f, _ = await verified(p, root)
    original = module.replace_file
    count = 0
    def failing(*args):
        nonlocal count
        count += 1
        if count == 2: raise OSError('Simulated disk failure')
        return original(*args)
    monkeypatch.setattr(module, 'replace_file', failing)
    with pytest.raises(ValueError, match='rolled_back'): await p.apply(f['id'], action('apply'))
    assert (root / 'app.py').read_text().endswith('return 0\n')
    assert not (root / 'added.py').exists() and (root / 'delete.py').is_file()
    assert (await p.integration(f['id']))['status'] == 'rolled_back'


async def test_crash_after_replace_recovers_from_durable_journal(setup, monkeypatch):
    import cooperative.integration as module
    p, root, _, _ = setup
    f, record = await verified(p, root)
    original = module.replace_file
    class Crash(BaseException): pass
    def crashing(*args):
        original(*args)
        raise Crash()
    monkeypatch.setattr(module, 'replace_file', crashing)
    with pytest.raises(Crash): await p.apply(f['id'], action('apply'))
    monkeypatch.setattr(module, 'replace_file', original)
    manager = Integrations(p.git, p.checks)  # New instance, no in-memory recovery state.
    saved = manager.load(p.get(f['id']))
    assert saved['status'] == 'applying' and saved['written'] == []
    assert manager.rollback(saved, 'Bob')['status'] == 'rolled_back'
    assert not (root / 'added.py').exists()


async def test_candidate_tampering_and_symlink_targets_are_rejected(setup):
    p, root, _, _ = setup
    f, _ = await verified(p, root)
    manager = Integrations(p.git, p.checks); saved = manager.load(f)
    (Path(saved['candidate']['worktree']) / 'app.py').write_text('changed = True\n')
    with pytest.raises(ValueError, match='candidate changed'): await p.apply(f['id'], action('apply'))
    (root / 'app.py').unlink(); (root / 'app.py').symlink_to(root / 'delete.py')
    with pytest.raises(ValueError, match='Unsafe integration path'): manager.prepare(f, 'Alice')


async def test_executable_mode_only_change_is_previewed_applied_and_reversible(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    source = root / 'app.py'; original_mode = source.stat().st_mode & 0o777
    candidate = Path(f['worktree']) / 'app.py'
    candidate.write_bytes(source.read_bytes()); candidate.chmod(0o755)
    f['pipeline']['release']['workspace_hash'] = p.checks.fingerprint(f); p.store.put(f)
    assert any(c['path'] == 'app.py' for c in p.git.changed_files(f)['files'])
    await p.integration(f['id'], action('preview'), 'prepare')
    await p.integration(f['id'], action('validate'), 'validate')
    await p.apply(f['id'], action('apply'))
    assert source.stat().st_mode & 0o777 == 0o755
    await p.integration(f['id'], action('undo'), 'rollback')
    assert source.stat().st_mode & 0o777 == original_mode


async def test_git_receipt_verifies_actual_committed_bytes(setup):
    import subprocess
    p, root, _, _ = setup
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    git('init'); git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
    git('add', 'app.py'); git('commit', '-m', 'Baseline')
    f, _ = await verified(p, root)
    await p.apply(f['id'], action('apply'))
    assert (await p.integration(f['id']))['git_receipt']['status'] == 'not_committed'
    git('add', 'app.py', 'added.py'); git('commit', '-m', 'Integrate feature')
    assert (await p.integration(f['id']))['git_receipt']['status'] == 'committed'
    (root / 'app.py').write_text('later_edits = True\n')
    assert (await p.integration(f['id']))['git_receipt']['status'] == 'local_changes'


async def test_check_environment_drift_invalidates_verified_candidate(setup, monkeypatch):
    p, root, _, _ = setup
    f, _ = await verified(p, root)
    original = p.checks.identity
    def drift(*args, **kwargs): return {**original(*args, **kwargs), 'cache_key': 'changed-environment'}
    monkeypatch.setattr(p.checks, 'identity', drift)
    with pytest.raises(ValueError, match='environment changed'): await p.apply(f['id'], action('apply'))
    assert (root / 'app.py').read_text().endswith('return 0\n')


async def test_validation_copy_has_its_own_git_boundary(setup):
    import subprocess
    p, root, _, _ = setup
    f = await ready(p, root)
    await p.integration(f['id'], action('preview'), 'prepare')
    saved = Integrations(p.git, p.checks).load(p.get(f['id']))
    candidate = saved['candidate']['worktree']
    top = subprocess.run(['git', '-C', candidate, 'rev-parse', '--show-toplevel'], check=True, capture_output=True, text=True).stdout.strip()
    assert Path(top) == Path(candidate)
    assert not (root / '.git').exists()


async def test_already_integrated_feature_preserves_additional_local_fixes(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    feature = Path(f['worktree'])
    (root / 'app.py').write_text((feature / 'app.py').read_text() + '\n# Local fix added after integration\n')
    (root / 'added.py').write_bytes((feature / 'added.py').read_bytes())
    (root / 'delete.py').unlink()
    before = (root / 'app.py').read_bytes()
    record = await p.integration(f['id'], action('preview'), 'prepare')
    assert record['status'] == 'preview' and record['conflicts'] == []
    assert record['changes'] == []
    assert set(record['already_present']) == {'app.py','added.py','delete.py'}
    assert (root / 'app.py').read_bytes() == before


async def test_three_way_preview_combines_nonoverlapping_edits_without_applying(setup):
    p, root, _, _ = setup
    original = ''.join(f'line_{i} = {i}\n' for i in range(20))
    (root / 'app.py').write_text(original)
    f = await p.create(request(root), auto_start=False)
    feature = Path(f['worktree']) / 'app.py'
    feature.write_text(original.replace('line_1 = 1', 'line_1 = 100'))
    (root / 'app.py').write_text(original.replace('line_18 = 18', 'line_18 = 1800'))
    f['pipeline'].update(status='complete', release={'workspace_hash': p.checks.fingerprint(f)})
    p.store.put(f)
    record = await p.integration(f['id'], action('preview'), 'prepare')
    assert record['status'] == 'preview' and record['auto_merged'] == ['app.py']
    saved = Integrations(p.git, p.checks).load(p.get(f['id']))
    combined = (Path(saved['candidate']['worktree']) / 'app.py').read_text()
    assert 'line_1 = 100' in combined and 'line_18 = 1800' in combined
    assert 'line_1 = 100' not in (root / 'app.py').read_text()


async def merge_finished(p, ident):
    import asyncio
    await asyncio.wait_for(p.integration_jobs[ident], 15)
    return await p.integration(ident)


async def test_one_click_merge_finishes_in_background_and_preserves_a_rollback(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    response = await p.integration(f['id'], action('merge'), 'merge')
    assert response['operation_active'] and response['auto_merge']
    assert (root / 'app.py').read_text().endswith('return 0\n')
    result = await merge_finished(p, f['id'])
    assert result['status'] == 'applied'
    assert not result['operation_active']
    assert (root / 'app.py').read_text().endswith('return 42\n')
    await p.integration(f['id'], action('undo'), 'rollback')
    assert (root / 'app.py').read_text().endswith('return 0\n')


async def test_failed_background_merge_never_applies_files(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    (root / 'broken.py').write_text('def broken(:\n')
    await p.integration(f['id'], action('merge'), 'merge')
    result = await merge_finished(p, f['id'])
    assert result['status'] == 'failed'
    assert result['checks']['syntax']['status'] == 'failed'
    assert result['checks']['python_tests']['status'] == 'not_run'
    assert (root / 'app.py').read_text().endswith('return 0\n')
    (root / 'broken.py').write_text('fixed = True\n')
    await p.integration(f['id'], action('retry'), 'merge')
    assert (await merge_finished(p, f['id']))['status'] == 'applied'


async def test_successful_checks_are_reused_but_failed_checks_run_again(setup, monkeypatch):
    p, root, _, _ = setup
    f, _ = await verified(p, root)
    original = p.checks.run
    calls = []
    async def run(feature, name):
        calls.append(name)
        return await original(feature, name)
    monkeypatch.setattr(p.checks, 'run', run)
    result = await p.integration(f['id'], action('retry'), 'validate')
    assert result['status'] == 'verified' and calls == []
    assert all(check['cached'] for check in result['checks'].values())
    manager = Integrations(p.git, p.checks); record = manager.load(f)
    record['checks']['syntax']['status'] = 'failed'; manager.save(record)
    result = await p.integration(f['id'], action('retry-failed'), 'validate')
    assert result['status'] == 'verified' and calls == ['syntax']


async def test_merge_check_progress_is_readable_and_duplicate_clicks_share_one_job(setup, monkeypatch):
    import asyncio
    p, root, _, _ = setup
    f = await ready(p, root)
    running = asyncio.Event(); release = asyncio.Event(); frontend = asyncio.Event()
    calls = []
    original = p.checks.run
    async def run(feature, name):
        calls.append(name)
        if name == 'python_tests':
            running.set()
            await release.wait()
        if name == 'build': frontend.set()
        return await original(feature, name)
    monkeypatch.setattr(p.checks, 'run', run)
    await p.integration(f['id'], action('merge'), 'merge')
    try:
        await asyncio.wait_for(running.wait(), 10)
        # The npm lane can finish while Python is still running.
        await asyncio.wait_for(frontend.wait(), 10)
        response = await asyncio.wait_for(p.integration(f['id'], action('duplicate'), 'merge'), 1)
        assert response['status'] == 'validating' and response['operation_active']
        assert response['checks']['python_tests']['status'] == 'running'
        assert response['checks']['python_tests']['started_at']
        assert (root / 'app.py').read_text().endswith('return 0\n')
    finally: release.set()
    assert (await merge_finished(p, f['id']))['status'] == 'applied'
    assert calls.count('python_tests') == 1


@pytest.mark.parametrize('choice,expected', [('local', 'local_edits = True\n'), ('feature', 'def answer():\n    return 42\n'), ('edit', 'combined = True\n')])
async def test_conflicts_are_resolved_in_preview_then_checked_before_merge(setup, choice, expected):
    p, root, _, _ = setup
    f = await ready(p, root)
    (root / 'app.py').write_text('local_edits = True\n')
    await p.integration(f['id'], action('merge'), 'merge')
    record = await merge_finished(p, f['id'])
    assert record['status'] == 'conflicts'
    versions = await p.integration_conflict(f['id'], 'app.py')
    assert versions['local']['content'] == 'local_edits = True\n'
    assert versions['base']['content'].endswith('return 0\n')
    request = {**action('resolve'), 'integration_id': record['id'], 'path': 'app.py',
               'choice': choice, 'content': 'combined = True\n' if choice == 'edit' else None}
    resolved = await p.integration(f['id'], request, 'resolve')
    assert resolved['status'] == 'preview' and not resolved['conflicts'] and not resolved['checks']
    assert (root / 'app.py').read_text() == 'local_edits = True\n'
    assert (await p.integration(f['id'], request, 'resolve'))['resolutions'] == resolved['resolutions']
    await p.integration(f['id'], action('merge-resolved'), 'merge')
    assert (await merge_finished(p, f['id']))['status'] == 'applied'
    assert (root / 'app.py').read_text() == expected


async def test_conflict_resolution_rejects_stale_versions_and_unresolved_markers(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    (root / 'app.py').write_text('local = True\n')
    record = await p.integration(f['id'], action('preview'), 'prepare')
    request = {**action('resolve'), 'integration_id': record['id'], 'path': 'app.py', 'choice': 'edit', 'content': '<<<<<<< local\nx\n=======\ny\n>>>>>>> feature\n'}
    with pytest.raises(ValueError, match='markers'): await p.integration(f['id'], request, 'resolve')
    with pytest.raises(ValueError, match='merge changed'):
        await p.integration(f['id'], {**request, 'integration_id': '0' * 32}, 'resolve')
    (root / 'app.py').write_text('new_local_edit = True\n')
    with pytest.raises(ValueError, match='Local files changed'):
        await p.integration(f['id'], {**request, 'choice': 'feature'}, 'resolve')
    assert (root / 'app.py').read_text() == 'new_local_edit = True\n'


async def test_release_check_reuse_requires_identical_full_inputs(setup, monkeypatch):
    p, root, _, _ = setup
    f = await ready(p, root)
    result = await p.checks.run(f, 'syntax')
    identity = p.checks.identity(f, 'syntax')
    f['pipeline']['checks']['syntax'] = {**result, **identity}
    p.store.put(f)
    original = p.checks.run; calls = []
    async def run(feature, name):
        calls.append(name); return await original(feature, name)
    monkeypatch.setattr(p.checks, 'run', run)
    await p.integration(f['id'], action('preview'), 'prepare')
    record = await p.integration(f['id'], action('validate'), 'validate')
    assert record['checks']['syntax']['cached'] and 'syntax' not in calls
    (root / 'local.py').write_text('local = True\n')
    await p.integration(f['id'], action('new-preview'), 'prepare')
    record = await p.integration(f['id'], action('new-checks'), 'validate')
    assert not record['checks']['syntax']['cached'] and 'syntax' in calls


async def test_agent_resolves_preview_then_checks_and_merges(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    local = 'def answer():\n    return 7\n'
    combined = 'def answer():\n    return 42\n\nlocal_value = 7\n'
    (root / 'app.py').write_text(local)

    async def resolve(spec):
        assert (root / 'app.py').read_text() == local
        evidence = spec.context['conflict']
        assert evidence['local']['content'] == local
        assert 'return 0' in evidence['base']['content']
        assert 'return 42' in evidence['feature']['content']
        return {'choice': 'edit', 'content': combined, 'summary': 'Retained the local value and feature result.'}

    p.runtime.resolve_merge_conflict = AsyncMock(side_effect=resolve)
    await p.integration(f['id'], action('agent'), 'resolve-agent')
    result = await merge_finished(p, f['id'])
    assert result['status'] == 'applied', result
    assert result['checks']['syntax']['status'] == 'passed'
    assert result['agent_resolutions'][0]['path'] == 'app.py'
    assert (root / 'app.py').read_text() == combined
    p.runtime.resolve_merge_conflict.assert_awaited_once()
    await p.integration(f['id'], action('undo'), 'rollback')
    assert (root / 'app.py').read_text() == local


@pytest.mark.parametrize('choice,content,message', [
    ('blocked', None, 'Both sides change the return value; clarify the intended value.'),
    ('edit', '<<<<<<< local\n', 'Invalid markers'),
    ('edit', 'def invalid(:\n', 'Invalid syntax'),
])
async def test_agent_bad_or_ambiguous_result_never_applies(setup, choice, content, message):
    p, root, _, _ = setup
    f = await ready(p, root)
    local = 'def answer():\n    return 7\n'
    (root / 'app.py').write_text(local)
    p.runtime.resolve_merge_conflict = AsyncMock(return_value={'choice': choice, 'content': content, 'summary': message})
    await p.integration(f['id'], action('agent'), 'resolve-agent')
    result = await merge_finished(p, f['id'])
    assert result['status'] in ('conflicts', 'failed'), result
    assert (root / 'app.py').read_text() == local
    assert not p.get(f['id'])['pipeline'].get('applied')


async def test_agent_rejects_edits_made_during_model_call_and_can_retry(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    (root / 'app.py').write_text('local = 7\n')

    async def resolve(spec):
        (root / 'app.py').write_text('newer_local = 8\n')
        return {'choice': 'feature', 'content': None, 'summary': 'Use feature.'}

    p.runtime.resolve_merge_conflict = AsyncMock(side_effect=resolve)
    await p.integration(f['id'], action('agent'), 'resolve-agent')
    result = await merge_finished(p, f['id'])
    assert result['status'] == 'conflicts'
    assert 'Local files changed' in result['error']
    assert (root / 'app.py').read_text() == 'newer_local = 8\n'
    p.runtime.resolve_merge_conflict = AsyncMock(return_value={
        'choice': 'edit', 'content': 'newer_local = 8\ndef answer():\n    return 42\n', 'summary': 'Combined changes.'})
    await p.integration(f['id'], action('retry-agent'), 'resolve-agent')
    assert (await merge_finished(p, f['id']))['status'] == 'applied'


async def test_agent_failure_is_retryable_and_double_click_does_not_duplicate_calls(setup):
    import asyncio
    p, root, _, _ = setup
    f = await ready(p, root)
    (root / 'app.py').write_text('local = True\n')
    started, finish = asyncio.Event(), asyncio.Event()

    async def resolve(spec):
        started.set()
        await finish.wait()
        raise RuntimeError('Model unavailable')

    p.runtime.resolve_merge_conflict = AsyncMock(side_effect=resolve)
    await p.integration(f['id'], action('agent'), 'resolve-agent')
    await asyncio.wait_for(started.wait(), 5)
    response = await p.integration(f['id'], action('duplicate'), 'resolve-agent')
    assert response['operation_active']
    finish.set()
    result = await merge_finished(p, f['id'])
    assert result['agent_resolution']['status'] == 'blocked'
    assert 'Model unavailable' in result['error']
    p.runtime.resolve_merge_conflict.assert_awaited_once()
    assert (root / 'app.py').read_text() == 'local = True\n'
    p.runtime.resolve_merge_conflict = AsyncMock(return_value={
        'choice': 'edit', 'content': 'local = True\ndef answer():\n    return 42\n', 'summary': 'Combined.'})
    await p.integration(f['id'], action('retry'), 'resolve-agent')
    assert (await merge_finished(p, f['id']))['status'] == 'applied'


async def test_merge_agent_receives_answer_and_previous_question(setup):
    p, root, _, _ = setup
    f = await ready(p, root)
    (root / 'app.py').write_text('local = True\n')
    question = 'Should the local flag be retained?'
    p.runtime.resolve_merge_conflict = AsyncMock(return_value={
        'choice': 'blocked', 'content': None, 'summary': question})
    await p.integration(f['id'], action('ask'), 'resolve-agent')
    assert (await merge_finished(p, f['id']))['status'] == 'conflicts'

    async def resolve(spec):
        assert question in spec.context['previous_question']
        assert spec.context['user_guidance'][0]['answer'] == 'Keep the local flag and add the feature.'
        assert question in spec.context['user_guidance'][0]['question']
        return {'choice': 'edit', 'content': 'local = True\ndef answer():\n    return 42\n', 'summary': 'Kept both as requested.'}

    p.runtime.resolve_merge_conflict = AsyncMock(side_effect=resolve)
    await p.integration(f['id'], {**action('answer'), 'note': 'Keep the local flag and add the feature.'}, 'resolve-agent')
    result = await merge_finished(p, f['id'])
    assert result['status'] == 'applied', result
    assert result['agent_guidance'][0]['answer'] == 'Keep the local flag and add the feature.'
