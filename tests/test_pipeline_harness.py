"""Integration tests against real Temporal and native harness gates; no model credentials."""
import asyncio
from pathlib import Path
import uuid

import pytest
import pytest_asyncio
from temporalio.worker import Worker

from temporal_agent_harness.plugin import AgentHarnessPlugin
from temporal_agent_harness.harness.agent_client import AgentClient
from agent_dag.pipeline_agent import FeaturePipelineAgentWorkflow
from cooperative.runtime import PipelineService, StageRuntime
from features.runtime import FeatureService
from features.storage import FeatureConflict
from workspace.store import WorkspaceStore
from tests._pipeline_stage import ScriptedStageWorkflow
from tests.test_cooperative import request, action


async def until(predicate, timeout=30):
    async with asyncio.timeout(timeout):
        while True:
            value = predicate()
            if value:
                return value
            await asyncio.sleep(.05)


async def test_blocked_development_can_be_manually_approved_without_evidence(durable, monkeypatch):
    import json
    service, root, runtime, temporal = durable
    original = service.callback
    async def callback(ident, run_id, call_id, name, args):
        _, run = service.run(ident, run_id)
        if name == 'save_stage_result' and run['stage'] == 'development':
            result = json.loads(args['result_json'])
            result.update(verdict='blocked', questions=['A teammate must validate rendered keyboard interaction.'])
            args = {**args, 'result_json': json.dumps(result)}
        return await original(ident, run_id, call_id, name, args)
    monkeypatch.setattr(service, 'callback', callback)
    f = await service.create(request(root)); ident = f['id']
    approval = await waiting(service, ident, 'planner')
    await service.approve_managed(ident, decision(approval))
    await until(lambda: service.get(ident)['pipeline'].get('execution', {}).get('status') == 'blocked')
    await service.control(ident, 'manual_approve', {'id': 'manual-review', 'revision': 1, 'author': 'Bob'})
    await waiting(service, ident, 'review')
    f = service.get(ident)
    run = f['pipeline']['runs'][-1]
    assert run['stage'] == 'review'
    assert run['context']['checks']['syntax']['status'] == 'passed'
    assert run['context']['manual_approvals'][0]['current']
    assert 'review' not in f['pipeline']['approvals']
    assert sum(r['stage'] == 'development' for r in f['pipeline']['runs']) == 1


@pytest_asyncio.fixture
async def durable(tmp_path, monkeypatch, harness_client):
    queue = 'pipeline-test-' + uuid.uuid4().hex
    monkeypatch.setenv('AGENT_DAG_TASK_QUEUE', queue)
    root = tmp_path / 'source'; root.mkdir(); (root / 'app.py').write_text('def answer():\n    return 0\n')
    store = WorkspaceStore(tmp_path / 'state')
    features = FeatureService(store, tmp_path / 'state', lambda _: None, enabled=False)
    runtime = StageRuntime(); runtime.temporal = harness_client
    service = PipelineService(features, lambda _: None, runtime=runtime)
    async with Worker(harness_client, task_queue=queue, workflows=[FeaturePipelineAgentWorkflow, ScriptedStageWorkflow],
                      plugins=[AgentHarnessPlugin()]):
        try:
            yield service, root, runtime, harness_client
        finally:
            try:
                async with asyncio.timeout(10):
                    await service.stop()
            finally:
                store.close()


async def waiting(service, ident, stage):
    expected = 'needs_plan_approval' if stage == 'planner' else 'needs_release_approval'
    await until(lambda: service.get(ident)['pipeline']['status'] == expected)
    execution = service.get(ident)['pipeline']['execution']['workflow_id']
    async with asyncio.timeout(20):
        while True:
            approvals = await service.runtime.pending_approvals(execution)
            if approvals:
                return approvals[0]
            await asyncio.sleep(.05)


def decision(approval, ident='approve', approved=True):
    return {'id': ident, 'author': 'Bob', 'revision': 1, 'tool_id': approval.tool_id, 'approved': approved, 'reason': 'Reviewed by Bob'}


