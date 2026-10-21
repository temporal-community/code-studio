<script lang="ts">
  import { onMount, tick, untrack } from 'svelte';
  import { request, names, uid } from './platform';

  interface Turn { id: string; author: string; text: string; answer: string; suggested_requirement: string; status: string; error?: string }
  let { featureId, revision, run, questions, current, manual, canAct, enabled, author, help, onsubmitted }:
    { featureId: string; revision: number; run: any; questions: string[]; current: boolean; manual: boolean; canAct: boolean; enabled: boolean; author: string; help: string; onsubmitted: () => Promise<void> } = $props();
  const storageKey = untrack(() => `code-studio:clarification:${featureId}:${run.id}`);
  function restore() {
    try { return JSON.parse(sessionStorage.getItem(storageKey) || '{}') || {}; } catch { return {}; }
  }
  const saved = restore();
  let question = $state(typeof saved.question === 'string' ? saved.question : '');
  let requirements = $state(typeof saved.requirements === 'string' ? saved.requirements : '');
  let asking = $state(false), submitting = $state(false), error = $state(''), status = $state('');
  let localTurns = $state<Turn[]>([]);
  let requirementsInput = $state<HTMLTextAreaElement>();
  let questionRequest = $state<{ id: string; author: string; revision: number; text: string }>();
  let requirementRequest: { id: string; author: string; revision: number; text: string } | undefined;
  let disposed = false;
  const turns = $derived([...new Map<string, Turn>([...(run.clarifications || []), ...localTurns].map(t => [t.id, t] as [string, Turn])).values()]);
  const pending = $derived(turns.some(t => t.status === 'pending'));
  const available = $derived(current && canAct && !asking && !submitting);
  $effect(() => { try { sessionStorage.setItem(storageKey, JSON.stringify({ question, requirements })); } catch { /* Keep drafts in memory. */ } });
  onMount(() => () => { disposed = true; });

  async function ask() {
    if (!available || !enabled || !question.trim()) return;
    if (!questionRequest || questionRequest.text !== question.trim()) questionRequest = { id: uid(), author, revision, text: question.trim() };
    asking = true; error = ''; status = '';
    try {
      const turn = await request<Turn>(`/work/${featureId}/runs/${run.id}/clarify`, questionRequest);
      if (disposed) return;
      localTurns = [...localTurns.filter(t => t.id !== turn.id), turn];
      questionRequest = undefined;
      if (turn.status === 'failed') error = turn.error || 'Could not get an explanation. Try again.';
      else { question = ''; status = 'Explanation received. You can ask a follow-up or submit requirements.'; }
    } catch (cause) { if (!disposed) error = (cause as Error).message; }
    finally { if (!disposed) asking = false; }
  }

  async function useSuggestion(value: string) {
    requirements = value; status = 'Suggested requirements copied. Review and edit them before submitting.';
    await tick(); requirementsInput?.focus();
  }

  async function submit() {
    if (!available || !requirements.trim()) return;
    if (!requirementRequest || requirementRequest.text !== requirements.trim()) requirementRequest = { id: uid(), author, revision, text: requirements.trim() };
    submitting = true; error = ''; status = '';
    try {
      await request(`/work/${featureId}/runs/${run.id}/requirements`, requirementRequest);
      requirements = ''; requirementRequest = undefined;
      if (!disposed) { status = 'Requirements submitted to Team input.'; await onsubmitted(); }
    } catch (cause) { if (!disposed) error = (cause as Error).message; }
    finally { if (!disposed) submitting = false; }
  }
</script>

