"""Small replicated text sequence. Deletes address characters, never whole snapshots.

A character has a unique, ordered ID and an immutable predecessor. Concurrent
insertions after the same predecessor are ordered by ID, newest first. Deleted
characters stay as anchors, so later/concurrent edits cannot lose their position.
The browser implements the same ordering in ui/shared/document.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

MAX_TEXT = 20_000
MAX_NODES = 200_000


@dataclass
class Document:
    nodes: dict[str, dict] = field(default_factory=dict)
    clock: int = 0

    def visible(self) -> list[dict]:
        children: dict[str, list[str]] = {}
        for node in self.nodes.values():
            children.setdefault(node['after'], []).append(node['id'])
        for siblings in children.values():
            siblings.sort()
        stack = children.get('', []).copy()
        result = []
        while stack:
            node = self.nodes[stack.pop()]
            if not node['deleted']:
                result.append(node)
            stack.extend(children.get(node['id'], []))
        return result

    @property
    def text(self) -> str:
        return ''.join(node['value'] for node in self.visible())

    def apply(self, operation: dict) -> None:
        """Validate a complete operation before mutating; duplicate characters are inert."""
        inserts, deletes = operation.get('inserts', []), operation.get('deletes', [])
        if not isinstance(inserts, list) or not isinstance(deletes, list):
            raise ValueError('Invalid text operation')
        if len(inserts) + len(deletes) > MAX_TEXT * 2:
            raise ValueError('Edit is too large')
        known = set(self.nodes)
        added = {}
        clock = self.clock
        for node in inserts:
            if not isinstance(node, dict) or set(node) != {'id', 'after', 'value'}:
                raise ValueError('Invalid character')
            ident, after, value = node['id'], node['after'], node['value']
            if not all(isinstance(x, str) for x in (ident, after, value)):
                raise ValueError('Invalid character fields')
            parts = ident.split(':')
            if not re.fullmatch(r'[0-9]{16}:[A-Za-z0-9_-]+:[0-9]{6}', ident) or len(ident) > 120:
                raise ValueError('Invalid character ID')
            if int(parts[0]) >= 2**53 - 1:
                raise ValueError('Character clock exceeds browser integer range')
            if len(value) != 1 or '\ud800' <= value <= '\udfff':
                raise ValueError('Invalid Unicode character')
            if after and after not in known:
                raise ValueError('Missing edit anchor; reconnect to synchronize')
            if after and ident <= after:
                raise ValueError('Character IDs must advance their anchors')
            if ident in self.nodes:
                existing = self.nodes[ident]
                if any(existing[k] != node[k] for k in ('after', 'value')):
                    raise ValueError('Character ID reused with different content')
            elif ident in added:
                raise ValueError('Duplicate character ID in operation')
            else:
                added[ident] = {**node, 'deleted': False}
                known.add(ident)
            clock = max(clock, int(parts[0]))
        if any(not isinstance(ident, str) or ident not in known for ident in deletes):
            raise ValueError('Unknown deleted character')
        if len(self.nodes) + len(added) > MAX_NODES:
            raise ValueError('This demo workspace has reached its edit-history limit')
        current_count = len(self.visible())
        newly_deleted = sum(not (self.nodes.get(i) or added[i])['deleted'] for i in set(deletes))
        if current_count + len(added) - newly_deleted > MAX_TEXT:
            raise ValueError('app.py is limited to 20,000 characters in this milestone')
        self.nodes.update(added)
        for ident in deletes:
            self.nodes[ident]['deleted'] = True
        self.clock = clock

    def replace(self, start: int, end: int, text: str, operation_id: str) -> dict:
        visible = self.visible()
        after = visible[start - 1]['id'] if start else ''
        clock = self.clock + 1
        inserts = []
        for index, value in enumerate(text):
            ident = f'{clock:016d}:{operation_id}:{index:06d}'
            inserts.append({'id': ident, 'after': after, 'value': value})
            after = ident
        return {'inserts': inserts, 'deletes': [n['id'] for n in visible[start:end]]}
