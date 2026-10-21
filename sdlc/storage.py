"""Persistent immutable run inputs, captured evidence, local reports, and shared comments."""
from __future__ import annotations

import json
import time

from .models import DraftReview, EvaluationSettings, digest, evaluate, validate_project
from .templates import demo_fixture

TERMINAL = {'completed', 'failed', 'cancelled'}


def evidence_tool(evidence: dict, name: str, arguments: dict) -> dict | list:
    """Read captured evidence with bounded model context; full evidence stays in the run."""
    if name == 'get_pull_request':
        data = evidence['pr']
    elif name == 'get_diff':
        data = {'files': [{**f, 'content': f.get('content', '')[:2000] if f.get('content') is not None else None,
                           'content_truncated': f.get('content_truncated', False) or len(f.get('content') or '') > 2000}
                          for f in evidence['files']], 'warnings': evidence['warnings']}
    elif name == 'get_checks':
        data = {'checks': [{k: v for k, v in c.items() if k != 'summary'} for c in evidence['checks'][:100]], 'total': len(evidence['checks']), 'truncated': len(evidence['checks']) > 100}
    elif name == 'get_test_results':
        cases = evidence['tests']['cases']
        selected = sorted(cases, key=lambda c: c['status'] != 'failed')[:40]
        data = {'availability': evidence['tests']['availability'], 'counts': {status: sum(c['status'] == status for c in cases) for status in ('passed', 'failed', 'skipped')}, 'cases': [{**c, 'message': c.get('message', '')[:1000]} for c in selected], 'truncated': len(cases) > len(selected)}
    elif name == 'get_failure_details':
        data = {'failures': [{**f, 'log': f.get('log', '')[-3000:]} for f in evidence['failures'][:8]], 'truncated': len(evidence['failures']) > 8}
    elif name == 'read_file':
        path = arguments.get('path')
        file = next((f for f in evidence['files'] if f['path'] == path), None)
        if not file or file.get('content') is None:
            raise ValueError('File contents were not captured. Use a path returned by get_diff with available content.')
        data = {'path': path, 'content': file['content'], 'truncated': file.get('content_truncated', False), 'sha': evidence['pr']['head_sha']}
    elif name == 'search_repository':
        query = str(arguments.get('query', ''))
        if not query or len(query) > 200:
            raise ValueError('Search query must contain 1–200 characters')
        data = {'scope': 'Captured changed-file contents only', 'matches': [
            {'path': f['path'], 'line': i, 'text': line[:500]}
            for f in evidence['files'] for i, line in enumerate((f.get('content') or '').splitlines(), 1)
            if query.casefold() in line.casefold()][:50]}
    else:
        raise ValueError('Choose a read-only evidence tool to preview')
    return data


