"""Exact patches preserve source, stage permissions, and crash-safe write receipts."""
import json
from pathlib import Path

import pytest
from temporal_agent_harness.harness import agent
from temporal_agent_harness.ai_sdks.openai_agents_harness import as_openai_agent_tool

from agent_dag.live_stage import stage_model_tools
from agent_dag.stage_agent import stage_model_callbacks
from cooperative.source_queries import read_range
from cooperative.definitions import files_for
from features.git import file_hash
from features.patches import patched_content
from features.storage import FeatureConflict
from tests.test_cooperative import setup as setup, request, result
from tests.test_stage_code_mode import settings


async def active(setup, stage='development'):
    p, root, _, _ = setup
    f = await p.create(request(root), auto_start=False)
    run = {'id': 'patch-run', 'stage': stage, 'status': 'running', 'input_revision': 1,
           'project': p.workspace.agent_project_snapshot(stage), 'context': {'source_patch_version': 1},
           'trace': [], 'result': None}
    f['pipeline'].update(status='running', current_stage=stage, runs=[run], approvals={'planner': {'revision': 1}})
    f['pipeline']['stages'][stage] = {'status': 'running', 'run_id': run['id']}
    p.store.put(f)
    return p, f, run


def patch_args(content='def answer():\n    return 0\n', **changes):
    return {'path': 'app.py', 'expected_hash': file_hash(content), 'old_text': 'return 0', 'new_text': 'return 42', **changes}


@pytest.mark.parametrize('content,old,new,error', [
    ('aaa', 'aa', 'b', 'more than once'),  # Includes overlapping matches.
    ('a a', 'a', 'b', 'more than once'),
    ('abc', 'd', 'b', 'not found'),
    ('abc', '', 'b', 'nonempty'),
    ('abc', 'a', 'a', 'no change'),
    ('abc', 'a', '\0', 'null bytes'),
    ('abc', 'a', 'x' * 20001, '20 KB'),
    ('abc', 'a', 'é' * 10001, '20 KB'),
    ('a' * 99999 + 'b', 'b', 'bc', '100 KB'),
])
def test_invalid_patch_is_rejected(content, old, new, error):
    with pytest.raises(ValueError, match=error):
        patched_content({'content': content, 'hash': file_hash(content)}, file_hash(content), old, new)


async def test_range_hash_patch_preserves_unmatched_bytes_and_records_changed_files(setup):
    p, f, run = await active(setup)
    original = '# café\r\ndef answer():\r\n    return 0\r\n# keep\r\n'
    path = Path(f['worktree'], 'app.py'); path.write_bytes(original.encode()); path.chmod(0o755)
    page = read_range(p.git, f, 'app.py', 3, 1)
    assert page['partial'] and page['content'] == '    return 0\r\n'
    args = patch_args(original, expected_hash=page['hash'], old_text=page['content'], new_text='    return 42\r\n')
    response = await p.callback(f['id'], run['id'], 'patch1', 'stage_patch_file', args)
    expected = original.replace('return 0', 'return 42').encode()
    assert path.read_bytes() == expected and path.stat().st_mode & 0o777 == 0o755
    assert response == {'path': 'app.py', 'hash': file_hash(expected.decode()), 'saved': True}
    # Callback retries return the receipt without reapplying an already completed edit.
    assert await p.callback(f['id'], run['id'], 'patch1', 'stage_patch_file', args) == response
    assert path.read_bytes() == expected
    deletion = await p.callback(f['id'], run['id'], 'patch2', 'stage_patch_file',
        patch_args(expected_hash=response['hash'], old_text='# keep\r\n', new_text=''))
    assert deletion['hash'] == file_hash(expected.decode().replace('# keep\r\n', ''))
    artifact = result()
    await p.callback(f['id'], run['id'], 'result', 'save_stage_result', {'result_json': json.dumps(artifact)})
    assert p.get(f['id'])['pipeline']['runs'][0]['result']['changed_files'] == ['app.py']
    assert (Path(f['repository']) / 'app.py').read_text() == 'def answer():\n    return 0\n'


