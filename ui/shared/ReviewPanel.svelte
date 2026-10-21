<script lang="ts">
  import { onMount } from 'svelte';
  let { author, revision, canRun, refreshKey }: { author: string; revision: number; canRun: boolean; refreshKey: number } = $props();
  let runs = $state<any[]>([]), fixtures = $state<any[]>([]), tools = $state<any[]>([]);
  let enabled = $state(false), credential = $state('');
  let source = $state('github'), url = $state('https://github.com/temporalio/ui/pull/3948');
  let fixture = $state('demo-empty-average'), selected = $state(''), detail = $state<any>(null);
  let section = $state('Findings'), error = $state(''), notice = $state(''), busy = $state(false);
  let fixtureName = $state(''), comment = $state(''), finding = $state('');
  let baseline = $state(''), comparison = $state<any>(null);
  let previewTool = $state('get_pull_request'), previewArgument = $state(''), preview = $state('');
  let showTools = $state(false), alive = false, generation = 0;
  let timeout: ReturnType<typeof setTimeout>;
  const finished = (run: any) => ['completed', 'failed', 'cancelled'].includes(run?.status);
  const short = (value: string | undefined) => value?.slice(0, 8) || '—';
  const link = (value: string | undefined) => { try { return value && new URL(value).protocol === 'https:' ? value : undefined; } catch { return undefined; } };
  const cases = $derived(detail?.evidence?.tests?.cases || []);
  const counts = $derived(['passed', 'failed', 'skipped'].map(status => ({ status, count: cases.filter((c: any) => c.status === status).length })));
  async function api(path: string, body?: unknown): Promise<any> {
    const response = await fetch('/workspace/' + path, body === undefined ? { signal: AbortSignal.timeout(15000) } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(30000),
    });
    if (response.status === 404) throw new Error('Restart just dev to load the SDLC workspace service and worker.');
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The workspace request failed. Check the local host.');
    return data;
  }
  async function refresh(): Promise<void> {
    const request = ++generation;
    try {
      const state = await api('review/state');
      if (!alive || request !== generation) return;
      runs = state.runs; fixtures = state.fixtures; tools = state.tools; enabled = state.enabled; credential = state.credential_source;
      if (!selected && runs.length) selected = runs[0].id;
      const id = selected;
      if (id) {
        const result = await api('review/runs/' + encodeURIComponent(id));
        if (alive && request === generation && id === selected) detail = result;
      }
    } catch (cause) { if (alive && request === generation) error = cause instanceof Error ? cause.message : 'Workspace unavailable'; }
  }
  async function act(action: () => Promise<void>): Promise<void> {
    if (busy) return;
    busy = true; error = ''; notice = '';
    try { await action(); } catch (cause) { error = cause instanceof Error ? cause.message : 'Request failed'; }
    finally { busy = false; }
  }
  async function run(): Promise<void> {
    await act(async () => {
      const result = await api('review/runs', { id: crypto.randomUUID(), author, revision,
        source: source === 'github' ? { kind: 'github', url } : { kind: 'fixture', fixture } });
      selected = result.id; detail = null; comparison = null; section = 'Findings'; await refresh();
    });
  }
  async function select(id: string): Promise<void> {
    selected = id; detail = null; comparison = null; finding = ''; comment = ''; fixtureName = ''; await refresh();
  }
  async function validate(): Promise<void> {
    await act(async () => { const result = await api('project/validate', {}); notice = result.message + ' Revision ' + result.revision + '.'; });
  }
  async function saveFixture(): Promise<void> {
    const id = selected;
    await act(async () => {
      const result = await api('review/runs/' + id + '/fixture', { name: fixtureName.trim() || detail.title });
      fixture = result.id; source = 'fixture'; notice = 'Saved fixture. The next run will use this exact PR evidence.'; await refresh();
    });
  }
  async function addComment(): Promise<void> {
    const id = selected;
    await act(async () => {
      await api('review/runs/' + id + '/comments', { id: crypto.randomUUID(), author, text: comment, finding: finding === '' ? null : Number(finding) });
      if (selected === id) comment = ''; await refresh();
    });
  }
  async function compare(): Promise<void> {
    const id = selected;
    await act(async () => { const result = await api(`review/compare?first=${encodeURIComponent(baseline)}&second=${encodeURIComponent(id)}`); if (selected === id) comparison = result; });
  }
  async function tryTool(): Promise<void> {
    await act(async () => {
      const args = previewTool === 'read_file' ? { path: previewArgument } : previewTool === 'search_repository' ? { query: previewArgument } : {};
      preview = JSON.stringify(await api('review/tools/preview', { fixture, tool: previewTool, arguments: args }), null, 2);
    });
  }
  $effect(() => { refreshKey; if (alive) { clearTimeout(timeout); timeout = setTimeout(() => void refresh(), 200); } });
  onMount(() => {
    alive = true; void refresh();
    const interval = setInterval(() => void refresh(), 5000);
    return () => { alive = false; generation++; clearTimeout(timeout); clearInterval(interval); };
  });
