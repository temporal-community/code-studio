import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const moduleUrl = pathToFileURL(process.env.FEATURE_DRAFT_MODULE);
const drafts = await import(moduleUrl);
function storage() {
  const values = new Map();
  globalThis.localStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
}

test('multiple drafts survive reload and editing or starting one preserves the others', async () => {
  storage();
  const first = { ...drafts.loadFeatureDraft(), title: 'First', brief: 'Review this', directory: '/project', sourceMode: 'git', base: 'main', branch: 'feature/first', acceptance: 'Works' };
  drafts.storeFeatureDraft('one', first);
  drafts.storeFeatureDraft('two', { ...first, title: 'Second' });
  const reloaded = await import(`${moduleUrl}?reload=1`);
  assert.equal(reloaded.listFeatureDrafts().length, 2);
  assert.deepEqual(Object.fromEntries(Object.keys(first).map(key => [key, reloaded.listFeatureDrafts().find(d => d.id === 'one')[key]])), first);
  drafts.storeFeatureDraft('one', { ...first, brief: 'Updated requirements' });
  assert.equal(drafts.listFeatureDrafts().length, 2);
  assert.equal(drafts.listFeatureDrafts().find(d => d.id === 'one').brief, 'Updated requirements');
  drafts.removeFeatureDraft('one');
  assert.deepEqual(drafts.listFeatureDrafts().map(d => d.id), ['two']);
});

test('saving incomplete drafts is allowed and does not overwrite form recovery', () => {
  storage();
  const form = { ...drafts.loadFeatureDraft(), brief: 'Unfinished idea' };
  drafts.saveFeatureDraft(form);
  drafts.storeFeatureDraft('one', { ...form, brief: 'Saved idea' });
  assert.equal(drafts.loadFeatureDraft().brief, 'Unfinished idea');
  assert.equal(drafts.listFeatureDrafts()[0].brief, 'Saved idea');
});

test('explicit storage failures are surfaced instead of reporting a successful save', () => {
  storage();
  const form = drafts.loadFeatureDraft();
  globalThis.localStorage.setItem = () => { throw new Error('Quota exceeded'); };
  assert.throws(() => drafts.storeFeatureDraft('one', form), /Quota exceeded/);
  assert.deepEqual(drafts.listFeatureDrafts(), []);
});

test('feature ideas survive reload and older saved drafts remain available', () => {
  storage();
  const form = { ...drafts.loadFeatureDraft(), idea: 'Make approvals easier to find' };
  drafts.saveFeatureDraft(form);
  drafts.storeFeatureDraft('new', form);
  assert.equal(drafts.loadFeatureDraft().idea, form.idea);
  assert.equal(drafts.listFeatureDrafts()[0].idea, form.idea);
  const { idea, ...oldForm } = form;
  localStorage.setItem('code-studio:saved-feature-drafts:v1', JSON.stringify([{ ...oldForm, id: 'old', updatedAt: 1 }]));
  assert.equal(drafts.listFeatureDrafts()[0].id, 'old');
  assert.equal(drafts.listFeatureDrafts()[0].idea, '');
});


test('deleting one draft persists across reload and preserves the others', async () => {
  storage();
  const form = { ...drafts.loadFeatureDraft(), title: 'Saved draft' };
  drafts.storeFeatureDraft('remove-me', form);
  drafts.storeFeatureDraft('keep-me', { ...form, title: 'Keep this draft' });
  assert.deepEqual(drafts.removeFeatureDraft('remove-me').map(d => d.id), ['keep-me']);
  const reloaded = await import(`${moduleUrl}?delete-reload=1`);
  assert.deepEqual(reloaded.listFeatureDrafts().map(d => d.id), ['keep-me']);
  assert.equal(reloaded.listFeatureDrafts()[0].title, 'Keep this draft');
});

test('deleting drafts surfaces storage read, parse, and write failures without replacing data', () => {
  storage();
  drafts.storeFeatureDraft('keep-me', { ...drafts.loadFeatureDraft(), title: 'Keep this draft' });
  const saved = localStorage.getItem('code-studio:saved-feature-drafts:v1');
  localStorage.getItem = () => { throw new Error('Storage unavailable'); };
  assert.throws(() => drafts.removeFeatureDraft('keep-me'), /Storage unavailable/);
  localStorage.getItem = () => '{invalid json';
  assert.throws(() => drafts.removeFeatureDraft('keep-me'));
  localStorage.getItem = () => saved;
  localStorage.setItem = () => { throw new Error('Quota exceeded'); };
  assert.throws(() => drafts.removeFeatureDraft('keep-me'), /Quota exceeded/);
  assert.equal(saved.includes('keep-me'), true);
});


test('deleting a saved draft preserves separate form recovery through the reset autosave', () => {
  storage();
  const recovery = { ...drafts.loadFeatureDraft(), title: 'Unsaved recovery', brief: 'Keep this work' };
  drafts.saveFeatureDraft(recovery);
  drafts.storeFeatureDraft('saved', { ...recovery, title: 'Saved draft' });

  const autosave = drafts.createFeatureDraftRecoveryAutosave();
  drafts.removeFeatureDraft('saved');
  autosave.preserveNextSave();
  autosave.save({ ...recovery, title: '', brief: '' });
  assert.deepEqual(drafts.loadFeatureDraft(), recovery);
  assert.deepEqual(drafts.listFeatureDrafts(), []);

  // The protection is limited to the deletion reset; subsequent new-form edits autosave.
  const laterEdit = { ...recovery, title: 'New form work' };
  autosave.save(laterEdit);
  assert.deepEqual(drafts.loadFeatureDraft(), laterEdit);
});
