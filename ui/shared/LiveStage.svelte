<script lang="ts">
  import { AgentSession, HttpTransport } from '@temporalio/agent-harness-svelte';
  import type { SdlcStageAgentV2 } from '../../client_sdk/SdlcStageAgentV2';
  import HarnessTimeline from './HarnessTimeline.svelte';
  import type { StageState } from '../../client_sdk/SdlcStageAgentV2';
  let { workflowId, saved }: { workflowId: string; saved?: StageState } = $props();
  const session = new AgentSession<SdlcStageAgentV2>({
    get sessionId() { return workflowId; },
    transport: new HttpTransport({ baseUrl: new URL('/api/', location.origin).href }),
    idlePollMs: 1500,
  });
  const progress = $derived(session.states.progress || saved);
</script>

<section aria-label="Live stage progress">
  {#if progress}
    <div class="heading"><strong>{progress.paused ? 'Paused at a tool boundary' : progress.activity || progress.status}</strong><span>{session.connection}</span></div>
    <div class="metrics"><span>{progress.completed_tools} tools completed</span><span>{progress.failed_tools} tool errors</span><span>{progress.usage.total_tokens.toLocaleString()} tokens</span></div>
    {#if progress.changed_files.length}<p>Saved files: {progress.changed_files.join(', ')}</p>{/if}
    {#each Object.entries(progress.checks) as [check, status]}<p>{check.replaceAll('_',' ')} · {status}</p>{/each}
    {#if progress.feedback.length}<p>Latest teammate feedback: {progress.feedback.at(-1)}</p>{/if}
    {#if progress.error}<p class="error" role="alert">{progress.error}</p>{/if}
  {:else}<p class="muted">Connecting to the agent’s live progress…</p>{/if}
  {#if session.error}<p class="muted">{saved ? 'Showing saved progress. The closed agent’s live stream is unavailable.' : 'Live progress is reconnecting. Saved output remains available.'}</p>{/if}
  <details><summary>Model and tool activity</summary><HarnessTimeline messages={session.messages} /></details>
</section>

<style>
  section { border:1px solid var(--line); border-radius:8px; padding:16px; margin:16px 0; font-size:11px; overflow-wrap:anywhere; }
  .heading { display:flex; gap:12px; justify-content:space-between; }
  .heading span,.muted,p,.metrics { color:var(--muted); }
  .metrics { display:flex; flex-wrap:wrap; gap:16px; margin:12px 0; }
  .error { color:var(--bad); }
  details { margin-top:15px; }
  summary { cursor:pointer; color:var(--muted); }
</style>
