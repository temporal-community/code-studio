"""Durable stage execution, explicit approvals, and visible handoffs for shared features."""
import asyncio
from contextlib import suppress
from datetime import timedelta
import json
import os
import time
import uuid

from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.common import WorkflowIDReusePolicy
from temporal_agent_harness.harness.agent_client import AgentClient, AgentTurnError, AgentTurnTimeout, CallbackResultError
from temporal_agent_harness.harness.agent_protocol import AgentConfig

from features.runtime import TemporalFeatureRuntime
from features.storage import ACTIVE, FeatureConflict
from sdlc.models import digest
from .checks import CheckRunner
from .definitions import STAGES, DEFINITIONS, files_for
from .models import StageResult, StageSettings, validate_project
from .release import preview
from .harness_runtime import PipelineRuntimeMixin
from .pipeline_host import PipelineHostMixin
from .library import freeze_library, library_files, read_library
from .experiments import ExperimentService
from .validation import ValidationService
from .context import optimize_context
from .clarification import ClarificationService, check_submission
from .browser_preview import BrowserPreviewService


class StageRuntime(PipelineRuntimeMixin, TemporalFeatureRuntime):
    async def explain_questions(self, workflow_id, spec):
        from .clarification import ClarificationReply
        temporal = await self.connect()
        try:
            handle = await temporal.start_workflow('CodeStudioStageClarification', spec, id=workflow_id,
                task_queue=os.environ.get('AGENT_DAG_TASK_QUEUE', 'agent-dag'),
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                execution_timeout=timedelta(minutes=3), result_type=ClarificationReply)
        except WorkflowAlreadyStartedError:
            handle = temporal.get_workflow_handle(workflow_id, result_type=ClarificationReply)
        return await handle.result()

    async def resolve_merge_conflict(self, spec):
        from .merge_resolution import MergeResolution
        temporal = await self.connect()
        return await temporal.execute_workflow('CodeStudioMergeResolution', spec,
            id='code-studio-merge-' + uuid.uuid4().hex,
            task_queue=os.environ.get('AGENT_DAG_TASK_QUEUE', 'agent-dag'),
            execution_timeout=timedelta(minutes=3), result_type=MergeResolution)

    async def suggest_improvement(self, spec):
        from .suggestions import FeatureSuggestion
        temporal = await self.connect()
        # A unique workflow per click: no cached example or shared conversation.
        return await temporal.execute_workflow('CodeStudioImprovementSuggestion', spec,
            id='code-studio-suggestion-' + uuid.uuid4().hex,
            task_queue=os.environ.get('AGENT_DAG_TASK_QUEUE', 'agent-dag'),
            execution_timeout=timedelta(minutes=3), result_type=FeatureSuggestion)

    async def execute(self, run, callback, event):
        temporal = await self.connect()
        try:
            await temporal.start_workflow('SdlcStageAgent', AgentConfig(), id=run['workflow_id'],
                task_queue=os.environ.get('AGENT_DAG_TASK_QUEUE', 'agent-dag'), execution_timeout=timedelta(minutes=20))
        except WorkflowAlreadyStartedError: pass
        client = AgentClient(temporal, run['workflow_id'])
        stream = await client.send_message('execute', {'stage': run['stage'], 'context': run['context'], 'project': run['project']},
            on_item=lambda item, _offset: item, update_id='execute:' + run['id'], timeout=1100)
        async def consume():
            async for item in stream:
                if isinstance(item, (AgentTurnError, AgentTurnTimeout)): raise item
                if getattr(item, 'event', None) is not None: event(item.event)
        reader = asyncio.create_task(consume())
        try:
            while not reader.done():
                for call in await asyncio.wait_for(client.get_pending_callbacks(), 10):
                    try:
                        try: result = await callback(call.tool_id, call.tool_name, call.tool_input)
                        except (ValueError, TypeError, KeyError, OSError) as exc:
                            await client.provide_callback_result(call.tool_id, error=str(exc), update_id='result:' + call.tool_id)
                        else:
                            await client.provide_callback_result(call.tool_id, result=json.dumps(result) if call.tool_name == 'save_stage_result' else result,
                                update_id='result:' + call.tool_id)
                    except CallbackResultError as exc:
                        if exc.error_type not in ('UnknownCallback', 'CallbackAlreadyResolved'): raise
                await asyncio.wait({reader}, timeout=.25)
            await reader
        finally:
            reader.cancel()
            with suppress(asyncio.CancelledError, Exception): await reader


