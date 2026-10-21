import { test } from 'node:test';
import assert from 'node:assert/strict';
const { currentHarnessSession } = await import(process.env.HARNESS_SESSION_MODULE);

const pipeline = {
  current_stage: 'development',
  execution: { workflow_id: 'current-coordinator' },
  stages: { development: { run_id: 'current-run' } },
  runs: [
    { id: 'old-run', execution_id: 'old-coordinator', workflow_id: 'old-agent' },
    { id: 'current-run', execution_id: 'current-coordinator', workflow_id: 'current-agent' },
  ],
};

test('selects the coordinator even when a current stage child exists', () => {
  assert.equal(currentHarnessSession(pipeline), 'current-coordinator');
});
test('follows a new coordinator independently of child runs', () => {
  assert.equal(currentHarnessSession({ ...pipeline, execution: { workflow_id: 'new-coordinator' } }), 'new-coordinator');
  assert.equal(currentHarnessSession({ ...pipeline, runs: [] }), 'current-coordinator');
});
test('keeps the coordinator across stage transitions and never falls back to a child', () => {
  assert.equal(currentHarnessSession({ ...pipeline, execution: undefined }), '');
  assert.equal(currentHarnessSession({ ...pipeline, current_stage: 'review', stages: {} }), 'current-coordinator');
  assert.equal(currentHarnessSession(null), '');
});
