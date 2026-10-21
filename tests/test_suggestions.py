import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from cooperative.api import work_routes
from cooperative.suggestions import FeatureSuggestion, SuggestionInput, SuggestionRequest, project_evidence
from tests.test_cooperative import setup as setup, request


def recommendation(title='Show why a stage is blocked'):
    return {'title': title, 'description': 'Show the actionable blocker beside the current stage.',
            'acceptance': 'A blocked stage shows its reason and the next available action.',
            'rationale': 'This reduces time spent finding what prevents progress.'}


async def test_each_click_calls_agent_with_fresh_source_and_previous_suggestions(setup, monkeypatch):
    service, root, _, _ = setup
    monkeypatch.setattr('cooperative.api.ROOT', root)
    (root / 'README.md').write_text('Current Code Studio behavior')
    existing = await service.create(request(root), auto_start=False)
    service.runtime.suggest_improvement = AsyncMock(side_effect=[recommendation(), recommendation('Compare stage attempts')])
    app = FastAPI(); app.include_router(work_routes(lambda: service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        first = await client.post('/workspace/platform/suggestion', json={'author': 'Alex'})
        assert first.status_code == 200
        assert first.json()['repository'] == str(root)
        (root / 'README.md').write_text('Freshly changed Code Studio behavior')
        second = await client.post('/workspace/platform/suggestion', json={
            'author': 'Alex', 'previous_titles': [first.json()['title']]})
        assert second.status_code == 200
        assert first.json()['title'] != second.json()['title']
    calls = service.runtime.suggest_improvement.call_args_list
    assert len(calls) == 2
    assert calls[0].args[0].context['repository']['excerpts'][0]['content'] == 'Current Code Studio behavior'
    assert calls[1].args[0].context['repository']['excerpts'][0]['content'] == 'Freshly changed Code Studio behavior'
    assert calls[1].args[0].context['previous_suggestions'] == [first.json()['title']]
    assert calls[1].args[0].context['existing_features'][0]['title'] == existing['title']
    assert len(service.store.list()) == 1
    assert not service.get(existing['id'])['pipeline']['runs']


@pytest.mark.parametrize('failure,status', [(RuntimeError('private provider detail'), 503), (TimeoutError(), 504)])
async def test_agent_errors_are_actionable_and_never_return_a_canned_example(setup, monkeypatch, failure, status):
    service, root, _, _ = setup
    monkeypatch.setattr('cooperative.api.ROOT', root)
    service.runtime.suggest_improvement = AsyncMock(side_effect=failure)
    app = FastAPI(); app.include_router(work_routes(lambda: service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/workspace/platform/suggestion', json={'author': 'Alex'})
    assert response.status_code == status
    assert 'title' not in response.json()
    assert 'private provider detail' not in response.text
    assert not service.store.list()


async def test_disabled_agent_does_not_start_a_workflow(setup):
    service, _, _, _ = setup
    service.enabled = False
    service.runtime.suggest_improvement = AsyncMock()
    app = FastAPI(); app.include_router(work_routes(lambda: service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/workspace/platform/suggestion', json={'author': 'Alex'})
    assert response.status_code == 503
    service.runtime.suggest_improvement.assert_not_called()


async def test_guided_idea_uses_selected_project_and_does_not_start_feature(setup, tmp_path):
    service, _, _, _ = setup
    project = tmp_path / 'another-project'; project.mkdir()
    (project / 'README.md').write_text('A calendar for reserving meeting rooms.')
    service.runtime.suggest_improvement = AsyncMock(return_value=recommendation('Recurring reservations'))
    app = FastAPI(); app.include_router(work_routes(lambda: service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/workspace/platform/suggestion', json={
            'author': 'Alex', 'description': '  Support weekly reservations, without email notifications.  ',
            'repository': str(project), 'previous_titles': ['Recurring reservations'],
        })
    assert response.status_code == 200
    assert response.json()['repository'] == str(project)
    spec = service.runtime.suggest_improvement.call_args.args[0]
    assert spec.context['requested_feature'] == 'Support weekly reservations, without email notifications.'
    assert spec.context['repository']['excerpts'][0]['content'] == 'A calendar for reserving meeting rooms.'
    assert spec.context['previous_suggestions'] == ['Recurring reservations']
    assert not service.store.list()


async def test_invalid_selected_source_returns_actionable_error_before_model_call(setup, tmp_path):
    service, _, _, _ = setup
    service.runtime.suggest_improvement = AsyncMock()
    app = FastAPI(); app.include_router(work_routes(lambda: service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/workspace/platform/suggestion', json={
            'author': 'Alex', 'repository': str(tmp_path / 'missing'), 'description': 'An idea'})
    assert response.status_code == 400
    assert 'working directory' in response.json()['detail']
    service.runtime.suggest_improvement.assert_not_called()


def test_selected_git_revision_supplies_committed_evidence_without_private_files(tmp_path):
    from features.git import LocalGit
    git = LocalGit(tmp_path / 'state')
    root = tmp_path / 'project'; root.mkdir()
    (root / 'README.md').write_text('Committed behavior')
    (root / '.env.local').write_text('secret')
    (root / 'linked.md').symlink_to(root / '.env.local')
    git.git(root, 'init', '-b', 'main')
    git.git(root, 'add', 'README.md', '.env.local', 'linked.md')
    git.git(root, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', '-c', 'commit.gpgsign=false', 'commit', '-m', 'Initial')
    (root / 'README.md').write_text('Uncommitted behavior')
    evidence = project_evidence(root, 'git', 'HEAD')
    assert evidence['paths'] == ['README.md']
    assert evidence['excerpts'][0]['content'] == 'Committed behavior'
    assert 'secret' not in json.dumps(evidence)
    assert 'Committed source at' in evidence['note']
    assert project_evidence(root)['excerpts'][0]['content'] == 'Uncommitted behavior'


def test_guidance_is_optional_trimmed_and_bounded():
    assert SuggestionRequest(author='Alex', description='   ').description == ''
    with pytest.raises(ValidationError):
        SuggestionRequest(author='Alex', description='x' * 4001)


def test_evidence_is_bounded_and_excludes_credentials_and_symlinks(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    (root / '.env.local').write_text('secret')
    (root / '.local').mkdir(); (root / '.local' / 'private.txt').write_text('secret')
    outside = tmp_path / 'private'; outside.write_text('secret')
    (root / 'README.md').symlink_to(outside)
    (root / 'ui').mkdir(); (root / 'ui' / 'Work.svelte').write_text('source' * 4000)
    evidence = project_evidence(root)
    assert evidence['paths'] == ['ui/Work.svelte']
    assert evidence['excerpts'][0]['truncated']
    assert len(evidence['excerpts'][0]['content']) == 16000
    assert 'secret' not in json.dumps(evidence)


def test_suggestions_require_usable_bounded_form_fields():
    with pytest.raises(ValidationError): FeatureSuggestion(**{**recommendation(), 'title': ' '})
    with pytest.raises(ValidationError): FeatureSuggestion(**{**recommendation(), 'title': 'x' * 161})


async def test_runtime_starts_a_distinct_bounded_workflow_for_every_click(monkeypatch):
    from cooperative.runtime import StageRuntime
    runtime = StageRuntime()
    client = SimpleNamespace(execute_workflow=AsyncMock(return_value=FeatureSuggestion(**recommendation())))
    runtime.connect = AsyncMock(return_value=client)
    spec = SuggestionInput(model='gpt-6-luna', context={})
    await runtime.suggest_improvement(spec)
    await runtime.suggest_improvement(spec)
    calls = client.execute_workflow.call_args_list
    assert calls[0].kwargs['id'] != calls[1].kwargs['id']
    assert calls[0].args == ('CodeStudioImprovementSuggestion', spec)
    assert calls[0].kwargs['execution_timeout'].total_seconds() == 180
