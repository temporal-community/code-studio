<script lang="ts">
  import { sessionName } from './shared/identity';
  import { workspaceConnection } from './shared/workspaceConnection';
  import { onMount, tick } from 'svelte';
  import { harnessTarget } from './shared/harnessDebug';
  import { SharedDocument, type Operation, type Snapshot, type TextChange } from './shared/document';
  import { highlight } from './shared/python';
  import AgentRuns from './shared/AgentRuns.svelte';
  import { names, stages, studioUrl, featureUrl } from './shared/platform';
  import ReviewPanel from './shared/ReviewPanel.svelte';
  import FeatureRunPanel from './shared/FeatureRunPanel.svelte';
  import AgentConversation from './shared/AgentConversation.svelte';

  interface Peer { id: string; name: string; cursor: string; file?: string }
  interface JournalEntry { kind: string; author: string; detail: string; revision: number; time: number }
  interface Edit { type: 'edit'; id: string; operation: Operation; author: string; file?: string }
  const route = new URLSearchParams(location.search);
  const catalogMode = route.get('view') === 'studio';
  const agentId = route.get('agent') || 'planner';
  const agentName = names[agentId] || 'Planner';
  const hostProject = catalogMode ? 'agent:' + agentId : route.get('view') === 'review' ? 'review' : 'feature';
  const featureMode = route.get('agent') !== 'review' && route.get('view') !== 'review';
  const storageKey = catalogMode ? 'code-studio:agent-draft:' + agentId : featureMode ? 'code-studio:feature-draft' : 'code-studio:shared-draft';
  let document = new SharedDocument();
  let documents: Record<string, SharedDocument> = {};
  let files = $state<string[]>(catalogMode ? [] : featureMode ? ['feature.py', 'planner.md', 'coder.md', 'feature.json'] : ['agent.py', 'instructions.md', 'agent.json', 'evaluations.json', 'app.py']);
  let selectedFile = $state(catalogMode ? '' : featureMode ? 'feature.py' : 'agent.py');
  let view = $state<'project' | 'reviews'>('project');
  let reviewRefresh = $state(0);
  let builderSessionId = $state('');
  $effect(() => { harnessTarget.set({ workflowId: builderSessionId, label: catalogMode ? agentName + ' designer' : 'Workspace agent' }); });
  let text = $state('');
  let revision = $state(0);
  let connection = $state<'connecting' | 'connected' | 'offline'>('connecting');
  let loaded = $state(false);
  const name = sessionName();
  const joined = !!name;
  let peers = $state<Peer[]>([]);
  let self = $state('');
  let events = $state<JournalEntry[]>([]);
  let filePath = $state('');
  let sessionId = $state('');
  let agentEnabled = $state(true);
  let agentError = $state<string | null>(null);
  let error = $state('');
  let pendingCount = $state(0);
  let prompt = $state('');
  let promptPending = $state(false);
  let promptId: string | null = null;
  let promptSentText = '';
  let copied = $state(false);
  let formatting = $state(false);
  let formatStatus = $state('');
  let validating = $state(false);
  let validationStatus = $state('');
  let editor = $state<HTMLTextAreaElement>(null!);
  let highlightCode = $state<HTMLElement>();
  let editorWidth = $state(0);
  let lineHeights = $state<number[]>([]);
  let scrollTop = $state(0);
  let scrollLeft = $state(0);
  let socket: ReturnType<typeof workspaceConnection> | undefined;
  let pending: Edit[] = [];
  let composing = $state(false);
  let deferred: any[] = [];
  let sequence = 0;
  const client = crypto.randomUUID();
  const nextId = () => `${client}-${++sequence}`;
  const lines = $derived(text.split('\n').length);
  const python = $derived(selectedFile.endsWith('.py'));
  const markdown = $derived(/\.(md|markdown)$/i.test(selectedFile));
  const highlighted = $derived(python ? highlight(text) : text.split('\n').map(line => [{ text: line, cls: '' }]));
  const otherPeers = $derived(peers.filter(p => p.id !== self));

  $effect(() => {
    // Measure after text or width changes so the gutter follows soft-wrapped lines.
    highlighted; editorWidth;
    lineHeights = markdown && highlightCode
      ? Array.from(highlightCode.children, line => line.getBoundingClientRect().height)
      : [];
  });

  function send(data: unknown): void {
    socket?.send(data);
  }
  function saveDraft(): void {
    pendingCount = pending.length;
    try { sessionStorage.setItem(storageKey, JSON.stringify({ pending, text, sessionId, selectedFile })); } catch { /* In-memory recovery remains available. */ }
  }
  function caret(): void {
    if (!editor) return;
    send({ type: 'presence', name, file: selectedFile, cursor: document.anchor(editor.selectionStart) });
  }
  async function renderChange(change: () => void): Promise<void> {
    const focused = window.document.activeElement === editor;
    const start = editor ? document.anchor(editor.selectionStart) : '';
    const end = editor ? document.anchor(editor.selectionEnd) : '';
    change();
    text = document.text;
    saveDraft();
    await tick();
    if (focused && editor) editor.setSelectionRange(document.offset(start), document.offset(end));
  }
  function receive(data: any): void {
    if (composing && (data.type === 'operation' || data.type === 'snapshot')) { deferred.push(data); return; }
    if (data.type === 'snapshot') {
      if (sessionId && sessionId !== data.session_id && pending.length) {
        error = 'The host workspace changed. Your draft is retained; copy it before joining the new workspace.';
        socket?.close(); return;
      }
      self = data.self; filePath = data.path; sessionId = data.session_id;
      builderSessionId = (catalogMode ? data.studio_session_ids?.[agentId] : featureMode ? data.feature_builder_session_id : data.builder_session_id) || '';
      if (catalogMode) {
        files = data.agent_projects?.[agentId]?.files || [];
        if (!files.length) { error = 'Restart just dev to load the six-agent platform.'; return; }
        if (!files.includes(selectedFile)) selectedFile = files[0]!;
      }
      if (!catalogMode && featureMode && !data.files?.['feature.py']) { error = 'Restart just dev to load the editable feature builder and its worker.'; return; }
      if (!data.files) { error = 'Restart just dev to load the SDLC agent project and review tools.'; selectedFile = 'app.py'; }
      agentEnabled = data.agent_enabled; agentError = data.agent_error;
      revision = data.revision; events = data.events;
      void renderChange(() => {
        documents = Object.fromEntries(Object.entries(data.files || { 'app.py': data }).map(([path, snapshot]) => [path, new SharedDocument(snapshot as Snapshot)]));
        for (const edit of pending) documents[edit.file || 'app.py']?.apply(edit.operation);
        document = documents[selectedFile] || documents['app.py'] || new SharedDocument();
      });
      loaded = true; connection = 'connected';
      for (const edit of pending) send(edit);
      caret();
    } else if (data.type === 'operation') {
      pending = pending.filter(edit => edit.id !== data.id);
      revision = Math.max(revision, data.revision); validationStatus = '';
      events = data.events;
      void renderChange(() => documents[data.file || 'app.py']?.apply(data.operation));
    } else if (data.type.startsWith('review-') || data.type === 'feature-changed') reviewRefresh++;
    else if (data.type === 'presence') peers = data.peers;
    else if (data.type === 'events') events = data.events;
    else if (data.type === 'agent-status' && (data.project || 'review') === hostProject) agentError = data.error;
    else if (data.type === 'prompt-ack' && data.id === promptId) {
      promptPending = false; promptId = null;
      if (prompt === promptSentText) prompt = '';
    } else if (data.type === 'error') {
      error = data.message;
      if (data.kind === 'prompt') promptPending = false;
      // A rejected edit stays in the draft. Never silently discard a person's text.
    }
  }
  function connect(): void {
    if (!joined) return;
    // Keep the failure state visible while retrying, instead of flashing the UI.
    const url = new URL('/workspace/connect', location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('name', name);
    url.searchParams.set('project', hostProject);
    socket = workspaceConnection(url, {
      message: event => receive(JSON.parse(event.data)),
      offline: () => {
        connection = 'offline'; peers = [];
        if (promptPending) { promptPending = false; error = 'Connection lost while sending. Retry sends the same request safely.'; }
      },
    });
  }
  function edit(value: string): void {
    if (value === document.text) return;
    if (Array.from(value).length > 20000) { error = 'Each project file supports up to 20,000 characters.'; editor.value = text; return; }
    validationStatus = '';
    const id = nextId();
    const operation = document.edit(value, id);
    const message: Edit = { type: 'edit', id, operation, author: name, file: selectedFile };
    document.apply(operation); text = document.text;
    pending.push(message); saveDraft(); send(message); caret();
  }
  async function validateAgent(): Promise<void> {
    validating = true; error = ''; validationStatus = '';
    try {
      const response = await fetch(catalogMode ? `/workspace/agents/${agentId}/validate` : featureMode ? '/workspace/features/agent/validate' : '/workspace/project/validate', { method: 'POST' });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Agent validation failed');
      validationStatus = result.message + ' Version ' + result.version.slice(0, 8) + '.';
    } catch (cause) { error = (cause as Error).message; }
    finally { validating = false; }
  }
  async function formatCode(): Promise<void> {
    if (!python || formatting || !loaded || !joined || composing || pendingCount || connection !== 'connected') return;
    const source = text, baseRevision = revision, baseSession = sessionId, baseFile = selectedFile;
    formatting = true; formatStatus = ''; error = '';
    try {
      const response = await fetch('/workspace/format', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source }), signal: AbortSignal.timeout(10000),
      });
      if (response.status === 404) throw new Error('Restart just dev to load the Python formatter.');
      if (!response.ok) {
        const details = await response.json().catch(() => ({}));
        throw new Error(typeof details.detail === 'string' ? details.detail : 'Python formatting failed. Check the workspace host.');
      }
      const result: { text: string; changes: TextChange[] } = await response.json();
      if (selectedFile !== baseFile || text !== source || revision !== baseRevision || sessionId !== baseSession || pending.length || connection !== 'connected' || composing) {
        formatStatus = 'File changed during formatting. Try again.'; return;
      }
      if (result.text === source) { formatStatus = 'Already formatted'; return; }
      if (Array.from(result.text).length > 20000) throw new Error('Formatted code exceeds the 20,000 character file limit.');
      const id = nextId();
      const operation = document.patch(result.changes, id);
      // Check the complete result before changing any shared state.
      const preview = new SharedDocument({ nodes: [...document.nodes.values()], clock: document.clock });
      preview.apply(operation);
      if (preview.text !== result.text) throw new Error('Could not apply formatting changes. Please retry.');
      const message: Edit = { type: 'edit', id, operation, author: name, file: selectedFile };
      pending.push(message);
      const selectionStart = document.anchor(editor.selectionStart), selectionEnd = document.anchor(editor.selectionEnd);
      await renderChange(() => document.apply(operation));
      editor.setSelectionRange(document.offset(selectionStart), document.offset(selectionEnd));
      send(message); caret();
      formatStatus = 'Formatted with Ruff';
    } catch (cause) {
      error = cause instanceof Error ? cause.message : 'Could not format Python.';
    } finally { formatting = false; }
  }
  function keydown(event: KeyboardEvent): void {
    if (event.shiftKey && event.altKey && event.code === 'KeyF') {
      event.preventDefault(); void formatCode();
    } else if (event.key === 'Tab') {
      event.preventDefault();
      const start = editor.selectionStart, end = editor.selectionEnd;
      edit(text.slice(0, start) + '    ' + text.slice(end));
      void tick().then(() => editor.setSelectionRange(start + 4, start + 4));
    }
  }
  function sendPrompt(): void {
    if (!prompt.trim() || promptPending || connection !== 'connected' || pendingCount) return;
    if (promptSentText !== prompt) { promptId = null; promptSentText = prompt; }
    promptId ??= nextId(); promptPending = true; error = '';
    send({ type: 'prompt', id: promptId, author: name, text: prompt.trim() });
  }
  async function invite(): Promise<void> {
    try { await navigator.clipboard.writeText(location.href); copied = true; setTimeout(() => copied = false, 2000); }
    catch { error = 'Open this same URL in a second browser window to join.'; }
  }
  function position(peer: Peer): string {
    const doc = documents[peer.file || 'app.py'];
    const before = (doc?.text || '').slice(0, doc?.offset(peer.cursor) || 0);
    return `${peer.file || 'app.py'} · L${before.split('\n').length}:C${(before.split('\n').at(-1)?.length ?? 0) + 1}`;
  }
  async function selectFile(path: string): Promise<void> {
    if (composing || !documents[path]) return;
    selectedFile = path; document = documents[path]; text = document.text;
    view = 'project'; formatStatus = ''; scrollTop = 0; scrollLeft = 0;
    await tick();
    if (editor) { editor.scrollTop = 0; editor.scrollLeft = 0; editor.setSelectionRange(0, 0); }
    caret();
  }
  function switchAgent(event: Event): void {
    const select = event.currentTarget as HTMLSelectElement;
    const next = select.value;
    // Keep the current selection if navigation is cancelled by the unsynced-edit guard.
    select.value = agentId;
    if (next !== agentId) location.assign(studioUrl(next, route.get('feature') || ''));
  }
  onMount(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null');
      if (saved?.pending?.length) { pending = saved.pending; selectedFile = saved.selectedFile || 'app.py'; text = saved.text; sessionId = saved.sessionId; pendingCount = pending.length; }
    } catch { /* Start with host state if browser storage is unavailable. */ }
    connect();
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (pending.length) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => { socket?.close(); window.removeEventListener('beforeunload', beforeUnload); };
  });
