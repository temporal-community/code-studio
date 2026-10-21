"""Feature state and mutation receipts, persisted alongside the shared workspace."""
from __future__ import annotations

import json
import time

from sdlc.models import digest
from .models import FeaturePlan, ImplementationResult

ACTIVE = {'queued', 'running'}


class FeatureConflict(ValueError):
    pass


class FeatureStore:
    def __init__(self, workspace):
        self.db = workspace.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS features (id TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feature_receipts (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feature_writes (id TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        self.db.commit()

    def get(self, ident: str) -> dict:
        row = self.db.execute('SELECT value FROM features WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Feature not found')
        return json.loads(row[0])

    def put(self, feature: dict):
        feature['updated_at'] = time.time()
        with self.db:
            self.db.execute('INSERT INTO features VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',
                            (feature['id'], json.dumps(feature)))

    def list(self) -> list[dict]:
        return [self.summary(json.loads(row[0])) for row in self.db.execute('SELECT value FROM features ORDER BY rowid DESC')]

    @staticmethod
    def summary(f: dict) -> dict:
        return {**{k: f[k] for k in ('id', 'title', 'branch', 'repository', 'status', 'author', 'input_revision', 'updated_at')},
                'contributors': sorted({f['author'], *[c['author'] for c in f['inputs']]}),
                'input_count': len(f['inputs']), 'plan_count': len(f['plans']),
                'agent_version': (f['jobs'][-1].get('agent_project') or {}).get('version') if f['jobs'] else None}

    def reserve(self, request: dict, git: dict) -> dict:
        existing = self.db.execute('SELECT value FROM features WHERE id=?', (request['id'],)).fetchone()
        if existing:
            result = json.loads(existing[0])
            if result['request_hash'] != digest(request):
                raise FeatureConflict('Feature ID was reused for a different request')
            return result
        feature = {**request, **git, 'request_hash': digest(request), 'input_revision': 1,
                   'status': 'creating', 'created_at': time.time(), 'updated_at': time.time(),
                   'inputs': [], 'plans': [], 'jobs': [], 'approval': None, 'implementation': None,
                   'error': None, 'activity': []}
        self.record(feature, request['author'], 'Created feature brief')
        self.put(feature)
        return feature

    @staticmethod
    def record(feature: dict, author: str, text: str):
        feature['activity'].append({'author': author, 'text': text, 'time': time.time()})
        feature['activity'] = feature['activity'][-100:]

    def receipt(self, key: str, request: dict):
        row = self.db.execute('SELECT fingerprint,value FROM feature_receipts WHERE id=?', (key,)).fetchone()
        if row:
            if row[0] != digest(request):
                raise FeatureConflict('Request ID was reused with different contents')
            return json.loads(row[1])
        return None

    def commit_receipt(self, feature: dict, key: str, request: dict, result: dict):
        try:
            self.db.execute('INSERT INTO feature_receipts VALUES (?, ?, ?)', (key, digest(request), json.dumps(result)))
            self.put(feature)
        except Exception:
            self.db.rollback()
            raise
        return result

    def edit(self, ident: str, request: dict) -> dict:
        key = ident + ':brief:' + request['id']
        if self.receipt(key, request) is not None:
            return self.get(ident)
        feature = self.get(ident)
        self.expect_revision(feature, request['revision'])
        for field in ('title', 'description', 'acceptance'):
            feature[field] = request[field].strip()
        if not feature['title'] or not feature['description']:
            raise ValueError('A title and feature description are required')
        self.invalidate(feature)
        self.record(feature, request['author'], 'Updated the feature brief')
        self.commit_receipt(feature, key, request, {'saved': True})
        return feature

    def contribute(self, ident: str, request: dict) -> dict:
        key = ident + ':input:' + request['id']
        if self.receipt(key, request) is not None:
            return self.get(ident)
        feature = self.get(ident)
        if not request['text'].strip():
            raise ValueError('Add some input before posting')
        if len(feature['inputs']) >= 100 or sum(len(c['text']) for c in feature['inputs']) + len(request['text']) > 80_000:
            raise ValueError('This feature has reached its input limit; create a follow-up feature')
        feature['inputs'].append({**request, 'time': time.time()})
        self.invalidate(feature)
        self.record(feature, request['author'], 'Added ' + request['kind'])
        self.commit_receipt(feature, key, request, {'saved': True})
        return feature

    @staticmethod
    def invalidate(feature: dict):
        feature['input_revision'] += 1
        feature['approval'] = None
        feature['status'] = 'draft'
        # A running planner may finish its old snapshot, clearly marked as stale.
        for job in feature['jobs']:
            if job['mode'] == 'code' and job['status'] in ACTIVE:
                job['status'] = 'superseded'
                job['error'] = 'Feature input changed. Existing file edits are retained; summarize and approve the updated scope before coding again.'

    @staticmethod
    def expect_revision(feature: dict, revision: int):
        if feature['input_revision'] != revision:
            raise FeatureConflict('The feature changed in another session. Review the latest input and try again.')

    def job(self, ident: str, job_id: str) -> tuple[dict, dict]:
        feature = self.get(ident)
        job = next((j for j in feature['jobs'] if j['id'] == job_id), None)
        if job is None:
            raise ValueError('Feature task not found')
        return feature, job

    def add_job(self, ident: str, request: dict, mode: str, context: dict, agent_project: dict | None = None) -> dict:
        feature = self.get(ident)
        old = next((j for j in feature['jobs'] if j['id'] == request['id']), None)
        if old:
            if old['request_hash'] != digest({**request, 'mode': mode}):
                raise FeatureConflict('Task ID was reused for a different request')
            return old
        self.expect_revision(feature, request['revision'])
        if any(j['status'] in ACTIVE for j in feature['jobs']):
            raise FeatureConflict('This feature already has an agent task running')
        if len(feature['jobs']) >= 30:
            raise ValueError('This feature has reached its 30-task limit; create a follow-up feature')
        plan = None
        if mode == 'code':
            if not feature['approval'] or feature['approval']['input_revision'] != feature['input_revision']:
                raise FeatureConflict('Approve the current feature plan before starting coding')
            plan = next(p for p in feature['plans'] if p['id'] == feature['approval']['plan_id'])
        else:
            feature['approval'] = None
        job = {'id': request['id'], 'mode': mode, 'status': 'queued', 'author': request['author'],
               'created_at': time.time(), 'input_revision': feature['input_revision'],
               'request_hash': digest({**request, 'mode': mode}), 'workflow_id': 'feature-' + ident + '-' + request['id'],
               'brief': {k: feature[k] for k in ('title', 'description', 'acceptance', 'inputs', 'branch', 'input_revision')},
               'context': context, 'agent_project': agent_project, 'approved_plan': plan, 'trace': [], 'error': None, 'usage': None}
        feature['jobs'].append(job)
        feature['status'] = 'planning' if mode == 'plan' else 'coding'
        feature['error'] = None
        self.record(feature, request['author'], 'Requested a feature summary' if mode == 'plan' else 'Started coding the approved plan')
        self.put(feature)
        return job

    def approve(self, ident: str, plan_id: str, request: dict) -> dict:
        key = ident + ':approve:' + request['id']
        if self.receipt(key, {**request, 'plan_id': plan_id}) is not None:
            return self.get(ident)
        feature = self.get(ident)
        self.expect_revision(feature, request['revision'])
        if not feature['plans'] or feature['plans'][-1]['id'] != plan_id:
            raise FeatureConflict('Only the latest feature plan can be approved')
        plan = feature['plans'][-1]
        if plan['input_revision'] != feature['input_revision']:
            raise FeatureConflict('This summary is outdated. Summarize the latest collaborator input first.')
        if any(j['status'] in ACTIVE for j in feature['jobs']):
            raise FeatureConflict('Wait for the agent task to finish before approving')
        plan_job = next(j for j in feature['jobs'] if j['id'] == plan_id)
        if plan_job['status'] != 'completed':
            raise FeatureConflict('The summary task must complete successfully before approval')
        if plan['plan']['open_questions']:
            raise FeatureConflict('Answer the open questions in collaborator input, then summarize again before approving')
        feature['approval'] = {'plan_id': plan_id, 'author': request['author'], 'time': time.time(),
                               'input_revision': feature['input_revision']}
        feature['status'] = 'approved'
        self.record(feature, request['author'], 'Approved plan ' + str(len(feature['plans'])))
        self.commit_receipt(feature, key, {**request, 'plan_id': plan_id}, {'approved': True})
        return feature

    def update_job(self, ident: str, job_id: str, **changes):
        feature, job = self.job(ident, job_id)
        job.update(changes)
        self.put(feature)

    def complete(self, ident: str, job_id: str):
        feature, job = self.job(ident, job_id)
        if job['status'] not in ACTIVE:
            return
        if job['mode'] == 'plan' and not any(p['id'] == job_id for p in feature['plans']):
            raise ValueError('The agent finished without saving a feature summary')
        if job['mode'] == 'code' and (not feature['implementation'] or feature['implementation']['job_id'] != job_id):
            raise ValueError('The agent finished without saving its implementation report')
        job['status'], job['finished_at'] = 'completed', time.time()
        if feature['input_revision'] == job['input_revision']:
            feature['status'] = 'needs_review' if job['mode'] == 'plan' else 'code_ready'
        self.record(feature, 'Feature agent', 'Finished the feature summary' if job['mode'] == 'plan' else 'Finished writing code for review')
        self.put(feature)

    def save_artifact(self, ident: str, job_id: str, call_id: str, name: str, args: dict) -> dict:
        key = ident + ':' + job_id + ':' + call_id
        payload = {'name': name, 'args': args}
        old = self.receipt(key, payload)
        if old is not None:
            return old
        feature, job = self.job(ident, job_id)
        if job['status'] not in ACTIVE:
            raise ValueError('This feature task is no longer active')
        if name == 'save_feature_plan' and job['mode'] == 'plan':
            plan = FeaturePlan.model_validate_json(args['plan_json']).model_dump()
            expected = {c['id'] for c in job['brief']['inputs']}
            received = [c['input_id'] for c in plan['input_coverage']]
            if set(received) != expected or len(received) != len(expected):
                raise ValueError('Account for every collaborator input exactly once in input_coverage')
            if any(p['id'] == job_id for p in feature['plans']):
                raise ValueError('This task already saved a plan')
            feature['plans'].append({'id': job_id, 'input_revision': job['input_revision'], 'plan': plan,
                                     'brief': job['brief'], 'agent_version': (job.get('agent_project') or {}).get('version'), 'created_at': time.time()})
            result = {'saved': True, 'outdated': feature['input_revision'] != job['input_revision']}
        elif name == 'save_implementation' and job['mode'] == 'code':
            self.require_coding(feature, job)
            result_data = ImplementationResult.model_validate_json(args['result_json']).model_dump()
            writes = [json.loads(row[0]) for row in self.db.execute('SELECT value FROM feature_writes')]
            actual = {w['path'] for w in writes if w['job_id'] == job_id and w['feature_id'] == ident and w['status'] == 'completed'}
            if set(result_data['changed_files']) != actual:
                raise ValueError('changed_files must name exactly the files successfully written in this task')
            feature['implementation'] = {'job_id': job_id, 'result': result_data, 'created_at': time.time(),
                                         'checks': {'status': 'not_run', 'message': 'Suggested checks have not been executed.'}}
            result = {'saved': True}
        else:
            raise ValueError('This tool is not available in the current feature phase')
        job['trace'].append({'name': name, 'detail': 'Saved shared artifact', 'time': time.time()})
        return self.commit_receipt(feature, key, payload, result)

    @staticmethod
    def require_coding(feature: dict, job: dict):
        approval = feature['approval']
        if job['mode'] != 'code' or job['status'] not in ACTIVE or not approval or approval['input_revision'] != feature['input_revision'] or job['input_revision'] != feature['input_revision'] or approval['plan_id'] != job['approved_plan']['id']:
            raise FeatureConflict('Coding requires approval of the current scope. New input needs a new summary and approval.')