async def test_real_children_native_gates_host_restart_and_release(durable):
    service, root, runtime, temporal = durable
    f = await service.create(request(root)); ident = f['id']
    approval = await waiting(service, ident, 'planner')
    before = service.get(ident)
    assert [r['stage'] for r in before['pipeline']['runs']] == ['ideation', 'planner']
    assert all(r['harness_version'] == 2 and '-subagent-' in r['workflow_id'] for r in before['pipeline']['runs'])
    assert before['pipeline']['runs'][-1]['progress']['summary'] == 'Completed planner'
    planner = before['pipeline']['runs'][-1]
    assert any('/ideation/' in file['path'] for file in planner['library']['files'])
    assert any(item['name'] == 'stage_read_shared_file' for item in planner['trace'])
    coordinator = before['pipeline']['execution']['workflow_id']

    # Reconnect the local host while the parent is parked at its native approval gate.
    await service.stop(); await asyncio.sleep(0)
    await service.start()
    await service.approve_managed(ident, decision(approval))
    await service.approve_managed(ident, decision(approval))  # Retry does not release twice.
    release = await waiting(service, ident, 'review')
    f = service.get(ident)
    assert f['pipeline']['execution']['workflow_id'] == coordinator
    assert f['pipeline']['approvals']['planner']['author'] == 'Bob'
    assert f['pipeline']['checks']['syntax']['status'] == 'passed'
    assert 'return 0' in (root / 'app.py').read_text()
    assert len([r for r in f['pipeline']['runs'] if r['stage'] == 'development']) == 1
    development = next(r for r in f['pipeline']['runs'] if r['stage'] == 'development')
    assert any(item['name'] == 'stage_patch_file' for item in development['trace'])
    assert development['progress']['changed_files'] == ['app.py']
    assert development['result']['changed_files'] == ['app.py']

    await service.approve_managed(ident, decision(release, 'release'))
    await until(lambda: service.get(ident)['pipeline']['status'] == 'complete')
    await until(lambda: not service.tasks)
    done = service.get(ident)
    assert done['pipeline']['release']['file_count']
    assert done['pipeline']['execution']['status'] == 'complete'
    assert any(e['type'] == 'tool_approval_resolved' for e in done['pipeline']['telemetry'])
    assert not (await AgentClient(temporal, coordinator).get_status()).subagents


async def test_declined_gate_does_not_start_design(durable):
    service, root, _, _ = durable
    f = await service.create(request(root)); approval = await waiting(service, f['id'], 'planner')
    await service.approve_managed(f['id'], decision(approval, approved=False))
    await until(lambda: service.get(f['id'])['pipeline']['status'] == 'blocked')
    assert len(service.get(f['id'])['pipeline']['runs']) == 2
    assert not service.get(f['id'])['pipeline']['approvals']


async def test_completed_child_closing_before_cleanup_preserves_handoff(durable, monkeypatch):
    service, root, runtime, temporal = durable
    original = service.pipeline_callback
    closed = []

    async def callback(ident, execution, owner, call, name, args):
        result = await original(ident, execution, owner, call, name, args)
        if name == 'finish_stage' and not closed:
            child = service.run(ident, args['run_id'])[1]['workflow_id']
            closed.append(child)
            await runtime.close_run(child)
            await asyncio.wait_for(temporal.get_workflow_handle(child).result(), 5)
        return result

    monkeypatch.setattr(service, 'pipeline_callback', callback)
    feature = await service.create(request(root))
    await waiting(service, feature['id'], 'planner')
    current = service.get(feature['id'])
    assert closed
    assert current['pipeline']['runs'][0]['status'] == 'completed'
    assert len(current['pipeline']['runs']) == 2
    assert not (await AgentClient(temporal, current['pipeline']['execution']['workflow_id']).get_status()).subagents


async def test_pause_feedback_and_resume_keep_the_same_child(durable):
    service, root, _, _ = durable
    f = await service.create({**request(root), 'description': 'wait for feedback'}); ident = f['id']
    approval = await waiting(service, ident, 'planner')
    await service.approve_managed(ident, decision(approval))
    await until(lambda: service.get(ident)['pipeline']['status'] == 'running' and service.get(ident)['pipeline']['runs'][-1]['stage'] == 'development' and service.get(ident)['pipeline']['runs'][-1]['workflow_id'])
    run_id = service.get(ident)['pipeline']['runs'][-1]['id']
    await service.control(ident, 'pause', action('pause'))
    await service.feedback_managed(ident, {'id': 'feedback', 'author': 'Bob', 'revision': 1, 'text': 'Continue the approved change.'})
    await asyncio.sleep(.5)
    assert service.get(ident)['pipeline']['status'] == 'paused'
    assert 'return 0' in Path(service.get(ident)['worktree'], 'app.py').read_text()
    await service.control(ident, 'resume', action('resume'))
    await waiting(service, ident, 'review')
    development = [r for r in service.get(ident)['pipeline']['runs'] if r['stage'] == 'development']
    assert len(development) == 1 and development[0]['id'] == run_id


