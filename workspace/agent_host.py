"""The only callback executor. Browser participants never register callback handlers."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import logging
import os

from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporal_agent_harness.harness.agent_client import AgentClient, CallbackResultError
from temporal_agent_harness.harness.agent_protocol import AgentConfig
from temporal_agent_harness.plugin import AgentHarnessPlugin

from .store import WorkspaceStore

logger = logging.getLogger(__name__)


class AgentHost:
    def __init__(self, store: WorkspaceStore, publish, *, project='review'):
        self.project = project
        self.agent_id = project.split(':', 1)[1] if project.startswith('agent:') else None
        self.workflow_type = 'StudioDesignerAgent' if self.agent_id else 'FeatureDesignerAgent' if project == 'feature' else 'SdlcBuilderAgent'
        self.session_id = store.studio_session_ids[self.agent_id] if self.agent_id else store.feature_builder_session_id if project == 'feature' else store.builder_session_id
        self.store = store
        self.publish = publish
        self.client: AgentClient | None = None
        self.connect_lock = asyncio.Lock()
        self.error: str | None = None

    async def connect(self) -> AgentClient:
        async with self.connect_lock:
            if self.client is None:
                config = ClientConfig.load_client_connect_config()
                config.setdefault('target_host', 'localhost:7233')
                temporal = await Client.connect(**config, plugins=[AgentHarnessPlugin()])
                try:
                    await temporal.start_workflow(self.workflow_type, AgentConfig(),
                                                  id=self.session_id,
                                                  task_queue=os.environ.get('AGENT_DAG_TASK_QUEUE', 'agent-dag'))
                except WorkflowAlreadyStartedError:
                    pass
                self.client = AgentClient(temporal, self.session_id)
            return self.client

    async def prompt(self, ident: str, author: str, text: str):
        extra = {}
        if self.agent_id:
            instructions = self.store.support_instruction_snapshot('studio-designer', self.agent_id)
            extra = {'agent_id': self.agent_id, 'instructions': instructions['content'],
                     'instructions_version': instructions['version']}
        activity = self.store.support_activity.track('studio-designer',
            ident=self.session_id + ':' + ident, success='submitted') if self.agent_id else nullcontext()
        with activity:
            client = await self.connect()
            await client.submit_message('generate', {'text': f'{author}: {text}', **extra}, update_id=ident)

    async def run(self):
        """Poll durable pending callbacks, including callbacks left pending across host restarts."""
        while True:
            try:
                client = await asyncio.wait_for(self.connect(), timeout=8)
                callbacks = await asyncio.wait_for(client.get_pending_callbacks(), timeout=8)
                if self.error:
                    self.error = None
                    self.publish({'type': 'agent-status', 'error': None, 'project': self.project})
                for call in callbacks:
                    try:
                        try:
                            if call.tool_name == 'project_validate':
                                if self.agent_id:
                                    from cooperative.models import validate_project
                                    result = await validate_project(self.agent_id, self.store.agent_project_snapshot(self.agent_id)['files'])
                                elif self.project == 'feature':
                                    from features.project import validate_code
                                    result = await validate_code(self.store.feature_project_snapshot()['files'])
                                else:
                                    from sdlc.runtime import validate_code
                                    result = await validate_code(self.store.project_snapshot()['files'])
                            else:
                                if self.agent_id:
                                    from cooperative.definitions import files_for
                                    PROJECT_FILES = dict.fromkeys(files_for(self.agent_id))
                                elif self.project == 'feature':
                                    from features.project import PROJECT_FILES
                                if self.agent_id or self.project == 'feature':
                                    if call.tool_name != 'project_list_files' and call.tool_input.get('path') not in PROJECT_FILES:
                                        raise ValueError('The designer can edit only the selected agent project files')
                                result = self.store.callback(call.tool_id, call.tool_name, call.tool_input)
                                if (self.agent_id or self.project == 'feature') and call.tool_name == 'project_list_files':
                                    result = {**result, 'files': list(PROJECT_FILES)}
                        except (ValueError, TypeError) as exc:
                            await client.provide_callback_result(call.tool_id, error=str(exc),
                                                                 update_id='workspace:' + call.tool_id)
                            continue
                        # The edit and its receipt are durable BEFORE completing the callback.
                        # Re-broadcast a recovered receipt too: a previous attempt may have
                        # committed before a disk/proxy error prevented delivery to browsers.
                        if 'operation' in result:
                            self.publish({'type': 'operation', 'id': 'callback:' + call.tool_id,
                                          'operation': result['operation'], 'revision': self.store.revision,
                                          'file': result.get('file', 'app.py'),
                                          'events': self.store.events()})
                        else:
                            self.publish({'type': 'events', 'events': self.store.events()})
                        public_result = {key: value for key, value in result.items() if key != 'operation'}
                        await client.provide_callback_result(call.tool_id, result=public_result,
                                                             update_id='workspace:' + call.tool_id)
                    except CallbackResultError as exc:
                        if exc.error_type not in ('CallbackAlreadyResolved', 'UnknownCallback'):
                            raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = 'Builder agent is unavailable. Check Temporal and the worker logs.'
                if self.error != message:
                    logger.warning('Workspace agent connection: %s', exc)
                    self.error = message
                    self.publish({'type': 'agent-status', 'error': message, 'project': self.project})
                await asyncio.sleep(2)
            await asyncio.sleep(0.35)
