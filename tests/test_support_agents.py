import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agent_dag.improvement_suggestion import INSTRUCTIONS as ADVISOR_INSTRUCTIONS
from agent_dag.merge_resolution import INSTRUCTIONS as MERGE_INSTRUCTIONS
from agent_dag.studio_designer import designer_instructions
from cooperative.suggestions import SuggestionRequest, suggest
from tests.test_cooperative import setup as setup, action
from tests.test_feature_project import replace
from tests.test_integration import ready, merge_finished
from tests.test_sdlc import NoNetworkGitHub
from tests.test_suggestions import recommendation
from workspace.agent_host import AgentHost
from workspace.server import create_app
from workspace.store import WorkspaceStore
from workspace.support_instructions import InstructionEdit


def save_prompt(store, agent_id, content, target='default'):
    current = store.support_instruction_snapshot(agent_id, target)
    return store.support_instructions.save(agent_id,
        InstructionEdit(target=target, version=current['version'], content=content, author='Alex'),
        current['default_content'])


@pytest.mark.parametrize('agent_id,target', [
    ('improvement-advisor', 'default'), ('merge-resolver', 'default'), ('studio-designer', 'ideation'),
])
def test_instruction_edits_persist_reject_stale_writes_and_restore_defaults(tmp_path, agent_id, target):
    endpoint = '/workspace/support-agents/' + agent_id
    with TestClient(create_app(tmp_path, with_agent=False, github=NoNetworkGitHub())) as client:
        original = next(p for p in client.get(endpoint).json()['prompts'] if p['id'] == target)
        body = {'target': target, 'version': original['version'], 'content': 'Ask for observable acceptance criteria.', 'author': 'Alex'}
        response = client.post(endpoint, json=body)
        assert response.status_code == 200
        saved = next(p for p in response.json()['prompts'] if p['id'] == target)
        assert saved['content'] == body['content'] and saved['version'] != original['version']
        assert saved['default_content'] == original['content']
        assert client.post(endpoint, json={**body, 'content': 'Stale replacement'}).status_code == 409
        assert client.post(endpoint, json={**body, 'version': saved['version'], 'content': '  '}).status_code == 422
        assert client.post(endpoint, json={**body, 'content': 'x' * 20001}).status_code == 422
        assert client.post(endpoint, json={**body, 'target': 'unknown'}).status_code == 422
        assert client.post('/workspace/support-agents/unknown', json=body).status_code == 404
    with TestClient(create_app(tmp_path, with_agent=False, github=NoNetworkGitHub())) as client:
        current = next(p for p in client.get(endpoint).json()['prompts'] if p['id'] == target)
        assert current == saved
        result = client.post(endpoint, json={**body, 'version': saved['version'], 'content': original['content']})
        assert result.status_code == 200
        restored = next(p for p in result.json()['prompts'] if p['id'] == target)
        assert restored['content'] == original['content']
        assert restored['version'] != original['version']  # No stale save can succeed after a reset.
        if agent_id == 'studio-designer':
            other = next(p for p in result.json()['prompts'] if p['id'] == 'design')
            assert other['content'] == designer_instructions('design')


async def test_suggestion_captures_saved_instructions_before_async_work(setup, monkeypatch):
    service, root, _, _ = setup
    saved = save_prompt(service.workspace, 'improvement-advisor', 'First instructions')
    entered, resume = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(spec):
        calls.append(spec)
        entered.set()
        await resume.wait()
        return recommendation()

    service.runtime.suggest_improvement = execute
    task = asyncio.create_task(suggest(service, root, SuggestionRequest(author='Alex')))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        save_prompt(service.workspace, 'improvement-advisor', 'Next instructions')
        resume.set()
        await task
    finally:
        resume.set()
        await asyncio.gather(task, return_exceptions=True)
    await suggest(service, root, SuggestionRequest(author='Alex'))
    assert calls[0].instructions == 'First instructions'
    assert calls[0].instructions_version == saved['version']
    assert calls[1].instructions == 'Next instructions'


def test_catalog_exposes_helpers_without_adding_pipeline_stages_and_reads_current_models(tmp_path):
    with TestClient(create_app(tmp_path, with_agent=False, github=NoNetworkGitHub())) as client:
        catalog = client.get('/workspace/agents').json()
        assert len(catalog['agents']) == len(catalog['stages']) == 6
        helpers = {item['id']: item for item in catalog['support_agents']}
        assert set(helpers) == {'studio-designer', 'improvement-advisor', 'merge-resolver'}
        assert helpers['studio-designer']['model_settings_url'] is None
        assert helpers['studio-designer']['max_turns'] == 24
        store = client.app.state.store
        for stage in ('ideation', 'development'):
            path = stage + '_settings.json'
            settings = json.loads(store.file(path).text)
            replace(store, path, json.dumps({**settings, 'model': 'gpt-6.1-sol'}), ident=stage)
        updated = {item['id']: item for item in client.get('/workspace/agents').json()['support_agents']}
        assert updated['improvement-advisor']['model'] == updated['merge-resolver']['model'] == 'gpt-6.1-sol'
        assert updated['studio-designer']['model'] == 'gpt-6-luna'
        replace(store, 'ideation_settings.json', '{draft', ident='invalid-settings')
        updated = client.get('/workspace/agents')
        assert updated.status_code == 200
        advisor = next(a for a in updated.json()['support_agents'] if a['id'] == 'improvement-advisor')
        assert advisor['model'] is None and advisor['model_error']


