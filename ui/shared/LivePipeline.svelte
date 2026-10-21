<script lang="ts">
  import { AgentSession, HttpTransport } from '@temporalio/agent-harness-svelte';
  import type { FeaturePipelineAgent } from '../../client_sdk/FeaturePipelineAgent';
  import HarnessTimeline from './HarnessTimeline.svelte';
  import { names, statusLabel } from './platform';
  let { workflowId }: { workflowId: string } = $props();
  const session = new AgentSession<FeaturePipelineAgent>({
    get sessionId() { return workflowId; },
    transport: new HttpTransport({ baseUrl: new URL('/api/', location.origin).href }), idlePollMs: 1500,
  });
  const pipeline = $derived(session.states.pipeline);
</script>

<section aria-label="Agent workflow activity">
  <h3>Agent workflow</h3>
  {#if pipeline}<p>{names[pipeline.current_stage]} · {pipeline.paused ? 'Paused' : statusLabel(pipeline.status)} · {pipeline.repairs} repair passes</p>{/if}
  <p class="connection">{session.connection}</p>
  {#each Object.values(session.agents) as agent (agent.agentId)}
    <details>
      <summary>{agent.parentId ? names[agent.agentKey || ''] || agent.agentKey : 'Feature coordinator'}<span>{agent.lifecycle === 'stopped' ? 'Finished' : agent.agentStatus === 'busy' ? 'Working' : 'Idle'}</span></summary>
      {#if agent.unavailable}<p>Live history is unavailable for this finished agent. Its saved output and tool evidence remain in the stage tabs.</p>{/if}
      <HarnessTimeline messages={agent.messages} />
    </details>
  {/each}
  {#if session.error}<p class="error">The live workflow connection is unavailable. Saved stage records are still available.</p>{/if}
</section>

<style>
  section { border:1px solid var(--line); border-radius:8px; padding:18px; margin-bottom:25px; font-size:12px; }
  h3 { font-size:13px; margin:0; }
  p,.connection { color:var(--muted); font-size:11px; line-height:1.8; }
  details { border-top:1px solid var(--line); padding:12px 0; }
  summary { display:flex; justify-content:space-between; cursor:pointer; gap:12px; }
  summary span { color:var(--muted); }
  .error { color:var(--bad); }
</style>
