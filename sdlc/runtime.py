"""Bridge durable harness review executions to the local evidence and artifact store."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
import logging
import os
import time

from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporal_agent_harness.harness.agent_client import AgentClient, AgentTurnError, AgentTurnTimeout, CallbackResultError
from temporal_agent_harness.harness.agent_protocol import AgentConfig
from temporal_agent_harness.harness import agent
from temporal_agent_harness.plugin import AgentHarnessPlugin

from .github import GitHub, local_token
from .models import ReviewExecution, digest, validate_project
from .storage import TERMINAL

logger = logging.getLogger(__name__)


async def validate_code(files: dict) -> dict:
    from agent_dag.pr_review import review_flow
    settings, _ = validate_project(files)
    spec = ReviewExecution(script=files['agent.py'], instructions=files['instructions.md'], settings=settings)
    error = await agent.code_mode_type_check(review_flow(spec=spec), spec.script)
    if error:
        raise ValueError(error)
    return {'ok': True, 'message': 'Agent settings and Python tool contract are valid.'}


class TemporalReviewRuntime:
    def __init__(self):
        self.temporal = None
        self.lock = asyncio.Lock()

    async def connect(self):
        async with self.lock:
            if self.temporal is None:
                config = ClientConfig.load_client_connect_config()
                config.setdefault('target_host', 'localhost:7233')
                self.temporal = await Client.connect(**config, plugins=[AgentHarnessPlugin()])
            return self.temporal

    async def execute(self, run: dict, callback, event):
        temporal = await self.connect()
        try:
            await temporal.start_workflow('PrReviewAgent', AgentConfig(), id=run['workflow_id'],
                                          task_queue=os.environ.get('AGENT_DAG_TASK_QUEUE', 'agent-dag'),
                                          execution_timeout=timedelta(minutes=15))
        except WorkflowAlreadyStartedError:
            pass
        client = AgentClient(temporal, run['workflow_id'])
        stream = await client.send_message('execute', {
            'script': run['project']['files']['agent.py'],
            'instructions': run['project']['files']['instructions.md'],
            'settings': run['settings'],
        }, on_item=lambda item, _offset: item, update_id='execute:' + run['id'], timeout=600)

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
                            result = callback(call.tool_id, call.tool_name, call.tool_input)
                        except (ValueError, TypeError, KeyError) as exc:
                            await client.provide_callback_result(call.tool_id, error=str(exc), update_id='result:' + call.tool_id)
                        else:
                            await client.provide_callback_result(call.tool_id, result=result, update_id='result:' + call.tool_id)
                    except CallbackResultError as exc:
                        if exc.error_type not in ('UnknownCallback', 'CallbackAlreadyResolved'):
                            raise
                await asyncio.wait({reader}, timeout=0.25)
            await reader
        finally:
            reader.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await reader

    async def close_run(self, workflow_id: str):
        temporal = await self.connect()
        await temporal.get_workflow_handle(workflow_id).signal('close')

    async def cancel_run(self, workflow_id: str):
        temporal = await self.connect()
        await temporal.get_workflow_handle(workflow_id).cancel()


class RunService:
    def __init__(self, store, publish, *, runtime=None, github=None, enabled=True):
        self.store, self.publish = store, publish
        self.runtime = runtime or TemporalReviewRuntime()
        self.github = github
        self.enabled = enabled
        self.tasks: dict[str, asyncio.Task] = {}
        self.slots = asyncio.Semaphore(2)
        self.stopping = False
        self.credential_source = 'Checking local configuration'

    async def start(self):
        if self.github is None:
            token, source = await asyncio.to_thread(local_token)
            self.credential_source = source
            self.github = GitHub(token, credential_source=source)
        else:
            self.credential_source = self.github.credential_source
        if self.enabled:
            for run in self.store.list():
                if run['status'] not in TERMINAL:
                    self.schedule(run['id'])

    async def stop(self):
        self.stopping = True
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        if self.github:
            await self.github.close()

    def announce(self, ident: str):
        self.publish({'type': 'review-run', 'run': self.store.summary(self.store.get(ident))})

    def schedule(self, ident: str):
        if ident not in self.tasks:
            task = asyncio.create_task(self.work(ident))
            self.tasks[ident] = task
            task.add_done_callback(lambda _: self.tasks.pop(ident, None))

    async def create(self, ident: str, source: dict, author: str, expected_revision: int):
        if not self.enabled:
            raise ValueError('Start the full stack with just dev to run the review agent')
        if expected_revision != self.store.workspace.revision:
            raise ValueError('The agent project changed. Wait for shared edits to sync, then run again.')
        if len(self.tasks) >= 8 and ident not in self.tasks:
            raise ValueError('Eight runs are already queued. Wait for a run to finish.')
        project = self.store.workspace.project_snapshot()
        # Validation may yield: retain this exact snapshot rather than rereading files later.
        await validate_code(project['files'])
        run = self.store.create(ident, source, author, project)
        if run['status'] not in TERMINAL:
            self.schedule(ident)
        self.announce(ident)
        return self.store.summary(run)

    async def work(self, ident: str):
        try:
            async with self.slots:
                run = self.store.get(ident)
                if run['status'] in TERMINAL:
                    return
                if time.time() - run['created_at'] > 900:
                    raise ValueError('This run exceeded its 15-minute lifetime. Start a new run.')
                if run['evidence'] is None:
                    self.store.change(ident, status='capturing', stage='Capturing PR evidence')
                    self.announce(ident)
                    if run['source']['kind'] == 'fixture':
                        evidence = self.store.fixture(run['source']['fixture'])['evidence']
                    else:
                        def progress(stage):
                            self.store.change(ident, stage=stage)
                            self.announce(ident)
                        evidence = await asyncio.wait_for(self.github.capture(run['source']['url'], progress), 180)
                    self.store.change(ident, evidence=evidence, evidence_hash=digest(evidence), title=evidence['pr']['title'])
                run = self.store.change(ident, status='running', stage='Running frozen agent project')
                self.announce(ident)
                seen_events = set()
                usage = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}

                def callback(call_id, name, arguments):
                    try:
                        result = self.store.callback(ident, call_id, name, arguments)
                    except (ValueError, TypeError, KeyError) as exc:
                        self.store.trace(ident, name, arguments, str(exc), error=True)
                        self.announce(ident)
                        raise
                    self.announce(ident)
                    return result

                def event(payload):
                    kind = str(payload.type)
                    if kind == 'tool_start':
                        self.store.change(ident, stage='Running ' + payload.tool_name)
                        self.announce(ident)
                    elif kind == 'tool_end' and payload.tool_name == 'review_changes':
                        key = payload.tool_id
                        if key not in seen_events:
                            seen_events.add(key)
                            # Stream replay can repeat events after a host restart; callback
                            # receipts remain the authority for file/report side effects.
                            self.store.trace(ident, 'review_changes', {}, payload.tool_output, event_id=key)
                            self.announce(ident)
                    elif kind == 'tool_error':
                        self.store.trace(ident, payload.tool_name, {}, payload.message,
                                         error=True, event_id=payload.tool_id + ':error')
                        self.announce(ident)
                    token_usage = getattr(payload, 'usage', None)
                    if token_usage is not None:
                        for key in usage:
                            usage[key] += getattr(token_usage, key, None) or 0
                        self.store.change(ident, usage=usage)

                await asyncio.wait_for(self.runtime.execute(run, callback, event), timeout=600)
                if not self.store.get(ident)['report']:
                    raise ValueError('The agent finished without saving a review. End agent.py with await save_review(review).')
                self.store.change(ident, status='completed', stage='Review complete')
                self.announce(ident)
                with suppress(Exception):
                    await asyncio.wait_for(self.runtime.close_run(run['workflow_id']), 5)
        except asyncio.CancelledError:
            # Shutdown leaves a resumable durable run; explicit cancellation updates storage first.
            raise
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            for secret in (os.environ.get('OPENAI_API_KEY'), getattr(self.github, 'token', None)):
                if secret:
                    message = message.replace(secret, '[redacted]')
            self.store.change(ident, status='failed', stage='Run failed', error=message[:4000])
            self.announce(ident)
            with suppress(Exception):
                await asyncio.wait_for(self.runtime.cancel_run(self.store.get(ident)['workflow_id']), 5)

    async def cancel(self, ident: str):
        run = self.store.get(ident)
        if run['status'] in TERMINAL:
            return
        self.store.change(ident, status='cancelled', stage='Cancelled by a collaborator')
        task = self.tasks.get(ident)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        with suppress(Exception):
            await asyncio.wait_for(self.runtime.cancel_run(run['workflow_id']), 5)
        self.announce(ident)