def test_details_show_runtime_instructions_for_each_studio_target_and_no_source_paths(tmp_path):
    with TestClient(create_app(tmp_path, with_agent=False, github=NoNetworkGitHub())) as client:
        designer = client.get('/workspace/support-agents/studio-designer').json()
        assert len(designer['prompts']) == 6
        for prompt in designer['prompts']:
            assert prompt['content'] == designer_instructions(prompt['id'])
            assert prompt['id'] + '_instructions.md' in prompt['content']
        advisor = client.get('/workspace/support-agents/improvement-advisor').json()
        merger = client.get('/workspace/support-agents/merge-resolver').json()
        assert advisor['prompts'][0]['content'] == ADVISOR_INSTRUCTIONS
        assert merger['prompts'][0]['content'] == MERGE_INSTRUCTIONS
        assert client.get('/workspace/support-agents/unknown').status_code == 404


def test_usage_persists_counts_retries_once_and_distinguishes_unknown_outcomes(tmp_path):
    store = WorkspaceStore(tmp_path)
    usage = store.support_activity
    since = usage.summary('studio-designer')['since']
    for _ in range(2):
        with usage.track('studio-designer', ident='same-submission', success='submitted'):
            pass
    with pytest.raises(RuntimeError), usage.track('studio-designer', ident='same-submission', success='submitted'):
        raise RuntimeError('A failed retry cannot erase an acknowledgement')
    with pytest.raises(RuntimeError), usage.track('merge-resolver'):
        raise RuntimeError('Model unavailable')
    with pytest.raises(asyncio.CancelledError), usage.track('improvement-advisor'):
        raise asyncio.CancelledError()
    with usage.track('merge-resolver'):
        assert usage.summary('merge-resolver')['running'] == 1
    # Simulate a process exiting before it records the outcome of an in-flight call.
    store.db.execute("INSERT INTO support_activity VALUES ('lost', 'merge-resolver', 'running', 1, NULL)")
    store.db.commit()
    store.close()
    store = WorkspaceStore(tmp_path)
    try:
        designer = store.support_activity.summary('studio-designer')
        assert designer['since'] == since
        assert designer['requests'] == designer['submitted'] == 1
        assert designer['failed'] == 0
        merger = store.support_activity.summary('merge-resolver')
        assert merger['requests'] == 3
        assert merger['failed'] == merger['completed'] == merger['interrupted'] == 1
        assert merger['running'] == 0
        assert store.support_activity.summary('improvement-advisor')['interrupted'] == 1
    finally:
        store.close()


async def test_studio_requests_track_acknowledgements_without_storing_message_text(tmp_path):
    store = WorkspaceStore(tmp_path)
    try:
        host = AgentHost(store, lambda _: None, project='agent:design')
        host.connect = AsyncMock(return_value=SimpleNamespace(submit_message=AsyncMock()))
        saved = save_prompt(store, 'studio-designer', 'Custom design assistant instructions', target='design')
        await host.prompt('message-1', 'Alex', 'private prompt text')
        sent = host.connect.return_value.submit_message.call_args.args[1]
        assert sent['instructions'] == saved['content']
        assert sent['instructions_version'] == saved['version']
        await host.prompt('message-1', 'Alex', 'private prompt text')
        assert store.support_activity.summary('studio-designer')['submitted'] == 1
        assert 'private prompt text' not in str(store.db.execute('SELECT * FROM support_activity').fetchall())
        host.connect.side_effect = RuntimeError('Offline')
        with pytest.raises(RuntimeError):
            await host.prompt('message-2', 'Alex', 'another prompt')
        assert store.support_activity.summary('studio-designer')['failed'] == 1
    finally:
        store.close()


async def test_advisor_success_and_invalid_response_are_counted(setup):
    service, root, _, _ = setup
    service.runtime.suggest_improvement = AsyncMock(return_value=recommendation())
    await suggest(service, root, SuggestionRequest(author='Alex'))
    service.runtime.suggest_improvement.return_value = {'title': 'Incomplete'}
    with pytest.raises(ValueError):
        await suggest(service, root, SuggestionRequest(author='Alex'))
    counts = service.workspace.support_activity.summary('improvement-advisor')
    assert counts['requests'] == 2 and counts['completed'] == counts['failed'] == 1


async def test_merge_response_is_counted_even_when_it_cannot_authorize_a_merge(setup):
    service, root, _, _ = setup
    saved = save_prompt(service.workspace, 'merge-resolver', 'Ask before changing public APIs.')
    feature = await ready(service, root)
    (root / 'app.py').write_text('local_change = True\n')
    service.runtime.resolve_merge_conflict = AsyncMock(return_value={
        'choice': 'blocked', 'content': None, 'summary': 'Clarify the intended result.'})
    await service.integration(feature['id'], action('resolve'), 'resolve-agent')
    assert (await merge_finished(service, feature['id']))['status'] == 'conflicts'
    counts = service.workspace.support_activity.summary('merge-resolver')
    assert counts['requests'] == counts['completed'] == 1
    sent = service.runtime.resolve_merge_conflict.call_args.args[0]
    assert sent.instructions == saved['content'] and sent.instructions_version == saved['version']
    assert (root / 'app.py').read_text() == 'local_change = True\n'
