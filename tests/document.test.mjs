import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const { SharedDocument } = await import(pathToFileURL(process.env.DOCUMENT_MODULE).href);
function document(text = 'Hello!') {
  const doc = new SharedDocument();
  doc.apply(doc.edit(text, 'seed'));
  return doc;
}
function clone(doc) {
  return new SharedDocument({ nodes: [...doc.nodes.values()], clock: doc.clock });
}

test('concurrent inserts and duplicate deliveries converge', () => {
  const alice = document(), bob = clone(alice);
  const a = alice.edit('Hello Alice!', 'alice');
  const b = bob.edit('Hello Bob!', 'bob');
  alice.apply(a); bob.apply(b);
  alice.apply(b); bob.apply(a); bob.apply(a);
  assert.equal(alice.text, 'Hello Bob Alice!');
  assert.equal(bob.text, alice.text);
});

test('offline edits replay over a newer snapshot without losing either author', () => {
  const local = document('abc'), server = clone(local);
  const first = local.edit('aXbc', 'alice-1'); local.apply(first);
  const second = local.edit('aXYbc', 'alice-2'); local.apply(second);
  const remote = server.edit('abcZ', 'bob-1'); server.apply(remote);
  const reconnected = clone(server);
  for (const operation of [first, second]) { reconnected.apply(operation); server.apply(operation); }
  reconnected.apply(first); reconnected.apply(second);
  assert.equal(reconnected.text, 'aXYbcZ');
  assert.equal(server.text, reconnected.text);
});

test('Unicode cursor anchors survive concurrent edits', () => {
  const doc = document('a😀b');
  const cursor = doc.anchor(3);
  doc.apply(doc.edit('Za😀b', 'alice'));
  assert.equal(doc.offset(cursor), 4);
  const second = doc.edit('Za😀🧪b', 'bob'); doc.apply(second);
  assert.equal(doc.text, 'Za😀🧪b');
});

test('removing a long selection does not overflow cursor recovery', () => {
  const doc = document('x'.repeat(20_000));
  const cursor = doc.anchor(20_000);
  doc.apply(doc.edit('', 'alice'));
  assert.equal(doc.offset(cursor), 0);
});

test('Python host and browser agree on a sequence of real browser operations', () => {
  const alice = document('a😀bc\ndef\n'), bob = clone(alice);
  const operations = [alice.edit('a😀bA\ndef\n', 'alice'), bob.edit('B😀bc\ndef\n', 'bob')];
  for (const operation of operations) alice.apply(operation);
  const seed = document('a😀bc\ndef\n');
  const python = spawnSync(process.env.CODE_STUDIO_PYTHON || '.venv/bin/python', ['-c', `
import json, sys
from workspace.document import Document
data = json.load(sys.stdin)
doc = Document({n['id']: n for n in data['nodes']}, data['clock'])
for operation in data['operations']:
    doc.apply(operation)
operation = doc.replace(0, 1, 'Python: ', 'agent')
doc.apply(operation)
print(json.dumps({'text': doc.text, 'operation': operation}))
`], { input: JSON.stringify({ nodes: [...seed.nodes.values()], clock: seed.clock, operations }), encoding: 'utf8' });
  assert.equal(python.status, 0, python.stderr);
  const response = JSON.parse(python.stdout);
  alice.apply(response.operation);
  assert.equal(alice.text, response.text);
});
