"""Design references reach real stage inputs without drifting saved runs or experiments."""
from contextlib import contextmanager
import inspect
from types import SimpleNamespace

import pytest

from cooperative.definitions import PROJECT_FILES
from cooperative.design_system import POLICY_PATH, PREFIX, ROOT, reference_files
from cooperative.library import freeze_library, library_files, read_library
from tests.test_cooperative import setup as setup, request, result
from tests.test_experiments import experiment_request


def test_only_written_guidance_is_bundled():
    files = reference_files()
    assert set(files) == {POLICY_PATH, PREFIX + 'foundations.md'}
    assert all(files.values())
    assert not (ROOT / 'snapshot.json').exists()


def test_frozen_guidance_reads_survive_policy_update_and_reject_tampering(monkeypatch):
    feature = {'input_revision': 1, 'pipeline': {'runs': []}}
    library = freeze_library(feature, 'design', {'files': PROJECT_FILES})
    feature['pipeline']['runs'].append({'id': 'design', 'input_revision': 1,
                                       'library': library, 'result': None})
    policy = read_library(feature, POLICY_PATH, 'design')
    monkeypatch.setattr('cooperative.library.reference_files', lambda: {POLICY_PATH: 'New policy'})
    fresh = freeze_library(feature, 'design', {'files': PROJECT_FILES})
    assert fresh['skills'][POLICY_PATH] == 'New policy'
    assert read_library(feature, POLICY_PATH, 'design') == policy
    library['skills'][POLICY_PATH] = 'Tampered policy'
    with pytest.raises(ValueError, match='changed'):
        library_files(feature, 'design')


@pytest.mark.parametrize('module_name', ['stage_agent', 'live_stage'])
@pytest.mark.parametrize('with_policy', [False, True])
async def test_both_model_paths_receive_frozen_policy_with_custom_stage_prompts(
    monkeypatch, module_name, with_policy
):
    from agent_dag import live_stage, stage_agent
    module = {'stage_agent': stage_agent, 'live_stage': live_stage}[module_name]
    context = {'optimization_version': 2, 'project_playbook': 'Custom project playbook',
               'validation_policy': 'Use available checks', 'shared_library': []}
    if with_policy:
        context['design_system_policy'] = 'Frozen example policy for all UI projects'
    project = {'files': {**PROJECT_FILES, 'design_instructions.md': 'Custom Design prompt'}}
    spec = SimpleNamespace(stage='design', context=context, project=project)
    observed = []

    async def run_model(model, model_input, wrapper, max_turns):
        instructions = model.instructions
        if callable(instructions):
            instructions = instructions(wrapper, model)
        observed.append((instructions, model_input))
        return SimpleNamespace(final_output=result(), context_wrapper=wrapper)

    monkeypatch.setattr(module, 'run_with_final_turn', run_model)
    monkeypatch.setattr(module, 'model_tool', lambda runner, tool: tool)
    if module_name == 'stage_agent':
        monkeypatch.setattr(module.workflow, 'patched', lambda _: True)
        monkeypatch.setattr(module, 'stage_model_callbacks', lambda *_: [])
    else:
        monkeypatch.setattr(module, 'stage_model_tools', lambda *_, **__: [])

    class Progress:
        current = SimpleNamespace(model_calls=0, feedback=[],
                                  usage=SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0))

        @contextmanager
        def mutate(self):
            yield self.current

    # Exercise model construction without the harness's active-workflow tool wrapper.
    get_context = inspect.getclosurevars(module.get_stage_context).nonlocals['user_fn']
    run_stage = inspect.getclosurevars(module.run_stage).nonlocals['user_fn']
    args = {'runner': SimpleNamespace(publish=lambda _: None), 'spec': spec,
            'budget': [0], 'context': await get_context(spec), 'focus': 'Design UI'}
    if module_name == 'live_stage':
        args['progress'] = Progress()
    await run_stage(**args)
    instructions, model_input = observed[0]
    assert 'Custom Design prompt' in instructions
    assert 'Custom project playbook' in instructions
    assert ('Frozen example policy' in instructions) == with_policy
    assert 'Frozen example policy' not in model_input
    assert ('do not reread /skills files' in instructions.lower() or
            'do not reread included artifacts or /skills files' in instructions.lower()) != with_policy


async def test_new_runs_can_read_written_foundations_through_host_callback(setup):
    service, root, _, _ = setup
    service.schedule = lambda *_: None
    feature = await service.create(request(root))
    run = feature['pipeline']['runs'][0]
    assert run['context']['design_system_policy'] == run['library']['skills'][POLICY_PATH]
    paths = {entry['path'] for entry in run['context']['shared_library']}
    assert {path for path in paths if path.startswith(PREFIX)} == {
        POLICY_PATH, PREFIX + 'foundations.md'}
    reference = await service.callback(feature['id'], run['id'], 'foundations', 'stage_read_shared_file',
                                       {'path': PREFIX + 'foundations.md'})
    assert reference['content'] == reference_files()[PREFIX + 'foundations.md']


@pytest.mark.parametrize('legacy_catalog', [False, True])
async def test_experiment_arms_keep_captured_references_after_guidance_update(setup, monkeypatch, legacy_catalog):
    service, root, _, _ = setup
    service.schedule = lambda *_: None
    await service.create(request(root), auto_start=False)
    if legacy_catalog:
        legacy_files = {POLICY_PATH: 'Previously saved policy',
                        PREFIX + 'components/example.json': '{"example": true}'}
        monkeypatch.setattr('cooperative.experiments.reference_files', lambda: legacy_files)
    await service.experiments.create(experiment_request())
    experiment = service.experiments.get('compare1')
    frozen = experiment['design_system']
    monkeypatch.setattr('cooperative.library.reference_files', lambda: {POLICY_PATH: 'Updated'})
    for ident in experiment['attempts'].values():
        await service.launch(ident, 'ideation', 'Alice')
        run = service.get(ident)['pipeline']['runs'][0]
        assert run['context']['design_system_policy'] == frozen[POLICY_PATH]
        assert all(run['library']['skills'][p] == c for p, c in frozen.items())
        for path, content in frozen.items():
            assert read_library(service.get(ident), path, run['id'])['content'] == content


def test_explicit_legacy_library_remains_without_design_policy():
    feature = {'input_revision': 1, 'pipeline': {'runs': []}}
    library = freeze_library(feature, 'design', {'files': PROJECT_FILES}, design_files={})
    assert POLICY_PATH not in library['skills']
