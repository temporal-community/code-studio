from datetime import timedelta
import json
import uuid

from temporalio.worker import Worker
from temporal_agent_harness.ai_sdks.openai_agents import ModelActivityParameters
from temporal_agent_harness.ai_sdks.openai_agents.testing import AgentEnvironment

from agent_dag.clarification import StageClarificationWorkflow
from cooperative.clarification import ClarificationInput
from cooperative.runtime import StageRuntime
from tests.test_suggestion_workflow import SuggestionModel
from tests.test_clarification import REPLY


async def test_explanation_is_one_read_only_call_and_reconnect_does_not_repeat_it(temporal_client, monkeypatch):
    # Reuse the fake model transport while returning this workflow's structured output.
    monkeypatch.setattr('tests.test_suggestion_workflow.recommendation', lambda: REPLY)
    model = SuggestionModel()
    async with AgentEnvironment(model=model, model_params=ModelActivityParameters(start_to_close_timeout=timedelta(seconds=15))) as agents:
        client = agents.applied_on_client(temporal_client)
        queue = 'clarification-test-' + uuid.uuid4().hex
        monkeypatch.setenv('AGENT_DAG_TASK_QUEUE', queue)
        async with Worker(client, task_queue=queue, workflows=[StageClarificationWorkflow]):
            runtime = StageRuntime(); runtime.temporal = client
            spec = ClarificationInput(model='gpt-6-luna', context={'stage': 'design',
                'question': 'Explain pairing with an example.', 'stage_result': {'questions': ['How should entries be paired?']},
                'conversation': [{'text': 'What is a stable ID?', 'answer': 'An identifier that does not change.'}]})
            ident = uuid.uuid4().hex
            first = await runtime.explain_questions(ident, spec)
            assert first.model_dump() == REPLY
            assert await runtime.explain_questions(ident, spec) == first
    assert len(model.calls) == 1 and not model.calls[0]['tools']
    sent = json.loads(model.calls[0]['input'][0]['content'])
    assert sent['conversation'] == spec.context['conversation']
    assert 'plain language' in model.calls[0]['instructions']
