"""Callback notifications reduce latency without replacing authoritative queries."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cooperative import harness_runtime


def call(ident):
    return SimpleNamespace(tool_id=ident, tool_name='read', tool_input={})


@pytest.fixture
def host(monkeypatch):
    messages = asyncio.Queue()
    queried = asyncio.Event()
    calls = []

    async def stream():
        while (item := await messages.get()) is not None:
            yield item

    async def status():
        queried.set()
        return SimpleNamespace(pending_callbacks=list(calls), subagents=[])

    async def provided(ident, **kwargs):
        calls[:] = [c for c in calls if c.tool_id != ident]

    client = SimpleNamespace(workflow_id='root', send_message=AsyncMock(return_value=stream()),
                             get_status=AsyncMock(side_effect=status),
                             provide_callback_result=AsyncMock(side_effect=provided))
    monkeypatch.setattr(harness_runtime, 'AgentClient', lambda *_: client)
    # A missed notification must fail the short test deadline, not hide in polling.
    monkeypatch.setattr(harness_runtime, 'CALLBACK_POLL_INTERVAL', 60)
    runtime = harness_runtime.PipelineRuntimeMixin()
    runtime.connect = AsyncMock(return_value=SimpleNamespace(start_workflow=AsyncMock()))

    async def execute(callback, paused=lambda: False):
        await runtime.execute_pipeline({'workflow_id': 'root', 'input': {}}, callback, AsyncMock(), paused)

    return SimpleNamespace(messages=messages, queried=queried, calls=calls, client=client, execute=execute)


@pytest.mark.parametrize('notify', [True, False])
async def test_new_callbacks_wake_host_with_polling_fallback(host, monkeypatch, notify):
    if not notify:
        monkeypatch.setattr(harness_runtime, 'CALLBACK_POLL_INTERVAL', .01)
    callback = AsyncMock(return_value='answer')
    task = asyncio.create_task(host.execute(callback))
    try:
        await asyncio.wait_for(host.queried.wait(), 1)
        host.calls.append(call('one'))
        if notify:
            host.messages.put_nowait(SimpleNamespace(event=SimpleNamespace(type='callback_requested')))
        async with asyncio.timeout(1):
            while not host.client.provide_callback_result.call_count:
                await asyncio.sleep(0)
        host.messages.put_nowait(None)
        await asyncio.wait_for(task, 1)
        callback.assert_awaited_once_with('root', 'one', 'read', {})
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_callback_completion_wakes_host_without_a_stream_event(host):
    host.calls.append(call('one'))

    async def provided(ident, **kwargs):
        host.calls.clear()
        if ident == 'one':
            host.calls.append(call('two'))
        else:
            host.messages.put_nowait(None)

    host.client.provide_callback_result.side_effect = provided
    callback = AsyncMock(return_value='answer')
    await asyncio.wait_for(host.execute(callback), 1)
    assert [args.args[1] for args in callback.await_args_list] == ['one', 'two']


async def test_notifications_respect_pause_and_cancellation_cleans_pending_callbacks(host):
    paused = True
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def callback(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    host.calls.append(call('one'))
    task = asyncio.create_task(host.execute(callback, lambda: paused))
    try:
        await asyncio.wait_for(host.queried.wait(), 1)
        host.queried.clear()
        host.messages.put_nowait(SimpleNamespace(event=SimpleNamespace(type='callback_requested')))
        await asyncio.wait_for(host.queried.wait(), 1)
        assert not started.is_set()
        paused = False
        host.messages.put_nowait(SimpleNamespace(event=SimpleNamespace(type='tool_approval_resolved')))
        await asyncio.wait_for(started.wait(), 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert cancelled.is_set()
    host.client.provide_callback_result.assert_not_awaited()


async def test_stop_is_not_lost_when_status_query_completes_on_same_tick(host):
    async def status():
        task.cancel()
        host.messages.put_nowait(None)
        return SimpleNamespace(pending_callbacks=[], subagents=[])

    host.client.get_status.side_effect = status
    task = asyncio.create_task(host.execute(AsyncMock()))
    with pytest.raises(asyncio.CancelledError):
        await task