class RunStore:
    def __init__(self, workspace):
        self.workspace = workspace
        self.db = workspace.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS review_runs (id TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS review_fixtures (id TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS review_callbacks (id TEXT PRIMARY KEY, digest TEXT NOT NULL, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS review_comments (id TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        fixture = demo_fixture()
        self.db.execute('INSERT OR IGNORE INTO review_fixtures VALUES (?, ?)', (fixture['id'], json.dumps(fixture)))
        self.db.commit()

    def get(self, ident: str) -> dict:
        row = self.db.execute('SELECT value FROM review_runs WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Run not found')
        return json.loads(row[0])

    def put(self, run: dict):
        self.db.execute('INSERT OR REPLACE INTO review_runs VALUES (?, ?)', (run['id'], json.dumps(run)))
        self.db.commit()

    def list(self) -> list[dict]:
        rows = self.db.execute('SELECT value FROM review_runs ORDER BY rowid DESC LIMIT 100').fetchall()
        return [self.summary(json.loads(row[0])) for row in rows]

    @staticmethod
    def summary(run: dict) -> dict:
        return {key: run.get(key) for key in ('id', 'status', 'created_at', 'finished_at', 'author', 'source',
                'version', 'revision', 'title', 'error', 'stage', 'evidence_hash', 'evaluation', 'usage', 'duration')}

    def create(self, ident: str, source: dict, author: str, project: dict) -> dict:
        request_hash = digest({'source': source, 'author': author, 'version': project['version']})
        try:
            old = self.get(ident)
        except ValueError:
            old = None
        if old:
            if old['request_hash'] != request_hash:
                raise ValueError('Run ID was reused for a different request')
            return old
        settings, evaluations = validate_project(project['files'])
        run = {'id': ident, 'status': 'queued', 'stage': 'Queued', 'created_at': time.time(),
               'author': author, 'source': source, 'project': project,
               'version': project['version'], 'revision': project['revision'],
               'settings': settings.model_dump(), 'evaluation_settings': evaluations.model_dump(),
               'request_hash': request_hash, 'workflow_id': 'pr-review-' + ident,
               'title': source.get('url') or source.get('fixture'), 'trace': [],
               'report': None, 'evidence': None, 'error': None, 'usage': None}
        self.put(run)
        return run

    def change(self, ident: str, **changes) -> dict:
        run = self.get(ident)
        run.update(changes)
        if run['status'] in TERMINAL and not run.get('finished_at'):
            run['finished_at'] = time.time()
            run['duration'] = run['finished_at'] - run['created_at']
        self.put(run)
        return run

    def fixtures(self) -> list[dict]:
        rows = self.db.execute('SELECT value FROM review_fixtures ORDER BY rowid').fetchall()
        return [{key: value for key, value in json.loads(row[0]).items() if key != 'evidence'} for row in rows]

    def fixture(self, ident: str) -> dict:
        row = self.db.execute('SELECT value FROM review_fixtures WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Fixture not found')
        return json.loads(row[0])

    def save_fixture(self, run_id: str, name: str) -> dict:
        run = self.get(run_id)
        if not run.get('evidence'):
            raise ValueError('Wait until this run has captured PR evidence')
        # Stable identity makes repeated saves of the same evidence idempotent.
        ident = 'saved-' + run['evidence_hash'][:20]
        fixture = {'id': ident, 'name': name[:100], 'description': f'Captured from run {run_id}',
                   'synthetic': run['source']['kind'] == 'fixture' and self.fixture(run['source']['fixture']).get('synthetic', False),
                   'evidence': run['evidence'], 'saved_from': run_id, 'created_at': time.time()}
        self.db.execute('INSERT OR IGNORE INTO review_fixtures VALUES (?, ?)', (ident, json.dumps(fixture)))
        self.db.commit()
        return self.fixture(ident)

    def trace(self, ident: str, name: str, inputs: dict, output: str, *, error: bool = False, event_id: str | None = None):
        run = self.get(ident)
        if event_id and any(entry.get('event_id') == event_id for entry in run['trace']):
            return
        run['trace'].append({'name': name, 'input': inputs, 'output': output[:24_000],
                             'truncated': len(output) > 24_000, 'error': error, 'time': time.time(), 'event_id': event_id})
        self.put(run)

    def callback(self, run_id: str, call_id: str, name: str, arguments: dict) -> str:
        key = run_id + ':' + call_id
        fingerprint = digest({'name': name, 'arguments': arguments})
        row = self.db.execute('SELECT digest,value FROM review_callbacks WHERE id=?', (key,)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise ValueError('Callback ID reused with a different request')
            return json.loads(row[1])
        run = self.get(run_id)
        if run['status'] in TERMINAL:
            raise ValueError('This run has already finished')
        if name not in run['settings']['tools']:
            raise ValueError(f'Tool {name} is not enabled for this agent version')
        if len(run['trace']) >= 80:
            raise ValueError('Run exceeded the 80-tool-call limit')
        evidence = run['evidence']
        if not evidence:
            raise ValueError('PR evidence has not been captured')
        if name == 'save_review':
            draft = DraftReview.model_validate_json(arguments['review_json']).model_dump()
            files = {f['path']: f for f in evidence['files']}
            for finding in draft['findings']:
                file = files.get(finding['path'])
                if not file:
                    raise ValueError(f'Finding refers to a file outside the captured diff: {finding["path"]}')
                if file.get('content') is not None and finding['line'] > max(1, len(file['content'].splitlines())):
                    raise ValueError(f'Finding line is outside the captured file: {finding["path"]}:{finding["line"]}')
            run['report'] = {**draft, 'pr': evidence['pr'], 'checks': evidence['checks'], 'tests': evidence['tests'],
                             'warnings': evidence['warnings'], 'evidence_hash': run['evidence_hash']}
            run['evaluation'] = evaluate(draft, EvaluationSettings.model_validate(run['evaluation_settings']), run['source'].get('fixture'))
            data = {'saved': True, 'findings': len(draft['findings'])}
        else:
            data = evidence_tool(evidence, name, arguments)
        output = json.dumps(data, ensure_ascii=False)
        run['trace'].append({'name': name, 'input': arguments, 'output': output[:24_000],
                             'truncated': len(output) > 24_000, 'error': False, 'time': time.time()})
        # Report, trace, and receipt share one transaction, before Temporal is acknowledged.
        try:
            self.db.execute('INSERT INTO review_callbacks VALUES (?, ?, ?)', (key, fingerprint, json.dumps(output)))
            self.put(run)
        except Exception:
            self.db.rollback()
            raise
        return output

    def comments(self, run_id: str) -> list[dict]:
        return [c for row in self.db.execute('SELECT value FROM review_comments ORDER BY rowid')
                if (c := json.loads(row[0]))['run_id'] == run_id]

    def add_comment(self, ident: str, run_id: str, author: str, text: str, finding: int | None):
        run = self.get(run_id)
        if finding is not None and (not run['report'] or not 0 <= finding < len(run['report']['findings'])):
            raise ValueError('Finding does not exist')
        comment = {'id': ident, 'run_id': run_id, 'author': author, 'text': text,
                   'finding': finding, 'created_at': time.time()}
        self.db.execute('INSERT OR IGNORE INTO review_comments VALUES (?, ?)', (ident, json.dumps(comment)))
        self.db.commit()
        return self.comments(run_id)

    def compare(self, first: str, second: str) -> dict:
        a, b = self.get(first), self.get(second)
        if not a.get('report') or not b.get('report'):
            raise ValueError('Choose two runs with saved review reports')
        def key(f):
            return f"{f['path']}:{f['line']}:{f['title'].casefold()}"
        af = {key(f): f for f in a['report']['findings']}
        bf = {key(f): f for f in b['report']['findings']}
        return {'first': self.summary(a), 'second': self.summary(b),
                'same_evidence': a.get('evidence_hash') == b.get('evidence_hash'),
                'same_agent': a['version'] == b['version'],
                'changed_files': [p for p in a['project']['files'] if a['project']['files'].get(p) != b['project']['files'].get(p)],
                'added': [bf[k] for k in sorted(bf.keys() - af.keys())], 'removed': [af[k] for k in sorted(af.keys() - bf.keys())],
                'retained': [bf[k] for k in sorted(af.keys() & bf.keys())]}
