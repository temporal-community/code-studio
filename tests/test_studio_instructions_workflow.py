"""Saved support instructions reach each turn of an existing Studio conversation."""
import asyncio
from datetime import timedelta
import uuid

from temporalio.worker import Worker
from temporal_agent_harness.ai_sdks.openai_agents import ModelActivityParameters
from temporal_agent_harness.ai_sdks.openai_agents.testing import AgentEnvironment
from temporal_agent_harness.harness.agent_client import AgentClient, AgentTurnError
from temporal_agent_harness.harness.agent_protocol import AgentConfig
from temporal_agent_harness.plugin import AgentHarnessPlugin

from agent_dag.studio_designer import StudioDesignerAgentWorkflow, designer_instructions
from tests.test_suggestion_workflow import SuggestionModel


async def test_existing_studio_conversation_uses_each_messages_captured_prompt(temporal_client):
    model = SuggestionModel()
    async with AgentEnvironment(model=model, model_params=ModelActivityParameters(start_to_close_timeout=timedelta(seconds=15))) as agents:
        client = agents.applied_on_client(temporal_client)
        queue = 'studio-instructions-' + uuid.uuid4().hex
        async with Worker(client, task_queue=queue, workflows=[StudioDesignerAgentWorkflow], plugins=[AgentHarnessPlugin()]):
            handle = await client.start_workflow('StudioDesignerAgent', AgentConfig(), id=uuid.uuid4().hex, task_queue=queue)
            try:
                for instructions in ['First saved instructions', 'Updated instructions for the next request', None]:
                    payload = {'agent_id': 'ideation', 'text': 'Explain your approach.'}
                    if instructions is not None:
                        payload.update(instructions=instructions, instructions_version=uuid.uuid4().hex)
                    async with asyncio.timeout(25):
                        stream = await AgentClient(client, handle.id).send_message('generate', payload,
                            on_item=lambda item, _offset: item)
                        async for item in stream:
                            assert not isinstance(item, AgentTurnError), str(item)
            finally:
                await handle.terminate('Test complete')
    assert [call['instructions'] for call in model.calls] == [
        'First saved instructions', 'Updated instructions for the next request', designer_instructions('ideation'),
    ]
    assert all(len(call['tools']) == 4 for call in model.calls)
