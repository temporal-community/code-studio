"""Durable workspace state, callback receipts, and a local app.py projection."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .document import Document
from .support_activity import SupportActivity
from .support_instructions import SupportInstructions
from sdlc.models import digest
from sdlc.templates import PROJECT_FILES
from features.project import PROJECT_FILES as FEATURE_FILES
from cooperative.definitions import (
    PROJECT_FILES as STAGE_FILES, DEFINITIONS, LEGACY_PROMPT_HASHES,
    PREVIOUS_UI_PROMPT_HASHES, files_for,
)

SEED = '''"""A Python project shared by people and agents."""


def greet(name: str) -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("world"))
'''


class WorkspaceStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'app.py'
        self.db = sqlite3.connect(directory / 'workspace.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_files (path TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        row = self.db.execute("SELECT value FROM state WHERE key='document'").fetchone()
        if row:
            state = json.loads(row[0])
            self.doc = Document(state['nodes'], state['clock'])
            self.revision = state['revision']
            self.session_id = state['session_id']
        else:
            self.doc = Document()
            self.revision = 0
            self.session_id = 'shared-python-' + uuid.uuid4().hex
            self.doc.apply(self.doc.replace(0, 0, self.path.read_text() if self.path.exists() else SEED, 'seed'))
        self.documents = {'app.py': self.doc}
        for name, seed in {**PROJECT_FILES, **FEATURE_FILES, **STAGE_FILES}.items():
            saved = self.db.execute('SELECT value FROM project_files WHERE path=?', (name,)).fetchone()
            if saved:
                data = json.loads(saved[0])
                self.documents[name] = Document(data['nodes'], data['clock'])
            else:
                document = Document()
                local = directory / name
                document.apply(document.replace(0, 0, local.read_text() if local.exists() else seed, 'seed'))
                self.documents[name] = document
        saved = self.db.execute("SELECT value FROM state WHERE key='builder_session'").fetchone()
        self.builder_session_id = json.loads(saved[0]) if saved else 'sdlc-builder-' + uuid.uuid4().hex
        self.db.execute('INSERT OR REPLACE INTO state VALUES (?, ?)', ('builder_session', json.dumps(self.builder_session_id)))
        saved = self.db.execute("SELECT value FROM state WHERE key='feature_builder_session'").fetchone()
        self.feature_builder_session_id = json.loads(saved[0]) if saved else 'feature-designer-' + uuid.uuid4().hex
        self.db.execute('INSERT OR REPLACE INTO state VALUES (?, ?)', ('feature_builder_session', json.dumps(self.feature_builder_session_id)))
        self.studio_session_ids = {}
        for agent_id in DEFINITIONS:
            key = 'studio_session:' + agent_id
            saved = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
            self.studio_session_ids[agent_id] = json.loads(saved[0]) if saved else 'studio-' + agent_id + '-' + uuid.uuid4().hex
            self.db.execute('INSERT OR REPLACE INTO state VALUES (?, ?)', (key, json.dumps(self.studio_session_ids[agent_id])))
        self._upgrade_planner_defaults()
        self._upgrade_stage_prompts()
        self._save_document()
        self._mirror()
        self.support_activity = SupportActivity(self.db)
        self.support_instructions = SupportInstructions(self.db)

    def support_instruction_snapshot(self, agent_id, target='default'):
        from sdlc.support_catalog import support_details
        prompts = support_details(agent_id, self)['prompts']
        return next(prompt for prompt in prompts if prompt['id'] == target)

    def _upgrade_planner_defaults(self):
        """Upgrade only the untouched starter settings, retaining CRDT anchors and custom edits."""
        name = 'planner_settings.json'
        doc = self.documents[name]
        old = json.loads(STAGE_FILES[name])
        old['max_turns'] = 12
        previous = {key: value for key, value in old.items() if key != 'tool_mode'}
        if doc.text not in (json.dumps(old, indent=2) + '\n', json.dumps(previous, indent=2) + '\n'):
            return
        if any(node['deleted'] or node['id'].split(':')[1] != 'seed' for node in doc.nodes.values()):
            return
        start = doc.text.index('"max_turns": 12') + len('"max_turns": ')
        doc.apply(doc.replace(start, start + 2, str(json.loads(STAGE_FILES[name])['max_turns']), 'planner-budget-upgrade'))
        self.revision += 1
        self.record('migration', 'Code Studio', 'Updated default Planner budget to 24 turns; custom settings are preserved')

    def _upgrade_stage_prompts(self):
        """Upgrade known defaults, including prior automatic upgrades, without replacing user edits."""
        for stage, expected_hash in LEGACY_PROMPT_HASHES.items():
            name = files_for(stage)[1]
            doc = self.documents[name]
            if hashlib.sha256(doc.text.encode()).hexdigest() not in (
                expected_hash, PREVIOUS_UI_PROMPT_HASHES.get(stage),
            ):
                continue
            if any(node['id'].split(':')[1] not in ('seed', 'stage-prompts-upgrade')
                   for node in doc.nodes.values()):
                continue
            doc.apply(doc.replace(0, len(doc.text), STAGE_FILES[name], 'stage-prompts-upgrade'))
            self.revision += 1
            self.record('migration', 'Code Studio', f'Updated default {stage} playbook; custom instructions are preserved')

    def close(self):
        self.db.close()

    def snapshot(self) -> dict:
        return {'nodes': list(self.doc.nodes.values()), 'clock': self.doc.clock,
                'revision': self.revision, 'text': self.doc.text, 'session_id': self.session_id,
                'file': 'app.py', 'path': str(self.path), 'builder_session_id': self.builder_session_id,
                'feature_builder_session_id': self.feature_builder_session_id,
                'feature_agent_version': self.feature_project_snapshot()['version'],
                'studio_session_ids': self.studio_session_ids,
                'agent_projects': {key: {'name': value['name'], 'files': files_for(key)} for key, value in DEFINITIONS.items()},
                'files': {name: {'nodes': list(doc.nodes.values()), 'clock': doc.clock,
                                 'text': doc.text} for name, doc in self.documents.items()}}

    def file(self, path: str) -> Document:
        if path not in self.documents:
            raise ValueError(f'Unknown project file: {path}')
        return self.documents[path]

    def project_snapshot(self) -> dict:
        files = {name: doc.text for name, doc in self.documents.items() if name in PROJECT_FILES}
        return {'revision': self.revision, 'version': digest(files), 'files': files}

    def agent_project_snapshot(self, agent_id):
        if agent_id not in DEFINITIONS:
            raise ValueError('Unknown agent')
        files = {name: self.file(name).text for name in files_for(agent_id)}
        return {'revision': self.revision, 'version': digest(files), 'files': files, 'agent_id': agent_id}

    def feature_project_snapshot(self) -> dict:
        files = {name: self.file(name).text for name in FEATURE_FILES}
        return {'revision': self.revision, 'version': digest(files), 'files': files}

    def events(self) -> list[dict]:
        rows = self.db.execute('SELECT value FROM events ORDER BY id DESC LIMIT 60').fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def record(self, kind: str, author: str, detail: str) -> dict:
        event = {'kind': kind, 'author': author, 'detail': detail,
                 'revision': self.revision, 'time': time.time()}
        self.db.execute('INSERT INTO events(value) VALUES (?)', (json.dumps(event),))
        return event

    def _save_document(self):
        state = {'nodes': self.doc.nodes, 'clock': self.doc.clock,
                 'revision': self.revision, 'session_id': self.session_id}
        self.db.execute('INSERT OR REPLACE INTO state VALUES (?, ?)', ('document', json.dumps(state)))
        for name, doc in self.documents.items():
            if name != 'app.py':
                self.db.execute('INSERT OR REPLACE INTO project_files VALUES (?, ?)',
                                (name, json.dumps({'nodes': doc.nodes, 'clock': doc.clock})))
        self.db.commit()

    def _mirror(self):
        for name, doc in self.documents.items():
            path = self.directory / name
            temporary = path.with_name(path.name + '.tmp')
            temporary.write_text(doc.text, encoding='utf-8')
            os.replace(temporary, path)

    def once(self, operation_id: str, payload: dict, action) -> dict:
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        row = self.db.execute('SELECT digest, result FROM receipts WHERE id=?', (operation_id,)).fetchone()
        if row:
            if row[0] != digest:
                raise ValueError('Operation ID was already used for a different request')
            self._mirror()
            return json.loads(row[1])
        before = {name: Document({key: dict(node) for key, node in doc.nodes.items()}, doc.clock)
                  for name, doc in self.documents.items()}
        revision = self.revision
        try:
            result = action()
            self.db.execute('INSERT INTO receipts VALUES (?, ?, ?)', (operation_id, digest, json.dumps(result)))
            self._save_document()  # document, journal, and receipt commit together
        except Exception:
            self.db.rollback()
            self.documents, self.revision = before, revision
            self.doc = self.documents['app.py']
            raise
        self._mirror()
        return result

    def edit(self, operation_id: str, operation: dict, author: str, file: str = 'app.py') -> dict:
        def apply():
            self.file(file).apply(operation)
            self.revision += 1
            event = self.record('edit', author, f'Edited {file}')
            return {'operation': operation, 'revision': self.revision, 'event': event, 'file': file}
        # Preserve receipt identities created by the original single-file editor.
        payload = {'operation': operation, 'author': author}
        if file != 'app.py':
            payload['file'] = file
        return self.once(operation_id, payload, apply)

    def callback(self, tool_id: str, name: str, arguments: dict) -> dict:
        def execute():
            if name == 'project_list_files':
                return {'files': list(self.documents), 'revision': self.revision}
            path = arguments.get('path', 'app.py')
            document = self.file(path)
            if name in ('workspace_read_file', 'project_read_file'):
                return {'path': path, 'revision': self.revision, 'content': document.text}
            if name not in ('workspace_edit_file', 'project_edit_file'):
                raise ValueError(f'Unsupported workspace callback: {name}')
            expected = arguments.get('base_revision')
            if expected != self.revision:
                self.record('conflict', 'Python agent', 'File changed while the agent was thinking; reread requested')
                return {'applied': False, 'revision': self.revision,
                        'message': 'Revision conflict. Read the current file and preserve human edits before retrying.'}
            old, new = arguments.get('old_text'), arguments.get('new_text')
            if not isinstance(old, str) or not isinstance(new, str):
                raise ValueError('old_text and new_text must be strings')
            text = document.text
            if old == '' and text == '':
                start = 0
            elif old and text.count(old) == 1:
                start = text.index(old)
            else:
                return {'applied': False, 'revision': self.revision,
                        'message': 'old_text must match exactly once. Read the file and retry.'}
            operation = document.replace(start, start + len(old), new, uuid.uuid4().hex)
            document.apply(operation)
            self.revision += 1
            self.record('agent-edit', 'Builder agent' if name.startswith('project_') else 'Python agent',
                        arguments.get('summary') or f'Updated {path} through a callback')
            return {'applied': True, 'revision': self.revision, 'message': f'Saved {path}', 'operation': operation, 'file': path}
        return self.once('callback:' + tool_id, {'name': name, 'arguments': arguments}, execute)
