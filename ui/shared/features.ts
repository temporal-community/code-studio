export interface FeatureInput { id: string; author: string; kind: string; text: string; time: number }
export interface FeaturePlan {
  id: string; input_revision: number;
  plan: { summary: string; goals: string[]; acceptance_criteria: string[]; out_of_scope: string[];
    steps: { title: string; details: string; files: string[] }[]; test_plan: string[]; risks: string[];
    open_questions: string[]; input_coverage: { input_id: string; handling: string }[] };
}
export interface FeatureJob { agent_project?: { version: string; revision: number; files: Record<string, string> } | null; id: string; mode: string; status: string; error: string | null; trace: { name: string; detail: string }[]; usage: { total_tokens: number } | null }
export interface FeatureSummary { id: string; title: string; branch: string; status: string; input_revision: number; contributors: string[]; input_count: number }
export interface Feature extends FeatureSummary {
  description: string; acceptance: string; repository: string; worktree: string; base_sha: string;
  source_dirty: boolean; error: string | null; inputs: FeatureInput[]; plans: FeaturePlan[]; jobs: FeatureJob[];
  approval: { author: string; plan_id: string; input_revision: number } | null;
  implementation: { job_id: string; result: { summary: string; changed_files: string[]; suggested_checks: string[]; limitations: string[] } } | null;
  activity: { author: string; text: string; time: number }[];
  syntax_check?: { status: string; message: string; truncated: boolean; files: { path: string; status: string; error?: string }[] };
}
export const newId = () => crypto.randomUUID();
export const active = (job: FeatureJob) => ['queued', 'running'].includes(job.status);
export const label = (value: string) => ({ needs_review: 'Review plan', code_ready: 'Code ready', branch_failed: 'Branch failed' }[value] ?? value.replaceAll('_', ' '));
export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/workspace/features' + path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (response.status === 404) throw new Error('Feature builder API is unavailable. Restart just dev to load the updated Python host and worker.');
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'The request could not be saved. Check the fields and retry.');
  return result;
}