class PipelineService(PipelineHostMixin):
    def __init__(self, features, publish, *, runtime=None):
        self.features, self.store, self.git = features, features.store, features.git
        self.workspace = features.workspace
        self.publish = publish
        self.runtime = runtime or StageRuntime()
        self.enabled = features.enabled or runtime is not None
        self.tasks = {}
        self.integration_jobs = {}
        self.integration_errors = {}
        self.validating_integrations = set()
        self.slots = asyncio.Semaphore(2)
        self.checks = CheckRunner(self.git)
        self.experiments = ExperimentService(self)
        self.validation = ValidationService(self)
        self.clarifications = ClarificationService(self)
        self.browser_previews = BrowserPreviewService(self)

    def announce(self, ident):
        self.publish({'type': 'feature-changed', 'feature_id': ident})

    def get(self, ident):
        f = self.store.get(ident)
        if 'pipeline' not in f: raise ValueError('This is a legacy feature. Start a new feature in the cooperative workspace.')
        return f

    def run(self, ident, run_id):
        f = self.get(ident)
        run = next((r for r in f['pipeline']['runs'] if r['id'] == run_id), None)
        if not run: raise ValueError('Stage run not found')
        return f, run

    async def start(self):
        self.browser_previews.closing = False
        if self.enabled:
            await self.clarifications.recover()
        await self.experiments.recover()
        for item in self.store.list():
            f = self.store.get(item['id'])
            if 'pipeline' not in f: continue
            if f['pipeline'].get('execution'):
                if self.enabled and f['pipeline']['execution']['status'] == 'active':
                    self.schedule_pipeline(f['id'])
                elif self.enabled and f['pipeline'].get('pending_start'):
                    pending = f['pipeline']['pending_start']
                    await self.stop_execution(f['id'], f['pipeline']['execution'])
                    await self.start_pipeline(f['id'], pending['stage'], pending['author'])
                continue
            active = [r for r in f['pipeline']['runs'] if r['status'] in ACTIVE]
            for run in active:
                if self.enabled: self.schedule(f['id'], run['id'])
            if not active and f['pipeline']['status'] == 'ready' and self.enabled:
                await self.handoff(f['id'], f['pipeline']['current_stage'], 'Workflow handoff')

    async def stop(self):
        await self.clarifications.stop()
        tasks = [*self.tasks.values(), *self.integration_jobs.values()]
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.validation.stop()
        await self.browser_previews.stop()

    async def create(self, request, *, auto_start=True, experiment_config=None, inputs=None):
        if not request['title'].strip() or not request['description'].strip():
            raise ValueError('A title and description are required')
        try:
            existing = self.store.get(request['id'])
        except ValueError:
            existing = None
        if existing and 'pipeline' in existing:
            if existing['request_hash'] != digest(request) or existing.get('experiment_config') != experiment_config:
                raise FeatureConflict('Feature ID was reused for different inputs')
            return existing
        f = await self.features.create(request, allow_snapshot=request.get('source_mode', 'current') == 'current', snapshot_current=request.get('source_mode', 'current') == 'current')
        async with self.features.lock(f['id']):
            f = self.store.get(f['id'])
            if 'pipeline' not in f:
                if experiment_config:
                    f['experiment_config'] = experiment_config
                    f['experiment_id'] = experiment_config['experiment_id']
                    f['experiment_arm'] = experiment_config['arm']
                    f['inputs'] = inputs or []
                f['pipeline'] = {'status': 'draft', 'current_stage': 'ideation', 'runs': [],
                    'stages': {s: {'status': 'pending', 'run_id': None} for s in STAGES},
                    'approvals': {}, 'checks': {}, 'release': None, 'repairs': 0, 'controls': {}, 'applied': None,
                    'harness_version': 2 if getattr(self.runtime, 'supports_pipeline', False) else 1}
                self.store.put(f)
                self.announce(f['id'])
        if auto_start and f['status'] != 'branch_failed' and f['pipeline']['status'] == 'draft' and not f['pipeline']['runs']:
            await self.handoff(f['id'], 'ideation', request['author'])
        return self.get(f['id'])

    async def handoff(self, ident, stage, author):
        try:
            if self.get(ident)['pipeline'].get('harness_version') == 2:
                await self.start_pipeline(ident, stage, author)
                return
            await self.launch(ident, stage, author)
        except Exception as exc:
            async with self.features.lock(ident):
                f = self.get(ident); p = f['pipeline']
                if p['status'] not in ('ready', 'draft') or p['current_stage'] != stage:
                    return
                p['status'] = f['status'] = 'failed'
                p['stages'][stage]['status'] = 'failed'
                f['error'] = str(exc)[:2500]
                self.store.record(f, 'Workflow', 'Could not start ' + stage + ': ' + f['error'])
                self.store.put(f); self.announce(ident)

    def require_active(self, f, run):
        p = f['pipeline']
        permitted_status = ('running', 'paused') if run.get('harness_version') == 2 else ('running',)
        if run['status'] not in ACTIVE or run['input_revision'] != f['input_revision'] or p['status'] not in permitted_status or p['stages'][run['stage']]['run_id'] != run['id']:
            raise FeatureConflict('This stage is no longer active for the current feature scope')
        if STAGES.index(run['stage']) >= STAGES.index('design') and p['approvals'].get('planner', {}).get('revision') != f['input_revision']:
            raise FeatureConflict('The current plan must be approved before implementation')
        if run['stage'] == 'deployment' and p['approvals'].get('review', {}).get('revision') != f['input_revision']:
            raise FeatureConflict('The current review must be approved before release preparation')

    async def launch(self, ident, stage, author, *, run_id=None, execution_id=None):
        if not self.enabled: raise ValueError('Start just dev with a model key and worker to run agents')
        async with self.features.lock(ident):
            f = self.get(ident); p = f['pipeline']
            if execution_id:
                self.require_execution(f, execution_id)
                if any(r['id'] == run_id for r in p['runs']):
                    return f
            if any(r['status'] in ACTIVE for r in p['runs']): raise FeatureConflict('An agent is already working on this feature')
            if len(p['runs']) >= 40: raise ValueError('This feature reached its 40-stage-run limit')
            if stage not in STAGES: raise ValueError('Unknown stage')
            if stage != 'ideation':
                previous = STAGES[STAGES.index(stage) - 1]
                if p['stages'][previous]['status'] not in ('completed', 'approved'):
                    raise FeatureConflict('Finish the previous stage before starting this agent')
            if STAGES.index(stage) >= 2 and p['approvals'].get('planner', {}).get('revision') != f['input_revision']:
                raise FeatureConflict('Approve the plan first')
            if stage == 'deployment' and p['approvals'].get('review', {}).get('revision') != f['input_revision']:
                raise FeatureConflict('Approve the review before preparing a release')
            experiment = f.get('experiment_config')
            project = experiment['projects'][stage] if experiment else self.workspace.agent_project_snapshot(stage)
            await validate_project(stage, project['files'])
            source_hash = await asyncio.to_thread(self.checks.fingerprint, f)
            checks = {name: {**value, 'output': value.get('output', '')[-4000:]} for name, value in p['checks'].items()}
            run_id = run_id or uuid.uuid4().hex
            run = {'id': run_id, 'stage': stage, 'status': 'queued', 'author': author, 'input_revision': f['input_revision'],
                'workflow_id': 'sdlc-stage-' + ident + '-' + run_id, 'project': project, 'result': None,
                'created_at': time.time(), 'trace': [], 'usage': None, 'error': None,
                'context': {'feature': {**{k: f[k] for k in ('title', 'description', 'acceptance', 'branch')}, 'inputs': [i for i in f['inputs'] if i['kind'] != 'comment']},
                            'checks': checks,
                            'diff': (await asyncio.to_thread(self.git.diff, f))['patch'][:50_000],
                            'source_hash': source_hash, 'available_checks': self.checks.available(f)}}
            if execution_id:
                run.update(harness_version=2, execution_id=execution_id, workflow_id='', status='running')
            playbook = experiment['playbook'] if experiment else self.experiments.playbook()['content']
            run['library'] = freeze_library(f, stage, project, conventions=playbook,
                design_files=experiment.get('design_system', {}) if experiment else None)
            await asyncio.to_thread(optimize_context, f, run, self.git, playbook)
            p['runs'].append(run); p['current_stage'] = stage; p['status'] = 'running'
            p['stages'][stage] = {'status': 'running', 'run_id': run_id}
            f['status'], f['error'] = stage, None
            self.store.record(f, author, 'Started ' + DEFINITIONS[stage]['name'] + ' · agent ' + project['version'][:8])
            self.store.put(f); self.announce(ident)
            if not execution_id:
                self.schedule(ident, run_id)
        return self.get(ident)

    def schedule(self, ident, run_id):
        key = (ident, run_id)
        if key not in self.tasks:
            task = asyncio.create_task(self.work(ident, run_id)); self.tasks[key] = task
            task.add_done_callback(lambda _: self.tasks.pop(key, None))

    async def work(self, ident, run_id):
        try:
            async with self.slots:
                f, run = self.run(ident, run_id)
                self.require_active(f, run)
                run['status'] = 'running'; self.store.put(f); self.announce(ident)
                await self.runtime.execute(run, lambda call, name, args: self.callback(ident, run_id, call, name, args),
                    lambda event: self.event(ident, run_id, event))
                f, run = self.run(ident, run_id)
                if not run['result']: raise ValueError('The agent finished without saving its stage handoff')
                if run['stage'] in ('development', 'review', 'deployment'):
                    await self.final_checks(ident, run_id)
                next_stage = await self.finish(ident, run_id)
                with suppress(Exception): await asyncio.wait_for(self.runtime.close_run(run['workflow_id']), 5)
            if next_stage: await self.handoff(ident, next_stage, DEFINITIONS[run['stage']]['name'] + ' handoff')
        except asyncio.CancelledError: raise
        except Exception as exc:
            f, run = self.run(ident, run_id)
            if run['status'] in ACTIVE:
                message = str(exc)[:2500] or type(exc).__name__
                secret = os.environ.get('OPENAI_API_KEY')
                if secret: message = message.replace(secret, '[redacted]')
                run['status'], run['error'] = 'failed', message
                f['pipeline']['status'] = f['status'] = 'failed'; f['error'] = message
                f['pipeline']['stages'][run['stage']]['status'] = 'failed'
                self.store.record(f, DEFINITIONS[run['stage']]['name'], 'Stage failed: ' + message)
                self.store.put(f); self.announce(ident)
            with suppress(Exception): await asyncio.wait_for(self.runtime.cancel_run(run['workflow_id']), 5)

    async def finish(self, ident, run_id, *, managed=False):
        async with self.features.lock(ident):
            f, run = self.run(ident, run_id); self.require_active(f, run)
            p = f['pipeline']; stage = run['stage']; result = run['result']; next_stage = None
            if stage in ('development', 'review', 'deployment'):
                current = await asyncio.to_thread(self.checks.fingerprint, f)
                for check, evidence in list(p['checks'].items()):
                    if evidence.get('source_hash') != current:
                        p['checks'][check] = {**evidence, 'status': 'stale'}
                if stage == 'deployment' and (current != p['approvals'].get('review', {}).get('source_hash')
                        or current != (p.get('release') or {}).get('workspace_hash')):
                    result['verdict'] = 'blocked'
                    result['questions'].append('Release source must match the reviewed source and packaged preview.')
            from .manual_approval import accepted_check
            if stage == 'deployment':
                for name, check in p['checks'].items():
                    if check['status'] in ('failed', 'timed_out', 'stale') and not accepted_check(p, name, check, current, f['input_revision']):
                        result['verdict'] = 'blocked'
                        detail = {'failed': 'failed; fix the reported errors and retry Deployment',
                                  'timed_out': 'timed out; inspect the last test in Checks, then retry Deployment or choose Manual Approve',
                                  'stale': 'has outdated inputs; retry Deployment to validate the current source'}[check['status']]
                        result['questions'].append(f'{name.replace("_", " ")} {detail}.')
            if stage == 'review':
                current = await asyncio.to_thread(self.checks.fingerprint, f)
                if current != run['context']['source_hash']:
                    result['questions'].append('Files changed during review. Rerun Review against the saved code.')
                    result['verdict'] = 'blocked'
                run['reviewed_hash'] = current
                if any(c['status'] == 'failed' for c in p['checks'].values()): result['verdict'] = 'changes_requested'
                if any(c['status'] in ('timed_out', 'stale') for c in p['checks'].values()):
                    result['verdict'] = 'blocked'
                    result['questions'].append('Validation infrastructure needs attention. Resolve the check issue and retry, or use Manual Approve; do not repeat code repairs for stale checks.')
            if stage == 'deployment' and not p['release'] and not result['questions'] and result['verdict'] != 'blocked':
                raise ValueError('Deployment must create a local release preview before completing')
            run['status'], run['finished_at'] = 'completed', time.time()
            p['stages'][stage]['status'] = 'completed'
            if managed:
                run['outcome'] = {'stage': stage, 'run_id': run_id, 'verdict': result['verdict'],
                    'questions': result['questions'], 'source_hash': run.get('reviewed_hash', ''),
                    'summary': result['summary']}
                self.store.put(f); self.announce(ident)
                return run['outcome']
            if result['questions'] or result['verdict'] == 'blocked' or (result['verdict'] == 'changes_requested' and stage != 'review'):
                p['status'] = 'blocked'; p['stages'][stage]['status'] = 'needs_input'
            elif stage == 'review' and result['verdict'] == 'changes_requested':
                if p['repairs'] < 2:
                    p['repairs'] += 1; next_stage = 'development'; p['stages']['development']['status'] = 'pending'
                    p['stages']['review']['status'] = 'pending'
                else:
                    p['status'] = 'blocked'; p['stages'][stage]['status'] = 'needs_input'
            elif stage in ('planner', 'review'):
                p['status'] = 'needs_plan_approval' if stage == 'planner' else 'needs_release_approval'
                p['stages'][stage]['status'] = 'needs_approval'
            elif stage == 'deployment':
                if not p['release']: raise ValueError('Deployment must create a local release preview before completing')
                p['status'] = 'complete'
            else: next_stage = STAGES[STAGES.index(stage) + 1]
            if next_stage:
                p['status'], p['current_stage'] = 'ready', next_stage
                self.store.record(f, DEFINITIONS[stage]['name'], 'Handed work to ' + DEFINITIONS[next_stage]['name'])
            else: self.store.record(f, DEFINITIONS[stage]['name'], 'Finished: ' + result['summary'][:300])
            f['status'] = p['status']; self.store.put(f); self.announce(ident)
            return next_stage

    def event(self, ident, run_id, event):
        data = event.model_dump(mode='json') if hasattr(event, 'model_dump') else event
        if isinstance(data, dict) and data.get('usage'):
            f, run = self.run(ident, run_id); run['usage'] = data['usage']; self.store.put(f); self.announce(ident)

    async def perform_check(self, ident, run_id, check, *, force=False, targets=()):
        return await self.validation.run(ident, run_id, check, force=force, targets=targets)

    async def final_checks(self, ident, run_id):
        # All stages validate full coverage; reuse still verifies dependency/environment identity.
        checks = ('syntax', 'python_tests', 'browser_tests', 'types', 'build')
        async def validate(check):
            result = await self.perform_check(ident, run_id, check)
            if result['status'] == 'stale':
                result = await self.perform_check(ident, run_id, check, force=True)
            return result
        results = await asyncio.gather(*(validate(check) for check in checks))
        async with self.features.lock(ident):
            f, run = self.run(ident, run_id); self.require_active(f, run)
            for check, result in zip(checks, results):
                identity = await asyncio.to_thread(self.checks.identity, f, check)
                if result.get('cache_key') != identity['cache_key'] or result['source_hash'] != identity['source_hash']:
                    result = {**result, 'status': 'stale', 'output': result.get('output', '') + '\nFinal validation inputs changed; rerun validation.'}
                    f['pipeline']['checks'][check] = result
            self.store.put(f)


    async def callback(self, ident, run_id, call_id, name, args):
        if name == 'stage_browser':
            from agent_dag.browser_tools import browser_enabled
            async with self.features.lock(ident):
                f, run = self.run(ident, run_id); self.require_active(f, run)
                settings = StageSettings.model_validate_json(run['project']['files'][files_for(run['stage'])[2]])
                if run['stage'] not in ('development', 'review') or not browser_enabled(settings, run['context']):
                    raise ValueError('Read-only browser is unavailable for this saved stage.')
                if len(run['trace']) >= 120: raise ValueError('Stage tool limit reached')
                self.reserve_tool_call(f, run, call_id)
            evidence_id = digest([ident, run_id, call_id])
            result = await self.browser_previews.observe(ident, run_id, args, evidence_id)
            async with self.features.lock(ident):
                f, run = self.run(ident, run_id); self.require_active(f, run)
                if not any(t.get('evidence_id') == evidence_id for t in run['trace']):
                    run['trace'].append({'name': name, 'detail': result['path'], 'evidence_id': evidence_id, 'time': time.time()})
                    self.store.put(f); self.announce(ident)
            return result
        # Independent reads/checks can overlap; writes remain serialized under the feature lock.
        if name in ('stage_run_check', 'stage_list_files', 'stage_read_file', 'stage_read_range', 'stage_search_source', 'stage_list_shared_files', 'stage_read_shared_file'):
            payload = {'name': name, 'args': args}; key = 'pipeline:' + ident + ':' + run_id + ':' + call_id
            async with self.features.lock(ident):
                f, run = self.run(ident, run_id)
                old = self.store.receipt(key, payload)
                if old is not None: return old
                self.require_active(f, run)
                settings = StageSettings.model_validate_json(run['project']['files'][files_for(run['stage'])[2]])
                shared = name in ('stage_list_shared_files', 'stage_read_shared_file') and 'library' in run
                query = name in ('stage_read_range', 'stage_search_source') and run['context'].get('optimization_version') and 'stage_read_file' in settings.tools
                if name not in settings.tools and not shared and not query: raise ValueError('Tool disabled for this saved agent and stage')
                if len(run['trace']) >= 120: raise ValueError('Stage tool limit reached')
                self.reserve_tool_call(f, run, call_id)
            if name == 'stage_run_check':
                result = await self.perform_check(ident, run_id, args['check'], force=args.get('force', False), targets=args.get('targets', []))
            elif name in ('stage_read_range', 'stage_search_source'):
                from .source_queries import read_range, search
                query = read_range if name == 'stage_read_range' else search
                result = await asyncio.to_thread(query, self.git, f, **args)
            elif name == 'stage_list_files':
                result = {'files': await asyncio.to_thread(self.git.files, f)}
            elif name == 'stage_list_shared_files':
                result = {'files': sorted(library_files(f, run_id))}
            elif name == 'stage_read_shared_file':
                result = read_library(f, args['path'], run_id)
            else:
                result = await asyncio.to_thread(self.git.read, f, args['path'])
            async with self.features.lock(ident):
                f, run = self.run(ident, run_id); self.require_active(f, run)
                if name != 'stage_run_check':
                    run['trace'].append({'name': name, 'detail': args.get('path', ''), 'time': time.time()})
                self.store.commit_receipt(f, key, payload, result)
                self.announce(ident)
            return result
        async with self.features.lock(ident):
            f, run = self.run(ident, run_id)
            payload = {'name': name, 'args': args}; key = 'pipeline:' + ident + ':' + run_id + ':' + call_id
            old = self.store.receipt(key, payload)
            if old is not None: return old
            self.require_active(f, run)
            settings = StageSettings.model_validate_json(run['project']['files'][files_for(run['stage'])[2]])
            patch = (name == 'stage_patch_file' and run['context'].get('source_patch_version') == 1
                     and {'stage_read_file', 'stage_write_file'} <= set(settings.tools))
            if name != 'save_stage_result' and name not in settings.tools and not patch: raise ValueError('Tool disabled for this saved agent and stage')
            if len(run['trace']) >= 120: raise ValueError('Stage tool limit reached')
            self.reserve_tool_call(f, run, call_id)
            if name == 'save_stage_result':
                result_data = StageResult.model_validate_json(args['result_json']).model_dump()
                if run['result']: raise ValueError('This stage already saved a result')
                rows = [json.loads(row[0]) for row in self.store.db.execute('SELECT value FROM feature_writes')]
                actual = {r['path'] for r in rows if r['feature_id'] == ident and r['job_id'] == run_id and r['status'] == 'completed'}
                # File ownership is recorded by the callback host, not inferred by the model.
                # Read-only stages often list the upstream diff they reviewed; retain their
                # artifact while recording only writes actually completed in this run.
                reported = result_data['changed_files']
                if set(reported) != actual:
                    run['reported_changed_files'] = reported
                    run['trace'].append({'name': 'reconcile_changed_files',
                        'detail': f'Recorded {len(actual)} files written by this run; the model listed {len(reported)}.',
                        'time': time.time()})
                result_data['changed_files'] = sorted(actual)
                run['result'] = result_data; result = {'saved': True}
            elif name in ('stage_write_file', 'stage_patch_file'):
                if run['stage'] != 'development': raise ValueError('Only Development can write feature source files')
                def authorize(): self.require_active(*self.run(ident, run_id))
                patch_args = {'old_text': args['old_text'], 'new_text': args['new_text']} if patch else None
                result = await self.features.write(ident, run_id, call_id, args['path'], args['expected_hash'],
                    None if patch else args['content'], DEFINITIONS[run['stage']]['name'], authorize=authorize, patch=patch_args)
                f, run = self.run(ident, run_id)
            elif name == 'create_release_preview':
                approved = f['pipeline']['approvals'].get('review', {})
                if await asyncio.to_thread(self.checks.fingerprint, f) != approved.get('source_hash'):
                    raise FeatureConflict('Files changed after review approval. Rerun Review before packaging.')
                result = await asyncio.to_thread(preview, self.git, f, run_id)
                if await asyncio.to_thread(self.checks.fingerprint, f) != approved['source_hash']:
                    raise FeatureConflict('Files changed while packaging. Rerun Review before packaging.')
                result['workspace_hash'] = approved['source_hash']; f['pipeline']['release'] = result
            else: raise ValueError('Unknown stage tool')
            run['trace'].append({'name': name, 'detail': args.get('path', args.get('check', '')), 'time': time.time()})
            self.store.commit_receipt(f, key, payload, result); self.announce(ident)
            return result

    def reserve_tool_call(self, feature, run, call_id):
        """Count in-flight calls too, so a gathered script cannot overrun the tool budget."""
        calls = run.setdefault('tool_calls', [])
        if call_id not in calls:
            if len(calls) >= 120:
                raise ValueError('Stage tool limit reached')
            calls.append(call_id)
            self.store.put(feature)

    async def control(self, ident, action, request):
        if self.get(ident)['pipeline'].get('harness_version') == 2:
            return await self.control_managed(ident, action, request)
        to_stop = []; next_stage = None
        async with self.features.lock(ident):
            f = self.get(ident); p = f['pipeline']
            self.store.expect_revision(f, request['revision'])
            fingerprint = digest({'action': action, **request})
            old = p['controls'].get(request['id'])
            if old:
                if old != fingerprint: raise FeatureConflict('Action ID was reused for a different request')
                return f
            active = [r for r in p['runs'] if r['status'] in ACTIVE]
            if action == 'pause':
                to_stop = active
                for run in active:
                    run['status'] = 'cancelled'; p['stages'][run['stage']]['status'] = 'paused'
                p['status'] = 'paused'
            elif action == 'approve':
                stage = p['current_stage']
                expected = {'planner': 'needs_plan_approval', 'review': 'needs_release_approval'}
                if p['status'] != expected.get(stage) or active: raise FeatureConflict('No current stage is awaiting approval')
                run = next(r for r in p['runs'] if r['id'] == p['stages'][stage]['run_id'])
                approval = {'run_id': run['id'], 'author': request['author'], 'revision': f['input_revision'], 'time': time.time()}
                if stage == 'review':
                    current = await asyncio.to_thread(self.checks.fingerprint, f)
                    if current != run['reviewed_hash']: raise FeatureConflict('Files changed since Review. Rerun Review before approving.')
                    approval['source_hash'] = current
                p['approvals'][stage] = approval; p['stages'][stage]['status'] = 'approved'
                next_stage = STAGES[STAGES.index(stage) + 1]
            elif action in ('resume', 'rerun'):
                if active: raise FeatureConflict('Pause the current agent before rerunning a stage')
                next_stage = request.get('stage') or p['current_stage']
                if next_stage not in STAGES: raise ValueError('Unknown stage')
                index = STAGES.index(next_stage)
                if index and p['stages'][STAGES[index - 1]]['status'] not in ('completed', 'approved'):
                    raise FeatureConflict('Finish the previous stage before starting this agent')
                if index >= 2 and p['approvals'].get('planner', {}).get('revision') != f['input_revision']:
                    raise FeatureConflict('Approve the plan first')
                if next_stage == 'deployment' and p['approvals'].get('review', {}).get('revision') != f['input_revision']:
                    raise FeatureConflict('Approve the review first')
                for stage in STAGES[STAGES.index(next_stage):]:
                    p['stages'][stage] = {'status': 'pending', 'run_id': None}
                    p['approvals'].pop(stage, None)
                p['release'] = None; p['applied'] = None
                if STAGES.index(next_stage) <= STAGES.index('development'): p['checks'] = {}
            else: raise ValueError('Unknown feature action')
            if next_stage: p['status'], p['current_stage'] = 'ready', next_stage
            p['controls'][request['id']] = fingerprint
            f['status'] = p['status']; f['error'] = None
            self.store.record(f, request['author'], action.capitalize() + (' ' + (request.get('stage') or p['current_stage'])))
            self.store.put(f); self.announce(ident)
        for run in to_stop:
            task = self.tasks.get((ident, run['id']))
            if task: task.cancel(); await asyncio.gather(task, return_exceptions=True)
            with suppress(Exception): await asyncio.wait_for(self.runtime.cancel_run(run['workflow_id']), 5)
        if next_stage: await self.handoff(ident, next_stage, request['author'])
        return self.get(ident)

    async def contribute(self, ident, request):
        if self.get(ident)['pipeline'].get('harness_version') == 2:
            return await self.contribute_managed(ident, request)
        async with self.features.lock(ident):
            before = self.get(ident); active = [r for r in before['pipeline']['runs'] if r['status'] in ACTIVE]
            if check_submission(self, before, request):
                return before
            if request['kind'] == 'comment':
                key = ident + ':input:' + request['id']
                if self.store.receipt(key, request) is not None: return before
                if not request['text'].strip(): raise ValueError('Add some input before posting')
                if len(before['inputs']) >= 100 or sum(len(c['text']) for c in before['inputs']) + len(request['text']) > 80_000:
                    raise ValueError('This feature has reached its input limit')
                before['inputs'].append({**request, 'time': time.time()})
                self.store.record(before, request['author'], 'Added comment')
                self.store.commit_receipt(before, key, request, {'saved': True}); self.announce(ident)
                return before
            f = self.store.contribute(ident, request)
            if f['input_revision'] == before['input_revision']: return f
            p = f['pipeline']
            for run in p['runs']:
                if run['status'] in ACTIVE: run['status'] = 'superseded'
            p['approvals'] = {}; p['status'] = 'scope_changed'; p['current_stage'] = 'ideation'; p['release'] = None
            p['checks'] = {}; p['repairs'] = 0; p['applied'] = None
            p['stages'] = {s: {'status': 'pending', 'run_id': None} for s in STAGES}
            f['status'] = p['status']; self.store.put(f); self.announce(ident)
        for run in active:
            task = self.tasks.get((ident, run['id']))
            if task: task.cancel(); await asyncio.gather(task, return_exceptions=True)
            with suppress(Exception): await asyncio.wait_for(self.runtime.cancel_run(run['workflow_id']), 5)
        return self.get(ident)

    async def apply(self, ident, request):
        self.require_integration_idle(ident)
        async with self.features.lock(ident):
            f = self.get(ident); p = f['pipeline']; self.store.expect_revision(f, request['revision'])
            from .integration import Integrations
            if f.get('experiment_id'):
                raise FeatureConflict('Experiment attempts stay isolated. Adopt their changes before integration')
            integrations = Integrations(self.git, self.checks)
            record = integrations.load(f)
            if not record: raise FeatureConflict('Prepare and validate an integration preview before applying')
            record = await asyncio.to_thread(integrations.apply, f, record, request['author'])
            result = {'applied': [c['path'] for c in record['changes']], 'source_directory': record['source_directory'],
                      'time': record['applied_at'], 'author': record['apply_author'], 'integration_id': record['id']}
            p['applied'] = result; self.store.record(f, request['author'], 'Applied and verified integration; rollback backup retained')
            self.store.put(f); self.announce(ident); return result

    async def integration(self, ident, request=None, action='get'):
        from .integration import Integrations
        integrations = Integrations(self.git, self.checks)
        if action == 'get':
            record = integrations.load(self.get(ident))
            if record and record['status'] == 'applied':
                record['git_receipt'] = await asyncio.to_thread(integrations.inspect_commit, record)
            active = ident in self.validating_integrations or (ident in self.integration_jobs and not self.integration_jobs[ident].done())
            public = integrations.public(record)
            if not public and (active or ident in self.integration_errors):
                public = {'status': 'preparing' if active else 'failed', 'changes': [], 'checks': {}, 'conflicts': []}
            if public:
                public['operation_active'] = active
                public['auto_merge'] = ident in self.integration_jobs and not self.integration_jobs[ident].done()
                if ident in self.integration_errors: public['error'] = self.integration_errors[ident]
            return public
        if action in ('merge', 'resolve-agent'):
            if ident in self.validating_integrations or (ident in self.integration_jobs and not self.integration_jobs[ident].done()):
                return await self.integration(ident)
            async with self.features.lock(ident):
                self.store.expect_revision(self.get(ident), request['revision'])
                if ident not in self.integration_jobs or self.integration_jobs[ident].done():
                    self.integration_errors.pop(ident, None)
                    self.integration_jobs[ident] = asyncio.create_task(self.merge_into_project(ident, request, resolve_conflicts=action == 'resolve-agent'))
            return await self.integration(ident)
        self.require_integration_idle(ident)
        async with self.features.lock(ident):
            f = self.get(ident); self.store.expect_revision(f, request['revision'])
            record = integrations.load(f)
            if action == 'prepare':
                if record and record.get('request_id') == request['id']: return integrations.public(record)
                record = await asyncio.to_thread(integrations.prepare, f, request['author'])
                record['request_id'] = request['id']; integrations.save(record)
                f['pipeline']['integration_id'] = record['id']
            elif not record:
                raise FeatureConflict('Prepare an integration preview first')
            elif action == 'validate':
                self.validating_integrations.add(ident)
                try: record = await integrations.validate(f, record)
                finally: self.validating_integrations.discard(ident)
            elif action == 'resolve':
                record = await asyncio.to_thread(integrations.resolve, f, record, request)
            elif action == 'rollback':
                record = await asyncio.to_thread(integrations.rollback, record, request['author'])
                if record['status'] == 'rolled_back': f['pipeline']['applied'] = None
            else: raise ValueError('Unknown integration action')
            self.store.record(f, request['author'], 'Integration ' + record['status'])
            self.store.put(f); self.announce(ident)
            return integrations.public(record)

    def require_integration_idle(self, ident):
        job = self.integration_jobs.get(ident)
        if (job and not job.done() and job is not asyncio.current_task()) or ident in self.validating_integrations:
            raise FeatureConflict('A merge is already running. Its progress will update automatically.')

    async def merge_into_project(self, ident, request, resolve_conflicts=False):
        """One explicit user action, with durable progress and no writes before verification."""
        from .integration import Integrations
        integrations = Integrations(self.git, self.checks)
        try:
            feature = self.get(ident)
            record = integrations.load(feature)
            if record and record['status'] in ('applied', 'applying', 'recovery_required'):
                return
            current = False
            if record and record['status'] != 'rolled_back':
                try:
                    await asyncio.to_thread(integrations.assert_current, feature, record)
                    current = True
                except ValueError: pass
            if not current:
                record = await self.integration(ident, request, 'prepare')
            if record['status'] == 'conflicts':
                if not resolve_conflicts: return
                await self.resolve_merge_conflicts(ident, request)
                record = integrations.load(self.get(ident))
                if record['status'] == 'conflicts': return
            # Validation reuses current successful checks, including an already
            # verified candidate, and still rechecks environment/source identity.
            record = await self.integration(ident, request, 'validate')
            if record['status'] == 'verified': await self.apply(ident, request)
        except (Exception, asyncio.CancelledError) as exc:
            self.integration_errors[ident] = str(exc) or 'Merge interrupted. Retry when you are ready.'
        finally:
            self.announce(ident)

    async def resolve_merge_conflicts(self, ident, request):
        from .integration import Integrations
        from .merge_resolution import MergeResolution, MergeResolutionInput
        integrations = Integrations(self.git, self.checks)
        project = self.workspace.agent_project_snapshot('development')
        settings = StageSettings.model_validate_json(project['files'][files_for('development')[2]])
        instructions = self.workspace.support_instruction_snapshot('merge-resolver')
        while True:
            async with self.features.lock(ident):
                feature = self.get(ident)
                self.store.expect_revision(feature, request['revision'])
                record = integrations.load(feature)
                if not record['conflicts']: return
                path = record['conflicts'][0]['path']
                evidence = await asyncio.to_thread(integrations.conflict, feature, record, path)
                if any(evidence[side]['content'] is None for side in ('base', 'local', 'feature')):
                    raise ValueError(f'{path}: the agent needs complete text; binary or oversized files cannot be resolved automatically.')
                previous_question = record.get('agent_resolution', {}).get('summary', '')
                guidance = record.setdefault('agent_guidance', [])
                if request.get('note', '').strip() and not any(item['id'] == request['id'] for item in guidance):
                    guidance.append({'id': request['id'], 'author': request['author'],
                                     'question': previous_question, 'answer': request['note'].strip()})
                record['agent_resolution'] = {'status': 'running', 'path': path}
                integrations.save(record)
                integration_id = record['id']
                spec = MergeResolutionInput(model=settings.model,
                    instructions=instructions['content'], instructions_version=instructions['version'], context={
                    'conflict': evidence,
                    'feature': {key: feature.get(key) for key in ('title', 'description', 'acceptance')},
                    'previous_resolutions': record.get('agent_resolutions', []),
                    'user_guidance': guidance,
                    'previous_question': previous_question,
                })
            self.announce(ident)
            try:
                with self.workspace.support_activity.track('merge-resolver'):
                    decision = MergeResolution.model_validate(await self.runtime.resolve_merge_conflict(spec))
                async with self.features.lock(ident):
                    feature = self.get(ident)
                    self.store.expect_revision(feature, request['revision'])
                    record = integrations.load(feature)
                    if record['id'] != integration_id: raise ValueError('Merge preview changed. Retry the merge.')
                    await asyncio.to_thread(integrations.assert_current, feature, record)
                    if decision.choice == 'blocked':
                        raise ValueError(f'{path}: {decision.summary}')
                    record = await asyncio.to_thread(integrations.resolve, feature, record, {
                        **request, 'id': uuid.uuid4().hex, 'integration_id': integration_id,
                        'path': path, 'choice': decision.choice, 'content': decision.content,
                    })
                    record.setdefault('agent_resolutions', []).append({'path': path, 'summary': decision.summary})
                    record['agent_resolution'] = {'status': 'complete', 'path': path}
                    integrations.save(record)
                    self.store.record(feature, request['author'], 'Agent resolved merge conflict: ' + path)
                    self.store.put(feature)
            except (Exception, asyncio.CancelledError) as exc:
                record = integrations.load(self.get(ident))
                if record and record['id'] == integration_id:
                    record['agent_resolution'] = {'status': 'blocked', 'path': path,
                                                  'summary': str(exc) or 'Agent interrupted. Retry resolution.'}
                    integrations.save(record)
                raise
            finally:
                self.announce(ident)

    async def integration_conflict(self, ident, path):
        from .integration import Integrations
        integrations = Integrations(self.git, self.checks)
        async with self.features.lock(ident):
            feature = self.get(ident); record = integrations.load(feature)
            if not record: raise ValueError('There is no merge to resolve')
            return await asyncio.to_thread(integrations.conflict, feature, record, path)
