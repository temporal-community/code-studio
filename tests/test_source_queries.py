from pathlib import Path

import pytest

from cooperative.source_queries import read_range, search
from agent_dag.live_stage import stage_model_tools
from agent_dag.stage_agent import stage_model_callbacks
from temporal_agent_harness.harness import agent
from tests.test_stage_code_mode import settings
from tests.test_cooperative import setup, request


async def test_queries_bound_results_and_preserve_source_permissions(setup):
    p, root, _, _ = setup
    f = await p.create(request(root), auto_start=False)
    Path(f['worktree'], 'app.py').write_text('needle\n' * 300)
    page = read_range(p.git, f, 'app.py', 20, 10)
    assert page['partial'] and page['total_lines'] == 300
    assert page['content'] == 'needle\n' * 10
    assert page['hash'] == p.git.read(f, 'app.py')['hash']
    found = search(p.git, f, 'needle', '*.py', 2)
    assert found['truncated'] and [m['line'] for m in found['matches']] == [1, 2]
    for path in ('../outside', '/etc/passwd', '.env.local', 'node_modules/skip.js'):
        with pytest.raises(ValueError): read_range(p.git, f, path)
    with pytest.raises(ValueError): read_range(p.git, f, 'app.py', 0)
    with pytest.raises(ValueError): search(p.git, f, '', '*')
    with pytest.raises(ValueError): search(p.git, f, 'needle', '../*')


async def test_optimized_tools_are_typed_and_do_not_change_frozen_old_tools():
    code = 'hits = await stage_search_source("answer", "*", 30)\npage = await stage_read_range("app.py", 1, 20)\nprint(page["content"])'
    config = settings('planner')
    new = stage_model_tools(config, None, None, optimized=True)[0]
    assert await agent.code_mode_type_check(new, code) is None
    old = stage_model_tools(config, None, None)[0]
    assert await agent.code_mode_type_check(old, code)
    config.tools = ['stage_list_files']
    restricted = stage_model_tools(config, None, None, optimized=True)[0]
    assert await agent.code_mode_type_check(restricted, code)


async def test_focused_checks_and_force_are_available_only_on_new_contract():
    config = settings('development')
    code = 'evidence = await stage_run_check("syntax", ["app.py"], False)\nprint(evidence["cached"])'
    new = stage_model_tools(config, None, None, optimized=True)[0]
    assert await agent.code_mode_type_check(new, code) is None
    old = stage_model_tools(config, None, None)[0]
    assert await agent.code_mode_type_check(old, code)


@pytest.mark.parametrize('stage', ['ideation', 'planner', 'design', 'development', 'review', 'deployment'])
async def test_legacy_new_runs_expose_queries_and_shared_reads_without_granting_writes(stage):
    config = settings(stage)
    context = {'optimization_version': 2, 'shared_library': []}
    tools = stage_model_callbacks(config, context)
    names = [tool.__name__ for tool in tools]
    assert {'stage_read_range', 'stage_search_source', 'stage_read_shared_file', 'stage_list_shared_files'} <= set(names)
    assert ('stage_write_file' in names) == (stage == 'development')
    # Legacy execution uses individual model callbacks. Validate the new typed
    # callbacks separately; its original dict-returning callbacks are unchanged.
    from temporal_agent_harness.ai_sdks.openai_agents_harness import as_openai_agent_tool
    assert [as_openai_agent_tool(None, tool).name for tool in tools] == names
    typed = agent.code_mode_tool([tool for tool in tools if tool.__name__ not in config.tools], name='queries')
    assert await agent.code_mode_type_check(typed, '''
hits = await stage_search_source("answer", "*.py", 5)
page = await stage_read_range("app.py", 1, 10)
artifact = await stage_read_shared_file("/artifacts/scope-1/planner/prior/result.json")
print(artifact["content"])
''') is None
    # Frozen old inputs retain their original tool contract on replay.
    assert [t.__name__ for t in stage_model_callbacks(config, {})] == config.tools
    assert [t.__name__ for t in stage_model_callbacks(config, {'optimization_version': 1})] == config.tools


def test_legacy_source_query_permissions_require_full_read_permission():
    config = settings('planner').model_copy(update={'tools': ['stage_list_files']})
    assert [t.__name__ for t in stage_model_callbacks(config, {'optimization_version': 2})] == ['stage_list_files']
