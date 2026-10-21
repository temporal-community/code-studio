<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import { api, newId } from './features';
  import { highlight } from './python';
  import { applyIfCurrent, detectRemoteChange } from './featureDiff';
  type ChangedFile = { path: string; change: 'modified' | 'added' | 'deleted'; content_available: boolean; diff_available: boolean };
  type FileDiff = { path: string; patch: string; truncated: boolean; content_available: boolean; diff_available: boolean };
  let { featureId, author, changes, disabled = false, onDirty }: { featureId: string; author: string; changes: number; disabled?: boolean; onDirty: (dirty: boolean) => void } = $props();
  let files = $state<ChangedFile[]>([]), selected = $state(''), content = $state(''), original = $state(''), hash = $state('');
  let error = $state(''), notice = $state(''), busy = $state(false), remoteChanged = $state(false), composing = $state(false);
  let showDiff = $state(true), diff = $state<FileDiff | null>(null), listTruncated = $state(false), loading = $state(true);
  let editor = $state<HTMLTextAreaElement>(null!);
  let top = $state(0), left = $state(0);
  const dirty = $derived(content !== original);
  const current = $derived(files.find(file => file.path === selected));
  const tokens = $derived(selected.endsWith('.py') ? highlight(content) : content.split('\n').map(text => [{ cls: '', text }]));
  let sequence = 0, refreshPending = false;
  $effect(() => onDirty(dirty));
  onDestroy(() => { sequence += 1; onDirty(false); });

  async function loadFileList() {
    const seq = ++sequence;
    loading = true; error = '';
    try {
      const result = await api<{ files: ChangedFile[]; truncated: boolean }>(`/${featureId}/diff/files`);
      if (seq !== sequence) return;
      // Keep the draft and selection, but continue checking the saved file's hash.
      if (dirty) {
        notice = 'Changed files were refreshed. Save or reload your draft before selecting another file.';
        const path = selected;
        const savedFile = files.find(file => file.path === path);
        const refreshedFile = result.files.find(file => file.path === path);
        if (showDiff || !savedFile?.content_available) return;
        if (!refreshedFile?.content_available) { remoteChanged = true; return; }
        const currentHash = hash;
        await detectRemoteChange(
          api<{ content: string; hash: string }>(`/${featureId}/file?path=${encodeURIComponent(path)}`),
          currentHash,
          () => seq === sequence && selected === path && dirty && !showDiff,
          () => { remoteChanged = true; },
        );
        return;
      }
      files = result.files; listTruncated = result.truncated;
      if (!files.some(file => file.path === selected)) selected = files[0]?.path || '';
      if (!selected) { diff = null; content = original = hash = ''; return; }
      await loadSelected(seq);
    } catch (cause) { if (seq === sequence) error = cause instanceof Error ? cause.message : String(cause); }
    finally { if (seq === sequence) loading = false; }
  }
  async function loadSelected(seq = ++sequence) {
    const path = selected, file = files.find(item => item.path === path);
    if (!path || !file || seq !== sequence) return;
    if (showDiff) {
      if (diff?.path !== path) diff = null;
      if (file.diff_available) {
        const applied = await applyIfCurrent(
          api<FileDiff>(`/${featureId}/diff/file?path=${encodeURIComponent(path)}`),
          () => seq === sequence && selected === path,
          result => { diff = result; },
        );
        if (!applied) return;
      } else {
        if (seq !== sequence || selected !== path) return;
        diff = { path, patch: '', truncated: false, content_available: file.content_available, diff_available: false };
      }
      return;
    }
    if (!file.content_available) { showDiff = true; await loadSelected(seq); return; }
    const result = await api<{ content: string; hash: string }>(`/${featureId}/file?path=${encodeURIComponent(path)}`);
    if (seq !== sequence || selected !== path) return;
    if (result.hash !== hash && dirty) { remoteChanged = true; return; }
    content = original = result.content; hash = result.hash; remoteChanged = false;

  }
  async function selectFile(path: string) {
    if (dirty || busy || path === selected) return;
    selected = path; showDiff = true; diff = null; content = original = hash = ''; error = ''; notice = '';
    const seq = ++sequence; busy = true;
    try { await loadSelected(seq); }
    catch (cause) { if (seq === sequence && selected === path) error = cause instanceof Error ? cause.message : String(cause); }
    finally { if (seq === sequence) busy = false; }
  }
  async function toggleView() {
    if (!current || busy || (showDiff && !current.content_available)) return;
    showDiff = !showDiff; error = '';
    const seq = ++sequence, path = selected; busy = true;
    try { await loadSelected(seq); }
    catch (cause) { if (seq === sequence && selected === path) error = cause instanceof Error ? cause.message : String(cause); }
    finally { if (seq === sequence) busy = false; }
  }
  async function save() {
    const source = content, path = selected;
    busy = true; error = ''; notice = '';
    try {
      const result = await api<{ hash: string }>(`/${featureId}/file`, { id: newId(), path, expected_hash: hash, content: source, author });
      hash = result.hash; original = source; remoteChanged = false; notice = 'Saved to the shared feature worktree';
      await loadFileList();
    } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { busy = false; }
  }
  async function reloadFile() {
    if (dirty && !confirm('Discard your unsaved draft and reload the latest file?')) return;
    content = original; const seq = ++sequence; busy = true;
    try { await loadSelected(seq); notice = 'Reloaded the latest saved file'; }
    catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { busy = false; }
  }
  async function format() {
    const source = content; busy = true; error = '';
    try {
      const response = await fetch('/workspace/format', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source }) });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Formatting failed');
      if (content !== source) throw new Error('Your draft changed while formatting. Try again.');
      content = result.text; notice = 'Formatted with Ruff. Save to share your changes.';
    } catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
    finally { busy = false; }
  }
  $effect(() => {
    void changes; void featureId;
    untrack(() => { if (busy) refreshPending = true; else void loadFileList(); });
  });
  $effect(() => {
    if (!busy) untrack(() => {
      if (refreshPending) { refreshPending = false; void loadFileList(); }
    });
  });
  function beforeUnload(e: BeforeUnloadEvent) { if (dirty) { e.preventDefault(); e.returnValue = ''; } }
  if (typeof window !== 'undefined') window.addEventListener('beforeunload', beforeUnload);
  onDestroy(() => { if (typeof window !== 'undefined') window.removeEventListener('beforeunload', beforeUnload); });
