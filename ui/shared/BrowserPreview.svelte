<script lang="ts">
  import { onMount } from 'svelte';
  import { request } from './platform';

  let { featureId }: { featureId: string } = $props();
  let preview = $state<any>(null), busy = $state(false), error = $state('');
  let disposed = false, generation = 0;
  async function refresh() {
    const current = ++generation;
    try { const next = await request(`/work/${featureId}/browser`); if (!disposed && current === generation) preview = next; }
    catch (cause) { if (!disposed && current === generation) error = (cause as Error).message; }
  }
  async function act(action: 'start' | 'stop') {
    if (busy) return;
    generation++;
    busy = true; error = '';
    try { const next = await request(`/work/${featureId}/browser/${action}`, {}); if (!disposed) preview = next; }
    catch (cause) { if (!disposed) error = (cause as Error).message; }
    finally { if (!disposed) busy = false; }
  }
  onMount(() => {
    void refresh(); const timer = setInterval(() => { if (!busy) void refresh(); }, 10000);
    return () => { disposed = true; clearInterval(timer); };
  });
</script>

<section class="browser-preview" aria-labelledby="preview-title">
  <h3 id="preview-title">Read-only browser preview</h3>
  <p>View the proposed frontend with live backend data before merging. Navigation is available; saving, submissions, and agent actions are blocked.</p>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if !preview}<p role="status">{error ? 'Preview status unavailable.' : 'Loading preview status…'}</p>
  {:else if !preview.available}<p>This project needs a preview adapter. Code Studio’s Vite frontend is supported.</p>
  {:else}
    <div class="actions">
      <button class="studio-button studio-button--primary" disabled={busy} onclick={() => void act('start')}>{busy ? 'Working…' : preview.url ? 'Refresh preview' : 'Build preview'}</button>
      {#if preview.url}
        <a class="studio-button studio-button--secondary" href={preview.url} target="_blank" rel="noopener noreferrer">Open preview ↗</a>
        <button class="studio-button studio-button--quiet" disabled={busy} onclick={() => void act('stop')}>Stop preview</button>
      {/if}
    </div>
    {#if busy}<p role="status">Preparing the preview. Building can take up to two minutes.</p>{/if}
    {#if preview.url}<p class="metadata">Source {preview.source_hash.slice(0, 12)} · Local to the Code Studio host · Expires after 30 minutes</p>{/if}
    {#if preview.stale}<p class="warning" role="status">Source or feature scope has changed. Refresh the preview to view the latest code.</p>{/if}
  {/if}
  <h4>Agent screenshots</h4>
  <p>Development and Review can capture pages here. Screenshots verify appearance and navigation; they do not verify saving or new backend behavior.</p>
  {#each preview?.evidence || [] as item (item.id)}
    <article>
      <div class="evidence-heading"><strong>{item.path}</strong><span>{item.stale ? 'Earlier source or scope' : 'Current source and scope'}</span></div>
      <p class="metadata">{item.request.width} × {item.request.height} · {new Date(item.created_at * 1000).toLocaleString()} · Run {item.run_id} · Source {item.source_hash.slice(0, 12)}</p>
      <a href={item.screenshot} target="_blank" rel="noopener noreferrer" aria-label={`Open screenshot of ${item.path}`}><img src={item.screenshot} alt={`Read-only preview of ${item.path} at ${item.request.width} pixels wide`} loading="lazy" /></a>
      {#if item.errors.length}<details><summary>Browser errors ({item.errors.length})</summary><pre>{item.errors.join('\n')}</pre></details>{/if}
      <details><summary>Page content and evidence ID</summary><p class="metadata">{item.id}</p><pre>{item.snapshot}</pre></details>
    </article>
  {:else}<p class="empty">No browser screenshots have been recorded for this feature.</p>{/each}
</section>

<style>
  .browser-preview { color:var(--color-content-primary); }
  h3,h4 { margin-block:0 12px; } h4 { margin-block-start:28px; }
  p { color:var(--color-content-secondary); line-height:1.6; overflow-wrap:anywhere; }
  .actions,.evidence-heading { display:flex; flex-wrap:wrap; gap:12px; align-items:center; }
  .actions .studio-button { min-height:44px; }
  .evidence-heading { justify-content:space-between; } .evidence-heading strong { overflow-wrap:anywhere; }
  .metadata { font-size:12px; } .error,.warning { padding:12px; border:1px solid currentColor; border-radius:7px; }
  .error { color:var(--color-content-danger); } .warning { color:var(--color-content-primary); }
  article { padding:16px; margin-block:16px; background:var(--color-surface-primary); border:1px solid var(--color-border-primary); border-radius:8px; }
  img { display:block; max-width:100%; height:auto; margin-block:12px; border:1px solid var(--color-border-primary); }
  summary { cursor:pointer; padding-block:12px; } pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:320px; overflow:auto; font:12px/1.6 var(--mono); }
  a:focus-visible,summary:focus-visible { outline:2px solid var(--color-content-brand); outline-offset:3px; }
  .empty { padding-block:16px; }
</style>
