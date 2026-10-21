<script lang="ts">
  import { joinSession } from './shared/identity';
  import ThemeToggle from './shared/ThemeToggle.svelte';

  let { onjoin }: { onjoin: () => void } = $props();
  let name = $state('');
  function submit(event: SubmitEvent) {
    event.preventDefault();
    if (joinSession(name)) onjoin();
  }
</script>

<svelte:head><title>Code Studio · Join workspace</title></svelte:head>
<div class="login">
  <header><strong>⌘ Code Studio</strong><ThemeToggle /></header>
  <main>
    <form onsubmit={submit}>
      <h1>Join your workspace</h1>
      <p>Build features and customize agents with your team.</p>
      <label for="display-name">Your name</label>
      <input id="display-name" bind:value={name} placeholder="e.g. Alex" maxlength="40" autocomplete="name" aria-describedby="session-note" required />
      <p id="session-note">Use one name across Features and Agents for this browser session.</p>
      <button type="submit" disabled={!name.trim()}>Continue →</button>
    </form>
  </main>
</div>

<style>
  .login { height: 100dvh; overflow: auto; background: var(--bg); }
  header { display: flex; align-items: center; justify-content: space-between; padding: 20px 24px; border-bottom: 1px solid var(--line); }
  main { min-height: calc(100dvh - 90px); display: grid; place-items: center; padding: 24px; }
  form { width: min(420px, 100%); padding: clamp(20px, 5vw, 36px); border: 1px solid var(--line-2); border-radius: 12px; background: var(--panel); }
  h1 { margin: 0 0 12px; font-size: 28px; letter-spacing: -.6px; }
  p { color: var(--muted); font-size: 14px; line-height: 1.6; margin: 0 0 28px; }
  label { display: block; margin-bottom: 8px; font-size: 14px; }
  input { width: 100%; min-height: 44px; padding: 10px 12px; border: 1px solid var(--line-2); border-radius: 7px; background: var(--bg); }
  #session-note { font-size: 12px; margin: 10px 0 24px; }
  button { width: 100%; min-height: 44px; border: 0; border-radius: 7px; background: var(--accent); color: var(--accent-ink); font-weight: 600; }
  button:disabled { opacity: .5; cursor: default; }
  input:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
</style>
