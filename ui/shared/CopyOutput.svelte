<script lang="ts">
  import { copyOutput } from './copyOutput';
  let { output }: { output: string } = $props();
  let feedback = $state('');
  let pending = $state(false);
  let attempt = 0;

  $effect(() => {
    output;
    attempt += 1;
    feedback = '';
    pending = false;
  });

  async function copy() {
    if (pending) return;
    const selectedOutput = output;
    const currentAttempt = ++attempt;
    feedback = '';
    pending = true;
    const succeeded = await copyOutput(selectedOutput);
    if (currentAttempt !== attempt || output !== selectedOutput) return;
    pending = false;
    feedback = succeeded ? 'copied' : 'failed';
  }
</script>

<div class="copy-output">
  <button disabled={pending} onclick={() => void copy()}>{pending ? 'Copying…' : 'Copy output'}</button>
  {#if pending}<span role="status">Copying output…</span>
  {:else if feedback === 'copied'}<span class="success" role="status">Output copied.</span>
  {:else if feedback === 'failed'}<span class="failure" role="alert">Could not copy output.</span>{/if}
</div>

<style>
  .copy-output { display:flex; align-items:center; gap:8px; flex-wrap:wrap; flex:none; }
  button { border:1px solid var(--line-2); border-radius:6px; padding:7px 10px; background:var(--panel-2); color:var(--ink); font-size:10px; white-space:nowrap; }
  button:disabled { opacity:.55; }
  span { font-size:10px; }
  .success { color:var(--good); }
  .failure { color:var(--bad); }
</style>