async def test_scope_change_revokes_pending_native_approval(durable):
    service, root, _, _ = durable
    f = await service.create(request(root)); ident = f['id']
    approval = await waiting(service, ident, 'planner')
    await service.contribute(ident, {'id': 'scope', 'author': 'Bob', 'kind': 'requirement', 'text': 'Support a default value.'})
    with pytest.raises(FeatureConflict):
        await service.approve_managed(ident, decision(approval))
    assert service.get(ident)['pipeline']['status'] == 'scope_changed'
    assert not service.get(ident)['pipeline']['approvals']


async def test_stale_review_hash_is_rejected_before_native_approval(durable):
    service, root, _, _ = durable
    f = await service.create(request(root)); ident = f['id']
    approval = await waiting(service, ident, 'planner')
    await service.approve_managed(ident, decision(approval))
    approval = await waiting(service, ident, 'review')
    Path(service.get(ident)['worktree'], 'app.py').write_text('human_edit = True\n')
    with pytest.raises(FeatureConflict, match='Files changed'):
        await service.approve_managed(ident, decision(approval, 'release'))
    assert (await service.runtime.pending_approvals(service.get(ident)['pipeline']['execution']['workflow_id']))[0].tool_id == approval.tool_id


async def test_restart_recovers_persisted_rerun_intent(durable, monkeypatch):
    service, root, _, _ = durable
    f = await service.create(request(root)); ident = f['id']
    await waiting(service, ident, 'planner')
    old = service.get(ident)['pipeline']['execution']['workflow_id']

    async def interrupted(*args):
        raise OSError('host stopped before starting replacement')

    with monkeypatch.context() as patch:
        patch.setattr(service, 'handoff', interrupted)
        with pytest.raises(OSError):
            await service.control(ident, 'rerun', {**action('rerun'), 'stage': 'planner'})
    await service.stop(); await asyncio.sleep(0)
    await service.start()
    await waiting(service, ident, 'planner')
    current = service.get(ident)['pipeline']
    assert current['execution']['workflow_id'] != old
    assert 'pending_start' not in current
    assert len([r for r in current['runs'] if r['stage'] == 'planner']) == 2


async def test_manual_deployment_completes_existing_preview_without_new_agent(durable, monkeypatch):
    service, root, runtime, temporal = durable
    original = service.final_checks
    async def checks(ident, run_id):
        await original(ident, run_id)
        feature, run = service.run(ident, run_id)
        if run['stage'] == 'deployment':
            feature['pipeline']['checks']['python_tests'] = {
                'status': 'timed_out', 'source_hash': service.checks.fingerprint(feature),
                'output': 'Check exceeded 300 seconds.', 'timeout_seconds': 300}
            service.store.put(feature)
    monkeypatch.setattr(service, 'final_checks', checks)
    feature = await service.create(request(root)); ident = feature['id']
    await service.approve_managed(ident, decision(await waiting(service, ident, 'planner')))
    await service.approve_managed(ident, decision(await waiting(service, ident, 'review'), ident='release'))
    await until(lambda: service.get(ident)['pipeline'].get('execution', {}).get('status') == 'blocked')
    before = service.get(ident)['pipeline']
    action = {'id': 'manual-deployment', 'author': 'Bob', 'revision': 1}
    await service.control(ident, 'manual_approve', action)
    await service.control(ident, 'manual_approve', action)
    after = service.get(ident)['pipeline']
    assert after['status'] == 'complete'
    assert after['release'] == before['release']
    assert len(after['runs']) == len(before['runs'])
    assert after['checks']['python_tests']['status'] == 'timed_out'
    assert after['approvals']['deployment']['manual']
    assert len(after['manual_approvals']) == 1
