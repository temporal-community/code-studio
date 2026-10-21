"""The multiplayer milestone, exercised without network sockets, Temporal, or model calls."""

import asyncio
from copy import deepcopy
import itertools
import json
from pathlib import Path
import random
import sqlite3
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from workspace.document import Document
from workspace.store import WorkspaceStore
from workspace.server import create_app


def document(text='hello world'):
    doc = Document()
    doc.apply(doc.replace(0, 0, text, 'seed'))
    return doc


def test_two_people_insert_at_same_position_without_losing_characters():
    base = document('Hello!')
    alice = base.replace(5, 5, ' Alice', 'alice')
    bob = base.replace(5, 5, ' Bob', 'bob')
    results = []
    for order in ((alice, bob), (bob, alice)):
        doc = deepcopy(base)
        for operation in order:
            doc.apply(operation)
        results.append(doc.text)
    assert results == ['Hello Bob Alice!', 'Hello Bob Alice!']


def test_delete_does_not_remove_a_concurrently_inserted_character():
    base = document('abc')
    deletion = base.replace(1, 2, '', 'alice')
    insertion = base.replace(2, 2, 'X', 'bob')
    for order in ((deletion, insertion), (insertion, deletion)):
        doc = deepcopy(base)
        for operation in order:
            doc.apply(operation)
        assert doc.text == 'aXc'


def test_concurrent_edits_converge_in_every_delivery_order():
    rng = random.Random(42)
    for trial in range(50):
        base = document('a😀bc\ndef\n')
        operations = []
        for client in ('alice', 'bob', 'agent'):
            start = rng.randrange(len(base.text) + 1)
            end = rng.randrange(start, len(base.text) + 1)
            operations.append(base.replace(start, end, client[:1] + '🧪', f'{client}-{trial}'))
        results = []
        for order in itertools.permutations(operations):
            doc = deepcopy(base)
            for operation in order:
                doc.apply(operation)
                doc.apply(operation)  # redelivery must not duplicate inserted text
            results.append(doc.text)
        assert len(set(results)) == 1


def test_invalid_operation_is_atomic():
    doc = document()
    operation = doc.replace(0, 0, 'new', 'alice')
    operation['deletes'] = ['unknown-character']
    before = deepcopy(doc)
    with pytest.raises(ValueError):
        doc.apply(operation)
    assert doc == before


@pytest.fixture
def store(tmp_path):
    store = WorkspaceStore(tmp_path)
    yield store
    store.close()


def test_human_edit_persists_to_real_file_and_receipt_survives_restart(tmp_path):
    store = WorkspaceStore(tmp_path)
    operation = store.doc.replace(0, 0, '# Alice was here\n', 'alice')
    result = store.edit('op-1', operation, 'Alice')
    session = store.session_id
    store.close()
    store = WorkspaceStore(tmp_path)
    assert store.path.read_text().startswith('# Alice was here\n')
    assert store.edit('op-1', operation, 'Alice') == result
    assert store.revision == 1
    assert store.session_id == session
    assert len(store.events()) == 1
    store.close()


def test_idempotency_key_cannot_be_reused_for_another_edit(store):
    operation = store.doc.replace(0, 0, '# hi\n', 'alice')
    store.edit('op-1', operation, 'Alice')
    with pytest.raises(ValueError, match='different request'):
        store.edit('op-1', operation, 'Bob')


def test_failed_commit_rolls_back_memory_journal_and_allows_retry(store, monkeypatch):
    before = store.snapshot()
    operation = store.doc.replace(0, 0, '# Must commit together\n', 'alice')
    save = store._save_document
    def fail():
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(store, '_save_document', fail)
    with pytest.raises(sqlite3.OperationalError):
        store.edit('op-fail', operation, 'Alice')
    assert store.snapshot() == before
    assert store.events() == []
    assert store.path.read_text() == before['text']
    monkeypatch.setattr(store, '_save_document', save)
    assert store.edit('op-fail', operation, 'Alice')['revision'] == 1
    assert len(store.events()) == 1


def test_stale_agent_patch_preserves_human_edit_then_reread_succeeds(store):
    original = store.callback('read-1', 'workspace_read_file', {})
    human = store.doc.replace(0, 0, '# Keep this human comment\n', 'alice')
    store.edit('human-1', human, 'Alice')
    args = {'base_revision': original['revision'], 'old_text': 'Hello,',
            'new_text': 'Welcome,', 'summary': 'Use a warmer greeting'}
    rejected = store.callback('edit-1', 'workspace_edit_file', args)
    assert rejected['applied'] is False
    assert 'Hello,' in store.path.read_text()
    assert store.revision == 1
    latest = store.callback('read-2', 'workspace_read_file', {})
    args['base_revision'] = latest['revision']
    applied = store.callback('edit-2', 'workspace_edit_file', args)
    assert applied['applied'] is True
    assert store.path.read_text().startswith('# Keep this human comment\n')
    assert 'Welcome,' in store.path.read_text()
    assert store.callback('edit-2', 'workspace_edit_file', args) == applied
    assert store.revision == 2
    assert any(e['kind'] == 'conflict' for e in store.events())


def test_agent_callback_receipt_survives_host_restart(tmp_path):
    store = WorkspaceStore(tmp_path)
    args = {'base_revision': 0, 'old_text': 'Hello,', 'new_text': 'Hi,', 'summary': 'Shorten greeting'}
    first = store.callback('call-1', 'workspace_edit_file', args)
    store.close()
    store = WorkspaceStore(tmp_path)
    assert store.callback('call-1', 'workspace_edit_file', args) == first
    assert store.revision == 1
    assert len([e for e in store.events() if e['kind'] == 'agent-edit']) == 1
    store.close()


