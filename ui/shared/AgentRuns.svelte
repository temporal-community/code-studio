<script lang="ts">
  import { onMount } from 'svelte';
  import { request, names, featureUrl, statusLabel } from './platform';
  let { agentId, refreshKey }: { agentId: string; refreshKey: number } = $props();
  let features = $state<any[]>([]), error = $state('');
  async function refresh() { try { features = (await request('/platform')).features; } catch (e) { error = (e as Error).message; } }
  $effect(() => { void refreshKey; void refresh(); });
  onMount(() => { const poll = setInterval(() => void refresh(), 5000); return () => clearInterval(poll); });
</script>
<section><div class="heading"><div><h2>{names[agentId]} in the feature workflow</h2><p>See where this agent is working, inspect its saved version, or join a feature in progress.</p></div><a class="primary" href="?view=work&new=1">Start feature ↗</a></div>{#if error}<p class="error">{error}</p>{/if}
{#each features as feature}<a class="run" href={featureUrl(feature.id, agentId)}><div><strong>{feature.title}</strong><small>{feature.working_directory}</small></div><div><span>{statusLabel(feature.stages[agentId]?.status || 'pending')}</span><small>{names[feature.current_stage]} · {statusLabel(feature.pipeline_status)}</small></div></a>{:else}<div class="empty"><h3>One feature, a team of agents</h3><p>Start a feature against Code Studio. The agents pass their work forward, with your team approving the plan and release.</p></div>{/each}
</section>
<style>section{padding:28px;overflow:auto;flex:1}.heading{display:flex;gap:20px;align-items:flex-start;justify-content:space-between}h2{font-size:19px;margin:0 0 10px;font-weight:550}p{color:var(--muted);font-size:12px;line-height:1.8}.primary{background:var(--accent);color:var(--accent-ink);padding:9px 12px;border-radius:7px;text-decoration:none;white-space:nowrap;font-size:11px}.run{display:flex;justify-content:space-between;gap:20px;padding:20px 16px;border:1px solid var(--line);border-radius:8px;text-decoration:none;color:var(--ink);margin-top:15px;background:var(--panel)}.run>div{display:flex;flex-direction:column;gap:7px}.run>div:last-child{text-align:right}.run strong{font-size:12px}.run span{font-size:11px;color:var(--accent)}small{font-size:10px;color:var(--muted);overflow-wrap:anywhere}.empty{padding:60px 25px;text-align:center;color:var(--muted)}.empty h3{color:var(--ink);font-size:16px}.error{color:var(--warn)}</style>
