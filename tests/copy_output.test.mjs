import { test } from 'node:test';
import assert from 'node:assert/strict';
const { copyOutput } = await import(process.env.COPY_OUTPUT_MODULE);

test('copies exactly the selected check output, excluding another check', async () => {
  const selected = 'first check: failed\nline two\n';
  const other = 'another check: passed\n';
  const writes = [];
  const succeeded = await copyOutput(selected, async value => { writes.push(value); });
  assert.equal(succeeded, true);
  assert.deepEqual(writes, [selected]);
  assert.equal(writes[0].includes(other), false);
});

test('reports a rejected clipboard write as a failure', async () => {
  const succeeded = await copyOutput('recorded output', async () => { throw new Error('permission denied'); });
  assert.equal(succeeded, false);
});

test('copies an empty recorded output unchanged', async () => {
  let written = null;
  assert.equal(await copyOutput('', async value => { written = value; }), true);
  assert.equal(written, '');
});
