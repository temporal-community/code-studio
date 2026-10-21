<script lang="ts">
  import { onMount } from 'svelte';
  import { api, newId, label } from './features';
  import type { FeatureSummary } from './features';
  let { author, canRun, refreshKey, revision }: { author: string; canRun: boolean; refreshKey: number; revision: number } = $props();
  let runs = $state<(FeatureSummary & { agent_version?: string | null })[]>([]);
  let enabled = $state(true), busy = $state(false), error = $state(''), message = $state('');
  let version = $state(''), repository = $state(''), branch = $state(''), base = $state('HEAD');
  let title = $state(''), description = $state(''), acceptance = $state('');
  let created = $state('');
  let pending: { id: string; fingerprint: string } | null = null;
  const url = (id: string) => `?view=features&feature=${encodeURIComponent(id)}`;
  async function refresh() {
    try {
      const [state, project] = await Promise.all([api('/state'), api('/agent')]);
      runs = state.features; enabled = state.enabled; version = project.version;
      if (!repository && state.demo_repository) repository = state.demo_repository;
    } catch (cause) { error = (cause as Error).message; }
  }
  async function validate() {
    busy = true; error = ''; message = '';
    try { const result = await api('/agent/validate', {}); version = result.version; message = result.message + ' Version ' + version.slice(0, 8) + '.'; }
    catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  async function demo() {
    busy = true; error = '';
    try { const repo = await api('/demo', {}); repository = repo.path; base = 'main'; message = 'Demo codebase ready. Try adding task completion or filtering.'; }
    catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  async function run(event: SubmitEvent) {
    event.preventDefault(); busy = true; error = ''; message = ''; created = '';
    try {
      const validated = await api('/agent/validate', {});
      const body = { title, description, acceptance, repository, branch, base, author };
      const fingerprint = JSON.stringify(body);
      if (!pending || pending.fingerprint !== fingerprint) pending = { id: newId(), fingerprint };
      const feature = await api('', { ...body, id: pending.id });
      created = feature.id;
      if (feature.status === 'branch_failed') throw new Error(feature.error || 'Could not create the feature branch');
      if (!feature.jobs.some((job: { id: string }) => job.id === 'initial-plan')) await api(`/${feature.id}/plan`, { id: 'initial-plan', revision: feature.input_revision, author, agent_version: validated.version });
      message = 'Agent started with version ' + validated.version.slice(0, 8) + '. Open the run to review and approve its plan.';
      pending = null; title = description = acceptance = branch = '';
      await refresh();
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  $effect(() => { void refreshKey; void revision; void refresh(); });
  onMount(() => { const interval = setInterval(() => void refresh(), 5000); return () => clearInterval(interval); });
</script>

<section class="runs">
  <div class="heading"><div><span class="eyebrow">PLANNER AGENT</span><h2>Run your agent on a codebase</h2><p>A saved copy of your Python flow, prompts, and settings drives each run.</p></div><button onclick={() => void validate()} disabled={!canRun || busy}>Validate agent</button></div>
  {#if error}<p class="alert" role="alert">{error}</p>{/if}
  {#if message}<p class="notice" role="status">{message}</p>{/if}
  {#if created}<a class="open-run" href={url(created)}>Open the feature run →</a>{/if}
  {#if !enabled}<p class="notice">Start <code>just dev</code> with an OpenAI key to execute agents. Shared editing works without it.</p>{/if}
  <form onsubmit={run}>
    <div class="form-title"><h3>New run</h3><code>agent {version.slice(0, 8) || 'loading…'}</code></div>
    <label>Local Git codebase<input bind:value={repository} placeholder="/Users/you/projects/my-project" required maxlength="1000" /></label>
    <button type="button" onclick={() => void demo()} disabled={!canRun || busy}>Use demo codebase</button>
    <div class="two-fields"><label>New feature branch<input bind:value={branch} placeholder="feature/complete-tasks" required maxlength="160" /></label><label>Base revision<input bind:value={base} required maxlength="200" /></label></div>
    <label>Run title<input bind:value={title} placeholder="Add task completion" required maxlength="160" /></label>
    <label>Work for the agent<textarea bind:value={description} rows="4" placeholder="Describe the feature to implement in this codebase…" required maxlength="12000"></textarea></label>
    <label>Acceptance criteria<textarea bind:value={acceptance} rows="2" placeholder="Expected behavior and edge cases" maxlength="8000"></textarea></label>
    <div class="run-footer"><p>Creates a branch in a separate worktree, then runs the planning phase. Coding starts after you approve the plan.</p><button class="primary" type="submit" disabled={!canRun || !enabled || busy}>{busy ? 'Starting…' : 'Run agent'}</button></div>
  </form>
  <h3>Shared runs <span>{runs.length}</span></h3>
  <div class="run-list">{#each runs as item}<a href={url(item.id)}><div><strong>{item.title}</strong><code>{item.branch}</code></div><div><span class="status">{label(item.status)}</span><small>{item.agent_version ? `agent ${item.agent_version.slice(0, 8)}${item.agent_version !== version ? ' · earlier version' : ''}` : 'Not run yet'}</small></div></a>{:else}<p class="muted">Runs from everyone in this workspace appear here, with the agent version they used.</p>{/each}</div>
  <details><summary>What can teammates customize?</summary><p><code>feature.py</code> controls orchestration and focus. <code>planner.md</code> and <code>coder.md</code> define behavior. <code>feature.json</code> selects the model, available file tools, and turn limit.</p><p>Planning has no file-write tools. Coding uses the version that created the approved plan. To try newer agent edits, open a run and choose <strong>Summarize again</strong>, or start a new branch.</p></details>
</section>

<style>
  .runs { padding:25px 28px 60px; overflow:auto; flex:1; min-height:0; }.heading { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }.eyebrow { font-size:9px; color:var(--accent); letter-spacing:1.5px; }h2 { margin:8px 0; font-size:22px; font-weight:550; letter-spacing:-.5px; }.heading p { color:var(--muted); font-size:12px; line-height:1.8; }.heading button { flex:none; margin-top:7px; }h3 { font-size:13px; margin:26px 0 15px; }h3 span { color:var(--muted); font-size:11px; margin-left:7px; }button { border:1px solid var(--line-2); border-radius:7px; background:var(--panel-2); padding:8px 12px; font-size:11px; }button:disabled { opacity:.45; }.primary { background:var(--accent); color:var(--accent-ink); font-weight:600; }form { margin:24px 0 32px; border:1px solid var(--line); padding:22px; border-radius:10px; background:var(--panel); }.form-title { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }.form-title h3 { margin:0; }.form-title code { color:var(--muted); font-size:10px; }label { display:flex; flex-direction:column; gap:7px; margin-bottom:17px; font-size:11px; }input,textarea { border:1px solid var(--line-2); border-radius:6px; padding:10px 12px; background:var(--bg); font-size:12px; width:100%; }textarea { resize:vertical; line-height:1.7; }input::placeholder,textarea::placeholder { color:var(--faint); }.two-fields { display:grid; grid-template-columns:2fr 1fr; gap:16px; margin-top:22px; }.run-footer { display:flex; justify-content:space-between; align-items:center; gap:25px; }.run-footer p { font-size:11px; color:var(--muted); line-height:1.8; margin:0; }.run-footer button { flex:none; }.alert,.notice { padding:12px 15px; border:1px solid var(--line); border-radius:8px; font-size:12px; line-height:1.8; white-space:pre-wrap; }.alert { color:var(--warn); }.notice { color:var(--good); }.open-run { color:var(--accent); display:inline-block; margin:4px 0; font-weight:550; }.run-list { border:1px solid var(--line); border-radius:8px; overflow:hidden; }.run-list>a { display:flex; justify-content:space-between; gap:20px; padding:16px; text-decoration:none; color:var(--ink); border-bottom:1px solid var(--line); }.run-list>a:hover { background:var(--panel-2); }.run-list>a>div { display:flex; flex-direction:column; gap:7px; }.run-list>a>div:last-child { text-align:right; }.run-list strong { font-size:12px; font-weight:550; }.run-list code,.run-list small { color:var(--muted); font-size:10px; overflow-wrap:anywhere; }.status { color:var(--accent); font-size:10px; text-transform:capitalize; }.muted { color:var(--muted); padding:15px; font-size:12px; }details { margin-top:28px; font-size:12px; color:var(--muted); line-height:1.9; }summary { cursor:pointer; color:var(--ink); }details code { font-size:11px; }@media(max-width:650px) { .runs { padding:20px 15px; }.heading,.run-footer { flex-direction:column; }.two-fields { grid-template-columns:1fr; gap:0; } }
</style>
