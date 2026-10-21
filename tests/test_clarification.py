import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from cooperative.api import work_routes
from cooperative.clarification import ClarificationRequest, ClarificationService
from features.storage import FeatureConflict
from tests.test_cooperative import setup as setup, request, settle, result
from tests.test_feature_project import replace
from workspace.store import WorkspaceStore


REPLY = {'answer': 'Pair entries by stable ID. If IDs are missing, treat ambiguous entries as additions and removals.',
         'suggested_requirement': 'Match entries by stable ID. Show ambiguous entries as separate additions and removals.'}


async def blocked(setup):
    p, root, agent, _ = setup

    async def execute(run, callback, event):
        value = result()
        value.update(verdict='blocked', questions=['How should repeated comparison entries be paired?'])
        await callback('saved', 'save_stage_result', {'result_json': json.dumps(value)})

    agent.execute = execute
    f = await p.create(request(root)); await settle(p)
    f = p.get(f['id'])
    assert f['pipeline']['status'] == 'blocked'
    p.runtime.explain_questions = AsyncMock(return_value=REPLY)
    return p, f, f['pipeline']['runs'][-1]


def question(ident='q1', text='What does this mean? Show an example.'):
    return ClarificationRequest(id=ident, author='Alex', revision=1, text=text)


async def test_questions_are_read_only_use_frozen_context_and_persist_history(setup):
    p, f, run = await blocked(setup)
    before = deepcopy(f['pipeline']); source = p.git.read(f, 'app.py')
    frozen = run['project']['files']['ideation_instructions.md']
    replace(p.workspace, 'ideation_instructions.md', 'A different live prompt')
    reply = await p.clarifications.ask(f['id'], run['id'], question())
    assert reply['status'] == 'completed' and reply['answer'] == REPLY['answer']
    assert await p.clarifications.ask(f['id'], run['id'], question()) == reply
    assert p.runtime.explain_questions.await_count == 1
    spec = p.runtime.explain_questions.call_args.args[1]
    assert spec.context['stage_instructions'] == frozen
    assert spec.context['stage_result']['questions'] == run['result']['questions']
    await p.clarifications.ask(f['id'], run['id'], question('q2', 'What happens when there are no IDs?'))
    followup = p.runtime.explain_questions.call_args.args[1]
    assert followup.context['conversation'][0]['answer'] == REPLY['answer']
    after = p.get(f['id'])
    assert after['input_revision'] == 1 and after['inputs'] == []
    assert after['pipeline']['status'] == before['status']
    assert after['pipeline']['approvals'] == before['approvals']
    assert p.git.read(f, 'app.py') == source
    reopened = WorkspaceStore(p.workspace.directory)
    try:
        from features.storage import FeatureStore
        assert len(FeatureStore(reopened).get(f['id'])['pipeline']['runs'][-1]['clarifications']) == 2
    finally:
        reopened.close()


@pytest.mark.parametrize('version', [1, 2])
async def test_explicit_requirements_update_scope_once_and_reject_stale_submissions(setup, version):
    p, f, run = await blocked(setup)
    f['pipeline']['harness_version'] = version
    p.store.put(f)
    await p.clarifications.ask(f['id'], run['id'], question())
    body = {**question('submit', REPLY['suggested_requirement']).model_dump(), 'kind': 'requirement', 'clarification_run_id': run['id']}
    updated = await p.contribute(f['id'], body)
    assert updated['input_revision'] == 2 and updated['pipeline']['status'] == 'scope_changed'
    assert updated['pipeline']['current_stage'] == 'ideation'
    assert updated['pipeline']['approvals'] == {}
    assert updated['inputs'][-1]['text'] == REPLY['suggested_requirement']
    assert (await p.contribute(f['id'], body))['input_revision'] == 2
    with pytest.raises(FeatureConflict):
        await p.contribute(f['id'], {**body, 'id': 'another-submission'})
    with pytest.raises(FeatureConflict):
        await p.clarifications.ask(f['id'], run['id'], question('stale'))
    assert len(p.get(f['id'])['pipeline']['runs']) == 1  # No automatic restart.


async def test_concurrent_requests_share_execution_and_scope_change_preserves_historical_answer(setup):
    p, f, run = await blocked(setup)
    entered, resume = asyncio.Event(), asyncio.Event()

    async def explain(*args):
        entered.set(); await resume.wait(); return REPLY

    p.runtime.explain_questions = AsyncMock(side_effect=explain)
    first = asyncio.create_task(p.clarifications.ask(f['id'], run['id'], question()))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(p.clarifications.ask(f['id'], run['id'], question()))
        with pytest.raises(FeatureConflict):
            await p.clarifications.ask(f['id'], run['id'], question('other'))
        await p.contribute(f['id'], {'id': 'scope', 'author': 'Bob', 'kind': 'requirement', 'text': 'Updated scope'})
        resume.set()
        assert (await first)['answer'] == (await second)['answer']
        assert p.runtime.explain_questions.await_count == 1
        assert p.get(f['id'])['input_revision'] == 2
    finally:
        resume.set()
        await asyncio.gather(*[t for t in (first, second) if t], return_exceptions=True)


async def test_host_restart_reconnects_pending_question_and_failures_leave_requirements_unchanged(setup):
    p, f, run = await blocked(setup)
    entered = asyncio.Event()

    async def interrupted(*args):
        entered.set(); await asyncio.Event().wait()

    p.runtime.explain_questions = AsyncMock(side_effect=interrupted)
    task = asyncio.create_task(p.clarifications.ask(f['id'], run['id'], question()))
    await asyncio.wait_for(entered.wait(), 5)
    workflow_id = p.runtime.explain_questions.call_args.args[0]
    await p.clarifications.stop()
    await asyncio.gather(task, return_exceptions=True)
    p.clarifications = ClarificationService(p)
    p.runtime.explain_questions = AsyncMock(return_value=REPLY)
    await p.clarifications.recover()
    await asyncio.gather(*list(p.clarifications.jobs.values()))
    assert p.runtime.explain_questions.call_args.args[0] == workflow_id
    p.runtime.explain_questions.side_effect = RuntimeError('Private provider details')
    failed = await p.clarifications.ask(f['id'], run['id'], question('failure'))
    assert failed['status'] == 'failed' and 'Private provider details' not in failed['error']
    assert p.get(f['id'])['input_revision'] == 1


async def test_api_validation_disabled_model_and_explicit_submission(setup):
    p, f, run = await blocked(setup)
    app = FastAPI(); app.include_router(work_routes(lambda: p))
    app.add_exception_handler(FeatureConflict, lambda req, exc: JSONResponse({'detail': str(exc)}, status_code=409))
    endpoint = f"/workspace/work/{f['id']}/runs/{run['id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        body = question().model_dump()
        assert (await client.post(endpoint + '/clarify', json={**body, 'text': ' '})).status_code == 422
        assert (await client.post(endpoint + '/clarify', json={**body, 'text': 'x' * 4001})).status_code == 422
        assert (await client.post(endpoint + '/clarify', json=body)).json()['answer'] == REPLY['answer']
        p.enabled = False
        assert (await client.post(endpoint + '/clarify', json=body)).status_code == 503
        saved = await client.post(endpoint + '/requirements', json={**body, 'id': 'save', 'text': 'Use stable IDs.'})
        assert saved.status_code == 200 and saved.json()['inputs'][-1]['text'] == 'Use stable IDs.'
        assert (await client.post(endpoint + '/requirements', json={**body, 'id': 'stale'})).status_code == 409
