"""Host contracts for durable pipeline orchestration and human approval gates."""
from temporal_agent_harness.harness import agent
from cooperative.agent_models import StageDispatch, StageOutcome


@agent.callback_tool_defn(inherently_safe=True)
async def prepare_stage(stage: str) -> StageDispatch:
    """Freeze this stage's definition and authoritative inputs; reserve its host run."""
    ...


@agent.callback_tool_defn(inherently_safe=True)
async def bind_stage(run_id: str, workflow_id: str) -> dict:
    """Associate the reserved host run with the coordinator's actual child workflow."""
    ...


@agent.callback_tool_defn(inherently_safe=True)
async def finish_stage(run_id: str) -> StageOutcome:
    """Run required checks, reconcile the artifact, and return the verified stage outcome."""
    ...


@agent.callback_tool_defn(inherently_safe=False)
async def approve_stage(stage: str, run_id: str, input_revision: int, source_hash: str) -> dict:
    """Approve this exact plan or reviewed source revision before the next stage starts.

    Requires a teammate's explicit decision. The host revalidates scope and source
    hashes when the approved call executes; this permission is never remembered.
    """
    ...


@agent.callback_tool_defn(inherently_safe=True)
async def pipeline_status(status: str, stage: str, repairs: int, error: str = '') -> dict:
    """Project the coordinator's transition into the shared feature record."""
    ...
