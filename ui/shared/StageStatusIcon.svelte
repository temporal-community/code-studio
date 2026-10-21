<script lang="ts">
  // Inline adapter for io icons check-circle-solid, spinner, warning, xmark-circle-solid and pause.
  // No io icon package is installed; the adjacent status text carries the accessible meaning.
  import type { StageTone } from './platform';

  let { status, tone, step }: { status: string; tone: StageTone; step: number } = $props();
</script>

<span class="stage-icon" data-tone={tone} aria-hidden="true">
  {#if tone === 'success'}
    <svg viewBox="0 0 24 24" focusable="false">
      {#if status === 'approved'}
        <path d="M3.5 12.5l3.5 3.5 7-8M10.5 15l1 1 7-8" />
      {:else}
        <path d="M6 12.5l4 4 8-9" />
      {/if}
    </svg>
  {:else if tone === 'running'}
    <svg class="spin" viewBox="0 0 24 24" focusable="false"><path d="M12 4a8 8 0 1 1-8 8" /></svg>
  {:else if tone === 'attention'}
    <svg viewBox="0 0 24 24" focusable="false"><path d="M12 6v7.5" /><circle cx="12" cy="17.5" r="1.1" class="dot" /></svg>
  {:else if tone === 'danger'}
    <svg viewBox="0 0 24 24" focusable="false"><path d="M7.5 7.5l9 9M16.5 7.5l-9 9" /></svg>
  {:else if tone === 'paused'}
    <svg viewBox="0 0 24 24" focusable="false"><path d="M9.5 7v10M14.5 7v10" /></svg>
  {:else}
    <b>{step}</b>
  {/if}
</span>

<style>
  .stage-icon { --tone: var(--panel-3); --glyph: var(--muted); width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center; background: var(--tone); color: var(--glyph); flex: none; transition: transform 160ms ease; }
  .stage-icon[data-tone='success'] { --tone: var(--status-fill-success); --glyph: var(--status-glyph); box-shadow: 0 0 0 4px color-mix(in srgb, var(--status-fill-success) 18%, transparent); }
  .stage-icon[data-tone='running'] { --tone: var(--status-fill-running); --glyph: var(--status-glyph); box-shadow: 0 0 0 4px color-mix(in srgb, var(--status-fill-running) 20%, transparent); }
  .stage-icon[data-tone='attention'] { --tone: var(--status-fill-warning); --glyph: var(--status-glyph-warning); box-shadow: 0 0 0 4px color-mix(in srgb, var(--status-fill-warning) 25%, transparent); }
  .stage-icon[data-tone='danger'] { --tone: var(--status-fill-danger); --glyph: var(--status-glyph); box-shadow: 0 0 0 4px color-mix(in srgb, var(--status-fill-danger) 20%, transparent); }
  .stage-icon[data-tone='paused'] { --tone: var(--faint); --glyph: var(--panel); }
  .stage-icon[data-tone='pending'] { border: 1px dashed var(--line-2); background: transparent; }
  svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round; }
  .dot { fill: currentColor; stroke: none; }
  b { font-size: 11px; font-weight: 550; }
  .spin { animation: spin 1s linear infinite; }
  @media (prefers-reduced-motion: reduce) { .spin { animation: none; } .stage-icon { transition: none; } }
</style>
