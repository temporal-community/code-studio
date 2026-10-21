"""Replay real SDK event shapes through the worker's configured observer; no API/server."""

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from openai.types.responses import (
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemAddedEvent,
    ResponseTextDeltaEvent,
)
from temporal_agent_harness.harness.agent_protocol import (
    ModelInteractionEnded,
    ModelInteractionStarted,
    ReplyDelta,
    ToolRequested,
)
from temporal_agent_harness.harness.agent_workflow import AgentWorkflowRunner

from agent_dag.worker import harness_observer_factory

TOKEN = {"context": {"turn_id": "turn-1", "turn_number": 1, "agent_id": "builder"},
         "model": "gpt-6-luna"}


@pytest.fixture
def publisher(monkeypatch):
    sink = SimpleNamespace(events=[], opens=[], closes=[])
    sink.publish = sink.events.append

    @asynccontextmanager
    async def open_publisher(context, **kwargs):
        sink.opens.append((context, kwargs))
        try:
            yield sink
        finally:
            sink.closes.append(True)

    monkeypatch.setattr(AgentWorkflowRunner, "publisher_from_activity", staticmethod(open_publisher))
    return sink


def added(item_id="item-1", call_id="call-1", name="write_code", index=0):
    return ResponseOutputItemAddedEvent(
        type="response.output_item.added", sequence_number=1, output_index=index,
        item=ResponseFunctionToolCall(type="function_call", id=item_id, call_id=call_id,
                                      name=name, arguments=""),
    )


def done(item_id="item-1", arguments='{"code":"print(1)"}', **extra):
    # Deliberately no name: this is the SDK/API shape that crashed the live worker.
    return ResponseFunctionCallArgumentsDoneEvent.model_validate({
        "type": "response.function_call_arguments.done", "sequence_number": 3,
        "output_index": 0, "item_id": item_id, "arguments": arguments, **extra,
    })


def requests(publisher):
    return [event for event in publisher.events if isinstance(event, ToolRequested)]


async def test_tool_call_without_done_name_preserves_identity_and_lifecycle(publisher):
    event = done()
    assert not hasattr(event, "name")
    interval = timedelta(milliseconds=250)
    async with harness_observer_factory(TOKEN, batch_interval=interval) as observer:
        await observer.on_event(added())
        await observer.on_event(event)

    [request] = requests(publisher)
    assert (request.tool_id, request.tool_name, request.tool_input) == (
        "call-1", "write_code", {"code": "print(1)"},
    )
    assert not hasattr(event, "name"), "The raw event also feeds the workflow; leave it untouched."
    assert isinstance(publisher.events[0], ModelInteractionStarted)
    assert publisher.events[0].model == TOKEN["model"]
    assert isinstance(publisher.events[-1], ModelInteractionEnded)
    assert publisher.opens[0][1]["batch_interval"] == interval
    assert publisher.closes == [True]


async def test_interleaved_calls_keep_their_own_names_and_call_ids(publisher):
    async with harness_observer_factory(TOKEN) as observer:
        await observer.on_event(added("first", "call-first", "read_code"))
        await observer.on_event(added("second", "call-second", "write_code", index=1))
        await observer.on_event(done("second"))
        await observer.on_event(done("first", "{}"))
    assert [(r.tool_id, r.tool_name) for r in requests(publisher)] == [
        ("call-second", "write_code"), ("call-first", "read_code"),
    ]


@pytest.mark.parametrize("arguments,expected", [
    ('{"code":"final"}', {"code": "final"}),
    ("", {"code": "buffered"}),
    ("not-json", {}),
])
async def test_harness_argument_parsing_is_preserved(publisher, arguments, expected):
    async with harness_observer_factory(TOKEN) as observer:
        await observer.on_event(added())
        await observer.on_event(ResponseFunctionCallArgumentsDeltaEvent(
            type="response.function_call_arguments.delta", sequence_number=2,
            output_index=0, item_id="item-1", delta='{"code":"buffered"}',
        ))
        await observer.on_event(done(arguments=arguments))
    assert requests(publisher)[0].tool_input == expected


async def test_named_done_event_without_opening_still_works(publisher):
    async with harness_observer_factory(TOKEN) as observer:
        await observer.on_event(done(name="write_code"))
    [request] = requests(publisher)
    assert (request.tool_id, request.tool_name) == ("item-1", "write_code")


async def test_unidentifiable_notification_does_not_crash_the_stream(publisher, caplog):
    async with harness_observer_factory(TOKEN) as observer:
        await observer.on_event(done())
        await observer.on_event(ResponseTextDeltaEvent(
            type="response.output_text.delta", sequence_number=4, output_index=0,
            content_index=0, item_id="message-1", delta="Still streaming", logprobs=[],
        ))
    assert requests(publisher) == []
    assert "item-1" in caplog.text
    assert [e.text for e in publisher.events if isinstance(e, ReplyDelta)] == ["Still streaming"]


async def test_stream_errors_propagate_and_close_publisher(publisher):
    with pytest.raises(RuntimeError, match="stream disconnected"):
        async with harness_observer_factory(TOKEN):
            raise RuntimeError("stream disconnected")
    assert publisher.closes == [True]
    assert isinstance(publisher.events[-1], ModelInteractionEnded)
