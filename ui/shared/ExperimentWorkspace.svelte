<script lang="ts">
  import { onMount } from 'svelte';
  import CopyOutput from './CopyOutput.svelte';
  import { request, uid, featureUrl, statusLabel } from './platform';
  let { featureId, revision, author, canAct, sourceStatus, experimentId }: {
    featureId: string; revision: number; author: string; canAct: boolean; sourceStatus: string; experimentId?: string;
  } = $props();
  let playbook = $state(''), playbookVersion = $state(''), baseline = $state(''), variant = $state('');
  let title = $state('Playbook comparison'), hypothesis = $state(''), busy = $state(false), error = $state(''), notice = $state('');
  let experiments = $state<any[]>([]), selected = $state(''), comparison = $state<any>(null);
  let assessments = $state<Record<string, { verdict: string; notes: string; version: string }>>({});
  let dirtyAssessment = $state<Record<string, boolean>>({});
  let createId = uid(), lastCreation = '';
  const canCreate = $derived(canAct && !busy && !['running','paused'].includes(sourceStatus));
  async function perform(work: () => Promise<void>) {
    busy = true; error = ''; notice = '';
    try { await work(); } catch (cause) { error = (cause as Error).message; } finally { busy = false; }
  }
  async function loadPlaybook() {
    const saved = await request('/playbook');
    playbook = baseline = saved.content; playbookVersion = saved.version;
    if (!variant) variant = saved.content;
  }
  async function refresh() {
    const listing = await request(`/experiments?feature_id=${encodeURIComponent(featureId)}`);
    experiments = listing.experiments;
    if (!selected && experiments.length) selected = experiments[0].id;
    if (selected) {
      const id = selected;
      const value = await request(`/experiments/${id}`);
      if (selected !== id) return;
      comparison = value;
      for (const arm of ['baseline', 'variant']) {
        const attempt = value.attempts[arm];
        if (!dirtyAssessment[arm] && attempt.assessment_version) assessments[arm] = {
          verdict: attempt.assessment.verdict || 'unassessed', notes: attempt.assessment.notes || '', version: attempt.assessment_version,
        };
      }
    }
  }
  async function create() {
    await perform(async () => {
      if (playbook !== baseline) throw new Error('Save or reload your project playbook before creating an experiment.');
      const fields = { feature_id: featureId, revision, author, title, hypothesis, variant_playbook: variant, baseline_playbook_version: playbookVersion };
      const fingerprint = JSON.stringify(fields);
      if (lastCreation && lastCreation !== fingerprint) createId = uid();
      lastCreation = fingerprint;
      const result = await request('/experiments', { id: createId, ...fields });
      selected = result.id; dirtyAssessment = {}; await refresh();
      createId = uid(); lastCreation = ''; notice = 'Both isolated attempts are ready. Start them below; plan and release approvals still happen in each attempt.';
    });
  }
  async function assess(arm: string) {
    await perform(async () => {
      const value = assessments[arm];
      if (!value) throw new Error('Load this attempt before saving an assessment.');
      await request(`/experiments/${selected}/assess/${arm}`, { author, expected_version: value.version, verdict: value.verdict, notes: value.notes });
      dirtyAssessment[arm] = false; await refresh(); notice = 'Assessment saved.';
    });
  }
  onMount(() => {
    selected = experimentId || '';
    let stopped = false;
    void perform(async () => { await loadPlaybook(); if (!stopped) await refresh(); });
    const timer = setInterval(() => { if (!busy && document.visibilityState === 'visible') void refresh().catch(cause => { if (!stopped) error = cause.message; }); }, 5000);
    return () => { stopped = true; clearInterval(timer); };
  });
</script>

