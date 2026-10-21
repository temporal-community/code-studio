"""Paired experiments freeze source, brief and agents; only the playbook differs."""
import asyncio
import copy
import json
import time

from pydantic import Field
from typing import Literal
from features.models import StrictModel
from features.storage import FeatureConflict
from sdlc.models import digest
from .definitions import STAGES
from .library import CONVENTIONS
from .design_system import reference_files


class PlaybookEdit(StrictModel):
    author: str = Field(min_length=1, max_length=40)
    expected_version: str
    content: str = Field(min_length=1, max_length=20000)


class ExperimentCreate(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,60}$')
    feature_id: str
    revision: int = Field(ge=1)
    author: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=160)
    hypothesis: str = Field(min_length=1, max_length=4000)
    variant_playbook: str = Field(min_length=1, max_length=20000)
    baseline_playbook_version: str


class ExperimentStart(StrictModel):
    author: str = Field(min_length=1, max_length=40)


class Assessment(StrictModel):
    author: str = Field(min_length=1, max_length=40)
    expected_version: str
    verdict: Literal['unassessed', 'met', 'partial', 'not_met']
    notes: str = Field(max_length=6000)


class ExperimentService:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.db = pipeline.store.db
        self.lock = asyncio.Lock()
        self.db.execute('CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.commit()

    def playbook(self):
        row = self.db.execute("SELECT value FROM state WHERE key='project_playbook'").fetchone()
        return json.loads(row[0]) if row else {'content': CONVENTIONS, 'version': digest(CONVENTIONS), 'author': 'Code Studio'}

    def edit_playbook(self, request):
        if not request['content'].strip():
            raise ValueError('The playbook cannot be empty')
        if self.playbook()['version'] != request['expected_version']:
            raise FeatureConflict('A teammate changed the playbook. Reload before saving.')
        saved = {'content': request['content'], 'version': digest(request['content']), 'author': request['author']}
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES (?, ?)', ('project_playbook', json.dumps(saved)))
        return saved

    def get(self, ident):
        row = self.db.execute('SELECT value FROM experiments WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Experiment not found')
        return json.loads(row[0])

    def put(self, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO experiments VALUES (?, ?)', (value['id'], json.dumps(value)))

    def list(self, feature_id):
        values = [json.loads(row[0]) for row in self.db.execute('SELECT value FROM experiments ORDER BY rowid DESC')]
        return [{'id': e['id'], 'title': e['title'], 'status': e['status']} for e in values
                if e['feature_id'] == feature_id or feature_id in e.get('attempts', {}).values()]

    async def create(self, request):
        async with self.lock:
            try:
                saved = self.get(request['id'])
            except ValueError:
                saved = None
            if saved:
                if saved['request_hash'] != digest(request):
                    raise FeatureConflict('Experiment ID was reused for different inputs')
            else:
                p = self.pipeline
                async with p.features.lock(request['feature_id']):
                    f = p.get(request['feature_id'])
                    p.store.expect_revision(f, request['revision'])
                    if f['pipeline']['status'] in ('running', 'paused'):
                        raise FeatureConflict('Wait for the current stage to finish before capturing an experiment')
                    if not request['title'].strip() or not request['hypothesis'].strip() or not request['variant_playbook'].strip():
                        raise ValueError('Add a title, hypothesis and variant playbook')
                    baseline = self.playbook()
                    if request['baseline_playbook_version'] != baseline['version']:
                        raise FeatureConflict('The baseline playbook changed. Reload it before creating the experiment.')
                    if baseline['content'] == request['variant_playbook']:
                        raise ValueError('Change the variant playbook to test a difference')
                    # Persist the manifest before copying: a crash must never recapture changed source.
                    source = await asyncio.to_thread(p.git.prepare, 'experiment-' + request['id'], f['worktree'],
                        'experiment/' + request['id'], 'HEAD', allow_snapshot=True, prefer_snapshot=True)
                    saved = {**request, 'request_hash': digest(request), 'status': 'preparing', 'created_at': time.time(),
                        'source': {**source, 'id': 'experiment-' + request['id']},
                        'brief': {k: f[k] for k in ('title', 'description', 'acceptance')},
                        'inputs': copy.deepcopy(f['inputs']),
                        'projects': {s: p.workspace.agent_project_snapshot(s) for s in STAGES},
                        'design_system': reference_files(),
                        'playbooks': {'baseline': baseline['content'], 'variant': request['variant_playbook']},
                        'attempts': {arm: 'exp-' + request['id'] + '-' + arm for arm in ('baseline', 'variant')},
                        'assessments': {}, 'start_requested': False}
                    self.put(saved)
            await self.prepare(saved)
        return await self.compare(saved['id'])

    async def prepare(self, experiment):
        p = self.pipeline
        await asyncio.to_thread(p.git.ensure_worktree, experiment['source'])
        for arm, ident in experiment['attempts'].items():
            config = {'projects': experiment['projects'], 'playbook': experiment['playbooks'][arm],
                      'design_system': experiment.get('design_system', {}),
                      'experiment_id': experiment['id'], 'arm': arm}
            request = {**experiment['brief'], 'id': ident, 'author': experiment['author'],
                'repository': experiment['source']['baseline'], 'source_mode': 'current', 'base': 'HEAD',
                'branch': 'experiment/' + experiment['id'] + '/' + arm}
            await p.create(request, auto_start=False, experiment_config=config, inputs=experiment['inputs'])
            f = p.get(ident)
            if f['status'] == 'branch_failed' or f['base_sha'] != experiment['source']['base_sha']:
                raise FeatureConflict('The experiment snapshot is unavailable or changed; attempts were not started')
        experiment['status'] = 'ready'
        self.put(experiment)

    async def start(self, ident, author):
        if not self.pipeline.enabled:
            raise ValueError('Start the workspace host and worker with a model key before running experiments')
        async with self.lock:
            experiment = self.get(ident)
            await self.prepare(experiment)
            for feature_id in experiment['attempts'].values():
                feature = self.pipeline.get(feature_id)
                if not feature['pipeline']['runs'] and not feature['pipeline'].get('execution'):
                    current = await asyncio.to_thread(self.pipeline.checks.fingerprint, feature)
                    if current != experiment['source']['base_sha']:
                        raise FeatureConflict('An unstarted attempt was edited. Restore its captured source or create a new experiment.')
            experiment['start_requested'] = True
            self.put(experiment)
            for feature_id in experiment['attempts'].values():
                feature = self.pipeline.get(feature_id)
                if not feature['pipeline']['runs'] and not feature['pipeline'].get('execution'):
                    await self.pipeline.handoff(feature_id, 'ideation', author)
        return await self.compare(ident)

    async def recover(self):
        for row in self.db.execute('SELECT value FROM experiments').fetchall():
            experiment = json.loads(row[0])
            if experiment['status'] == 'preparing':
                try:
                    await self.prepare(experiment)
                except (ValueError, OSError):
                    continue  # Keep the captured manifest; the UI can retry explicitly.
            if experiment['start_requested'] and self.pipeline.enabled:
                try:
                    await self.start(experiment['id'], experiment['author'])
                except (ValueError, OSError):
                    continue

    async def assess(self, ident, arm, request):
        if arm not in ('baseline', 'variant'):
            raise ValueError('Unknown experiment attempt')
        async with self.lock:
            experiment = self.get(ident)
            current = experiment['assessments'].get(arm, {})
            if digest(current) != request['expected_version']:
                raise FeatureConflict('A teammate updated this assessment. Reload before saving.')
            feature = self.pipeline.get(experiment['attempts'][arm])
            source_hash = await asyncio.to_thread(self.pipeline.checks.fingerprint, feature)
            experiment['assessments'][arm] = {**{k: request[k] for k in ('verdict', 'notes', 'author')},
                'source_hash': source_hash, 'input_revision': feature['input_revision'],
                'evidence_hash': self.evidence_hash(feature, source_hash),
                'run_ids': [r['id'] for r in feature['pipeline']['runs']], 'time': time.time()}
            self.put(experiment)
        return await self.compare(ident)

    @staticmethod
    def evidence_hash(feature, source_hash):
        return digest({'source': source_hash, 'scope': feature['input_revision'],
            'runs': [{key: run.get(key) for key in ('id', 'status', 'result')} for run in feature['pipeline']['runs']],
            'checks': feature['pipeline']['checks']})

    async def compare(self, ident):
        experiment = self.get(ident)
        result = {k: experiment[k] for k in ('id', 'title', 'hypothesis', 'feature_id', 'brief', 'playbooks', 'created_at', 'status', 'start_requested')}
        result['source_hash'] = experiment['source']['base_sha']
        result['agent_versions'] = {stage: project['version'] for stage, project in experiment['projects'].items()}
        result['attempts'] = {}
        for arm, feature_id in experiment['attempts'].items():
            try:
                f = self.pipeline.get(feature_id)
            except ValueError:
                result['attempts'][arm] = {'id': feature_id, 'status': 'preparing'}
                continue
            p = f['pipeline']; runs = p['runs']
            measured = [r for r in runs if r.get('usage') is not None]
            latest = {r['stage']: r for r in runs if r.get('result')}
            fingerprint = await asyncio.to_thread(self.pipeline.checks.fingerprint, f)
            diff = await asyncio.to_thread(self.pipeline.git.diff, f)
            assessment = experiment['assessments'].get(arm, {})
            result['attempts'][arm] = {'id': feature_id, 'status': p['status'], 'stage': p['current_stage'],
                'scope_changed': f['input_revision'] != 1, 'source_hash': f['base_sha'],
                'playbook_version': digest(experiment['playbooks'][arm]),
                'tokens': sum((r['usage'] or {}).get('total_tokens', 0) for r in measured) if measured else None,
                'measured_runs': len(measured), 'total_runs': len(runs),
                'repair_rounds': p['repairs'],
                'checks': {key: {**value, 'current': value['source_hash'] == fingerprint} for key, value in p['checks'].items()},
                'results': {stage: run['result'] for stage, run in latest.items()},
                'review_findings': latest.get('review', {}).get('result', {}).get('findings', []),
                'review_recorded': 'review' in latest,
                'diff': diff, 'assessment': assessment, 'assessment_version': digest(assessment)}
            result['attempts'][arm]['assessment_stale'] = bool(assessment) and (
                assessment.get('evidence_hash') != self.evidence_hash(f, fingerprint))
        return result
