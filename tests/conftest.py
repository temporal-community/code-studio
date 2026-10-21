"""Share the Temporal server, while keeping clients, workers, and workflows isolated."""
import asyncio
import shutil

import pytest_asyncio
from temporalio.client import Client, Interceptor, OutboundInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def temporal_server():
    # One ephemeral server per pytest process, never the developer's running server.
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which('temporal'),
    ) as env:
        yield env.client.service_client.config.target_host


class WorkflowTracker(Interceptor):
    def __init__(self):
        self.handles = []

    def intercept_client(self, next):
        handles = self.handles

        class Tracker(OutboundInterceptor):
            async def start_workflow(self, input):
                handle = await super().start_workflow(input)
                handles.append(handle)
                return handle

        return Tracker(next)


async def terminate_tree(client, handle):
    """Clean up parked gates and abandoned children, even after a failed assertion.

    Read histories instead of eventually consistent visibility queries. Terminate
    the parent first so it cannot start another child while we traverse the tree.
    """
    try:
        await handle.terminate('Integration test teardown')
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
    history = await handle.fetch_history()
    children = [event.child_workflow_execution_started_event_attributes.workflow_execution
                for event in history.events if event.HasField('child_workflow_execution_started_event_attributes')]
    for child in children:
        await terminate_tree(client, client.get_workflow_handle(child.workflow_id, run_id=child.run_id))


@pytest_asyncio.fixture
async def temporal_client(temporal_server):
    # Connect on each test's event loop; don't share loop-bound clients or workers.
    tracker = WorkflowTracker()
    client = await Client.connect(temporal_server, interceptors=[tracker])
    try:
        yield client
    finally:
        async with asyncio.timeout(20):
            for handle in tracker.handles:
                await terminate_tree(client, handle)


@pytest_asyncio.fixture
async def harness_client(temporal_client):
    # The model plugin installs its own converter on temporal_client; harness-only
    # workflows use Pydantic. Both clients retain the same teardown tracker.
    return Client(**{**temporal_client.config(), 'data_converter': pydantic_data_converter})
