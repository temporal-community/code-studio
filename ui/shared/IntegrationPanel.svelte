<script lang="ts">
  import { onMount } from 'svelte';
  import { request, uid } from './platform';
  import CopyOutput from './CopyOutput.svelte';
  let { featureId, revision, author, canAct, onchanged }: { featureId: string; revision: number; author: string; canAct: boolean; onchanged: () => void } = $props();
  let record = $state<any>(null), error = $state(''), busy = $state(false), loading = $state(true);
  let now = $state(Date.now()), agentReply = $state('');
  let disposed = false, fetching = false, generation = 0;
  const working = $derived(busy || !!record?.operation_active);
  const interrupted = $derived(record?.status === 'validating' && !record.operation_active && !busy);
  const checks = $derived(Object.entries(record?.checks || {}) as [string, any][]);
  const finished = $derived(checks.filter(([, check]) => ['passed', 'not_available'].includes(check.status)).length);
  const failed = $derived(checks.filter(([, check]) => ['failed', 'timed_out', 'stale'].includes(check.status)));
  const heading = $derived(record?.status === 'applied' ? 'Merged into your project' : record?.status === 'conflicts' ? (working ? 'Agent is resolving conflicts…' : 'Merge conflicts found') : ['applying', 'recovery_required'].includes(record?.status) ? (record?.operation_active ? 'Finishing merge…' : 'Restore interrupted merge') : interrupted ? 'Merge checks were interrupted' : working ? (record?.status === 'validating' ? 'Checking before merge…' : 'Merging into your project…') : record?.status === 'failed' || record?.error ? 'Merge needs attention' : 'Ready to merge');
  const checkNames: Record<string, string> = { syntax: 'Python syntax', python_tests: 'Python tests', browser_tests: 'App tests', types: 'Type checks', build: 'Production build' };
  function duration(check: any) {
    const seconds = Math.max(0, Math.floor(((check.finished_at ? check.finished_at * 1000 : now) - check.started_at * 1000) / 1000));
    return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  }
  function checkStatus(check: any) {
    if (check.cached && check.status === 'passed') return 'Passed · reused';
    return ({ passed:'Passed', running:'Running', queued:'Waiting', not_available:'Not configured', failed:'Failed', timed_out:'Timed out', stale:'Files changed', interrupted:'Interrupted', not_run:'Not run' } as Record<string, string>)[check.status] || check.status;
  }
  async function load() {
    if (fetching) return;
    fetching = true;
    const current = generation;
    try {
      const next = await request(`/work/${featureId}/integration`);
      if (disposed || current !== generation) return;
      const applied = next?.status === 'applied' && record?.status !== 'applied';
      record = next;
      if (applied) onchanged();
    } catch (e) { if (!disposed) error = (e as Error).message; }
    finally { fetching = false; if (!disposed) loading = false; }
  }
  onMount(() => {
    void load();
    const timer = setInterval(() => {
      now = Date.now();
      if (document.visibilityState === 'visible' && (working || record?.status === 'validating')) void load();
    }, 1500);
    const catchUp = () => { if (document.visibilityState === 'visible') void load(); };
    document.addEventListener('visibilitychange', catchUp);
    return () => { disposed = true; clearInterval(timer); document.removeEventListener('visibilitychange', catchUp); };
  });
  async function act(action: string) {
    generation += 1; busy = true; error = '';
    try {
      const next = await request(`/work/${featureId}/integration/${action}`, { id:uid(), author, revision, note: action === 'resolve-agent' ? agentReply.trim() : '' });
      generation += 1;
      if (!disposed) record = next;
      onchanged();
    } catch (e) { error = (e as Error).message; await load(); }
    finally { busy = false; }
  }
</script>

