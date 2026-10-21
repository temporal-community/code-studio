import copy

import pytest

from cooperative.library import artifacts, freeze_library, library_files, read_library
from cooperative.definitions import PROJECT_FILES
from tests.test_cooperative import result
from agent_dag.live_stage import stage_model_tools
from tests.test_stage_code_mode import settings
from temporal_agent_harness.harness import agent


def saved(run_id, scope=1):
    return {'id': run_id, 'stage': 'planner', 'input_revision': scope,
            'status': 'completed', 'result': result()}


def test_frozen_inputs_preserve_skills_and_exclude_future_or_other_scope_artifacts():
    old = saved('old', 1); prior = saved('prior', 2)
    feature = {'input_revision': 2, 'pipeline': {'runs': [old, prior]}}
    project = {'files': copy.deepcopy(PROJECT_FILES)}
    library = freeze_library(feature, 'design', project)
    current = {'id': 'current', 'stage': 'design', 'input_revision': 2,
               'status': 'running', 'result': None, 'library': library}
    feature['pipeline']['runs'].append(current)
    project['files']['design_instructions.md'] = 'Changed after start'
    feature['pipeline']['runs'].append(saved('future', 2))
    files = library_files(feature, 'current')
    assert files['/skills/design/SKILL.md'] == PROJECT_FILES['design_instructions.md']
    assert '/artifacts/scope-2/planner/prior/result.json' in files
    assert not any('/old/' in path or '/future/' in path for path in files)
    assert '/artifacts/scope-1/planner/old/result.json' in library_files(feature)
    assert '/skills/runs/current/design/SKILL.md' in library_files(feature)
    for path in ('/etc/passwd', '/skills/../source/app.py', '/artifacts/scope-2/planner/future/result.json'):
        with pytest.raises(ValueError, match='Unknown shared file'):
            read_library(feature, path, 'current')
    prior['result']['summary'] = 'Tampered with'
    with pytest.raises(ValueError, match='changed'):
        library_files(feature, 'current')


def test_artifact_names_do_not_use_model_supplied_titles_as_paths():
    run = saved('run')
    run['result']['deliverables'][0]['title'] = '../../outside'
    files = artifacts({'pipeline': {'runs': [run]}})
    assert len(files) == 3
    assert files['/artifacts/scope-1/planner/run/deliverable-01.md'].startswith('# ../../outside')
    assert all('..' not in path for path in files)


async def test_shared_tools_are_typed_read_only_and_opt_in_for_new_runs():
    config = settings('planner')
    tool = stage_model_tools(config, None, None, shared=True)[0]
    assert await agent.code_mode_type_check(tool, '''
files = await stage_list_shared_files()
skill = await stage_read_shared_file("/skills/project/SKILL.md")
print(skill["content"])
''') is None
    assert await agent.code_mode_type_check(tool, 'await stage_write_shared_file("/skills/project/SKILL.md", "bad")')
    old = stage_model_tools(config, None, None)[0]
    assert await agent.code_mode_type_check(old, 'await stage_list_shared_files()')


def test_library_http_routes_expose_saved_files_and_frozen_inputs():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from cooperative.api import work_routes
    feature = {'input_revision': 1, 'pipeline': {'runs': [saved('prior')]}}
    frozen = freeze_library(feature, 'design', {'files': PROJECT_FILES})
    feature['pipeline']['runs'].append({'id': 'reader', 'stage': 'design', 'result': None,
        'input_revision': 1, 'library': frozen})
    app = FastAPI()
    app.include_router(work_routes(lambda: SimpleNamespace(get=lambda _ident: feature)))
    with TestClient(app) as client:
        listing = client.get('/workspace/work/example/library', params={'run_id': 'reader'})
        assert listing.status_code == 200
        paths = [item['path'] for item in listing.json()['files']]
        assert '/skills/design/SKILL.md' in paths
        path = '/artifacts/scope-1/planner/prior/deliverable-01.md'
        response = client.get('/workspace/work/example/library/file', params={'run_id': 'reader', 'path': path})
        assert response.status_code == 200
        assert response.json()['content'] == '# Handoff\n\nContinue the approved feature.\n'
