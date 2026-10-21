<script lang="ts">
  import { onMount } from 'svelte';
  import { harnessTarget, harnessUrl } from './harnessDebug';

  let { inline = false }: { inline?: boolean } = $props();
  // Depend on the ID, not the store object, so feature refreshes preserve playback.
  const workflowId = $derived($harnessTarget.workflowId);
  const preference = 'code-studio:harness-pip-minimized';
  function savedPreference() {
    try { return localStorage.getItem(preference) === '1'; } catch { return false; }
  }
  let minimized = $state(savedPreference()), visible = $state(true);
  let availability = $state<'loading' | 'ready' | 'offline' | 'mismatch'>('loading');
  let frame = $state<HTMLIFrameElement>();
  const expanded = $derived(!minimized && !!workflowId);
  const frameUrl = $derived(harnessUrl(workflowId, true));
  function toggle() {
    minimized = !minimized;
    try { localStorage.setItem(preference, minimized ? '1' : '0'); } catch { /* Optional preference. */ }
  }
  onMount(() => {
    const update = () => { visible = document.visibilityState === 'visible'; };
    update();
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  });
  function stylePreview(event: Event) {
    const doc = (event.currentTarget as HTMLIFrameElement).contentDocument;
    if (!doc || doc.getElementById('studio-preview-style')) return;
    const style = doc.createElement('style');
    style.id = 'studio-preview-style';
    style.textContent = '.svelte-flow__minimap{display:none!important}.app.bleed{grid-template-rows:minmax(0,1fr) auto!important}';
    doc.head.appendChild(style);
  }
  $effect(() => {
    if (!expanded || !visible) return;
    const expected = workflowId, controller = new AbortController();
    availability = 'loading';
    fetch('/harness/api/debug-sessions?workflow_id=' + encodeURIComponent(expected), { signal: controller.signal })
      .then(async response => {
        const sessions = response.ok ? await response.json() : [];
        if (!controller.signal.aborted) availability = Array.isArray(sessions) && sessions.some(s => s.workflow_id === expected) ? 'ready' : 'offline';
      }).catch(() => { if (!controller.signal.aborted) availability = 'offline'; });
    return () => controller.abort();
  });
  $effect(() => {
    const preview = frame, expected = workflowId;
    if (!preview || !expected) return;
    const timer = setInterval(() => {
      try {
        const location = preview.contentWindow?.location;
        if (location && location.href !== 'about:blank' && new URLSearchParams(location.search).get('s') !== expected) availability = 'mismatch';
      } catch { availability = 'mismatch'; }
    }, 1000);
    return () => clearInterval(timer);
  });
</script>

<aside class:inline class:expanded class="harness-pip" aria-label="Harness state flow">
  <header>
    <div class="title"><span>{expanded ? 'State flow' : 'Harness UI'}<small>{$harnessTarget.label}</small></span></div>
    <a href={harnessUrl(workflowId)} target="_blank" rel="noopener noreferrer" aria-label="Open Harness debugger in a new tab" title="Open debugger in a new tab" onclick={() => { if (expanded) toggle(); }}>↗</a>
    {#if workflowId}<button type="button" aria-label={expanded ? 'Minimize state flow' : 'Show state flow'} aria-expanded={expanded} onclick={toggle}>{expanded ? '−' : '▣'}</button>{/if}
  </header>
  {#if expanded}
    <div class="flow-preview">
      {#if visible && availability === 'ready'}
        {#key frameUrl}<iframe bind:this={frame} src={frameUrl} title="Live Harness state flow" onload={stylePreview}></iframe>{/key}
      {:else}<p>{!visible ? 'Preview suspended while this tab is hidden.' : availability === 'loading' ? 'Connecting to Harness UI…' : availability === 'mismatch' ? 'The debugger selected a different session. Restart just dev to load the updated Harness UI.' : 'Harness UI is unavailable for this workflow. Restart the stack with just dev.'}</p>{/if}
    </div>
  {/if}
</aside>

<style>
  .harness-pip { position:fixed; z-index:60; right:16px; bottom:16px; width:230px; max-width:calc(100vw - 32px); border:1px solid var(--line-2); border-radius:10px; background:var(--panel); color:var(--ink); box-shadow:0 6px 24px #0003; overflow:hidden; }
  .expanded { width:460px; }
  header { display:flex; align-items:center; gap:8px; height:48px; padding:0 10px; }
  .title { flex:1; min-width:0; font-size:12px; font-weight:550; }
  small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); font-size:10px; font-weight:400; margin-top:3px; }
  button,a { display:grid; place-items:center; flex:none; width:32px; height:32px; border:1px solid var(--line); border-radius:6px; background:var(--panel-2); color:var(--ink); cursor:pointer; font-size:18px; text-decoration:none; }
  a:focus-visible,button:focus-visible { outline:2px solid var(--accent); outline-offset:-3px; }
  .flow-preview { height:290px; border-top:1px solid var(--line); background:var(--bg); }
  iframe { width:100%; height:100%; border:0; display:block; }
  p { padding:28px; color:var(--muted); font-size:12px; line-height:1.7; }
  .inline { position:relative; inset:auto; width:100%; max-width:none; z-index:auto; box-shadow:none; align-self:start; }
  @media(max-width:600px) { .flow-preview { height:220px; max-height:35dvh; } }
</style>
