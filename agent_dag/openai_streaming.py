"""Compatibility for the published harness 0.6.0 Responses stream observer.

That observer eagerly reads ``event.name`` on arguments-done events, even when
the opening function-call item already supplied the name. OpenAI 3.24.0's done
event has no such field. Normalize only the observer's copy via the public
observer-factory hook; keep the raw events collected for workflow replay intact.
Remove this adapter when upgrading to a harness release that fixes the lookup.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Self

from openai.types.responses import (
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemAddedEvent,
)
from temporal_agent_harness.ai_sdks.integration_helpers import StreamObserver
from temporal_agent_harness.ai_sdks.openai_agents_harness import (
    harness_observer_factory as packaged_observer_factory,
)

logger = logging.getLogger(__name__)


class _FunctionCallNameObserver:
    def __init__(self, delegate: StreamObserver[Any]) -> None:
        self._delegate = delegate
        self._names: dict[str, str] = {}

    async def __aenter__(self) -> Self:
        self._delegate = await self._delegate.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool | None:
        try:
            return await self._delegate.__aexit__(exc_type, exc, tb)
        finally:
            self._names.clear()

    async def on_event(self, event: Any) -> None:
        if isinstance(event, ResponseOutputItemAddedEvent):
            item = event.item
            if isinstance(item, ResponseFunctionToolCall):
                self._names[item.id or item.call_id] = item.name
        elif isinstance(event, ResponseFunctionCallArgumentsDoneEvent):
            name = self._names.pop(event.item_id, None) or getattr(event, "name", None)
            if not name:
                # An incomplete stream cannot identify this notification. The activity
                # still collects the original event for the SDK's tool-execution path.
                logger.warning("Skipping unnamed tool-request notification for item %s", event.item_id)
                return
            if not getattr(event, "name", None):
                event = event.model_copy(update={"name": name})
        await self._delegate.on_event(event)


def harness_observer_factory(
    token: Any, *, batch_interval: timedelta | None = None
) -> StreamObserver[Any]:
    """Retain packaged routing, publishing and lifecycle with normalized done events."""
    return _FunctionCallNameObserver(
        packaged_observer_factory(token, batch_interval=batch_interval)
    )
