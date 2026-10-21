"""Local side effects and revision checks for the durable pipeline's callbacks.

Temporal owns sequencing. SQLite keeps the feature projection and idempotency
receipts; source access always stays on this host.
"""
import asyncio
from contextlib import suppress
import time
import uuid

from temporal_agent_harness.harness.agent_client import AgentTurnError
from features.storage import ACTIVE, FeatureConflict
from sdlc.models import digest
from .definitions import STAGES, DEFINITIONS


class PipelineHostMixin:
    def require_execution(self, feature, workflow_id):
        execution = feature['pipeline'].get('execution', {})
        if execution.get('workflow_id') != workflow_id or execution.get('status') != 'active' or execution['input']['input_revision'] != feature['input_revision']:
            raise FeatureConflict('This execution no longer owns the current feature scope')
        return execution

    async def start_pipeline(self, ident, stage, author):
        if not self.enabled:
            raise ValueError('Start just dev with a model key and worker to run agents')
        async with self.features.lock(ident):
            f = self.get(ident); p = f['pipeline']
            existing = p.get('execution')
            if existing and existing['status'] == 'active':
                self.schedule_pipeline(ident)
                return
            execution = {'workflow_id': 'feature-pipeline-' + ident + '-' + uuid.uuid4().hex,
                'status': 'active', 'author': author, 'paused': False,
                'input': {'feature_id': ident, 'input_revision': f['input_revision'], 'stage': stage, 'repairs': p['repairs']}}
            p['execution'] = execution
            p.pop('pending_start', None)
            p['status'] = f['status'] = 'running'; p['current_stage'] = stage
            f['error'] = None
            self.store.put(f); self.announce(ident)
            self.schedule_pipeline(ident)

    def schedule_pipeline(self, ident):
        execution = self.get(ident)['pipeline']['execution']
        key = (ident, execution['workflow_id'])
        if key not in self.tasks:
            task = asyncio.create_task(self.work_pipeline(ident, execution))
            self.tasks[key] = task
            task.add_done_callback(lambda _: self.tasks.pop(key, None))

    async def work_pipeline(self, ident, execution):
        workflow_id = execution['workflow_id']
        while True:
            try:
                execution = self.require_execution(self.get(ident), workflow_id)
                await self.runtime.execute_pipeline(execution,
                    lambda owner, call, name, args: self.pipeline_callback(ident, workflow_id, owner, call, name, args),
                    lambda event: self.pipeline_event(ident, workflow_id, event),
                    lambda: self.get(ident)['pipeline'].get('execution', {}).get('paused', False))
                async with self.features.lock(ident):
                    f = self.get(ident)
                    current = self.require_execution(f, workflow_id)
                    current['status'] = current.get('result_status', 'failed')
                    self.store.put(f); self.announce(ident)
                return
            except asyncio.CancelledError:
                raise
            except FeatureConflict:
                return
            except AgentTurnError as exc:
                async with self.features.lock(ident):
                    f = self.get(ident)
                    if f['pipeline'].get('execution', {}).get('workflow_id') != workflow_id:
                        return
                    f['pipeline']['execution']['status'] = 'failed'
                    f['pipeline']['status'] = f['status'] = 'failed'; f['error'] = str(exc)[:2000]
                    self.store.put(f); self.announce(ident)
                return
            except Exception:
                # The workflow and pending callbacks are durable; a dropped connection
                # must reattach to this execution, never create a replacement run.
                await asyncio.sleep(2)

    def stage_dispatch(self, f, run_id):
        run = next(r for r in f['pipeline']['runs'] if r['id'] == run_id)
        return {'run_id': run_id, 'execution': {k: run[k] for k in
            ('stage', 'context', 'project', 'input_revision')} | {'run_id': run_id}}

    async def pipeline_callback(self, ident, execution_id, owner, call_id, name, args):
        f = self.get(ident); self.require_execution(f, execution_id)
        if owner != execution_id:
            run = next((r for r in f['pipeline']['runs'] if r['workflow_id'] == owner and r.get('execution_id') == execution_id), None)
            if not run:
                raise FeatureConflict('Unknown child workflow for this execution')
            return await self.callback(ident, run['id'], call_id, name, args)
        payload = {'name': name, 'args': args}
        key = 'coordinator:' + execution_id + ':' + call_id
        old = self.store.receipt(key, payload)
        if old is not None:
            return old
        if name == 'prepare_stage':
            run_id = digest({'execution': execution_id, 'call': call_id})[:32]
            f = await self.launch(ident, args['stage'], f['pipeline']['execution']['author'],
                run_id=run_id, execution_id=execution_id)
            result = self.stage_dispatch(f, run_id)
        elif name == 'finish_stage':
            f, run = self.run(ident, args['run_id'])
            if run.get('execution_id') != execution_id:
                raise FeatureConflict('The stage belongs to a different execution')
            if run.get('outcome'):
                result = run['outcome']
            else:
                if not run['result']:
                    raise ValueError('The agent finished without saving its stage handoff')
                with suppress(Exception):
                    progress = await self.runtime.stage_progress(run['workflow_id'])
                    f, current = self.run(ident, run['id'])
                    current['progress'] = progress; current['usage'] = progress['usage']
                    self.store.put(f)
                if run['stage'] in ('development', 'review', 'deployment'):
                    await self.final_checks(ident, run['id'])
                result = await self.finish(ident, run['id'], managed=True)
        else:
            async with self.features.lock(ident):
                f = self.get(ident); p = f['pipeline']; self.require_execution(f, execution_id)
                if name == 'bind_stage':
                    run = next(r for r in p['runs'] if r['id'] == args['run_id'] and r.get('execution_id') == execution_id)
                    if run['workflow_id'] and run['workflow_id'] != args['workflow_id']:
                        raise FeatureConflict('This stage already has a child workflow')
                    run['workflow_id'] = args['workflow_id']
                    result = {'bound': True}
                elif name == 'approve_stage':
                    # Even a caller using the generic harness API cannot bypass domain checks.
                    intent = p.get('approval_decisions', {}).get(call_id)
                    if not intent or not intent['approved'] or intent['execution_id'] != execution_id:
                        raise FeatureConflict('Use the feature approval screen to approve this scope')
                    run = self.validate_approval(f, args)
                    approval = {'run_id': run['id'], 'author': intent['author'], 'revision': f['input_revision'],
                        'time': time.time(), 'tool_id': call_id, 'reason': intent['reason']}
                    if args['stage'] == 'review':
                        current = await asyncio.to_thread(self.checks.fingerprint, f)
                        if current != args['source_hash']:
                            raise FeatureConflict('Files changed since Review. Rerun Review before approving.')
                        approval['source_hash'] = current
                    p['approvals'][args['stage']] = approval
                    p['stages'][args['stage']]['status'] = 'approved'
                    result = {'approved': True}
                elif name == 'pipeline_status':
                    status, stage = args['status'], args['stage']
                    if status not in ('ready', 'blocked', 'complete', 'failed', 'needs_plan_approval', 'needs_release_approval') or stage not in STAGES:
                        raise ValueError('Invalid pipeline transition')
                    p['status'] = f['status'] = status; p['current_stage'] = stage; p['repairs'] = args['repairs']
                    f['error'] = args.get('error') or None
                    if status.startswith('needs_'):
                        p['stages'][stage]['status'] = 'needs_approval'
                    if status in ('blocked', 'complete', 'failed'):
                        # Keep serving the final callback until its result is acknowledged.
                        p['execution']['result_status'] = status
                        if status != 'complete':
                            p['stages'][stage]['status'] = 'failed' if status == 'failed' else 'needs_input'
                            for run in p['runs']:
                                if run.get('execution_id') == execution_id and run['status'] in ACTIVE:
                                    run['status'] = 'failed'; run['error'] = f['error']
                    self.store.record(f, 'Workflow', status.replace('_', ' ').capitalize() + ' · ' + DEFINITIONS[stage]['name'])
                    result = {'saved': True}
                else:
                    raise ValueError('Unknown coordinator callback')
                self.store.commit_receipt(f, key, payload, result); self.announce(ident)
                return result
        async with self.features.lock(ident):
            f = self.get(ident); self.require_execution(f, execution_id)
            self.store.commit_receipt(f, key, payload, result); self.announce(ident)
        return result

    def validate_approval(self, f, args):
        p = f['pipeline']; stage = args['stage']
        expected = {'planner': 'needs_plan_approval', 'review': 'needs_release_approval'}
        if stage not in expected or p['status'] != expected[stage] or args['input_revision'] != f['input_revision']:
            raise FeatureConflict('This approval is no longer current')
        run = next((r for r in p['runs'] if r['id'] == args['run_id']), None)
        if not run or run['status'] != 'completed' or p['stages'][stage]['run_id'] != run['id']:
            raise FeatureConflict('This approval does not match the current saved run')
        if stage == 'review' and run.get('reviewed_hash') != args['source_hash']:
            raise FeatureConflict('This approval does not match the reviewed source')
        return run

    async def approve_managed(self, ident, request):
        f = self.get(ident); execution_id = f['pipeline'].get('execution', {}).get('workflow_id')
        # A stable decision intent is saved before resolving the harness update.
        existing = f['pipeline'].get('approval_decisions', {}).get(request['tool_id'])
        fingerprint = digest(request)
        if existing:
            if existing['fingerprint'] != fingerprint:
                raise FeatureConflict('A teammate already submitted a decision for this request')
            execution_id = existing['execution_id']
        else:
            self.require_execution(f, execution_id)
            waiting = await self.runtime.pending_approvals(execution_id)
            approval = next((item for item in waiting if item.tool_id == request['tool_id'] and item.tool_name == 'approve_stage'), None)
            if not approval:
                raise FeatureConflict('This approval request is no longer waiting')
            async with self.features.lock(ident):
                f = self.get(ident); self.require_execution(f, execution_id)
                self.store.expect_revision(f, request['revision'])
                self.validate_approval(f, approval.tool_input)
                if approval.tool_input['stage'] == 'review':
                    current = await asyncio.to_thread(self.checks.fingerprint, f)
                    if current != approval.tool_input['source_hash']:
                        raise FeatureConflict('Files changed since Review. Rerun Review before approving.')
                decisions = f['pipeline'].setdefault('approval_decisions', {})
                if request['tool_id'] in decisions:
                    raise FeatureConflict('A teammate already submitted a decision for this request')
                decisions[request['tool_id']] = {**request, 'fingerprint': fingerprint, 'execution_id': execution_id}
                self.store.record(f, request['author'], ('Approved' if request['approved'] else 'Requested changes to') + ' ' + approval.tool_input['stage'])
                self.store.put(f); self.announce(ident)
        await self.runtime.resolve_approval(execution_id, request)
        return self.get(ident)

    async def control_managed(self, ident, action, request):
        if action == 'approve':
            f = self.get(ident)
            waiting = await self.runtime.pending_approvals(f['pipeline']['execution']['workflow_id'])
            approval = next((item for item in waiting if item.tool_name == 'approve_stage'), None)
            if not approval:
                raise FeatureConflict('The saved approval request is not ready yet')
            return await self.approve_managed(ident, {k: request[k] for k in ('id', 'author', 'revision')} |
                {'tool_id': approval.tool_id, 'approved': True, 'reason': ''})
        async with self.features.lock(ident):
            f = self.get(ident); p = f['pipeline']; self.store.expect_revision(f, request['revision'])
            fingerprint = digest({'action': action, **request})
            previous = p['controls'].get(request['id'])
            if previous and previous != fingerprint:
                raise FeatureConflict('Action ID was reused for a different request')
            execution = p.get('execution')
            if action in ('pause', 'resume') and execution and execution['status'] == 'active':
                if action == 'resume' and not execution.get('paused') and not previous:
                    raise FeatureConflict('This workflow is not paused')
                if action == 'pause' and p['status'] not in ('running', 'paused'):
                    raise FeatureConflict('This workflow is already waiting for a decision')
                execution['paused'] = action == 'pause'
                controls = execution.setdefault('controls', [])
                control = next((c for c in controls if c['ident'] == request['id']), None)
                if control is None:
                    control = {'ident': request['id'], 'action': action, 'sequence': len(controls) + 1}
                    controls.append(control)
                p['status'] = f['status'] = 'paused' if execution['paused'] else 'running'
                p['controls'][request['id']] = fingerprint
                self.store.put(f); self.announce(ident)
                live_action = True
            else:
                if previous:
                    if p.get('pending_start'):
                        # A restart will finish this durable handoff intent.
                        asyncio.create_task(self.recover_handoff(ident))
                    return f
                if action not in ('resume', 'rerun', 'manual_approve'):
                    raise FeatureConflict('No active workflow to pause')
                if execution and execution['status'] == 'active' and p['status'] not in ('paused', 'needs_plan_approval', 'needs_release_approval') and not (action == 'manual_approve' and p['status'] == 'blocked' and execution.get('result_status') == 'blocked'):
                    raise FeatureConflict('Pause the current agent before rerunning a stage')
                manual = action == 'manual_approve'
                approved_run = None
                if manual:
                    if p['status'] != 'blocked' or p['current_stage'] not in ('development', 'review', 'deployment'):
                        raise FeatureConflict('Manual approval requires blocked Development, Review, or Deployment')
                    approved_run = next((r for r in p['runs'] if r['id'] == p['stages'][p['current_stage']]['run_id']), None)
                    if not approved_run or approved_run['status'] != 'completed' or not approved_run.get('result') or approved_run['input_revision'] != f['input_revision']:
                        raise FeatureConflict('The current stage must have a saved handoff')
                completing_preview = manual and p['current_stage'] == 'deployment'
                stage = 'deployment' if completing_preview else STAGES[STAGES.index(p['current_stage']) + 1] if manual else request.get('stage') or p['current_stage']
                if stage not in STAGES:
                    raise ValueError('Unknown stage')
                index = STAGES.index(stage)
                if not manual and index and p['stages'][STAGES[index - 1]]['status'] not in ('completed', 'approved'):
                    raise FeatureConflict('Finish the previous stage before starting this agent')
                if index >= 2 and p['approvals'].get('planner', {}).get('revision') != f['input_revision']:
                    raise FeatureConflict('Approve the plan first')
                if not manual and stage == 'deployment' and p['approvals'].get('review', {}).get('revision') != f['input_revision']:
                    raise FeatureConflict('Approve the review first')
                if manual:
                    from .manual_approval import check_signature, check_condition
                    source_hash = await asyncio.to_thread(self.checks.fingerprint, f)
                    if completing_preview:
                        release = p.get('release') or {}
                        review = p['approvals'].get('review', {})
                        if (not release or review.get('revision') != f['input_revision']
                                or review.get('source_hash') != source_hash
                                or release.get('workspace_hash') != source_hash
                                or release.get('source_hash') != source_hash):
                            raise FeatureConflict('Review and package the current source before approving this preview')
                        from pathlib import Path
                        if not Path(release.get('path', '')).is_file():
                            raise FeatureConflict('The release ZIP is missing. Rerun Deployment to recreate it')
                    approval = {'run_id': approved_run['id'], 'author': request['author'],
                        'revision': f['input_revision'], 'time': time.time(), 'manual': True,
                        'source_hash': source_hash,
                        'accepted_checks': {name: check_signature(value) for name, value in p['checks'].items()},
                        'accepted_conditions': {name: condition for name, value in p['checks'].items()
                                                if (condition := check_condition(value)) is not None}}
                    p['approvals'][approved_run['stage']] = approval
                    p['stages'][approved_run['stage']]['status'] = 'approved'
                    p.setdefault('manual_approvals', []).append({**approval, 'stage': approved_run['stage']})
                    self.store.record(f, request['author'], 'Manually approved ' + approved_run['stage'] + ' without additional evidence')
                if execution:
                    execution['status'] = 'superseded'
                for run in p['runs']:
                    if run['status'] in ACTIVE:
                        run['status'] = 'superseded'
                if completing_preview:
                    p['status'] = f['status'] = 'complete'
                    p.pop('pending_start', None)
                    f['error'] = None
                else:
                    for target in STAGES[index:]:
                        p['stages'][target] = {'status': 'pending', 'run_id': None}
                        p['approvals'].pop(target, None)
                    p['release'] = p['applied'] = None
                    if index <= STAGES.index('development'):
                        p['checks'] = {}
                    p['status'] = f['status'] = 'ready'; p['current_stage'] = stage
                    p['pending_start'] = {'stage': stage, 'author': request['author']}
                p['controls'][request['id']] = fingerprint
                self.store.put(f); self.announce(ident)
                live_action = False
        if live_action:
            await self.runtime.control_pipeline(execution['workflow_id'], **control)
        else:
            await self.stop_execution(ident, execution)
            if not completing_preview:
                await self.handoff(ident, stage, request['author'])
        return self.get(ident)

    async def recover_handoff(self, ident):
        f = self.get(ident); pending = f['pipeline'].get('pending_start')
        if pending:
            await self.stop_execution(ident, f['pipeline'].get('execution'))
            await self.start_pipeline(ident, pending['stage'], pending['author'])

    async def stop_execution(self, ident, execution):
        if not execution:
            return
        task = self.tasks.get((ident, execution['workflow_id']))
        if task:
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
        with suppress(Exception):
            await asyncio.wait_for(self.runtime.cancel_run(execution['workflow_id']), 5)

    async def contribute_managed(self, ident, request):
        from .clarification import check_submission
        async with self.features.lock(ident):
            f = self.get(ident); execution = f['pipeline'].get('execution')
            if check_submission(self, f, request):
                return f
            if request['kind'] == 'comment':
                key = ident + ':input:' + request['id']
                if self.store.receipt(key, request) is not None:
                    return f
                if not request['text'].strip():
                    raise ValueError('Add some input before posting')
                if len(f['inputs']) >= 100 or sum(len(c['text']) for c in f['inputs']) + len(request['text']) > 80_000:
                    raise ValueError('This feature has reached its input limit')
                f['inputs'].append({**request, 'time': time.time()})
                self.store.record(f, request['author'], 'Added comment')
                self.store.commit_receipt(f, key, request, {'saved': True}); self.announce(ident)
                return f
            before = f['input_revision']
            f = self.store.contribute(ident, request)
            if before == f['input_revision']:
                return f
            p = f['pipeline']
            p.pop('pending_start', None)
            if p.get('execution'):
                p['execution']['status'] = 'superseded'
            for run in p['runs']:
                if run['status'] in ACTIVE:
                    run['status'] = 'superseded'
            p.update(approvals={}, status='scope_changed', current_stage='ideation', release=None,
                checks={}, repairs=0, applied=None, stages={s: {'status': 'pending', 'run_id': None} for s in STAGES})
            f['status'] = 'scope_changed'; self.store.put(f); self.announce(ident)
        await self.stop_execution(ident, execution)
        return self.get(ident)

    async def feedback_managed(self, ident, request):
        async with self.features.lock(ident):
            f = self.get(ident); self.store.expect_revision(f, request['revision'])
            execution = f['pipeline'].get('execution', {})
            self.require_execution(f, execution.get('workflow_id'))
            if f['pipeline']['status'] not in ('running', 'paused'):
                raise FeatureConflict('No active stage to receive feedback')
            if not request['text'].strip():
                raise ValueError('Feedback cannot be empty')
            controls = execution.setdefault('controls', [])
            control = next((c for c in controls if c['ident'] == request['id']), None)
            if control and control.get('text') != request['author'] + ': ' + request['text']:
                raise FeatureConflict('Feedback ID was reused with different contents')
            if control is None:
                control = {'ident': request['id'], 'action': 'feedback', 'text': request['author'] + ': ' + request['text'], 'sequence': len(controls) + 1}
                controls.append(control)
                self.store.record(f, request['author'], 'Live feedback: ' + request['text'])
                self.store.put(f); self.announce(ident)
        await self.runtime.control_pipeline(execution['workflow_id'], **control)
        return self.get(ident)

    async def pipeline_event(self, ident, execution_id, event):
        async with self.features.lock(ident):
            self.record_pipeline_event(ident, execution_id, event)

    def record_pipeline_event(self, ident, execution_id, event):
        f = self.get(ident); p = f['pipeline']
        if p.get('execution', {}).get('workflow_id') != execution_id:
            return
        envelope = event.model_dump(mode='json')
        data = envelope['event']; kind = data['type']
        if kind not in ('model_interaction_started', 'model_interaction_ended', 'tool_start', 'tool_end',
                        'tool_error', 'tool_approval_requested', 'tool_approval_resolved', 'subagent_started', 'subagent_stopped'):
            return
        identity = digest(envelope)
        cursors = p.setdefault('event_cursors', {})
        cursor = cursors.get(envelope['agent_id'], {'time': -1, 'ids': []})
        if envelope['timestamp'] < cursor['time'] or (envelope['timestamp'] == cursor['time'] and identity in cursor['ids']):
            return
        cursors[envelope['agent_id']] = {'time': envelope['timestamp'],
            'ids': [*(cursor['ids'] if envelope['timestamp'] == cursor['time'] else []), identity]}
        if kind == 'subagent_started':
            p.setdefault('agent_workflows', {})[data['subagent_id']] = data['workflow_id']
        row = {k: data[k] for k in ('type', 'tool_id', 'tool_name', 'model', 'usage', 'subagent_id', 'workflow_id', 'agent_key', 'approved', 'reason') if k in data}
        row.update(time=envelope['timestamp'], agent_id=envelope['agent_id'])
        p['telemetry'] = [*p.get('telemetry', []), row][-300:]
        child = p.get('agent_workflows', {}).get(envelope['agent_id'])
        run = next((r for r in p['runs'] if child and r['workflow_id'] == child), None)
        if run:
            run['telemetry'] = [*run.get('telemetry', []), row][-200:]
        self.store.put(f); self.announce(ident)
