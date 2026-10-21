<script lang="ts">
  import { untrack } from 'svelte';
  import { request } from './platform';
  import { sessionName } from './identity';
  import type { SupportAgentDetails, SupportPrompt } from './supportAgents';

  let { agentId, name, prompt }: { agentId: string; name: string; prompt: SupportPrompt } = $props();
  let saved = $state(untrack(() => prompt));
  let draft = $state(untrack(() => prompt.content));
  let busy = $state(false), error = $state(''), message = $state('');
  const dirty = $derived(draft !== saved.content);
  const inputId = $derived(`instructions-${agentId}-${prompt.id}`);

  function change(value: string) { draft = value; message = ''; }

  async function update(save: boolean) {
    if (busy) return;
    busy = true; error = ''; message = '';
    try {
      const result = await request<SupportAgentDetails>('/support-agents/' + encodeURIComponent(agentId), save ? {
        target: prompt.id, version: saved.version, content: draft, author: sessionName() || 'Teammate',
      } : undefined);
      const current = result.prompts.find(item => item.id === prompt.id);
      if (!current) throw new Error('Instructions were not returned. Your draft has been kept.');
      saved = current; draft = current.content;
      message = save ? 'Instructions saved. New requests will use this version.' : 'Loaded the latest saved instructions.';
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
</script>

<svelte:window onbeforeunload={(event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } }} />

<form onsubmit={(event) => { event.preventDefault(); if (dirty && draft.trim()) void update(true); }} aria-busy={busy}>
  <label for={inputId}>{name} instructions{agentId === 'studio-designer' ? ` for ${prompt.label}` : ''}</label>
  <p id={inputId + '-help'}>Edit how this agent responds. Saving keeps its existing tools, model settings, and approval rules.</p>
  <textarea id={inputId} rows="16" maxlength="20000" required disabled={busy} aria-describedby={inputId + '-help'}
    value={draft} oninput={(event) => change(event.currentTarget.value)} spellcheck="false"></textarea>
  <p class="saved">{dirty ? 'Unsaved changes' : 'Up to date'}{saved.updated_at ? ` · Saved ${new Date(saved.updated_at * 1000).toLocaleString()} by ${saved.author}` : ' · Default instructions'}</p>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <p class="status" role="status">{message}</p>
  <div class="actions">
    <button class="studio-button studio-button--primary" type="submit" disabled={busy || !dirty || !draft.trim()}>{busy ? 'Working…' : 'Save instructions'}</button>
    <button class="studio-button studio-button--secondary" type="button" disabled={busy || !dirty} onclick={() => change(saved.content)}>Discard changes</button>
    <button class="studio-button studio-button--secondary" type="button" disabled={busy || draft === saved.default_content} onclick={() => change(saved.default_content)}>Use default instructions</button>
    <button class="studio-button studio-button--secondary" type="button" disabled={busy} onclick={() => void update(false)}>Reload saved instructions</button>
  </div>
  <p class="note">Reload replaces your draft with the latest saved version. Default instructions are only applied after you save.</p>
</form>

<style>
  form { min-width:0; }
  label { display:block; margin-block:16px 8px; font-size:13px; font-weight:600; }
  p { font-size:13px; line-height:1.6; color:var(--color-content-secondary); overflow-wrap:anywhere; }
  textarea { box-sizing:border-box; display:block; width:100%; min-height:320px; resize:vertical; padding:16px; border:1px solid var(--color-border-secondary); border-radius:6px; background:var(--color-surface-primary); color:var(--color-content-primary); font:13px/1.7 var(--mono); }
  textarea:focus-visible { outline:2px solid var(--color-content-brand); outline-offset:3px; }
  textarea:disabled { opacity:.65; }
  .actions { display:flex; flex-wrap:wrap; gap:8px; }
  .actions .studio-button { min-height:44px; }
  .error { color:var(--color-content-danger); }
  .status:empty { display:none; }
  .note, .saved { font-size:12px; }
</style>
