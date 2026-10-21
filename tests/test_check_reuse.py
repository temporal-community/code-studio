import asyncio
from pathlib import Path

import pytest

from tests.test_cooperative import setup, request


async def active(service, root):
    f = await service.create(request(root), auto_start=False)
    run = {'id': 'check-run', 'stage': 'ideation', 'status': 'running', 'input_revision': 1, 'trace': []}
    f['pipeline']['runs'].append(run)
    f['pipeline']['status'] = 'running'
    f['pipeline']['stages']['ideation'] = {'status': 'running', 'run_id': run['id']}
    service.store.put(f)
    return f, run['id']


async def test_duplicate_checks_coalesce_reuse_failures_and_force_reruns(setup, monkeypatch):
    p, root, _, _ = setup
    f, run = await active(p, root)
    original = p.checks.run; calls = []
    async def execute(feature, check):
        calls.append(check); await asyncio.sleep(.03)
        return {**await original(feature, check), 'status': 'failed', 'output': 'Actual deterministic error'}
    monkeypatch.setattr(p.checks, 'run', execute)
    results = await asyncio.gather(*(p.perform_check(f['id'], run, 'syntax') for _ in range(4)))
    assert calls == ['syntax']
    assert all(r['status'] == 'failed' for r in results)
    assert (await p.perform_check(f['id'], run, 'syntax'))['cached']
    assert not (await p.perform_check(f['id'], run, 'syntax', force=True))['cached']
    assert len(calls) == 2
    Path(f['worktree'], 'app.py').write_text('new_source = True\n')
    assert not (await p.perform_check(f['id'], run, 'syntax'))['cached']
    assert len(calls) == 3


async def test_source_changes_during_check_never_create_passing_cached_evidence(setup, monkeypatch):
    p, root, _, _ = setup
    f, run = await active(p, root)
    original = p.checks.run
    async def execute(feature, check):
        result = await original(feature, check)
        Path(f['worktree'], 'app.py').write_text('changed_during_check = True\n')
        return result
    monkeypatch.setattr(p.checks, 'run', execute)
    result = await p.perform_check(f['id'], run, 'syntax')
    assert result['status'] == 'stale'
    assert not p.get(f['id'])['pipeline'].get('check_cache')


async def test_environment_changes_invalidate_identity(setup):
    p, root, _, _ = setup
    f, _ = await active(p, root)
    modules = root / 'node_modules'; package = modules / 'tool.js'; package.write_text('version1')
    before = p.checks.identity(f, 'types')
    package.write_text('version2')
    after = p.checks.identity(f, 'types')
    assert before['source_hash'] == after['source_hash']
    assert before['cache_key'] != after['cache_key']


async def test_vite_temporary_files_do_not_invalidate_dependency_identity(setup):
    p, root, _, _ = setup
    f, _ = await active(p, root)
    before = p.checks.identity(f, 'types')
    cache = root / 'node_modules/.vite-temp'
    cache.mkdir()
    (cache / 'config.mjs').write_text('temporary generated config')
    assert p.checks.identity(f, 'types') == before
    (root / 'node_modules/skip.js').write_text('changed actual dependency')
    assert p.checks.identity(f, 'types') != before


async def test_focused_pass_does_not_replace_full_validation(setup):
    p, root, _, _ = setup
    f, run = await active(p, root)
    Path(f['worktree'], 'broken.py').write_text('def invalid(:\n')
    focused = await p.perform_check(f['id'], run, 'syntax', targets=['app.py'])
    assert focused['status'] == 'passed' and focused['scope'] == 'focused'
    assert 'syntax' not in p.get(f['id'])['pipeline']['checks']
    await p.final_checks(f['id'], run)
    assert p.get(f['id'])['pipeline']['checks']['syntax']['status'] == 'failed'
    assert (await p.perform_check(f['id'], run, 'syntax'))['cached']
    with pytest.raises(ValueError):
        await p.perform_check(f['id'], run, 'syntax', targets=['../outside.py'])
    with pytest.raises(ValueError):
        await p.perform_check(f['id'], run, 'types', targets=['app.py'])


async def test_final_checks_overlap_with_bounded_execution_and_serialize_npm(setup, monkeypatch):
    p, root, _, _ = setup
    f, run = await active(p, root)
    current = peak = npm = 0
    original = p.checks.run
    async def execute(feature, check):
        nonlocal current, peak, npm
        current += 1; peak = max(peak, current)
        is_npm = check in ('types', 'build', 'browser_tests')
        npm += int(is_npm)
        assert npm <= 1
        await asyncio.sleep(.03)
        result = await original(feature, check)
        current -= 1; npm -= int(is_npm)
        return result
    monkeypatch.setattr(p.checks, 'run', execute)
    await p.final_checks(f['id'], run)
    assert peak == 2
    assert set(p.get(f['id'])['pipeline']['checks']) == {'syntax', 'python_tests', 'browser_tests', 'types', 'build'}


async def test_final_barrier_rejects_evidence_changed_after_individual_checks(setup, monkeypatch):
    p, root, _, _ = setup
    f, run = await active(p, root)
    original = p.perform_check
    async def execute(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[2] == 'build':
            Path(f['worktree'], 'app.py').write_text('changed_after_check = True\n')
        return result
    monkeypatch.setattr(p, 'perform_check', execute)
    await p.final_checks(f['id'], run)
    assert p.get(f['id'])['pipeline']['checks']['build']['status'] == 'stale'
