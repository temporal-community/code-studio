import assert from 'node:assert/strict';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
const { compareStageResults } = await import(pathToFileURL(process.env.STAGE_RUN_COMPARISON_MODULE).href);

test('distinguishes absent results and fields from present empty text and lists', () => {
  const fields = compareStageResults({ summary: '', risks: [] }, null);
  assert.equal(fields.find(x => x.key === 'summary').kind, 'removed');
  assert.equal(fields.find(x => x.key === 'summary').leftValue, '');
  assert.equal(fields.find(x => x.key === 'handoff').kind, 'absent');
  assert.equal(fields.find(x => x.key === 'risks').leftPresent, true);
  assert.deepEqual(fields.find(x => x.key === 'risks').entries, []);
  assert.equal(compareStageResults({}, { summary: 'new' }).find(x => x.key === 'summary').kind, 'added');
});

test('matches identical duplicate entries by occurrence and leaves surplus as add/remove', () => {
  const same = { title: 'T', body: 'B' };
  const result = compareStageResults({ deliverables: [same, same] }, { deliverables: [same] })[1];
  assert.equal(result.kind, 'changed');
  assert.deepEqual(result.entries.map(x => x.kind).sort(), ['removed', 'unchanged']);
});

test('pairs unique exact identities and exposes changed text fields', () => {
  const result = compareStageResults(
    { deliverables: [{ title: 'A', body: 'old' }], findings: [{ title: 'Bug', path: 'x', severity: 'low', line: 1, explanation: 'old' }] },
    { deliverables: [{ title: 'A', body: 'new' }], findings: [{ title: 'Bug', path: 'x', severity: 'high', line: 2, explanation: 'new' }] });
  assert.deepEqual(result[1].entries[0].changes, [{field:'body',before:'old',after:'new'}]);
  assert.deepEqual(result[4].entries[0].changes.map(x => x.field), ['severity','line','explanation']);
});

test('ambiguous identities are not paired and text-only changed wording is addition plus removal', () => {
  const result = compareStageResults(
    { deliverables: [{title:'A',body:'old'}, {title:'A',body:'older'}], acceptance_criteria: ['old'] },
    { deliverables: [{title:'A',body:'new'}], acceptance_criteria: ['new'] });
  assert.deepEqual(result[1].entries.map(x => x.kind).sort(), ['added','removed','removed']);
  assert.deepEqual(result[2].entries.map(x => x.kind).sort(), ['added','removed']);
});

test('findings require unique path and title; other stage result fields are not compared', () => {
  const fields = compareStageResults({verdict:'ready',changed_files:['a'],findings:[{title:'T',path:'p',severity:'low',line:null,explanation:'x'}]},
    {verdict:'blocked',changed_files:['b'],findings:[{title:'T',path:'p',severity:'low',line:null,explanation:'x'}]});
  assert.equal(fields.length, 7);
  assert.equal(fields.find(x => x.key === 'findings').entries[0].kind, 'unchanged');
  assert.equal(fields.some(x => x.key === 'changed_files' || x.key === 'verdict'), false);
});
