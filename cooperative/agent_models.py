"""Typed execution contracts and observable state for the durable feature pipeline."""
from typing import Literal

from pydantic import Field
from temporal_agent_harness.harness.state import HarnessState

from features.models import StrictModel
from .models import StageExecution, StageId


class UsageState(HarnessState):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class StageState(HarnessState):
    stage: str = ''
    run_id: str = ''
    version: str = ''
    status: str = 'idle'
    activity: str = ''
    model: str = ''
    model_calls: int = 0
    completed_tools: int = 0
    failed_tools: int = 0
    active_tools: dict[str, str] = Field(default_factory=dict)
    changed_files: list[str] = Field(default_factory=list)
    checks: dict[str, str] = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list)
    handoff: str = ''
    summary: str = ''
    usage: UsageState = Field(default_factory=UsageState)
    paused: bool = False
    feedback: list[str] = Field(default_factory=list)
    error: str = ''


class PipelineState(HarnessState):
    feature_id: str = ''
    input_revision: int = 0
    status: str = 'idle'
    current_stage: str = 'ideation'
    current_run_id: str = ''
    child_workflow_id: str = ''
    completed: list[str] = Field(default_factory=list)
    repairs: int = 0
    paused: bool = False
    approval_stage: str = ''
    error: str = ''


class LiveStageExecution(StageExecution):
    """Frozen stage inputs and the host run that owns its callback receipts."""
    run_id: str
    input_revision: int


class LiveControl(StrictModel):
    """A human control or feedback message applied at a cooperative boundary."""
    action: Literal['pause', 'resume', 'feedback']
    text: str = Field(default='', max_length=4000)
    sequence: int = Field(default=0, ge=0)


class ControlReply(StrictModel):
    """Acknowledgement that a workflow accepted a control message."""
    accepted: bool = True


class PipelineExecution(StrictModel):
    """Start a durable feature execution at one validated scope revision."""
    feature_id: str
    input_revision: int
    stage: StageId = 'ideation'
    repairs: int = 0


class StageDispatch(StrictModel):
    run_id: str
    execution: LiveStageExecution


class StageOutcome(StrictModel):
    stage: StageId
    run_id: str
    verdict: Literal['ready', 'blocked', 'changes_requested']
    questions: list[str]
    source_hash: str = ''
    summary: str


class PipelineReply(StrictModel):
    """The final status of this pipeline execution."""
    status: str
    stage: str


class ApprovalRequest(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    author: str = Field(min_length=1, max_length=40)
    revision: int = Field(ge=1)
    tool_id: str = Field(min_length=1, max_length=200)
    approved: bool
    reason: str = Field(default='', max_length=2000)


class FeedbackRequest(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    author: str = Field(min_length=1, max_length=40)
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=3900)
