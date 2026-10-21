import { test } from 'node:test';
import assert from 'node:assert/strict';
const { approvalInbox } = await import(process.env.APPROVAL_INBOX_MODULE);

const feature = (id, pipeline_status, current_stage = 'development') => ({
  id, title: `Feature ${id}`, pipeline_status, current_stage,
});

test('projects only the exact approval statuses in source order', () => {
  const result = approvalInbox([
    feature('plan', 'needs_plan_approval', 'planner'),
    feature('running', 'running', 'development'),
    feature('review', 'needs_release_approval', 'deployment'),
    feature('other', 'needs_review', 'review'),
    feature('unknown', 'needs_approval', 'planner'),
  ]);
  assert.deepEqual(result, [
    { ...feature('plan', 'needs_plan_approval', 'planner'), approvalType: 'Plan approval', approvalStage: 'planner' },
    { ...feature('review', 'needs_release_approval', 'deployment'), approvalType: 'Review approval', approvalStage: 'review' },
  ]);
});

test('uses current stage for display data and gate stage for navigation', () => {
  const [entry] = approvalInbox([feature('review', 'needs_release_approval', 'deployment')]);
  assert.equal(entry.current_stage, 'deployment');
  assert.equal(entry.approvalStage, 'review');
});

test('returns an empty list when no feature waits at either gate', () => {
  assert.deepEqual(approvalInbox([feature('running', 'running'), feature('done', 'complete')]), []);
  assert.deepEqual(approvalInbox([]), []);
});