<section aria-label="Experiment workspace">
  <h3>Improve your agent team</h3>
  <p>Compare a baseline with a different project playbook. Both attempts start from one snapshot of this feature’s current workspace, brief, contributions, and agent definitions.</p>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  <details>
    <summary>Edit shared project playbook</summary>
    <p>Used by future stage runs across this workspace. Existing runs and experiment pairs keep their saved versions.</p>
    <label>Project playbook<textarea bind:value={playbook} rows="12" maxlength="20000"></textarea></label>
    <div class="actions"><button disabled={!canAct || busy || !playbookVersion} onclick={() => void perform(async () => {
      const saved = await request('/playbook', { author, expected_version: playbookVersion, content: playbook });
      playbookVersion = saved.version; baseline = saved.content; notice = 'Project playbook saved for future runs.';
    })}>Save playbook</button><button disabled={busy} onclick={() => void perform(loadPlaybook)}>Reload saved playbook</button></div>
  </details>
  <details open={!comparison}>
    <summary>Create paired experiment</summary>
    <label>Experiment title<input bind:value={title} maxlength="160" /></label>
    <label>Hypothesis<textarea bind:value={hypothesis} rows="2" maxlength="4000" placeholder="Explicit clipboard-failure and accessibility rules will reduce Review findings."></textarea></label>
    <details><summary>Baseline playbook</summary><pre>{baseline}</pre></details>
    <label>Variant playbook<textarea bind:value={variant} rows="10" maxlength="20000"></textarea></label>
    <button disabled={!canCreate || !playbookVersion} onclick={() => void create()}>Create isolated pair</button>
    {#if ['running','paused'].includes(sourceStatus)}<p>Capture an experiment when the current stage has finished.</p>{/if}
  </details>
  {#if experiments.length}<label>Saved experiments<select bind:value={selected} onchange={() => { dirtyAssessment = {}; comparison = null; void perform(refresh); }}>{#each experiments as item}<option value={item.id}>{item.title}</option>{/each}</select></label>{/if}
  {#if comparison}
    <h3>{comparison.title}</h3><p>{comparison.hypothesis}</p>
    <p>Starting source <code>{comparison.source_hash.slice(0,12)}</code> · frozen agent definitions · approvals remain separate</p>
    <details><summary>Frozen brief and agent versions</summary><pre>{comparison.brief.description}

Acceptance criteria:
{comparison.brief.acceptance}

{JSON.stringify(comparison.agent_versions, null, 2)}</pre></details>
    {#if !comparison.start_requested}<button disabled={!canAct || busy} onclick={() => void perform(async () => { await request(`/experiments/${selected}/start`, { author }); await refresh(); })}>Start both attempts</button>{/if}
    <div class="comparison">
      {#each ['baseline', 'variant'] as arm}
        {@const attempt = comparison.attempts[arm]}
        <article>
          <h3>{arm === 'baseline' ? 'Baseline' : 'Variant'}</h3>
          <a href={featureUrl(attempt.id)}>Open attempt &amp; approvals ↗</a>
          <p>{statusLabel(attempt.status)}</p>
          {#if attempt.scope_changed}<p class="error">This attempt’s scope changed after capture; account for that when comparing results.</p>{/if}
          <details><summary>Frozen playbook</summary><pre>{comparison.playbooks[arm]}</pre></details>
          {#if attempt.total_runs !== undefined}
            <dl><dt>Recorded tokens</dt><dd>{attempt.tokens === null ? 'Not recorded' : attempt.tokens.toLocaleString()} <small>({attempt.measured_runs}/{attempt.total_runs} runs measured)</small></dd><dt>Repair rounds</dt><dd>{attempt.repair_rounds}</dd><dt>Latest Review findings</dt><dd>{attempt.review_recorded ? attempt.review_findings.length : 'Not reviewed'}</dd></dl>
            <h4>Recorded checks</h4>
            {#each Object.entries(attempt.checks) as [name, raw]}{@const check = raw as any}<details><summary>{name.replaceAll('_',' ')} · {check.status}{check.current ? '' : ' · stale'}</summary><div class="check-output-row"><pre>{check.output}</pre><CopyOutput output={check.output} /></div></details>{:else}<p>No check evidence yet.</p>{/each}
            <details><summary>Changed source</summary><pre>{attempt.diff.patch || 'No source changes.'}</pre>{#if attempt.diff.truncated}<p>Diff truncated. Open the attempt to inspect files.</p>{/if}</details>
            <details><summary>Stage results and artifacts</summary>{#each Object.entries(attempt.results) as [stage, output]}<h4>{stage}</h4><pre>{JSON.stringify(output, null, 2)}</pre>{/each}</details>
            {#if assessments[arm]}
              <h4>Your assessment against acceptance criteria</h4>
              {#if attempt.assessment_stale}<p class="error">The source, scope, or run history changed after this assessment. Review the current evidence and save an updated assessment.</p>{/if}
              <p>This is a human assessment; stage claims and check results remain separate evidence.</p>
              <label>Outcome<select bind:value={assessments[arm].verdict} onchange={() => dirtyAssessment[arm] = true}><option value="unassessed">Not assessed</option><option value="met">Met</option><option value="partial">Partially met</option><option value="not_met">Not met</option></select></label>
              <label>Evidence and notes<textarea bind:value={assessments[arm].notes} oninput={() => dirtyAssessment[arm] = true} rows="4" maxlength="6000"></textarea></label>
              <button disabled={!canAct || busy || !dirtyAssessment[arm]} onclick={() => void assess(arm)}>Save assessment</button>
              <button disabled={busy} onclick={() => { dirtyAssessment[arm] = false; void perform(refresh); }}>Reload assessment</button>
            {/if}
          {/if}
        </article>
      {/each}
    </div>
    <p>One pair is a useful observation. Repeat with several tasks before keeping a playbook change. Experiment outputs stay isolated; compare their diffs before adopting changes.</p>
  {/if}
</section>

<style>
  section,article { min-width:0; } p,small { color:var(--muted); font-size:12px; line-height:1.8; }
  details { border:1px solid var(--line); border-radius:7px; padding:12px; margin:14px 0; min-width:0; } summary { cursor:pointer; font-size:12px; }
  label { display:flex; flex-direction:column; gap:7px; margin:16px 0; font-size:12px; } input,textarea,select { width:100%; min-width:0; background:var(--panel); color:var(--ink); border:1px solid var(--line); border-radius:6px; padding:10px; } textarea { resize:vertical; font:12px/1.8 var(--mono); }
  button { background:var(--accent-soft); color:var(--ink); border:1px solid var(--line); padding:9px 12px; border-radius:6px; font-size:12px; } button:disabled { opacity:.45; } .actions { display:flex; gap:10px; }
  .comparison { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; margin:20px 0; } article { border:1px solid var(--line); padding:16px; border-radius:8px; } a { color:var(--accent); font-size:12px; } h4 { font-size:12px; margin:20px 0 10px; }
  pre { white-space:pre-wrap; overflow-wrap:anywhere; font:11px/1.8 var(--mono); max-height:440px; overflow:auto; } .check-output-row { display:flex; align-items:flex-start; gap:10px; flex-wrap:wrap; } .check-output-row pre { flex:1 1 450px; min-width:0; margin:0; } dl { font-size:12px; } dt { color:var(--muted); margin-top:12px; } dd { margin:4px 0; } .error { color:var(--bad); } .notice { color:var(--good); }
  @media(max-width:1100px) { .comparison { grid-template-columns:minmax(0,1fr); } }
</style>
