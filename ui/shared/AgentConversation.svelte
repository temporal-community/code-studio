<script lang="ts">
  import { AgentSession, HttpTransport } from "@temporalio/agent-harness-svelte";
  import type { SdlcBuilderAgent } from "../../client_sdk/SdlcBuilderAgent";
  import type { StudioDesignerAgent } from "../../client_sdk/StudioDesignerAgent";
  let { sessionId, featureMode = false, agentName = '' }: { sessionId: string; featureMode?: boolean; agentName?: string } = $props();
  const agent = new AgentSession<SdlcBuilderAgent | StudioDesignerAgent>({
    get sessionId() { return sessionId; },
    transport: new HttpTransport({ baseUrl: new URL('/api/', location.origin).href }),
    idlePollMs: 1000,
    // The workspace host is the sole callback executor. Browsers only observe.
  });
</script>

<div class="agent-connection"><span class:working={agent.agentStatus === 'busy'}></span>
  {agent.agentStatus === 'busy' ? 'Builder agent is working' : 'Builder agent'}
  <small>{agent.connection}</small>
</div>
{#if agent.error}<p class="agent-error">Agent stream unavailable. Your file edits are still shared.</p>{/if}
{#if !agent.messages.length}
  <div class="conversation-empty">{agentName ? `Ask the designer to customize the ${agentName} agent’s flow, instructions, model, or tools. Both teammates see its edits and validation results.` : featureMode ? "Ask the designer to customize the Planner’s Python flow, prompts, model, or tools. Both teammates see its edits and validation results." : "Ask the builder to change the review flow, instructions, or evaluation rules. Both collaborators will see the conversation and edits."}</div>
{/if}
{#each agent.messages as message (message.id)}
  <article class="human-message"><span>COLLABORATOR</span><p>{message.input.text}</p></article>
  <article class="agent-message">
    <span>BUILDER AGENT <small>{message.status}</small></span>
    {#each message.parts as part}
      {#if part.type === 'reply_delta'}<p>{part.text}</p>
      {:else if part.type === 'tool'}
        <div class="tool-call">{part.toolName === 'project_read_file' ? 'Read project file' : part.toolName === 'project_edit_file' ? 'Edit project file' : part.toolName}<small>{part.state}</small></div>
      {/if}
    {/each}
    {#if message.output?.text && !message.parts.some(part => part.type === 'reply_delta' && part.text)}<p>{message.output.text}</p>{/if}
    {#if message.status === 'error'}<p class="agent-error">{message.error}</p>{/if}
  </article>
{/each}

<style>
  .agent-connection { display:flex; align-items:center; gap:7px; padding:12px 0; color:var(--muted); font-size:12px; }
  .agent-connection>span { width:6px; height:6px; border-radius:50%; background:var(--good); }
  .agent-connection .working { background:var(--accent); animation:pulse 1.2s infinite; }
  small { margin-left:auto; color:var(--muted); font-size:10px; }
  article { padding:14px; border:1px solid var(--line); border-radius:10px; margin:8px 0; overflow-wrap:anywhere; }
  article>span { display:flex; gap:8px; color:var(--muted); font-size:9px; letter-spacing:1.1px; }
  .human-message { background:var(--panel-2); }
  .agent-message { border-color:color-mix(in srgb,var(--accent) 23%,var(--line)); }
  p { margin:9px 0 0; white-space:pre-wrap; font-size:12px; line-height:1.65; }
  .tool-call { display:flex; margin-top:9px; padding:7px 9px; background:var(--panel-2); border-radius:5px; font:10px var(--mono); }
  .conversation-empty { padding:20px 8px; color:var(--muted); font-size:12px; line-height:1.8; }
  .agent-error { color:var(--bad); font-size:11px; }
</style>
