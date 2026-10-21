"""One durable owner for stage children, review gates, and bounded repair loops."""
from temporalio import workflow
from temporalio.contrib.workflow_streams import WorkflowStream

with workflow.unsafe.imports_passed_through():
    from temporal_agent_harness.harness import agent
    from temporal_agent_harness.harness.agent_protocol import AgentConfig, MidTurn, ToolApprovalPolicy
    from temporal_agent_harness.harness.agent_workflow import AgentWorkflowRunner, ToolApprovalDenied
    from cooperative.agent_models import PipelineState, PipelineExecution, PipelineReply, StageDispatch, StageOutcome, LiveControl, ControlReply
    from cooperative.definitions import STAGES
    from .pipeline_tools import prepare_stage, bind_stage, finish_stage, approve_stage, pipeline_status
    from .child_lifecycle import child_already_closed, forget_closed_child


def next_transition(outcome: StageOutcome, repairs: int):
    """Deterministic pipeline policy, evaluated only after host evidence checks."""
    if outcome.questions or outcome.verdict == 'blocked':
        return 'blocked', outcome.stage, repairs
    if outcome.verdict == 'changes_requested':
        if outcome.stage == 'review' and repairs < 2:
            return 'ready', 'development', repairs + 1
        return 'blocked', outcome.stage, repairs
    if outcome.stage in ('planner', 'review'):
        return 'needs_plan_approval' if outcome.stage == 'planner' else 'needs_release_approval', outcome.stage, repairs
    if outcome.stage == 'deployment':
        return 'complete', outcome.stage, repairs
    return 'ready', STAGES[STAGES.index(outcome.stage) + 1], repairs


@agent.defn(name='FeaturePipelineAgent')
class FeaturePipelineAgentWorkflow:
    pipeline = agent.state(PipelineState)

    @agent.init
    def __init__(self, config: AgentConfig):
        self.control_sequence = 0
        self.runner = AgentWorkflowRunner(config, stream=WorkflowStream(),
            approval_policy_default=ToolApprovalPolicy.allow_inherently_safe())

    async def call(self, tool, **kwargs):
        return await self.runner.run_tool(str(workflow.uuid4()), tool, **kwargs)

    @agent.accepts(mid_turn=MidTurn.ACCEPT, model_callable=False)
    async def control(self, message: LiveControl) -> ControlReply:
        """Pause/resume execution at tool boundaries or forward feedback to the active stage."""
        if message.sequence and message.sequence <= self.control_sequence:
            return ControlReply()
        self.control_sequence = max(self.control_sequence, message.sequence)
        if message.action != 'feedback':
            with self.pipeline.mutate() as state:
                state.paused = message.action == 'pause'
        child = self.pipeline.current.child_workflow_id
        if child:
            try:
                await workflow.get_external_workflow_handle(child).signal('set_control', message)
            except Exception as exc:
                if not child_already_closed(exc) or not workflow.patched('closed-child-control-v1'):
                    raise
                if message.action == 'feedback':
                    with self.pipeline.mutate() as state:
                        state.error = 'The stage finished before feedback arrived. Send feedback to the next active stage.'
                    return ControlReply(accepted=False)
        return ControlReply()

    async def stop_child(self, handle):
        try:
            await self.runner.stop_subagent(handle)
        except Exception as exc:
            if not child_already_closed(exc) or not workflow.patched('closed-child-cleanup-v1'):
                raise
            forget_closed_child(self.runner, handle)

    async def transition(self, status, stage, repairs, error=''):
        await self.call(pipeline_status, status=status, stage=stage, repairs=repairs, error=error)
        with self.pipeline.mutate() as state:
            state.status = status
            state.current_stage = stage
            state.repairs = repairs
            state.error = error

    @agent.accepts(model_callable=False)
    async def execute(self, message: PipelineExecution) -> PipelineReply:
        """Run a feature through child agents, preserving approval waits and repair history."""
        self.pipeline.set(PipelineState(feature_id=message.feature_id, input_revision=message.input_revision,
            current_stage=message.stage, repairs=message.repairs, status='running', paused=self.pipeline.current.paused))
        stage, repairs = message.stage, message.repairs
        try:
            for _ in range(40):
                await workflow.wait_condition(lambda: not self.pipeline.current.paused)
                dispatch = StageDispatch.model_validate(await self.call(prepare_stage, stage=stage))
                with self.pipeline.mutate() as state:
                    state.current_stage = stage
                    state.current_run_id = dispatch.run_id
                    state.status = 'running'
                handle = await self.runner.start_subagent(stage, 'SdlcStageAgentV2', workflow.info().task_queue)
                child = next(item for item in self.runner.current_status.subagents if item.subagent_id == handle)
                with self.pipeline.mutate() as state:
                    state.child_workflow_id = child.workflow_id
                await self.call(bind_stage, run_id=dispatch.run_id, workflow_id=child.workflow_id)
                if self.pipeline.current.paused:
                    await workflow.get_external_workflow_handle(child.workflow_id).signal('set_control', LiveControl(action='pause'))
                try:
                    await self.runner.run_subagent_turn(handle, 'execute', dispatch.execution.model_dump())
                    await workflow.wait_condition(lambda: not self.pipeline.current.paused)
                    outcome = StageOutcome.model_validate(await self.call(finish_stage, run_id=dispatch.run_id))
                finally:
                    # Clear before awaiting close, so a simultaneous control cannot target a closed child.
                    with self.pipeline.mutate() as state:
                        state.child_workflow_id = ''
                    await self.stop_child(handle)
                with self.pipeline.mutate() as state:
                    if stage not in state.completed:
                        state.completed.append(stage)
                status, target, repairs = next_transition(outcome, repairs)
                await self.transition(status, target, repairs)
                if status in ('blocked', 'complete'):
                    return PipelineReply(status=status, stage=stage)
                if status.startswith('needs_'):
                    with self.pipeline.mutate() as state:
                        state.approval_stage = stage
                    try:
                        await self.call(approve_stage, stage=stage, run_id=outcome.run_id,
                            input_revision=message.input_revision, source_hash=outcome.source_hash)
                    except ToolApprovalDenied as exc:
                        await self.transition('blocked', stage, repairs, str(exc))
                        return PipelineReply(status='blocked', stage=stage)
                    with self.pipeline.mutate() as state:
                        state.approval_stage = ''
                    target = STAGES[STAGES.index(stage) + 1]
                    await self.transition('ready', target, repairs)
                stage = target
            raise ValueError('This execution reached its 40-stage limit')
        except Exception as exc:
            await self.transition('failed', stage, repairs, str(exc)[:2000])
            return PipelineReply(status='failed', stage=stage)