async def test_stale_hash_invalid_matches_and_unsafe_paths_do_not_write(setup):
    p, f, run = await active(setup)
    before = p.git.read(f, 'app.py')
    for index, args in enumerate([
        patch_args(expected_hash='0' * 64), patch_args(expected_hash=''), patch_args(old_text='missing'),
        patch_args(old_text=' '), patch_args(path='../outside.py'), patch_args(path='.env.local'),
        patch_args(path='.code-studio-workspace.json'), patch_args(path='new.py'),
    ]):
        with pytest.raises(ValueError):
            await p.callback(f['id'], run['id'], str(index), 'stage_patch_file', args)
    (Path(f['worktree']) / 'link.py').symlink_to(Path(f['repository']) / 'app.py')
    with pytest.raises(ValueError, match='Symbolic'):
        await p.callback(f['id'], run['id'], 'link', 'stage_patch_file', patch_args(path='link.py'))
    assert p.git.read(f, 'app.py') == before
    assert not p.store.db.execute('SELECT * FROM feature_writes').fetchall()


async def test_retry_recovers_replacement_after_crash_and_never_overwrites_new_edits(setup, monkeypatch):
    p, f, run = await active(setup)
    original_write = p.git.write

    def write_then_crash(*args):
        original_write(*args)
        raise RuntimeError('Crash after atomic file replacement')

    monkeypatch.setattr(p.git, 'write', write_then_crash)
    args = patch_args()
    with pytest.raises(RuntimeError, match='Crash'):
        await p.callback(f['id'], run['id'], 'patch', 'stage_patch_file', args)
    record = json.loads(p.store.db.execute('SELECT value FROM feature_writes').fetchone()[0])
    assert record['status'] == 'pending' and 'return 42' in record['content']
    monkeypatch.setattr(p.git, 'write', original_write)
    response = await p.callback(f['id'], run['id'], 'patch', 'stage_patch_file', args)
    assert response['hash'] == file_hash('def answer():\n    return 42\n')
    with pytest.raises(FeatureConflict, match='different'):
        await p.callback(f['id'], run['id'], 'patch', 'stage_patch_file', {**args, 'new_text': 'return 99'})
    monkeypatch.setattr(p.git, 'write', write_then_crash)
    next_args = patch_args(expected_hash=response['hash'], old_text='return 42', new_text='return 43')
    with pytest.raises(RuntimeError):
        await p.callback(f['id'], run['id'], 'patch-next', 'stage_patch_file', next_args)
    path = Path(f['worktree'], 'app.py'); path.write_text('human_change = True\n')
    monkeypatch.setattr(p.git, 'write', original_write)
    with pytest.raises(ValueError, match='changed since'):
        await p.callback(f['id'], run['id'], 'patch-next', 'stage_patch_file', next_args)
    assert path.read_text() == 'human_change = True\n'


@pytest.mark.parametrize('restriction', ['old-run', 'read-only', 'no-read', 'no-write', 'unapproved', 'stale-scope'])
async def test_host_enforces_frozen_capabilities_and_approval(setup, restriction):
    p, f, run = await active(setup, 'review' if restriction == 'read-only' else 'development')
    if restriction == 'old-run': run['context'] = {}
    elif restriction == 'unapproved': f['pipeline']['approvals'] = {}
    elif restriction == 'stale-scope': f['input_revision'] = 2
    elif restriction in ('no-read', 'no-write'):
        path = files_for('development')[2]
        config = json.loads(run['project']['files'][path])
        config['tools'].remove('stage_read_file' if restriction == 'no-read' else 'stage_write_file')
        run['project']['files'][path] = json.dumps(config)
    p.store.put(f)
    with pytest.raises(ValueError):
        await p.callback(f['id'], run['id'], 'denied', 'stage_patch_file', patch_args())
    assert 'return 0' in p.git.read(f, 'app.py')['content']


async def test_patch_tool_available_in_both_modes_only_for_new_writable_runs():
    code = 'r = await stage_patch_file("app.py", "hash", "return 0", "return 42")\nprint(r["hash"])'
    config = settings('development')
    assert await agent.code_mode_type_check(stage_model_tools(config, None, None, patches=True)[0], code) is None
    assert await agent.code_mode_type_check(stage_model_tools(config, None, None)[0], code)
    assert await agent.code_mode_type_check(stage_model_tools(settings('review'), None, None, patches=True)[0], code)
    for config in (settings('development'), settings('review'), settings('development').model_copy(update={'tools': ['stage_read_file']})):
        writable = 'stage_write_file' in config.tools
        individual = config.model_copy(update={'tool_mode': 'individual'})
        tools = stage_model_tools(individual, None, None, patches=True)
        assert ('stage_patch_file' in [t.__name__ for t in tools]) == writable
        legacy = stage_model_callbacks(config, {'source_patch_version': 1})
        assert ('stage_patch_file' in [as_openai_agent_tool(None, t).name for t in legacy]) == writable
        assert 'stage_patch_file' not in [t.__name__ for t in stage_model_callbacks(config, {})]
