import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const { SharedDocument } = await import(pathToFileURL(process.env.DOCUMENT_MODULE).href);
const { highlight } = await import(pathToFileURL(process.env.PYTHON_MODULE).href);
const { applyIfCurrent, detectRemoteChange } = await import(pathToFileURL(process.env.FEATURE_DIFF_MODULE).href);

test('highlighting preserves multiline strings, Unicode, and incomplete code exactly', () => {
  for (const source of [
    'def greet(name: str) -> str:\n    return f"Hi, {name}!" # 😀\n',
    '"""A <script> tag\nnot HTML\n"""\nx = "unfinished\n',
    '# 🧪 Python\nif True:\n\tprint(None, 42)\n',
  ]) {
    const lines = highlight(source);
    assert.equal(lines.map(tokens => tokens.map(t => t.text).join('')).join('\n'), source);
    // Surrogate pairs must stay in a single span to render emoji correctly.
    for (const token of lines.flat()) assert.equal(token.text.isWellFormed(), true);
  }
  const tokens = highlight('def greet():\n    return "hello" # comment').flat();
  assert.equal(tokens.find(t => t.text === 'def').cls, 'keyword');
  assert.equal(tokens.find(t => t.text === 'greet').cls, 'fn def');
  assert.equal(tokens.find(t => t.text === '"hello"').cls, 'string');
  assert.equal(tokens.find(t => t.text === '# comment').cls, 'comment');
});

test('Ruff changes preserve browser anchors and a concurrent collaborator insertion', () => {
  const source = '# 🧪 Keep this\ndef greet( name:str )->str:\n return "Hi, "+name\n';
  const python = spawnSync(process.env.CODE_STUDIO_PYTHON || '.venv/bin/python', ['-c', `
import json, sys
from workspace.formatting import format_python
print(json.dumps(format_python(sys.stdin.read())))
`], { input: source, encoding: 'utf8' });
  assert.equal(python.status, 0, python.stderr);
  const formatted = JSON.parse(python.stdout);
  const alice = new SharedDocument();
  alice.apply(alice.edit(source, 'seed'));
  const snapshot = { nodes: [...alice.nodes.values()], clock: alice.clock };
  const bob = new SharedDocument(snapshot);
  const anchor = alice.anchor(source.indexOf('greet') + 'greet'.length);
  const format = alice.patch(formatted.changes, 'format');
  const human = bob.edit(source + '# Bob is still here\n', 'bob');
  alice.apply(format);
  assert.equal(alice.text, formatted.text);
  assert.equal(alice.offset(anchor), formatted.text.indexOf('greet') + 'greet'.length);
  alice.apply(human);
  bob.apply(human); bob.apply(format);
  assert.equal(alice.text, formatted.text + '# Bob is still here\n');
  assert.equal(bob.text, alice.text);
});


test('an out-of-order file diff cannot replace the selected file diff', async () => {
  let selected = 'older.py';
  let visible = null;
  const pending = new Map();
  const response = path => new Promise(resolve => pending.set(path, resolve));
  const older = applyIfCurrent(response('older.py'), () => selected === 'older.py', value => { visible = value; });

  selected = 'current.py';
  const current = applyIfCurrent(response('current.py'), () => selected === 'current.py', value => { visible = value; });
  pending.get('current.py')({ path: 'current.py', patch: 'current patch' });
  assert.equal(await current, true);
  assert.equal(visible.patch, 'current patch');

  pending.get('older.py')({ path: 'older.py', patch: 'stale patch' });
  assert.equal(await older, false);
  assert.equal(visible.path, 'current.py');
  assert.equal(visible.patch, 'current patch');
});


test('a dirty-file refresh detects remote edits without replacing the draft or selection', async () => {
  const draft = { selected: 'app.py', content: 'local draft', savedHash: 'before' };
  let remoteChanged = false;
  const changed = await detectRemoteChange(
    Promise.resolve({ hash: 'after' }),
    draft.savedHash,
    () => draft.selected === 'app.py',
    () => { remoteChanged = true; },
  );

  assert.equal(changed, true);
  assert.equal(remoteChanged, true);
  assert.deepEqual(draft, { selected: 'app.py', content: 'local draft', savedHash: 'before' });

  remoteChanged = false;
  assert.equal(await detectRemoteChange(
    Promise.resolve({ hash: 'before' }), draft.savedHash, () => true, () => { remoteChanged = true; },
  ), false);
  assert.equal(remoteChanged, false);
});
