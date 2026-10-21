"""Version-checked support instructions, persisted in the workspace database."""
import json
import time
import uuid

from pydantic import Field
from features.models import StrictModel
from sdlc.models import digest


class InstructionEdit(StrictModel):
    target: str = Field(default='default', min_length=1, max_length=40)
    version: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=20000)
    author: str = Field(min_length=1, max_length=40)


class InstructionConflict(ValueError):
    pass


class SupportInstructions:
    def __init__(self, db):
        self.db = db

    def snapshot(self, agent_id, target, default):
        key = f'support_instructions:{agent_id}:{target}'
        row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        saved = json.loads(row[0]) if row else {
            'content': default, 'version': digest(default), 'updated_at': None, 'author': None,
        }
        return {**saved, 'default_content': default}

    def save(self, agent_id, edit, default):
        if not edit.content.strip() or not edit.author.strip():
            raise ValueError('Instructions and author cannot be blank.')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.snapshot(agent_id, edit.target, default)
            if current['version'] != edit.version:
                raise InstructionConflict('These instructions changed in another session. Load the latest saved version before saving again. Your draft has been kept.')
            saved = {'content': edit.content, 'version': uuid.uuid4().hex,
                     'updated_at': time.time(), 'author': edit.author.strip()}
            self.db.execute('INSERT OR REPLACE INTO state VALUES (?, ?)',
                            (f'support_instructions:{agent_id}:{edit.target}', json.dumps(saved)))
        return {**saved, 'default_content': default}
