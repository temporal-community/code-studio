"""Durable agent tasks with one local callback host for each shared feature."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
import json
import logging
import os
import time

from temporalio.exceptions import WorkflowAlreadyStartedError
from temporal_agent_harness.harness.agent_client import AgentClient, AgentTurnError, AgentTurnTimeout, CallbackResultError
from temporal_agent_harness.harness.agent_protocol import AgentConfig

from sdlc.models import digest
from sdlc.runtime import TemporalReviewRuntime
from .git import file_hash
from cooperative.source import SourceWorkspace
from .storage import ACTIVE, FeatureConflict, FeatureStore
from .project import validate_code, FeatureAgentSettings

logger = logging.getLogger(__name__)


class TemporalFeatureRuntime(TemporalReviewRuntime):
    async def execute(self, job, callback, event):
        temporal = await self.connect()
        try:
            await temporal.start_workflow('ConfiguredFeatureAgent' if job.get('agent_project') else 'FeatureAgent', AgentConfig(), id=job['workflow_id'],
                task_queue=os.environ.get('AGENT_DAG_TASK_QUEUE', 'agent-dag'),
                execution_timeout=timedelta(minutes=15))
        except WorkflowAlreadyStartedError:
            pass
        client = AgentClient(temporal, job['workflow_id'])
        stream = await client.send_message('execute', {
            'mode': job['mode'], 'brief': job['brief'], 'context': job['context'],
            'plan': job['approved_plan']['plan'] if job['approved_plan'] else None,
            'agent_project': job.get('agent_project'),
        }, on_item=lambda item, _offset: item, update_id='execute:' + job['id'], timeout=800)

        async def consume():
            async for item in stream:
                if isinstance(item, (AgentTurnError, AgentTurnTimeout)):
                    raise item
                payload = getattr(item, 'event', None)
                if payload is not None:
                    event(payload)
        reader = asyncio.create_task(consume())
        try:
            while not reader.done():
                for call in await asyncio.wait_for(client.get_pending_callbacks(), 10):
                    try:
                        try:
                            result = await callback(call.tool_id, call.tool_name, call.tool_input)
                        except (ValueError, TypeError, KeyError, OSError) as exc:
                            await client.provide_callback_result(call.tool_id, error=str(exc), update_id='result:' + call.tool_id)
                        else:
                            await client.provide_callback_result(call.tool_id, result=json.dumps(result) if job.get('agent_project') and call.tool_name in ('save_feature_plan', 'save_implementation') else result, update_id='result:' + call.tool_id)
                    except CallbackResultError as exc:
                        if exc.error_type not in ('UnknownCallback', 'CallbackAlreadyResolved'):
                            raise
                await asyncio.wait({reader}, timeout=0.25)
            await reader
        finally:
            reader.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await reader


class FeatureService:
    def __init__(self, workspace, directory, publish, *, runtime=None, enabled=True):
        self.workspace = workspace
        self.store = FeatureStore(workspace)
        self.git = SourceWorkspace(directory)
        self.publish = publish
        self.runtime = runtime or TemporalFeatureRuntime()
        self.enabled = enabled
        self.tasks = {}
        self.locks = {}
        self.repo_lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(2)

    def lock(self, ident):
        return self.locks.setdefault(ident, asyncio.Lock())

    def announce(self, ident):
        self.publish({'type': 'feature-changed', 'feature_id': ident})

    async def start(self):
        for item in self.store.list():
            feature = self.store.get(item['id'])
            if feature['status'] == 'creating':
                try:
                    await asyncio.to_thread(self.git.ensure_worktree, feature)
                    feature['status'], feature['error'] = 'draft', None
                except (ValueError, OSError) as exc:
                    feature['status'], feature['error'] = 'branch_failed', str(exc)
                self.store.put(feature)
            if self.enabled:
                for job in feature['jobs']:
                    if job['status'] in ACTIVE:
                        self.schedule(feature['id'], job['id'])

    async def stop(self):
        # Leave Temporal executions and persistent jobs resumable after a host restart.
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def create(self, request, *, allow_snapshot=False, snapshot_current=False):
        async with self.repo_lock:
            try:
                feature = self.store.get(request['id'])
            except ValueError:
                prepared = await asyncio.to_thread(self.git.prepare, request['id'], request['repository'], request['branch'], request['base'], allow_snapshot=allow_snapshot, prefer_snapshot=snapshot_current)
                feature = self.store.reserve(request, prepared)
            else:
                feature = self.store.reserve(request, {})
            if 'pipeline' in feature:
                raise FeatureConflict('Use the cooperative feature workflow to update this feature')
            if feature['status'] in ('creating', 'branch_failed'):
                try:
                    await asyncio.to_thread(self.git.ensure_worktree, feature)
                    feature['status'], feature['error'] = 'draft', None
                except (ValueError, OSError) as exc:
                    feature['status'], feature['error'] = 'branch_failed', str(exc)
                self.store.put(feature)
            self.announce(feature['id'])
            return feature

    async def mutate(self, ident, method, *args):
        async with self.lock(ident):
            feature = self.store.get(ident)
            if feature['status'] in ('creating', 'branch_failed'):
                raise ValueError('Create the feature branch successfully before editing this feature')
            before = {j['id'] for j in feature['jobs'] if j['status'] in ACTIVE}
            feature = getattr(self.store, method)(ident, *args)
            stopped = [j for j in feature['jobs'] if j['id'] in before and j['status'] == 'superseded']
            # Marking jobs superseded precedes cancellation: no subsequent write can pass its gate.
            self.announce(ident)
        for job in stopped:
            await self.stop_job(ident, job)
        return self.store.get(ident)

    async def launch(self, ident, request, mode):
        if not self.enabled:
            raise ValueError('Start just dev with an OpenAI key and worker to run the feature agent')
        async with self.lock(ident):
            if len(self.tasks) >= 8 and (ident, request['id']) not in self.tasks:
                raise ValueError('Eight agent tasks are already queued. Wait for one to finish.')
            feature = self.store.get(ident)
            if 'pipeline' in feature:
                raise FeatureConflict('Use the cooperative feature workflow to run this feature')
            context = await asyncio.to_thread(self.git.context, feature)
            existing = next((j for j in feature['jobs'] if j['id'] == request['id']), None)
            project = self.workspace.feature_project_snapshot()
            if mode == 'code' and feature['approval']:
                plan_job = next(j for j in feature['jobs'] if j['id'] == feature['approval']['plan_id'])
                project = plan_job.get('agent_project')
                if not project:
                    raise FeatureConflict('Summarize again with a saved agent version before coding')
            if not existing:
                if mode == 'plan' and request.get('agent_version') and request['agent_version'] != project['version']:
                    raise FeatureConflict('The agent changed in another session. Review the latest version and run again.')
                await validate_code(project['files'])
            job = self.store.add_job(ident, request, mode, context, agent_project=project)
            if job['status'] in ACTIVE:
                self.schedule(ident, job['id'])
            self.announce(ident)
            return self.store.get(ident)

    def schedule(self, ident, job_id):
        key = (ident, job_id)
        if key not in self.tasks:
            task = asyncio.create_task(self.work(ident, job_id))
            self.tasks[key] = task
            task.add_done_callback(lambda _: self.tasks.pop(key, None))

    async def stop_job(self, ident, job):
        task = self.tasks.get((ident, job['id']))
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        with suppress(Exception):
            await asyncio.wait_for(self.runtime.cancel_run(job['workflow_id']), 5)

    async def cancel(self, ident, job_id):
        async with self.lock(ident):
            feature, job = self.store.job(ident, job_id)
            if job['status'] in ACTIVE:
                job['status'], job['error'] = 'cancelled', 'Stopped by a collaborator. Existing file edits are retained.'
                feature['status'] = 'approved' if feature['approval'] else 'draft'
                self.store.record(feature, 'Collaborator', 'Stopped the agent task')
                self.store.put(feature)
                self.announce(ident)
        await self.stop_job(ident, job)
        return self.store.get(ident)

    async def work(self, ident, job_id):
        try:
            async with self.slots:
                feature, job = self.store.job(ident, job_id)
                if job['status'] not in ACTIVE:
                    return
                self.store.update_job(ident, job_id, status='running')
                self.announce(ident)
                await self.runtime.execute(job,
                    lambda call_id, name, args: self.callback(ident, job_id, call_id, name, args),
                    lambda event: self.event(ident, job_id, event))
                self.store.complete(ident, job_id)
                self.announce(ident)
                with suppress(Exception):
                    await asyncio.wait_for(self.runtime.close_run(job['workflow_id']), 5)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception('Feature agent task failed: %s/%s', ident, job_id)
            feature, job = self.store.job(ident, job_id)
            if job['status'] in ACTIVE:
                message = str(exc)[:2000] or type(exc).__name__
                key = os.environ.get('OPENAI_API_KEY')
                if key:
                    message = message.replace(key, '[redacted]')
                job['status'], job['error'] = 'failed', message
                if feature['input_revision'] == job['input_revision']:
                    feature['status'], feature['error'] = 'failed', message
                self.store.put(feature)
                self.announce(ident)
            with suppress(Exception):
                await asyncio.wait_for(self.runtime.cancel_run(job['workflow_id']), 5)

    def event(self, ident, job_id, event):
        data = event.model_dump(mode='json') if hasattr(event, 'model_dump') else event
        if not isinstance(data, dict):
            return
        feature, job = self.store.job(ident, job_id)
        if data.get('usage'):
            job['usage'] = data['usage']
            self.store.put(feature)
            self.announce(ident)

    async def callback(self, ident, job_id, call_id, name, args):
        async with self.lock(ident):
            feature, job = self.store.job(ident, job_id)
            if name in ('save_feature_plan', 'save_implementation'):
                result = self.store.save_artifact(ident, job_id, call_id, name, args)
            else:
                self.store.require_coding(feature, job)
                if job.get('agent_project') and name not in FeatureAgentSettings.model_validate_json(job['agent_project']['files']['feature.json']).tools:
                    raise ValueError('This file tool is disabled in the saved agent version')
                if len(job['trace']) >= 80:
                    raise ValueError('This feature task reached its file tool limit')
                if name == 'feature_list_files':
                    result = {'files': await asyncio.to_thread(self.git.files, feature)}
                elif name == 'feature_read_file':
                    await asyncio.to_thread(self.git.verify, feature)
                    result = await asyncio.to_thread(self.git.read, feature, args['path'])
                elif name == 'feature_write_file':
                    result = await self.write(ident, job_id, call_id, args['path'], args['expected_hash'], args['content'], 'Feature agent')
                else:
                    raise ValueError('Unknown feature tool')
                feature, job = self.store.job(ident, job_id)
                if not any(t.get('id') == call_id for t in job['trace']):
                    job['trace'].append({'id': call_id, 'name': name, 'detail': args.get('path', ''), 'time': time.time()})
                    self.store.put(feature)
            self.announce(ident)
            return result

    async def write(self, ident, job_id, call_id, path, expected_hash, content, author, *, authorize=None, patch=None):
        """Caller holds the feature lock; persist intent before touching the local file."""
        feature = self.store.get(ident)
        key = ident + ':' + (job_id or 'human') + ':' + call_id
        payload = {'path': path, 'expected_hash': expected_hash, 'content': content, 'author': author}
        if patch is not None:
            from .patches import validate_patch, patched_content
            validate_patch(expected_hash, patch['old_text'], patch['new_text'])
            payload['patch'] = patch
        fingerprint = digest(payload)
        row = self.store.db.execute('SELECT value FROM feature_writes WHERE id=?', (key,)).fetchone()
        record = json.loads(row[0]) if row else None
        if record:
            if record['fingerprint'] != fingerprint:
                raise FeatureConflict('File save ID was reused for different contents')
            if record['status'] == 'completed':
                return record['result']
        if authorize:
            authorize()
        elif job_id:
            _, job = self.store.job(ident, job_id)
            self.store.require_coding(feature, job)
        if patch is not None:
            # The original patch cannot be reconstructed from an already replaced file.
            # Keep its full intended result in the existing durable write journal.
            content = record['content'] if record else patched_content(
                await asyncio.to_thread(self.git.read, feature, path), expected_hash, **patch)
        if record:
            # Recover a file replacement that completed just before a host crash.
            target = self.git.resolve_file(feature, path)
            current = await asyncio.to_thread(self.git.read, feature, path) if target.exists() else {'hash': ''}
            if current['hash'] == file_hash(content):
                result = {'path': path, 'hash': current['hash'], 'saved': True}
            else:
                result = await asyncio.to_thread(self.git.write, feature, path, expected_hash, content)
        else:
            record = {'feature_id': ident, 'job_id': job_id, 'path': path, 'fingerprint': fingerprint, 'status': 'pending'}
            if patch is not None:
                record['content'] = content
            with self.store.db:
                self.store.db.execute('INSERT INTO feature_writes VALUES (?, ?)', (key, json.dumps(record)))
            result = await asyncio.to_thread(self.git.write, feature, path, expected_hash, content)
        record.update(status='completed', result=result)
        with self.store.db:
            self.store.db.execute('UPDATE feature_writes SET value=? WHERE id=?', (json.dumps(record), key))
        feature = self.store.get(ident)
        feature.pop('syntax_check', None)
        if not job_id and 'pipeline' in feature:
            pipeline = feature['pipeline']
            pipeline['approvals'].pop('review', None)
            if pipeline['status'] in ('needs_release_approval', 'complete'):
                pipeline['status'] = feature['status'] = 'needs_review'
                pipeline['current_stage'] = 'review'
                pipeline['stages']['review']['status'] = 'stale'
        self.store.record(feature, author, 'Saved ' + path)
        self.store.put(feature)
        self.announce(ident)
        return result
