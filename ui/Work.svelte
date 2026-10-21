<script lang="ts">
  import { loadFeatureDraft, clearFeatureDraft, listFeatureDrafts, storeFeatureDraft, removeFeatureDraft, createFeatureDraftRecoveryAutosave } from './shared/featureDraft';
  import { sessionName } from './shared/identity';
  import { workspaceConnection } from './shared/workspaceConnection';
  import CopyOutput from './shared/CopyOutput.svelte';
  import { coalesceRefresh } from './shared/refresh';
  import { onMount, tick } from 'svelte';
  import HarnessPip from './shared/HarnessPip.svelte';
  import { featureOverview } from './shared/featureOverview';
  import { approvalInbox } from './shared/approvalInbox';
  import { harnessTarget } from './shared/harnessDebug';
  import { currentHarnessSession } from './shared/harnessSession';
  import FeatureFiles from './shared/FeatureFiles.svelte';
  import IntegrationPanel from './shared/IntegrationPanel.svelte';
  import SharedLibrary from './shared/SharedLibrary.svelte';
  import ExperimentWorkspace from './shared/ExperimentWorkspace.svelte';
  import LiveStage from './shared/LiveStage.svelte';
  import StageQuestions from './shared/StageQuestions.svelte';
  import BrowserPreview from './shared/BrowserPreview.svelte';
  import StageRunComparison from './shared/StageRunComparison.svelte';
  import LivePipeline from './shared/LivePipeline.svelte';
  import PipelineApprovals from './shared/PipelineApprovals.svelte';
  import { request, names, stages, statusLabel, stageTone, uid, studioUrl, featureUrl } from './shared/platform';
  import StageStatusIcon from './shared/StageStatusIcon.svelte';
  import { markdown, markdownInline } from './shared/markdown';
  let features = $state<any[]>([]), feature = $state<any>(null), selected = $state(new URLSearchParams(location.search).get('feature') || '');
  let selectedStage = $state(new URLSearchParams(location.search).get('stage') || ''), tab = $state('output');
  let featureQuery = $state(''), stageFilter = $state('all');
  const initialDrafts = listFeatureDrafts();
  const initialDraftId = initialDrafts.find(item => item.id === new URLSearchParams(location.search).get('draft'))?.id || '';
  let drafts = $state(initialDrafts);
  let draftId = $state(initialDraftId);
  const displayedDrafts = $derived(drafts.filter(item => ['all', 'draft'].includes(stageFilter) &&
    (!featureQuery.trim() || `${item.title} ${item.brief}`.toLowerCase().includes(featureQuery.trim().toLowerCase()))));
  const displayedFeatures = $derived(features.filter((item: any) => {
    const query = featureQuery.trim().toLowerCase();
    const textMatches = !query || String(item.title ?? '').toLowerCase().includes(query) || String(item.summary ?? '').toLowerCase().includes(query);
    return textMatches && (stageFilter === 'all' || stageFilter === item.current_stage);
  }));
  const name = sessionName();
  const joined = !!name;
  let peers = $state<{ name: string; id: string }[]>([]), connected = $state(false), loaded = $state(false), enabled = $state(true);
  const savedDraft = initialDrafts.find(item => item.id === initialDraftId) || loadFeatureDraft();
  let creating = $state(new URLSearchParams(location.search).has('new') || !!initialDraftId), directory = $state(savedDraft.directory), title = $state(savedDraft.title), idea = $state(savedDraft.idea), brief = $state(savedDraft.brief), acceptance = $state(savedDraft.acceptance), branch = $state(savedDraft.branch);
  let sourceMode = $state(savedDraft.sourceMode), base = $state(savedDraft.base), busy = $state(false), error = $state(''), notice = $state(''), changes = $state(0), filesDirty = $state(false);
  $effect(() => {
    if (creating || (loaded && !features.length && !selected)) {
      const form = { directory, title, idea, brief, acceptance, branch, sourceMode, base };
      if (draftId) {
        try { drafts = storeFeatureDraft(draftId, form); }
        catch { error = 'Could not save this draft. Browser storage may be full or unavailable. Keep this page open and copy your text before leaving.'; }
      } else recoveryAutosave.save(form);
    }
  });
  let comment = $state(''), inputKind = $state('comment'), historyId = $state('');
  let socket: ReturnType<typeof workspaceConnection> | null = null;
  let createId = initialDraftId || uid();
  const recoveryAutosave = createFeatureDraftRecoveryAutosave();
  let suggesting = $state(false), detailsOpen = $state(false);
  let workInput = $state<HTMLTextAreaElement>();
  let workHeading = $state<HTMLHeadingElement>();
  let previousSuggestions: string[] = [];
  let draftVersion = 0;
  let disposed = false, platformRevision = '', featureRevision = '', filesRevision = '';
  let revisionsFeature = '';
  let codeChanges = $state(0);
  let expanded = $state<Record<string, boolean>>({});
  const pipeline = $derived(feature?.pipeline);
  const stage = $derived(selectedStage || pipeline?.current_stage || 'ideation');
  const run = $derived(pipeline?.runs.find((r: any) => r.id === historyId) || [...(pipeline?.runs || [])].reverse().find((r: any) => r.stage === stage));
  let comparisonMode = $state(false), comparisonA = $state(''), comparisonB = $state(''), comparisonNotice = $state('');
  const stageRuns = $derived((pipeline?.runs || []).filter((item: any) => item.stage === stage));
  const comparisonRunA = $derived(stageRuns.find((item: any) => item.id === comparisonA));
  const comparisonRunB = $derived(stageRuns.find((item: any) => item.id === comparisonB));
  $effect(() => {
    if (comparisonMode && (!comparisonRunA || !comparisonRunB || comparisonA === comparisonB)) {
      comparisonMode = false;
      comparisonNotice = 'The selected runs are no longer available for this stage. Choose two retained runs to compare.';
    }
  });
  const canAct = $derived(joined && connected && !busy && !suggesting && !filesDirty);
  const uniquePeers = $derived([...new Map(peers.map(p => [p.name, p])).values()]);
  const currentAgent = $derived(names[pipeline?.current_stage] || 'Agent team');
  const currentRun = $derived(pipeline?.runs.find((r: any) => r.id === pipeline.stages[pipeline.current_stage]?.run_id));
  $effect(() => { harnessTarget.set({ workflowId: creating ? '' : currentHarnessSession(pipeline), label: feature ? `${feature.title} · Coordinator` : 'Feature agents' }); });
  const overview = $derived(featureOverview(pipeline));
  const pendingApprovals = $derived(approvalInbox(features));
  const releaseCheckIssues = $derived(Object.entries(pipeline?.checks || {}).filter(([, value]) => ['failed', 'timed_out', 'stale'].includes((value as any).status)));
  function checkProblem(name: string, value: any) {
    const label = name.replaceAll('_', ' ');
    if (value.status === 'timed_out') {
      const seconds = value.timeout_seconds || value.output?.match(/Check exceeded (\d+) seconds/)?.[1];
      return `${label} timed out${seconds ? ` after ${seconds} seconds` : ''}. Inspect the last test in the output. Retry after resolving a slow or stuck test, or manually accept this incomplete check.`;
    }
    return value.status === 'stale' ? `${label} is stale because validation inputs changed. Retry Deployment to check the current source.` : `${label} failed. Fix the errors shown in Checks, then rerun Development and Review if source files changed.`;
  }
  const completed = $derived(pipeline ? stages.filter(s => ['completed', 'approved'].includes(pipeline.stages[s].status)).length : 0);

  function hitTurnLimit(message: string | undefined) { return /MaxTurnsExceeded|Max turns (\d+) exceeded/.test(message || ''); }
  function openComparison() {
    if (stageRuns.length < 2) return;
    comparisonA = stageRuns[stageRuns.length - 1].id;
    comparisonB = stageRuns[stageRuns.length - 2].id;
    comparisonNotice = '';
    comparisonMode = true;
  }
  function closeComparison() { comparisonMode = false; comparisonNotice = ''; }
  function turnBudget(value: any): number | null {
    try {
      const budget = JSON.parse(value?.project?.files?.[`${value.stage}_settings.json`] || '{}').max_turns;
      if (Number.isInteger(budget) && budget > 0) return budget;
    } catch { /* A historical settings file may no longer match the current schema. */ }
    const match = value?.error?.match(/Max turns (\d+) exceeded/);
    return match ? Number(match[1]) : null;
  }

  let refreshDetails = false;
  function refresh(details = true) { refreshDetails ||= details; return flushRefresh(); }
  const flushRefresh = coalesceRefresh(async () => {
    const inspect = refreshDetails; refreshDetails = false;
    const id = selected, includeFiles = tab === 'code';
    try {
      const rev = await request(`/platform/revisions?feature_id=${encodeURIComponent(inspect ? id : '')}&include_files=${inspect && includeFiles}`);
      if (disposed || id !== selected) return;
      if (!loaded || rev.platform !== platformRevision) {
        const state = await request('/platform');
        if (disposed || id !== selected) return;
        features = state.features; enabled = state.enabled; loaded = true;
        platformRevision = rev.platform;
        if (!directory) directory = state.default_directory;
        if (!selected && features.length && !creating) {
          selected = features[0].id;
          void refresh();
          return;
        }
      }
      if (!inspect) return;
      if (id && (!feature || revisionsFeature !== id || rev.feature !== featureRevision)) {
        const result = await request(`/work/${encodeURIComponent(id)}`);
        if (disposed || id !== selected) return;
        // Keep DOM and child props stable when a notification carries no new data.
        if (JSON.stringify(result) !== JSON.stringify(feature)) { feature = result; changes += 1; }
        featureRevision = rev.feature;
      }
      if (id && includeFiles && (revisionsFeature !== id || rev.files !== filesRevision || rev.files_error)) {
        filesRevision = rev.files || '';
        codeChanges += 1;
      }
      revisionsFeature = id;
    } catch (e) { if (!disposed && id === selected) error = (e as Error).message; }
  });
  $effect(() => { if (tab === 'code') void refresh(); });
  function openDraft(id: string) {
    if (filesDirty || busy || suggesting) return;
    const draft = drafts.find(item => item.id === id);
    if (!draft) return;
    draftVersion += 1; draftId = id; createId = id; creating = true;
    ({ directory, title, idea, brief, acceptance, branch, sourceMode, base } = draft);
    detailsOpen = false; error = ''; notice = ''; history.replaceState(null, '', `?view=work&draft=${encodeURIComponent(id)}`);
  }
  async function deleteDraft(id: string) {
    if (filesDirty || busy || suggesting) return;
    const draft = drafts.find(item => item.id === id);
    if (!draft) return;
    const draftTitle = draft.title.trim() || 'Untitled feature';
    if (!window.confirm(`Delete “${draftTitle}” from this browser’s saved drafts? This cannot be undone.`)) return;
    try {
      const remaining = removeFeatureDraft(id);
      recoveryAutosave.preserveNextSave();
      drafts = remaining;
      draftVersion += 1;
      draftId = '';
      createId = uid();
      creating = true;
      directory = title = idea = brief = acceptance = branch = '';
      sourceMode = 'current';
      base = 'HEAD';
      detailsOpen = false;
      history.replaceState(null, '', '?view=work&new');
      await tick();
      workHeading?.focus();
      error = '';
      notice = `Deleted “${draftTitle}” from this browser’s saved drafts.`;
    } catch {
      error = 'Could not delete this draft. Browser storage may be unavailable; the saved draft has not been removed.';
    }
  }

  function saveDraft() {
    try {
      const id = draftId || createId;
      drafts = storeFeatureDraft(id, { directory, title, idea, brief, acceptance, branch, sourceMode, base });
      draftId = id; creating = true; stageFilter = 'all'; featureQuery = '';
      clearFeatureDraft();
      history.replaceState(null, '', `?view=work&draft=${encodeURIComponent(id)}`);
      error = ''; notice = 'Draft saved in this browser. You can edit it now or return from the feature list. No agents have started.';
    } catch { error = 'Could not save the draft. Browser storage may be full or unavailable.'; }
  }
  function startNew() {
    draftVersion += 1;
    if (draftId) {
      const scratch = loadFeatureDraft();
      ({ directory, title, idea, brief, acceptance, branch, sourceMode, base } = scratch);
      draftId = ''; createId = uid();
    }
    creating = true; detailsOpen = false; error = ''; notice = '';
    history.replaceState(null, '', '?view=work&new');
  }
  async function choose(id: string) { if (filesDirty) return; draftVersion += 1; selected = id; creating = false; feature = null; selectedStage = ''; historyId = ''; comparisonMode = false; comparisonA = ''; comparisonB = ''; comparisonNotice = ''; tab = 'output'; error = ''; notice = ''; history.replaceState(null, '', featureUrl(id)); await refresh(); }
  async function perform(fn: () => Promise<void>) { busy = true; error = ''; notice = ''; try { await fn(); } catch (e) { error = (e as Error).message; } finally { busy = false; } }
  async function suggestFeature() {
    if (suggesting || busy || !enabled || !joined || !directory.trim() || (sourceMode === 'git' && !base.trim())) return;
    const version = draftVersion;
    suggesting = true; error = ''; notice = '';
    try {
      const suggestion = await request<{ title: string; description: string; acceptance: string; rationale: string; repository: string }>('/platform/suggestion', {
        author: name, previous_titles: previousSuggestions, description: idea.trim(),
        repository: directory, source_mode: sourceMode, base,
      });
      previousSuggestions = [...previousSuggestions, suggestion.title].slice(-10);
      if (disposed || version !== draftVersion) return;
      title = suggestion.title; brief = suggestion.description; acceptance = suggestion.acceptance;
      detailsOpen = true;
      notice = suggestion.rationale;
    } catch (e) {
      if (!disposed && version === draftVersion) error = (e as Error).message;
    } finally { suggesting = false; }
    if (!disposed && version === draftVersion && !error) { await tick(); workInput?.focus(); }
  }

  async function create(e: SubmitEvent) { e.preventDefault(); if (!canAct || !enabled) return; const form = e.currentTarget as HTMLFormElement; detailsOpen = true; await tick(); if (!form.reportValidity()) return; await perform(async () => { const result = await request('/work', { id: createId, title, description: brief, acceptance, repository: directory, base, branch: branch || 'feature/' + title.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 65) + '-' + createId.slice(0, 6), author: name, source_mode: sourceMode }); clearFeatureDraft(); if (draftId) { try { drafts = removeFeatureDraft(draftId); } catch { error = 'Feature started, but the saved draft could not be removed from browser storage.'; } draftId = ''; } createId = uid(); title = idea = brief = acceptance = branch = ''; detailsOpen = false; await choose(result.id); }); }
  async function control(action: string, target?: string) { await perform(async () => { await request(`/work/${selected}/control/${action}`, { id: uid(), author: name, revision: feature.input_revision, stage: target || null }); historyId = ''; selectedStage = ''; await refresh(); }); }
  async function post(e: SubmitEvent) { e.preventDefault(); const text = comment; await perform(async () => { if (inputKind === 'steering') { await request(`/work/${selected}/feedback`, { id: uid(), author: name, revision: feature.input_revision, text }); } else { await request(`/work/${selected}/inputs`, { id: uid(), author: name, kind: inputKind, text }); } if (comment === text) comment = ''; await refresh(); }); }
  async function share() { try { await navigator.clipboard.writeText(new URL(featureUrl(selected), location.href).href); notice = 'Feature link copied. Teammates can join this same workspace.'; } catch { notice = 'Share this page’s URL to invite a teammate.'; } }
  function when(value: number) { return new Date(value * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
  onMount(() => {
    let pending: ReturnType<typeof setTimeout> | undefined;
    let pendingDetails = false;
    function scheduleRefresh(details: boolean) {
      pendingDetails ||= details;
      if (pending !== undefined || document.visibilityState !== 'visible') return;
      pending = setTimeout(() => { pending = undefined; const details = pendingDetails; pendingDetails = false; void refresh(details); }, 100);
    }
    function catchUp() { if (document.visibilityState === 'visible') void refresh(); }
    document.addEventListener('visibilitychange', catchUp);
    // Opening the socket and initial loading share the same refresh batch.
    socket = workspaceConnection(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/workspace/connect?name=${encodeURIComponent(name || 'Visitor')}`, {
      open: () => { connected = true; void refresh(); socket?.send({ type: 'presence', name: name || 'Visitor', file: 'Feature workspace' }); },
      message: e => { const data = JSON.parse(e.data); if (data.type === 'presence') peers = data.peers; if (data.type === 'feature-changed') scheduleRefresh(!data.feature_id || data.feature_id === selected); },
      offline: () => { connected = false; peers = []; },
    });
    void refresh();
    const poll = setInterval(catchUp, 30_000);
    return () => { disposed = true; flushRefresh.dispose(); clearTimeout(pending); document.removeEventListener('visibilitychange', catchUp); clearInterval(poll); socket?.close(); };
  });
</script>

<svelte:head><title>Code Studio · Features</title></svelte:head>
<div class="work-app">
  <header class="topbar"><a class="brand" href="/">⌘ <strong>Code Studio</strong></a><nav><a href="/">Agents</a><a class="active" href="?view=work" aria-current="page">Features</a></nav><div class="connection"><i class:connected></i>{connected ? 'Live workspace' : 'Reconnecting…'}<span>{uniquePeers.length} online</span></div></header>
  <div class="layout">
    <aside class="feature-list"><div class="list-title">FEATURES <span>{displayedFeatures.length + displayedDrafts.length}</span></div><button class="new io-button io-button--primary" disabled={!joined || filesDirty || busy || suggesting} onclick={startNew}>＋ Start feature</button><label class="feature-search">Search features<input type="search" bind:value={featureQuery} placeholder="Title or summary" /></label><label class="feature-filter">Stage<select bind:value={stageFilter}><option value="all">All</option><option value="draft">Draft</option>{#each stages as stage}<option value={stage}>{names[stage]}</option>{/each}</select></label>
      {#if loaded}
        <section class="approval-inbox" aria-labelledby="approval-inbox-title">
          <h2 id="approval-inbox-title">Needs team approval</h2>
          {#if pendingApprovals.length}
            <ul>{#each pendingApprovals as item (item.id)}
              <li><a href={featureUrl(item.id, item.approvalStage)}><strong>{item.title}</strong><span>{item.approvalType}</span><small>Current stage: {names[item.current_stage] || item.current_stage}</small></a></li>
            {/each}</ul>
          {:else}<p class="muted">No pending approvals.</p>{/if}
        </section>
      {/if}
      {#each displayedDrafts as draft (draft.id)}<button class="draft-open" class:selected={creating && draftId === draft.id} aria-label={`Open draft: ${draft.title.trim() || 'Untitled feature'}`} disabled={filesDirty || busy || suggesting} onclick={() => openDraft(draft.id)}><strong>{draft.title.trim() || 'Untitled feature'}</strong><span class="draft-tag">Draft</span></button>{/each}{#each displayedFeatures as item (item.id)}<button class:selected={item.id === selected && !creating} disabled={filesDirty || busy || suggesting} onclick={() => void choose(item.id)}><strong>{item.title}{#if item.experiment_arm} · {item.experiment_arm}{/if}</strong><span>{names[item.current_stage]} · {statusLabel(item.pipeline_status)}</span><div class="mini-progress">{#each stages as s}<i data-tone={stageTone(item.stages[s].status)}></i>{/each}</div></button>{/each}{#if loaded && (features.length || drafts.length) && !displayedFeatures.length && !displayedDrafts.length}<p class="muted side-empty" role="status">No features match your search and filters.</p>{/if}{#if loaded && !features.length && !drafts.length}<p class="muted side-empty">Start a feature or join one your team creates. Progress and decisions stay together.</p>{/if}<div class="team"><span class="eyebrow">IN THIS WORKSPACE</span>{#each uniquePeers as peer}<div><b>{peer.name.slice(0,1).toUpperCase()}</b>{peer.name}{#if peer.name === name}<small>you</small>{/if}</div>{/each}</div></aside>
    <main>
      {#if error}<div class="banner error" role="alert">{error}<button onclick={() => { error = ''; void refresh(); }}>Refresh</button></div>{/if}{#if notice}<div class="banner" role="status">{notice}</div>{/if}
      {#if creating || (loaded && !features.length)}
        <section class="create">
          <span class="eyebrow">FROM IDEA TO RELEASE PREVIEW</span>
          <h1 bind:this={workHeading} tabindex="-1">{draftId ? 'Review feature draft' : 'Start a feature'}</h1>
          <p class="muted">The agents move work forward. Your team approves the plan and the release.</p>
          <form onsubmit={create} novalidate aria-busy={suggesting}>
            <fieldset disabled={suggesting || busy}>
              <div class="two source-row">
                <label>Working directory<input bind:value={directory} required maxlength="1000" /></label>
                <label>Starting source<select bind:value={sourceMode}><option value="current">Current working files</option><option value="git">Committed Git revision</option></select></label>
              </div>
              {#if sourceMode === 'git'}
                <label>Base revision<input bind:value={base} required maxlength="200" /></label>
              {:else}
                <p class="fine source-hint">Includes your current uncommitted work in an isolated copy. Source files change only when you apply reviewed results.</p>
              {/if}
              <div class="idea-row">
                <label>Describe feature<input bind:value={idea} maxlength="4000" placeholder="What would you like to build or improve?" aria-describedby="feature-idea-help" onkeydown={e => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); void suggestFeature(); } }} /></label>
                <button class="io-button io-button--secondary" type="button" onclick={() => void suggestFeature()} disabled={!enabled || !joined || !directory.trim() || (sourceMode === 'git' && !base.trim())} aria-busy={suggesting}>{suggesting ? (idea.trim() ? 'Preparing feature…' : 'Suggesting…') : idea.trim() ? 'Submit' : 'Suggest Feature'}</button>
              </div>
              <p id="feature-idea-help" class="fine idea-help">Share an idea to draft the details, or leave it blank for a suggestion.</p>
              {#if suggesting}<p class="fine" role="status">{idea.trim() ? 'Preparing the work description and acceptance criteria from your idea…' : 'Finding a feature to suggest from the selected source…'}</p>{/if}
              <details class="feature-details" bind:open={detailsOpen}>
                <summary>Feature details <span>Title, work description, and acceptance criteria</span></summary>
                <div class="details-fields">
                  <label>Feature title<input bind:value={title} maxlength="160" placeholder="A short name for the feature" required /></label>
                  <label>What work should be done?<textarea bind:this={workInput} bind:value={brief} rows="10" maxlength="12000" required placeholder="Describe the outcome, users, and constraints…"></textarea></label>
                  <label>Acceptance criteria<textarea bind:value={acceptance} rows="8" maxlength="8000" placeholder="What should be true when this is done?"></textarea></label>
                </div>
              </details>
              <details><summary>Branch / workspace name</summary><input aria-label="Branch or workspace name" bind:value={branch} placeholder="Generated from your feature title" maxlength="160" /></details>
              <div class="form-actions">
                <button class="io-button" type="button" onclick={saveDraft} disabled={busy || suggesting}>{draftId ? 'Save draft' : 'Save as draft'}</button>
                {#if draftId}<button class="draft-delete" aria-label={`Delete draft: ${title.trim() || 'Untitled feature'}`} disabled={filesDirty || busy || suggesting} onclick={() => void deleteDraft(draftId)} type="button">Delete</button>{/if}
                <button class="primary io-button io-button--primary" aria-busy={busy} disabled={!canAct || !enabled} type="submit">{busy ? 'Starting…' : 'Start feature →'}</button>
              </div>
              {#if !enabled}<p class="warning">Start just dev with an OpenAI key to run the agents.</p>{/if}
            </fieldset>
          </form>
        </section>
      {:else if feature}
        <section class="feature-header" aria-label="Feature overview">
          <div class="feature-heading"><span class="eyebrow">SHARED FEATURE · SCOPE {feature.input_revision}</span><h1>{feature.title}</h1>{#if feature.experiment_arm}<p>Experiment · {feature.experiment_arm}</p>{/if}<p><code>{feature.branch}</code><span>{feature.workspace_mode === 'snapshot' ? 'Current-source snapshot' : 'Git worktree'}</span></p>
            <div class="overview-status"><span class="eyebrow">WHAT’S HAPPENING</span><h2>{overview.title}</h2><p>{overview.detail}</p></div>
            <button onclick={() => void share()}>Share feature ↗</button>
          </div>
          <HarnessPip inline />
        </section>
        <div class="progress-heading"><strong>{statusLabel(pipeline.status)}</strong><span>{completed} of 6 stages finished</span>{#if pipeline.status === 'running'}<button disabled={!canAct} onclick={() => void control('pause')}>Pause agents</button>{/if}</div>
        <div class="stage-strip" data-preview-navigation aria-label="Feature stages">{#each stages as s, index}<button class:selected={stage === s} data-tone={stageTone(pipeline.stages[s].status)} aria-current={stage === s ? 'step' : undefined} onclick={() => { selectedStage = s; historyId = ''; comparisonMode = false; comparisonA = ''; comparisonB = ''; comparisonNotice = ''; tab = 'output'; }} disabled={filesDirty}><StageStatusIcon status={pipeline.stages[s].status} tone={stageTone(pipeline.stages[s].status)} step={index + 1} /><strong>{names[s]}</strong><small class="stage-status">{statusLabel(pipeline.stages[s].status)}</small></button>{/each}</div>
        {#if ['needs_plan_approval','needs_release_approval'].includes(pipeline.status) && pipeline.harness_version === 2}<PipelineApprovals workflowId={pipeline.execution.workflow_id} featureId={selected} revision={feature.input_revision} author={name} {canAct} onresolved={() => void refresh()} />{/if}
        {#if ['needs_plan_approval','needs_release_approval'].includes(pipeline.status) && pipeline.harness_version !== 2}<div class="approval"><div><strong>{pipeline.status === 'needs_plan_approval' ? 'The plan is ready for your team' : 'Review is ready for release approval'}</strong><p>{pipeline.status === 'needs_plan_approval' ? 'Review the plan, then let Design and Development continue.' : 'Review the findings and checks. Approval starts the local release preview and deployment plan.'}</p></div><button class="primary" disabled={!canAct} onclick={() => void control('approve')}>{pipeline.status === 'needs_plan_approval' ? 'Approve plan & continue' : 'Approve release preview'}</button></div>{/if}
        {#if ['paused','failed','blocked','scope_changed','needs_review','draft'].includes(pipeline.status)}
          <div class="approval attention"><div>
            <strong>{pipeline.status === 'scope_changed' ? 'New scope needs a fresh plan' : hitTurnLimit(feature.error) ? `${currentAgent} reached its turn limit` : `${currentAgent} needs attention`}</strong>
            <p>{pipeline.status === 'failed' ? 'Inspect the failed stage’s output. Retry starts this stage again with the current Studio settings; earlier outputs and saved workspace files are retained.' : pipeline.status === 'scope_changed' ? 'Review the team input, then restart from Ideation. Previous outputs remain in history.' : pipeline.status === 'paused' && pipeline.harness_version === 2 ? 'The same saved run resumes at its next tool boundary. A tool already in progress can finish while paused. Use Rerun in Agent version to start with new settings.' : 'Inspect the output, add decisions, or customize the agent before resuming.'}</p>
            {#if feature.error && !currentRun?.error}<details><summary>Error details</summary><pre>{feature.error}</pre></details>{/if}
          </div><button class="primary" disabled={!canAct || !enabled} onclick={() => void control('resume')}>{pipeline.status === 'failed' ? `Retry ${currentAgent}` : 'Resume workflow'}</button></div>
        {/if}
        {#if pipeline.current_stage === 'deployment' && pipeline.status === 'blocked' && releaseCheckIssues.length}
          <div class="approval attention"><div><strong>Release checks need attention</strong>{#each releaseCheckIssues as [name, value]}<p>{checkProblem(name, value)}</p>{/each}<button onclick={() => { selectedStage = 'deployment'; tab = 'checks'; }}>View check output</button></div><button disabled={!canAct || !enabled} onclick={() => void control('resume')}>Retry Deployment</button></div>
        {/if}
        {#if pipeline.harness_version === 2 && pipeline.status === 'blocked' && ['development', 'review', 'deployment'].includes(pipeline.current_stage)}
          <div class="approval attention"><div><strong>Approve this stage manually</strong><p>{pipeline.current_stage === 'deployment' ? 'Finish the existing local preview without providing evidence. The ZIP must match the reviewed source. This does not apply or remotely deploy changes.' : `Continue to ${pipeline.current_stage === 'development' ? 'Review' : 'Deployment'} without providing evidence.`} Your approval is recorded; check results remain visible.</p></div><button class="primary" disabled={!canAct || !enabled} onclick={() => void control('manual_approve')}>Manual Approve</button></div>
        {/if}
        <div class="content-header"><div><span class="agent-glyph">{stages.indexOf(stage) + 1}</span><h2>{names[stage]} <span>agent</span></h2>{#if run}<code>v {run.project.version.slice(0,8)}</code>{/if}</div><a href={studioUrl(stage, selected)}>Customize agent ↗</a></div>
        <div class="tabs" data-preview-navigation>{#each [['output','Output & handoff'],['code','Code'],['preview','Browser preview'],['library','Skills & artifacts'],['experiments','Experiments'],['checks','Checks'],['agent','Agent version'],['activity','Activity']] as [value,text]}<button class:active={tab === value} disabled={filesDirty && tab !== value} onclick={() => tab = value!}>{text}</button>{/each}</div>
        <section class="content">
          {#if tab === 'output'}
            {#if comparisonMode && comparisonRunA && comparisonRunB}
              <div class="comparison-controls">
                <div class="comparison-toolbar">
                  <label>Run A<select aria-label="Comparison Run A" value={comparisonA} onchange={e => { if (e.currentTarget.value !== comparisonB) comparisonA = e.currentTarget.value; }}>
                    {#each stageRuns as item, i (item.id)}<option value={item.id} disabled={item.id === comparisonB}>Run {i + 1} · {statusLabel(item.status)} · {item.project?.version?.slice(0,8) || 'version unavailable'}</option>{/each}
                  </select></label>
                  <label>Run B<select aria-label="Comparison Run B" value={comparisonB} onchange={e => { if (e.currentTarget.value !== comparisonA) comparisonB = e.currentTarget.value; }}>
                    {#each stageRuns as item, i (item.id)}<option value={item.id} disabled={item.id === comparisonA}>Run {i + 1} · {statusLabel(item.status)} · {item.project?.version?.slice(0,8) || 'version unavailable'}</option>{/each}
                  </select></label>
                  <button type="button" onclick={closeComparison}>Return to single-run view</button>
                </div>
                <p class="comparison-note">Comparison is limited to {names[stage]} runs. Runs from other stages are not selectable.</p>
                <StageRunComparison runA={comparisonRunA} runB={comparisonRunB} stageName={names[stage] || stage} />
              </div>
            {:else}
              {#if comparisonNotice}<p class="comparison-note" role="status">{comparisonNotice}</p>{/if}
              <div class="run-comparison-entry"><p class="comparison-note">Compare retained runs from this stage only.</p><button type="button" disabled={stageRuns.length < 2} onclick={openComparison}>Compare runs</button>{#if stageRuns.length < 2}<span class="comparison-note">Two runs of {names[stage]} are needed to compare outputs.</span>{/if}</div>
              {#if run?.harness_version === 2 && run.workflow_id}<LiveStage workflowId={run.workflow_id} saved={run.progress} />{/if}
              {#if run}<div class="result-meta"><span class="pill">{statusLabel(run.result?.verdict || run.status)}</span><select aria-label="Stage run history" value={run.id} onchange={e => historyId = e.currentTarget.value}>{#each pipeline.runs.filter((r: any) => r.stage === stage) as old, i (old.id)}<option value={old.id}>Run {i + 1} · {statusLabel(old.status)} · {old.project.version.slice(0,8)}</option>{/each}</select></div>{/if}
            {#if run?.result}<p class="summary">{run.result.summary}</p>{#each run.result.deliverables as item}<article class="deliverable"><h3>{item.title}</h3><div class="prose">{@html markdown(item.body)}</div></article>{/each}{#if run.result.acceptance_criteria.length}<h3>Acceptance criteria</h3><ul class="prose criteria">{#each run.result.acceptance_criteria as item}<li>{@html markdownInline(item)}</li>{/each}</ul>{/if}{#if run.result.questions.length}
              {#key selected + ':' + run.id}
                <StageQuestions featureId={selected} revision={feature.input_revision} {run} {canAct} {enabled} author={name}
                  manual={!!pipeline.approvals?.[stage]?.manual}
                  current={pipeline.status === 'blocked' && pipeline.current_stage === run.stage && pipeline.stages[run.stage]?.run_id === run.id && run.input_revision === feature.input_revision}
                  questions={run.result.questions.flatMap((item: string) => stage === 'deployment' && item === 'Resolve failing release checks before completing the preview.' && releaseCheckIssues.length ? releaseCheckIssues.map(([name, value]) => checkProblem(name, value)) : [item])}
                  help={stage === 'deployment' && releaseCheckIssues.length ? 'Open Checks for the failing output. You can ask the agent to explain the recorded issue below.' : ''}
                  onsubmitted={async () => { notice = 'Requirements added to Team input. Choose Resume workflow to plan the updated scope.'; await refresh(); }} />
              {/key}
            {/if}{#each run.result.findings as finding}<div class="finding"><strong>{finding.severity} · {finding.title}</strong><code>{finding.path}{finding.line ? ':' + finding.line : ''}</code><p>{finding.explanation}</p></div>{/each}{#if run.result.risks.length}<details open={expanded[`${selected}:${run.id}:risks`] ?? false} ontoggle={e => expanded[`${selected}:${run.id}:risks`] = e.currentTarget.open}><summary>Risks & considerations</summary><ul>{#each run.result.risks as item}<li>{item}</li>{/each}</ul></details>{/if}<div class="handoff"><span class="eyebrow">HANDOFF TO THE NEXT STAGE</span><p>{run.result.handoff}</p></div>
            {:else if run && ['queued','running'].includes(run.status)}<div class="empty"><div class="pulse"></div><h3>{names[stage]} is working</h3><p>{run.trace.at(-1)?.detail || 'Reading the feature context and prior handoffs…'}</p><p>Version {run.project.version.slice(0,8)} is saved for this run. You can keep collaborating while it works.</p></div>
            {:else if run?.status === 'failed'}
              <div class="empty failure">
                <h3>{names[stage]} {hitTurnLimit(run.error) ? 'reached its turn limit' : 'stopped before finishing'}</h3>
                <p>{hitTurnLimit(run.error) ? `This run used its ${turnBudget(run) ? turnBudget(run) + '-turn' : 'configured'} budget before producing a handoff.` : 'This run did not produce a final handoff. Open the error details to see what stopped it.'}</p>
                <p>Earlier stage outputs and saved workspace files are retained. This run’s completed tool activity is available under Agent version.</p>
                <p class="fine">Saved agent version {run.project.version.slice(0,8)}{turnBudget(run) ? ` · ${turnBudget(run)} model turns` : ''}. Studio edits apply to the next run.</p>
                <a class="text-link" href={studioUrl(stage, selected)}>Open {names[stage]} settings in Studio ↗</a>
                <p>{hitTurnLimit(run.error) ? 'Review the agent’s scope and turn budget, then retry with its current definition.' : 'Address the error, then retry the stage with its current definition.'} This failed run stays in history.</p>
                <details><summary>Error details</summary><pre>{run.error || 'No error details were recorded.'}</pre></details>
              </div>
            {:else}<div class="empty"><h3>{names[stage]} · {statusLabel(pipeline.stages[stage].status)}</h3><p>Its output and handoff will appear here when this stage runs.</p></div>{/if}
            {#if pipeline.status === 'complete' && stage === 'deployment' && pipeline.release}<div class="release">{#if feature.experiment_id}<h3>Experiment complete</h3><p>Experiment output stays isolated. Compare this result in the Experiments tab before adopting source changes. This is a local preview; nothing has been remotely deployed.</p>{:else}<h3>Local release preview ready</h3><p>This is a local preview; nothing has been remotely deployed.</p>{#key selected}<IntegrationPanel featureId={selected} revision={feature.input_revision} author={name} {canAct} onchanged={() => void refresh()}/>{/key}{/if}<details><summary>Download feature files</summary><p>{pipeline.release.file_count} source files packaged.</p><a class="primary" href={pipeline.release.download}>Download ZIP</a></details></div>{/if}
            {/if}
          {:else if tab === 'code'}<p class="directory"><strong>Feature workspace</strong><code>{feature.worktree}</code><span>Shared saves stay isolated until reviewed and applied.</span></p>{#key selected}<FeatureFiles featureId={selected} author={name} changes={codeChanges} disabled={!joined || !connected} onDirty={value => filesDirty = value}/>{/key}
          {:else if tab === 'preview'}{#key selected}<BrowserPreview featureId={selected} />{/key}
          {:else if tab === 'experiments'}{#key selected}<ExperimentWorkspace featureId={selected} revision={feature.input_revision} author={name} {canAct} sourceStatus={pipeline.status} experimentId={feature.experiment_id} />{/key}
          {:else if tab === 'library'}{#key selected}<SharedLibrary featureId={selected} runs={pipeline.runs} {changes} />{/key}
          {:else if tab === 'checks'}<h3>Actual local check results</h3><p class="muted">Checks run against the feature’s isolated source. Commands use installed dependencies; credentials are not passed to check processes. The legacy browser_tests check runs the configured collaboration script, not a rendered browser.</p>{#each Object.entries(pipeline.checks) as [check, value] (check)}{@const result = value as any}<details class="check" open={expanded[`${selected}:check:${check}`] ?? result.status !== 'passed'} ontoggle={e => expanded[`${selected}:check:${check}`] = e.currentTarget.open}><summary><strong>{check.replaceAll('_',' ')}</strong><span class:success={result.status === 'passed'} class:warning={result.status !== 'passed'}>{statusLabel(result.status)}</span></summary><p class="fine">{result.command?.join(' ') || 'Python syntax parser'} · {when(result.started_at)}</p><div class="check-output-row"><pre>{result.output}</pre><CopyOutput output={result.output} /></div>{#if result.truncated}<p class="fine">Showing the final 48 KB of output.</p>{/if}</details>{:else}<div class="empty"><p>Development records syntax, tests, and available type checks here. Review can run additional checks.</p></div>{/each}
          {:else if tab === 'agent'}<h3>{run ? 'The exact definition used for this run' : 'This agent has not run yet'}</h3><p class="muted">{feature.experiment_id ? 'This experiment keeps its captured agent definitions for every stage and rerun. Studio edits apply to future experiments.' : 'Studio changes apply when an agent is next started. Rerun a stage to use a newer version; downstream outputs and approvals will be reset.'}</p><a class="text-link" href={studioUrl(stage, selected)}>Edit {names[stage]} in Agent Studio ↗</a>{#if run}<p class="fine">Version {run.project.version} · workspace revision {run.project.revision}</p>{#each Object.entries(run.project.files) as [path, source] (path)}<details open={expanded[`${selected}:agent:${stage}:${path}`] ?? false} ontoggle={e => expanded[`${selected}:agent:${stage}:${path}`] = e.currentTarget.open}><summary>{path}</summary><pre>{String(source)}</pre></details>{/each}<button disabled={!canAct || pipeline.status === 'running'} onclick={() => void control('rerun', stage)}>Rerun {names[stage]} with {feature.experiment_id ? 'frozen' : 'current'} definition</button><h3>Tool activity</h3>{#each run.trace as item}<div class="trace"><code>{item.name}</code><span>{item.detail}</span></div>{/each}{/if}
          {:else}{#if pipeline.execution?.workflow_id}<LivePipeline workflowId={pipeline.execution.workflow_id} />{/if}<h3>Team activity & agent handoffs</h3>{#each [...feature.activity].reverse() as event}<div class="event"><time>{when(event.time)}</time><div><strong>{event.author}</strong><p>{event.text}</p></div></div>{/each}{/if}
        </section>
      {:else}<div class="empty"><p>{loaded ? 'Select a feature to join your team’s work.' : 'Loading features…'}</p></div>{/if}
    </main>
    <aside class="discussion"><div class="discussion-head"><span class="eyebrow">PEOPLE + AGENTS</span><h2>Team input</h2><p>Discuss progress or add requirements. Keep decisions with the feature.</p></div>{#if feature && !creating && joined}<div class="messages">{#each feature.inputs as item}<article><header><strong>{item.author}</strong><time>{when(item.time)}</time></header><small>{item.kind}</small><p>{item.text}</p></article>{:else}<p class="muted">Your team’s comments and decisions will appear here.</p>{/each}</div><form class="composer" onsubmit={post}><label>Input type<select bind:value={inputKind}><option value="comment">Comment · discussion only</option>{#if pipeline.harness_version === 2 && ['running','paused'].includes(pipeline.status)}<option value="steering">Live feedback · next model turn</option>{/if}<option value="requirement">Requirement · updates scope</option><option value="decision">Decision · updates scope</option><option value="question">Question · updates scope</option></select></label><textarea aria-label="Team input" bind:value={comment} rows="4" maxlength="4000" placeholder="Add context for the team…" required></textarea><div><span>{name}</span><button class="primary" disabled={!canAct || !comment.trim()} type="submit">Post input</button></div><p>{inputKind === 'comment' ? 'Comments stay in the discussion without interrupting agents.' : 'Scope changes pause agents and require a new plan and approval. Existing files are retained.'}</p></form>{:else}<div class="team-intro"><h3>Join at any stage</h3><p>Teammates share one feature workspace, see which agent is working, and can inspect or customize its definition.</p><div>Ideation → Planner → Design → Development → Review → Deployment</div></div>{/if}</aside>
  </div>
</div>

<style>
  .comparison-controls { display:grid; gap:12px; min-width:0; }
  .comparison-toolbar { display:flex; align-items:end; flex-wrap:wrap; gap:12px; }
  .comparison-toolbar label { flex:1 1 180px; min-width:0; margin:0; color:var(--color-content-primary); }
  .comparison-toolbar select { min-width:0; }
  .comparison-note { color:var(--color-content-secondary); font-size:11px; line-height:1.7; margin:0; }
  .run-comparison-entry { display:flex; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:14px; }
  .comparison-toolbar button { min-height:40px; }
  .run-comparison-entry button:focus-visible,.comparison-toolbar button:focus-visible { outline:2px solid var(--color-content-brand); outline-offset:2px; }
  @media(max-width:600px) { .comparison-toolbar { align-items:stretch; flex-direction:column; } .comparison-toolbar label,.comparison-toolbar button { width:100%; } }
  .feature-list .draft-tag { display:inline-block; width:fit-content; padding:2px 7px; border:1px solid var(--line-2); border-radius:4px; color:var(--accent); font-size:10px; }
  .feature-list>.draft-open { min-width:0; text-align:left; padding:10px 8px; background:transparent; border-color:transparent; margin-bottom:7px; }
  .feature-list>.draft-open strong { overflow-wrap:anywhere; }
  .feature-list>.draft-open span { margin-top:6px; }
  .create .form-actions>.draft-delete { padding:7px 11px; color:var(--color-content-danger); background:var(--color-interactive-tertiary); border-color:var(--color-border-secondary); }
  .create .form-actions>.draft-delete:hover:enabled { background:var(--color-interactive-tertiary-hover); border-color:var(--color-content-danger); }
  .create .form-actions>.draft-delete:active:enabled { background:var(--color-interactive-tertiary-press); }
  .create .form-actions>.draft-delete:disabled { opacity:.45; cursor:not-allowed; }
  .form-actions { flex-wrap:wrap; }
  .create fieldset { border:0; padding:0; margin:0; min-width:0; }
  .failure{max-width:650px;margin:auto;padding-top:35px;padding-bottom:35px}.failure details{text-align:left;margin-top:25px}.failure pre{white-space:pre-wrap;overflow-wrap:anywhere}
  .work-app{height:100dvh;display:flex;flex-direction:column}.topbar{height:64px;flex:none;display:flex;align-items:center;gap:35px;padding:0 25px;border-bottom:1px solid var(--line);background:var(--panel)}.brand{font-size:23px;display:flex;gap:10px;align-items:center;color:var(--ink);text-decoration:none}.brand strong{font-size:16px;letter-spacing:-.4px}nav{display:flex;gap:25px;height:100%;align-items:center}nav a{font-size:12px;text-decoration:none;color:var(--muted);height:100%;display:flex;align-items:center;border-bottom:2px solid transparent}nav a.active{color:var(--ink);border-color:var(--accent)}.connection{margin-left:auto;display:flex;gap:8px;align-items:center;font-size:10px;color:var(--muted)}.connection span{margin-left:15px}.connection i{width:6px;height:6px;background:var(--warn);border-radius:50%}.connection i.connected{background:var(--good)}.layout{display:grid;grid-template-columns:220px minmax(400px,1fr) 310px;flex:1;min-height:0}.feature-list{padding:25px 13px 20px;border-right:1px solid var(--line);background:var(--panel);display:flex;flex-direction:column;overflow:auto}.list-title{font-size:9px;letter-spacing:1.4px;color:var(--muted);display:flex;justify-content:space-between;padding:0 10px 16px}.feature-search,.feature-filter{font-size:9px;gap:5px;margin:0 8px 10px}.feature-search input,.feature-filter select{font-size:10px;padding:7px 8px}button:not(.io-button){border:1px solid var(--line-2);border-radius:7px;padding:8px 11px;background:var(--panel-2);font-size:11px}button:disabled{opacity:.4}button:not(.io-button):hover:enabled{border-color:var(--accent)}.primary:not(.io-button--primary){background:var(--accent);border-color:var(--accent);color:var(--accent-ink);font-size:11px;font-weight:550;padding:9px 12px;border-radius:7px;text-decoration:none;display:inline-block}.new{margin-bottom:20px}.new.io-button--primary{background:var(--color-interactive-primary);border-color:var(--color-interactive-primary);color:var(--color-content-inverse-primary)}.new.io-button--primary:hover:not(:disabled){background:var(--color-interactive-primary-hover);border-color:var(--color-interactive-primary-hover)}.new.io-button--primary:active:not(:disabled){background:var(--color-interactive-primary-press)}.feature-list>button:not(.new){text-align:left;padding:14px 10px;background:transparent;border-color:transparent;margin-bottom:7px}.feature-list>button.selected{background:var(--accent-soft);border-color:color-mix(in srgb,var(--accent) 25%,var(--line))}.feature-list strong{font-size:12px;font-weight:550;display:block;overflow-wrap:anywhere}.feature-list button>span{font-size:9px;color:var(--muted);display:block;margin-top:8px;line-height:1.6}.mini-progress{display:flex;gap:4px;margin-top:13px}.mini-progress i{height:3px;flex:1;background:var(--line-2);border-radius:2px}.mini-progress i[data-tone=success]{background:var(--status-fill-success)}.mini-progress i[data-tone=running]{background:var(--status-fill-running);animation:pulse 2s infinite}.mini-progress i[data-tone=attention]{background:var(--status-fill-warning)}.mini-progress i[data-tone=danger]{background:var(--status-fill-danger)}.mini-progress i[data-tone=paused]{background:var(--faint)}.team{margin-top:auto;padding:35px 10px 0}.team>div{display:flex;gap:10px;align-items:center;font-size:11px;margin-top:14px}.team b{width:25px;height:25px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;border-radius:50%;font-size:10px;font-weight:500}.team small{color:var(--muted);margin-left:auto}.eyebrow{font-size:9px;letter-spacing:1.4px;color:var(--muted)}main{min-width:0;overflow:auto}.feature-header{padding:30px 30px 20px;display:flex;align-items:center;gap:20px;justify-content:space-between}h1{font-size:26px;line-height:1.25;letter-spacing:-.7px;font-weight:550;margin:12px 0;overflow-wrap:anywhere}.feature-header button{flex:none}.feature-header p{display:flex;gap:15px;align-items:center;margin:10px 0 0;flex-wrap:wrap}.feature-header code{font-size:10px;color:var(--accent);overflow-wrap:anywhere}.feature-header p span{font-size:9px;color:var(--muted)}.progress-heading{display:flex;align-items:center;gap:15px;margin:0 30px 15px;font-size:10px;color:var(--muted)}.progress-heading strong{color:var(--ink);font-size:11px;font-weight:550}.progress-heading button{margin-left:auto;padding:5px 9px;font-size:10px}.stage-strip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));margin:0 30px 25px;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--panel)}.stage-strip button{--tone-fill:transparent;--tone-ink:var(--muted);border:0;border-right:1px solid var(--line);border-radius:0;background:var(--panel);padding:18px 6px 14px;display:flex;flex-direction:column;align-items:center;gap:8px;position:relative;transition:background-color 120ms ease}.stage-strip button:last-child{border-right:0}.stage-strip button:first-child{border-radius:9px 0 0 9px}.stage-strip button:last-child{border-radius:0 9px 9px 0}.stage-strip button::before{content:'';position:absolute;inset:0 0 auto;height:4px;background:var(--tone-fill)}.stage-strip button[data-tone=success]{--tone-fill:var(--status-fill-success);--tone-ink:var(--good);background:color-mix(in srgb,var(--status-fill-success) 7%,var(--panel))}.stage-strip button[data-tone=running]{--tone-fill:var(--status-fill-running);--tone-ink:var(--accent);background:color-mix(in srgb,var(--status-fill-running) 7%,var(--panel))}.stage-strip button[data-tone=running]::before{animation:pulse 2s infinite}.stage-strip button[data-tone=attention]{--tone-fill:var(--status-fill-warning);--tone-ink:var(--warn);background:color-mix(in srgb,var(--status-fill-warning) 12%,var(--panel))}.stage-strip button[data-tone=danger]{--tone-fill:var(--status-fill-danger);--tone-ink:var(--bad);background:color-mix(in srgb,var(--status-fill-danger) 9%,var(--panel))}.stage-strip button[data-tone=paused]{--tone-fill:var(--faint)}.stage-strip strong{font-size:11px;font-weight:550;color:var(--ink)}.stage-strip .stage-status{font-size:9px;font-weight:550;color:var(--tone-ink);text-transform:capitalize;padding:2px 7px;border-radius:999px;background:color-mix(in srgb,var(--tone-fill) 16%,transparent)}.stage-strip button:hover:enabled{filter:brightness(.98)}.stage-strip .selected{box-shadow:inset 0 0 0 2px var(--accent)}.stage-strip .selected strong{color:var(--accent)}@media(prefers-reduced-motion:reduce){.stage-strip button{transition:none}.stage-strip button[data-tone=running]::before{animation:none}}.approval{margin:0 30px 25px;padding:17px 20px;border:1px solid color-mix(in srgb,var(--accent) 30%,var(--line));background:var(--accent-soft);border-radius:9px;display:flex;align-items:center;justify-content:space-between;gap:20px}.approval strong{font-size:12px;font-weight:550}.approval p{font-size:11px;color:var(--muted);line-height:1.8;margin:6px 0 0}.approval button{flex:none}.attention{border-color:color-mix(in srgb,var(--warn) 25%,var(--line));background:var(--panel-2)}.content-header{display:flex;justify-content:space-between;align-items:center;padding:5px 30px 16px;gap:15px}.content-header>div{display:flex;gap:10px;align-items:center}.agent-glyph{display:grid;place-items:center;width:30px;height:30px;background:var(--panel-2);border:1px solid var(--line);border-radius:8px;font-size:10px;color:var(--accent)}h2{font-size:16px;font-weight:550;margin:0}.content-header h2 span{color:var(--muted);font-weight:400}.content-header code{font-size:9px;color:var(--muted)}.content-header>a,.text-link{font-size:11px;color:var(--accent);text-decoration:none}.tabs{overflow-x:auto;display:flex;gap:20px;border-bottom:1px solid var(--line);padding:0 30px}.tabs button{padding:12px 0;border:0;border-bottom:2px solid transparent;border-radius:0;background:none;color:var(--muted)}.tabs button.active{border-bottom-color:var(--accent);color:var(--ink)}.content{padding:25px 30px 55px}h3{font-size:13px;font-weight:550;margin:23px 0 10px}.summary{font-size:14px;line-height:1.85;white-space:pre-wrap}.result-meta{display:flex;justify-content:space-between;align-items:center;gap:15px}.pill{font-size:9px;padding:4px 8px;border:1px solid var(--line-2);border-radius:5px;color:var(--accent)}.result-meta select{width:auto;font-size:10px;padding:6px 9px}.deliverable{border-bottom:1px solid var(--line);padding:0 0 20px}.prose{font-size:14px;line-height:1.7;color:var(--ink);overflow-wrap:anywhere;max-width:78ch}.prose :global(p){margin:0 0 12px}.prose :global(p:last-child){margin-bottom:0}.prose :global(ol),.prose :global(ul){margin:0 0 12px;padding-left:22px}.prose :global(li){margin:0 0 8px;padding-left:2px}.prose :global(li::marker){color:var(--muted)}.prose :global(b){font-weight:600}.prose :global(code){font-family:var(--mono);font-size:.86em;padding:1px 5px;border-radius:4px;background:var(--panel-3);color:var(--ink);overflow-wrap:anywhere}.prose :global(pre){font-size:12px;margin:0 0 12px}.prose :global(h4){font-size:14px;font-weight:600;margin:16px 0 8px}ul.criteria{padding-left:22px;line-height:1.7}.handoff p{white-space:pre-wrap;line-height:1.85;font-size:12px;color:var(--muted);overflow-wrap:anywhere}.handoff{background:var(--panel-2);border:1px solid var(--line);border-radius:9px;padding:19px;margin-top:25px}.handoff p{margin-bottom:0}.questions{padding:16px 20px;border:1px solid var(--warn);border-radius:8px;margin-top:20px}.questions h3{margin:0}.questions p{font-size:11px;color:var(--muted)}ul{padding-left:20px;font-size:12px;line-height:1.9;color:var(--muted)}.finding{padding:16px;border:1px solid var(--line);border-radius:7px;margin-top:12px;font-size:12px}.finding code{display:block;font-size:10px;color:var(--accent);margin-top:7px}.finding p{color:var(--muted);line-height:1.8}.empty{padding:70px 25px;text-align:center;color:var(--muted)}.empty h3{font-size:17px;color:var(--ink)}.empty p{font-size:12px;line-height:1.8}.pulse{width:12px;height:12px;border-radius:50%;background:var(--accent);margin:auto;animation:pulse 2s infinite}.muted{color:var(--muted);font-size:12px;line-height:1.8}.fine{color:var(--muted);font-size:10px;line-height:1.8;overflow-wrap:anywhere}.warning,.error{color:var(--warn)}.success{color:var(--good)}details{border-top:1px solid var(--line);padding:16px 0;font-size:12px}summary{cursor:pointer;color:var(--muted)}pre{padding:16px;background:var(--panel-2);font-size:11px;overflow:auto;max-height:450px;line-height:1.8;border-radius:7px}.check summary{display:flex;justify-content:space-between;gap:20px;font-size:11px;text-transform:capitalize}.check-output-row{display:flex;align-items:flex-start;gap:10px;flex-wrap:wrap}.check-output-row pre{flex:1 1 450px;min-width:0;margin:0}.directory{padding:15px;border:1px solid var(--line);border-radius:8px;display:flex;flex-direction:column;gap:8px;font-size:11px}.directory code{font-size:10px;color:var(--accent);overflow-wrap:anywhere}.directory span{font-size:10px;color:var(--muted)}.event{display:flex;gap:20px;padding:14px 0;border-bottom:1px solid var(--line);font-size:11px}.event time{color:var(--muted);font-size:9px;min-width:55px}.event strong{font-weight:550}.event p{color:var(--muted);margin:5px 0 0;line-height:1.8}.trace{display:flex;gap:20px;padding:8px 0;border-bottom:1px solid var(--line);font-size:10px}.trace span{color:var(--muted)}.release{border:1px solid var(--line);border-radius:10px;padding:20px;margin-top:25px}.release h3{margin-top:0}.release p{font-size:12px;line-height:1.8;color:var(--muted)}.banner{margin:15px 25px;padding:12px 15px;border:1px solid var(--line-2);border-radius:8px;font-size:12px;line-height:1.8;white-space:pre-wrap}.banner button{margin-left:15px}.create{padding:45px 38px;max-width:780px;margin:auto}label{display:flex;flex-direction:column;gap:8px;font-size:11px;margin-bottom:18px}input,select,textarea{width:100%;border:1px solid var(--line-2);border-radius:7px;background:var(--panel);padding:10px 12px;font-size:12px}textarea{resize:vertical;line-height:1.8}input::placeholder,textarea::placeholder{color:var(--faint)}.create form{margin-top:30px}.two{display:grid;grid-template-columns:1fr 1fr;gap:20px}.form-actions{display:flex;gap:20px;align-items:center;justify-content:space-between;margin-top:25px}.discussion{border-left:1px solid var(--line);background:var(--panel);display:flex;flex-direction:column;min-height:0}.discussion-head{padding:29px 22px 20px;border-bottom:1px solid var(--line)}.discussion-head h2{margin:12px 0}.discussion-head p{font-size:11px;color:var(--muted);line-height:1.8;margin:0}.messages{padding:0 22px;overflow:auto;flex:1;min-height:150px}.messages article{padding:20px 0;border-bottom:1px solid var(--line)}.messages header{display:flex;justify-content:space-between;font-size:11px}.messages time{color:var(--faint);font-size:9px}.messages small{display:block;color:var(--accent);text-transform:uppercase;font-size:8px;letter-spacing:.7px;margin-top:7px}.messages p{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.85;color:var(--muted);margin:7px 0 0}.composer{border:1px solid var(--line-2);border-radius:9px;background:var(--panel-2);margin:15px;padding:13px}.composer label{font-size:9px;gap:6px;margin-bottom:9px}.composer select{font-size:10px;padding:7px 9px}.composer textarea{font-size:12px;border:0;background:transparent;padding:0;max-height:250px}.composer>div{display:flex;align-items:center;justify-content:space-between;margin-top:10px}.composer span{font-size:10px;color:var(--muted)}.composer p{font-size:9px;color:var(--muted);line-height:1.8;margin:13px 0 0}.team-intro{padding:35px 25px}.team-intro h3{font-size:14px;margin-top:0}.team-intro p,.team-intro div{font-size:12px;color:var(--muted);line-height:1.9}.team-intro div{padding-top:20px;color:var(--accent);font-size:11px}.side-empty{padding:12px;font-size:11px}.approval-inbox{margin:0 0 14px;padding:11px 9px;border:1px solid var(--line);border-radius:8px;background:var(--panel-2);min-width:0}.approval-inbox h2{font-size:10px;line-height:1.4;margin:0 0 8px;overflow-wrap:anywhere}.approval-inbox ul{list-style:none;padding:0;margin:0}.approval-inbox li+li{border-top:1px solid var(--line);margin-top:7px;padding-top:7px}.approval-inbox a{display:block;min-width:0;color:var(--ink);text-decoration:none;overflow-wrap:anywhere}.approval-inbox a:hover strong{text-decoration:underline;color:var(--accent)}.approval-inbox a strong,.approval-inbox a span,.approval-inbox a small{display:block;font-size:10px;line-height:1.5;overflow-wrap:anywhere}.approval-inbox a span{color:var(--accent);margin-top:2px}.approval-inbox a small{color:var(--muted);font-size:9px}.approval-inbox>p{font-size:10px;margin:0;overflow-wrap:anywhere}@media(min-width:1700px){.layout{grid-template-columns:240px minmax(400px,1fr) 350px}.content{max-width:1100px;margin:auto}}@media(max-width:1150px){.layout{grid-template-columns:180px minmax(360px,1fr) 270px}.feature-header,.content{padding-left:20px;padding-right:20px}.stage-strip,.progress-heading,.approval{margin-left:20px;margin-right:20px}.stage-strip strong{font-size:10px}.stage-strip .stage-status{font-size:8px}.content-header{padding-left:20px;padding-right:20px}.tabs{overflow-x:auto;padding:0 20px;gap:15px}.approval{flex-wrap:wrap}}@media(max-width:900px){.layout{grid-template-columns:180px minmax(0,1fr);overflow:auto}.feature-list{grid-row:1/3}.discussion{grid-column:2;border-top:1px solid var(--line);min-height:450px}main{overflow:visible}.messages{max-height:400px}.connection span{display:none}}@media(max-width:600px){.layout{display:block}.feature-list{max-height:230px}.team{display:none}.topbar{padding:0 15px;gap:20px}.connection{display:none}.feature-header{flex-wrap:wrap}.stage-strip{grid-template-columns:repeat(3,1fr)}.stage-strip button{border-bottom:1px solid var(--line)}.stage-strip button:nth-child(n){border-radius:0}.stage-strip button:nth-child(1){border-radius:9px 0 0 0}.stage-strip button:nth-child(3){border-radius:0 9px 0 0;border-right:0}.stage-strip button:nth-child(4){border-radius:0 0 0 9px}.stage-strip button:nth-child(6){border-radius:0 0 9px 0}.stage-strip button:nth-child(n+4){border-bottom:0}.content-header{flex-wrap:wrap}.tabs{overflow-x:auto;gap:12px}.tabs button{font-size:10px}.create{padding:30px 20px}.two{grid-template-columns:1fr;gap:0}.form-actions{flex-wrap:wrap}}
  .create { container-type:inline-size; }
  .source-row { grid-template-columns:minmax(0, 1.7fr) minmax(0, 1fr); }
  .source-hint { margin:-6px 0 24px; }
  .idea-row { display:grid; grid-template-columns:minmax(0, 1fr) auto; gap:12px; align-items:end; }
  .idea-row label { margin:0; min-width:0; }
  .idea-row button,.create .form-actions button { min-height:44px; }
  .create input,.create select { min-height:44px; min-width:0; }
  .idea-help { margin:8px 0 24px; }
  .feature-details summary { min-height:44px; align-content:center; color:var(--color-content-primary); }
  .feature-details summary span { display:block; margin:5px 0 0 16px; color:var(--color-content-secondary); font-size:11px; }
  .details-fields { padding-top:18px; }
  .details-fields input,
  .details-fields textarea,
  .composer textarea { border:1px solid var(--color-border-secondary); border-radius:4px; }
  .details-fields input:focus,
  .details-fields textarea:focus,
  .composer textarea:focus { outline:2px solid var(--color-content-brand); outline-offset:2px; }
  .details-fields label:last-child { margin-bottom:0; }
  @container (max-width:480px) {
    .source-row { grid-template-columns:minmax(0,1fr); gap:0; }
    .idea-row { grid-template-columns:minmax(0,1fr); }
    .idea-row button { justify-self:start; }
  }
  .feature-header { display:grid; grid-template-columns:minmax(0,1fr) minmax(360px,460px); gap:24px; align-items:start; }
  .feature-heading { min-width:0; }
  .feature-heading h1 { overflow-wrap:anywhere; }
  .feature-heading>p { flex-wrap:wrap; overflow-wrap:anywhere; }
  .overview-status { margin:22px 0 18px; }
  .overview-status h2 { font-size:15px; line-height:1.5; margin:8px 0; }
  .feature-header .overview-status p { display:block; color:var(--muted); font-size:12px; line-height:1.75; margin:0; }
  @media(max-width:1300px) { .feature-header { grid-template-columns:minmax(0,1fr); } }
</style>