<section class="integration" aria-label="Merge into project" aria-busy={working}>
  <div class="heading"><span class="status-icon" class:done={record?.status === 'applied'} aria-hidden="true">{record?.status === 'applied' ? '✓' : record?.status === 'conflicts' ? '!' : '↳'}</span><h3 aria-live="polite">{heading}</h3></div>
  {#if error || record?.error || record?.agent_resolution?.status === 'blocked'}<p class="warning" role="alert">{error || record?.error || record.agent_resolution.summary}</p>{/if}
  {#if loading}<p role="status">Loading merge status…</p>
  {:else if record?.status === 'applied'}
    <p>Your feature is in your local project. You can now commit and deploy it using your usual workflow.</p>
    <p class="success">{record.changes.length} file changes merged{record.apply_author ? ` by ${record.apply_author}` : ''}. A backup is saved.</p>
  {:else if record?.status === 'conflicts'}
    {#if working}
      <p role="status">Resolving <code>{record.agent_resolution?.path || 'conflicting files'}</code>. Checks and merging follow automatically.</p>
    {:else}
      <p>The agent can combine changes in {record.conflicts.length} {record.conflicts.length === 1 ? 'file' : 'files'}, run checks, and merge the result. Your project files haven’t changed.</p>
      <label class="agent-reply">Your answer or instructions for the merge agent
        <textarea bind:value={agentReply} rows="3" maxlength="4000" disabled={!canAct || working} placeholder="For example: Keep the local experiment completion panel and add the feature’s approval inbox."></textarea>
      </label>
      <button class="primary" disabled={!canAct || working} onclick={() => void act('resolve-agent')}>{agentReply.trim() ? 'Send answer and retry merge' : record.agent_resolution ? 'Retry agent resolution' : 'Resolve with agent and merge'}</button>
    {/if}
  {:else if ['applying','recovery_required'].includes(record?.status) && !record?.operation_active}
    <p>Restore the saved files before trying again. Later local edits will be preserved.</p>
    {#if record.rollback_conflicts?.length}<p class="warning">These files have newer edits: {record.rollback_conflicts.join(', ')}. Save or reconcile them before restoring.</p>{/if}
    <button class="primary" disabled={!canAct || working} onclick={() => void act('rollback')}>Restore previous files</button>
  {:else if working}
    <p role="status">{record?.status === 'validating' ? `${finished} of ${checks.length} checks complete. ${record.auto_merge ? 'Changes merge automatically when they pass.' : 'When they pass, choose Merge into project.'}` : 'Combining your feature with the latest local files.'}</p>
  {:else}
    <p>{interrupted ? 'Your project files haven’t changed. Resume to finish the checks and merge.' : failed.length ? 'Fix the failed checks below, then retry. Your project files haven’t changed.' : 'Bring this feature into your local project. We’ll combine local edits, check the result, and merge it.'}</p>
    <button class="primary" disabled={!canAct || working} onclick={() => void act('merge')}>{interrupted ? 'Resume merge' : record?.status === 'failed' || record?.error ? 'Retry merge' : 'Merge into project'}</button>
    {#if !failed.length && !interrupted}<p class="hint">No commit or remote deployment. You can undo the merge.</p>{/if}
  {/if}
  {#if record?.auto_merged?.length && record.status !== 'applied'}<p class="hint">Local edits combined automatically in {record.auto_merged.length} {record.auto_merged.length === 1 ? 'file' : 'files'}.</p>{/if}

  {#if checks.length}
    <div class="checks" aria-label="Merge checks">
      {#each checks as [name, check]}
        <details class:failed={['failed','timed_out','stale'].includes(check.status)}>
          <summary><span>{checkNames[name] || name}</span><span class="check-state">{checkStatus(check)}{#if check.started_at && !check.cached} · {duration(check)}{/if}</span></summary>
          {#if check.status === 'running'}<p>Still running. This view updates automatically.{#if check.timeout_seconds} Timeout: {Math.ceil(check.timeout_seconds / 60)} minutes.{/if}</p>{/if}
          {#if check.output}<div class="check-output-row"><pre>{check.output}</pre><CopyOutput output={check.output} /></div>{/if}
        </details>
      {/each}
    </div>
  {/if}
  {#if record?.id}
    <details class="more"><summary>Changes and merge details{record.changes.length ? ` (${record.changes.length} files)` : ''}</summary>
      <p><code>{record.source_directory}</code></p>
      {#if record.already_present?.length}<p>{record.already_present.length} files already contain the feature changes.</p>{/if}
      {#if record.agent_resolutions?.length}<ul>{#each record.agent_resolutions as item}<li><code>{item.path}</code>: {item.summary}</li>{/each}</ul>{/if}
      {#if record.conflicts?.length}<p>Unresolved: {record.conflicts.map((item: any) => item.path).join(', ')}</p>{/if}
      {#if record.resolutions?.length}<p>{record.resolutions.length} conflicts resolved for this merge.</p>{/if}
      {#if record.manual_overrides?.length}<p>{record.manual_overrides.length} earlier checks were manually approved. Merge checks still run independently.</p>{/if}
      <ul>{#each record.changes as item}<li>{item.kind} · <code>{item.path}</code></li>{/each}</ul>
      {#if record.diff?.truncated}<p>The diff is shortened. Checks cover the complete files.</p>{/if}
      <pre>{record.diff?.patch || 'No text differences.'}</pre>
      {#if record.status === 'applied'}
        <p>Merged {new Date(record.applied_at * 1000).toLocaleString()} · Receipt <code>{record.id}</code></p>
        {#if record.git_receipt?.status === 'committed'}<p>Committed in Git: <code>{record.git_receipt.head}</code></p>{/if}
        <p>Undo restores the files from before this merge and preserves later edits. It doesn’t undo Git commits.</p>
        <button disabled={!canAct || working} onclick={() => void act('rollback')}>Undo merge</button>
      {:else}<button disabled={!canAct || working} onclick={() => void act('prepare')}>Refresh from latest files</button>{/if}
    </details>
  {/if}
</section>

<style>
  .integration { font-size:13px; line-height:1.6; }
  .heading { display:flex; align-items:center; gap:10px; }
  .status-icon { display:grid; place-items:center; width:28px; height:28px; border-radius:50%; background:var(--accent-soft); color:var(--accent); font-size:18px; }
  .status-icon.done { color:var(--good); background:color-mix(in srgb,var(--good) 12%,transparent); }
  h3 { font-size:17px; margin:0; }
  p { color:var(--muted); overflow-wrap:anywhere; margin:12px 0; }
  code { overflow-wrap:anywhere; font-size:11px; } ul { padding-left:18px; }
  button { min-height:44px; padding:10px 16px; border:1px solid var(--line-2); border-radius:7px; background:var(--panel-2); color:var(--ink); font:inherit; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; } button:focus-visible, summary:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
  .agent-reply { display:block; margin:16px 0; color:var(--ink); }
  .agent-reply textarea { display:block; box-sizing:border-box; width:100%; margin-top:8px; padding:12px; border:1px solid var(--line-2); border-radius:7px; background:var(--panel-2); color:var(--ink); font:inherit; resize:vertical; }
  .agent-reply textarea:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
  .primary { background:var(--accent); color:var(--accent-ink); border-color:transparent; font-weight:600; }
  .warning,.failed .check-state { color:var(--warn); } .success { color:var(--good); } .hint { font-size:12px; }
  .checks { margin-top:18px; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  .checks details+details { border-top:1px solid var(--line); } .checks summary { display:flex; justify-content:space-between; gap:12px; padding:10px 12px; min-height:44px; }
  .check-state { color:var(--muted); font-size:12px; text-align:right; } .checks p,.checks pre { margin:8px 12px 12px; }
  .check-output-row { display:flex; align-items:flex-start; gap:10px; flex-wrap:wrap; padding:0 12px 12px; }
  .check-output-row pre { flex:1 1 450px; min-width:0; margin:8px 0 0; }
  .more { margin-top:20px; border-top:1px solid var(--line); padding-top:12px; } summary { cursor:pointer; min-height:44px; align-content:center; }
  pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:300px; overflow:auto; background:var(--panel-2); padding:12px; border-radius:6px; font-size:11px; }
  @media(max-width:600px) { .checks summary { flex-wrap:wrap; gap:4px; } }
</style>
