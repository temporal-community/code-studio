"""Exercise budget exhaustion through the real Agents SDK, without model or server access."""
import json

import pytest
from agents import Agent, Model, RunContextWrapper, function_tool, trace
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from agent_dag.stage_agent import run_with_final_turn
from cooperative.definitions import PROJECT_FILES
from cooperative.models import StageResult
from tests.test_cooperative import result
from tests.test_feature_project import replace
from workspace.store import WorkspaceStore


class ExploringModel(Model):
    def __init__(self, *, finish_early=False, fail=False, blocked=False):
        self.calls = []; self.finish_early = finish_early; self.fail = fail; self.blocked = blocked

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, **kwargs):
        self.calls.append({'input': input, 'tools': tools})
        if self.fail: raise RuntimeError('Model connection failed')
        index = len(self.calls)
        if tools and not self.finish_early:
            output = [ResponseFunctionToolCall(type='function_call', call_id=f'call-{index}', name='inspect_file', arguments=json.dumps({'path': f'file-{index}.py'}))]
        else:
            artifact = result()
            if self.blocked:
                artifact.update(verdict='blocked', questions=['Implementation still needs tests.'], changed_files=['file-1.py'])
            output = [ResponseOutputMessage(id=f'message-{index}', type='message', role='assistant', status='completed',
                content=[ResponseOutputText(type='output_text', text=json.dumps(artifact), annotations=[])])]
        return ModelResponse(output=output, usage=Usage(requests=1, input_tokens=10, output_tokens=5, total_tokens=15), response_id=f'response-{index}')

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


@pytest.mark.parametrize('limit', [2, 12, 24])
async def test_reserves_final_turn_and_keeps_every_completed_tool_result(limit):
    calls = []
    @function_tool
    async def inspect_file(path: str) -> str:
        calls.append(path)
        return 'Observed source for ' + path
    model = ExploringModel()
    model_agent = Agent(name='Planner', model=model, tools=[inspect_file], output_type=StageResult)
    context = RunContextWrapper(context={})
    with trace('budget-test', disabled=True):
        output = await run_with_final_turn(model_agent, 'Inspect the feature', context, limit)
    assert isinstance(output.final_output, StageResult)
    assert output.final_output.verdict == 'ready'
    assert len(model.calls) == limit
    assert len(calls) == limit - 1
    assert len(set(calls)) == len(calls)  # No exploration or side effects replayed.
    assert model.calls[-1]['tools'] == []
    history = model.calls[-1]['input']
    assert history[0]['content'] == 'Inspect the feature'
    assert [item['output'] for item in history if item.get('type') == 'function_call_output'] == ['Observed source for ' + path for path in calls]
    assert 'blocked' in history[-1]['content']
    assert context.usage.requests == limit
    assert context.usage.total_tokens == limit * 15


async def test_normal_completion_uses_no_extra_turn_and_unrelated_errors_are_not_retried():
    model = ExploringModel(finish_early=True)
    with trace('budget-test', disabled=True):
        await run_with_final_turn(Agent(name='Planner', model=model, output_type=StageResult), 'Plan', RunContextWrapper(context={}), 12)
    assert len(model.calls) == 1
    model = ExploringModel(fail=True)
    with trace('budget-test', disabled=True), pytest.raises(RuntimeError, match='connection failed'):
        await run_with_final_turn(Agent(name='Planner', model=model, output_type=StageResult), 'Plan', RunContextWrapper(context={}), 12)
    assert len(model.calls) == 1


async def test_finalization_keeps_incomplete_work_blocked_and_written_paths():
    @function_tool
    async def inspect_file(path: str) -> str:
        return 'Written file-1.py; tests not implemented'
    model = ExploringModel(blocked=True)
    with trace('budget-test', disabled=True):
        output = await run_with_final_turn(Agent(name='Development', model=model, tools=[inspect_file], output_type=StageResult), 'Implement', RunContextWrapper(context={}), 2)
    assert output.final_output.verdict == 'blocked'
    assert output.final_output.changed_files == ['file-1.py']
    assert output.final_output.questions


def old_settings():
    config = json.loads(PROJECT_FILES['planner_settings.json']); config['max_turns'] = 12
    return json.dumps(config, indent=2) + '\n'


def test_old_untouched_planner_is_migrated_once_and_edit_anchors_are_preserved(tmp_path):
    (tmp_path / 'planner_settings.json').write_text(old_settings())
    store = WorkspaceStore(tmp_path)
    assert json.loads(store.file('planner_settings.json').text)['max_turns'] == 24
    assert any(':seed:' in key for key in store.file('planner_settings.json').nodes)
    assert store.events()[-1]['kind'] == 'migration'
    revision = store.revision; version = store.agent_project_snapshot('planner')['version']; store.close()
    store = WorkspaceStore(tmp_path)
    assert store.revision == revision; assert store.agent_project_snapshot('planner')['version'] == version
    store.close()


def test_customized_planner_budget_is_preserved_even_when_restored_to_old_value(tmp_path):
    store = WorkspaceStore(tmp_path)
    replace(store, 'planner_settings.json', old_settings())
    revision = store.revision; store.close()
    store = WorkspaceStore(tmp_path)
    assert json.loads(store.file('planner_settings.json').text)['max_turns'] == 12
    assert store.revision == revision
    store.close()


def test_default_upgrade_merges_with_an_offline_edit_to_another_setting(tmp_path, monkeypatch):
    (tmp_path / 'planner_settings.json').write_text(old_settings())
    with monkeypatch.context() as patch:
        patch.setattr(WorkspaceStore, '_upgrade_planner_defaults', lambda _: None)
        store = WorkspaceStore(tmp_path)
        doc = store.file('planner_settings.json')
        start = doc.text.index('Planner')
        pending = doc.replace(start, start + len('Planner'), 'Team Planner', 'offline-edit')
        store.close()
    store = WorkspaceStore(tmp_path)
    store.edit('offline-edit', pending, 'Bob', file='planner_settings.json')
    settings = json.loads(store.file('planner_settings.json').text)
    assert settings['name'] == 'Team Planner'
    assert settings['max_turns'] == 24
    store.close()
