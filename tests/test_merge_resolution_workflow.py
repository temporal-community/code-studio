"""Exercise the real workflow and model activity with a fake model, without an API key."""
from datetime import timedelta
import json
import uuid
import pytest

from temporalio.worker import Worker
from temporal_agent_harness.ai_sdks.openai_agents import ModelActivityParameters
from temporal_agent_harness.ai_sdks.openai_agents.testing import AgentEnvironment
from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from agent_dag.merge_resolution import MergeResolutionWorkflow
from cooperative.merge_resolution import MergeResolution, MergeResolutionInput
def recommendation():
    return {'choice': 'edit', 'content': 'local = True\nfeature = True\n', 'summary': 'Preserved both changes.'}


class MergeModel(Model):
    def __init__(self): self.calls = []

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, **kwargs):
        self.calls.append({'instructions': system_instructions, 'input': input, 'tools': tools})
        return ModelResponse(output=[ResponseOutputMessage(id='message', type='message', role='assistant', status='completed',
            content=[ResponseOutputText(type='output_text', text=json.dumps(recommendation()), annotations=[])])],
            usage=Usage(requests=1, input_tokens=10, output_tokens=10, total_tokens=20), response_id='response')

    def stream_response(self, *args, **kwargs): raise NotImplementedError


@pytest.mark.parametrize('instructions', [None, 'Custom resolver instructions: explain API compatibility.'])
async def test_merge_resolution_runs_as_read_only_structured_call_in_temporal(temporal_client, instructions):
    model = MergeModel()
    async with AgentEnvironment(model=model, model_params=ModelActivityParameters(start_to_close_timeout=timedelta(seconds=15))) as agents:
        client = agents.applied_on_client(temporal_client)
        queue = 'suggestion-test-' + uuid.uuid4().hex
        async with Worker(client, task_queue=queue, workflows=[MergeResolutionWorkflow]):
            result = await client.execute_workflow(MergeResolutionWorkflow.run,
                MergeResolutionInput(model='gpt-6-luna', instructions=instructions, context={'conflict': {'path': 'app.py', 'local': {'content': 'local = True'}}}),
                id=uuid.uuid4().hex, task_queue=queue, execution_timeout=timedelta(seconds=30))
    assert result == MergeResolution(**recommendation())
    assert len(model.calls) == 1
    assert not model.calls[0]['tools']
    assert 'local = True' in str(model.calls[0]['input'])
    if instructions is not None:
        assert model.calls[0]['instructions'] == instructions