</script>

<div class="review-panel">
  <div class="intro"><div><div class="eyebrow">SHARED PLAYGROUND</div><h1>Build. Review. Improve.</h1><p>Run your agent against a PR, discuss the findings, then compare a new version.</p></div><button onclick={() => showTools = !showTools}>{showTools ? 'Hide tools' : 'Tool catalog'}</button></div>
  <form class="run-form" onsubmit={event => { event.preventDefault(); void run(); }}>
    <div class="source-row"><label>Review source<select aria-label="Review source" bind:value={source}><option value="github">GitHub PR</option><option value="fixture">Saved fixture</option></select></label>
      {#if source === 'github'}<label class="grow">Pull request URL<input aria-label="Pull request URL" type="url" bind:value={url} required /></label>
      {:else}<label class="grow">Fixture<select aria-label="Run fixture" bind:value={fixture}>{#each fixtures as f}<option value={f.id}>{f.name}</option>{/each}</select></label>{/if}
    </div>
    <div class="run-actions"><span>{source === 'github' ? 'GitHub: ' + credential : fixtures.find(f => f.id === fixture)?.synthetic ? 'Synthetic evidence · model review runs live' : 'Captured evidence · model review runs live'}</span><button type="button" onclick={() => void validate()} disabled={!canRun || busy}>Validate agent</button><button class="primary" type="submit" disabled={!canRun || !enabled || busy}>{busy ? 'Working…' : 'Run agent →'}</button></div>
    {#if source === 'fixture'}<p class="hint">Use <code>{fixture}</code> as the fixture ID in <code>evaluations.json</code>.</p>{/if}
    {#if !enabled}<p class="hint">Start the full stack with <code>just dev</code> to run agents.</p>{:else if !canRun}<p class="hint">Waiting for shared edits to finish syncing.</p>{/if}
  </form>
  {#if error}<p class="error" role="alert">{error}<button aria-label="Dismiss review error" onclick={() => error = ''}>×</button></p>{/if}
  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  {#if showTools}
    <section class="tools"><h2>Review tools</h2><p>Enable tools in <code>agent.json</code> and call them from <code>agent.py</code>. Each returns a JSON string.</p>
      {#each tools as tool}<details><summary><code>{tool.name}{tool.signature}</code></summary><p>{tool.description}</p></details>{/each}
      <h3>Try an evidence tool</h3><div class="source-row"><label class="grow">Fixture<select aria-label="Tool preview fixture" bind:value={fixture}>{#each fixtures as f}<option value={f.id}>{f.name}</option>{/each}</select></label><label>Tool<select aria-label="Preview tool" bind:value={previewTool}>{#each tools.filter(t => !['review_changes', 'save_review'].includes(t.name)) as tool}<option>{tool.name}</option>{/each}</select></label></div>
      {#if ['read_file', 'search_repository'].includes(previewTool)}<label>{previewTool === 'read_file' ? 'File path' : 'Search text'}<input bind:value={previewArgument} placeholder={previewTool === 'read_file' ? 'stats.py' : 'average'} /></label>{/if}
      <button onclick={() => void tryTool()} disabled={busy}>Preview tool result</button>{#if preview}<pre class="preview">{preview}</pre>{/if}
    </section>
  {/if}
  <div class="history-title"><h2>Shared runs <small>{runs.length}</small></h2><span>Versioned agent + captured evidence</span></div>
  {#if !runs.length}<div class="empty">Your first review starts here.<p>Use the demo fixture for a repeatable Python regression, or review the linked Temporal UI PR.</p></div>
  {:else}
    <div class="run-list" aria-label="Shared runs">{#each runs as item (item.id)}<button class:selected={selected === item.id} onclick={() => void select(item.id)}><span class="badge" data-status={item.status}>{item.status}</span><strong>{item.title}</strong><small>{item.author} · {short(item.version)}</small></button>{/each}</div>
  {/if}
  {#if detail}
    <section class="run-detail">
      <div class="report-heading"><h2>{detail.title}</h2>{#if !finished(detail)}<button onclick={() => void act(async () => { await api('review/runs/' + selected + '/cancel', {}); await refresh(); })} disabled={busy}>Cancel run</button>{/if}</div>
      <p class="run-stage"><span class="badge" data-status={detail.status}>{detail.status}</span>{detail.stage} · agent {short(detail.version)}{#if detail.duration} · {detail.duration.toFixed(1)}s{/if}{#if detail.usage} · {detail.usage.total_tokens.toLocaleString()} tokens{/if}</p>
      {#if detail.error}<p class="error">{detail.error}</p>{/if}
      {#if detail.evidence}<p class="commit">{detail.evidence.pr.repository} · head <code>{short(detail.evidence.pr.head_sha)}</code> · base <code>{short(detail.evidence.pr.base_sha)}</code>{#if link(detail.evidence.pr.url)} · <a href={link(detail.evidence.pr.url)} target="_blank" rel="noreferrer">Open PR ↗</a>{/if}</p>{/if}
      {#if detail.evaluation}<div class="evaluation"><strong>Evaluation: {detail.evaluation.status.replaceAll('_', ' ')}</strong>{#each detail.evaluation.assertions as assertion}<span data-status={assertion.passed ? 'passed' : 'failed'}>{assertion.passed ? '✓' : '×'} {assertion.name}</span>{/each}</div>{/if}
      <nav aria-label="Run details">{#each ['Findings', 'CI & tests', 'Trace', 'Agent version', 'Discussion', 'Compare'] as tab}<button class:active={section === tab} onclick={() => section = tab}>{tab}{tab === 'Discussion' && detail.comments.length ? ` (${detail.comments.length})` : ''}</button>{/each}</nav>
      {#if section === 'Findings'}
        {#if detail.report}<p class="summary-text">{detail.report.summary}</p>{#if detail.status !== 'completed'}<p class="hint">This report was saved before the run finished. Run status: {detail.status}.</p>{/if}
          {#each detail.report.findings as item, index}<article class="finding"><div><span class="badge" data-status={item.severity}>{item.severity}</span><strong>{index + 1}. {item.title}</strong></div><code>{item.path}:{item.line}</code><p>{item.explanation}</p><p class="suggestion">{item.suggestion}</p><button onclick={() => { section = 'Discussion'; finding = String(index); }}>Discuss finding</button></article>{:else}<p class="empty">No findings in the captured changes.</p>{/each}
        {:else}<p class="empty">{finished(detail) ? 'This run did not save a review report.' : 'The agent is collecting evidence and preparing its review…'}</p>{/if}
        {#if detail.evidence?.warnings?.length}<details class="warnings"><summary>Evidence notes ({detail.evidence.warnings.length})</summary><ul>{#each detail.evidence.warnings as warning}<li>{warning}</li>{/each}</ul></details>{/if}
      {:else if section === 'CI & tests'}
        <h3>Captured CI checks</h3><p class="hint">Statuses below came from GitHub or the selected fixture. This app does not execute repository tests.</p>
        {#each detail.evidence?.checks || [] as check}<div class="check-row"><span class="badge" data-status={check.conclusion || check.status}>{check.conclusion || check.status}</span><strong>{check.name}</strong><code>{short(check.sha)}</code>{#if link(check.url)}<a href={link(check.url)} target="_blank" rel="noreferrer">Details ↗</a>{/if}</div>{:else}<p class="empty">No CI checks were available in this capture.</p>{/each}
        <h3>Individual tests</h3>{#if cases.length}<div class="test-counts">{#each counts as count}<span data-status={count.status}><strong>{count.count}</strong> {count.status}</span>{/each}<small>captured cases</small></div>
          {#each cases as test}<details class="test-case"><summary><span class="badge" data-status={test.status}>{test.status}</span>{test.suite}::{test.name}</summary><small>{test.artifact} · run {test.run_id} · attempt {test.attempt} · {test.duration}s</small>{#if test.message}<pre>{test.message}</pre>{/if}</details>{/each}
        {:else}<p class="empty">Individual test results are unavailable. A successful CI job does not supply individual test outcomes.</p>{/if}
        {#each detail.evidence?.failures || [] as failure}<details class="failure"><summary>Failure details · {failure.job}</summary><p>{failure.steps.join(' · ')}</p><pre>{failure.log}</pre>{#if link(failure.url)}<a href={link(failure.url)} target="_blank" rel="noreferrer">Open job ↗</a>{/if}</details>{/each}
      {:else if section === 'Trace'}
        <p class="hint">Tool calls from this run. Evidence tools use the same captured PR snapshot.</p>{#each detail.trace as step, index}<details><summary>{index + 1}. <code>{step.name}</code> <small>{new Date(step.time * 1000).toLocaleTimeString()}</small></summary><pre>{JSON.stringify(step.input, null, 2)}</pre><pre>{step.output}{step.truncated ? '\n… output truncated in trace' : ''}</pre></details>{:else}<p class="empty">Tool calls will appear when the agent starts.</p>{/each}
      {:else if section === 'Agent version'}
        <p>Agent <code>{short(detail.version)}</code> · workspace revision {detail.revision}</p><p class="hint">These files were frozen when {detail.author} started this run. Current edits apply to future runs.</p>{#each Object.entries(detail.project.files) as [path, content]}<details><summary>{path}</summary><pre>{content}</pre></details>{/each}
      {:else if section === 'Discussion'}
        {#each detail.comments as entry}<article class="comment"><strong>{entry.author}</strong><small>{new Date(entry.created_at * 1000).toLocaleString()}{entry.finding !== null ? ` · Finding ${entry.finding + 1}` : ' · Run discussion'}</small><p>{entry.text}</p></article>{:else}<p class="empty">Discuss findings and decide how to improve the next agent version.</p>{/each}
        <form class="comment-form" onsubmit={event => { event.preventDefault(); void addComment(); }}><label>Discuss<select aria-label="Comment target" bind:value={finding}><option value="">This run</option>{#each detail.report?.findings || [] as item, index}<option value={String(index)}>Finding {index + 1}: {item.title}</option>{/each}</select></label><textarea aria-label="Shared review comment" bind:value={comment} placeholder="Add evidence, ask a question, or suggest an agent change…" maxlength="4000" rows="3" required></textarea><button class="primary" disabled={!canRun || !comment.trim() || busy}>Post as {author}</button></form>
      {:else if section === 'Compare'}
        <p class="hint">Findings are matched by file, line, and title. Reworded findings can appear as added and removed.</p>
        <p>Compare this run with an earlier version.</p><div class="source-row"><select class="grow" aria-label="Comparison baseline" bind:value={baseline}><option value="">Choose a baseline run</option>{#each runs.filter(r => r.id !== selected && r.status === 'completed') as other}<option value={other.id}>{other.title} · {short(other.version)} · {other.author}</option>{/each}</select><button onclick={() => void compare()} disabled={!baseline || !detail.report || busy}>Compare</button></div>
        {#if comparison}<div class="comparison"><p class:notice={comparison.same_evidence} class:error={!comparison.same_evidence}>{comparison.same_evidence ? 'Same PR evidence — agent versions can be compared directly.' : 'Different PR evidence — findings may differ because the PR or CI changed.'}</p><p>{comparison.same_agent ? 'Agent files are identical.' : 'Changed agent files: ' + comparison.changed_files.join(', ')}</p><div class="metrics"><span>{comparison.added.length} added</span><span>{comparison.removed.length} removed</span><span>{comparison.retained.length} retained</span></div>{#each ['first', 'second'] as side}<p>{side === 'first' ? 'Baseline' : 'Selected'} · {short(comparison[side].version)} · evaluation {comparison[side].evaluation?.status || 'not configured'} · {comparison[side].duration?.toFixed(1) || '—'}s · {comparison[side].usage?.total_tokens ?? '—'} tokens</p>{/each}{#each comparison.added as item}<p>+ {item.path}:{item.line} — {item.title}</p>{/each}{#each comparison.removed as item}<p>− {item.path}:{item.line} — {item.title}</p>{/each}</div>{/if}
      {/if}
      {#if detail.evidence}<div class="save-fixture"><div><h3>Keep this evidence for another run</h3><p>Save a fixture, edit the agent, and run it against the same PR and CI snapshot.</p></div><div class="source-row"><input class="grow" aria-label="Fixture name" bind:value={fixtureName} placeholder="Fixture name (optional)" maxlength="100" /><button onclick={() => void saveFixture()} disabled={!canRun || busy}>Save fixture</button></div></div>{/if}
    </section>
  {:else if selected}<p class="empty">Loading run…</p>{/if}
</div>

<style>
  .review-panel { overflow:auto; flex:1; padding:26px; font-size:12px; line-height:1.6; }
  .intro { display:flex; justify-content:space-between; gap:18px; align-items:start; }.eyebrow { color:var(--accent); font-size:9px; letter-spacing:1.6px; }h1 { font-size:24px; letter-spacing:-.7px; margin:5px 0; font-weight:600; }h2 { margin:0; font-size:14px; font-weight:600; }h3 { font-size:12px; margin:18px 0 10px; }p { color:var(--muted); margin:7px 0 14px; }small,.hint { font-size:10px; color:var(--muted); }
  button,input,select,textarea { border:1px solid var(--line-2); border-radius:6px; background:var(--panel-2); color:var(--ink); padding:8px 10px; font:inherit; min-width:0; }button { cursor:pointer; white-space:nowrap; font-size:11px; }button:disabled { opacity:.4; cursor:default; }button:hover:not(:disabled) { border-color:var(--accent); }.primary { background:var(--accent); color:var(--accent-ink); border-color:transparent; font-weight:600; }label { display:flex; flex-direction:column; gap:5px; color:var(--muted); font-size:10px; }input,select,textarea { width:100%; font-size:12px; }textarea { resize:vertical; }a { color:var(--accent); }code { font:10px var(--mono); }pre { white-space:pre-wrap; overflow-wrap:anywhere; background:var(--bg); padding:12px; border-radius:6px; font:10px/1.7 var(--mono); max-height:420px; overflow:auto; }
  .run-form,.tools { border:1px solid var(--line); background:var(--panel); padding:16px; border-radius:10px; margin:16px 0 22px; }.source-row { display:flex; align-items:end; gap:10px; }.grow { flex:1; min-width:0; }.run-actions { display:flex; align-items:center; gap:8px; margin-top:14px; }.run-actions>span { margin-right:auto; color:var(--faint); font-size:10px; }.run-form .hint { margin-bottom:0; }
  .error,.notice { border-radius:6px; padding:10px 12px; font-size:11px; overflow-wrap:anywhere; }.error { color:var(--bad); background:color-mix(in srgb,var(--bad) 8%,transparent); }.error>button { float:right; padding:0 5px; }.notice { color:var(--good); background:color-mix(in srgb,var(--good) 8%,transparent); }
  details { border-bottom:1px solid var(--line); padding:10px 0; }summary { cursor:pointer; overflow-wrap:anywhere; }summary small { float:right; }.tools>button { margin-top:12px; }.history-title { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:12px; }.history-title>span { font-size:9px; color:var(--faint); }.history-title small { margin-left:7px; }.empty { padding:22px 16px; border:1px dashed var(--line-2); border-radius:8px; color:var(--muted); font-size:11px; }.empty p { margin:5px 0 0; }
  .run-list { max-height:190px; overflow:auto; border:1px solid var(--line); border-radius:8px; margin-bottom:24px; }.run-list button { width:100%; border:0; border-bottom:1px solid var(--line); border-radius:0; display:flex; gap:10px; align-items:center; background:var(--panel); text-align:left; padding:12px; }.run-list button.selected { background:var(--accent-soft); box-shadow:inset 2px 0 var(--accent); }.run-list strong { font-size:11px; font-weight:500; overflow:hidden; text-overflow:ellipsis; }.run-list small { margin-left:auto; white-space:nowrap; }
  .badge { display:inline-block; padding:2px 6px; background:var(--panel-2); border:1px solid var(--line); border-radius:4px; font-size:9px; white-space:nowrap; color:var(--muted); }[data-status='completed'],[data-status='success'],[data-status='passed'] { color:var(--good); }[data-status='failure'],[data-status='failed'],[data-status='high'],[data-status='timed_out'] { color:var(--bad); }[data-status='queued'],[data-status='capturing'],[data-status='running'],[data-status='medium'],[data-status='in_progress'] { color:var(--warn); }
  .report-heading { display:flex; justify-content:space-between; align-items:center; gap:12px; }.run-stage { display:flex; align-items:center; gap:8px; font-size:10px; margin:12px 0 6px; }.commit { font-size:10px; overflow-wrap:anywhere; }.evaluation { display:flex; flex-wrap:wrap; gap:8px 15px; padding:12px; border:1px solid var(--line); border-radius:8px; font-size:10px; margin:16px 0; }.evaluation strong { width:100%; }nav { display:flex; flex-wrap:wrap; border-bottom:1px solid var(--line); gap:3px; margin:20px 0 16px; }nav button { background:transparent; border:0; border-bottom:2px solid transparent; border-radius:0; padding:8px; font-size:10px; }nav button.active { border-bottom-color:var(--accent); color:var(--accent); }
  .summary-text,.finding p,.comment p { white-space:pre-wrap; overflow-wrap:anywhere; }.finding { border:1px solid var(--line); border-radius:8px; padding:16px; margin:12px 0; }.finding>div { display:flex; align-items:baseline; gap:8px; }.finding>code { display:block; margin:8px 0; color:var(--accent); }.finding .suggestion { border-left:2px solid var(--accent); padding-left:10px; }.finding button { font-size:10px; }.warnings { color:var(--muted); font-size:11px; }.warnings li { margin:7px 0; }
  .check-row { display:flex; gap:10px; align-items:center; border-bottom:1px solid var(--line); padding:10px 0; }.check-row strong { font-size:11px; font-weight:400; overflow-wrap:anywhere; }.check-row code { margin-left:auto; }.check-row a { white-space:nowrap; font-size:10px; }.test-counts { display:flex; gap:18px; margin:12px 0; }.test-counts small { margin-left:auto; }.test-case summary { font:10px/1.8 var(--mono); }.test-case .badge { margin-right:8px; }.test-case>small { display:block; margin-top:12px; }.comment { padding:14px; margin:10px 0; border:1px solid var(--line); border-radius:8px; }.comment small { display:block; margin-top:3px; }.comment p { margin-bottom:0; }.comment-form { display:grid; gap:12px; margin-top:18px; }.comment-form button { justify-self:end; }.metrics { display:flex; gap:12px; }.metrics span { padding:12px; background:var(--panel-2); border-radius:6px; }.save-fixture { border-top:1px solid var(--line); margin-top:24px; padding-top:8px; }.save-fixture p { font-size:11px; }
  @media(max-width:1100px) { .review-panel { padding:18px; }.run-actions { flex-wrap:wrap; }.run-actions>span { width:100%; }.history-title>span { display:none; }.run-list small { display:none; }.source-row { flex-wrap:wrap; }.source-row label.grow { min-width:170px; }.intro>button { padding:6px; font-size:10px; }h1 { font-size:20px; } }
</style>
