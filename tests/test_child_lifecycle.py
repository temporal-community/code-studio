from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError
from temporal_agent_harness.harness.state import StateRef
from cooperative.agent_models import PipelineState, LiveControl
from agent_dag.pipeline_agent import FeaturePipelineAgentWorkflow
from agent_dag.child_lifecycle import child_already_closed


async def test_pause_survives_closed_child_but_unrelated_errors_propagate(monkeypatch):
    from agent_dag import pipeline_agent
    agent = SimpleNamespace(control_sequence=0, pipeline=StateRef('pipeline', PipelineState(child_workflow_id='closed')))
    signal = AsyncMock(side_effect=ApplicationError('gone', type='ExternalWorkflowExecutionNotFound'))
    monkeypatch.setattr(pipeline_agent.workflow, 'get_external_workflow_handle', lambda _: SimpleNamespace(signal=signal))
    monkeypatch.setattr(pipeline_agent.workflow, 'patched', lambda _: True)
    assert (await FeaturePipelineAgentWorkflow.control(agent, LiveControl(action='pause', sequence=1))).accepted
    assert agent.pipeline.current.paused
    assert not (await FeaturePipelineAgentWorkflow.control(agent, LiveControl(action='feedback', text='Late', sequence=2))).accepted
    signal.side_effect = ApplicationError('permission denied', type='PermissionDenied')
    with pytest.raises(ApplicationError):
        await FeaturePipelineAgentWorkflow.control(agent, LiveControl(action='resume', sequence=3))


def test_only_exact_missing_child_error_is_recoverable():
    assert child_already_closed(ApplicationError('gone', type='ExternalWorkflowExecutionNotFound'))
    assert not child_already_closed(ValueError('ExternalWorkflowExecutionNotFound'))
