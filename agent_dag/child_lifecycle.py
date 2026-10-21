"""Compatibility for already-closed children on harness 0.6.0.

The pinned harness deregisters only after its close signal succeeds. Keep the
private-registry workaround here until upstream stop_subagent is idempotent.
"""
from temporalio.exceptions import ApplicationError
from temporal_agent_harness.harness.agent_protocol.events import SubagentStopped


def child_already_closed(error):
    return isinstance(error, ApplicationError) and error.type == 'ExternalWorkflowExecutionNotFound'


def forget_closed_child(runner, handle):
    child = next(item for item in runner.current_status.subagents if item.subagent_id == handle)
    runner.publish(SubagentStopped(subagent_id=handle, agent_key=child.agent_key, workflow_id=child.workflow_id))
    runner._status.remove_subagent(handle)
