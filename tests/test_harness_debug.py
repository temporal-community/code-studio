from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from temporalio.service import RPCError, RPCStatusCode

from scripts.harness_server import create_app, prepare_ui


def test_packaged_ui_adapter_preserves_explicit_selection(tmp_path):
    prepare_ui(tmp_path)
    script = next(p.read_text() for p in (tmp_path / 'assets').glob('*.js') if 'debug-sessions' in p.read_text())
    assert 'return e?r??null:[...t]' in script
    assert 'if(!s&&o)throw new Error("The requested workflow is unavailable.")' in script
    patched = list((tmp_path / 'assets').glob('studio-*.js'))
    assert len(patched) == 1
    assert patched[0].name in (tmp_path / 'index.html').read_text()
    client = TestClient(create_app(tmp_path))
    assert client.get('/').headers['cache-control'] == 'no-store'


def test_debugger_attaches_to_exact_closed_workflow_without_creating_a_session(tmp_path):
    app = create_app(tmp_path)
    describe = AsyncMock(return_value=SimpleNamespace(id='feature-closed',
        workflow_type='FeaturePipelineAgent', start_time=datetime.now(timezone.utc),
        status=SimpleNamespace(name='COMPLETED')))
    requested = []
    def handle(ident):
        requested.append(ident)
        return SimpleNamespace(describe=describe)
    app.state.temporal = SimpleNamespace(get_workflow_handle=handle)
    client = TestClient(app)
    response = client.get('/api/debug-sessions', params={'workflow_id': 'feature-closed'})
    assert response.status_code == 200
    assert requested == ['feature-closed']
    assert response.json()[0]['closed']
    assert response.json()[0]['agent_workflow_type'] == 'FeaturePipelineAgent'
    describe.side_effect = RPCError('not found', RPCStatusCode.NOT_FOUND, b'')
    assert client.get('/api/debug-sessions?workflow_id=missing').status_code == 404


def test_direct_child_replay_keeps_root_frames_and_separates_descendants(tmp_path):
    """Execute the actual patched bundle's mapper, not a copy of its logic."""
    import json
    import subprocess
    prepare_ui(tmp_path)
    script = next((tmp_path / 'assets').glob('studio-*.js')).read_text()
    mapper = 'function e7(' + script.split('function e7(', 1)[1].split('function t7(', 1)[0]
    # Child-root IDs trigger the upstream suffix heuristic. Descendants are
    # instead identified by the stream's explicit observed-subagent mapping.
    frames = [{'event': kind, 'data': {'type': kind, 'agent_id': 'root-a1b2c3', 'turn_number': 1}}
              for kind in ['message_accepted', 'model_interaction_started', 'tool_start', 'tool_end']]
    frames.append({'event': 'tool_start', 'data': {'type': 'tool_start', 'agent_id': 'root-a1b2c3-d4e5f6', 'turn_number': 1}})
    descendants = [{'subagentId': 'root-a1b2c3-d4e5f6', 'workflowId': 'nested', 'label': 'Nested'}]
    code = mapper + '\nconsole.log(JSON.stringify(e7(' + ','.join(json.dumps(v) for v in [
        {'workflow_id': 'selected-child'}, frames, descendants, 'Stage']) + ')));'
    result = subprocess.run(['node', '-e', code], capture_output=True, text=True, check=True)
    timeline = json.loads(result.stdout)
    assert [e['frame']['event'] for e in timeline if e['role'] == 'parent'] == [
        'message_accepted', 'model_interaction_started', 'tool_start', 'tool_end']
    assert all(e['workflowId'] == 'selected-child' for e in timeline[:-1])
    assert timeline[-1]['workflowId'] == 'nested'
    assert timeline[-1]['role'] == 'subagent'
