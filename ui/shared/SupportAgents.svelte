<script lang="ts">
  import { request } from './platform';
  import type { SupportAgent, SupportAgentDetails } from './supportAgents';
  import SupportInstructionEditor from './SupportInstructionEditor.svelte';

  let { agents }: { agents: SupportAgent[] } = $props();
  let details = $state<Record<string, SupportAgentDetails>>({});
  let loading = $state<Record<string, boolean>>({});
  let errors = $state<Record<string, string>>({});
  let studioTarget = $state('ideation');

  async function loadDetails(id: string) {
    if (loading[id] || details[id]) return;
    loading[id] = true;
    errors[id] = '';
    try {
      details[id] = await request<SupportAgentDetails>('/support-agents/' + encodeURIComponent(id));
    } catch (error) {
      errors[id] = (error as Error).message;
    } finally {
      loading[id] = false;
    }
  }
</script>

<section class="support-agents" aria-labelledby="support-agents-title">
  <div class="heading">
    <h2 id="support-agents-title">Support agents <span>{agents.length}</span></h2>
    <p>Assistants for customizing agents, finding improvements, and resolving conflicts.</p>
  </div>
  <div class="agent-list">
    {#each agents as agent (agent.id)}
      {@const agentDetails = details[agent.id]}
      <details class="support-agent" ontoggle={(event) => { if (event.currentTarget.open) void loadDetails(agent.id); }}>
        <summary>
          <div class="identity"><h3>{agent.name}</h3><p>{agent.description}</p></div>
          <div class="quick-facts">
            <span class="model">{agent.model || 'Model needs attention'}</span>
            <span>{agent.usage.requests} {agent.usage.requests === 1 ? 'request' : 'requests'} tracked{agent.usage.running ? ` · ${agent.usage.running} in progress` : ''}</span>
            <span class="disclosure"><span class="closed-label">View details</span><span class="open-label">Hide details</span> <span aria-hidden="true">⌄</span></span>
          </div>
        </summary>
        <div class="agent-details">
          <dl class="facts">
            <div><dt>When it runs</dt><dd>{agent.trigger}</dd></div>
            <div><dt>Model settings</dt><dd>{agent.model_source}. Up to {agent.max_turns} {agent.max_turns === 1 ? 'model turn' : 'model turns'} per request.
              {#if agent.model_settings_url}<a href={agent.model_settings_url}>Open {agent.model_stage === 'ideation' ? 'Ideation' : 'Development'} settings</a>{/if}
              {#if agent.model_error}<p class="error" role="alert">{agent.model_error}</p>{/if}
            </dd></div>
            <div><dt>Capabilities</dt><dd>{agent.capabilities.join(' · ')}</dd></div>
            <div><dt>Recorded activity</dt><dd>
              {#if agent.id === 'studio-designer'}{agent.usage.submitted} submitted{:else}{agent.usage.completed} completed{/if}
              · {agent.usage.failed} failed · {agent.usage.interrupted} outcomes unknown
              <p>{agent.usage_note}</p>
              <p>Tracking began {new Date(agent.usage.since * 1000).toLocaleString()}. Earlier requests are not included. Outcomes may be unknown after an interruption or host restart.</p>
            </dd></div>
          </dl>
          <div class="instructions" aria-busy={loading[agent.id] || false}>
            <h4>Instructions</h4>
            {#if loading[agent.id]}
              <p role="status">Loading instructions…</p>
            {:else if errors[agent.id]}
              <p class="error" role="alert">{errors[agent.id]}</p>
              <button class="studio-button studio-button--secondary" onclick={() => void loadDetails(agent.id)}>Retry loading instructions</button>
            {:else if agentDetails}
              <p>{agentDetails.instructions_note}</p>
              {#if agent.id === 'studio-designer'}
                <label for="studio-prompt-target">Instructions for customizing</label>
                <select id="studio-prompt-target" bind:value={studioTarget}>
                  {#each agentDetails.prompts as prompt}<option value={prompt.id}>{prompt.label}</option>{/each}
                </select>
                <a class="studio-button studio-button--secondary" href={'/?view=studio&agent=' + studioTarget}>Open {agentDetails.prompts.find(prompt => prompt.id === studioTarget)?.label} Studio</a>
              {/if}
              {#if agent.id === 'improvement-advisor'}
                <p>Creates the title, work description, and acceptance criteria before a feature starts. The workflow’s Ideation agent runs after you start the feature and has separate instructions in Agent Studio.</p>
              {/if}
              {#each agentDetails.prompts as prompt (prompt.id)}
                <div hidden={agent.id === 'studio-designer' && prompt.id !== studioTarget}>
                  <SupportInstructionEditor agentId={agent.id} name={agent.name} {prompt} />
                </div>
              {/each}
            {/if}
          </div>
        </div>
      </details>
    {/each}
  </div>
</section>

<style>
  .support-agents { max-width:1250px; margin-block:32px; color:var(--color-content-primary); }
  .heading { margin-block-end:16px; }
  h2 { margin:0; font-size:16px; font-weight:600; }
  h2 span { margin-inline-start:8px; color:var(--color-content-secondary); font-size:12px; }
  .heading p, .identity p { margin:8px 0 0; color:var(--color-content-secondary); font-size:13px; line-height:1.6; }
  .agent-list { border:1px solid var(--color-border-primary); border-radius:8px; overflow:hidden; background:var(--color-surface-primary); }
  .support-agent + .support-agent { border-block-start:1px solid var(--color-border-primary); }
  summary { display:flex; align-items:center; justify-content:space-between; gap:24px; padding:20px; min-height:44px; cursor:pointer; list-style:none; }
  summary::-webkit-details-marker { display:none; }
  summary:hover { background:var(--color-interactive-tertiary-hover); }
  summary:active { background:var(--color-interactive-tertiary-press); }
  summary:focus-visible { outline:2px solid var(--color-content-brand); outline-offset:-3px; }
  .identity { min-width:0; }
  h3 { margin:0; font-size:15px; font-weight:600; }
  .quick-facts { flex:none; display:grid; gap:8px; text-align:end; font-size:12px; color:var(--color-content-secondary); }
  .model { font-family:var(--mono); color:var(--color-content-primary); }
  .disclosure { color:var(--color-content-brand); }
  .open-label, details[open] .closed-label { display:none; }
  details[open] .open-label { display:inline; }
  details[open] .disclosure > [aria-hidden] { display:inline-block; transform:rotate(180deg); }
  .agent-details { padding:0 20px 24px; }
  .facts { display:grid; grid-template-columns:1fr 1fr; gap:20px 32px; border-block-start:1px solid var(--color-border-primary); padding-block-start:20px; margin:0 0 24px; }
  dt, h4 { margin:0 0 8px; font-size:13px; font-weight:600; }
  dd { margin:0; font-size:13px; line-height:1.6; color:var(--color-content-secondary); }
  dd p { margin:8px 0 0; }
  a { color:var(--color-content-brand); text-underline-offset:3px; }
  dd a { display:block; margin-block-start:8px; padding-block:8px; }
  a:focus-visible, select:focus-visible { outline:2px solid var(--color-content-brand); outline-offset:3px; }
  .instructions > p { font-size:13px; line-height:1.6; color:var(--color-content-secondary); }
  label { display:block; margin-block:16px 8px; font-size:13px; }
  select { font:inherit; font-size:13px; min-height:44px; max-width:100%; padding:8px 12px; border:1px solid var(--color-border-secondary); border-radius:6px; background:var(--color-surface-primary); color:var(--color-content-primary); }
  .error, .instructions > p.error { color:var(--color-content-danger); }
  .instructions .studio-button { min-height:44px; }
  @media(max-width:800px) { summary { align-items:start; flex-direction:column; gap:16px; } .quick-facts { text-align:start; } .facts { grid-template-columns:1fr; } }
</style>