</script>

{#if dirty}<p class="notice">You have an unsaved draft. Save it or reload the file before switching files.</p>{/if}
{#if remoteChanged}<p class="warning">This file changed in another session. Your draft is preserved. Copy your changes before reloading the latest file.</p>{/if}
{#if error}<p class="warning" role="alert">{error} <button onclick={() => void loadFileList()}>Retry</button></p>{/if}
{#if notice}<p class="notice" role="status">{notice}</p>{/if}
<div class="browser">
  <aside class="changed-panel" aria-label="Changed files">
    <div class="panel-title"><strong>Changed files</strong><button aria-label="Refresh changed files" onclick={() => void loadFileList()} disabled={loading || busy}>↻</button></div>
    {#if listTruncated}<p class="fine">File list capped; some changes may not be shown.</p>{/if}
    {#if loading && !files.length}<p class="fine">Loading changed files…</p>
    {:else if error && !files.length}<p class="warning">Changes could not be loaded.</p>
    {:else if !files.length}<p class="fine">No changed files</p>
    {:else}<ul>{#each files as file (file.path)}<li><button class:selected={file.path === selected} aria-current={file.path === selected ? 'true' : undefined} disabled={dirty || busy} onclick={() => void selectFile(file.path)}><span class="path">{file.path}</span><small class:deleted={file.change === 'deleted'}>{file.change}</small></button></li>{/each}</ul>{/if}
  </aside>
  <section class="file-view" aria-label="Selected file">
    {#if selected && current}
      <div class="filebar"><strong>{selected}</strong><div><button onclick={() => void toggleView()} disabled={busy || (showDiff && !current.content_available)}>{showDiff ? 'Full file' : 'Show diff'}</button>{#if !showDiff}<button disabled={busy} onclick={() => void reloadFile()}>Reload file</button><button class="primary" disabled={!dirty || disabled || busy} onclick={() => void save()}>Save file</button>{/if}</div></div>
      {#if showDiff}
        {#if !current.diff_available}<p class="notice">A text diff is unavailable for this file.</p>
        {:else if !diff}<p class="fine">Loading diff…</p>
        {:else if !diff.diff_available}<p class="notice">A text diff is unavailable for this file.</p>
        {:else}<div class="diff"><p>{current.change} · changes from the feature base, including uncommitted edits.</p>{#if diff.truncated}<p class="warning">This file diff is truncated at 120 KB.</p>{/if}<pre>{diff.patch || 'No textual changes to display.'}</pre></div>{/if}
      {:else}
        <div class="editor" class:composing>
          <div class="highlight" aria-hidden="true"><pre style:transform={`translate(${-left}px, ${-top}px)`}>{#each tokens as line}<span class="code-line">{#each line as token}<span class={token.cls}>{token.text}</span>{/each}</span>{/each}</pre></div>
          <textarea bind:this={editor} bind:value={content} aria-label={`${selected} feature editor`} spellcheck="false" wrap="off" autocapitalize="off" autocomplete="off" disabled={disabled || busy}
            onscroll={() => { top = editor.scrollTop; left = editor.scrollLeft; }} oncompositionstart={() => composing = true} oncompositionend={() => composing = false}
            onkeydown={e => { if (e.key === 'Tab') { e.preventDefault(); const a = editor.selectionStart, b = editor.selectionEnd; content = content.slice(0, a) + '    ' + content.slice(b); queueMicrotask(() => editor.setSelectionRange(a + 4, a + 4)); } }}></textarea>
        </div>
        <div class="filebar"><span>{dirty ? 'Unsaved draft' : 'Saved'} · UTF-8</span><div>{#if selected.endsWith('.py')}<button disabled={disabled || busy || content.length > 20_000} onclick={() => void format()}>Format Python</button>{/if}</div></div>
      {/if}
    {:else if error}<p class="warning">Changes could not be loaded. Retry before reviewing or integrating this feature.</p>
    {:else if !loading && !files.length}<p class="notice">No changed files to review.</p>
    {:else}<p class="fine">Select a changed file to review its diff.</p>{/if}
  </section>
</div>

<style>
  .browser { display:grid; grid-template-columns:minmax(150px, 230px) minmax(0,1fr); gap:16px; min-height:300px; margin-top:12px; }.changed-panel { border:1px solid var(--line); border-radius:8px; background:var(--panel); align-self:start; overflow:hidden; }.panel-title { display:flex; align-items:center; justify-content:space-between; padding:9px 10px; border-bottom:1px solid var(--line); font-size:11px; }.panel-title button { padding:3px 7px; }.changed-panel ul { list-style:none; padding:4px; margin:0; max-height:460px; overflow:auto; }.changed-panel li button { width:100%; display:flex; align-items:center; justify-content:space-between; gap:5px; border-color:transparent; background:transparent; text-align:left; padding:7px 6px; }.changed-panel li button.selected { background:var(--accent-soft); border-color:var(--line-2); }.path { overflow-wrap:anywhere; font:10px var(--mono); }.changed-panel small { flex:none; color:var(--muted); font-size:9px; }.changed-panel small.deleted { color:var(--warn); }.file-view { min-width:0; }.filebar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 0 12px; font-size:11px; }.filebar>div { display:flex; gap:6px; flex-wrap:wrap; }.filebar button,.panel-title button { border:1px solid var(--line-2); background:var(--panel-2); border-radius:6px; padding:7px 9px; }.filebar button:disabled,.panel-title button:disabled { opacity:.45; }.primary { background:var(--accent)!important; color:var(--accent-ink); }.notice,.fine,.diff>p { color:var(--muted); font-size:11px; }.warning { color:var(--warn); font-size:11px; white-space:pre-wrap; }.diff pre { overflow:auto; max-height:550px; padding:16px; background:var(--bg); font:11px/1.7 var(--mono); white-space:pre; }.editor { position:relative; height:460px; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:var(--bg); }.highlight { position:absolute; inset:0; pointer-events:none; overflow:hidden; }.highlight pre,.editor textarea { margin:0; padding:16px 20px 50px; font:12px/22px var(--mono); tab-size:4; white-space:pre; letter-spacing:normal; font-variant-ligatures:none; }.highlight pre { width:max-content; min-width:100%; }.code-line { display:block; min-height:22px; }.editor textarea { position:absolute; inset:0; width:100%; height:100%; resize:none; border:0; background:transparent; color:transparent; caret-color:var(--ink); outline:none; }.editor textarea::selection { background:color-mix(in srgb,var(--accent) 25%,transparent); color:transparent; }.composing .highlight { visibility:hidden; }.composing textarea { color:var(--ink); }.keyword { color:var(--syn-keyword); }.string { color:var(--syn-string); }.comment,.doc { color:var(--syn-comment); }.number { color:var(--syn-number); }.fn { color:var(--syn-fn); }.builtin { color:var(--syn-builtin); }.host { color:var(--syn-host); }.op { color:var(--syn-op); }.const { color:var(--syn-const); }
  @media(max-width:700px) { .browser { grid-template-columns:minmax(110px, 1fr) minmax(0, 2fr); gap:8px; }.filebar { align-items:flex-start; }.filebar>strong { overflow-wrap:anywhere; } }
  @media(forced-colors:active) { .highlight { display:none; }.editor textarea { color:CanvasText; } }
</style>
