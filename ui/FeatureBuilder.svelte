<script lang="ts">
  import { sessionName } from './shared/identity';
  import { workspaceConnection } from './shared/workspaceConnection';
  import { onMount } from 'svelte';
  import FeatureFiles from './shared/FeatureFiles.svelte';
  import { api, newId, active, label } from './shared/features';
  import type { Feature, FeatureSummary } from './shared/features';

  const name = sessionName();
  const joined = !!name;
  let connection = $state('connecting'), peers = $state<{ id: string; name: string }[]>([]);
  let features = $state<FeatureSummary[]>([]), selected = $state(new URLSearchParams(location.search).get('feature') || '');
  let feature = $state<Feature | null>(null), enabled = $state(true), loaded = $state(false);
  let creating = $state(false), busy = $state(false), error = $state(''), notice = $state('');
  let tab = $state('brief'), changes = $state(0), filesDirty = $state(false);
  let repository = $state(''), repoInfo = $state<{ path: string; current_branch: string; dirty: boolean; branches: string[] } | null>(null);
  let title = $state(''), description = $state(''), acceptance = $state(''), branch = $state(''), base = $state('HEAD');
  let editBrief = $state(false), editRevision = $state(0), editTitle = $state(''), editDescription = $state(''), editAcceptance = $state('');
  let input = $state(''), kind = $state('requirement'), planId = $state('');
  let socket: ReturnType<typeof workspaceConnection> | null = null;
  let refreshSeq = 0;
  const running = $derived(feature?.jobs.findLast(active));
  const latest = $derived(feature?.plans.at(-1));
  const plan = $derived(feature?.plans.find(p => p.id === planId) || latest);
  const canApprove = $derived(!!plan && plan.id === latest?.id && plan.input_revision === feature?.input_revision && !plan.plan.open_questions.length && !running && feature?.jobs.find(j => j.id === plan.id)?.status === 'completed');
  const writable = $derived(joined && connection === 'connected' && !busy);

  async function refresh() {
    const seq = ++refreshSeq;
    try {
      const state = await api('/state');
      if (seq !== refreshSeq) return;
      features = state.features; enabled = state.enabled; loaded = true;
      if (!repository && state.demo_repository) repository = state.demo_repository;
      if (!selected && features.length && !creating) selected = features[0]!.id;
      if (selected) {
        const id = selected;
        const next = await api<Feature>(`/${id}`);
        if (seq !== refreshSeq || selected !== id) return;
        feature = next; changes += 1;
      }
    } catch (cause) { error = (cause as Error).message; }
  }
  async function choose(id: string) {
    if (filesDirty) return;
    selected = id; creating = false; feature = null; editBrief = false; input = ''; planId = ''; tab = 'brief'; error = ''; notice = '';
    const url = new URL(location.href); url.searchParams.set('view', 'features'); url.searchParams.set('feature', id); history.replaceState(null, '', url);
    await refresh();
  }
  async function perform(action: () => Promise<void>) {
    busy = true; error = ''; notice = '';
    try { await action(); } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  async function inspectRepository() {
    await perform(async () => { repoInfo = await api('/repository', { path: repository }); repository = repoInfo!.path; });
  }
  async function createDemo() {
    await perform(async () => { repoInfo = await api('/demo', {}); repository = repoInfo!.path; base = 'main'; notice = 'Demo project ready. Describe a feature and choose a new branch.'; });
  }
  function startNew() { creating = true; error = ''; notice = ''; editBrief = false; }
  async function createFeature(event: SubmitEvent) {
    event.preventDefault();
    await perform(async () => {
      const result = await api<Feature>('', { id: newId(), title, description, acceptance, repository, base, branch, author: name });
      title = description = acceptance = branch = '';
      await choose(result.id);
    });
  }
  function beginEdit() {
    if (!feature) return;
    editTitle = feature.title; editDescription = feature.description; editAcceptance = feature.acceptance;
    editRevision = feature.input_revision; editBrief = true;
  }
  async function saveBrief(event: SubmitEvent) {
    event.preventDefault();
    await perform(async () => {
      await api(`/${selected}/brief`, { id: newId(), revision: editRevision, author: name, title: editTitle, description: editDescription, acceptance: editAcceptance });
      editBrief = false; await refresh();
    });
  }
  async function postInput(event: SubmitEvent) {
    event.preventDefault(); const text = input;
    await perform(async () => { await api(`/${selected}/inputs`, { id: newId(), author: name, kind, text }); if (input === text) input = ''; await refresh(); });
  }
  async function act(route: string) {
    await perform(async () => { await api(`/${selected}/${route}`, { id: newId(), revision: feature!.input_revision, author: name }); await refresh(); });
  }
  async function summarize() { await act('plan'); tab = 'plan'; planId = ''; }
  async function startCoding() { await act('code'); tab = 'code'; }
  async function stop() { await perform(async () => { await api(`/${selected}/jobs/${running!.id}/cancel`, {}); await refresh(); }); }
  async function checkSyntax() { await perform(async () => { await api(`/${selected}/syntax`, {}); await refresh(); }); }
  async function share() {
    try { const url = new URL(location.href); url.searchParams.set('view', 'features'); url.searchParams.set('feature', selected); await navigator.clipboard.writeText(url.toString()); notice = 'Feature link copied. Open it in another browser session.'; }
    catch { notice = 'Share this page’s URL with the other browser session.'; }
  }
  function time(value: number) { return new Date(value * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }

  onMount(() => {
    socket = workspaceConnection(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/workspace/connect?name=${encodeURIComponent(name || 'Visitor')}`, {
      open: () => { connection = 'connected'; void refresh(); socket?.send({ type: 'presence', name: name || 'Visitor', file: 'Feature builder' }); },
      message: event => {
        const message = JSON.parse(event.data);
        if (message.type === 'presence') peers = message.peers;
        if (message.type === 'feature-changed') void refresh();
      },
      offline: () => { connection = 'reconnecting'; peers = []; },
    });
    void refresh();
    const poll = setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 5000);
    return () => { clearInterval(poll); socket?.close(); };
  });
</script>

<svelte:head><title>Code Studio · Feature builder run</title></svelte:head>
<div class="app-shell">
  <header class="topbar"><a class="brand" href="/" onclick={e => { if (filesDirty) { e.preventDefault(); notice = "Save or reload your file draft before leaving this feature."; } }}>code<span>studio</span><b>FEATURE BUILDER RUNS</b></a><div class="top-actions"><span class="connection" class:online={connection === 'connected'}><i></i>{connection}</span><span>{peers.length} online</span><a href="?view=studio&agent=planner" onclick={e => { if (filesDirty) { e.preventDefault(); notice = "Save or reload your file draft before opening Agent studio."; } }}>Edit Planner agent ↗</a></div></header>
  <div class="layout">
    <aside class="sidebar">
      <div class="side-heading"><span>YOUR FEATURES</span><span>{features.length}</span></div>
      <button class="new-button" onclick={startNew} disabled={!joined || filesDirty}>＋ New feature</button>
      <nav aria-label="Features">{#each features as item}<button class:selected={item.id === selected && !creating} disabled={filesDirty} onclick={() => void choose(item.id)}><strong>{item.title}</strong><code>{item.branch}</code><div><span class="status" class:ready={['approved', 'code_ready'].includes(item.status)}>{label(item.status)}</span><span>{item.input_count} inputs</span></div></button>{/each}</nav>
      {#if !features.length}<p class="side-empty">Start with a brief.<br>Build it together.</p>{/if}
      <div class="people"><span class="eyebrow">IN THE WORKSPACE</span>{#each peers as peer}<div><span class="avatar">{peer.name.slice(0, 1).toUpperCase()}</span>{peer.name}{#if peer.name === name}<small>you</small>{/if}</div>{/each}</div>
    </aside>

    <main>
      {#if error}<div class="banner error" role="alert">{error}<button onclick={() => { error = ''; void refresh(); }}>Retry</button></div>{/if}
      {#if notice}<div class="banner" role="status">{notice}</div>{/if}
      {#if !enabled && loaded}<div class="banner">Shared collaboration is available. Start <code>just dev</code> with your OpenAI key to run agents.</div>{/if}
      {#if creating || (loaded && !features.length)}
        <section class="new-feature"><span class="eyebrow">A NEW PIECE OF WORK</span><h1>What should we build?</h1><p class="muted">Create a feature branch and a shared brief. Invite others before asking the agent to plan.</p>
          <form onsubmit={createFeature}>
            <div class="repository"><label>Local Git repository<input bind:value={repository} oninput={() => repoInfo = null} placeholder="/Users/you/projects/my-project" required maxlength="1000" /></label><div class="row"><button type="button" onclick={() => void inspectRepository()} disabled={!repository || !writable}>Check repository</button><button type="button" onclick={() => void createDemo()} disabled={!writable}>Create demo project</button></div>{#if repoInfo}<p class="fine">Ready · {repoInfo.current_branch || 'detached HEAD'}{repoInfo.dirty ? ' · Original checkout has uncommitted edits.' : ''}</p>{/if}</div>
            <div class="two-fields"><label>New branch<input bind:value={branch} placeholder="feature/complete-tasks" required maxlength="160" /></label><label>Start from<input bind:value={base} placeholder="HEAD" required maxlength="200" /></label></div>
            <p class="fine">A separate worktree starts from this committed revision. Uncommitted changes in the original checkout are not copied.</p>
            <label>Feature title<input bind:value={title} placeholder="Let people complete a task" required maxlength="160" /></label>
            <label>What work should be done?<textarea bind:value={description} rows="5" placeholder="Describe the behavior, who it is for, and why it matters…" required maxlength="12000"></textarea></label>
            <label>Acceptance criteria <span class="muted">optional</span><textarea bind:value={acceptance} rows="3" placeholder="What must be true when this is finished?" maxlength="8000"></textarea></label>
            <div class="row"><button class="primary" type="submit" disabled={!writable}>{busy ? 'Creating…' : 'Create branch & feature'}</button>{#if features.length}<button type="button" onclick={() => creating = false}>Cancel</button>{/if}</div>
          </form>
        </section>
      {:else if feature}
        <div class="feature-heading"><div><span class="eyebrow">COLLABORATIVE FEATURE · REVISION {feature.input_revision}</span><h1>{feature.title}</h1><code class="branch">⑂ {feature.branch}</code></div><button onclick={() => void share()}>Share feature ↗</button></div>
        <div class="flow"><span class:current={['draft', 'creating', 'branch_failed'].includes(feature.status)}>1 · Define together</span><span class:current={['planning', 'needs_review'].includes(feature.status)}>2 · Review plan</span><span class:current={['approved', 'coding', 'code_ready'].includes(feature.status)}>3 · Build & edit</span></div>
        {#if feature.error}<div class="banner error">{feature.error}{#if feature.status === 'branch_failed'}<button onclick={() => void perform(async () => { await api(`/${selected}/retry-branch`, {}); await refresh(); })} disabled={!writable}>Retry branch creation</button>{/if}</div>{/if}
        {#if running}<div class="agent-working"><i></i><div><strong>{running.mode === 'plan' ? 'Agent is summarizing the feature' : 'Agent is coding the approved plan'}</strong><span>{running.status === 'queued' ? 'Queued for the worker' : running.trace.at(-1)?.detail || 'Working from the shared feature context…'}</span></div><button onclick={() => void stop()} disabled={!writable}>Stop</button></div>{/if}
        <div class="tabs" role="tablist" aria-label="Feature views">{#each [['brief', 'Feature brief'], ['plan', `Agent plan${feature.plans.length ? ' · ' + feature.plans.length : ''}`], ['code', 'Code Diff'], ['activity', 'Activity']] as [value, text]}<button role="tab" aria-selected={tab === value} disabled={filesDirty && tab !== value} onclick={() => tab = value!}>{text}</button>{/each}</div>
        <div class="content">
          {#if tab === 'brief'}
            {#if editBrief}<form onsubmit={saveBrief}><label>Feature title<input bind:value={editTitle} maxlength="160" required /></label><label>Feature description<textarea bind:value={editDescription} rows="7" maxlength="12000" required></textarea></label><label>Acceptance criteria<textarea bind:value={editAcceptance} rows="4" maxlength="8000"></textarea></label>{#if editRevision !== feature.input_revision}<p class="warning">New input arrived while you were editing. Copy your draft, cancel, and reopen the latest brief before saving.</p>{/if}<div class="row"><button class="primary" type="submit" disabled={!writable}>Save brief</button><button type="button" onclick={() => editBrief = false}>Cancel</button></div></form>
            {:else}<div class="section-heading"><h2>The brief</h2><button onclick={beginEdit} disabled={!writable || feature.status === 'branch_failed'}>Edit brief</button></div><p class="prose">{feature.description}</p><h3>Acceptance criteria</h3><p class="prose muted">{feature.acceptance || 'Use collaborator input to add the outcomes that matter.'}</p><div class="callout"><h3>Ready for a shared plan?</h3><p>The agent will combine the brief and all {feature.inputs.length} collaborator inputs into a proposed implementation. Review it together before approving.</p><button class="primary" onclick={() => void summarize()} disabled={!writable || !enabled || !!running || feature.status === 'branch_failed'}>Summarize feature</button></div><details><summary>Local branch details</summary><p class="fine">Repository: {feature.repository}<br>Worktree: {feature.worktree}<br>Base: {feature.base_sha}</p>{#if feature.source_dirty}<p class="fine">The original checkout had uncommitted changes. This branch starts from its committed base.</p>{/if}</details>{/if}
          {:else if tab === 'plan'}
            <div class="section-heading"><h2>Shared implementation plan</h2><button onclick={() => void summarize()} disabled={!writable || !enabled || !!running}>Summarize again</button></div>
            {#if plan}
              <div class="plan-meta"><select aria-label="Plan version" value={plan.id} onchange={e => planId = e.currentTarget.value}>{#each feature.plans as version, index}<option value={version.id}>Plan {index + 1} · input revision {version.input_revision}</option>{/each}</select><span class="status">{feature.approval?.plan_id === plan.id ? `Approved by ${feature.approval.author}` : plan.input_revision !== feature.input_revision ? 'Outdated — new input added' : 'Awaiting review'}</span></div>
              {#if plan.input_revision !== feature.input_revision}<div class="banner">The brief or collaborator input has changed. Summarize again before approving.</div>{/if}
              {@const planJob = feature.jobs.find(j => j.id === plan.id)}
              {#if planJob?.agent_project}<p class="fine">Agent version <code>{planJob.agent_project.version.slice(0, 8)}</code> · approval and coding use these saved files. <a href="?view=studio&agent=planner">Edit the next version in Agent Studio ↗</a></p>{/if}
              <p class="prose summary-text">{plan.plan.summary}</p>
              {#each [['Goals', plan.plan.goals], ['Acceptance criteria', plan.plan.acceptance_criteria], ['Out of scope', plan.plan.out_of_scope]] as section}<h3>{section[0]}</h3><ul>{#each section[1] as item}<li>{item}</li>{/each}</ul>{/each}
              <h3>Implementation steps</h3><ol class="steps">{#each plan.plan.steps as step}<li><strong>{step.title}</strong><p>{step.details}</p><div class="chips">{#each step.files as path}<code>{path}</code>{/each}</div></li>{/each}</ol>
              <h3>Test plan</h3><ul>{#each plan.plan.test_plan as item}<li>{item}</li>{/each}</ul>
              {#if plan.plan.risks.length}<h3>Risks & considerations</h3><ul>{#each plan.plan.risks as item}<li>{item}</li>{/each}</ul>{/if}
              {#if plan.plan.open_questions.length}<div class="callout warning"><h3>Resolve before coding</h3><ul>{#each plan.plan.open_questions as item}<li>{item}</li>{/each}</ul><p>Add decisions in collaborator input, then summarize again.</p></div>{/if}
              {#if plan.plan.input_coverage.length}<h3>How everyone’s input was handled</h3>{#each plan.plan.input_coverage as item}{@const source = feature.inputs.find(i => i.id === item.input_id)}<div class="coverage"><strong>{source?.author} · {source?.kind}</strong><blockquote>{source?.text}</blockquote><p>{item.handling}</p></div>{/each}{/if}
              <div class="approval-bar">{#if feature.approval?.plan_id === plan.id}<p class="approved">✓ Approved by {feature.approval.author}</p><button class="primary" onclick={() => void startCoding()} disabled={!writable || !enabled || !!running}>Start coding</button>{:else}<p>Approval records agreement on this exact revision.</p><button class="primary" onclick={() => void act(`plans/${plan.id}/approve`)} disabled={!writable || !canApprove}>Approve plan</button>{/if}</div>
            {:else}<div class="empty"><h3>A plan everyone can review</h3><p>Add the team’s requirements and questions, then ask the agent to summarize the feature.</p><button class="primary" onclick={() => void summarize()} disabled={!writable || !enabled || !!running}>Summarize feature</button></div>{/if}
          {:else if tab === 'code'}
            {#if feature.implementation}<div class="implementation"><h2>Implementation ready for review</h2><p class="prose">{feature.implementation.result.summary}</p>{#if feature.implementation.job_id !== feature.jobs.findLast(j => j.mode === 'code')?.id || !feature.approval}<p class="warning">This report is from an earlier coding task. Review it against the latest scope and files.</p>{/if}<h3>Suggested local checks</h3>{#each feature.implementation.result.suggested_checks as command}<pre class="command">{command}</pre>{/each}<p class="fine">Tests and type checks have not been executed by the agent. Run these in the worktree below.</p>{#each feature.implementation.result.limitations as item}<p class="muted">{item}</p>{/each}</div>
            {:else if !running}<div class="banner">{feature.approval ? 'The plan is approved. Start coding when your team is ready.' : 'Review and approve the agent’s plan before starting coding.'}{#if feature.approval}<button class="primary" onclick={() => void startCoding()} disabled={!writable || !enabled}>Start coding</button>{/if}</div>{/if}
            <p class="worktree"><strong>Local worktree</strong><code>{feature.worktree}</code><span>Shared saves are written here. Changes stay uncommitted for review.</span></p>
            <div class="section-heading"><h3>Python syntax</h3><button onclick={() => void checkSyntax()} disabled={!writable || filesDirty}>Check saved Python syntax</button></div>{#if feature.syntax_check}<p class:warning={feature.syntax_check.status === 'failed'}>{feature.syntax_check.status} · {feature.syntax_check.message}</p>{#each feature.syntax_check.files as file}<div class="check-row"><code>{file.path}</code><span>{file.status}</span></div>{#if file.error}<pre class="warning">{file.error}</pre>{/if}{/each}{:else}<p class="fine">Syntax checks parse saved files without executing them.</p>{/if}
            {#key selected}<FeatureFiles featureId={selected} author={name} {changes} disabled={!joined || connection !== 'connected'} onDirty={value => filesDirty = value} />{/key}
          {:else}
            <h2>Shared activity</h2>{#each [...feature.activity].reverse() as event}<div class="activity"><time>{time(event.time)}</time><div><strong>{event.author}</strong><p>{event.text}</p></div></div>{/each}
            <h3>Agent versions & tasks</h3>{#each [...feature.jobs].reverse() as job}<details><summary>{job.mode === 'plan' ? 'Feature summary' : 'Coding'} · {job.status}{job.usage ? ` · ${job.usage.total_tokens} tokens` : ''}</summary>{#if job.agent_project}<p class="fine">Frozen agent version {job.agent_project.version} · workspace revision {job.agent_project.revision}</p>{#each Object.entries(job.agent_project.files) as [path, source]}<details><summary>{path}</summary><pre class="agent-source">{source}</pre></details>{/each}{:else}<p class="fine">Legacy built-in agent. Summarize again to use the editable studio version.</p>{/if}{#if job.error}<p class="warning">{job.error}</p>{/if}{#each job.trace as item}<p class="fine">{item.name} {item.detail}</p>{/each}</details>{/each}
          {/if}
        </div>
      {:else}<div class="empty"><p>{loaded ? 'Choose a feature or start something new.' : 'Loading the shared workspace…'}</p></div>{/if}
    </main>

    <aside class="collaboration">
      <div class="collab-heading"><span class="eyebrow">BUILD THE CONTEXT TOGETHER</span><h2>Collaborator input <span>{feature && !creating ? feature.inputs.length : 0}</span></h2><p>Requirements, questions, and decisions feed directly into the agent’s plan.</p></div>
      {#if feature && !creating && joined}<div class="input-feed">{#each feature.inputs as item}<article><header><strong>{item.author}</strong><time>{time(item.time)}</time></header><span class="kind">{item.kind}</span><p>{item.text}</p></article>{/each}{#if !feature.inputs.length}<div class="empty-input"><span>↗</span><h3>Make the brief better together</h3><p>Share this feature’s URL with a second browser session. Add a requirement or ask a question.</p></div>{/if}</div><form class="input-form" onsubmit={postInput}><label>Input type<select bind:value={kind}><option value="requirement">Requirement</option><option value="question">Question</option><option value="decision">Decision</option><option value="feedback">Feedback</option></select></label><label class="sr-only" for="contribution">Your input</label><textarea id="contribution" bind:value={input} placeholder="What should the team consider?" rows="4" maxlength="4000" required></textarea><div><span>Posting as {name}</span><button class="primary" type="submit" disabled={!writable || !input.trim() || feature.status === 'branch_failed'}>Add input</button></div><p>New input requires a fresh summary and approval. Active coding stops; existing edits are kept.</p></form>
      {:else}<div class="empty-input"><span>◎</span><h3>A shared understanding before code</h3><p>Create a feature, invite a collaborator, and let the agent turn everyone’s input into a clear plan.</p></div>{/if}
    </aside>
  </div>
</div>

<style>
  .agent-source { max-height:350px; overflow:auto; font-size:11px; padding:12px; background:var(--panel-2); }
  .app-shell { height:100dvh; display:flex; flex-direction:column; }.topbar { height:66px; flex:none; border-bottom:1px solid var(--line); display:flex; align-items:center; justify-content:space-between; padding:0 25px; background:var(--panel); }.brand { font-size:21px; font-weight:650; letter-spacing:-1px; text-decoration:none; color:var(--ink); display:flex; align-items:center; }.brand>span { color:var(--muted); }.brand b { font-size:9px; letter-spacing:1.5px; font-weight:500; margin-left:22px; border-left:1px solid var(--line-2); padding-left:22px; color:var(--muted); }.top-actions { display:flex; align-items:center; gap:20px; font-size:11px; color:var(--muted); }.top-actions a { color:var(--ink); text-decoration:none; }.connection { display:flex; align-items:center; gap:6px; }.connection i,.agent-working i { width:6px; height:6px; border-radius:50%; background:var(--warn); }.connection.online i { background:var(--good); }
  .layout { flex:1; display:grid; grid-template-columns:225px minmax(360px,1fr) 330px; min-height:0; }.sidebar { border-right:1px solid var(--line); display:flex; flex-direction:column; padding:25px 14px 16px; overflow:auto; background:var(--panel); }.side-heading { display:flex; justify-content:space-between; color:var(--muted); font-size:10px; letter-spacing:1.3px; margin:0 10px 17px; }.new-button { width:100%; padding:10px; text-align:left; color:var(--accent); border-color:var(--line-2); margin-bottom:18px; }nav { display:flex; flex-direction:column; gap:5px; }nav button { background:none; border-color:transparent; text-align:left; padding:13px 11px; }nav button.selected { background:var(--accent-soft); border-color:color-mix(in srgb,var(--accent) 25%,var(--line)); }nav strong { display:block; font-size:12px; font-weight:550; margin-bottom:5px; overflow-wrap:anywhere; }nav code { font-size:10px; color:var(--muted); display:block; overflow-wrap:anywhere; }nav button>div { display:flex; align-items:center; justify-content:space-between; gap:4px; font-size:9px; margin-top:12px; color:var(--muted); }.status { font-size:10px; color:var(--muted); padding:3px 7px; border:1px solid var(--line-2); border-radius:5px; text-transform:capitalize; }.status.ready { color:var(--good); }.side-empty { color:var(--muted); font-size:12px; padding:15px; line-height:1.9; }.people { margin-top:auto; padding:35px 10px 0; }.people>div { display:flex; align-items:center; gap:10px; padding-top:14px; font-size:12px; }.people small { color:var(--muted); margin-left:auto; }.avatar { display:grid; place-items:center; width:25px; height:25px; border-radius:50%; background:var(--accent-soft); color:var(--accent); font-size:10px; }
  main { overflow:auto; min-width:0; }.new-feature { max-width:770px; padding:45px 42px; margin:auto; }.eyebrow { color:var(--muted); font-size:9px; letter-spacing:1.5px; font-weight:500; }.new-feature h1 { font-size:27px; margin:13px 0; }.new-feature form { margin-top:28px; }label { display:flex; flex-direction:column; gap:8px; font-size:12px; margin-bottom:18px; font-weight:550; }input,textarea,select { background:var(--panel); border:1px solid var(--line-2); border-radius:7px; padding:10px 12px; width:100%; font-weight:400; }textarea { resize:vertical; line-height:1.7; }input::placeholder,textarea::placeholder { color:var(--faint); }button { background:var(--panel-2); border:1px solid var(--line-2); border-radius:7px; padding:8px 12px; font-size:12px; }button:hover:enabled { border-color:var(--accent); }button:disabled { opacity:.4; }.primary { background:var(--accent); color:var(--accent-ink); border-color:var(--accent); font-weight:550; }.row { display:flex; align-items:center; gap:10px; }.repository { padding:20px; border:1px solid var(--line); border-radius:10px; margin-bottom:24px; background:var(--panel-2); }.two-fields { display:grid; grid-template-columns:2fr 1fr; gap:15px; }.muted,.fine { color:var(--muted); }.fine { font-size:11px; line-height:1.8; overflow-wrap:anywhere; }.two-fields+p { margin-top:-7px; margin-bottom:24px; }
  .feature-heading { display:flex; justify-content:space-between; align-items:center; gap:20px; padding:34px 32px 25px; }.feature-heading h1 { font-size:26px; line-height:1.3; letter-spacing:-.7px; margin:12px 0 10px; overflow-wrap:anywhere; }.feature-heading button { flex:none; font-size:11px; }.branch { color:var(--accent); font-size:11px; overflow-wrap:anywhere; }.flow { margin:0 32px 25px; display:flex; gap:10px; }.flow span { flex:1; padding:8px 0; font-size:10px; border-bottom:2px solid var(--line-2); color:var(--muted); }.flow .current { border-color:var(--accent); color:var(--ink); }.tabs { display:flex; gap:18px; padding:0 32px; border-bottom:1px solid var(--line); }.tabs button { border:0; border-radius:0; background:none; color:var(--muted); padding:13px 0; font-size:12px; border-bottom:2px solid transparent; white-space:nowrap; }.tabs button[aria-selected=true] { color:var(--ink); border-bottom-color:var(--accent); }.content { padding:24px 32px 60px; }.section-heading { display:flex; align-items:center; justify-content:space-between; gap:10px; }.section-heading button { font-size:11px; }h2 { font-size:16px; margin:0 0 15px; font-weight:550; }h3 { font-size:12px; font-weight:650; margin:26px 0 12px; }.section-heading h2,.section-heading h3 { margin:8px 0; }.prose { white-space:pre-wrap; line-height:1.85; overflow-wrap:anywhere; font-size:13px; }.summary-text { font-size:14px; }.callout { padding:22px; background:var(--panel-2); border:1px solid var(--line); border-radius:10px; margin:30px 0; }.callout h3 { margin:0 0 8px; }.callout p { color:var(--muted); font-size:12px; line-height:1.8; }.callout button { margin-top:9px; }details { border-top:1px solid var(--line); padding:15px 0; font-size:12px; }summary { cursor:pointer; color:var(--muted); }ul,ol { padding-left:22px; font-size:12px; line-height:1.9; }li { padding:3px 0; }.steps li { padding:8px 0; }.steps p { color:var(--muted); margin:5px 0; }.chips { display:flex; flex-wrap:wrap; gap:5px; }.chips code { background:var(--panel-2); font-size:10px; padding:2px 7px; border-radius:4px; }.plan-meta { display:flex; align-items:center; gap:15px; margin:18px 0; }.plan-meta select { width:auto; font-size:11px; }.coverage { border-left:2px solid var(--line-2); padding:10px 16px; margin:15px 0; font-size:12px; }.coverage strong { font-size:11px; }.coverage blockquote { margin:8px 0; color:var(--muted); font-style:italic; white-space:pre-wrap; }.coverage p { margin:0; line-height:1.8; }.approval-bar { margin-top:35px; padding:20px 0; border-top:1px solid var(--line); display:flex; justify-content:space-between; gap:15px; align-items:center; }.approval-bar p { color:var(--muted); font-size:12px; }.approval-bar .approved { color:var(--good); }.approval-bar button { flex:none; }.empty { padding:60px 20px; text-align:center; color:var(--muted); font-size:13px; }.empty p { line-height:1.8; }.empty h3 { color:var(--ink); font-size:17px; }.empty button { margin-top:18px; }
  .banner { margin:16px 25px; padding:14px 16px; background:var(--accent-soft); color:var(--muted); border:1px solid var(--line-2); border-radius:8px; font-size:12px; line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }.content .banner { margin:15px 0; }.banner button { margin-left:12px; }.error,.warning { color:var(--warn); }.agent-working { display:flex; align-items:center; gap:12px; margin:0 32px 20px; padding:15px; border:1px solid var(--line); border-radius:8px; }.agent-working i { background:var(--accent); animation:pulse 2s infinite; }.agent-working>div { flex:1; display:flex; flex-direction:column; gap:3px; font-size:11px; }.agent-working span { color:var(--muted); }.worktree { padding:14px 16px; border:1px solid var(--line); border-radius:8px; display:flex; flex-direction:column; gap:7px; font-size:11px; }.worktree code { overflow-wrap:anywhere; color:var(--accent); }.worktree span { color:var(--muted); }.command { padding:12px; background:var(--panel-2); border-radius:6px; overflow:auto; font-size:11px; }.check-row { display:flex; justify-content:space-between; gap:12px; font-size:11px; padding:5px 0; }.activity { display:flex; gap:20px; padding:13px 0; border-bottom:1px solid var(--line); font-size:12px; }.activity time { font-size:10px; color:var(--muted); padding-top:2px; min-width:65px; }.activity p { color:var(--muted); margin:4px 0 0; }
  .collaboration { border-left:1px solid var(--line); background:var(--panel); display:flex; flex-direction:column; min-height:0; }.collab-heading { padding:29px 22px 20px; border-bottom:1px solid var(--line); }.collab-heading h2 { margin:13px 0 9px; font-size:16px; }.collab-heading h2 span { color:var(--muted); font-size:11px; margin-left:5px; }.collab-heading p { font-size:11px; color:var(--muted); line-height:1.8; margin:0; }.input-feed { flex:1; overflow:auto; padding:0 22px; min-height:140px; }.input-feed article { padding:22px 0; border-bottom:1px solid var(--line); }.input-feed header { display:flex; justify-content:space-between; font-size:12px; }.input-feed time { color:var(--faint); font-size:10px; }.kind { color:var(--accent); font-size:9px; text-transform:uppercase; letter-spacing:.6px; margin-top:7px; display:inline-block; }.input-feed article p { font-size:12px; color:var(--muted); white-space:pre-wrap; overflow-wrap:anywhere; margin:8px 0 0; line-height:1.85; }.empty-input { padding:45px 25px; color:var(--muted); text-align:center; }.empty-input>span { display:grid; place-items:center; width:43px; height:43px; font-size:24px; color:var(--accent); background:var(--accent-soft); border-radius:12px; margin:auto; }.empty-input h3 { margin:20px 0 10px; font-size:13px; color:var(--ink); }.empty-input p { font-size:11px; line-height:1.9; }.input-form { margin:16px; border:1px solid var(--line-2); border-radius:10px; background:var(--panel-2); padding:13px; }.input-form label { font-size:10px; gap:5px; margin-bottom:10px; }.input-form select { padding:6px 8px; font-size:11px; }.input-form textarea { border:0; background:transparent; padding:0; font-size:12px; resize:vertical; max-height:240px; }.input-form>div { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-top:10px; }.input-form span { font-size:10px; color:var(--muted); }.input-form p { font-size:9px; color:var(--muted); line-height:1.8; margin:13px 0 0; }.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); }
  @media(min-width:1600px) { .layout { grid-template-columns:245px minmax(360px,1fr) 370px; }.content { max-width:1050px; margin:auto; } }
  @media(max-width:1150px) { .layout { grid-template-columns:185px minmax(330px,1fr) 285px; }.feature-heading,.content { padding-left:22px; padding-right:22px; }.tabs { padding:0 22px; gap:13px; }.feature-heading h1 { font-size:22px; }.brand b { display:none; }.new-feature { padding:35px 25px; } }
  @media(max-width:900px) { .layout { grid-template-columns:180px minmax(0,1fr); overflow:auto; }.collaboration { grid-column:2; min-height:400px; border-top:1px solid var(--line); }.sidebar { grid-row:1/3; }main { overflow:visible; }.input-feed { max-height:400px; }.top-actions { gap:10px; }.top-actions>span:nth-child(2) { display:none; } }
  @media(max-width:600px) { .layout { display:block; }.sidebar { max-height:230px; border-bottom:1px solid var(--line); }.people,.side-empty { display:none; }nav { flex-direction:row; overflow:auto; }nav button { min-width:165px; }.topbar { padding:0 15px; }.feature-heading { flex-wrap:wrap; }.tabs { gap:12px; }.tabs button { font-size:11px; }.two-fields { grid-template-columns:1fr; gap:0; } }
</style>
