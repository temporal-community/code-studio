"""Verify the model's script surface is the frozen tool allow-list, not a shell."""
import json
import pytest

from temporal_agent_harness.harness import agent
from temporal_agent_harness.harness.state import StateRef
from agent_dag.live_stage import stage_model_tools
from cooperative.agent_models import StageState
from cooperative.definitions import PROJECT_FILES, files_for
from cooperative.models import StageSettings
from scripts.generate_agent_types import generate
from agent_dag.live_stage import LiveStageAgentWorkflow
from agent_dag.pipeline_agent import FeaturePipelineAgentWorkflow, next_transition
from cooperative.agent_models import StageOutcome
from pathlib import Path


def settings(stage):
    return StageSettings.model_validate_json(PROJECT_FILES[files_for(stage)[2]])


async def test_code_mode_allows_gathered_reads_but_cannot_expand_permissions():
    tool = stage_model_tools(settings('planner'), None, StateRef('progress', StageState()))[0]
    assert tool.__name__ == 'stage_tools'
    assert await agent.code_mode_type_check(tool, '''import asyncio
async def main():
    files, source = await asyncio.gather(stage_list_files(), stage_read_file("app.py"))
    return source["content"]
asyncio.run(main())''') is None
    for script in ('await stage_write_file("app.py", "", "bad")', 'await create_release_preview()'):
        assert await agent.code_mode_type_check(tool, script)


def test_individual_and_empty_toolsets_are_supported():
    config = settings('development').model_copy(update={'tool_mode': 'individual'})
    assert [tool.__name__ for tool in stage_model_tools(config, None, None)] == config.tools
    assert stage_model_tools(config.model_copy(update={'tools': []}), None, None) == []


def test_client_types_match_current_workflow_schemas():
    for cls in (LiveStageAgentWorkflow, FeaturePipelineAgentWorkflow):
        name, generated = generate(cls)
        assert Path('client_sdk', name + '.ts').read_text() == generated


def test_repair_loop_is_bounded_and_blockers_prevent_approval():
    outcome = StageOutcome(stage='review', run_id='run', verdict='changes_requested', questions=[], summary='Fix tests')
    assert next_transition(outcome, 0) == ('ready', 'development', 1)
    assert next_transition(outcome, 1) == ('ready', 'development', 2)
    assert next_transition(outcome, 2) == ('blocked', 'review', 2)
    assert next_transition(outcome.model_copy(update={'verdict': 'ready', 'questions': ['Missing evidence']}), 0)[0] == 'blocked'
