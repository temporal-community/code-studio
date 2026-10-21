"""Reconnectable local callback host for a durable parent and its stage children."""
import asyncio
import json
import os

from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporal_agent_harness.harness.agent_client import AgentClient, AgentTurnError, AgentTurnTimeout, CallbackResultError
from temporal_agent_harness.harness.agent_protocol import AgentConfig


CALLBACK_POLL_INTERVAL = .25
CALLBACK_WAKE_EVENTS = {'callback_requested', 'subagent_started', 'subagent_stopped',
                        'tool_approval_resolved'}


class PipelineRuntimeMixin:
    supports_pipeline = True

    async def execute_pipeline(self, execution, callback, event, is_paused):
        temporal = await self.connect()
        ident = execution['workflow_id']
        try:
            # Human decisions can wait indefinitely. Model activities retain their own deadlines.
            await temporal.start_workflow('FeaturePipelineAgent', AgentConfig(), id=ident,
                task_queue=os.environ.get('AGENT_DAG_TASK_QUEUE', 'agent-dag'))
        except WorkflowAlreadyStartedError:
            pass
        root = AgentClient(temporal, ident)
        stream = await root.send_message('execute', execution['input'], update_id='execute:' + ident,
            timeout=None, on_item=lambda item, _offset: item)
        for control in execution.get('controls', []):
            await self.control_pipeline(ident, **control)

        wakeup = asyncio.Event()

        async def consume():
            async for item in stream:
                if isinstance(item, (AgentTurnError, AgentTurnTimeout)):
                    raise item
                await event(item)
                if getattr(getattr(item, 'event', None), 'type', None) in CALLBACK_WAKE_EVENTS:
                    wakeup.set()

        async def fulfill(client, call):
            try:
                try:
                    result = await callback(client.workflow_id, call.tool_id, call.tool_name, call.tool_input)
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    await client.provide_callback_result(call.tool_id, error=str(exc), update_id='result:' + call.tool_id)
                else:
                    if call.tool_name == 'save_stage_result':
                        result = json.dumps(result)
                    await client.provide_callback_result(call.tool_id, result=result, update_id='result:' + call.tool_id)
            except CallbackResultError as exc:
                if exc.error_type not in ('UnknownCallback', 'CallbackAlreadyResolved'):
                    raise

        reader = asyncio.create_task(consume())
        reader.add_done_callback(lambda _: wakeup.set())
        pending = {}
        try:
            while not reader.done():
                # Events and callback completions trigger a fresh authoritative query.
                # Keep polling as a fallback for reconnects, paused runs, and stream gaps.
                wakeup.clear()
                async with asyncio.timeout(10):
                    status = await root.get_status()
                clients = [(root, status.pending_callbacks)]
                for child in status.subagents:
                    client = AgentClient(temporal, child.workflow_id)
                    try:
                        async with asyncio.timeout(10):
                            calls = await client.get_pending_callbacks()
                    except RPCError as exc:
                        if exc.status != RPCStatusCode.NOT_FOUND:
                            raise
                        continue  # Parent cleanup can lag a child that already closed.
                    clients.append((client, calls))
                for key, task in list(pending.items()):
                    if task.done():
                        del pending[key]
                        await task
                if not is_paused():
                    for client, calls in clients:
                        for call in calls:
                            key = (client.workflow_id, call.tool_id)
                            if key not in pending:
                                pending[key] = asyncio.create_task(fulfill(client, call))
                                pending[key].add_done_callback(lambda _: wakeup.set())
                try:
                    # Python 3.11 wait_for can swallow host cancellation if its
                    # inner task finishes on the same tick. Keep this in our task.
                    async with asyncio.timeout(CALLBACK_POLL_INTERVAL):
                        await wakeup.wait()
                except TimeoutError:
                    pass
            await reader
            if pending:
                await asyncio.gather(*pending.values())
        finally:
            for task in [reader, *pending.values()]:
                task.cancel()
            await asyncio.gather(reader, *pending.values(), return_exceptions=True)

    async def pending_approvals(self, workflow_id):
        return await AgentClient(await self.connect(), workflow_id).get_pending_approvals()

    async def resolve_approval(self, workflow_id, request):
        return await AgentClient(await self.connect(), workflow_id).approve_tool(request['tool_id'],
            approved=request['approved'], reason=request.get('reason') or None, remember=False,
            update_id='approval:' + request['id'])

    async def control_pipeline(self, workflow_id, action, ident, text='', sequence=0):
        return await AgentClient(await self.connect(), workflow_id).submit_message('control',
            {'action': action, 'text': text, 'sequence': sequence}, update_id='control:' + ident)

    async def stage_progress(self, workflow_id):
        temporal = await self.connect()
        return await temporal.get_workflow_handle(workflow_id).query('progress_snapshot')
