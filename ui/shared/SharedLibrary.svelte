<script lang="ts">
  import { request } from './platform';
  let { featureId, runs, changes }: { featureId: string; runs: { id: string; stage: string; input_revision: number }[]; changes: number } = $props();
  type FileEntry = { path: string; hash: string; bytes: number };
  let runId = $state(''), path = $state(''), files = $state<FileEntry[]>([]);
  let displayedKey = '';
  const selectedHash = $derived(files.find(file => file.path === path)?.hash);
  let content = $state(''), error = $state(''), loading = $state(false);
  $effect(() => {
    const id = featureId, run = runId; changes;
    let active = true;
    void request(`/work/${id}/library${run ? '?run_id=' + encodeURIComponent(run) : ''}`)
      .then(result => { if (active) { files = result.files; error = ''; } })
      .catch(cause => { if (active) error = cause.message; });
    return () => { active = false; };
  });
  $effect(() => {
    const id = featureId, run = runId, selected = path; void selectedHash;
    let active = true;
    const key = `${id}:${run}:${selected}`;
    if (key !== displayedKey) content = '';
    error = '';
    if (selected) {
      loading = true;
      void request(`/work/${id}/library/file?path=${encodeURIComponent(selected)}${run ? '&run_id=' + encodeURIComponent(run) : ''}`)
        .then(result => { if (active) { content = result.content; displayedKey = key; } })
        .catch(cause => { if (active) error = cause.message; })
        .finally(() => { if (active) loading = false; });
    } else loading = false;
    return () => { active = false; };
  });
</script>

<section aria-label="Skills and artifacts">
  <h3>Skills &amp; artifacts</h3>
  <p>Read-only playbooks and saved stage documents. Skills come from the stage’s saved instructions. Each artifact path identifies its scope, stage, and run.</p>
  <label>Browse<select bind:value={runId} onchange={() => { path = ''; }}><option value="">All saved versions</option>{#each runs as run}<option value={run.id}>{run.stage} · scope {run.input_revision} · {run.id.slice(0,8)} · frozen inputs</option>{/each}</select></label>
  {#if runId}<p>These are the exact inputs available when this run started. Its output appears under All saved versions.</p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <div class="browser">
    <nav aria-label="Shared files">
      {#each ['/skills/', '/artifacts/'] as prefix}
        <strong>{prefix}</strong>
        {#each files.filter(file => file.path.startsWith(prefix)) as file (file.path)}
          <button class:active={path === file.path} onclick={() => { path = file.path; }} title={file.path}>{file.path.slice(prefix.length)}<small>{file.bytes.toLocaleString()} bytes</small></button>
        {:else}<p>No saved files yet.</p>{/each}
      {/each}
    </nav>
    <article aria-label="Shared file contents">
      {#if path}<strong>{path}</strong>{#if loading && !content}<p>Loading…</p>{:else}<pre>{content}</pre>{/if}
      {:else}<p>Choose a playbook or artifact to read it. Documents are published automatically when an agent saves its structured stage result.</p>{/if}
    </article>
  </div>
</section>

<style>
  section { min-width:0; } p { color:var(--muted); font-size:12px; line-height:1.7; } label { display:flex; align-items:center; gap:12px; font-size:12px; margin:18px 0; }
  select { max-width:100%; background:var(--panel); color:var(--ink); border:1px solid var(--line); padding:8px; border-radius:6px; }
  .browser { display:grid; grid-template-columns:minmax(180px, 30%) minmax(0,1fr); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  nav { padding:12px; border-right:1px solid var(--line); max-height:600px; overflow:auto; } nav>strong { display:block; font-size:12px; margin:10px 0; }
  button { display:block; width:100%; text-align:left; padding:10px; border:0; background:transparent; border-radius:5px; color:var(--muted); font-size:11px; overflow-wrap:anywhere; } button.active { background:var(--accent-soft); color:var(--accent); } small { display:block; margin-top:5px; }
  article { padding:18px; min-width:0; max-height:600px; overflow:auto; } article>strong { font-size:12px; overflow-wrap:anywhere; } pre { white-space:pre-wrap; overflow-wrap:anywhere; font:12px/1.8 var(--mono); }
  .error { color:var(--bad); } @media(max-width:700px) { .browser { grid-template-columns:minmax(0,1fr); } nav { max-height:240px; border-right:0; border-bottom:1px solid var(--line); } }
</style>
