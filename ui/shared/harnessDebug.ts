import { writable } from 'svelte/store';

export const harnessTarget = writable({ workflowId: '', label: 'Harness UI' });

// Open the full debugger at the current workflow's graph pane.
export function harnessUrl(workflowId = '', embedded = false): string {
  const query = new URLSearchParams({ p: 'graph', sm: '0', studio_ui: '4' });
  if (workflowId) query.set('s', workflowId);
  if (embedded) { query.set('b', 'graph'); query.set('pip', '1'); }
  return `/harness/?${query}`;
}