</script>

<svelte:head><title>Code Studio · Collaborative SDLC agents</title></svelte:head>
<div class="workspace-app">
  <header>
    <a class="brand" href="/">⌘ <strong>Code Studio</strong><span>AGENT STUDIO · {catalogMode ? agentName.toUpperCase() : featureMode ? 'PLANNER' : 'REVIEW'}</span></a>
    <div class="header-actions">
      {#if catalogMode}
        <label class="agent-switcher">Agent
          <select value={agentId} onchange={switchAgent} disabled={composing}>
            {#each stages as stage}<option value={stage}>{names[stage]}</option>{/each}
          </select>
        </label>
      {:else}<a class="agents-link" href="/">← Agents</a>{/if}
      {#if route.get('feature')}<a class="agents-link" href={featureUrl(route.get('feature')!, agentId)}>← Back to feature</a>{/if}<span class="connection" class:online={connection === 'connected'}>{!joined ? 'Not joined' : connection === 'connected' ? 'Live' : connection === 'offline' ? 'Workspace offline · retrying' : 'Connecting…'}</span><button onclick={invite}>{copied ? 'Link copied' : 'Invite collaborator ↗'}</button>
    </div>
  </header>
  <aside class="project-panel">
    <div class="project-name"><span class="project-icon">Py</span><div><strong>{catalogMode ? agentName + ' agent' : featureMode ? 'Planner agent' : 'Review agent'}</strong><small>LOCAL WORKSPACE</small></div></div>
    <div class="section-label">{featureMode ? 'AGENT DEFINITION' : 'FILES'}</div>
    {#each files as file}<button class="file" class:active={selectedFile === file && view === 'project'} onclick={() => void selectFile(file)} disabled={!loaded || composing}><span>{file.endsWith('.py') ? '⌘' : '≡'}</span>{file}{#if file === 'app.py'}<small>scratch</small>{/if}</button>{/each}
    <button class="review-nav" class:active={view === 'reviews'} onclick={() => view = 'reviews'}>▷ &nbsp; {catalogMode ? 'Feature runs' : featureMode ? 'Run on codebase' : 'Runs & reports'}</button>
    <a class="other-studio" href="?view=work&new=1">Start a feature ↗</a>
    <div class="section-label people-title">IN THIS WORKSPACE <small>{peers.length}</small></div>
    {#each peers as peer, index (peer.id)}
      <div class="person"><span class="avatar" style={`--person-color:${['#8b93ff','#38d3ee','#f5b945','#e995c8'][index % 4]}`}>{peer.name.slice(0, 1).toUpperCase()}</span><div>{peer.name}{#if peer.id === self}<small> (you)</small>{/if}<small class="person-position">{position(peer)}</small></div></div>
    {/each}
    <div class="person"><span class="avatar agent-avatar">✦</span><div>Builder agent<small class="person-position">Edits through local callbacks</small></div></div>
    {#if joined && otherPeers.length === 0}<p class="invite-note">Open this URL in another browser session and join with a different name.</p>{/if}
    <div class="local-file"><span class="section-label">SAVED ON THIS COMPUTER</span><code>{filePath ? filePath.replace(/app\.py$/, selectedFile) : '.local/shared-project/' + selectedFile}</code></div>
  </aside>
  <main>
    <div class="editor-tabs"><button class="tab" class:current={view === 'project'} onclick={() => view = 'project'}>Agent project</button><button class="tab" class:current={view === 'reviews'} onclick={() => view = 'reviews'}>{catalogMode ? 'Feature runs' : featureMode ? 'Run on codebase' : 'Runs & reports'}</button><span class="revision">revision {revision}</span></div>
    {#if view === 'reviews'}
      {#if catalogMode}<AgentRuns {agentId} refreshKey={reviewRefresh}/>{:else if featureMode}<FeatureRunPanel author={name} {revision} canRun={joined && loaded && pendingCount === 0 && connection === 'connected'} refreshKey={reviewRefresh} />{:else}
      <ReviewPanel author={name} {revision} canRun={joined && loaded && pendingCount === 0 && connection === 'connected'} refreshKey={reviewRefresh} />{/if}
    {:else}
    <div class="editor-toolbar"><span>{selectedFile} <span class="separator">/</span> UTF-8</span><div class="editor-actions"><span class:unsaved={pendingCount > 0}>{!loaded ? 'Waiting for workspace…' : pendingCount ? `${pendingCount} edit${pendingCount === 1 ? '' : 's'} waiting to sync` : 'All changes saved'}</span><button class="format-button" onclick={() => void validateAgent()} disabled={!loaded || !joined || connection !== 'connected' || pendingCount > 0 || validating}>{validating ? 'Validating…' : 'Validate agent'}</button>{#if python}<button class="format-button" onclick={() => void formatCode()} disabled={!loaded || !joined || connection !== 'connected' || pendingCount > 0 || formatting} title="Format Python with Ruff (Shift+Alt+F)">{formatting ? 'Formatting…' : 'Format Python'}</button>{/if}</div></div>
    {#if formatStatus}<p class="format-status" role="status">{formatStatus}</p>{/if}
    {#if validationStatus}<p class="format-status" role="status">{validationStatus}</p>{/if}
    {#if joined && !loaded && connection === 'offline'}<p class="host-notice">Waiting for the local workspace host. Start or restart <code>just dev</code>, or use <code>just collaboration</code> for shared editing only.</p>{/if}
    {#if error}<div class="error-banner" role="alert">{error}<button onclick={() => error = ''} aria-label="Dismiss error">×</button></div>{/if}
    <div class="editor-container" class:markdown>
      <div class="line-numbers" aria-hidden="true"><div style:transform={`translateY(${-scrollTop}px)`}>{#each Array(lines) as _, index}<div style:height={`${lineHeights[index] || 23}px`}>{index + 1}</div>{/each}</div></div>
      <div class="editor-surface" class:composing>
      <div class="highlight-layer" aria-hidden="true"><pre style:width={markdown ? `${editorWidth}px` : undefined} style:transform={`translate(${-scrollLeft}px, ${-scrollTop}px)`}><code bind:this={highlightCode}>{#each highlighted as tokens}<span class="syntax-line">{#each tokens as token}<span class={token.cls}>{token.text}</span>{/each}</span>{/each}</code></pre></div>
      <textarea bind:this={editor} bind:clientWidth={editorWidth} value={text} aria-label={`${selectedFile} shared editor`} aria-keyshortcuts="Alt+Shift+F" spellcheck="false" autocapitalize="off" autocomplete="off" wrap={markdown ? 'soft' : 'off'} disabled={!loaded || !joined}
        oninput={event => { if (!composing) edit(event.currentTarget.value); }}
        onkeydown={keydown} onkeyup={caret} onclick={caret} onselect={caret}
        onscroll={() => { scrollTop = editor.scrollTop; scrollLeft = editor.scrollLeft; }}
        oncompositionstart={() => composing = true}
        oncompositionend={() => { composing = false; edit(editor.value); const waiting = deferred; deferred = []; for (const message of waiting) receive(message); }}></textarea>
      </div>
    </div>
    <div class="activity-panel"><div class="section-label">WORKSPACE ACTIVITY <span>Human and agent changes</span></div>
      <div class="activity-list">{#each [...events].reverse().slice(0, 8) as event}
        <div class="activity"><span class:agent-dot={event.author === 'Builder agent'} class="activity-dot"></span><strong>{event.author}</strong><span>{event.detail}</span><small>r{event.revision}</small></div>
      {:else}<p>Changes from both collaborators and the agent will appear here.</p>{/each}</div>
    </div>
    {/if}
  </main>
  <aside class="conversation-panel"><div class="conversation-title"><span>✦</span><strong>Build together</strong><small>SHARED CHAT</small></div>
    <div class="conversation-scroll">{#if builderSessionId && agentEnabled}<AgentConversation sessionId={builderSessionId} {featureMode} agentName={catalogMode ? agentName : ''}/>{:else}<p class="empty-chat">The shared editor works independently of the agent. Start the full stack to chat with the Builder agent.</p>{/if}</div>
    {#if agentError}<p class="agent-warning">{agentError}</p>{/if}
    <form class="composer" onsubmit={event => { event.preventDefault(); sendPrompt(); }}>
      <textarea aria-label="Message Builder agent" bind:value={prompt} placeholder={catalogMode ? `Customize the ${agentName} agent with your team…` : featureMode ? "Ask the designer to customize your feature builder…" : "Ask the builder to improve this reviewer…"} rows="3" maxlength="8000"></textarea>
      <div><span>Sending as {name || 'collaborator'}</span><button class="primary" type="submit" disabled={!joined || !loaded || !agentEnabled || !prompt.trim() || promptPending || pendingCount > 0 || connection !== 'connected'}>{promptPending ? 'Sending…' : 'Send ↗'}</button></div>
    </form>
    <p class="scope-note">{catalogMode ? "Edit this agent’s Python flow, instructions, and tools. Saved changes apply to the next stage run; active runs keep their frozen version." : featureMode ? "This assistant edits the feature builder. Run your saved agent against a codebase in Run on codebase." : "Build the agent together, then run a frozen version. Reports stay in this workspace."}</p>
  </aside>
</div>

<style>
  .agents-link { color:var(--muted); text-decoration:none; font-size:11px; }
  .other-studio { color:var(--muted); font-size:11px; margin:18px 8px 0; text-decoration:none; }
  :global(body) { overflow:hidden; }
  button { padding:8px 12px; border-radius:7px; border:1px solid var(--line-2); background:var(--panel-2); font-size:12px; }
  button:hover:not(:disabled) { border-color:var(--accent); }
  button:disabled { opacity:.45; }
  .primary { background:var(--accent); color:var(--accent-ink); border-color:transparent; font-weight:600; }
  .workspace-app { height:100dvh; display:grid; grid-template-columns:230px minmax(320px,1fr) 350px; grid-template-rows:64px minmax(0,1fr); background:var(--bg); }
  header { grid-column:1/-1; display:flex; align-items:center; justify-content:space-between; padding:0 24px; border-bottom:1px solid var(--line); background:var(--panel); }
  .brand { color:var(--ink); text-decoration:none; display:flex; align-items:center; gap:12px; font-size:23px; }
  .brand strong { font-size:16px; letter-spacing:-.4px; }
  .brand>span { margin-left:12px; color:var(--faint); font-size:9px; letter-spacing:1.6px; }
  .header-actions { display:flex; align-items:center; gap:20px; }
  .agent-switcher { display:flex; align-items:center; gap:8px; color:var(--muted); font-size:11px; }
  .agent-switcher select { min-height:44px; max-width:160px; padding:8px 10px; border:1px solid var(--line-2); border-radius:7px; background:var(--panel-2); color:var(--ink); font:inherit; }
  .agent-switcher select:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  .connection { font-size:11px; color:var(--muted); }
  .connection:before { content:''; display:inline-block; width:6px; height:6px; border-radius:50%; background:var(--warn); margin-right:7px; }
  .connection.online:before { background:var(--good); }
  .project-panel { display:flex; flex-direction:column; padding:24px 14px; border-right:1px solid var(--line); background:var(--panel); overflow:auto; }
  .project-name { display:flex; gap:10px; align-items:center; padding:0 4px 30px; }
  .project-name strong { font-size:12px; }
  .project-name small { display:block; font-size:8px; letter-spacing:1.3px; color:var(--faint); margin-top:4px; }
  .project-icon { padding:8px; background:var(--accent-soft); color:var(--accent); border-radius:8px; font:13px var(--mono); }
  .section-label { color:var(--muted); font-size:9px; font-weight:600; letter-spacing:1.3px; padding:0 8px; }
  .section-label>small { float:right; }
  .file { display:flex; gap:10px; padding:11px; margin-top:10px; border-radius:6px; background:transparent; color:var(--ink); font:11px var(--mono); width:100%; text-align:left; border-color:transparent; }
  .file.active,.review-nav.active { background:var(--accent-soft); color:var(--accent); }.review-nav { margin-top:20px; text-align:left; }
  .file>span { color:var(--accent); }.file small { margin-left:auto; color:var(--muted); font-size:9px; }
  .people-title { margin:34px 0 10px; }
  .person { display:flex; gap:10px; align-items:center; padding:9px 7px; font-size:12px; }
  .avatar { display:grid; place-items:center; width:28px; height:28px; flex-shrink:0; border-radius:50%; color:var(--person-color,var(--accent)); background:color-mix(in srgb,var(--person-color,var(--accent)) 15%,transparent); font-weight:600; }
  .agent-avatar { border-radius:8px; }.person small { color:var(--muted); font-size:10px; }.person-position { display:block; margin-top:2px; }
  .invite-note { font-size:11px; line-height:1.8; color:var(--muted); padding:10px; border:1px dashed var(--line-2); border-radius:7px; }
  .local-file { margin-top:auto; padding:25px 7px 0; }.local-file .section-label { padding:0; }
  .local-file code { display:block; font-size:9px; line-height:1.8; color:var(--faint); overflow-wrap:anywhere; margin-top:9px; }
  main { display:flex; flex-direction:column; min-height:0; min-width:0; }
  .editor-tabs { height:45px; flex-shrink:0; display:flex; align-items:center; border-bottom:1px solid var(--line); background:var(--panel); }
  .tab { display:flex; align-items:center; gap:8px; align-self:stretch; padding:0 22px; border:0; border-top:2px solid transparent; border-radius:0; background:transparent; font-size:12px; }.tab.current { border-top-color:var(--accent); background:var(--bg); }
  .revision { margin-left:auto; padding:0 16px; color:var(--faint); font:10px var(--mono); }
  .editor-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:8px 20px; color:var(--muted); font-size:10px; }
  .editor-actions { display:flex; align-items:center; gap:12px; }.format-button { font-size:11px; padding:6px 9px; white-space:nowrap; }
  .format-status { margin:0 20px 8px; color:var(--muted); font-size:11px; }
  .host-notice { margin:0 20px 12px; font-size:12px; line-height:1.8; color:var(--muted); }
  .separator { margin:0 10px; color:var(--line-2); }.unsaved { color:var(--warn); }
  .editor-container { flex:1; min-height:150px; display:flex; overflow:hidden; padding-top:12px; }
  .line-numbers { overflow:hidden; width:54px; flex-shrink:0; text-align:right; padding-right:16px; font:12px/23px var(--mono); color:var(--faint); }
  .line-numbers>div>div { height:23px; }
  .editor-surface { flex:1; min-width:0; position:relative; }
  .highlight-layer { position:absolute; inset:0; overflow:hidden; pointer-events:none; }
  .highlight-layer pre, .editor-surface textarea { padding:0 20px 60px 0; margin:0; font:12px/23px var(--mono); tab-size:4; white-space:pre; letter-spacing:normal; font-variant-ligatures:none; }
  .highlight-layer pre { min-width:100%; width:max-content; }.highlight-layer code { font:inherit; }
  .syntax-line { display:block; min-height:23px; }
  .editor-surface textarea { position:absolute; inset:0; width:100%; height:100%; resize:none; border:0; outline:none; background:transparent; color:transparent; caret-color:var(--ink); border-radius:0; }
  .markdown .highlight-layer pre, .markdown .editor-surface textarea { white-space:pre-wrap; overflow-wrap:anywhere; }
  .markdown .highlight-layer pre { min-width:0; }
  .markdown .editor-surface textarea { overflow-x:hidden; }
  .editor-surface textarea::selection { background:color-mix(in srgb,var(--accent) 25%,transparent); color:transparent; }
  .editor-surface.composing .highlight-layer { visibility:hidden; }.editor-surface.composing textarea { color:var(--ink); }
  .highlight-layer .keyword { color:var(--syn-keyword); }.highlight-layer .string { color:var(--syn-string); }
  .highlight-layer .comment, .highlight-layer .doc { color:var(--syn-comment); }
  .highlight-layer .number { color:var(--syn-number); }.highlight-layer .fn { color:var(--syn-fn); }
  .highlight-layer .builtin { color:var(--syn-builtin); }.highlight-layer .host { color:var(--syn-host); }
  .highlight-layer .op { color:var(--syn-op); }.highlight-layer .const { color:var(--syn-const); }
  @media(forced-colors:active) { .highlight-layer { display:none; }.editor-surface textarea { color:CanvasText; }.editor-surface textarea::selection { color:HighlightText; background:Highlight; } }
  .editor-container textarea:focus-visible { outline:none; }
  .activity-panel { height:190px; flex-shrink:0; border-top:1px solid var(--line); padding:17px 18px; background:var(--panel); }
  .activity-panel .section-label { padding:0; }.section-label>span { float:right; font-weight:400; letter-spacing:0; color:var(--faint); }
  .activity-list { max-height:135px; overflow:auto; margin-top:14px; }.activity { display:flex; gap:8px; align-items:baseline; font-size:10px; margin:10px 0; }.activity strong { font-weight:500; white-space:nowrap; }.activity>span:not(.activity-dot) { color:var(--muted); }.activity small { margin-left:auto; color:var(--faint); font:9px var(--mono); }.activity-dot { align-self:center; width:4px; height:4px; flex-shrink:0; border-radius:50%; background:var(--cyan); }.activity-dot.agent-dot { background:var(--accent); }.activity-list>p { font-size:11px; color:var(--faint); }
  .conversation-panel { border-left:1px solid var(--line); background:var(--panel); display:flex; flex-direction:column; min-height:0; }
  .conversation-title { height:45px; flex-shrink:0; display:flex; align-items:center; gap:9px; padding:0 18px; border-bottom:1px solid var(--line); }.conversation-title>span { color:var(--accent); }.conversation-title strong { font-size:12px; font-weight:500; }.conversation-title small { margin-left:auto; font-size:8px; letter-spacing:1px; color:var(--faint); }
  .conversation-scroll { flex:1; overflow:auto; padding:0 16px; }.empty-chat { font-size:12px; line-height:1.8; padding-top:20px; color:var(--muted); }
  .composer { margin:14px; border:1px solid var(--line-2); border-radius:10px; background:var(--panel-2); padding:12px; }.composer textarea { width:100%; border:0; resize:vertical; min-height:60px; max-height:180px; outline:none; background:transparent; font-size:12px; }.composer>div { display:flex; justify-content:space-between; align-items:center; margin-top:10px; }.composer span { color:var(--faint); font-size:10px; }
  .scope-note { padding:0 18px 14px; margin:0; font-size:9px; line-height:1.8; color:var(--faint); text-align:center; }.agent-warning { margin:10px 16px 0; font-size:11px; color:var(--warn); }
  .error-banner { padding:10px 16px; color:var(--bad); background:color-mix(in srgb,var(--bad) 8%,transparent); font-size:12px; display:flex; align-items:center; gap:12px; }.error-banner button { margin-left:auto; padding:2px 6px; }
  @media(max-width:1100px) { .workspace-app { grid-template-columns:190px minmax(280px,1fr) 300px; }.brand>span { display:none; } }
  @media(max-width:800px) { :global(body) { overflow:auto; }.workspace-app { height:auto; min-height:100dvh; grid-template-columns:1fr; grid-template-rows:auto auto minmax(500px,65dvh) minmax(400px,auto); }.project-panel { padding:12px 16px; border-bottom:1px solid var(--line); }.project-name { padding-bottom:8px; }.project-panel .section-label,.local-file,.invite-note { display:none; }.file { display:inline-flex; width:auto; margin:4px; }.person { display:inline-flex; padding:6px; }.project-panel { display:block; }.conversation-panel { min-height:400px; border-top:1px solid var(--line); }.header-actions { gap:10px; flex-wrap:wrap; }header { padding:8px 16px; flex-wrap:wrap; gap:8px; }.activity-panel { height:160px; } }
</style>
