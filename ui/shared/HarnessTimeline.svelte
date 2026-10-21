<script lang="ts">
  import type { MessagePart } from '@temporalio/agent-harness-svelte';
  let { messages }: { messages: readonly { id: string; status: string; startedAt: number | null; endedAt: number | null; parts: MessagePart[]; error: string | null }[] } = $props();
</script>

<div class="timeline">
  {#each messages as message (message.id)}
    <div class="turn"><strong>{message.status === 'running' ? 'Working' : message.status}</strong>{#if message.startedAt && message.endedAt}<span>{(message.endedAt - message.startedAt).toFixed(1)}s</span>{/if}</div>
    {#each message.parts as part}
      {#if part.type === 'model_interaction'}
        <p class="model">{part.model || 'Model'} · {part.status}{#if part.usage?.total_tokens != null} · {part.usage.total_tokens.toLocaleString()} tokens{/if}</p>
      {:else if part.type === 'tool'}
        <details class:failed={part.state === 'failed' || part.state === 'denied'}>
          <summary><code>{part.toolName}</code><span>{part.state.replaceAll('_', ' ')}</span></summary>
          <pre>{JSON.stringify(part.input, null, 2)}</pre>
          {#if part.output}<pre>{part.output}</pre>{/if}
          {#if part.error}<p role="alert">{part.error}</p>{/if}
          {#if part.approval}<p>{part.approval.approved ? 'Approved' : 'Declined'}{part.approval.reason ? ': ' + part.approval.reason : ''}</p>{/if}
        </details>
      {:else if part.type === 'subagent'}<p>{part.agentKey} · {part.status === 'running' ? 'Working' : part.status}</p>
      {:else if part.type === 'reply_delta'}<p class="reply">{part.text}</p>{/if}
    {/each}
    {#if message.error}<p class="failed" role="alert">{message.error}</p>{/if}
  {/each}
</div>

<style>
  .timeline { font-size:11px; overflow-wrap:anywhere; }
  .turn, summary { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  .turn { margin:20px 0 8px; text-transform:capitalize; }
  span,.model { color:var(--muted); }
  details { border-bottom:1px solid var(--line); padding:10px 0; }
  summary { cursor:pointer; }
  code { font-size:10px; }
  pre { max-height:260px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; padding:12px; background:var(--panel-2); border-radius:6px; font-size:10px; line-height:1.7; }
  .reply { white-space:pre-wrap; line-height:1.8; }
  .failed { color:var(--bad); }
</style>