def receive(socket, kind):
    for _ in range(20):
        message = socket.receive_json()
        if message['type'] == kind:
            return message
    raise AssertionError(f'No {kind} message')


def test_two_browser_connections_presence_live_edits_and_reconnect(tmp_path):
    with TestClient(create_app(tmp_path, with_agent=False)) as client:
        with client.websocket_connect('/workspace/connect?name=Alice') as alice:
            first = receive(alice, 'snapshot')
            with client.websocket_connect('/workspace/connect?name=Bob') as bob:
                second = receive(bob, 'snapshot')
                people = receive(bob, 'presence')['peers']
                assert {p['name'] for p in people} == {'Alice', 'Bob'}
                assert first['text'] == second['text']
                a = Document({n['id']: n for n in first['nodes']}, first['clock'])
                b = Document({n['id']: n for n in second['nodes']}, second['clock'])
                operation_a = a.replace(0, 0, '# Alice\n', 'alice')
                operation_b = b.replace(len(b.text), len(b.text), '\n# Bob\n', 'bob')
                alice.send_json({'type': 'edit', 'id': 'a1', 'operation': operation_a, 'author': 'Alice'})
                bob.send_json({'type': 'edit', 'id': 'b1', 'operation': operation_b, 'author': 'Bob'})
                for socket, doc in ((alice, a), (bob, b)):
                    for _ in range(2):
                        message = receive(socket, 'operation')
                        doc.apply(message['operation'])
                assert a.text == b.text == (tmp_path / 'app.py').read_text()
                assert a.text.startswith('# Alice\n') and a.text.endswith('# Bob\n')
                bob.send_json({'type': 'presence', 'name': 'Bob', 'cursor': operation_b['inserts'][0]['id']})
                # Alice may have a prior presence event queued; consume until Bob's cursor is visible.
                for _ in range(3):
                    presence = receive(alice, 'presence')
                    if any(p['name'] == 'Bob' and p['cursor'] for p in presence['peers']):
                        break
                else:
                    raise AssertionError('No collaborator cursor')
            with client.websocket_connect('/workspace/connect?name=Bob') as reconnected:
                snapshot = receive(reconnected, 'snapshot')
                assert snapshot['text'] == a.text and snapshot['revision'] == 2
                reconnected.send_json({'type': 'edit', 'id': 'b1', 'operation': operation_b, 'author': 'Bob'})
                assert receive(reconnected, 'operation')['revision'] == 2


def test_host_rejects_cross_origin_websocket(tmp_path):
    from starlette.websockets import WebSocketDisconnect
    with TestClient(create_app(tmp_path, with_agent=False)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/workspace/connect', headers={'origin': 'https://unrelated.example'}):
                pass


def test_idle_presence_expires_without_removing_an_active_same_name_peer(tmp_path, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr('workspace.server.PRESENCE_IDLE_TIMEOUT', 0.3)
    with TestClient(create_app(tmp_path, with_agent=False)) as client:
        with client.websocket_connect('/workspace/connect?name=Alex') as abandoned:
            stale_id = receive(abandoned, 'snapshot')['self']
            with client.websocket_connect('/workspace/connect?name=Alex') as active:
                active_id = receive(active, 'snapshot')['self']
                assert len(receive(active, 'presence')['peers']) == 2
                # Keep the live connection responsive while the abandoned page expires.
                for _ in range(8):
                    active.send_json({'type': 'ping'})
                    receive(active, 'pong')
                    time.sleep(0.06)
                active.send_json({'type': 'presence', 'file': 'planner_agent.py'})
                people = receive(active, 'presence')['peers']
                assert [p['id'] for p in people] == [active_id]
                assert stale_id != active_id
                with pytest.raises(WebSocketDisconnect) as closed:
                    while True:
                        abandoned.receive_json()
                assert closed.value.code == 1001


def test_full_file_paste_is_accepted_over_websocket(tmp_path):
    with TestClient(create_app(tmp_path, with_agent=False)) as client:
        with client.websocket_connect('/workspace/connect?name=Alice') as socket:
            snapshot = receive(socket, 'snapshot')
            doc = Document({n['id']: n for n in snapshot['nodes']}, snapshot['clock'])
            content = '#' + 'x' * 19_999
            operation = doc.replace(0, len(doc.text), content, 'alice')
            socket.send_json({'type': 'edit', 'id': 'paste', 'operation': operation, 'author': 'Alice'})
            assert receive(socket, 'operation')['revision'] == 1
            assert (tmp_path / 'app.py').read_text() == content


def test_workspace_refuses_a_second_callback_host(tmp_path):
    with TestClient(create_app(tmp_path, with_agent=False)):
        with pytest.raises(RuntimeError, match='already has a callback host'):
            with TestClient(create_app(tmp_path, with_agent=False)):
                pass


async def test_callback_host_writes_once_before_acknowledging(store):
    from workspace.agent_host import AgentHost
    acknowledged = asyncio.Event()
    calls = [SimpleNamespace(tool_id='call-1', tool_name='workspace_edit_file', tool_input={
        'base_revision': 0, 'old_text': 'Hello,', 'new_text': 'Hi,', 'summary': 'Shorten greeting',
    })]
    class Client:
        async def get_pending_callbacks(self):
            return calls
        async def provide_callback_result(self, tool_id, *, result, update_id):
            assert 'Hi,' in store.path.read_text()
            assert 'operation' not in result
            assert result['applied'] is True
            assert update_id == 'workspace:call-1'
            acknowledged.set()
    messages = []
    host = AgentHost(store, messages.append)
    host.client = Client()
    task = asyncio.create_task(host.run())
    try:
        await asyncio.wait_for(acknowledged.wait(), 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert store.revision == 1
    assert messages[0]['type'] == 'operation'
