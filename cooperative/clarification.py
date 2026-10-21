"""Read-only conversations about a stage's questions, separate from scope changes."""
import asyncio
from copy import deepcopy
import logging
import time
from typing import Annotated, Literal

from pydantic import Field, StringConstraints
from features.models import StrictModel
from features.storage import FeatureConflict
from sdlc.models import digest
from .definitions import files_for
from .models import StageSettings

logger = logging.getLogger(__name__)


class ClarificationRequest(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    author: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    revision: int = Field(ge=1)
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ClarificationReply(StrictModel):
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    suggested_requirement: str = Field(default='', max_length=4000)


class ClarificationInput(StrictModel):
    model: Literal['gpt-6-luna', 'gpt-6.1-sol']
    context: dict


def require_current_questions(feature, run_id, revision):
    p = feature['pipeline']
    run = next((r for r in p['runs'] if r['id'] == run_id), None)
    if (feature['input_revision'] != revision or run is None or run['input_revision'] != revision
            or p['stages'].get(run['stage'], {}).get('run_id') != run_id
            or p['current_stage'] != run['stage'] or p['status'] != 'blocked'
            or not (run.get('result') or {}).get('questions')):
        raise FeatureConflict('This question belongs to an earlier stage or scope. Open the current stage before asking or submitting requirements.')
    return run


def check_submission(service, feature, request):
    """Called inside the contribution lock, before any scope mutations."""
    if not request.get('clarification_run_id'):
        return False
    if service.store.receipt(feature['id'] + ':input:' + request['id'], request) is not None:
        return True
    require_current_questions(feature, request['clarification_run_id'], request['revision'])
    return False


class ClarificationService:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.jobs = {}
        self.slots = asyncio.Semaphore(2)

    def schedule(self, ident, run_id, turn_id):
        key = (ident, run_id, turn_id)
        if key not in self.jobs:
            task = asyncio.create_task(self.execute(*key))
            self.jobs[key] = task
            task.add_done_callback(lambda _: self.jobs.pop(key, None))
        return self.jobs[key]

    async def recover(self):
        for item in self.pipeline.store.list():
            f = self.pipeline.store.get(item['id'])
            for run in f.get('pipeline', {}).get('runs', []):
                for turn in run.get('clarifications', []):
                    if turn['status'] == 'pending':
                        self.schedule(f['id'], run['id'], turn['id'])

    async def stop(self):
        tasks = list(self.jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def ask(self, ident, run_id, request):
        p = self.pipeline
        async with p.features.lock(ident):
            f, run = p.run(ident, run_id)
            turns = run.setdefault('clarifications', [])
            turn = next((t for t in turns if t['id'] == request.id), None)
            if turn:
                if turn['request_hash'] != digest(request.model_dump()):
                    raise FeatureConflict('This question ID was already used for different contents.')
                if turn['status'] != 'pending':
                    return turn
            else:
                require_current_questions(f, run_id, request.revision)
                if any(t['status'] == 'pending' for t in turns):
                    raise FeatureConflict('An explanation is already being prepared. Wait for it before asking another question.')
                if len(turns) >= 12:
                    raise ValueError('This stage has reached its 12-question limit. Submit your requirements or start a new stage attempt.')
                turn = {**request.model_dump(), 'request_hash': digest(request.model_dump()),
                        'status': 'pending', 'time': time.time(), 'answer': '', 'suggested_requirement': '',
                        'workflow_id': 'code-studio-clarification-' + digest([ident, run_id, request.id])}
                turns.append(turn)
                p.store.put(f); p.announce(ident)
            task = self.schedule(ident, run_id, turn['id'])
        return await asyncio.shield(task)

    async def execute(self, ident, run_id, turn_id):
        p = self.pipeline
        f, run = p.run(ident, run_id)
        turns = run['clarifications']
        index = next(i for i, t in enumerate(turns) if t['id'] == turn_id)
        turn = turns[index]
        try:
            settings = StageSettings.model_validate_json(run['project']['files'][files_for(run['stage'])[2]])
            spec = ClarificationInput(model=settings.model, context=deepcopy({
                'stage': run['stage'], 'stage_instructions': run['project']['files'][files_for(run['stage'])[1]],
                'feature': run['context'].get('feature', {}), 'stage_result': run['result'],
                'repository': run['context'].get('repository', {}),
                'upstream': run['context'].get('upstream', {}),
                'conversation': [{k: t[k] for k in ('author', 'text', 'answer')} for t in turns[:index] if t['status'] == 'completed'],
                'question': turn['text'],
            }))
            async with self.slots:
                async with asyncio.timeout(190):
                    reply = ClarificationReply.model_validate(await p.runtime.explain_questions(turn['workflow_id'], spec))
            changes = {**reply.model_dump(), 'status': 'completed'}
        except asyncio.CancelledError:
            raise  # Keep pending: a restarted host reconnects to the same durable request.
        except Exception:
            logger.exception('Stage clarification failed for feature %s run %s', ident, run_id)
            changes = {'status': 'failed', 'error': 'Could not get an explanation. Your requirements are unchanged. Try asking again.'}
        async with p.features.lock(ident):
            f, run = p.run(ident, run_id)
            turn = next(t for t in run['clarifications'] if t['id'] == turn_id)
            turn.update(changes)
            p.store.put(f); p.announce(ident)
        return turn
