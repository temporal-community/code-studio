"""An experiment pair uses real Temporal children and keeps separate native gates."""
from pathlib import Path

from tests.test_pipeline_harness import durable, waiting, decision
from tests.test_cooperative import request
from tests.test_experiments import experiment_request


async def test_pair_runs_frozen_playbooks_and_keeps_approvals_independent(durable):
    service, root, _, _ = durable
    original = await service.create(request(root))
    await waiting(service, original['id'], 'planner')
    created = await service.experiments.create(experiment_request())
    await service.experiments.start(created['id'], 'Alice')
    baseline_id = created['attempts']['baseline']['id']
    variant_id = created['attempts']['variant']['id']
    baseline_gate = await waiting(service, baseline_id, 'planner')
    await waiting(service, variant_id, 'planner')
    baseline = service.get(baseline_id); variant = service.get(variant_id)
    assert baseline['pipeline']['execution']['workflow_id'] != variant['pipeline']['execution']['workflow_id']
    assert baseline['pipeline']['runs'][0]['project'] == variant['pipeline']['runs'][0]['project']
    assert baseline['pipeline']['runs'][0]['library']['skills']['/skills/project/SKILL.md'] != variant['pipeline']['runs'][0]['library']['skills']['/skills/project/SKILL.md']
    await service.approve_managed(baseline_id, decision(baseline_gate, 'baseline-approve'))
    await waiting(service, baseline_id, 'review')
    assert service.get(variant_id)['pipeline']['status'] == 'needs_plan_approval'
    assert 'return 0' in Path(variant['worktree'], 'app.py').read_text()
    comparison = await service.experiments.compare(created['id'])
    assert comparison['attempts']['baseline']['checks']['syntax']['current']
    assert comparison['attempts']['baseline']['checks']['syntax']['status'] == 'passed'
    assert 'return 42' in comparison['attempts']['baseline']['diff']['patch']
    assert not comparison['attempts']['variant']['checks']
    assert comparison['attempts']['baseline']['measured_runs'] > 0
    # Repeated start/recovery never creates another execution or stage run.
    await service.experiments.start(created['id'], 'Alice')
    assert len(service.get(variant_id)['pipeline']['runs']) == 2
