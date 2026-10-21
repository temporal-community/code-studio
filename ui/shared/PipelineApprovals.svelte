<script lang="ts">
  import { AgentSession, HttpTransport } from '@temporalio/agent-harness-svelte';
  import { request, uid } from './platform';
  let { workflowId, featureId, revision, author, canAct, onresolved }: {
    workflowId: string; featureId: string; revision: number; author: string; canAct: boolean; onresolved: () => void;
  } = $props();
  const session = new AgentSession({
    get sessionId() { return workflowId; },
    transport: new HttpTransport({ baseUrl: new URL('/api/', location.origin).href }), idlePollMs: 1000,
  });
  let busy = $state(false), error = $state(''), reason = $state('');
  // Keep the same request ID after a network failure so a decision can be safely retried.
  let pending: { id: string; tool_id: string; approved: boolean; reason: string } | null = null;
  async function decide(toolId: string, approved: boolean) {
    busy = true; error = '';
    if (!pending || pending.tool_id !== toolId || pending.approved !== approved || pending.reason !== reason) {
      pending = { id: uid(), tool_id: toolId, approved, reason };
    }
    try {
      await request(`/work/${featureId}/approvals`, { ...pending, author, revision });
      pending = null; reason = ''; onresolved();
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
</script>

{#each session.pendingApprovals.filter(item => item.part.toolName === 'approve_stage') as waiting (waiting.part.toolId)}
  <section class="approval" aria-label="Stage approval">
    <strong>{waiting.part.input.stage === 'planner' ? 'The plan is ready for your team' : 'Review is ready for release approval'}</strong>
    <p>{waiting.part.input.stage === 'planner' ? 'Review the plan and handoff below. Approval lets Design and Development continue.' : 'Review the findings and checks below. Approval starts the local release preview.'}</p>
    <label>Decision note (optional)<input bind:value={reason} maxlength="2000" placeholder="Explain a requested change or approval…" /></label>
    <div><button class="primary" disabled={!canAct || busy} onclick={() => void decide(waiting.part.toolId, true)}>{waiting.part.input.stage === 'planner' ? 'Approve plan & continue' : 'Approve release preview'}</button><button disabled={!canAct || busy} onclick={() => void decide(waiting.part.toolId, false)}>Request changes</button></div>
    <small>This decision applies only to scope {revision} and this saved run.</small>
  </section>
{/each}
{#if !session.pendingApprovals.length}<p class="waiting">Connecting to the saved approval request…</p>{/if}
{#if error}<p class="error" role="alert">{error}</p>{/if}
{#if session.error}<p class="error">The approval connection is unavailable. Reconnecting…</p>{/if}

<style>
  .approval { margin:16px 30px; padding:20px; border:1px solid var(--accent); border-radius:9px; background:var(--accent-soft); font-size:12px; }
  p { color:var(--muted); line-height:1.8; }
  label { display:flex; flex-direction:column; gap:7px; font-size:11px; margin:15px 0; }
  input { padding:9px 12px; border:1px solid var(--line); border-radius:6px; background:var(--panel); width:100%; }
  button { padding:9px 12px; border:1px solid var(--line-2); border-radius:6px; background:var(--panel); font-size:11px; }
  button:disabled { opacity:.5; }
  .primary { background:var(--accent); color:var(--accent-ink); }
  .approval>div { display:flex; gap:12px; flex-wrap:wrap; }
  small { display:block; margin-top:12px; color:var(--muted); }
  .error,.waiting { margin:12px 30px; font-size:12px; }
  .error { color:var(--bad); }
</style>