<section class="questions" aria-label="Stage questions">
  <h3>{manual ? 'Recorded blockers · manually accepted' : 'Needs attention'}</h3>
  <ul>{#each questions as question}<li>{question}</li>{/each}</ul>
  {#if !manual && help}<p>{help}</p>{/if}
  {#if turns.length}
    <div class="conversation" aria-label="Clarification conversation">
      {#each turns as turn (turn.id)}
        <article><h4>{turn.author}</h4><p>{turn.text}</p>
          {#if turn.status === 'completed'}
            <h4>{names[run.stage]} agent</h4><p>{turn.answer}</p>
            {#if turn.suggested_requirement}
              <div class="suggestion"><h4>Suggested requirements · review before submitting</h4><p>{turn.suggested_requirement}</p>
                {#if current}<button class="studio-button studio-button--secondary" disabled={!available || !!requirements.trim()} onclick={() => void useSuggestion(turn.suggested_requirement)}>Use suggested requirements</button>{/if}
              </div>
            {/if}
          {:else if turn.status === 'pending'}<p role="status">The agent is preparing an explanation…</p>
          {:else}<p class="error">{turn.error}</p>{/if}
        </article>
      {/each}
    </div>
  {/if}
  {#if current}
    <form onsubmit={(event) => { event.preventDefault(); void ask(); }}>
      <label for={'ask-' + run.id}>Ask the {names[run.stage]} agent</label>
      <textarea id={'ask-' + run.id} bind:value={question} rows="3" maxlength="4000" disabled={!canAct || asking || submitting}
        placeholder="What does this mean? Can you show an example and recommend an option?" aria-describedby={'ask-help-' + run.id}></textarea>
      <div class="action"><p id={'ask-help-' + run.id}>Ask for an explanation or discuss options. This does not change requirements or resume work.</p>
        <button class="studio-button studio-button--secondary" type="submit" disabled={!available || !enabled || (pending && !questionRequest) || !question.trim()}>{asking ? 'Asking agent…' : 'Ask agent'}</button>
      </div>
    </form>
    <form onsubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label for={'requirements-' + run.id}>Requirements to submit</label>
      <textarea id={'requirements-' + run.id} bind:this={requirementsInput} bind:value={requirements} rows="5" maxlength="4000"
        disabled={!canAct || asking || submitting} placeholder="Once you have decided, describe how you want this to work…" aria-describedby={'requirements-help-' + run.id}></textarea>
      <div class="action"><p id={'requirements-help-' + run.id}>Adds requirements to Team input and updates the scope. Then choose Resume workflow to restart from Ideation and review a new plan.</p>
        <button class="studio-button studio-button--primary" type="submit" disabled={!available || !requirements.trim()}>{submitting ? 'Submitting…' : 'Submit requirements'}</button>
      </div>
    </form>
    {#if !enabled}<p>Start just dev with an OpenAI key to ask the agent. You can still submit requirements.</p>{/if}
    {#if !author}<p>Join the workspace to ask questions or submit requirements.</p>{/if}
  {:else if !manual}<p>This is a saved stage result. Open the current blocked stage to ask questions or submit requirements.</p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <p class="status" role="status">{status}</p>
</section>

<style>
  .questions { border:1px solid var(--color-content-warning); border-radius:8px; padding:20px; margin-block:20px; color:var(--color-content-primary); min-width:0; }
  h3 { font-size:15px; margin:0 0 16px; } h4 { font-size:13px; margin:0 0 8px; }
  p, li { font-size:13px; line-height:1.7; color:var(--color-content-secondary); overflow-wrap:anywhere; }
  p { white-space:pre-wrap; } ul { padding-inline-start:20px; } li + li { margin-block-start:8px; }
  form, .conversation { border-block-start:1px solid var(--color-border-primary); padding-block-start:20px; margin-block-start:20px; }
  label { display:block; font-size:13px; font-weight:600; margin-block-end:8px; }
  textarea { box-sizing:border-box; width:100%; min-height:100px; resize:vertical; padding:12px; border:1px solid var(--color-border-secondary); border-radius:6px; background:var(--color-surface-primary); color:var(--color-content-primary); font:14px/1.7 var(--sans); }
  textarea:focus-visible { outline:2px solid var(--color-content-brand); outline-offset:3px; } textarea:disabled { opacity:.65; }
  .action { display:flex; align-items:center; gap:16px; justify-content:space-between; margin-block-start:8px; }
  .action p { margin:0; max-width:65ch; font-size:12px; } .studio-button { min-height:44px; flex-shrink:0; }
  article + article { border-block-start:1px solid var(--color-border-primary); padding-block-start:16px; margin-block-start:16px; }
  .suggestion { padding:16px; border:1px solid var(--color-border-primary); border-radius:6px; background:var(--color-surface-secondary); }
  .error { color:var(--color-content-danger); } .status:empty { display:none; }
  @media(max-width:800px) { .action { align-items:start; flex-direction:column; } .questions { padding:16px; } }
</style>
