export const stages = ['ideation', 'planner', 'design', 'development', 'review', 'deployment'];
export const names: Record<string, string> = { ideation: 'Ideation', planner: 'Planner', design: 'Design', development: 'Development', review: 'Review', deployment: 'Deployment' };
export const statusLabel = (status: string) => ({ needs_plan_approval: 'Plan approval needed', needs_release_approval: 'Release approval needed', needs_review: 'Review needed', scope_changed: 'Scope updated', needs_input: 'Needs team input', changes_requested: 'Changes requested', complete: 'Release preview ready', ready: 'Handing off', running: 'Working', paused: 'Paused', failed: 'Failed', pending: 'Waiting', needs_approval: 'Awaiting approval' }[status] || status.replaceAll('_', ' '));
export const uid = () => crypto.randomUUID();
export const studioUrl = (agent: string, feature = '') => `?view=studio&agent=${agent}${feature ? '&feature=' + encodeURIComponent(feature) : ''}`;
export const featureUrl = (id: string, stage = '') => `?view=work&feature=${encodeURIComponent(id)}${stage ? '&stage=' + stage : ''}`;
export async function request<T = any>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/workspace' + path, body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(response.status === 404 ? 'Restart just dev to load the cooperative platform endpoints.' : typeof data.detail === 'string' ? data.detail : 'Could not save this request. Check the fields and try again.');
  return data;
}
export type StageTone = 'success' | 'running' | 'attention' | 'danger' | 'paused' | 'pending';
export const stageTone = (status: string): StageTone => {
  if (['completed', 'approved', 'complete'].includes(status)) return 'success';
  if (['running', 'ready'].includes(status)) return 'running';
  if (status === 'failed' || status === 'timed_out') return 'danger';
  if (status === 'paused') return 'paused';
  if (['blocked', 'needs_input', 'needs_review', 'needs_approval', 'needs_plan_approval', 'needs_release_approval', 'changes_requested', 'scope_changed'].includes(status)) return 'attention';
  return 'pending';
};
