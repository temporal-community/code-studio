import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

let page = 0;
const loadPage = () => import(`${pathToFileURL(process.env.IDENTITY_MODULE)}?page=${++page}`);
function storage(entries = []) {
  const values = new Map(entries);
  globalThis.sessionStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  return values;
}

test('one login survives route loads and refreshes, but a new session starts empty', async () => {
  storage();
  const login = await loadPage();
  assert.equal(login.sessionName(), '');
  assert.equal(login.joinSession('  Alex  '), true);
  for (const route of ['agents', 'ideation', 'planner', 'features', 'refresh']) {
    assert.equal((await loadPage()).sessionName(), 'Alex', route);
  }
  storage();
  assert.equal((await loadPage()).sessionName(), '');
});

test('rejects blank names and bounds the shared display name', async () => {
  const values = storage();
  const identity = await loadPage();
  assert.equal(identity.joinSession('   '), false);
  assert.equal(values.size, 0);
  identity.joinSession('a'.repeat(80));
  assert.equal(identity.sessionName().length, 40);
  assert.equal(values.get('code-studio:name').length, 40);
});

test('recognizes both legacy feature and current names, with current identity taking precedence', async () => {
  storage([['feature-name', ' Alex ']]);
  assert.equal((await loadPage()).sessionName(), 'Alex');
  storage([['feature-name', 'Alex'], ['code-studio:name', 'Sam']]);
  assert.equal((await loadPage()).sessionName(), 'Sam');
});

test('login replaces legacy identity with the canonical session key', async () => {
  const values = storage([['feature-name', 'Alex']]);
  const identity = await loadPage();
  identity.joinSession('Sam');
  assert.equal(values.has('feature-name'), false);
  assert.equal((await loadPage()).sessionName(), 'Sam');
});

test('blocked browser storage still allows joining the current page', async () => {
  globalThis.sessionStorage = {
    getItem() { throw new Error('Storage blocked'); },
    setItem() { throw new Error('Storage blocked'); },
  };
  const identity = await loadPage();
  assert.equal(identity.sessionName(), '');
  assert.equal(identity.joinSession('Alex'), true);
  assert.equal(identity.sessionName(), 'Alex');
});
