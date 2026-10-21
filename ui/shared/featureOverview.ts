import { names } from './platform';

const activities: Record<string, string> = {
  ideation: 'Turning the brief into goals, constraints, and acceptance criteria.',
  planner: 'Inspecting the project and preparing an implementation plan.',
  design: 'Working out the interfaces, affected files, and implementation approach.',
  development: 'Implementing the approved plan and checking the changes.',
  review: 'Reviewing the implementation and recorded checks for release readiness.',
  deployment: 'Packaging the reviewed code and preparing local release instructions.',
};

export function featureOverview(pipeline: { status: string; current_stage: string; applied?: unknown } | null | undefined) {
  if (!pipeline) return { title: 'Getting ready', detail: 'Select a feature to follow its agents.' };
  const stage = names[pipeline.current_stage] || 'Agent';
  switch (pipeline.status) {
    case 'draft': return { title: 'Ready to begin', detail: 'Start the workflow to turn this feature brief into a plan.' };
    case 'needs_review': return { title: `${stage} needs attention`, detail: 'Inspect the saved handoff and decide whether to retry or approve the current result.' };
    case 'paused': return { title: `${stage} is paused`, detail: 'Work is saved. Resume the workflow when you are ready to continue.' };
    case 'blocked': return { title: `${stage} needs your decision`, detail: 'The agent could not approve the current result. Inspect its handoff, retry after changes, or choose Manual Approve.' };
    case 'failed': return { title: `${stage} stopped with an error`, detail: 'Inspect the error below. Saved files are retained when you retry.' };
    case 'needs_plan_approval': return { title: 'The plan is ready for approval', detail: 'Your approval lets Design and Development begin.' };
    case 'needs_release_approval': return { title: 'Review is ready for approval', detail: 'Your approval starts preparation of the local release preview.' };
    case 'complete': return pipeline.applied
      ? { title: 'Changes applied to your working directory', detail: 'The reviewed files are in your source checkout. Committing, merging, and remote deployment are separate steps.' }
      : { title: 'Your local release preview is ready', detail: 'Download the preview or apply reviewed changes from Deployment. Nothing has been remotely deployed.' };
    case 'scope_changed': return { title: 'The feature scope changed', detail: 'Restart from Ideation to create a fresh plan for the updated requirements.' };
    case 'running': return { title: `${stage} is working`, detail: activities[pipeline.current_stage] || 'Following the current stage’s instructions.' };
    default: return { title: `${stage} is next`, detail: activities[pipeline.current_stage] || 'Ready for the next stage.' };
  }
}
