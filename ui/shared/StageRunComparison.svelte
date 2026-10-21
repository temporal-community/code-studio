<script lang="ts">
  import { compareStageResults, type EntryChange, type FieldComparison } from './stageRunComparison';
  let { runA, runB, stageName }: { runA: any; runB: any; stageName: string } = $props();
  const fields = $derived(compareStageResults(runA?.result, runB?.result));
  const labels: Record<string, string> = { unchanged: 'Unchanged', changed: 'Changed', added: 'Added', removed: 'Removed', absent: 'Absent in both runs' };
  function time(value: unknown) {
    if (typeof value !== 'number') return '';
    return new Date(value * 1000).toLocaleString();
  }
  function describe(run: any) {
    const version = run.project?.version;
    return [
      `Run ${run.id}`,
      `${stageName} · ${run.result?.verdict || run.status || 'Status unavailable'}`,
      typeof version === 'string' ? `Project version ${version}` : '',
      run.author ? `Author ${run.author}` : '',
      time(run.created_at) ? `Created ${time(run.created_at)}` : '',
    ].filter(Boolean);
  }
  function valueText(value: any): string {
    if (typeof value === 'string') return value || 'Empty text';
    if (value === null) return 'None';
    return String(value);
  }
  function entryText(entry: EntryChange, side: 'before' | 'after', field: FieldComparison): string {
    const value = side === 'before' ? entry.before : entry.after;
    if (value === undefined) return 'No matching entry in this run.';
    if (typeof value === 'string') return value || 'Empty text';
    if (field.key === 'deliverables') return `${value?.title ?? 'Untitled'}${value?.body === '' ? ' · Empty body' : value?.body ? `
${value.body}` : ''}`;
    if (field.key === 'findings') return `${value?.severity ?? 'Severity unavailable'} · ${value?.title ?? 'Untitled'}
${value?.path ?? 'Path unavailable'}${value?.line == null ? '' : `:${value.line}`}
${value?.explanation || 'Empty explanation'}`;
    return String(value);
  }
  function sideValue(field: FieldComparison, side: 'left' | 'right') {
    const present = side === 'left' ? field.leftPresent : field.rightPresent;
    const value = side === 'left' ? field.leftValue : field.rightValue;
    if (!present) return 'Absent in this run';
    if (field.key === 'summary' || field.key === 'handoff') return valueText(value);
    if (Array.isArray(value) && value.length === 0) return 'Empty list';
    return '';
  }
  function sideRunMeta(run: any) { return run.result == null ? 'No saved result' : ''; }
</script>

<div class="comparison" aria-label="Recorded stage output comparison">
  <div class="run-headings">
    {#each [runA, runB] as current, index (current.id)}
      <section class="run-card" aria-label={`Run ${index === 0 ? 'A' : 'B'} details`}>
        <h3>Run {index === 0 ? 'A' : 'B'}</h3>
        {#each describe(current) as line}<p>{line}</p>{/each}
        {#if sideRunMeta(current)}<p class="run-empty">{sideRunMeta(current)}</p>{/if}
      </section>
    {/each}
  </div>
  {#each fields as field (field.key)}
    <section class="field" aria-labelledby={`compare-${field.key}`}>
      <header><h3 id={`compare-${field.key}`}>{field.label}</h3><span class="change-label">{labels[field.kind]}</span></header>
      {#if field.key === 'summary' || field.key === 'handoff'}
        <div class="sides">
          <div class="side"><strong>Run A</strong><p>{sideValue(field, 'left')}</p></div>
          <div class="side"><strong>Run B</strong><p>{sideValue(field, 'right')}</p></div>
        </div>
      {:else if !field.leftPresent && !field.rightPresent}
        <p class="muted">Absent in both runs.</p>
      {:else if (field.leftPresent && Array.isArray(field.leftValue) && field.leftValue.length === 0) || (field.rightPresent && Array.isArray(field.rightValue) && field.rightValue.length === 0)}
        <p class="muted">{field.leftPresent && Array.isArray(field.leftValue) && field.leftValue.length === 0 ? 'Run A: Empty list.' : 'Run A: Absent in this run.'} {field.rightPresent && Array.isArray(field.rightValue) && field.rightValue.length === 0 ? 'Run B: Empty list.' : 'Run B: Absent in this run.'}</p>
        {#if field.entries?.length}{@render entryList(field)}{/if}
      {:else if field.entries?.length}
        {@render entryList(field)}
      {:else}
        <p class="muted">No entries recorded.</p>
      {/if}
    </section>
  {/each}
</div>

{#snippet entryList(field: FieldComparison)}
  <div class="entries">
    {#each field.entries || [] as entry, i (`${field.key}-${i}-${entry.kind}`)}
      <article class="entry">
        <span class="change-label">{labels[entry.kind]}</span>
        <div class="sides">
          <div class="side"><strong>Run A</strong><p>{entryText(entry, 'before', field)}</p></div>
          <div class="side"><strong>Run B</strong><p>{entryText(entry, 'after', field)}</p></div>
        </div>
        {#if entry.changes?.length}
          <ul>{#each entry.changes as change}<li><strong>{change.field} changed</strong><div>Before: {valueText(change.before)}</div><div>After: {valueText(change.after)}</div></li>{/each}</ul>
        {/if}
      </article>
    {/each}
  </div>
{/snippet}

<style>
  .comparison { display:grid; gap:16px; min-width:0; }
  .run-headings,.sides { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; min-width:0; }
  .run-card,.field,.entry { min-width:0; border:1px solid var(--color-border-primary); border-radius:8px; background:var(--color-surface-primary); padding:14px; }
  .run-card h3,.field h3 { margin:0; color:var(--color-content-primary); }
  .run-card p,.side p { margin:7px 0 0; white-space:pre-wrap; overflow-wrap:anywhere; color:var(--color-content-secondary); font-size:12px; line-height:1.75; }
  .run-card p:first-of-type { color:var(--color-content-primary); }
  .run-empty { font-weight:600; }
  .field { background:var(--color-surface-secondary); }
  .field>header { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px; }
  .change-label { flex:none; font-size:10px; color:var(--color-content-secondary); border:1px solid var(--color-border-secondary); border-radius:999px; padding:3px 8px; }
  .side { min-width:0; padding:10px; border:1px solid var(--color-border-primary); border-radius:6px; background:var(--color-surface-primary); }
  .side>strong { font-size:10px; color:var(--color-content-secondary); }
  .side p { margin-top:6px; }
  .entries { display:grid; gap:10px; }
  .entry { background:var(--color-surface-primary); }
  .entry>.change-label { display:inline-block; margin-bottom:10px; }
  .entry ul { padding-left:20px; margin-bottom:0; }
  .entry li { margin-top:8px; overflow-wrap:anywhere; }
  .entry li div { color:var(--color-content-secondary); white-space:pre-wrap; overflow-wrap:anywhere; margin-top:4px; }
  .muted { color:var(--color-content-secondary); font-size:12px; }
  @media(max-width:650px) { .run-headings,.sides { grid-template-columns:minmax(0,1fr); } }
</style>
