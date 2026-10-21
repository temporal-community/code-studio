<script lang="ts">
  import { onMount } from 'svelte';
  import { theme, type ThemeMode } from './theme';

  let mode = $state<ThemeMode>(theme.getState().mode);
  let hasModeChanged = $state(false);
  let observedMode: ThemeMode = theme.getState().mode;

  onMount(() => theme.subscribe(state => {
    if (state.mode !== observedMode) {
      observedMode = state.mode;
      hasModeChanged = true;
    }
    mode = state.mode;
  }));
</script>

<button class="theme-toggle io-button io-button--quiet" type="button" aria-label="Toggle color theme" aria-pressed={mode === 'dark'} onclick={() => theme.toggle()}>
  {#key mode}
    {#if mode === 'light'}
      <svg class="theme-icon sun" class:animate={hasModeChanged} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <g class="sun-rays" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.8">
          <path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" />
        </g>
        <circle class="sun-disc" cx="12" cy="12" r="4.25" fill="currentColor" />
      </svg>
    {:else}
      <svg class="theme-icon moon" class:animate={hasModeChanged} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <circle class="moon-disc" cx="10" cy="12" r="6" fill="currentColor" />
        <circle class="moon-cutout" cx="14" cy="12" r="5" />
      </svg>
    {/if}
  {/key}
</button>

<style>
  .theme-toggle { min-width: 36px; min-height: 36px; padding: 7px; }
  .theme-icon { display: block; width: 18px; height: 18px; overflow: visible; }
  .sun, .moon { color: var(--color-content-brand); }
  .moon-cutout { fill: var(--panel-2); transform: translateX(0); }
  .sun.animate { animation: sun-rise 450ms cubic-bezier(.2,.8,.25,1) both; }
  .sun.animate .sun-rays, .sun.animate .sun-disc { animation: sunrise-color 450ms ease-out both; }
  .moon.animate { animation: moon-lower 450ms cubic-bezier(.2,.8,.25,1) both; }
  .moon.animate .moon-cutout { animation: moon-crescent 450ms ease-out both; }
  @keyframes sun-rise { from { opacity:.65; transform:translateY(7px) scale(.78); } to { opacity:1; transform:translateY(0) scale(1); } }
  @keyframes sunrise-color { 0% { color:var(--color-content-warning); } 35% { color:var(--color-content-brand); } 70% { color:var(--color-content-warning); } 100% { color:var(--color-content-brand); } }
  @keyframes moon-lower { from { opacity:.65; transform:translateY(-7px) scale(.8); } to { opacity:1; transform:translateY(0) scale(1); } }
  @keyframes moon-crescent { from { transform:translateX(7px); } to { transform:translateX(0); } }
  @media (prefers-reduced-motion: reduce) {
    .theme-icon.animate, .theme-icon.animate .sun-rays, .theme-icon.animate .sun-disc, .theme-icon.animate .moon-cutout { animation:none; }
    .moon-cutout { transform:translateX(0); }
  }
</style>
